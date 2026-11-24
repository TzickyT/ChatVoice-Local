"""Emotes for ChatVoice Local: 7TV, BetterTTV, FrankerFaceZ, Twitch and Kick (the same sources Chatterino uses).

EmoteStore   finds emotes (global + channel sets), cuts chat text into text/emote pieces, caches images on disk.
EmoteImages  decodes images for the Tk Activity view (Pillow needed; animated emotes supported).
Everything here is safe to call from any thread except EmoteImages.sprite()/group_photo(), which belong to the Tk thread.
"""
import json, os, re, threading, time, urllib.request, urllib.error, urllib.parse

UA = 'ChatVoiceLocal/1.0 (+emotes)'
MAX_IMAGE_BYTES = 6 * 1024 * 1024
KICK_EMOTE_RE = re.compile(r'\[emote:(\d+):([^\]]+)\]')
BTTV_ZERO_WIDTH = frozenset({'SoSnowy', 'IceCold', 'cvHazmat', 'cvMask', 'TopHat', 'ReinDeer', 'SantaHat', 'CandyCane'})
PROVIDER_LABELS = {'7tv': '7TV', 'bttv': 'BetterTTV', 'ffz': 'FrankerFaceZ', 'twitch': 'Twitch', 'kick': 'Kick'}
THIRD_PARTY = ('7tv', 'bttv', 'ffz')


class Emote:
    __slots__ = ('key', 'name', 'provider', 'url', 'zero_width', 'width', 'height')
    def __init__(self, provider, eid, name, url, zero_width=False, width=0, height=0):
        self.provider = provider; self.name = name; self.url = url; self.zero_width = bool(zero_width)
        self.key = provider + ':' + str(eid); self.width = int(width or 0); self.height = int(height or 0)


def _https(url):
    url = str(url or '')
    return 'https:' + url if url.startswith('//') else url


def _list(value):
    return value if isinstance(value, list) else []


def parse_7tv(data):
    """7TV emote set (global, or a user's `emote_set`). Returns [Emote]."""
    if not isinstance(data, dict): return []
    if 'emotes' not in data and isinstance(data.get('emote_set'), dict): data = data['emote_set']
    out = []
    for item in _list(data.get('emotes')):
        try:
            eid = item['id']; name = item.get('name') or (item.get('data') or {}).get('name')
            d = item.get('data') or {}; host = d.get('host') or {}
            base = _https(host.get('url')) or 'https://cdn.7tv.app/emote/' + str(eid)
            files = [f for f in _list(host.get('files')) if isinstance(f, dict)]
            pick = next((f for f in files if f.get('name') == '2x.webp'), None) or next((f for f in files if str(f.get('name', '')).startswith('2x')), None) or (files[0] if files else None)
            fname = pick.get('name') if pick else '2x.webp'
            zw = bool(int(item.get('flags') or 0) & 1) or bool(int(d.get('flags') or 0) & 256)
            if name: out.append(Emote('7tv', eid, name, base.rstrip('/') + '/' + fname, zw, (pick or {}).get('width'), (pick or {}).get('height')))
        except (KeyError, TypeError, ValueError, AttributeError): continue
    return out


def parse_bttv(data):
    """BetterTTV global list, or a channel object with channelEmotes + sharedEmotes."""
    items = data if isinstance(data, list) else (_list(data.get('channelEmotes')) + _list(data.get('sharedEmotes')) if isinstance(data, dict) else [])
    out = []
    for item in items:
        try:
            eid, code = item['id'], item['code']
            out.append(Emote('bttv', eid, code, 'https://cdn.betterttv.net/emote/%s/2x.webp' % eid, code in BTTV_ZERO_WIDTH))
        except (KeyError, TypeError): continue
    return out


def parse_ffz(data, set_ids=None):
    """FrankerFaceZ /v1/set/global or /v1/room/id/<id> reply. set_ids limits which sets count."""
    if not isinstance(data, dict) or not isinstance(data.get('sets'), dict): return []
    if set_ids is None:
        set_ids = data.get('default_sets') or []
        room = data.get('room')
        if isinstance(room, dict) and room.get('set') is not None: set_ids = [room['set']]
    out = []
    for sid in set_ids:
        for item in _list((data['sets'].get(str(sid)) or {}).get('emoticons')):
            try:
                urls = item.get('animated') or item.get('urls') or {}
                pick = urls.get('2') or urls.get('1') or urls.get('4') or next(iter(urls.values()))
                out.append(Emote('ffz', item['id'], item['name'], _https(pick), False, item.get('width'), item.get('height')))
            except (KeyError, TypeError, StopIteration, AttributeError): continue
    return out


def twitch_emote(eid, name=''):
    return Emote('twitch', eid, name, 'https://static-cdn.jtvnw.net/emoticons/v2/%s/default/dark/2.0' % eid)


def kick_emote(eid, name=''):
    return Emote('kick', eid, name, 'https://files.kick.com/emotes/%s/fullsize' % eid)


def cheermote(prefix, tier, name=''):
    prefix = re.sub(r'[^A-Za-z0-9_]', '', str(prefix)).lower()
    return Emote('cheer', '%s_%s' % (prefix, int(tier or 1)), name or prefix, 'https://d3aqoihi2n8ty8.cloudfront.net/actions/%s/dark/animated/%d/2.gif' % (prefix, int(tier or 1)))


def sniff_type(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n': return 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'): return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image/webp'
    if data[:3] == b'\xff\xd8\xff': return 'image/jpeg'
    if data[4:12] in (b'ftypavif', b'ftypavis'): return 'image/avif'
    return 'application/octet-stream'


BADGE_ORDER = ('broadcaster', 'staff', 'admin', 'global_mod', 'moderator', 'lead_moderator', 'vip', 'founder', 'subscriber', 'sub_gifter', 'partner', 'verified', 'og', 'bits')
BADGE_TITLES = {'broadcaster': 'Broadcaster', 'staff': 'Staff', 'admin': 'Admin', 'global_mod': 'Global moderator', 'moderator': 'Moderator', 'lead_moderator': 'Lead moderator', 'vip': 'VIP', 'founder': 'Founder', 'subscriber': 'Subscriber', 'sub_gifter': 'Sub gifter', 'partner': 'Verified', 'verified': 'Verified', 'og': 'OG', 'bits': 'Bits'}
KICK_BADGE_COLORS = {'broadcaster': (233, 17, 60), 'moderator': (0, 199, 0), 'vip': (224, 5, 185), 'subscriber': (83, 252, 24), 'founder': (245, 166, 35), 'og': (255, 196, 0), 'verified': (83, 252, 24), 'sub_gifter': (47, 128, 237), 'voice': (145, 70, 255)}
MAX_BADGES = 4


def make_kick_badge(kind):
    """PNG bytes for a Kick badge (Kick has no public badge images): a coloured rounded square with a white symbol. None without Pillow."""
    try:
        from PIL import Image, ImageDraw
        import io, math
        S = 144; im = Image.new('RGBA', (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        color = KICK_BADGE_COLORS.get(kind, (120, 120, 130)); dark = (20, 20, 20, 255) if kind in ('subscriber', 'verified', 'og') else (255, 255, 255, 255)
        d.rounded_rectangle((4, 4, S - 4, S - 4), 28, fill=color + (255,))
        c = S / 2
        if kind in ('subscriber', 'founder'):
            pts = [(c + (52 if i % 2 == 0 else 22) * math.sin(i * math.pi / 5), c - (52 if i % 2 == 0 else 22) * math.cos(i * math.pi / 5)) for i in range(10)]; d.polygon(pts, fill=dark)
        elif kind == 'voice':   # microphone
            d.rounded_rectangle((c - 17, 22, c + 17, 82), 17, fill=dark); d.arc((c - 33, 44, c + 33, 104), 0, 180, fill=dark, width=10); d.line([(c, 104), (c, 120)], fill=dark, width=10); d.line([(c - 20, 120), (c + 20, 120)], fill=dark, width=10)
        elif kind == 'vip': d.polygon([(c, 28), (S - 30, c), (c, S - 28), (30, c)], fill=dark)
        elif kind == 'moderator': d.line([(34, S - 34), (S - 34, 34)], fill=dark, width=18); d.line([(34, 34 + 26), (34 + 26, 34)], fill=dark, width=16); d.line([(S - 60, S - 34), (S - 34, S - 60)], fill=dark, width=16)
        elif kind == 'verified': d.line([(38, c + 4), (c - 8, S - 42), (S - 36, 40)], fill=dark, width=20, joint='curve')
        elif kind == 'og': d.ellipse((34, 34, S - 34, S - 34), outline=dark, width=18)
        elif kind == 'sub_gifter': d.rectangle((34, 56, S - 34, S - 34), fill=dark); d.rectangle((c - 8, 40, c + 8, S - 34), fill=color + (255,)); d.rectangle((34, 56, S - 34, 68), fill=color + (255,))
        else: d.ellipse((38, 38, S - 38, S - 38), fill=dark)   # broadcaster: a record dot
        out = io.BytesIO(); im.resize((48, 48), Image.LANCZOS).save(out, 'PNG'); return out.getvalue()
    except Exception: return None


class EmoteStore:
    def __init__(self, cache_dir, status=None):
        self.dir = cache_dir; self.status = status or (lambda text: None)
        self.lock = threading.RLock()
        self.enabled = {p: True for p in PROVIDER_LABELS}
        self.globals = {p: {} for p in THIRD_PARTY}
        self.channels = {}            # 'twitch:<id>' or 'kick:<id>' -> {provider: {name: Emote}}
        self.channel_loaded = set()
        self.registry = {}            # emote key -> Emote (for the OBS dock image route and the Tk loader)
        self.global_state = 'idle'    # idle / loading / done
        self.errors = []
        self.generation = 0
        self.twitch_badges = {}       # set id -> {version id: image url}
        self.kick_sub_badges = []     # [(months, image url)] a Kick channel's own subscriber badges
        self.badges_on = {'twitch': True, 'kick': True}
        try: os.makedirs(self.dir, exist_ok=True)
        except OSError: pass

    # ---- loading ----
    def _get_json(self, url, timeout=12):
        request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response: return json.loads(response.read(20 * 1024 * 1024).decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code == 404: return None      # this channel simply has no set there
            raise

    def _fill(self, bucket, provider, emotes):
        with self.lock:
            target = bucket.setdefault(provider, {}); target.clear()
            for e in emotes:
                target.setdefault(e.name, e); self.registry[e.key] = e

    def _attempt(self, label, fn):
        try: fn()
        except Exception as exc:
            with self.lock: self.errors.append('%s: %s' % (label, str(exc)[:120]))

    def load_globals(self, force=False, done=None):
        with self.lock:
            if self.global_state == 'loading' or (self.global_state == 'done' and not force): return
            self.global_state = 'loading'; self.errors = []; gen = self.generation
        def work():
            self._attempt('7TV global', lambda: self._fill(self.globals, '7tv', parse_7tv(self._get_json('https://7tv.io/v3/emote-sets/global'))))
            self._attempt('BetterTTV global', lambda: self._fill(self.globals, 'bttv', parse_bttv(self._get_json('https://api.betterttv.net/3/cached/emotes/global'))))
            self._attempt('FrankerFaceZ global', lambda: self._fill(self.globals, 'ffz', parse_ffz(self._get_json('https://api.frankerfacez.com/v1/set/global'))))
            with self.lock: self.global_state = 'done'
            if done: done()
        threading.Thread(target=work, daemon=True).start()

    def load_channel(self, platform, channel_id, done=None, force=False):
        """platform 'twitch' (7TV + BetterTTV + FrankerFaceZ) or 'kick' (7TV). channel_id is the numeric user id."""
        channel_id = str(channel_id or '').strip()
        if not channel_id.isdigit(): return
        ck = platform + ':' + channel_id
        with self.lock:
            if ck in self.channel_loaded and not force: return
            self.channel_loaded.add(ck); bucket = self.channels.setdefault(ck, {})
        def work():
            q = urllib.parse.quote(channel_id)
            self._attempt('7TV ' + platform, lambda: self._fill(bucket, '7tv', parse_7tv(self._get_json('https://7tv.io/v3/users/%s/%s' % (platform, q)))))
            if platform == 'twitch':
                self._attempt('BetterTTV channel', lambda: self._fill(bucket, 'bttv', parse_bttv(self._get_json('https://api.betterttv.net/3/cached/users/twitch/' + q))))
                self._attempt('FrankerFaceZ channel', lambda: self._fill(bucket, 'ffz', parse_ffz(self._get_json('https://api.frankerfacez.com/v1/room/id/' + q))))
            if done: done()
        threading.Thread(target=work, daemon=True).start()

    def reload(self, channel_keys=(), done=None):
        with self.lock:
            self.channel_loaded.clear(); self.channels.clear(); self.globals = {p: {} for p in THIRD_PARTY}; self.global_state = 'idle'
        self.load_globals(force=True, done=done)
        for ck in channel_keys:
            platform, _, cid = ck.partition(':'); self.load_channel(platform, cid, done=done, force=True)

    def counts(self):
        with self.lock:
            out = {p: [len(self.globals[p]), 0] for p in THIRD_PARTY}
            for bucket in self.channels.values():
                for p, names in bucket.items(): out[p][1] += len(names)
            return out, list(self.errors)

    # ---- chat text -> pieces ----
    def lookup(self, word, channel_key=None):
        with self.lock:
            buckets = ([self.channels.get(channel_key, {})] if channel_key else []) + [self.globals]
            for bucket in buckets:
                for provider in THIRD_PARTY:   # 7TV first, then BetterTTV, then FrankerFaceZ
                    if not self.enabled.get(provider): continue
                    hit = bucket.get(provider, {}).get(word)
                    if hit: return hit
        return None

    def _split_words(self, text, channel_key, out):
        for piece in re.split(r'(\s+)', text):
            if not piece: continue
            hit = None if piece.isspace() else self.lookup(piece, channel_key)
            out.append(hit if hit else piece)

    def tokenize(self, platform, text, fragments=None, channel_key=None):
        """-> list of str and Emote. Twitch passes EventSub fragments, Kick passes text holding [emote:id:name]."""
        text = str(text or ''); out = []
        platform = str(platform).lower()
        if platform == 'twitch' and fragments:
            for frag in fragments:
                kind = frag.get('type'); ftext = str(frag.get('text') or '')
                if kind == 'emote' and frag.get('id') and self.enabled.get('twitch'):
                    e = twitch_emote(frag['id'], ftext); self._register(e); out.append(e)
                elif kind == 'cheermote' and frag.get('prefix') and self.enabled.get('twitch'):
                    e = cheermote(frag['prefix'], frag.get('tier') or 1, ftext); self._register(e); out.append(e)
                    if frag.get('bits'): out.append(' ' + str(frag['bits']))
                else: self._split_words(ftext, channel_key, out)
        elif platform == 'kick':
            pos = 0
            for m in KICK_EMOTE_RE.finditer(text):
                self._split_words(text[pos:m.start()], channel_key, out)
                if self.enabled.get('kick'): e = kick_emote(m.group(1), m.group(2)); self._register(e); out.append(e)
                else: out.append(m.group(2))
                pos = m.end()
            self._split_words(text[pos:], channel_key, out)
        else: self._split_words(text, channel_key, out)
        merged = []
        for t in out:
            if isinstance(t, str) and merged and isinstance(merged[-1], str): merged[-1] += t
            else: merged.append(t)
        return merged

    def _register(self, e):
        with self.lock: self.registry[e.key] = e

    @staticmethod
    def plain_text(tokens, keep_emotes=True):
        """Text for the log / speech. keep_emotes=False drops the emote names (for 'do not read emote names')."""
        return re.sub(r'\s+', ' ', ''.join(t if isinstance(t, str) else (' ' + t.name + ' ' if keep_emotes else ' ') for t in tokens)).strip()

    @staticmethod
    def has_emotes(tokens):
        return any(not isinstance(t, str) for t in tokens)

    @staticmethod
    def to_parts(tokens):
        """JSON-friendly pieces for the OBS dock."""
        return [{'t': 'text', 'v': t} if isinstance(t, str) else {'t': 'emote', 'k': t.key, 'n': t.name, 'z': t.zero_width, 'w': t.width, 'h': t.height} for t in tokens]

    # ---- badges (sub / mod / VIP ...) ----
    def set_twitch_badges(self, sets):
        with self.lock: self.twitch_badges = {k: dict(v) for k, v in (sets or {}).items() if isinstance(v, dict)}

    def set_kick_sub_badges(self, items):
        good = []
        for it in items or []:
            try: good.append((int(it[0]), str(it[1])))
            except (TypeError, ValueError, IndexError): continue
        with self.lock: self.kick_sub_badges = sorted(good)

    def _badge(self, key, title, url, kick_kind=None):
        with self.lock:
            e = self.registry.get(key)
            if e: return e
            e = Emote('badge', key, title, url); self.registry[e.key] = e
        if kick_kind:
            path = self._path(e)
            if not os.path.exists(path):
                data = make_kick_badge(kick_kind)
                if data:
                    try:
                        with open(path, 'wb') as fh: fh.write(data)
                    except OSError: pass
        return e

    def voice_badge(self):
        """The microphone shown next to people who have paid voice time."""
        return self._badge('voice', 'Voice sub', 'https://chatvoice.local/voice.png', kick_kind='voice')

    def badges_for(self, platform, metadata):
        """Badge images for a chat message, most important first (at most MAX_BADGES)."""
        platform = str(platform).lower(); found = {}
        if platform == 'twitch':
            with self.lock: sets = self.twitch_badges
            for pair in metadata.get('badge_ids') or []:
                try: sid, vid = pair[0], str(pair[1])
                except (TypeError, IndexError): continue
                url = (sets.get(sid) or {}).get(vid) or next(iter((sets.get(sid) or {}).values()), None)
                if sid in BADGE_ORDER and url and sid not in found: found[sid] = self._badge('twitch_%s_%s' % (sid, vid), BADGE_TITLES.get(sid, sid), url)
        elif platform == 'kick':
            for b in metadata.get('badge_info') or []:
                kind = str(b.get('type') or '').lower(); kind = {'sub_gifter': 'sub_gifter', 'og': 'og'}.get(kind, kind)
                if kind not in BADGE_ORDER or kind in found: continue
                months = int(b.get('count') or 0) if str(b.get('count') or '0').isdigit() else 0
                with self.lock: custom = list(self.kick_sub_badges)
                if kind == 'subscriber' and custom:
                    pick = max([c for c in custom if c[0] <= months] or custom[:1], key=lambda c: c[0])
                    found[kind] = self._badge('kick_sub_%d' % pick[0], 'Subscriber', pick[1])
                else: found[kind] = self._badge('kick_' + kind, BADGE_TITLES.get(kind, kind), 'https://kick.com/badges/%s.png' % kind, kick_kind=kind)
        return [found[k] for k in BADGE_ORDER if k in found][:MAX_BADGES]

    # ---- images on disk ----
    def _path(self, e):
        ext = os.path.splitext(urllib.parse.urlparse(e.url).path)[1].lower()
        ext = ext if re.fullmatch(r'\.[a-z0-9]{2,5}', ext) else '.img'
        return os.path.join(self.dir, re.sub(r'[^A-Za-z0-9_.-]', '_', e.key) + ext)

    def image_bytes(self, key):
        with self.lock: e = self.registry.get(key)
        if not e: return None
        path = self._path(e)
        try:
            with open(path, 'rb') as fh: return fh.read()
        except OSError: pass
        request = urllib.request.Request(e.url, headers={'User-Agent': UA})
        with urllib.request.urlopen(request, timeout=15) as response: data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES or sniff_type(data) == 'application/octet-stream': raise ValueError('not a usable image')
        try:
            with open(path + '.part', 'wb') as fh: fh.write(data)
            os.replace(path + '.part', path)
        except OSError: pass
        return data

    def prune_cache(self, days=30):
        cutoff = time.time() - days * 86400
        try:
            for name in os.listdir(self.dir):
                p = os.path.join(self.dir, name)
                if os.path.getmtime(p) < cutoff: os.remove(p)
        except OSError: pass


class EmoteImages:
    """Decodes emote images (background threads) and builds Tk photo images (Tk thread). Needs Pillow."""
    MAX_FRAMES = 120

    def __init__(self, store, height=26, on_ready=None):
        self.store = store; self.height = height; self.on_ready = on_ready or (lambda key: None)
        self.decoded = {}; self.failed = {}; self.loading = set(); self.sprites = {}; self.composites = {}
        self.lock = threading.Lock()

    def set_height(self, height):
        with self.lock: self.height = int(height); self.decoded.clear(); self.loading.clear()
        self.sprites.clear(); self.composites.clear()

    def request(self, key):
        """True when decoded; otherwise starts a background load and returns False."""
        with self.lock:
            if key in self.decoded: return True
            if key in self.loading or time.time() < self.failed.get(key, 0): return False
            self.loading.add(key); height = self.height
        threading.Thread(target=self._load, args=(key, height), daemon=True).start()
        return False

    def _load(self, key, height):
        try:
            from PIL import Image, ImageSequence
            import io
            data = self.store.image_bytes(key)
            if not data: raise ValueError('unknown emote')
            target = max(14, int(height * 0.7)) if key.startswith('badge:') else height
            im = Image.open(io.BytesIO(data)); total = getattr(im, 'n_frames', 1)
            step = max(1, -(-total // self.MAX_FRAMES)); frames = []; delays = []
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                if i % step: continue
                rgba = frame.convert('RGBA'); w, h = rgba.size
                scale = target / float(h or 1); nw = max(1, min(int(round(w * scale)), target * 4)); nh = target
                frames.append(rgba.resize((nw, nh), Image.LANCZOS))
                delays.append(max(20, int(frame.info.get('duration', 100) or 100) * step))
            if not frames: raise ValueError('empty image')
            with self.lock:
                if height == self.height: self.decoded[key] = (frames, delays)
        except Exception:
            with self.lock: self.failed[key] = time.time() + 120
        finally:
            with self.lock: self.loading.discard(key)
        self.on_ready(key)

    def sprite(self, key):
        """Tk thread. -> (PhotoImage, frames, delays) or None while not ready."""
        if key in self.sprites: return self.sprites[key]
        if not self.request(key): return None
        from PIL import ImageTk
        frames, delays = self.decoded[key]
        self.sprites[key] = (ImageTk.PhotoImage(frames[0]), frames, delays)
        return self.sprites[key]

    def group_photo(self, keys):
        """Tk thread. One emote -> its (animated) sprite; base + zero-width overlays -> a still composite. None if not all ready."""
        if len(keys) == 1:
            s = self.sprite(keys[0]); return s
        gk = '+'.join(keys)
        if gk in self.composites: return self.composites[gk]
        if not all(self.request(k) for k in keys): return None
        from PIL import Image, ImageTk
        base = self.decoded[keys[0]][0][0].copy()
        for k in keys[1:]:
            over = self.decoded[k][0][0]; scale = min(base.width / float(over.width), base.height / float(over.height), 1.0)
            if scale < 1.0: over = over.resize((max(1, int(over.width * scale)), max(1, int(over.height * scale))), Image.LANCZOS)
            base.alpha_composite(over, ((base.width - over.width) // 2, (base.height - over.height) // 2))
        self.composites[gk] = (ImageTk.PhotoImage(base), [base], [1000])
        return self.composites[gk]
