"""Dashboard + Event feed pages.

Dashboard: live status, viewers, uptime, title and category for Twitch and Kick, session totals,
and (Twitch, your own channel) editing of the stream title and category.
Event feed: Bits, Kicks, subs, gifted subs, follows, raids and channel point redemptions as they happen.
Everything is read-only except the title/category update, which only runs when you press the button.
"""
import collections
import datetime
import json
import os
import threading
import time
import tkinter as tk
import urllib.parse
from tkinter import ttk

import kick
import kick_auth

POLL_SECONDS = 30
FEED_LIMIT = 500
SHOW_LIMIT = 300

MONEY, FOLLOW, RAID, POINTS = '#ffd166', '#3ddc84', '#7cc7ff', '#b794ff'
TYPES = {
    'bits': ('BITS', 'money'), 'kicks': ('KICKS', 'money'), 'sub': ('SUB', 'money'),
    'resub': ('RESUB', 'money'), 'gift': ('GIFT SUBS', 'money'),
    'follow': ('FOLLOW', 'follow'), 'raid': ('RAID', 'raid'), 'points': ('POINTS', 'points'), 'powerup': ('POWER-UP', 'money'),
}
GROUP_COLORS = {'money': MONEY, 'follow': FOLLOW, 'raid': RAID, 'points': POINTS}
# Which kinds of events the Event feed shows. Set in Advanced Filter > Feed setup (everything is shown by default).
FEED_GROUPS = (('money', 'Bits, Kicks & subs', 'Cheers, Kicks, subs, resubs, gifted subs and Power-ups.'),
               ('follow', 'Follows', 'New followers.'),
               ('raid', 'Raids', 'Incoming raids.'),
               ('points', 'Channel points', 'Channel point redemptions.'))


def hidden_groups(app):
    """Event groups switched off in Feed setup (empty = show everything)."""
    shown = getattr(app, 'feed_show', None) or {}
    return {group for group, var in shown.items() if not var.get()}


def parse_time(text):
    """ISO or 'YYYY-MM-DD HH:MM:SS' (UTC) to an aware datetime, or None."""
    try:
        return datetime.datetime.strptime(str(text).replace(' ', 'T')[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def uptime_text(started):
    moment = parse_time(started)
    if not moment: return '-'
    seconds = max(0, int((datetime.datetime.now(datetime.timezone.utc) - moment).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}'


STREAM_DAY_START_HOUR = 5   # a stream that runs past midnight still counts as one day; the totals start fresh at 5:00


def stream_day(ts=None):
    moment = datetime.datetime.fromtimestamp(ts if ts else time.time()) - datetime.timedelta(hours=STREAM_DAY_START_HOUR)
    return moment.strftime('%Y-%m-%d')


class HoverTip:
    """Small text box that appears while the mouse rests on a widget."""
    def __init__(self, widget, text, pal, font, wrap=420):
        self.widget, self.text, self.pal, self.font, self.wrap = widget, text, pal, font, wrap
        self.tip = None; self.job = None
        widget.bind('<Enter>', self._schedule, add='+'); widget.bind('<Leave>', self._hide, add='+'); widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _e=None):
        self._hide()
        self.job = self.widget.after(200, self._show)

    def _show(self):
        self.job = None
        if self.tip is not None: return
        tip = self.tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        try: tip.attributes('-topmost', True)
        except tk.TclError: pass
        edge = tk.Frame(tip, bg=self.pal['line'], padx=1, pady=1); edge.pack()
        tk.Label(edge, text=self.text, bg=self.pal['log'], fg=self.pal['text'], font=(self.font, 10), justify='left', wraplength=self.wrap, padx=12, pady=9).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        x = max(0, min(x, self.widget.winfo_screenwidth() - tip.winfo_reqwidth() - 8))
        tip.wm_geometry('+%d+%d' % (x, y))

    def _hide(self, _e=None):
        if self.job is not None:
            try: self.widget.after_cancel(self.job)
            except Exception: pass
            self.job = None
        if self.tip is not None:
            try: self.tip.destroy()
            except Exception: pass
            self.tip = None


class EventFeed:
    def __init__(self):
        self.entries = collections.deque(maxlen=FEED_LIMIT)
        self.path = None
        self.day = stream_day()
        self.month = self.day[:7]
        self.month_totals = {'bits': 0, 'kicks': 0, 'subs': 0, 'gifted': 0, 'follows': 0, 'raids': 0, 'points': 0}
        self.reset_totals()

    def reset_totals(self):
        self.totals = {'bits': 0, 'kicks': 0, 'subs': 0, 'gifted': 0, 'follows': 0, 'raids': 0, 'points': 0}
        self.sample_totals = dict(self.totals)

    def clear(self):
        self.entries.clear(); self.reset_totals(); self.save()

    def roll_day(self):
        """A new stream day starts: yesterday's earnings are cleared. Returns True when that happened."""
        today = stream_day()
        changed = False
        if today[:7] != self.month:   # a new month starts: the monthly totals on the Dashboard begin again
            self.month = today[:7]
            self.month_totals = dict.fromkeys(self.month_totals, 0); changed = True
        if today != self.day:
            self.day = today
            self.entries.clear(); self.reset_totals(); changed = True
        if changed: self.save()
        return changed

    def load(self, path):
        """Restores today's totals and events after a restart, so the day's earnings survive closing the app."""
        self.path = path
        try:
            with open(path, 'r', encoding='utf-8') as fh: data = json.load(fh)
            if data.get('month') == stream_day()[:7]:
                for key in self.month_totals:
                    self.month_totals[key] = max(0, int((data.get('month_totals') or {}).get(key, 0)))
            if data.get('day') != stream_day(): return
            for key in self.totals:
                self.totals[key] = max(0, int((data.get('totals') or {}).get(key, 0)))
            for entry in (data.get('entries') or [])[-FEED_LIMIT:]:
                if isinstance(entry, dict) and entry.get('type') in TYPES: self.entries.append(entry)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        if not self.path: return
        try:
            data = {'day': self.day, 'month': self.month, 'month_totals': dict(self.month_totals), 'totals': {k: max(0, self.totals[k] - self.sample_totals[k]) for k in self.totals},
                    'entries': [e for e in self.entries if not e.get('sample')]}
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh: json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, platform, kind, user, text, amount=0, user_id='', sample=False):
        if kind not in TYPES: return None
        self.roll_day()
        try: amount = max(0, int(float(amount or 0)))
        except (TypeError, ValueError): amount = 0
        entry = {'ts': time.time(), 'platform': str(platform).title(), 'type': kind, 'user': str(user or 'someone'),
                 'user_id': str(user_id or ''), 'text': str(text or ''), 'amount': amount}
        if sample: entry['sample'] = True
        self.entries.append(entry)
        for t in ((self.totals, self.sample_totals) if sample else (self.totals, self.month_totals)):
            if kind == 'bits': t['bits'] += amount
            elif kind == 'kicks': t['kicks'] += amount
            elif kind in ('sub', 'resub'): t['subs'] += 1
            elif kind == 'gift': t['gifted'] += amount or 1
            elif kind == 'follow': t['follows'] += 1
            elif kind == 'raid': t['raids'] += 1
            elif kind == 'points': t['points'] += 1
        return entry


# ---- feeding events in ---------------------------------------------------------------------------------
def feed_add(app, platform, kind, user, text, amount=0, user_id='', sample=False):
    """Called on the UI thread for every Bits/Kicks/sub/follow/raid/points event."""
    if kind == 'bits': text = 'cheered ' + str(amount) + ' bits' + ((': ' + text) if text else '')
    elif kind == 'kicks': text = 'gifted ' + str(amount) + ' Kicks'
    entry = app.feed.add(platform, kind, user, text, amount, user_id, sample)
    if entry:
        if not sample: app.feed.save()
        render_feed(app)
        update_totals(app)


# ---- Event feed (lives on the Activity page, to the right of the chat) ---------------------------------
def build_session_tiles(app, parent, pal, font, page_bg):
    app.feed_pal, app.feed_font = pal, font
    row = tk.Frame(parent, bg=page_bg); row.pack(fill='x', pady=(0, 12))
    app.act_total_vars = {}
    for key, title in (('bits', 'Bits'), ('kicks', 'Kicks'), ('subs', 'Subs'), ('gifted', 'Gifted subs'), ('follows', 'Follows'), ('raids', 'Raids'), ('points', 'Points')):
        outer = tk.Frame(row, bg=pal['line'], padx=1, pady=1); outer.pack(side='left', padx=(0, 6))
        tile = tk.Frame(outer, bg=pal['bg'], padx=10, pady=5); tile.pack()
        app.act_total_vars[key] = tk.StringVar(value='0')
        tk.Label(tile, textvariable=app.act_total_vars[key], bg=pal['bg'], fg=pal['text'], font=(font, 13, 'bold')).pack()
        tk.Label(tile, text=title, bg=pal['bg'], fg=pal['muted'], font=(font, 8)).pack()
    update_totals(app)


def build_feed_panel(app, parent, pal, font, page_bg):
    """Builds the EVENT FEED header and list inside `parent` (the right-hand pane of the Activity page)."""
    app.feed_pal, app.feed_font = pal, font
    head = tk.Frame(parent, bg=page_bg); head.pack(fill='x', pady=(0, 6))
    feed_title = tk.Label(head, text='EVENT FEED', bg=page_bg, fg=pal['faint'], font=(font, 9, 'bold')); feed_title.pack(side='left')
    HoverTip(feed_title, 'Bits and Kicks show up from chat. Twitch follows, subs, raids, channel point redemptions and Power-ups need you signed in to Twitch in your own channel (log in again once to approve new permissions). Kick subs, gifted subs, redemptions and raids come from the Kick live chat feed.\n\nThe totals at the top count one stream day (they start fresh at 5:00 in the morning), so after your stream you can see what you earned.', pal, font)
    clear = tk.Label(head, text='Clear', bg=page_bg, fg=pal['muted'], font=(font, 9), cursor='hand2')
    clear.pack(side='right', padx=(8, 0))
    clear.bind('<Button-1>', lambda _e: (app.feed.clear(), render_feed(app), update_totals(app)))
    sample = tk.Label(head, text='Sample', bg=page_bg, fg=pal['muted'], font=(font, 9), cursor='hand2')
    sample.pack(side='right', padx=(8, 0))
    sample.bind('<Button-1>', lambda _e: add_samples(app))
    holder = tk.Frame(parent, bg=pal['line'], padx=1, pady=1); holder.pack(fill='both', expand=True)
    inner = tk.Frame(holder, bg=pal['log']); inner.pack(fill='both', expand=True)
    scroll = ttk.Scrollbar(inner, orient='vertical'); scroll.pack(side='right', fill='y')
    text = app.feed_text = tk.Text(inner, width=30, height=20, state='disabled', wrap='word', bg=pal['log'], fg=pal['text'], relief='flat',
                                   borderwidth=0, highlightthickness=0, font=(font, 10), padx=12, pady=10, spacing1=2, spacing3=2,
                                   selectbackground=pal['accent_dim'], selectforeground='#ffffff', yscrollcommand=scroll.set)
    scroll.configure(command=text.yview)
    text.pack(side='left', fill='both', expand=True)
    text.tag_configure('time', foreground=pal['faint'], font=(font, 9))
    text.tag_configure('detail', foreground=pal['muted'], lmargin1=12, lmargin2=12, spacing3=8)
    text.tag_configure('empty', foreground=pal['muted'])
    for group, color in GROUP_COLORS.items():
        text.tag_configure('type_' + group, foreground=color, font=(font, 9, 'bold'))
    render_feed(app)


def render_feed(app):
    text = getattr(app, 'feed_text', None)
    if text is None: return
    pal, font = app.feed_pal, app.feed_font
    hidden = hidden_groups(app)
    text.configure(state='normal')
    text.delete('1.0', 'end')
    shown = 0
    for entry in reversed(app.feed.entries):
        label, group = TYPES[entry['type']]
        if group in hidden: continue
        if shown >= SHOW_LIMIT: break
        tag = 'feed_user_%d' % shown
        text.insert('end', label + '  ', ('type_' + group,))
        text.insert('end', entry['user'], (tag,))
        text.insert('end', '  ' + time.strftime('%H:%M', time.localtime(entry['ts'])) + '  ' + entry['platform'] + '\n', ('time',))
        text.tag_configure(tag, foreground=pal['link'], font=(font, 10, 'bold'), underline=False)
        text.tag_bind(tag, '<Enter>', lambda _e, t=tag: (text.configure(cursor='hand2'), text.tag_configure(t, underline=True)))
        text.tag_bind(tag, '<Leave>', lambda _e, t=tag: (text.configure(cursor='xterm'), text.tag_configure(t, underline=False)))
        text.tag_bind(tag, '<Button-1>', lambda e, entry=entry: _open_card(app, e, entry))
        text.insert('end', entry['text'] + '\n', ('detail',))
        shown += 1
    if not shown and hidden and app.feed.entries:
        text.insert('end', 'Some event types are switched off in Advanced Filter > Feed setup.', ('empty',))
    elif not shown:
        text.insert('end', 'Nothing yet. Bits, Kicks, subs, follows, raids and points show up here as they happen.', ('empty',))
    text.configure(state='disabled')


def _open_card(app, event, entry):
    if entry['user_id'] or entry['platform'] in ('Twitch', 'Kick'):
        app._show_viewer_card(event, entry['user'], entry['platform'], {'user_id': entry['user_id'], 'badges': []})
    return 'break'


def add_samples(app):
    samples = [('Twitch', 'follow', 'SampleViewer', 'followed', 0), ('Twitch', 'sub', 'SampleSub', 'subscribed (Tier 1)', 1),
               ('Twitch', 'gift', 'SampleGifter', 'gifted 5 subs (Tier 1)', 5), ('Twitch', 'raid', 'SampleRaider', 'raided with 42 viewers', 42),
               ('Twitch', 'points', 'SamplePoints', 'redeemed Hydrate (500 points)', 500), ('Twitch', 'bits', 'SampleCheer', '', 100),
               ('Kick', 'kicks', 'SampleKicker', '', 50)]
    for platform, kind, user, text, amount in samples:
        feed_add(app, platform, kind, user, text, amount, sample=True)


def update_totals(app):
    t = app.feed.totals
    for key, variable in getattr(app, 'act_total_vars', {}).items():
        variable.set(str(t[key]))
    for key, variable in getattr(app, 'dash_total_vars', {}).items():
        variable.set(str(app.feed.month_totals[key]))


# ---- Dashboard page ------------------------------------------------------------------------------------
def build_dashboard_page(app, page, pal, font, page_bg):
    app.feed_pal, app.feed_font = pal, font
    app._page_header(page, 'Dashboard', 'Live status, viewers and stream info for your channels. Events and chat are on the Activity page. It refreshes every 30 seconds. Title and category editing works for Twitch when you are signed in to your own channel.', info=True)
    bg = page_bg
    app.dash_vars = {}
    app.dash_state = {'Twitch': {}, 'Kick': {}}
    app.dash_busy = set()
    app.dash_ids = {}
    app.dash_categories = {}
    app.dash_title_dirty = False
    row = tk.Frame(page, bg=bg); row.pack(fill='x')
    row.columnconfigure(0, weight=1, uniform='dash'); row.columnconfigure(1, weight=1, uniform='dash')
    for column, platform in enumerate(('Twitch', 'Kick')):
        outer = tk.Frame(row, bg=pal['line'], padx=1, pady=1)
        outer.grid(row=0, column=column, sticky='nsew', padx=(0, 10) if column == 0 else (10, 0))
        card = tk.Frame(outer, bg=pal['bg'], padx=18, pady=16); card.pack(fill='both', expand=True)
        color = pal['twitch'] if platform == 'Twitch' else pal['kick']
        head = tk.Frame(card, bg=pal['bg']); head.pack(fill='x')
        tk.Label(head, text=platform.upper(), bg=pal['bg'], fg=color, font=(font, 10, 'bold')).pack(side='left')
        v = app.dash_vars[platform] = {k: tk.StringVar(value=d) for k, d in
                                       (('state', 'Not connected'), ('viewers', '-'), ('uptime', '-'), ('followers', '-'), ('title', '-'), ('category', '-'))}
        pill = tk.Label(head, textvariable=v['state'], bg=pal['raised'], fg=pal['muted'], font=(font, 9, 'bold'), padx=9, pady=2)
        pill.pack(side='right'); v['pill'] = pill
        stats = tk.Frame(card, bg=pal['bg']); stats.pack(fill='x', pady=(12, 8))
        for index, (title, key) in enumerate((('WATCHING', 'viewers'), ('UPTIME', 'uptime'), ('FOLLOWERS', 'followers'))):
            cell = tk.Frame(stats, bg=pal['bg']); cell.pack(side='left', padx=(0, 28))
            tk.Label(cell, text=title, bg=pal['bg'], fg=pal['faint'], font=(font, 8, 'bold')).pack(anchor='w')
            tk.Label(cell, textvariable=v[key], bg=pal['bg'], fg=pal['text'], font=(font, 22, 'bold')).pack(anchor='w')
        tk.Label(card, text='TITLE', bg=pal['bg'], fg=pal['faint'], font=(font, 8, 'bold')).pack(anchor='w')
        tk.Label(card, textvariable=v['title'], bg=pal['bg'], fg=pal['text'], font=(font, 10), wraplength=360, justify='left', anchor='w').pack(anchor='w', pady=(0, 8))
        tk.Label(card, text='CATEGORY', bg=pal['bg'], fg=pal['faint'], font=(font, 8, 'bold')).pack(anchor='w')
        tk.Label(card, textvariable=v['category'], bg=pal['bg'], fg=pal['text'], font=(font, 10), anchor='w').pack(anchor='w')
        if platform == 'Twitch':
            tk.Frame(card, bg=pal['line'], height=1).pack(fill='x', pady=(14, 10))
            tk.Label(card, text='CHANGE STREAM INFO', bg=pal['bg'], fg=pal['faint'], font=(font, 8, 'bold')).pack(anchor='w', pady=(0, 6))
            app.dash_title_edit = tk.StringVar(); app.dash_category_edit = tk.StringVar()
            title_entry = ttk.Entry(card, textvariable=app.dash_title_edit); title_entry.pack(fill='x', ipady=3)
            title_entry.bind('<KeyRelease>', lambda _e: setattr(app, 'dash_title_dirty', True))
            tk.Label(card, text='Category (type a name, press Enter to search)', bg=pal['bg'], fg=pal['muted'], font=(font, 9)).pack(anchor='w', pady=(8, 2))
            app.dash_category_box = ttk.Combobox(card, textvariable=app.dash_category_edit, values=[])
            app.dash_category_box.pack(fill='x')
            app.dash_category_box.bind('<Return>', lambda _e: search_categories(app))
            app.dash_category_box.bind('<<ComboboxSelected>>', lambda _e: setattr(app, 'dash_title_dirty', True))
            ttk.Button(card, text='Update Twitch stream info', command=lambda: update_twitch_info(app)).pack(anchor='w', pady=(10, 0))
        else:
            tk.Frame(card, bg=pal['line'], height=1).pack(fill='x', pady=(14, 10))
            tk.Label(card, text='CHANGE STREAM INFO', bg=pal['bg'], fg=pal['faint'], font=(font, 8, 'bold')).pack(anchor='w', pady=(0, 6))
            app.kick_title_edit = tk.StringVar(); app.kick_category_edit = tk.StringVar()
            app.kick_title_dirty = False; app.kick_categories = {}
            kick_entry = ttk.Entry(card, textvariable=app.kick_title_edit); kick_entry.pack(fill='x', ipady=3)
            kick_entry.bind('<KeyRelease>', lambda _e: setattr(app, 'kick_title_dirty', True))
            tk.Label(card, text='Category (type a name, press Enter to search)', bg=pal['bg'], fg=pal['muted'], font=(font, 9)).pack(anchor='w', pady=(8, 2))
            app.kick_category_box = ttk.Combobox(card, textvariable=app.kick_category_edit, values=[])
            app.kick_category_box.pack(fill='x')
            app.kick_category_box.bind('<Return>', lambda _e: search_kick_categories(app))
            app.kick_category_box.bind('<<ComboboxSelected>>', lambda _e: setattr(app, 'kick_title_dirty', True))
            ttk.Button(card, text='Update Kick stream info', command=lambda: update_kick_info(app)).pack(anchor='w', pady=(10, 0))
    tk.Label(page, text='THIS MONTH', bg=bg, fg=pal['faint'], font=(font, 9, 'bold')).pack(anchor='w', pady=(22, 8))
    tiles = tk.Frame(page, bg=bg); tiles.pack(fill='x')
    app.dash_total_vars = {}
    for key, title in (('bits', 'Bits'), ('kicks', 'Kicks'), ('subs', 'Subs'), ('gifted', 'Gifted subs'), ('follows', 'Follows'), ('raids', 'Raids'), ('points', 'Points redeemed')):
        outer = tk.Frame(tiles, bg=pal['line'], padx=1, pady=1); outer.pack(side='left', padx=(0, 8))
        tile = tk.Frame(outer, bg=pal['bg'], padx=14, pady=10); tile.pack()
        app.dash_total_vars[key] = tk.StringVar(value='0')
        tk.Label(tile, textvariable=app.dash_total_vars[key], bg=pal['bg'], fg=pal['text'], font=(font, 16, 'bold')).pack()
        tk.Label(tile, text=title, bg=pal['bg'], fg=pal['muted'], font=(font, 9)).pack()
    update_totals(app)


def set_card(app, platform, info, error=''):
    v = app.dash_vars.get(platform)
    if not v: return
    pal = app.feed_pal
    if error:
        v['state'].set('Unavailable'); v['pill'].configure(fg=pal['muted'])
        v['viewers'].set('-'); v['uptime'].set('-'); v['followers'].set('-')
        return
    live = bool(info.get('live'))
    v['state'].set('LIVE' if live else 'Offline')
    v['pill'].configure(fg=pal['ok'] if live else pal['muted'])
    v['viewers'].set(str(info.get('viewers', 0)) if live else '-')
    v['uptime'].set(uptime_text(info.get('started')) if live else '-')
    v['followers'].set(str(info['followers']) if info.get('followers') not in (None, '') else '-')
    v['title'].set(info.get('title') or '-')
    v['category'].set(info.get('category') or '-')


def apply_status(app, platform, info, error=''):
    app.dash_busy.discard(platform)
    if not error: app.dash_state[platform] = info
    set_card(app, platform, info or {}, error)
    if platform == 'Twitch' and not error:
        if info.get('broadcaster_id'): app.dash_ids[str(info.get('login', '')).lower()] = info['broadcaster_id']
        if info.get('category') and info.get('game_id'): app.dash_categories[info['category']] = info['game_id']
        if not app.dash_title_dirty:
            app.dash_title_edit.set(info.get('title', '')); app.dash_category_edit.set(info.get('category', ''))
    if platform == 'Kick' and not error and not app.kick_title_dirty:
        if info.get('title'): app.kick_title_edit.set(info['title'])
        if info.get('category'): app.kick_category_edit.set(info['category'])


def tick_uptime(app):
    try:
        if app.feed.roll_day(): render_feed(app); update_totals(app)
    except Exception:
        pass
    try:
        for platform, info in app.dash_state.items():
            if info.get('live'):
                app.dash_vars[platform]['uptime'].set(uptime_text(info.get('started')))
    except Exception:
        pass
    app.root.after(1000, lambda: tick_uptime(app))


def poll(app, repeat=True):
    try:
        if app.twitch_token and 'Twitch' not in app.dash_busy:
            login = app.channel.get().strip().lstrip('#').lower()
            if login:
                app.dash_busy.add('Twitch')
                threading.Thread(target=_twitch_status, args=(app, login, app._can_moderate_twitch()), daemon=True).start()
        slug = (app.kick_channel.get().strip().lstrip('@') or getattr(app, 'kick_slug', '')).strip()
        if slug and 'Kick' not in app.dash_busy:
            app.dash_busy.add('Kick')
            threading.Thread(target=_kick_status, args=(app, slug), daemon=True).start()
    except Exception:
        pass
    if repeat: app.root.after(POLL_SECONDS * 1000, lambda: poll(app))


def _twitch_status(app, login, own=False):
    try:
        cached = app.dash_ids.get(login)
        if not cached:
            users = app._twitch_helix('GET', '/users?' + urllib.parse.urlencode({'login': login})).get('data') or []
            if not users: raise RuntimeError('Twitch channel not found.')
            cached = users[0]['id']
        streams = app._twitch_helix('GET', '/streams?' + urllib.parse.urlencode({'user_id': cached})).get('data') or []
        channel = (app._twitch_helix('GET', '/channels?' + urllib.parse.urlencode({'broadcaster_id': cached})).get('data') or [{}])[0]
        stream = streams[0] if streams else {}
        followers = ''
        if own:
            try: followers = app._twitch_helix('GET', '/channels/followers?' + urllib.parse.urlencode({'broadcaster_id': cached, 'first': 1})).get('total', '')
            except Exception: followers = ''
        info = {'live': bool(stream), 'viewers': int(stream.get('viewer_count') or 0), 'started': stream.get('started_at') or '',
                'title': channel.get('title') or stream.get('title') or '', 'category': channel.get('game_name') or stream.get('game_name') or '',
                'game_id': channel.get('game_id') or stream.get('game_id') or '', 'broadcaster_id': cached, 'login': login, 'followers': followers}
        app.ui_events.put(('dash_status', ('Twitch', info, '')))
    except Exception as exc:
        app.ui_events.put(('dash_status', ('Twitch', {}, str(exc) or 'error')))


def _kick_status(app, slug):
    try:
        app.ui_events.put(('dash_status', ('Kick', kick.fetch_channel_status(slug), '')))
    except Exception as exc:
        app.ui_events.put(('dash_status', ('Kick', {}, str(exc) or 'error')))


# ---- changing the Twitch title / category --------------------------------------------------------------
def search_categories(app):
    query = app.dash_category_edit.get().strip()
    if not query or not app.twitch_token: return 'break'
    def run():
        try:
            rows = app._twitch_helix('GET', '/search/categories?' + urllib.parse.urlencode({'query': query, 'first': 8})).get('data') or []
            app.ui_events.put(('dash_categories', [(r.get('name', ''), r.get('id', '')) for r in rows if r.get('name') and r.get('id')]))
        except Exception as exc:
            app._report('Category search failed: ' + str(exc))
    threading.Thread(target=run, daemon=True).start()
    return 'break'


def apply_categories(app, pairs):
    for name, ident in pairs: app.dash_categories[name] = ident
    names = [name for name, _ in pairs]
    app.dash_category_box.configure(values=names)
    if names:
        app.dash_category_box.focus_set()
        try: app.dash_category_box.event_generate('<Down>')
        except tk.TclError: pass
    else:
        app._report('No Twitch category matched that name.')


def update_twitch_info(app):
    if not app._can_moderate_twitch():
        app._report('Changing the title needs you signed in to Twitch in your own channel.'); return
    title = app.dash_title_edit.get().strip()
    category = app.dash_category_edit.get().strip()
    if not title:
        app._report('Type a stream title first.'); return
    if len(title) > 140:
        app._report('Twitch titles are at most 140 characters.'); return
    current = app.dash_state.get('Twitch', {})
    payload = {'title': title}
    if category and category != current.get('category'):
        match = next((ident for name, ident in app.dash_categories.items() if name.casefold() == category.casefold()), '')
        if not match:
            app._report('Pick the category from the list: type its name and press Enter in the category box.'); return
        payload['game_id'] = match
    broadcaster = str(app.twitch_user_id)
    def run():
        try:
            app._twitch_helix('PATCH', '/channels?' + urllib.parse.urlencode({'broadcaster_id': broadcaster}), payload)
            app._report('Updated your Twitch stream info.')
            app.ui_events.put(('dash_saved', None))
        except Exception as exc:
            app._report('Could not update Twitch stream info: ' + str(exc))
    threading.Thread(target=run, daemon=True).start()


def saved(app):
    app.dash_title_dirty = False
    poll(app, repeat=False)


# ---- changing the Kick title / category ----------------------------------------------------------------
def search_kick_categories(app):
    query = app.kick_category_edit.get().strip()
    if not query or not app._can_moderate_kick(): return 'break'
    def run():
        try:
            token = app._kick_access_token()
            app.ui_events.put(('dash_kick_categories', kick_auth.search_categories(token, query)))
        except Exception as exc:
            app._report('Kick category search failed: ' + str(exc))
    threading.Thread(target=run, daemon=True).start()
    return 'break'


def apply_kick_categories(app, pairs):
    for name, ident in pairs: app.kick_categories[name] = ident
    names = [name for name, _ in pairs]
    app.kick_category_box.configure(values=names)
    if names:
        app.kick_category_box.focus_set()
        try: app.kick_category_box.event_generate('<Down>')
        except tk.TclError: pass
    else:
        app._report('No Kick category matched that name.')


def update_kick_info(app):
    if not app._can_moderate_kick():
        app._report('Changing the title needs you signed in to Kick in your own channel.'); return
    title = app.kick_title_edit.get().strip()
    category = app.kick_category_edit.get().strip()
    if not title:
        app._report('Type a stream title first.'); return
    if len(title) > 140:
        app._report('Kick titles are at most 140 characters.'); return
    current = app.dash_state.get('Kick', {})
    category_id = None
    if category and category != current.get('category'):
        category_id = next((ident for name, ident in app.kick_categories.items() if name.casefold() == category.casefold()), None)
        if category_id is None:
            app._report('Pick the category from the list: type its name and press Enter in the category box.'); return
    def run():
        try:
            kick_auth.update_channel(app._kick_access_token(), title, category_id)
            app._report('Updated your Kick stream info.')
            app.ui_events.put(('dash_kick_saved', None))
        except Exception as exc:
            app._report('Could not update Kick stream info: ' + str(exc))
    threading.Thread(target=run, daemon=True).start()


def kick_saved(app):
    app.kick_title_dirty = False
    poll(app, repeat=False)
