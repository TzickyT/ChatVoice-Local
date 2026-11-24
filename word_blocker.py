"""Word block list for ChatVoice Local.

Reads `blocked_words.txt` (one word or phrase per line) and tells the app whether a chat message contains a blocked
word, in any common disguise: capitals, repeated letters (heeeeck), spaced or dotted letters (h e c k, h.e.c.k),
number and symbol swaps (h3ck, h!ck), look-alike letters from other alphabets, accents, invisible characters,
full-width letters, censored forms (h**k, h*ck), words glued inside longer strings, and common endings (s, es, ed,
er, ers, ing, y ...).

File format (see TEMPLATE below): `#` starts a comment, `=word` matches that exact word only, `*word` also matches
inside longer words, `-word` is an allowed word that is never blocked, and a line with spaces is a phrase.
"""
import functools
import itertools
import os
import re
import time
import unicodedata

LEET = {'0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't', '8': 'b', '9': 'g',
        '@': 'a', '$': 's', '!': 'i', '|': 'i', '+': 't', '(': 'c'}
AMBIGUOUS = '1!|'          # these may also stand for an "l"
WILD = '*#%&^~?'           # censoring characters: stand for one hidden letter (or are just filler)
SYMCHARS = '@$!|+()' + WILD
SUFFIXES = ('ers', 'er', 'es', 'ed', 'ing', 'ies', 'y', 's')
E_SUFFIXES = ('ers', 'er', 'ed', 'ing', 'y')   # raped -> rap -> rape, cheesy -> chees -> cheese
MAX_WINDOW = 8
AUTO_SUBSTRING_LENGTH = 6  # words this long (or longer) are also found glued inside other strings

HOMOGLYPHS = {
    'а': 'a', 'в': 'b', 'е': 'e', 'ё': 'e', 'к': 'k', 'м': 'm', 'н': 'h', 'о': 'o', 'р': 'p', 'с': 'c', 'т': 't',
    'у': 'y', 'х': 'x', 'і': 'i', 'ї': 'i', 'ј': 'j', 'ѕ': 's', 'ԁ': 'd', 'ӏ': 'l', 'ɡ': 'g', 'ɑ': 'a', 'ı': 'i',
    'α': 'a', 'β': 'b', 'ε': 'e', 'ι': 'i', 'κ': 'k', 'ν': 'v', 'ο': 'o', 'ρ': 'p', 'τ': 't', 'υ': 'u', 'χ': 'x',
    'η': 'n', 'ω': 'w', 'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c', 'ᴅ': 'd', 'ᴇ': 'e', 'ɢ': 'g', 'ʜ': 'h', 'ɪ': 'i', 'ᴊ': 'j',
    'ᴋ': 'k', 'ʟ': 'l', 'ᴍ': 'm', 'ɴ': 'n', 'ᴏ': 'o', 'ᴘ': 'p', 'ʀ': 'r', 'ᴛ': 't', 'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w',
    'ʏ': 'y', 'ᴢ': 'z',
}

TEMPLATE = """# ChatVoice Local blocked words
#
# Any chat message that contains one of these words is NOT read aloud. Put one word or phrase on each line.
# Lines that start with # are comments. Capitals do not matter. The list reloads by itself when you save this file.
#
# Every entry is also caught when it is disguised: heeeeck, h e c k, h.e.c.k, h3ck, h!ck, h**k, look-alike letters
# from other alphabets, accents, invisible characters, and with common endings (s, es, ed, er, ing, y ...).
# Words of 6 or more letters are also caught when they are glued inside a longer string.
#
# Special lines:
#   =word       exact match only: no endings, no disguises, no hiding inside other words
#   *word       also catch it inside longer words, whatever its length (may block innocent words)
#   -word       an allowed word that is never blocked (for innocent words that look like a blocked one)
#   two words   a line with spaces is a phrase (it is also caught with the spaces left out)
#
# Add your words below this line (the list is empty at first).
"""


def clean(text):
    """Lower-case, strip accents and invisible characters, turn full-width and look-alike letters into plain ones."""
    out = []
    for ch in unicodedata.normalize('NFKD', str(text or '')):
        if ch.isspace():
            out.append(' ')
            continue
        if unicodedata.category(ch) in ('Mn', 'Me', 'Cf', 'Cc', 'Cs', 'Co'):
            continue
        out.append(''.join(HOMOGLYPHS.get(c, c) for c in ch.casefold()))
    return ''.join(out)


def tokenize(text):
    """Splits cleaned text into (token, preceded_by_whitespace). A token is letters, digits and leet/censor symbols."""
    tokens, current, pending_space, start_space = [], [], True, True
    for ch in text:
        if ch.isalnum() or ch in SYMCHARS:
            if not current:
                start_space = pending_space
                pending_space = False
            current.append(ch)
        else:
            if current:
                tokens.append((''.join(current), start_space))
                current = []
            if ch == ' ':
                pending_space = True
    if current:
        tokens.append((''.join(current), start_space))
    return tokens


def simple_form(raw):
    """One plain-letters reading of a token (edge symbols dropped, number/symbol swaps undone, censor marks removed)."""
    out = []
    for ch in raw.strip(SYMCHARS):
        if ch in WILD or ch == ')':
            continue
        out.append(LEET.get(ch, ch))
    return ''.join(out)


def token_forms(raw):
    """Every plain-letters reading of a token. A '*' stands for one censored letter (or filler)."""
    forms = set()
    for strip_edges in (True, False):
        s = raw.strip(SYMCHARS) if strip_edges else raw
        s = s.strip(WILD)
        if not s:
            continue
        branches = ['']
        for ch in s:
            if ch == ')':
                continue
            if ch in WILD:
                options = ['*']
            elif ch in AMBIGUOUS:
                options = ['i', 'l']
            else:
                options = [LEET.get(ch, ch)]
            if len(options) == 1:
                branches = [b + options[0] for b in branches]
            else:
                branches = [b + o for b in branches for o in options][:16]
        forms.update(b for b in branches if b)
    return forms


def cap_runs(text, limit=2):
    return ''.join(ch * min(len(list(group)), limit) for ch, group in itertools.groupby(text))


@functools.lru_cache(maxsize=8192)
def variants(form):
    """The form itself, with stretched letters squeezed, and with common word endings removed."""
    runs = [(ch, len(list(g))) for ch, g in itertools.groupby(form)]
    result = {form}
    repeated = sum(1 for _ch, n in runs if n > 1)
    if repeated:
        if repeated <= 6:
            choices = [(1,) if n == 1 else (2, 1) for _ch, n in runs]
            result.update(''.join(ch * k for (ch, _n), k in zip(runs, combo)) for combo in itertools.product(*choices))
        else:
            result.add(''.join(ch * 2 if n > 1 else ch for ch, n in runs))
            result.add(''.join(ch for ch, _n in runs))
    extra = set()
    for word in result:
        w = word
        if w.endswith('z') and len(w) > 3:
            w = w[:-1] + 's'
            extra.add(w)
        for suffix in SUFFIXES:
            if w.endswith(suffix) and len(w) - len(suffix) >= 2:
                stem = w[:-len(suffix)]
                extra.add(stem)
                if suffix in E_SUFFIXES:
                    extra.add(stem + 'e')
        if w.endswith('ies') and len(w) > 5:
            extra.add(w[:-3] + 'y')
        if w.endswith('a') and len(w) >= 4:
            extra.add(w[:-1] + 'er')
        if w.endswith('er') and len(w) >= 5:
            extra.add(w[:-2] + 'a')
    return frozenset(result | extra)


class WordBlocker:
    def __init__(self, path):
        self.path = path
        self.words, self.exact, self.sub, self.allow, self.phrases = set(), set(), set(), set(), []
        self.by_length = {}
        self.max_length = 0
        self.entries = 0
        self._mtime = None
        self._last_check = 0.0

    # ---- file handling
    def ensure_file(self):
        if os.path.exists(self.path):
            return
        try:
            with open(self.path, 'w', encoding='utf-8', newline='\r\n') as f:
                f.write(TEMPLATE)
        except OSError:
            pass

    def _read_lines(self):
        try:
            with open(self.path, 'r', encoding='utf-8-sig', errors='replace') as f:
                return f.read().splitlines()
        except OSError:
            return []

    def load(self):
        """(Re)reads the file. Returns the number of entries."""
        words, exact, sub, allow, phrases = set(), set(), set(), set(), []
        for line in self._read_lines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            for part in re.split(r'\s*[,;]\s*', line):
                part = part.strip()
                if not part:
                    continue
                mode = part[0] if part[0] in '=*-' else ''
                body = part[1:].strip() if mode else part
                if mode == '=':
                    plain = ''.join(ch for ch in clean(body) if ch.isalnum())
                    if plain:
                        exact.add(plain)
                    continue
                pieces = [simple_form(raw) for raw, _sp in tokenize(clean(body))]
                pieces = [p for p in pieces if p]
                joined = ''.join(pieces)
                if len(joined) < 2:
                    continue
                if mode == '-':
                    allow.add(joined)
                elif mode == '*':
                    sub.add(cap_runs(joined))
                elif len(pieces) > 1:
                    phrases.append((len(pieces), cap_runs(joined)))
                else:
                    words.add(joined)
                    if len(joined) >= AUTO_SUBSTRING_LENGTH:
                        sub.add(cap_runs(joined))
        self.words, self.exact, self.sub, self.allow, self.phrases = words, exact, sub, allow, phrases
        self.by_length = {}
        for w in words:
            self.by_length.setdefault(len(w), []).append(w)
        self.max_length = max([len(w) for w in words] + [len(p) for _k, p in phrases] + [0])
        self.entries = len(words) + len(exact) + len(phrases) + len(sub - {cap_runs(w) for w in words})
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None
        return self.entries

    def maybe_reload(self):
        """Picks up edits to the file (checked at most every 2 seconds)."""
        now = time.time()
        if now - self._last_check < 2:
            return
        self._last_check = now
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self.load()

    def add_from_file(self, source_path):
        """Appends the words of another .txt file that are not in the list yet. Returns how many were added."""
        self.ensure_file()
        with open(source_path, 'r', encoding='utf-8-sig', errors='replace') as f:
            incoming = [line.strip() for line in f.read().splitlines()]
        have = {line.strip().lower() for line in self._read_lines()}
        new = []
        for line in incoming:
            if line and not line.startswith('#') and line.lower() not in have:
                have.add(line.lower())
                new.append(line)
        if new:
            with open(self.path, 'a', encoding='utf-8', newline='') as f:
                existing = self._read_lines()
                if existing and existing[-1].strip():
                    f.write('\r\n')
                f.write('\r\n'.join(new) + '\r\n')
        self.load()
        return len(new)

    @property
    def active(self):
        return bool(self.words or self.exact or self.sub or self.phrases)

    # ---- matching
    def _hit(self, form):
        return any(v in self.words for v in variants(form))

    def _wild_hit(self, form):
        known = sum(1 for ch in form if ch != '*')
        if known < 2:
            return False
        if self._hit(form.replace('*', '')):          # the mark is only filler: ni*gger
            return True
        if form.count('*') <= len(form) // 2:         # the mark hides one letter: f**k, f*ck
            for word in self.by_length.get(len(form), ()):
                if all(a == '*' or a == b for a, b in zip(form, word)):
                    return True
        return False

    @staticmethod
    def _window_ok(tokens, simples, i, j):
        if not any(tokens[k][1] for k in range(i + 1, j + 1)):
            return True                               # letters split by symbols only: f.u.c.k
        parts = simples[i:j + 1]                      # split by spaces: only small pieces (n i g g e r)
        return all(len(p) <= 3 for p in parts) and (len(parts) >= 3 or any(len(p) == 1 for p in parts))

    def matches(self, text):
        self.maybe_reload()
        if not self.active or not text:
            return False
        cleaned = clean(text)
        tokens = tokenize(cleaned)
        if not tokens:
            return False
        simples = [simple_form(raw) for raw, _sp in tokens]
        if self.exact:
            for raw, _sp in tokens:
                if raw.strip(SYMCHARS) in self.exact:
                    return True
        if self.words:
            for (raw, _sp), simple in zip(tokens, simples):
                if simple in self.allow:
                    continue
                for form in token_forms(raw):
                    if '*' in form:
                        if self._wild_hit(form):
                            return True
                    elif self._hit(form):
                        return True
            limit = self.max_length + 4
            for i in range(len(tokens)):
                joined = simples[i]
                for j in range(i + 1, min(len(tokens), i + MAX_WINDOW)):
                    joined += simples[j]
                    if len(joined) > limit:
                        break
                    if self._window_ok(tokens, simples, i, j) and self._hit(joined) and joined not in self.allow:
                        return True
        for pieces, squashed in self.phrases:
            for i in range(len(tokens)):
                joined = ''
                for j in range(i, min(len(tokens), i + max(pieces + 2, len(squashed)))):
                    joined += simples[j]
                    if cap_runs(joined) == squashed:
                        return True
                    if len(joined) > len(squashed) + 2:
                        break
        if self.sub:
            for chunk in cleaned.split():
                squashed = ''.join(LEET.get(ch, ch) for ch in chunk if (ch.isalnum() or ch in LEET))
                for allowed in self.allow:
                    squashed = squashed.replace(allowed, '#')
                squashed = cap_runs(squashed)
                if any(entry in squashed for entry in self.sub):
                    return True
        return False
