"""Guest connection to Kick's current, undocumented Centrifugo chat feed."""
import asyncio
import json
import os
import sys
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import websockets

KICK_CHANNEL_API = 'https://kick.com/api/v2/channels/'
KICK_REALTIME_API = 'https://web.kick.com/api/v1/realtime'
KICK_ORIGIN = 'https://kick.com'


class KickHTTPError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _http_json(url, payload=None, token=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {'Accept':'application/json', 'Origin':KICK_ORIGIN, 'Referer':KICK_ORIGIN+'/', 'User-Agent':'ChatVoiceLocal/1.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise KickHTTPError('Kick returned HTTP '+str(exc.code)+' from '+urllib.parse.urlparse(url).path+'.', exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError('Could not reach Kick. Check your internet connection or try again later.') from exc


def fetch_channel_status(slug):
    """Read-only live status of a Kick channel (same public page data the chat reader already uses)."""
    result = _http_json(KICK_CHANNEL_API + urllib.parse.quote(str(slug), safe=''))
    channel = result.get('data', result)
    if isinstance(channel, list):
        channel = channel[0] if channel else {}
    if not isinstance(channel, dict):
        raise RuntimeError('Kick returned an unexpected channel response.')
    info = {'live': False, 'viewers': 0, 'title': '', 'category': '', 'started': ''}
    for key in ('followers_count', 'followersCount'):
        if channel.get(key) not in (None, ''):
            info['followers'] = channel.get(key); break
    live = channel.get('livestream')
    if isinstance(live, dict):
        info['live'] = bool(live.get('is_live', True))
        try: info['viewers'] = int(live.get('viewer_count') or 0)
        except (TypeError, ValueError): info['viewers'] = 0
        info['title'] = str(live.get('session_title') or '')
        categories = live.get('categories') or []
        if categories and isinstance(categories[0], dict):
            info['category'] = str(categories[0].get('name') or '')
        info['started'] = str(live.get('created_at') or live.get('start_time') or '')
    return info


class KickReader:
    """Read one public Kick chat directly, without OAuth or webhooks."""
    def __init__(self, channel, on_event, status, token_provider=None):
        # token_provider() returns a valid Kick user access token, or '' to connect as a guest.
        self.token_provider = token_provider or (lambda: '')
        self.channel = channel.strip().lstrip('@').strip().lower()
        self.on_event = on_event
        self.status = status
        self.stop_event = threading.Event()
        self.thread = None
        self._connected = False

    def start(self):
        if not self.channel:
            raise ValueError('Enter a Kick channel name first.')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _say(self, message):
        # A stopped reader must not overwrite the status of the reader that replaced it.
        if not self.stop_event.is_set():
            self.status(message)

    def _channel_details(self):
        result = _http_json(KICK_CHANNEL_API + urllib.parse.quote(self.channel, safe=''))
        channel = result.get('data', result)
        if isinstance(channel, list):
            channel = channel[0] if channel else {}
        if not isinstance(channel, dict):
            raise RuntimeError('Kick returned an unexpected channel response.')
        chatroom = channel.get('chatroom') or {}
        chatroom_id = chatroom.get('id')
        channel_id = channel.get('id') or channel.get('channel_id') or channel.get('user_id')
        if not chatroom_id or not channel_id:
            raise RuntimeError('Could not find this Kick channel or its chatroom. Check the channel name and try again.')
        self.sub_badges = []
        for sb in (channel.get('subscriber_badges') or []):
            try:
                img = sb.get('badge_image') or {}; src = img.get('src') or str(img.get('srcset') or '').split(' ')[0]
                if src: self.sub_badges.append([int(sb.get('months') or 0), src])
            except (AttributeError, TypeError, ValueError): continue
        self.owner_user_id = str(channel.get('user_id') or (channel.get('user') or {}).get('id') or '')  # 7TV looks Kick channels up by this id
        return str(channel_id), str(chatroom_id), (channel.get('user') or {}).get('username', self.channel)

    def _realtime_connection(self, channel_id, client_id):
        token = ''
        try:
            token = self.token_provider() or ''
        except Exception:
            token = ''
        try:
            return self._realtime_request(channel_id, client_id, token)
        except KickHTTPError as exc:
            if token and exc.code in (401, 403):
                self._say('Kick login was not accepted for chat; continuing as a guest.')
                return self._realtime_request(channel_id, client_id, '')
            raise

    def _realtime_request(self, channel_id, client_id, user_token):
        payload = {'client':{'id':client_id,'type':'web'},'capabilities':{'accepted_providers':[{'provider':'centrifugo'},{'provider':'pusher'}]}}
        result = _http_json(KICK_REALTIME_API+'/channels/'+urllib.parse.quote(channel_id,safe='')+'/chat/connection', payload, user_token)
        data = result.get('data', result)
        connections = data.get('connections') or []
        connection = next((item for item in connections if item.get('provider') == 'centrifugo'), None)
        if not connection:
            raise RuntimeError('Kick did not offer the current realtime chat connection.')
        socket_url = (connection.get('credentials') or {}).get('url')
        if not socket_url:
            raise RuntimeError('Kick did not return a realtime chat address.')
        auth = _http_json(KICK_REALTIME_API+'/auth/connection', {'client_id':client_id}, user_token)
        token = (auth.get('data') or {}).get('token')
        if not token:
            raise RuntimeError('Kick did not return a chat token.')
        return socket_url, token

    @staticmethod
    def _decode_frames(raw):
        frames=[]
        for line in raw.splitlines() or [raw]:
            if not line.strip():
                continue
            try:
                value=json.loads(line)
                frames.extend(value if isinstance(value,list) else [value])
            except (TypeError,ValueError):
                continue
        return frames

    def _deliver_kicks(self, payload):
        """A viewer gifted Kicks. Every such event is also written to kick_kicks_events.log so the format can be checked."""
        data=payload.get('data',{})
        if isinstance(data,str):
            try: data=json.loads(data)
            except ValueError: data={}
        try:
            log=os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys,'frozen',False) else __file__)),'kick_kicks_events.log')
            if not os.path.exists(log) or os.path.getsize(log)<200000:
                with open(log,'a',encoding='utf-8') as handle: handle.write(time.strftime('%Y-%m-%d %H:%M:%S ')+json.dumps(payload,ensure_ascii=False)[:4000]+'\n')
        except OSError:
            pass
        if not isinstance(data,dict):
            return
        sender=data.get('sender') or data.get('gifter') or data.get('user') or {}
        gift=data.get('gift') or {}
        amount=None
        for source,key in ((gift,'amount'),(data,'kicks_amount'),(data,'amount'),(data,'kicks')):
            if isinstance(source,dict) and source.get(key) not in (None,''):
                amount=source.get(key); break
        try: amount=int(float(amount))
        except (TypeError,ValueError): return
        username=(sender.get('username') or sender.get('slug') or '') if isinstance(sender,dict) else ''
        if amount<=0 or not username:
            return
        self.on_event('Kick','kicks',username,'',{'kicks':amount,'user_id':str(sender.get('id') or sender.get('user_id') or ''),'source':'kick_guest_realtime'})

    @staticmethod
    def _event_data(payload):
        data = payload.get('data', {})
        if isinstance(data, str):
            try: data = json.loads(data)
            except ValueError: data = {}
        return data if isinstance(data, dict) else {}

    def _log_unknown_event(self, event, payload):
        """Writes the first sample of each non-chat event type to kick_events.log, so unfamiliar formats can be checked."""
        seen = self.__dict__.setdefault('_seen_events', set())
        if event in seen or len(seen) > 60:
            return
        seen.add(event)
        try:
            log = os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)), 'kick_events.log')
            if not os.path.exists(log) or os.path.getsize(log) < 200000:
                with open(log, 'a', encoding='utf-8') as handle:
                    handle.write(time.strftime('%Y-%m-%d %H:%M:%S ') + json.dumps(payload, ensure_ascii=False)[:2500] + '\n')
        except OSError:
            pass

    def _deliver_other(self, event, payload):
        """Subs, gifted subs, reward redemptions and raids/hosts from the same live chat feed. Anything unfamiliar is only logged."""
        name = event.lower().replace('_', '')
        data = self._event_data(payload)
        feed = None
        if 'gifted' in name and 'subscription' in name:
            who = data.get('gifter_username') or data.get('gifter') or 'Anonymous'
            gifted = data.get('gifted_usernames') or data.get('gifted_users') or []
            count = len(gifted) if isinstance(gifted, list) and gifted else int(data.get('total') or data.get('gifted_count') or 1)
            feed = ('gift', who, 'gifted ' + str(count) + ' sub' + ('s' if count != 1 else ''), count, '')
        elif 'subscription' in name:
            who = data.get('username') or ''
            try: months = int(data.get('months') or 1)
            except (TypeError, ValueError): months = 1
            if who: feed = ('resub' if months > 1 else 'sub', who, ('resubscribed, ' + str(months) + ' months') if months > 1 else 'subscribed', 0, '')
        elif 'reward' in name and ('redeem' in name or 'redemption' in name):
            who = data.get('username') or ''
            title = data.get('reward_title') or data.get('title') or 'a reward'
            note = str(data.get('user_input') or '').strip()
            try: cost = int(data.get('reward_cost') or data.get('cost') or 0)
            except (TypeError, ValueError): cost = 0
            if who: feed = ('points', who, 'redeemed ' + str(title) + ((' (' + str(cost) + ' points)') if cost else '') + ((': ' + note) if note else ''), cost, str(data.get('user_id') or ''))
        elif 'host' in name or 'raid' in name:
            who = data.get('host_username') or data.get('username') or ''
            try: viewers = int(data.get('number_viewers') or data.get('viewers') or 0)
            except (TypeError, ValueError): viewers = 0
            if who: feed = ('raid', who, 'raided with ' + str(viewers) + ' viewer' + ('s' if viewers != 1 else ''), viewers, '')
        if feed:
            kind, who, text, amount, uid = feed
            self.on_event('Kick', 'feed', who, text, {'feed': kind, 'amount': amount, 'user_id': uid})
        else:
            self._log_unknown_event(event, payload)

    def _deliver_publication(self, frame):
        if self.stop_event.is_set():
            return
        push=frame.get('push') or {}
        publication=push.get('pub') or {}
        payload=publication.get('data') or {}
        if not isinstance(payload,dict):
            return
        event=payload.get('event','')
        if 'kicks' in event.lower():
            self._deliver_kicks(payload)
            return
        if 'userbanned' in event.lower().replace('_',''):
            ban=payload.get('data',{})
            if isinstance(ban,str):
                try: ban=json.loads(ban)
                except ValueError: ban={}
            target=(ban.get('user') or {}) if isinstance(ban,dict) else {}
            who=target.get('username') or target.get('slug') or ''
            if who: self.on_event('Kick','banned',who,'',{'user_id':str(target.get('id') or '')})
            return
        if 'chatmessage' not in event.lower() and 'chatmessagesentevent' not in event.lower():
            try: self._deliver_other(event, payload)
            except Exception: pass
            return
        message=payload.get('data',{})
        if isinstance(message,str):
            try: message=json.loads(message)
            except ValueError: return
        if not isinstance(message,dict):
            return
        sender=message.get('sender') or {}
        content=str(message.get('content') or '').strip()
        raw_content=content  # keeps the [emote:id:name] markers so the app can show the emote images
        content=re.sub(r'\[emote:\d+:([^\]]+)\]',r'\1',content)
        if not content:
            return
        username=sender.get('username') or sender.get('slug') or 'viewer'
        identity=sender.get('identity') or {}
        badges=[str(b.get('type') or b.get('name') or b.get('text') or '').lower() for b in (identity.get('badges') or []) if isinstance(b,dict)] if isinstance(identity,dict) else []
        metadata={'message_id':str(message.get('id') or ''),'user_id':str(sender.get('id') or ''),'source':'kick_guest_realtime','badges':badges,'raw':raw_content,'badge_info':[{'type':str(b.get('type') or '').lower(),'count':b.get('count')} for b in (identity.get('badges') or []) if isinstance(b,dict)] if isinstance(identity,dict) else []}
        self.on_event('Kick','message',username,content,metadata)

    async def _listen(self, socket_url, token, chatroom_id):
        async with websockets.connect(socket_url, origin=KICK_ORIGIN, open_timeout=20, ping_interval=None, close_timeout=4) as ws:
            await ws.send(json.dumps({'id':1,'connect':{'token':token,'name':'ChatVoice Local'}}))
            connected=False
            while not connected and not self.stop_event.is_set():
                raw=await asyncio.wait_for(ws.recv(),timeout=20)
                for frame in self._decode_frames(raw):
                    if frame.get('id')==1:
                        if frame.get('error'):
                            raise RuntimeError('Kick realtime connection was refused: '+str(frame['error'].get('message','unknown error')))
                        if frame.get('connect') is not None:
                            connected=True
            if self.stop_event.is_set():
                return
            await ws.send(json.dumps({'id':2,'subscribe':{'channel':'chatrooms.'+chatroom_id+'.v2'}}))
            subscribed=False
            while not subscribed and not self.stop_event.is_set():
                raw=await asyncio.wait_for(ws.recv(),timeout=20)
                for frame in self._decode_frames(raw):
                    if frame.get('push'):
                        self._deliver_publication(frame)
                    if frame.get('id')==2:
                        if frame.get('error'):
                            raise RuntimeError('Kick refused the chat subscription: '+str(frame['error'].get('message','unknown error')))
                        subscribed=frame.get('subscribe') is not None
            if subscribed and not self._connected:
                self._connected=True
                self._say('Kick chat connected to '+self.channel+'.')
            while not self.stop_event.is_set():
                try:
                    raw=await asyncio.wait_for(ws.recv(),timeout=1)
                except asyncio.TimeoutError:
                    continue
                for frame in self._decode_frames(raw):
                    if frame == {}:
                        await ws.send('{}')
                    elif frame.get('push'):
                        self._deliver_publication(frame)
                    elif frame.get('error'):
                        raise RuntimeError('Kick realtime connection error: '+str(frame['error'].get('message','unknown error')))

    def _run(self):
        backoff=2
        while not self.stop_event.is_set():
            try:
                self._say('Connecting to Kick chat '+self.channel+'…')
                channel_id,chatroom_id,_=self._channel_details()
                self.on_event('Kick','badge_sets','','',{'kick_sub':getattr(self,'sub_badges',[])})
                if getattr(self,'owner_user_id',''): self.on_event('Kick','emote_channel','','',{'channel_id':self.owner_user_id})
                client_id=str(uuid.uuid4())
                socket_url,token=self._realtime_connection(channel_id,client_id)
                asyncio.run(self._listen(socket_url,token,chatroom_id))
                if not self.stop_event.is_set():
                    raise RuntimeError('Kick closed the chat connection.')
                break
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self._say('Kick chat reconnecting: '+str(exc))
                self._connected=False
                delay=min(backoff+random.random(),30)
                self.stop_event.wait(delay)
                backoff=min(backoff*2,30)
        self._say('Kick chat disconnected.')
