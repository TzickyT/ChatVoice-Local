"""Channel points page and Chat controls page (Twitch, your own channel).

Channel points: your rewards, switch them on or off, change the cost, create new ones, and approve or refuse
redemptions that are waiting. Twitch only lets an app change rewards that this same app created, so rewards
made on twitch.tv are shown but read-only.
Chat controls: slow mode, followers-only, subscribers-only, emote-only, shield mode, announcements, clear chat.
Every change happens only when you press or switch something.
"""
import threading
import tkinter as tk
import urllib.parse
from tkinter import ttk, messagebox, simpledialog

import kick_auth


def _q(**kw):
    return urllib.parse.urlencode(kw)


def _clamp(text, low, high, default):
    try: return max(low, min(high, int(str(text).strip())))
    except (TypeError, ValueError): return default


def _is_kick(app):
    return getattr(app, 'rewards_platform', None) is not None and app.rewards_platform.get() == 'Kick'


def _need_own(app, platform=None):
    platform = platform or ('Kick' if _is_kick(app) else 'Twitch')
    if platform == 'Kick':
        if app._can_moderate_kick(): return True
    elif app._can_moderate_twitch(): return True
    app._report('This needs you signed in to ' + platform + ' in your own channel.')
    return False


# ======================================================================================================
# Channel points
# ======================================================================================================
def build_rewards_page(app, page, pal, font, page_bg):
    app._page_header(page, 'Channel points', 'Your rewards and the redemptions waiting for you, for Twitch or Kick. On Twitch, the app can only change rewards that it created itself: rewards you made on twitch.tv are listed but read-only. Kick needs the editing permissions ticked in your Kick app first (see the README).')
    app.rewards_platform = tk.StringVar(value='Twitch')
    app.reward_rows, app.reward_mine, app.pending_rows, app.rewards_busy = {}, set(), {}, False
    bar = ttk.Frame(page, style='Page.TFrame'); bar.pack(fill='x', pady=(0, 8))
    platform_box = ttk.Combobox(bar, textvariable=app.rewards_platform, values=('Twitch', 'Kick'), state='readonly', width=8)
    platform_box.pack(side='left', padx=(0, 8))
    platform_box.bind('<<ComboboxSelected>>', lambda _e: (app.rewards_busy and setattr(app, 'rewards_busy', False), load_rewards(app)))
    ttk.Button(bar, text='Refresh', style='Outline.TButton', command=lambda: load_rewards(app)).pack(side='left')
    ttk.Button(bar, text='Turn on / off', style='Outline.TButton', command=lambda: toggle_reward(app)).pack(side='left', padx=8)
    ttk.Button(bar, text='Change cost', style='Outline.TButton', command=lambda: change_cost(app)).pack(side='left')
    ttk.Button(bar, text='New reward', command=lambda: new_reward(app)).pack(side='left', padx=8)
    app.rewards_note = tk.StringVar(value='')
    tk.Label(page, textvariable=app.rewards_note, bg=page_bg, fg=pal['muted'], font=(font, 9), anchor='w', justify='left', wraplength=760).pack(fill='x', pady=(0, 6))
    app.rewards_tree = ttk.Treeview(page, columns=('title', 'cost', 'state', 'edit'), show='headings', height=6, selectmode='browse')
    for key, title, width in (('title', 'Reward', 330), ('cost', 'Cost', 90), ('state', 'State', 90), ('edit', 'Control', 150)):
        app.rewards_tree.heading(key, text=title); app.rewards_tree.column(key, width=width, anchor='w')
    app.rewards_tree.pack(fill='x')
    tk.Label(page, text='WAITING FOR YOU', bg=page_bg, fg=pal['faint'], font=(font, 9, 'bold')).pack(anchor='w', pady=(18, 6))
    app.pending_tree = ttk.Treeview(page, columns=('reward', 'user', 'cost', 'note'), show='headings', height=5, selectmode='browse')
    for key, title, width in (('reward', 'Reward', 220), ('user', 'Viewer', 150), ('cost', 'Cost', 80), ('note', 'Message', 270)):
        app.pending_tree.heading(key, text=title); app.pending_tree.column(key, width=width, anchor='w')
    app.pending_tree.pack(fill='x')
    bar2 = ttk.Frame(page, style='Page.TFrame'); bar2.pack(fill='x', pady=(8, 0))
    ttk.Button(bar2, text='Approve', command=lambda: resolve(app, 'FULFILLED')).pack(side='left')
    ttk.Button(bar2, text='Refuse and refund points', style='Outline.TButton', command=lambda: resolve(app, 'CANCELED')).pack(side='left', padx=8)


def load_rewards(app):
    if not hasattr(app, 'rewards_tree'): return
    if _is_kick(app):
        _load_kick_rewards(app); return
    if not app._can_moderate_twitch():
        app.rewards_note.set('Sign in to Twitch in your own channel to see and manage your rewards.')
        return
    if app.rewards_busy: return
    app.rewards_busy = True
    app.rewards_note.set('Loading…')
    broadcaster = str(app.twitch_user_id)
    def run():
        try:
            everything = app._twitch_helix('GET', '/channel_points/custom_rewards?' + _q(broadcaster_id=broadcaster)).get('data') or []
            mine = app._twitch_helix('GET', '/channel_points/custom_rewards?' + _q(broadcaster_id=broadcaster, only_manageable_rewards='true')).get('data') or []
            pending = []
            for reward in mine:
                rows = app._twitch_helix('GET', '/channel_points/custom_rewards/redemptions?' + _q(broadcaster_id=broadcaster, reward_id=reward['id'], status='UNFULFILLED', first=50)).get('data') or []
                for row in rows:
                    row['_reward_id'] = reward['id']; pending.append(row)
            app.ui_events.put(('rewards_data', (everything, {r['id'] for r in mine}, pending, '')))
        except Exception as exc:
            app.ui_events.put(('rewards_data', ([], set(), [], str(exc) or 'error')))
    threading.Thread(target=run, daemon=True).start()


def apply_rewards(app, payload):
    everything, mine_ids, pending, error = payload
    app.rewards_busy = False
    for tree in (app.rewards_tree, app.pending_tree):
        tree.delete(*tree.get_children())
    app.reward_rows, app.reward_mine, app.pending_rows = {}, set(mine_ids), {}
    if error:
        app.rewards_note.set('Could not load rewards: ' + error)
        return
    for reward in everything:
        rid = reward.get('id')
        if not rid: continue
        state = 'Off' if not reward.get('is_enabled', True) else ('Paused' if reward.get('is_paused') else 'On')
        app.reward_rows[rid] = reward
        app.rewards_tree.insert('', 'end', iid=rid, values=(reward.get('title', ''), reward.get('cost', ''), state, 'You can edit' if rid in app.reward_mine else 'Twitch only'))
    for row in pending:
        rid = row.get('id')
        if not rid: continue
        reward = row.get('reward') or {}
        app.pending_rows[rid] = row
        app.pending_tree.insert('', 'end', iid=rid, values=(reward.get('title', ''), row.get('user_name', ''), reward.get('cost', ''), row.get('user_input', '')))
    if not everything: app.rewards_note.set('No channel point rewards found. On Twitch, channel points need Affiliate or Partner status. On Kick, the rewards permissions must be switched on (see the Dashboard).')
    else: app.rewards_note.set(str(len(everything)) + ' rewards, ' + str(len(app.reward_mine)) + ' you can edit here, ' + str(len(app.pending_rows)) + ' waiting.')


def _load_kick_rewards(app):
    if not app._can_moderate_kick():
        app.rewards_note.set('Sign in to Kick in your own channel to see and manage your rewards.')
        return
    if app.rewards_busy: return
    app.rewards_busy = True
    app.rewards_note.set('Loading…')
    def run():
        try:
            token = app._kick_access_token()
            rewards = kick_auth.list_rewards(token)
            pending = []
            try:
                for row in kick_auth.list_redemptions(token):
                    pending.append({'id': row['id'], 'user_name': row['user'], 'user_input': row['input'],
                                    'reward': {'title': row['reward_title'], 'cost': row['cost']}})
            except Exception as exc:
                app._report('Could not read waiting Kick redemptions: ' + str(exc))
            app.ui_events.put(('rewards_data', (rewards, {r['id'] for r in rewards}, pending, '')))
        except Exception as exc:
            app.ui_events.put(('rewards_data', ([], set(), [], str(exc) or 'error')))
    threading.Thread(target=run, daemon=True).start()


def _selected_reward(app):
    selection = app.rewards_tree.selection()
    reward = app.reward_rows.get(selection[0]) if selection else None
    if not reward:
        app._report('Select a reward in the list first.')
        return None
    if reward['id'] not in app.reward_mine:
        app._report('Twitch only lets this app change rewards it created. Change "' + str(reward.get('title', '')) + '" on twitch.tv.')
        return None
    return reward


def _send(app, method, path, payload, done):
    def run():
        try:
            app._twitch_helix(method, path, payload)
            app._report(done)
            app.ui_events.put(('rewards_reload', None))
        except Exception as exc:
            app._report('Twitch refused that: ' + str(exc))
            app.ui_events.put(('rewards_reload', None))
    threading.Thread(target=run, daemon=True).start()


def _send_kick(app, action, done):
    """Runs action(token) on a worker thread, reports, then reloads the Kick lists."""
    def run():
        try:
            action(app._kick_access_token())
            app._report(done)
        except Exception as exc:
            app._report('Kick refused that: ' + str(exc))
        app.ui_events.put(('rewards_reload', None))
    threading.Thread(target=run, daemon=True).start()


def toggle_reward(app):
    if not _need_own(app): return
    reward = _selected_reward(app)
    if not reward: return
    turn_on = not reward.get('is_enabled', True)
    done = 'Turned ' + ('on' if turn_on else 'off') + ' "' + str(reward.get('title', '')) + '".'
    if _is_kick(app):
        _send_kick(app, lambda token: kick_auth.update_reward(token, reward['id'], {'is_enabled': turn_on}), done); return
    _send(app, 'PATCH', '/channel_points/custom_rewards?' + _q(broadcaster_id=app.twitch_user_id, id=reward['id']), {'is_enabled': turn_on}, done)


def change_cost(app):
    if not _need_own(app): return
    reward = _selected_reward(app)
    if not reward: return
    cost = simpledialog.askinteger('Change cost', 'New cost in channel points for "' + str(reward.get('title', '')) + '":', parent=app.root,
                                   initialvalue=reward.get('cost') or 100, minvalue=1, maxvalue=10000000)
    if not cost: return
    done = 'Changed the cost of "' + str(reward.get('title', '')) + '" to ' + str(cost) + '.'
    if _is_kick(app):
        _send_kick(app, lambda token: kick_auth.update_reward(token, reward['id'], {'cost': cost}), done); return
    _send(app, 'PATCH', '/channel_points/custom_rewards?' + _q(broadcaster_id=app.twitch_user_id, id=reward['id']), {'cost': cost}, done)


def new_reward(app):
    if not _need_own(app): return
    title = simpledialog.askstring('New reward', 'Reward name (up to 45 characters):', parent=app.root)
    title = (title or '').strip()[:45]
    if not title: return
    cost = simpledialog.askinteger('New reward', 'Cost in channel points:', parent=app.root, initialvalue=100, minvalue=1, maxvalue=10000000)
    if not cost: return
    done = 'Created the reward "' + title + '" (' + str(cost) + ' points).'
    if _is_kick(app):
        _send_kick(app, lambda token: kick_auth.create_reward(token, title, cost), done); return
    _send(app, 'POST', '/channel_points/custom_rewards?' + _q(broadcaster_id=app.twitch_user_id), {'title': title, 'cost': cost}, done)


def resolve(app, status):
    if not _need_own(app): return
    selection = app.pending_tree.selection()
    row = app.pending_rows.get(selection[0]) if selection else None
    if not row:
        app._report('Select a waiting redemption first.'); return
    who = str(row.get('user_name', 'this viewer')); title = str((row.get('reward') or {}).get('title', 'the reward'))
    if status == 'CANCELED' and not messagebox.askyesno('Refuse redemption', 'Refuse "' + title + '" from ' + who + ' and give their points back?'): return
    done = ('Approved ' if status == 'FULFILLED' else 'Refused and refunded ') + '"' + title + '" from ' + who + '.'
    if _is_kick(app):
        _send_kick(app, lambda token: kick_auth.resolve_redemptions(token, [row['id']], accept=(status == 'FULFILLED')), done); return
    _send(app, 'PATCH', '/channel_points/custom_rewards/redemptions?' + _q(id=row['id'], broadcaster_id=app.twitch_user_id, reward_id=row['_reward_id']), {'status': status}, done)


# ======================================================================================================
# Chat controls
# ======================================================================================================
CONTROL_ROWS = (
    ('slow', 'Slow mode', 'Each viewer can post once every few seconds.', 'seconds', '30'),
    ('follow', 'Followers-only', 'Only people who follow you can chat. The number is how many minutes they must have followed (0 = any follower).', 'minutes', '10'),
    ('sub', 'Subscribers-only', 'Only subscribers can chat.', None, None),
    ('emote', 'Emote-only', 'Chat can only contain emotes.', None, None),
    ('shield', 'Shield mode', "Twitch's emergency protection, using the settings you saved on twitch.tv.", None, None),
)


def build_controls_page(app, page, pal, font, page_bg, toggle_cls):
    app._page_header(page, 'Chat controls', 'Switch chat modes, send an announcement or clear chat on Twitch. These change your live chat the moment you switch them, and need you signed in to your own Twitch channel. Kick has no equivalent here yet.')
    app.ctl_vars = {key: tk.BooleanVar(value=False) for key, *_ in CONTROL_ROWS}
    app.ctl_nums = {'slow': tk.StringVar(value='30'), 'follow': tk.StringVar(value='10')}
    app.ctl_loading = False
    outer = tk.Frame(page, bg=pal['line'], padx=1, pady=1); outer.pack(fill='x')
    box = tk.Frame(outer, bg=pal['bg'], padx=20, pady=6); box.pack(fill='x')
    for index, (key, title, desc, unit, default) in enumerate(CONTROL_ROWS):
        if index: tk.Frame(box, bg=pal['line'], height=1).pack(fill='x')
        row = tk.Frame(box, bg=pal['bg'], pady=10); row.pack(fill='x')
        text = tk.Frame(row, bg=pal['bg']); text.pack(side='left', fill='x', expand=True)
        tk.Label(text, text=title, bg=pal['bg'], fg=pal['text'], font=(font, 10, 'bold')).pack(anchor='w')
        tk.Label(text, text=desc, bg=pal['bg'], fg=pal['muted'], font=(font, 9), wraplength=520, justify='left').pack(anchor='w')
        toggle_cls(row, app.ctl_vars[key], bg=pal['bg']).pack(side='right')
        if unit:
            tk.Label(row, text=unit, bg=pal['bg'], fg=pal['muted'], font=(font, 9)).pack(side='right', padx=(6, 14))
            number = ttk.Entry(row, textvariable=app.ctl_nums[key], width=6); number.pack(side='right')
            number.bind('<Return>', lambda _e, k=key: on_toggle(app, k) if app.ctl_vars[k].get() else None)
        app.ctl_vars[key].trace_add('write', lambda *_a, k=key: on_toggle(app, k))
    tk.Label(page, text='ANNOUNCEMENT', bg=page_bg, fg=pal['faint'], font=(font, 9, 'bold')).pack(anchor='w', pady=(20, 6))
    line = ttk.Frame(page, style='Page.TFrame'); line.pack(fill='x')
    app.announce_text = tk.StringVar(); app.announce_color = tk.StringVar(value='primary')
    entry = ttk.Entry(line, textvariable=app.announce_text); entry.pack(side='left', fill='x', expand=True, ipady=4)
    entry.bind('<Return>', lambda _e: send_announcement(app))
    ttk.Combobox(line, textvariable=app.announce_color, values=('primary', 'blue', 'green', 'orange', 'purple'), state='readonly', width=9).pack(side='left', padx=8)
    ttk.Button(line, text='Send', command=lambda: send_announcement(app)).pack(side='left')
    bar = ttk.Frame(page, style='Page.TFrame'); bar.pack(fill='x', pady=(18, 0))
    ttk.Button(bar, text='Reload current state', style='Outline.TButton', command=lambda: load_controls(app)).pack(side='left')
    ttk.Button(bar, text='Clear chat', style='Outline.TButton', command=lambda: clear_chat(app)).pack(side='left', padx=8)


def load_controls(app):
    if not hasattr(app, 'ctl_vars') or not app._can_moderate_twitch(): return
    broadcaster = str(app.twitch_user_id)
    def run():
        state, error = {}, ''
        try:
            row = (app._twitch_helix('GET', '/chat/settings?' + _q(broadcaster_id=broadcaster, moderator_id=broadcaster)).get('data') or [{}])[0]
            state.update({'slow': bool(row.get('slow_mode')), 'follow': bool(row.get('follower_mode')), 'sub': bool(row.get('subscriber_mode')), 'emote': bool(row.get('emote_mode'))})
            if row.get('slow_mode_wait_time'): state['slow_num'] = str(row['slow_mode_wait_time'])
            if row.get('follower_mode_duration') is not None: state['follow_num'] = str(row['follower_mode_duration'])
        except Exception as exc:
            error = str(exc) or 'error'
        try:
            shield = (app._twitch_helix('GET', '/moderation/shield_mode?' + _q(broadcaster_id=broadcaster, moderator_id=broadcaster)).get('data') or [{}])[0]
            state['shield'] = bool(shield.get('is_active'))
        except Exception as exc:
            error = error or str(exc) or 'error'
        app.ui_events.put(('controls_data', (state, error, '')))
    threading.Thread(target=run, daemon=True).start()


def apply_controls(app, payload):
    state, error, message = payload
    app.ctl_loading = True
    try:
        for key in app.ctl_vars:
            if key in state: app.ctl_vars[key].set(bool(state[key]))
        if state.get('slow_num'): app.ctl_nums['slow'].set(state['slow_num'])
        if 'follow_num' in state: app.ctl_nums['follow'].set(state['follow_num'])
    finally:
        app.ctl_loading = False
    if error: app._report('Could not read all chat settings: ' + error)


def on_toggle(app, key):
    if getattr(app, 'ctl_loading', False): return
    on = bool(app.ctl_vars[key].get())
    if not app._can_moderate_twitch():
        app._report('Chat controls need you signed in to Twitch in your own channel.')
        app.ctl_loading = True
        try: app.ctl_vars[key].set(not on)
        finally: app.ctl_loading = False
        return
    broadcaster = str(app.twitch_user_id)
    ids = _q(broadcaster_id=broadcaster, moderator_id=broadcaster)
    if key == 'shield':
        method, path, payload = 'PUT', '/moderation/shield_mode?' + ids, {'is_active': on}
        done = 'Shield mode is ' + ('on.' if on else 'off.')
    else:
        method, path = 'PATCH', '/chat/settings?' + ids
        if key == 'slow':
            payload = {'slow_mode': on}
            if on: payload['slow_mode_wait_time'] = _clamp(app.ctl_nums['slow'].get(), 3, 120, 30)
            done = 'Slow mode ' + (('on (' + str(payload['slow_mode_wait_time']) + ' seconds).') if on else 'off.')
        elif key == 'follow':
            payload = {'follower_mode': on}
            if on: payload['follower_mode_duration'] = _clamp(app.ctl_nums['follow'].get(), 0, 129600, 10)
            done = 'Followers-only chat is ' + ('on.' if on else 'off.')
        elif key == 'sub':
            payload = {'subscriber_mode': on}; done = 'Subscribers-only chat is ' + ('on.' if on else 'off.')
        else:
            payload = {'emote_mode': on}; done = 'Emote-only chat is ' + ('on.' if on else 'off.')
    def run():
        try:
            app._twitch_helix(method, path, payload)
            app._report(done)
        except Exception as exc:
            app._report('Twitch refused that: ' + str(exc))
            app.ui_events.put(('controls_reload', None))  # puts the switches back to what Twitch really has
    threading.Thread(target=run, daemon=True).start()


def send_announcement(app):
    message = app.announce_text.get().strip()
    if not message:
        app._report('Type the announcement first.'); return
    if not _need_own(app): return
    broadcaster = str(app.twitch_user_id)
    payload = {'message': message[:500], 'color': app.announce_color.get() or 'primary'}
    def run():
        try:
            app._twitch_helix('POST', '/chat/announcements?' + _q(broadcaster_id=broadcaster, moderator_id=broadcaster), payload)
            app._report('Announcement sent.')
            app.ui_events.put(('announce_done', None))
        except Exception as exc:
            app._report('Could not send the announcement: ' + str(exc))
    threading.Thread(target=run, daemon=True).start()


def clear_chat(app):
    if not _need_own(app): return
    if not messagebox.askyesno('Clear chat', 'Clear the whole Twitch chat for everyone?'): return
    broadcaster = str(app.twitch_user_id)
    def run():
        try:
            app._twitch_helix('DELETE', '/moderation/chat?' + _q(broadcaster_id=broadcaster, moderator_id=broadcaster))
            app._report('Twitch chat cleared.')
        except Exception as exc:
            app._report('Could not clear chat: ' + str(exc))
    threading.Thread(target=run, daemon=True).start()
