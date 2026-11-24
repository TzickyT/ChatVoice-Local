"""Viewer card: the popup that opens when you click a name in Activity.

Top: who they are (roles, account age, followage, paid TTS time, your own note).
Middle: app-only actions (mute their TTS). Bottom: real Twitch / Kick moderation.
Nothing here talks to Twitch or Kick except the same calls the old menu already used,
plus read-only profile look-ups for Twitch.
"""
import base64
import datetime
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser

ROLE_LABELS = (
    ('broadcaster', 'Broadcaster'), ('moderator', 'Mod'), ('vip', 'VIP'),
    ('founder', 'Founder'), ('subscriber', 'Sub'), ('og', 'OG'),
    ('verified', 'Verified'), ('staff', 'Staff'),
)
DANGER = '#ff6b6b'


def parse_iso(text):
    try:
        return datetime.datetime.strptime(str(text)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def date_text(moment):
    return moment.astimezone().strftime('%d %b %Y')


def span_text(moment):
    days = max(0, (datetime.datetime.now(datetime.timezone.utc) - moment).days)
    years, rest = divmod(days, 365)
    months = rest // 30
    parts = []
    if years: parts.append(f'{years} year' + ('s' if years != 1 else ''))
    if months: parts.append(f'{months} month' + ('s' if months != 1 else ''))
    if not parts:
        parts.append('today' if days == 0 else f'{days} day' + ('s' if days != 1 else ''))
    return ', '.join(parts)


def fetch_profile(app, card, user_id):
    """Runs in a worker thread. Results go back through the app's UI event queue."""
    info = {}
    try:
        data = app._twitch_helix('GET', '/users?' + urllib.parse.urlencode({'id': user_id}))
        row = (data.get('data') or [{}])[0]
        info['created'] = row.get('created_at') or ''
        url = str(row.get('profile_image_url') or '')
        if url:
            url = url.replace('-300x300.', '-70x70.')
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'ChatVoiceLocal'})
                with urllib.request.urlopen(request, timeout=8) as response:
                    raw = response.read(400000)
                if raw[:8] == b'\x89PNG\r\n\x1a\n':
                    info['avatar'] = base64.b64encode(raw).decode('ascii')
            except Exception:
                pass
    except Exception as exc:
        info['error'] = str(exc)
    if app._can_moderate_twitch() and str(user_id) != str(app.twitch_user_id):
        try:
            query = urllib.parse.urlencode({'broadcaster_id': app.twitch_user_id, 'user_id': user_id})
            data = app._twitch_helix('GET', '/channels/followers?' + query)
            rows = data.get('data') or []
            info['followed'] = (rows[0].get('followed_at') or '') if rows else ''
        except Exception as exc:
            info['follow_error'] = 'Log out and log in to Twitch again to show this' if 'permission' in str(exc) else 'Not available right now'
    app.ui_events.put(('viewer_card', (card, info)))


class ViewerCard:
    def __init__(self, app, event, name, platform, meta, pal, font):
        self.app, self.name, self.platform = app, name, str(platform).title()
        self.meta = meta or {}
        self.pal, self.font = pal, font
        self.user_id = str(self.meta.get('user_id') or '')
        self.opened = time.time()
        self.alive = True
        self.values = {}
        self.avatar_image = None
        self.click_binding = None
        badges = [str(b).lower() for b in (self.meta.get('badges') or [])]
        if not badges:
            badges = list(app._recall_badges(self.platform, name, self.user_id) or [])
        self.badges = badges
        self._build(event)
        if self.platform == 'Twitch' and self.user_id and app.twitch_token:
            threading.Thread(target=fetch_profile, args=(app, self, self.user_id), daemon=True).start()

    # ---- building ------------------------------------------------------------------------------------
    def _label(self, parent, text='', size=10, bold=False, fg=None, **kw):
        return tk.Label(parent, text=text, bg=kw.pop('bg', self.pal['bg']), fg=fg or self.pal['text'],
                        font=(self.font, size, 'bold' if bold else 'normal'), **kw)

    def _button(self, parent, text, command, danger=False, enabled=True):
        pal = self.pal
        fg = DANGER if danger else pal['text']
        if not enabled: fg = pal['faint']
        button = tk.Label(parent, text=text, anchor='w', padx=12, pady=7, bg=pal['raised'], fg=fg,
                          font=(self.font, 10), cursor='hand2' if enabled else 'arrow')
        button.pack(fill='x', pady=2)
        if enabled:
            button.bind('<Enter>', lambda _e: button.configure(bg=pal['hover']))
            button.bind('<Leave>', lambda _e: button.configure(bg=pal['raised']))
            button.bind('<Button-1>', lambda _e: (self._run(command), 'break')[1])
        return button

    def _section(self, parent, text):
        tk.Frame(parent, bg=self.pal['line'], height=1).pack(fill='x', pady=(12, 8))
        self._label(parent, text, 8, True, self.pal['faint']).pack(anchor='w', pady=(0, 4))

    def _build(self, event):
        app, pal = self.app, self.pal
        top = self.top = tk.Toplevel(app.root)
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=pal['line'])
        try: top.attributes('-topmost', True)
        except tk.TclError: pass
        body = tk.Frame(top, bg=pal['bg'], padx=16, pady=14)
        body.pack(padx=1, pady=1, fill='both')

        # -- profile (top) --
        head = tk.Frame(body, bg=pal['bg']); head.pack(fill='x')
        self.avatar = tk.Canvas(head, width=70, height=70, bg=pal['bg'], highlightthickness=0)
        self.avatar.pack(side='left')
        self._draw_initial()
        who = tk.Frame(head, bg=pal['bg']); who.pack(side='left', padx=(12, 0), anchor='n')
        self._label(who, self.name, 15, True).pack(anchor='w')
        self._label(who, self.platform, 9, True, pal['twitch'] if self.platform == 'Twitch' else pal['kick']).pack(anchor='w')
        chips = tk.Frame(who, bg=pal['bg']); chips.pack(anchor='w', pady=(6, 0))
        shown = [label for key, label in ROLE_LABELS if key in self.badges]
        for label in shown:
            self._label(chips, label, 8, True, pal['link'], bg=pal['accent_dim'], padx=7, pady=1).pack(side='left', padx=(0, 4))
        if not shown:
            self._label(chips, 'Viewer', 8, False, pal['muted']).pack(side='left')

        info = tk.Frame(body, bg=pal['bg']); info.pack(fill='x', pady=(12, 0))
        rows = []
        if self.platform == 'Twitch':
            rows.append(('created', 'Account created', 'Loading…' if (self.user_id and app.twitch_token) else 'Sign in to Twitch to see this'))
            if app._can_moderate_twitch() and 'broadcaster' not in self.badges:
                rows.append(('followed', 'Following you', 'Loading…' if self.user_id else 'Unknown'))
        else:
            rows.append(('created', 'Account details', 'Only shown for Twitch viewers'))
        rows.append(('messages', 'Messages in Activity', str(self._message_count())))
        paid = self._paid_text()
        if paid: rows.append(('paid', 'Paid TTS', paid))
        rows.append(('tts', 'TTS', self._tts_text()))
        for index, (key, title, value) in enumerate(rows):
            self._label(info, title, 9, False, pal['muted']).grid(row=index, column=0, sticky='nw', pady=2)
            variable = tk.StringVar(value=value)
            self.values[key] = variable
            tk.Label(info, textvariable=variable, bg=pal['bg'], fg=pal['text'], font=(self.font, 9), anchor='w',
                     justify='left', wraplength=210).grid(row=index, column=1, sticky='nw', padx=(14, 0), pady=2)

        self._label(body, 'YOUR NOTE (only on this PC)', 8, True, pal['faint']).pack(anchor='w', pady=(12, 4))
        self.note = tk.Entry(body, bg=pal['raised'], fg=pal['text'], insertbackground=pal['text'], relief='flat',
                             font=(self.font, 10), highlightthickness=1, highlightbackground=pal['line'], highlightcolor=pal['accent'])
        self.note.pack(fill='x', ipady=5)
        self.note.insert(0, self._load_note())
        self.note.bind('<Button-1>', lambda _e: self.note.focus_force())
        self.note.bind('<Return>', lambda _e: (self._save_note(), self.close(), 'break')[2])

        # -- app only (middle) --
        self._section(body, 'APP ONLY')
        if self._muted():
            self._button(body, 'Unmute TTS (app only)', self._unmute)
        else:
            self._button(body, 'Mute TTS (app only)', self._mute)

        # -- real moderation (bottom) --
        self._section(body, 'ON ' + self.platform.upper())
        if not app._can_moderate(self.platform):
            self._label(body, 'Moderation needs you signed in to ' + self.platform + ' in your own channel.', 9, False, pal['muted'], wraplength=260, justify='left').pack(anchor='w')
        else:
            message_id = self.meta.get('message_id')
            if message_id: self._button(body, 'Delete this message', lambda: app._moderate('delete', self.name, self.platform, self.meta))
            if self.user_id:
                self._button(body, 'Timeout ' + app._format_duration(app._timeout_seconds()), lambda: app._moderate('timeout', self.name, self.platform, self.meta), danger=True)
                self._button(body, 'Ban from stream', lambda: app._moderate('ban', self.name, self.platform, self.meta), danger=True)
                self._button(body, 'Unban', lambda: app._moderate('unban', self.name, self.platform, self.meta))
            else:
                self._label(body, 'No user id for this message, so it cannot be moderated.', 9, False, pal['muted'], wraplength=260, justify='left').pack(anchor='w')

        tk.Frame(body, bg=pal['line'], height=1).pack(fill='x', pady=(12, 6))
        footer = tk.Frame(body, bg=pal['bg']); footer.pack(fill='x')
        link = self._label(footer, 'Open channel page', 9, False, pal['link'], cursor='hand2')
        link.pack(side='left')
        link.bind('<Button-1>', lambda _e: self._run(self._open_page))
        close = self._label(footer, 'Close', 9, False, pal['muted'], cursor='hand2')
        close.pack(side='right')
        close.bind('<Button-1>', lambda _e: self.close())

        top.bind('<Escape>', lambda _e: self.close())
        self.note.bind('<Escape>', lambda _e: self.close())
        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        x = max(0, min(event.x_root, top.winfo_screenwidth() - width - 8))
        y = max(0, min(event.y_root, top.winfo_screenheight() - height - 48))
        top.geometry(f'+{x}+{y}')
        top.deiconify()
        top.lift()
        try: top.focus_force()
        except tk.TclError: pass
        self.click_binding = app.root.bind('<Button-1>', self._outside_click, add='+')

    def _draw_initial(self):
        pal = self.pal
        self.avatar.delete('all')
        self.avatar.create_oval(3, 3, 67, 67, fill=pal['accent_dim'], outline=pal['accent'], width=2)
        self.avatar.create_text(35, 35, text=(self.name[:1] or '?').upper(), fill=pal['link'], font=(self.font, 24, 'bold'))

    # ---- data ---------------------------------------------------------------------------------------
    def _norm(self):
        return self.app._normalize_username(self.name)

    def _message_count(self):
        norm = self._norm()
        return sum(1 for entry in self.app.activity_entries
                   if entry.get('type') == 'chat' and str(entry.get('platform')).title() == self.platform
                   and self.app._normalize_username(entry.get('user')) == norm)

    def _muted(self):
        return (self.platform, self._norm()) in self.app.ignored_users

    def _tts_text(self):
        return 'Muted (app only)' if self._muted() else 'Read aloud'

    def _paid_text(self):
        app = self.app
        try:
            if not app.paid_voices_enabled.get(): return ''
            if app._is_streamer(self.platform, self.name): return 'Free (you)'
            role = app._free_role(self.badges)
            if role: return 'Free (' + role + ')'
            expiry = app._paid_expiry(self.platform, self.name, self.user_id)
            if expiry > time.time():
                return 'Until ' + datetime.datetime.fromtimestamp(expiry).strftime('%d %b %Y')
            return 'No paid time'
        except Exception:
            return ''

    def _note_key(self):
        return self.platform + ('|id:' + self.user_id if self.user_id else '|name:' + self._norm())

    def _load_note(self):
        notes = self.app.settings.get('viewer_notes')
        return str(notes.get(self._note_key(), '')) if isinstance(notes, dict) else ''

    def _save_note(self):
        try: text = self.note.get().strip()[:300]
        except tk.TclError: return
        notes = self.app.settings.get('viewer_notes')
        if not isinstance(notes, dict): notes = {}
        key = self._note_key()
        if text == str(notes.get(key, '')): return
        if text: notes[key] = text
        else: notes.pop(key, None)
        self.app.settings['viewer_notes'] = notes
        self.app._save_connection_settings()

    # ---- actions -------------------------------------------------------------------------------------
    def _run(self, command):
        self.close()
        command()

    def _mute(self):
        self.app.add_ignored_user(self.name, self.platform)

    def _unmute(self):
        self.app.unblock_ignored_user((self.platform, self._norm()))

    def _open_page(self):
        host = 'https://www.twitch.tv/' if self.platform == 'Twitch' else 'https://kick.com/'
        webbrowser.open(host + urllib.parse.quote(self._norm()))

    def apply(self, info):
        """Fills in the Twitch profile once the look-up finishes (called on the UI thread)."""
        if not self.alive: return
        if 'error' in info and 'created' not in info:
            self.values['created'].set('Not available right now')
        elif 'created' in self.values:
            moment = parse_iso(info.get('created'))
            self.values['created'].set((date_text(moment) + '  (' + span_text(moment) + ')') if moment else 'Unknown')
        if 'followed' in self.values:
            if 'follow_error' in info: self.values['followed'].set(info['follow_error'])
            elif 'followed' in info:
                moment = parse_iso(info.get('followed'))
                self.values['followed'].set(('Since ' + date_text(moment) + '  (' + span_text(moment) + ')') if moment else 'Not following')
            else: self.values['followed'].set('Unknown')
        if info.get('avatar'):
            try:
                self.avatar_image = tk.PhotoImage(data=info['avatar'])
                self.avatar.delete('all')
                self.avatar.create_image(35, 35, image=self.avatar_image)
                self.avatar.create_rectangle(1, 1, 69, 69, outline=self.pal['accent'], width=2)
            except tk.TclError:
                self.avatar_image = None

    def _outside_click(self, _event):
        if time.time() - self.opened > 0.3: self.close()

    def close(self):
        if not self.alive: return
        self.alive = False
        self._save_note()
        try:
            if self.click_binding: self.app.root.unbind('<Button-1>', self.click_binding)
        except tk.TclError: pass
        try: self.top.destroy()
        except tk.TclError: pass
        if getattr(self.app, '_viewer_card', None) is self: self.app._viewer_card = None
