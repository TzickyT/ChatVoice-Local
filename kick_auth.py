"""Kick OAuth 2.1 (authorization code + PKCE) sign-in for a desktop app.

Flow: open the browser at id.kick.com, receive the redirect on http://localhost:17891/kick/callback,
exchange the code for tokens, then look up who signed in. Register this exact redirect URI
in your app at https://kick.com/settings/developer.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_URL = 'https://id.kick.com/oauth/authorize'
TOKEN_URL = 'https://id.kick.com/oauth/token'
REVOKE_URL = 'https://id.kick.com/oauth/revoke'
API_URL = 'https://api.kick.com/public/v1'
USER_AGENT = 'ChatVoiceLocal/1.0 (+Windows desktop app)'
REDIRECT_PORT = 17891
REDIRECT_URI = 'http://localhost:%d/kick/callback' % REDIRECT_PORT
# The app registered at kick.com/settings/developer must have these scopes ticked:
# 'Read user information', 'Subscribe to events (read chat, follows, etc.)', 'Write to Chat feed', 'Execute moderation actions for moderators', 'Execute moderation actions on chat messages'.
SCOPES = 'user:read events:subscribe chat:write moderation:ban moderation:chat_message:manage'
# Always asked for at login (title/category editing and channel points rewards), because every scope
# listed here must also be ticked in the Kick app (Read channel information, Update channel information,
# Read channel points rewards information, Read/add/edit/delete channel points rewards) or Kick refuses the login.
EXTRA_SCOPES = 'channel:read channel:write channel:rewards:read channel:rewards:write'


def scopes(full=False):
    return SCOPES + ((' ' + EXTRA_SCOPES) if full else '')
# Built-in app used by everyone who installs this program. Override with "kick_client_id" in connection_settings.json.
DEFAULT_CLIENT_ID = '01M7DN9GYA6K8BC9GVZK8J4F8G'
# Token requests go through this Wix function (it holds the Client Secret), so the secret is never in the program.
# Override with "kick_token_relay" in connection_settings.json. A local "kick_client_secret" skips the relay.
RELAY_URL = 'https://zicktsoenamy.wixsite.com/tzickyt/_functions/kickToken'

_DONE_PAGE = ('<html><body style="font-family:Segoe UI,Arial;background:#14171c;color:#eee;text-align:center;padding-top:15vh">'
              '<h2>%s</h2><p>You can close this tab and return to ChatVoice Local.</p></body></html>')


class KickAuthError(RuntimeError):
    pass


def _post_form(url, form):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode('utf-8', 'replace'))
            detail = body.get('error_description') or body.get('message') or body.get('error') or ''
        except ValueError:
            detail = ''
        raise KickAuthError('Kick sign-in failed (HTTP %d). %s' % (exc.code, detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KickAuthError('Could not reach Kick. Check your internet connection.') from exc


def _api_get(path, token):
    req = urllib.request.Request(API_URL + path, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:200].replace('\n', ' ')
        raise KickAuthError('Kick API returned HTTP %d for %s. %s' % (exc.code, path, detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KickAuthError('Could not reach Kick. Check your internet connection.') from exc


def _normalize(result):
    """Turns a token response into {'access_token','refresh_token','expires_at'}."""
    token = result.get('access_token')
    if not token:
        raise KickAuthError(result.get('error_description') or result.get('error') or 'Kick did not return an access token.')
    return {'access_token': token, 'refresh_token': result.get('refresh_token') or '', 'scope': str(result.get('scope') or ''),
            'expires_at': time.time() + int(result.get('expires_in') or 3600) - 60}


def _post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8', 'replace')).get('error') or ''
        except ValueError:
            detail = ''
        raise KickAuthError('Kick sign-in failed (HTTP %d). %s' % (exc.code, detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KickAuthError('Could not reach the sign-in service. Check your internet connection.') from exc


def _token_request(form, client_secret):
    """Uses the relay (which adds the secret) unless a secret is configured locally."""
    if client_secret:
        return _post_form(TOKEN_URL, dict(form, client_secret=client_secret))
    if RELAY_URL:
        return _post_json(RELAY_URL, form)
    return _post_form(TOKEN_URL, form)


def refresh(client_id, client_secret, refresh_token):
    form = {'grant_type': 'refresh_token', 'client_id': client_id, 'refresh_token': refresh_token}
    tokens = _normalize(_token_request(form, client_secret))
    tokens['refresh_token'] = tokens['refresh_token'] or refresh_token
    return tokens


def whoami(access_token):
    """Returns (user_id, username, channel_slug) of the signed-in account."""
    data = _api_get('/users', access_token).get('data') or []
    user = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    name = user.get('name') or ''
    user_id = str(user.get('user_id') or '')
    slug = ''
    try:
        channels = _api_get('/channels', access_token).get('data') or []
        if channels:
            slug = channels[0].get('slug') or ''
    except KickAuthError:
        pass  # channel:read may be missing; the user name is a good fallback
    slug = slug or name.lower().replace(' ', '-').replace('_', '-')
    return user_id, name, slug


def _api_call(method, path, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': USER_AGENT}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(API_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise KickAuthError('Kick refused this action (HTTP %d). Log out of Kick and log in again to grant moderation permission, and make sure this account is the channel owner or a moderator.' % exc.code) from exc
        raise KickAuthError('Kick returned HTTP %d for this action.' % exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KickAuthError('Could not reach Kick. Check your internet connection.') from exc


def _api_json(method, path, token, payload=None, base=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': USER_AGENT}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request((base or API_URL) + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:160].replace('\n', ' ')
        if exc.code in (401, 403):
            raise KickAuthError('Kick refused this (HTTP %d). Tick the matching permission in your Kick app at kick.com/settings/developer, then log out of Kick and log in again. %s' % (exc.code, detail)) from exc
        raise KickAuthError('Kick returned HTTP %d. %s' % (exc.code, detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KickAuthError('Could not reach Kick. Check your internet connection.') from exc
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}


def update_channel(token, title=None, category_id=None):
    body = {}
    if title: body['stream_title'] = str(title)[:140]
    if category_id: body['category_id'] = int(category_id)
    if not body:
        raise KickAuthError('Nothing to change.')
    _api_json('PATCH', '/channels', token, body)


def search_categories(token, query):
    quoted = urllib.parse.quote(str(query), safe='')
    try:
        result = _api_json('GET', '/categories?q=' + quoted, token)
    except KickAuthError:
        result = _api_json('GET', '/categories?name=' + quoted, token, base=API_URL.replace('/v1', '/v2'))
    rows = result.get('data') or []
    return [(str(r.get('name', '')), r.get('id')) for r in rows if isinstance(r, dict) and r.get('name') and r.get('id') is not None][:10]


def list_rewards(token):
    data = _api_json('GET', '/channels/rewards', token).get('data') or []
    return [r for r in data if isinstance(r, dict) and r.get('id')]


def create_reward(token, title, cost):
    _api_json('POST', '/channels/rewards', token, {'title': str(title)[:45], 'cost': int(cost)})


def update_reward(token, reward_id, payload):
    _api_json('PATCH', '/channels/rewards/' + urllib.parse.quote(str(reward_id), safe=''), token, payload)


def _redeemer_name(row):
    for key in ('redeemer', 'user', 'viewer'):
        value = row.get(key)
        if isinstance(value, dict):
            name = value.get('username') or value.get('name') or value.get('slug')
            if name: return str(name)
    for key in ('username', 'user_name', 'redeemer_username'):
        if row.get(key): return str(row[key])
    ident = row.get('user_id') or (row.get('redeemer') or {}).get('user_id') if isinstance(row.get('redeemer'), dict) else row.get('user_id')
    return ('viewer ' + str(ident)) if ident else 'viewer'


def list_redemptions(token, status='pending'):
    """Waiting redemptions as flat dicts: id, user, input, reward_title, cost."""
    data = _api_json('GET', '/channels/rewards/redemptions?' + urllib.parse.urlencode({'status': status}), token).get('data') or []
    rows = []
    for item in data:
        if not isinstance(item, dict): continue
        if isinstance(item.get('redemptions'), list):
            reward = item.get('reward') or {}
            for row in item['redemptions']:
                if isinstance(row, dict) and row.get('id'):
                    rows.append({'id': str(row['id']), 'user': _redeemer_name(row), 'input': str(row.get('user_input') or ''),
                                 'reward_title': str(reward.get('title') or ''), 'cost': reward.get('cost', '')})
        elif item.get('id'):
            reward = item.get('reward') if isinstance(item.get('reward'), dict) else {}
            rows.append({'id': str(item['id']), 'user': _redeemer_name(item), 'input': str(item.get('user_input') or ''),
                         'reward_title': str(reward.get('title') or item.get('reward_title') or ''), 'cost': reward.get('cost', item.get('cost', ''))})
    return rows


def resolve_redemptions(token, ids, accept=True):
    _api_json('POST', '/channels/rewards/redemptions/' + ('accept' if accept else 'reject'), token, {'ids': [str(i) for i in ids][:25]})


def send_chat(token, broadcaster_id, content):
    """Posts a chat message as the signed-in user in their own channel (needs the chat:write scope)."""
    payload = {'broadcaster_user_id': int(broadcaster_id), 'content': str(content)[:500], 'type': 'user'}
    req = urllib.request.Request(API_URL + '/chat', data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                                          'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:160].replace('\n', ' ')
        if exc.code in (401, 403):
            raise KickAuthError('Kick refused the message (HTTP %d). Log out of Kick and log in again to grant chat permission; '
                                '"Write to Chat feed" must be ticked in your Kick app. %s' % (exc.code, detail)) from exc
        raise KickAuthError('Kick returned HTTP %d when sending the message. %s' % (exc.code, detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise KickAuthError('Could not reach Kick. Check your internet connection.') from exc


def ban_user(token, broadcaster_id, user_id, minutes=None, reason=''):
    """Permanent ban, or a timeout when minutes is given."""
    payload = {'broadcaster_user_id': int(broadcaster_id), 'user_id': int(user_id)}
    if minutes:
        payload['duration'] = int(minutes)
    if reason:
        payload['reason'] = reason[:100]
    _api_call('POST', '/moderation/bans', token, payload)


def unban_user(token, broadcaster_id, user_id):
    _api_call('DELETE', '/moderation/bans', token, {'broadcaster_user_id': int(broadcaster_id), 'user_id': int(user_id)})


def delete_message(token, message_id):
    _api_call('DELETE', '/chat/' + urllib.parse.quote(str(message_id), safe=''), token)


def revoke(access_token, refresh_token=''):
    for kind, value in (('access_token', access_token), ('refresh_token', refresh_token)):
        if not value:
            continue
        try:
            req = urllib.request.Request(REVOKE_URL + '?' + urllib.parse.urlencode({'token': value, 'token_hint_type': kind}), data=b'', method='POST')
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:
            pass  # forgetting the token locally is what matters


class KickLogin:
    """Runs the browser sign-in on a background thread and reports through callbacks."""

    def __init__(self, client_id, client_secret, on_success, on_status, on_fail=None, scopes=None):
        self.scopes = scopes or SCOPES
        self.on_fail = on_fail or (lambda message: None)
        self.client_id = client_id.strip()
        self.client_secret = (client_secret or '').strip()
        self.on_success = on_success   # (tokens, user_id, username, slug)
        self.on_status = on_status
        self.stop_event = threading.Event()
        self.server = None

    def start(self):
        if not self.client_id:
            raise ValueError('Enter your Kick app Client ID first (kick.com/settings/developer).')
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        if self.server:
            try:
                self.server.server_close()
            except Exception:
                pass

    def _run(self):
        try:
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            state = secrets.token_urlsafe(16)
            outcome = {}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    if urllib.parse.urlparse(self.path).path != '/kick/callback':
                        self.send_response(404); self.end_headers(); return
                    outcome.update({k: v[0] for k, v in query.items()})
                    message = 'Kick sign-in complete' if 'code' in query else 'Kick sign-in was not completed'
                    body = (_DONE_PAGE % message).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            try:
                self.server = HTTPServer(('127.0.0.1', REDIRECT_PORT), Handler)
            except OSError as exc:
                raise KickAuthError('Port %d is in use, so the Kick sign-in page cannot return to the app. Close the program using it and try again.' % REDIRECT_PORT) from exc
            self.server.timeout = 1
            params = {'client_id': self.client_id, 'response_type': 'code', 'redirect_uri': REDIRECT_URI, 'state': state,
                      'scope': self.scopes, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
            self.on_status('Kick login: finish signing in in your browser…')
            webbrowser.open(AUTH_URL + '?' + urllib.parse.urlencode(params))
            deadline = time.time() + 300
            while not self.stop_event.is_set() and not outcome and time.time() < deadline:
                self.server.handle_request()
            self.server.server_close()
            if self.stop_event.is_set():
                return
            if not outcome:
                raise KickAuthError('Kick login timed out. Click Login with Kick and try again.')
            if outcome.get('error'):
                raise KickAuthError('Kick login was declined or failed: ' + (outcome.get('error_description') or outcome['error']))
            if outcome.get('state') != state:
                raise KickAuthError('Kick login response did not match this request. Try again.')
            form = {'grant_type': 'authorization_code', 'client_id': self.client_id, 'redirect_uri': REDIRECT_URI,
                    'code_verifier': verifier, 'code': outcome.get('code', '')}
            tokens = _normalize(_token_request(form, self.client_secret))
            user_id, username, slug = whoami(tokens['access_token'])
            self.on_success(tokens, user_id, username, slug)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.on_status('Kick login failed: ' + str(exc))
                self.on_fail(str(exc))
