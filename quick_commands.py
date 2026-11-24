"""!title and !category, typed in the Activity send box (or the OBS dock).

They are handled inside the app and are never sent to the real Twitch or Kick chat.
The platform dropdown decides where they apply: All (both), Twitch or Kick.
  !title <new title>       changes the stream title
  !category <name>         changes the category to the best match for that name
  !title / !category       shows the current one
"""
import re
import threading
import urllib.parse

import kick_auth

PATTERN = re.compile(r'^!(title|category)\b\s*(.*)$', re.IGNORECASE | re.DOTALL)


def handle(app, target, text):
    """Returns True when the text was a !title / !category command (so it must not be sent to chat)."""
    match = PATTERN.match(str(text or '').strip())
    if not match:
        return False
    command, argument = match.group(1).lower(), re.sub(r'\s+', ' ', match.group(2)).strip()
    wanted = ['Twitch', 'Kick'] if target == 'All' else [target]
    ready = []
    for platform in wanted:
        if app._can_moderate(platform): ready.append(platform)
        else: app._report('!' + command + ': sign in to ' + platform + ' in your own channel first.')
    if not ready:
        return True
    if not argument:
        for platform in ready:
            info = app.dash_state.get(platform, {}) if hasattr(app, 'dash_state') else {}
            current = info.get('title' if command == 'title' else 'category')
            app._report(platform + ' ' + command + ': ' + (current or 'not loaded yet (open the Dashboard to refresh)'))
        return True
    if command == 'title' and len(argument) > 140:
        app._report('!title: titles are at most 140 characters (this one is ' + str(len(argument)) + ').')
        return True
    for platform in ready:
        if platform == 'Twitch':
            args = (app, command, argument, str(app.twitch_user_id))
            threading.Thread(target=_twitch, args=args, daemon=True).start()
        else:
            threading.Thread(target=_kick, args=(app, command, argument), daemon=True).start()
    return True


def _pick(pairs, wanted):
    """Exact (case-insensitive) match first, otherwise the first result. Returns (name, id, exact)."""
    for name, ident in pairs:
        if name.casefold() == wanted.casefold():
            return name, ident, True
    return pairs[0][0], pairs[0][1], False


def _done(app, platform, command, argument, name=None, exact=True):
    if command == 'title':
        app._report(platform + ' title changed to: ' + argument)
    else:
        app._report(platform + ' category set to "' + str(name) + '"' + ('' if exact else ' (closest match to "' + argument + '")'))
    app.ui_events.put(('dash_saved' if platform == 'Twitch' else 'dash_kick_saved', None))


def _twitch(app, command, argument, broadcaster):
    try:
        path = '/channels?' + urllib.parse.urlencode({'broadcaster_id': broadcaster})
        if command == 'title':
            app._twitch_helix('PATCH', path, {'title': argument})
            _done(app, 'Twitch', command, argument)
            return
        rows = app._twitch_helix('GET', '/search/categories?' + urllib.parse.urlencode({'query': argument, 'first': 8})).get('data') or []
        pairs = [(r.get('name', ''), r.get('id', '')) for r in rows if r.get('name') and r.get('id')]
        if not pairs:
            app._report('!category: Twitch has no category matching "' + argument + '".')
            return
        name, ident, exact = _pick(pairs, argument)
        app._twitch_helix('PATCH', path, {'game_id': ident})
        _done(app, 'Twitch', command, argument, name, exact)
    except Exception as exc:
        app._report('!' + command + ' on Twitch failed: ' + str(exc))


def _kick(app, command, argument):
    try:
        token = app._kick_access_token()
        if command == 'title':
            kick_auth.update_channel(token, title=argument)
            _done(app, 'Kick', command, argument)
            return
        pairs = kick_auth.search_categories(token, argument)
        if not pairs:
            app._report('!category: Kick has no category matching "' + argument + '".')
            return
        name, ident, exact = _pick(pairs, argument)
        kick_auth.update_channel(token, category_id=ident)
        _done(app, 'Kick', command, argument, name, exact)
    except Exception as exc:
        app._report('!' + command + ' on Kick failed: ' + str(exc))
