import collections, copy, csv, asyncio, json, queue, threading, time, re, secrets, random, urllib.parse, urllib.request, urllib.error, webbrowser, os, shutil, subprocess, tempfile, sys
# A windowed .exe has no console, so sys.stdout/stderr are None and libraries such as kokoro (loguru) crash when
# they try to log. Send those messages to a log file next to the program instead. Must run before the imports below.
if sys.stdout is None or sys.stderr is None:
    try: _log_stream=open(os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys,'frozen',False) else __file__)),'chatvoice_local.log'),'w',encoding='utf-8',buffering=1)
    except OSError: _log_stream=open(os.devnull,'w',encoding='utf-8')
    if sys.stdout is None: sys.stdout=_log_stream
    if sys.stderr is None: sys.stderr=_log_stream
if '--stream-proxy' in sys.argv:  # the small stream audio window that OBS captures: skip the heavy imports below
    import stream_proxy; sys.exit(stream_proxy.run())
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from tkinter import font as tkfont
import numpy as np
import sounddevice as sd
from kokoro import KPipeline
import websockets
import soundfile as sf
from kick import KickReader
import kick_auth
import dock_server
import word_blocker
import viewer_card
import dashboard
import channel_tools
import quick_commands
import stream_proxy
import emotes
try:
    from PIL import Image as _PILImage, ImageTk as _PILImageTk
    PIL_OK=True
except Exception:
    PIL_OK=False
try:
    import keyring
except ImportError:
    keyring=None
try:
    import win32crypt
except ImportError:
    win32crypt=None

# Folder that holds the settings, voices and helper scripts: next to main.py, or next to the .exe when packaged.
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys,'frozen',False) else __file__))
try: os.chdir(APP_DIR)
except OSError: pass
APP_VERSION = '1.0.0'
APP_TITLE = 'ChatVoice Local'
# Names used before the app was renamed. Saved logins and backups made under the old name keep working.
LEGACY_KEYRING = 'MultiChat TTS'
LEGACY_BACKUP_FORMAT = 'MultiChat TTS backup'
MAX_MESSAGE_LENGTH = 300
ACTIVITY_MAX = 500
EMOTE_SIZES = {'Small':20,'Medium':26,'Large':36}
MAX_QUEUE = 40
PAUSED_QUEUE_LIMIT = 12
DEFAULT_TWITCH_CLIENT_ID = 'g4ucqlp0v5879egyojt7nyjfqk9jpv'
# Exact account names only; the old "ends with bot" guess blocked real users such as Abbot or Talbot.
KNOWN_BOT_NAMES = frozenset({'nightbot','streamelements','streamlabs','moobot','fossabot','wizebot','coebot','deepbot','phantombot','soundalerts','sery_bot','botrix','kickbot'})
KOKORO_VOICE_GROUPS = [
    ('American English', 'a', [('af_heart','Heart'),('af_alloy','Alloy'),('af_aoede','Aoede'),('af_bella','Bella'),('af_jessica','Jessica'),('af_kore','Kore'),('af_nicole','Nicole'),('af_nova','Nova'),('af_river','River'),('af_sarah','Sarah'),('af_sky','Sky'),('am_adam','Adam'),('am_echo','Echo'),('am_eric','Eric'),('am_fenrir','Fenrir'),('am_liam','Liam'),('am_michael','Michael'),('am_onyx','Onyx'),('am_puck','Puck'),('am_santa','Santa')]),
    ('British English', 'b', [('bf_alice','Alice'),('bf_emma','Emma'),('bf_isabella','Isabella'),('bf_lily','Lily'),('bm_daniel','Daniel'),('bm_fable','Fable'),('bm_george','George'),('bm_lewis','Lewis')]),
    ('Japanese', 'j', [('jf_alpha','Alpha'),('jf_gongitsune','Gongitsune'),('jf_nezumi','Nezumi'),('jf_tebukuro','Tebukuro'),('jm_kumo','Kumo')]),
    ('Mandarin Chinese', 'z', [('zf_xiaobei','Xiaobei'),('zf_xiaoni','Xiaoni'),('zf_xiaoxiao','Xiaoxiao'),('zf_xiaoyi','Xiaoyi'),('zm_yunjian','Yunjian'),('zm_yunxi','Yunxi'),('zm_yunxia','Yunxia'),('zm_yunyang','Yunyang')]),
    ('Spanish', 'e', [('ef_dora','Dora'),('em_alex','Alex'),('em_santa','Santa')]),
    ('French', 'f', [('ff_siwis','Siwis')]),
    ('Hindi', 'h', [('hf_alpha','Alpha'),('hf_beta','Beta'),('hm_omega','Omega'),('hm_psi','Psi')]),
    ('Italian', 'i', [('if_sara','Sara'),('im_nicola','Nicola')]),
    ('Brazilian Portuguese', 'p', [('pf_dora','Dora'),('pm_alex','Alex'),('pm_santa','Santa')]),
]
KOKORO_VOICE_INFO = {
    f'{language} — {name} ({voice_id})': {'kind':'kokoro','voice_id':voice_id,'lang':lang}
    for language,lang,voices in KOKORO_VOICE_GROUPS for voice_id,name in voices
}
KOKORO_ID_TO_LABEL = {info['voice_id']:label for label,info in KOKORO_VOICE_INFO.items()}
def _build_voice_aliases():
    """Names viewers can type before a colon ("Heart: hello") mapped to full Kokoro voice labels. English voices win name clashes."""
    aliases={}
    for _language,_lang,voices in KOKORO_VOICE_GROUPS:
        for voice_id,name in voices:
            label=KOKORO_ID_TO_LABEL.get(voice_id)
            if not label: continue
            aliases.setdefault(name.lower(),label); aliases.setdefault(voice_id.lower(),label)
    return aliases
VOICE_ALIASES = _build_voice_aliases()
VOICE_CODE_RE = re.compile(r'(?<!\S)([A-Za-z][A-Za-z0-9_]*):[ \t]*')
def split_voice_segments(text,aliases=VOICE_ALIASES):
    """Splits chat text into [(voice_label_or_None, text)]. "Heart: hi Adam: yo" -> [(Heart,'hi'),(Adam,'yo')]."""
    segments=[]; position=0; current=None
    for match in VOICE_CODE_RE.finditer(text):
        label=aliases.get(match.group(1).lower())
        if not label: continue
        chunk=text[position:match.start()].strip()
        if chunk: segments.append((current,chunk))
        current=label; position=match.end()
    tail=text[position:].strip()
    if tail: segments.append((current,tail))
    return segments
def normalize_voice_label(value):
    # Older saved settings may hold a bare ID such as 'af_heart'; show it under its full dropdown label.
    return KOKORO_ID_TO_LABEL.get(value,value) if isinstance(value,str) else value
# Extra voices from the separate Kokoro v1.1-zh model (about 100 Mandarin voices plus a few English ones).
# They only work with that model, so each entry carries its repo and is never mixed with standard v1.0 voices.
ZH11_REPO = 'hexgrad/Kokoro-82M-v1.1-zh'
ZH11_SEED_IDS = ('af_maple','af_sol','bf_vale','zf_001','zm_010')
ZH11_GROUPS = {'z':'Mandarin Chinese v1.1','a':'American English v1.1','b':'British English v1.1'}
def _zh11_cache_path():
    return os.path.join(APP_DIR,'zh11_voices.json')
def _fetch_zh11_voice_ids():
    from huggingface_hub import HfApi
    ids=sorted({m.group(1) for f in HfApi().list_repo_files(ZH11_REPO) for m in [re.fullmatch(r'voices/(.+)\.pt',f)] if m})
    if ids:
        try:
            with open(_zh11_cache_path(),'w',encoding='utf-8') as fh: json.dump(ids,fh)
        except OSError: pass
    return ids
def load_zh11_voice_ids(wait=6.0):
    # Use the saved list right away and refresh it in the background; without a saved list, wait briefly for the first lookup.
    cached=None
    try:
        with open(_zh11_cache_path(),'r',encoding='utf-8') as fh: data=json.load(fh)
        if isinstance(data,list) and all(isinstance(x,str) for x in data) and data: cached=data
    except (OSError,json.JSONDecodeError): pass
    found=[]
    def worker():
        try: found.extend(_fetch_zh11_voice_ids())
        except Exception: pass
    thread=threading.Thread(target=worker,daemon=True); thread.start()
    if cached is None: thread.join(wait)
    return cached or list(found) or list(ZH11_SEED_IDS)
def zh11_entry(voice_id):
    match=re.fullmatch(r'([abz])([fm])_(.+)',voice_id)
    if not match: return None,None
    lang,gender,rest=match.groups()
    name=('Female ' if gender=='f' else 'Male ')+rest if lang=='z' else rest.replace('_',' ').title()
    return f'{ZH11_GROUPS[lang]} — {name} ({voice_id})',{'kind':'kokoro','voice_id':voice_id,'lang':lang,'repo':ZH11_REPO}
def register_zh11_voices(voice_ids):
    for voice_id in voice_ids:
        label,info=zh11_entry(voice_id)
        if label and label not in KOKORO_VOICE_INFO: KOKORO_VOICE_INFO[label]=info
def voice_group(info):
    # Voices can only be blended when they share both the language and the model they belong to.
    return (info.get('lang','a'),info.get('repo'))
# Random voice mode: which voice groups may be picked for each message.
RANDOM_SAPI_GROUP = 'Windows voices'
RANDOM_MIX_GROUP = 'Blends, saved mixes and imported packs'
RANDOM_DEFAULT_GROUPS = ('American English','British English')
VOICE_MODES = ('single','platform','five','random')
STUDIO_TEST_TEXT = 'Hello chat! This is how this voice setup sounds. Let me know what you think.'
# Ready-made mixes of voices that are already installed. Each pairs two voices of the same language.
BUILTIN_BLENDS = [
    ('Heart + Bella',('af_heart','af_bella'),(50,50)),
    ('Heart + Nicole',('af_heart','af_nicole'),(60,40)),
    ('Bella + Sarah',('af_bella','af_sarah'),(50,50)),
    ('Nova + Sky',('af_nova','af_sky'),(50,50)),
    ('Adam + Michael',('am_adam','am_michael'),(50,50)),
    ('Echo + Eric',('am_echo','am_eric'),(50,50)),
    ('Onyx + Fenrir',('am_onyx','am_fenrir'),(60,40)),
    ('Liam + Puck',('am_liam','am_puck'),(50,50)),
    ('Emma + Lily (British)',('bf_emma','bf_lily'),(50,50)),
    ('Alice + Isabella (British)',('bf_alice','bf_isabella'),(50,50)),
    ('George + Daniel (British)',('bm_george','bm_daniel'),(50,50)),
    ('Fable + Lewis (British)',('bm_fable','bm_lewis'),(50,50)),
]
# user:read:chat is required; the others enable sending chat, moderating (Activity page) and ban-evader detection (Chat Moderation page).
TWITCH_SCOPES = 'user:read:chat user:write:chat moderator:manage:banned_users moderator:manage:chat_messages moderator:read:suspicious_users moderator:read:followers channel:read:subscriptions bits:read channel:read:redemptions channel:manage:broadcast channel:manage:redemptions moderator:manage:chat_settings moderator:manage:shield_mode moderator:manage:announcements'
TWITCH_REQUIRED_SCOPES = frozenset({'user:read:chat'})
_TWITCH_RENEW_LOCK = threading.Lock()
TWITCH_EVENTSUB = 'wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30'
TWITCH_API = 'https://api.twitch.tv/helix'
# Interface palette: graphite surfaces, one pink accent for primary actions, platform colours for Twitch and Kick.
PAL = {
    'ink':'#0f1115','bg':'#171a20','raised':'#232831','hover':'#2d3440','line':'#2d333e','log':'#12151a',
    'text':'#eceff4','muted':'#9099a8','faint':'#6a7382',
    'accent':'#d6336c','accent_hi':'#e4457c','accent_lo':'#b32a5b','accent_dim':'#4a2a40','link':'#ff8fb7',
    'ok':'#3ddc84','idle':'#6a7382','twitch':'#b794ff','kick':'#53fc18',
}
FONT_FAMILY = 'Segoe UI'

PAGE_BG = '#12151a'      # page background, a step darker than the cards that sit on it
FILTER_CARD = PAL['bg']  # card surface (same colour the ttk widgets use)


class ToggleSwitch(tk.Canvas):
    """Pill-shaped on/off switch bound to a BooleanVar."""
    W, H = 44, 24
    def __init__(self, parent, variable, bg=FILTER_CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg, highlightthickness=0, bd=0, cursor='hand2')
        self.variable = variable
        self.bind('<Button-1>', lambda _e: self.variable.set(not self.variable.get()))
        self.bind('<space>', lambda _e: self.variable.set(not self.variable.get()))
        self._trace = variable.trace_add('write', lambda *_a: self._draw())
        self.bind('<Destroy>', lambda _e: self._untrace())
        self._draw()
    def _untrace(self):
        try: self.variable.trace_remove('write', self._trace)
        except Exception: pass
    def _draw(self):
        try: on = bool(self.variable.get())
        except tk.TclError: on = False
        self.delete('all')
        w, h = self.W, self.H
        track = PAL['accent'] if on else '#39404d'
        self.create_oval(0, 0, h, h, fill=track, outline=track)
        self.create_oval(w - h, 0, w, h, fill=track, outline=track)
        self.create_rectangle(h / 2, 0, w - h / 2, h, fill=track, outline=track)
        pad = 3
        x = w - h + pad if on else pad
        self.create_oval(x, pad, x + h - 2 * pad, h - pad, fill='#ffffff' if on else '#c3c9d4', outline='')


class ChipFlow(tk.Frame):
    """Wrapping row of small pill labels (used for the chat voice codes)."""
    def __init__(self, parent, bg=FILTER_CARD):
        super().__init__(parent, bg=bg, height=1)
        self.chips = []; self._width = 0
        self.bind('<Configure>', self._on_configure)
    def set_names(self, names):
        for chip in self.chips: chip.destroy()
        self.chips = [tk.Label(self, text=name, bg=PAL['raised'], fg=PAL['text'], font=(FONT_FAMILY, 9), padx=10, pady=4) for name in names]
        self._layout()
    def _on_configure(self, event):
        if event.width != self._width:
            self._width = event.width; self._layout()
    def _layout(self):
        width = self._width or self.winfo_width()
        if width <= 1: return
        x = y = row_h = 0; gap = 6
        for chip in self.chips:
            chip.update_idletasks()
            cw, ch = chip.winfo_reqwidth(), chip.winfo_reqheight()
            if x and x + cw > width: x = 0; y += row_h + gap; row_h = 0
            chip.place(x=x, y=y); x += cw + gap; row_h = max(row_h, ch)
        self.configure(height=max(1, y + row_h))


def twitch_has_required_scopes(scopes):
    return TWITCH_REQUIRED_SCOPES.issubset(set(scopes or []))


def http_json(url, data=None, headers=None):
    body = None if data is None else json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={**({'Content-Type':'application/json'} if body else {}), **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))

class TwitchDeviceOAuth:
    """Twitch Device Code Flow for a public Windows desktop client."""
    def __init__(self,client_id,callback,status):
        self.client_id=client_id.strip(); self.callback=callback; self.status=status; self.stop_event=threading.Event(); self.thread=None
    def start(self):
        if not self.client_id: raise ValueError('Enter your Twitch app Client ID first.')
        self.thread=threading.Thread(target=self._authorize,daemon=True); self.thread.start()
    def stop(self): self.stop_event.set()
    def _post_form(self,url,form):
        req=urllib.request.Request(url,data=urllib.parse.urlencode(form).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try: return json.loads(e.read().decode('utf-8','replace'))
            except ValueError: raise RuntimeError('Twitch authorization failed ('+str(e.code)+').')
    def _token_request(self,form):
        req=urllib.request.Request('https://id.twitch.tv/oauth2/token',data=urllib.parse.urlencode(form).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read())
    def _authorize(self):
        try:
            device=self._post_form('https://id.twitch.tv/oauth2/device',{'client_id':self.client_id,'scopes':TWITCH_SCOPES})
            code=device.get('device_code')
            if not code: raise RuntimeError(device.get('message') or device.get('error_description') or 'Twitch did not return a device code.')
            uri=device.get('verification_uri','https://www.twitch.tv/activate'); user_code=device.get('user_code','')
            self.status('Twitch login: go to '+uri+' and enter code '+user_code); webbrowser.open(uri)
            interval=max(2,int(device.get('interval',5))); deadline=time.time()+int(device.get('expires_in',1800))
            form={'client_id':self.client_id,'scopes':TWITCH_SCOPES,'device_code':code,'grant_type':'urn:ietf:params:oauth:grant-type:device_code'}
            while not self.stop_event.is_set() and time.time()<deadline:
                time.sleep(interval); result=self._post_form('https://id.twitch.tv/oauth2/token',form)
                if result.get('access_token'):
                    req=urllib.request.Request('https://id.twitch.tv/oauth2/validate',headers={'Authorization':'OAuth '+result['access_token']})
                    with urllib.request.urlopen(req,timeout=10) as r: validated=json.loads(r.read())
                    if validated.get('client_id')!=self.client_id: raise RuntimeError('The token belongs to a different Twitch Client ID.')
                    if not twitch_has_required_scopes(validated.get('scopes',[])): raise RuntimeError('Twitch did not grant chat reading. Log in again and approve the permission.')
                    self.callback(result['access_token'],validated,result.get('refresh_token')); return
                message=(result.get('message') or result.get('error') or '').lower()
                if 'authorization_pending' in message: continue
                if 'slow_down' in message: interval+=5; continue
                if 'expired_token' in message: raise RuntimeError('Twitch login code expired. Click Login on the Twitch account and try again.')
                if 'access_denied' in message: raise RuntimeError('Twitch login was declined.')
                raise RuntimeError(result.get('message') or result.get('error_description') or 'Twitch authorization failed.')
            if not self.stop_event.is_set(): raise RuntimeError('Twitch login code expired. Click Login on the Twitch account and try again.')
        except Exception as e:
            if not self.stop_event.is_set(): self.status('Twitch login failed: '+str(e))
    def refresh(self,refresh_token):
        form={'client_id':self.client_id,'refresh_token':refresh_token,'grant_type':'refresh_token'}
        return self._token_request(form)

CLONE_LANGUAGES=('English','Chinese','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian')

class SpeechEngine:
    WINDOWS_SAPI_PREFIX = 'Windows SAPI: '
    def __init__(self): self.pipelines={}; self.models={}; self.zh11_g2p=None; self.lock=threading.Lock(); self.clone_process=None; self.clone_lock=threading.RLock(); self.clone_model=''; self._preloaded={}; self.muted=False; self.cancel_token=0; self.devices={'me':None,'stream':None}; self.route=('me',); self._warned_stream=False; self.warmed=set(); self.warm_lock=threading.Lock(); self.notify=lambda message: None; self.proxy=None; self.current_kind=''; self.reward_ignore_mute=False
    def _is_muted(self):
        """Ctrl+9 state for the speech playing now: channel point and Power-up voices can be set to ignore it."""
        return self.muted and not (self.reward_ignore_mute and self.current_kind=='reward')
    def _play(self,audio,sample_rate):
        if self._is_muted():
            # Ctrl+9: only the sound is off. The message still "plays" silently so reading stays in step with chat.
            token=self.cancel_token; end=time.time()+len(audio)/float(sample_rate)
            while time.time()<end and self._is_muted() and self.cancel_token==token: time.sleep(0.05)
            return
        # Routing: 'me' = your speakers/headphones, 'stream' = the device OBS listens to. One speech can go to both at once.
        token=self.cancel_token
        picked=[]; use_proxy=False
        for target in tuple(self.route or ('me',)):
            if target=='stream':
                if self.devices.get('stream') is None:
                    if not self._warned_stream:
                        self._warned_stream=True; self.notify('No stream output chosen yet, so this played on your speakers. Pick one on Voice setup > Audio output.')
                    target='me'
                elif self.devices['stream']=='proxy': use_proxy=True; continue  # played by the ChatVoice Stream Audio window below
                else: device=self.devices['stream']
            if target=='me': device=self.devices.get('me')
            if device not in picked: picked.append(device)
        sent=False
        if use_proxy:
            sent=self._send_to_proxy(audio,sample_rate)
            if sent: picked=[]  # the stream audio window already plays on the speakers, so playing here too would double the sound
        if not picked:
            if sent:
                end=time.time()+len(audio)/float(sample_rate)
                while time.time()<end and self.cancel_token==token and not self._is_muted(): time.sleep(0.03)
                if self.cancel_token!=token or self._is_muted(): self.proxy.stop()
                return
            picked=[self.devices.get('me')]
        helpers=[]
        for device in picked[1:]:
            thread=threading.Thread(target=self._play_extra,args=(device,audio,sample_rate,token),daemon=True); thread.start(); helpers.append(thread)
        data=np.asarray(audio,dtype=np.float32)
        if picked[0] is not None and data.ndim==1: data=np.repeat(data[:,None],2,axis=1)
        try:
            sd.play(data,sample_rate,device=picked[0]); sd.wait()
        finally:
            for thread in helpers: thread.join(timeout=60)
    def _send_to_proxy(self,audio,sample_rate):
        """Hands a clip to the ChatVoice Stream Audio window (started on demand). False if it is not available."""
        client=self.proxy
        if client is None: return False
        if not client.running() and not client.start(self.devices.get('me')): return False
        return client.play(audio,sample_rate)
    def _play_extra(self,device,audio,sample_rate,token):
        """Plays the same clip on a second output device (own stream, so it never fights with the main one)."""
        try:
            data=np.asarray(audio,dtype=np.float32)
            if data.ndim==1: data=np.repeat(data[:,None],2,axis=1)
            with sd.OutputStream(samplerate=sample_rate,device=device,channels=data.shape[1],dtype='float32') as out:
                for start in range(0,len(data),4096):
                    if self.cancel_token!=token or self._is_muted(): break
                    out.write(np.ascontiguousarray(data[start:start+4096]))
        except Exception as exc:
            self.notify('Could not play on the second output device: '+str(exc).splitlines()[0][:140])
    @staticmethod
    def list_output_devices():
        """[(name, index)] of the output devices on the same Windows audio system as the default speakers."""
        try:
            api=sd.query_devices(kind='output')['hostapi']
            return [(d['name'],i) for i,d in enumerate(sd.query_devices()) if d['max_output_channels']>0 and d['hostapi']==api]
        except Exception: return []
    @staticmethod
    def _sapi_script():
        return os.path.join(APP_DIR, 'windows_sapi_speech.ps1')
    def list_windows_voices(self):
        try:
            result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',self._sapi_script(),'-Action','List'],capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode != 0: return []
            names=json.loads(result.stdout.strip() or '[]')
            return [str(name) for name in names if name]
        except Exception:
            return []
    def speak_windows_voice(self, text, voice, speed, volume):
        with tempfile.TemporaryDirectory(prefix='chatvoice-sapi-') as folder:
            config_path=os.path.join(folder,'speech.json'); output_path=os.path.join(folder,'speech.wav')
            with open(config_path,'w',encoding='utf-8') as f:
                json.dump({'voice':voice,'text':text,'speed':float(speed),'volume':float(volume),'output':output_path},f,ensure_ascii=False)
            result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',self._sapi_script(),'-Action','Speak','-ConfigPath',config_path],capture_output=True,text=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode != 0 or not os.path.isfile(output_path):
                raise RuntimeError('Windows could not generate audio with the selected SAPI voice. Confirm that the voice is installed and enabled in Windows.')
            audio,sample_rate=sf.read(output_path,dtype='float32',always_2d=False)
            if len(audio): self._play(np.asarray(audio,dtype=np.float32),sample_rate)
    @staticmethod
    def _voice_info(voice, profiles):
        if isinstance(voice,str) and os.path.isfile(voice): return {'kind':'pack','path':voice,'lang':'a'}
        info=profiles.get(voice) or KOKORO_VOICE_INFO.get(voice)
        if info: return info
        # Backward compatibility for saved presets containing the old raw voice IDs.
        for language,lang,voices in KOKORO_VOICE_GROUPS:
            for voice_id,_ in voices:
                if voice == voice_id: return {'kind':'kokoro','voice_id':voice_id,'lang':lang}
        return {'kind':'kokoro','voice_id':voice,'lang':'a'}
    def load_voice_pack(self, voice, profiles=None):
        info=self._voice_info(voice,profiles or {})
        if info.get('kind') == 'clone': raise RuntimeError('Cloned voices cannot be blended with Kokoro voice packs.')
        if info.get('kind') == 'pack':
            import torch
            try: return torch.load(info['path'],map_location='cpu',weights_only=True)
            except TypeError: return torch.load(info['path'],map_location='cpu')
        return self._get_pipeline(info.get('lang','a'),info.get('repo')).load_voice(info.get('voice_id',voice))
    def _zh11_en_callable(self):
        # Lets the v1.1-zh Chinese pipeline pronounce English words; this pipeline only makes phonemes, no audio.
        if self.zh11_g2p is None: self.zh11_g2p=KPipeline(lang_code='a',repo_id=ZH11_REPO,model=False)
        g2p=self.zh11_g2p
        def en_callable(text):
            if text=='Kokoro': return 'kˈOkəɹO'
            if text=='Sol': return 'sˈOl'
            return next(g2p(text)).phonemes
        return en_callable
    def _get_pipeline(self, lang_code='a', repo=None):
        key=(lang_code,repo)
        if key in self.pipelines: return self.pipelines[key]
        if repo:
            # A separate model such as Kokoro v1.1-zh: downloaded on first use and shared by all of its languages.
            model=self.models.get(repo)
            if model is None:
                from kokoro import KModel
                model=KModel(repo_id=repo).eval(); self.models[repo]=model
            options={'lang_code':lang_code,'repo_id':repo,'model':model}
            if lang_code=='z': options['en_callable']=self._zh11_en_callable()
            self.pipelines[key]=KPipeline(**options)
        else:
            model=self.models.get(None)
            if model is None:
                pipeline=KPipeline(lang_code=lang_code); self.models[None]=pipeline.model; self.pipelines[key]=pipeline
            else:
                self.pipelines[key]=KPipeline(lang_code=lang_code,model=model)
        return self.pipelines[key]
    def _render(self, text, voice, speed, profiles=None, lang_code=None, repo=None):
        if not isinstance(voice, (str, int, float)):
            pipeline=self._get_pipeline(lang_code or 'a',repo); chunks=[]
            for _,_,audio in pipeline(text, voice=voice, speed=speed): chunks.append(np.asarray(audio,dtype=np.float32))
            return np.concatenate(chunks) if chunks else np.zeros(0,dtype=np.float32)
        info=self._voice_info(voice,profiles or {})
        if info.get('kind') == 'clone': raise RuntimeError('Use the local clone engine to speak a cloned voice.')
        pipeline=self._get_pipeline(info.get('lang','a'),info.get('repo'))
        voice_id=self.load_voice_pack(voice,profiles) if info.get('kind')=='pack' else info.get('voice_id',voice)
        chunks=[]
        for _,_,audio in pipeline(text, voice=voice_id, speed=speed):
            chunks.append(np.asarray(audio,dtype=np.float32))
        return np.concatenate(chunks) if chunks else np.zeros(0,dtype=np.float32)
    def stop_clone_worker(self):
        self._preloaded={}
        process=self.clone_process; self.clone_process=None
        if process and process.poll() is None:
            try: process.stdin.write(json.dumps({'shutdown':True})+'\n'); process.stdin.flush(); process.wait(timeout=5)
            except Exception:
                try: process.terminate()
                except Exception: pass
    def _start_clone_worker(self):
        if self.clone_process and self.clone_process.poll() is None: return
        root=APP_DIR
        python=os.path.join(root,'.qwen3tts-env','Scripts','python.exe')
        worker=os.path.join(root,'qwen3tts_worker.py')
        engine=os.path.join(root,'qwen3tts-engine')
        if not (os.path.isfile(python) and os.path.isfile(worker) and os.path.isdir(engine)):
            raise RuntimeError('Install the optional local cloning engine from Create Speech first.')
        self.notify('Starting the voice cloning engine. The first time it downloads the voice model (several GB), which can take several minutes...')
        # The engine's own messages go to qwen3tts.log so a failure can be explained instead of silently ignored.
        try: log=open(self.CLONE_LOG,'w',encoding='utf-8')
        except OSError: log=subprocess.DEVNULL
        self.clone_process=subprocess.Popen([python,worker,engine],cwd=engine,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        ready=json.loads(self.clone_process.stdout.readline() or '{}')
        if not ready.get('ready'): raise RuntimeError((ready.get('error') or 'The local voice cloning engine stopped while starting.')+self._clone_log_tail())
        self.notify('Voice cloning engine is ready.'+('' if ready.get('cuda') else ' (No NVIDIA GPU found, so this will be slow.)'))
    CLONE_LOG=os.path.join(APP_DIR,'qwen3tts.log')
    CLONE_LAST=os.path.join(APP_DIR,'last_clone_output.wav')
    @classmethod
    def _clone_log_tail(cls):
        try:
            with open(cls.CLONE_LOG,'r',encoding='utf-8',errors='replace') as fh: lines=[line.strip() for line in fh.read().splitlines() if line.strip()][-6:]
            return ('\n\nLast messages from the engine (full log: qwen3tts.log):\n'+'\n'.join(lines)) if lines else ''
        except OSError: return ''
    def _clone_request(self,profile):
        request={'lang':profile.get('lang','English')}
        if profile.get('model_dir'): request.update({'kind':'finetuned','model_dir':profile['model_dir'],'speaker':profile.get('speaker','')})
        else:
            request.update({'kind':'clone','reference_audio':profile['reference_audio']})
            if self.clone_model: request['model']=self.clone_model
        return request
    def preload_clone(self,profile):
        """Starts the engine, loads the model and warms it up so the first chat message is not slow."""
        try:
            with self.clone_lock:
                self._start_clone_worker()
                if self._preloaded.get(profile.get('reference_audio') or profile.get('model_dir')): return
                self.notify('Preparing the cloned voice (loading model)...')
                request=self._clone_request(profile); request['prepare']=True
                self.clone_process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n'); self.clone_process.stdin.flush()
                response=json.loads(self.clone_process.stdout.readline() or '{}')
                if not response.get('ok'): raise RuntimeError(response.get('error') or 'Preparing the cloned voice failed.')
                self._preloaded[profile.get('reference_audio') or profile.get('model_dir')]=True
                self.notify('Cloned voice is ready.')
        except Exception as exc:
            self.notify('Cloned voice could not be prepared: '+str(exc).splitlines()[0][:150])
    def _drain_clone(self):
        # Read and discard whatever the engine still sends for the current message so the next request starts clean.
        while True:
            try: response=json.loads(self.clone_process.stdout.readline() or '{}')
            except ValueError: continue
            if 'chunk' not in response: return
    def cancel(self):
        """Stops a message that is still being generated (Stop audio / Pause)."""
        self.cancel_token+=1
        if self.proxy is not None: self.proxy.stop()
    def _speak_streaming(self,text,voice,speed,volume,profiles):
        """Plays each sentence as soon as it is generated instead of waiting for the whole message."""
        info=self._voice_info(voice,profiles or {})
        pipeline=self._get_pipeline(info.get('lang','a'),info.get('repo'))
        voice_id=self.load_voice_pack(voice,profiles) if info.get('kind')=='pack' else info.get('voice_id',voice)
        token=self.cancel_token; playback=queue.Queue(); state={'error':None}
        def player():
            while True:
                audio=playback.get()
                if audio is None: return
                if self.cancel_token!=token: continue
                try: self._play(np.clip(audio*volume,-1,1),24000)
                except Exception as exc: state['error']=exc
        thread=threading.Thread(target=player,daemon=True); thread.start()
        try:
            for _,_,audio in pipeline(text,voice=voice_id,speed=speed,split_pattern=r'(?<=[.!?])\s+'):
                if self.cancel_token!=token: break
                playback.put(np.asarray(audio,dtype=np.float32))
        finally:
            playback.put(None); thread.join()
        if state['error']: raise state['error']
    def warm_up(self,labels,profiles=None,render=True):
        """Loads models and voice packs ahead of time so a voice's first chat message is not slow."""
        profiles=profiles or {}
        for label in labels:
            if not isinstance(label,str) or label in self.warmed: continue
            info=self._voice_info(label,profiles)
            if info.get('kind') not in ('kokoro',) or label.startswith(self.WINDOWS_SAPI_PREFIX): continue
            try:
                with self.warm_lock, self.lock:
                    pipeline=self._get_pipeline(info.get('lang','a'),info.get('repo'))
                    voice_id=info.get('voice_id',label); pipeline.load_voice(voice_id)
                    if render:
                        for _ in pipeline('Ready.',voice=voice_id,speed=1.0): pass
                self.warmed.add(label)
            except Exception: self.warmed.add(label)
    def _speak_clone(self,text,profile,speed,volume):
        with self.clone_lock:
            self._start_clone_worker()
            self.notify('Generating cloned speech...')
            playback=queue.Queue(); state={'gain':None,'error':None}
            def player():
                while True:
                    item=playback.get()
                    if item is None: return
                    audio,rate=item
                    try: self._play(audio*float(volume),rate)
                    except Exception as exc: state['error']=exc
            thread=threading.Thread(target=player,daemon=True); thread.start()
            try:
                with tempfile.TemporaryDirectory(prefix='chatvoice-clone-') as folder:
                    request=self._clone_request(profile); request.update({'text':text,'output_path':os.path.join(folder,'speech.wav')})
                    self.clone_process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n'); self.clone_process.stdin.flush()
                    chunks=[]; first=True
                    while True:
                        response=json.loads(self.clone_process.stdout.readline() or '{}')
                        if 'chunk' in response:
                            audio,rate=sf.read(response['chunk'],dtype='float32',always_2d=False)
                            audio=np.asarray(audio,dtype=np.float32); chunks.append(audio)
                            peak=float(np.max(np.abs(audio))) if len(audio) else 0.0
                            if state['gain'] is None:  # one gain for the whole message so sentences keep the same loudness
                                if not np.isfinite(peak) or peak<1e-4: self._drain_clone(); raise RuntimeError('The cloning engine made a silent recording. Try a clearer, louder reference voice. A copy is saved as last_clone_output.wav in the app folder.')
                                state['gain']=(0.3/peak) if peak<0.1 else (1.0/peak if peak>1 else 1.0)
                            audio=np.clip(audio*state['gain'],-1,1)
                            if first: self.notify(f'Playing cloned speech...'); first=False
                            playback.put((audio,rate)); last_rate=rate
                            continue
                        if not response.get('ok'): raise RuntimeError((response.get('error') or 'Local voice cloning stopped without returning audio.')+self._clone_log_tail())
                        break
                    if chunks:
                        try: sf.write(self.CLONE_LAST,np.concatenate(chunks),last_rate)  # a copy to listen to when troubleshooting
                        except Exception: pass
            finally:
                playback.put(None); thread.join()
            if state['error']: raise state['error']
            self.notify('Finished playing cloned speech.')
    @staticmethod
    def _join_audio(parts, smoothness_ms):
        parts=[p for p in parts if len(p)]
        if not parts: return np.zeros(0,dtype=np.float32)
        result=parts[0]
        overlap=max(0,int(24000*float(smoothness_ms)/1000))
        for part in parts[1:]:
            n=min(overlap,len(result),len(part))
            if n:
                fade=np.linspace(0,1,n,dtype=np.float32)
                result=np.concatenate((result[:-n],result[-n:]*(1-fade)+part[:n]*fade,part[n:]))
            else: result=np.concatenate((result,part))
        return result
    def speak_config(self, text, cfg):
        voices=cfg.get('voices',['af_heart'])
        selected_voice=cfg.get('selected_voice',voices[0])
        selected_info=self._voice_info(selected_voice,cfg.get('voice_profiles',{}))
        if selected_info.get('kind')=='preset':
            preset_cfg=dict(selected_info.get('config') or {})
            preset_cfg['speed']=float(cfg.get('speed',preset_cfg.get('speed',1)))
            preset_cfg['volume']=float(cfg.get('volume',preset_cfg.get('volume',.8)))
            return self.speak_config(text,preset_cfg)
        with self.lock:
            mode=cfg.get('mode','Single voice'); speed=float(cfg.get('speed',1)); volume=float(cfg.get('volume',.8))
            smooth=float(cfg.get('smoothness',35)); voices=cfg.get('voices',['af_heart','af_bella','am_adam'])
            profiles=cfg.get('voice_profiles',{})
            count=max(2,min(3,int(cfg.get('speaker_count',2))))
            selected_voice=cfg.get('selected_voice',voices[0]) if mode=='Take turns per message' else voices[0]
            selected_info=self._voice_info(selected_voice,profiles)
            if mode=='Take turns per word':
                word_voice_infos=[self._voice_info(voice,profiles) for voice in voices[:count]]
                if any(info.get('kind')=='clone' for info in word_voice_infos) or any(isinstance(voice,str) and voice.startswith(self.WINDOWS_SAPI_PREFIX) for voice in voices[:count]):
                    raise RuntimeError('Take turns per word supports Kokoro voices and Kokoro voice packs. Cloned and Windows voices cannot be switched word by word.')
            if selected_info.get('kind') == 'clone':
                if mode not in ('Single voice','Take turns per message'):
                    raise RuntimeError('Cloned voices only work in the Take turns per message mode (or as a voice on the Text to Speech page). Switch the mode and try again.')
                self._speak_clone(text,selected_info,speed,volume)
                return
            if isinstance(selected_voice,str) and selected_voice.startswith(self.WINDOWS_SAPI_PREFIX):
                if self._is_muted(): return
                self.speak_windows_voice(text,selected_voice[len(self.WINDOWS_SAPI_PREFIX):],speed,volume)
                return
            if mode in ('Single voice','Take turns per message'):
                self._speak_streaming(text,selected_voice,speed,volume,profiles); return
            if mode=='Blend voices':
                # Kokoro voice packs are tensors. Weighted averaging creates one mixed voice identity.
                import torch
                weights=[max(0.0,float(x)) for x in cfg.get('weights',[50,50,0])[:count]]
                total=sum(weights) or 1.0; weights=[x/total for x in weights]
                infos=[self._voice_info(v,profiles) for v in voices[:count]]
                if len({voice_group(item) for item in infos})>1: raise RuntimeError('Blend voices from the same language and the same Kokoro model only (v1.1 voices cannot be mixed with standard voices).')
                packs=[self.load_voice_pack(v,profiles) for v in voices[:count]]
                mixed=packs[0]*weights[0]
                for pack,weight in zip(packs[1:],weights[1:]): mixed=mixed+pack*weight
                audio=self._render(text,mixed,speed,profiles,infos[0].get('lang','a'),infos[0].get('repo'))
            elif mode=='Sentence split':
                segments=re.split(r'(?<=[.!?])\s+',text.strip())
                if len(segments)==1:
                    words=text.split()
                    if len(words)>3:
                        step=max(1,(len(words)+count-1)//count)
                        segments=[' '.join(words[i:i+step]) for i in range(0,len(words),step)]
                parts=[self._render(segment,voices[i%count],speed,profiles) for i,segment in enumerate(segments) if segment.strip()]
                audio=self._join_audio(parts,smooth)
            elif mode=='Take turns per message':
                audio=self._render(text,cfg.get('selected_voice',voices[0]),speed,profiles)
            elif mode=='Take turns per word':
                words=re.findall(r'\S+',text.strip())
                parts=[]
                for index,word in enumerate(words):
                    parts.append(self._render(word,voices[index%count],speed,profiles))
                    if index<len(words)-1:
                        if re.search(r"[.!?][\"'”’)]*$",word): pause=.19
                        elif re.search(r"[,;:][\"'”’)]*$",word): pause=.11
                        else: pause=.035
                        parts.append(np.zeros(int(24000*pause),dtype=np.float32))
                audio=np.concatenate(parts) if parts else np.zeros(0,dtype=np.float32)
            else:
                audio=self._render(text,voices[0],speed,profiles)
            if len(audio):
                audio=np.clip(audio*volume,-1,1); self._play(audio,24000)

class TwitchReader:
    def __init__(self, token, client_id, user_id, channel, on_event, status, refresh_token=None, save_tokens=None):
        self.token,self.client_id,self.user_id=token,client_id,user_id
        self.refresh_token=refresh_token; self.save_tokens=save_tokens; self.token_lock=threading.Lock()
        self.channel=channel.lower().lstrip('#')
        self.on_event,self.status=on_event,status; self.stop_event=threading.Event(); self.thread=None
    def start(self):
        self.thread=threading.Thread(target=lambda:asyncio.run(self.run()),daemon=True); self.thread.start()
    def stop(self): self.stop_event.set()
    def _say(self,message):
        # A reader that was stopped (e.g. by Reconnect) must not touch the UI status any more.
        if not self.stop_event.is_set(): self.status(message)
    async def _recv(self,ws,limit=45):
        # recv() in 1-second slices so stop() takes effect quickly instead of after the keepalive timeout.
        waited=0
        while not self.stop_event.is_set():
            try: return await asyncio.wait_for(ws.recv(),1)
            except asyncio.TimeoutError:
                waited+=1
                if waited>=limit: raise
        return None
    @staticmethod
    def _fragments(event):
        out=[]
        for f in ((event.get('message') or {}).get('fragments') or []):
            if not isinstance(f,dict): continue
            cheer=f.get('cheermote') or {}
            out.append({'type':f.get('type'),'text':f.get('text',''),'id':(f.get('emote') or {}).get('id'),'prefix':cheer.get('prefix'),'bits':cheer.get('bits'),'tier':cheer.get('tier')})
        return out
    def _emit(self,event):
        if self.stop_event.is_set(): return
        msg=event.get('message',{}).get('text','')
        self.on_event('Twitch','message',event.get('chatter_user_name') or event.get('chatter_user_login',''),msg,{'fragments':self._fragments(event),'message_type':event.get('message_type'),'highlighted':event.get('channel_points_custom_reward_id') is not None,'message_id':event.get('message_id'),'user_id':event.get('chatter_user_id'),'bits':((event.get('cheer') or {}).get('bits',0) if isinstance(event.get('cheer'),dict) else 0),'badges':[str(b.get('set_id','')).lower() for b in (event.get('badges') or []) if isinstance(b,dict)],'badge_ids':[[str(b.get('set_id','')).lower(),str(b.get('id',''))] for b in (event.get('badges') or []) if isinstance(b,dict)]})
    def _emit_ban(self,event):
        if self.stop_event.is_set(): return
        name=event.get('target_user_name') or event.get('target_user_login') or ''
        if name: self.on_event('Twitch','banned',name,'',{'user_id':event.get('target_user_id')})
    def _emit_suspicious(self,event):
        # Twitch flags a chatter as a possible/likely ban evader (channel.suspicious_user.message).
        if self.stop_event.is_set(): return
        name=event.get('user_name') or event.get('user_login') or ''
        if not name: return
        self.on_event('Twitch','suspicious',name,(event.get('message') or {}).get('text',''),{'user_id':event.get('user_id'),'login':event.get('user_login'),'broadcaster_id':event.get('broadcaster_user_id') or getattr(self,'broadcaster_id',''),'evaluation':event.get('ban_evasion_evaluation'),'low_trust_status':event.get('low_trust_status'),'types':event.get('types') or []})
    def _subscribe_suspicious(self,broadcaster_id,session):
        # Needs the moderator:read:suspicious_users permission and a broadcaster/moderator login. Chat keeps working without it.
        reason=''
        try: self._api('/eventsub/subscriptions',{'type':'channel.suspicious_user.message','version':'1','condition':{'broadcaster_user_id':broadcaster_id,'moderator_user_id':self.user_id},'transport':{'method':'websocket','session_id':session}})
        except urllib.error.HTTPError as e:
            if e.code in (401,403): reason='Twitch refused it. Log out of Twitch, log in again and approve the new permission, and make sure this account is the broadcaster or a moderator of #'+self.channel+'.'
            else: reason='Twitch returned HTTP '+str(e.code)+'.'
        except Exception as e: reason=str(e) or 'unknown error'
        if not self.stop_event.is_set(): self.on_event('Twitch','suspicious_status','',reason,{'channel':self.channel})
    def _dispatch(self,sub_type,event):
        if sub_type=='channel.chat.message': self._emit(event)
        elif sub_type=='channel.chat.clear_user_messages': self._emit_ban(event)
        elif sub_type=='channel.suspicious_user.message': self._emit_suspicious(event)
        else: self._emit_feed(sub_type,event)
    def _subscribe_events(self,broadcaster_id,session):
        # Follows, subs, raids and channel point redemptions for the Event feed. Only your own channel can see these.
        if str(broadcaster_id)!=str(self.user_id): return
        b=broadcaster_id
        wanted=[('channel.follow','2',{'broadcaster_user_id':b,'moderator_user_id':self.user_id}),
                ('channel.subscribe','1',{'broadcaster_user_id':b}),
                ('channel.subscription.gift','1',{'broadcaster_user_id':b}),
                ('channel.subscription.message','1',{'broadcaster_user_id':b}),
                ('channel.raid','1',{'to_broadcaster_user_id':b}),
                ('channel.channel_points_custom_reward_redemption.add','1',{'broadcaster_user_id':b}),
                ('channel.custom_power_up_redemption.add','1',{'broadcaster_user_id':b})]
        failed=[]
        for sub_type,version,condition in wanted:
            if self.stop_event.is_set(): return
            try: self._api('/eventsub/subscriptions',{'type':sub_type,'version':version,'condition':condition,'transport':{'method':'websocket','session_id':session}})
            except urllib.error.HTTPError as e: failed.append(sub_type.split('.',1)[1]+' ('+str(e.code)+')')
            except Exception: failed.append(sub_type.split('.',1)[1])
        if failed and not self.stop_event.is_set():
            self.on_event('Twitch','feed_status','','Some Twitch events for the Event feed could not start: '+', '.join(failed)+'. Log out of Twitch, log in again and approve the new permissions.',{})
    def _emit_feed(self,sub_type,event):
        if self.stop_event.is_set() or not isinstance(event,dict): return
        tier={'1000':'Tier 1','2000':'Tier 2','3000':'Tier 3'}.get(str(event.get('tier')),'')
        tier_text=(' ('+tier+')') if tier else ''
        name=event.get('user_name') or event.get('user_login') or ''; uid=event.get('user_id') or ''
        amount=0; extra={}
        if sub_type=='channel.follow': kind,text='follow','followed'
        elif sub_type=='channel.subscribe':
            if event.get('is_gift'): return
            kind,text='sub','subscribed'+tier_text
        elif sub_type=='channel.subscription.gift':
            if event.get('is_anonymous'): name,uid='Anonymous',''
            try: amount=int(event.get('total') or 1)
            except (TypeError,ValueError): amount=1
            kind,text='gift','gifted '+str(amount)+' sub'+('s' if amount!=1 else '')+tier_text
        elif sub_type=='channel.subscription.message':
            months=event.get('cumulative_months'); message=(event.get('message') or {}).get('text','')
            kind,text='resub','resubscribed'+tier_text+((', '+str(months)+' months') if months else '')+((': '+message) if message else '')
        elif sub_type=='channel.raid':
            name=event.get('from_broadcaster_user_name') or event.get('from_broadcaster_user_login') or ''; uid=event.get('from_broadcaster_user_id') or ''
            try: amount=int(event.get('viewers') or 0)
            except (TypeError,ValueError): amount=0
            kind,text='raid','raided with '+str(amount)+' viewer'+('s' if amount!=1 else '')
        elif sub_type=='channel.channel_points_custom_reward_redemption.add':
            reward=event.get('reward') or {}
            try: amount=int(reward.get('cost') or 0)
            except (TypeError,ValueError): amount=0
            note=str(event.get('user_input') or '').strip()
            kind,text='points','redeemed '+str(reward.get('title') or 'a reward')+' ('+str(amount)+' points)'+((': '+note) if note else '')
            extra={'reward':str(reward.get('title') or ''),'input':note}
        elif sub_type=='channel.custom_power_up_redemption.add':
            power=event.get('custom_power_up') or {}
            try: amount=int(power.get('bits') or 0)
            except (TypeError,ValueError): amount=0
            note=str(event.get('user_input') or '').strip()
            kind,text='powerup','used the Power-up '+str(power.get('title') or 'a Power-up')+' ('+str(amount)+' Bits)'+((': '+note) if note else '')
            extra={'reward':str(power.get('title') or ''),'input':note}
        else: return
        self.on_event('Twitch','feed',name,text,dict({'feed':kind,'amount':amount,'user_id':uid},**extra))
    def _api(self, path, data=None):
        with self.token_lock: token=self.token
        for attempt in range(2):
            req=urllib.request.Request(TWITCH_API+path, data=None if data is None else json.dumps(data).encode(), method='GET' if data is None else 'POST', headers={'Authorization':'Bearer '+token,'Client-Id':self.client_id,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=15) as response:
                    raw=response.read(); return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code!=401 or attempt or not self.refresh_token: raise
                form={'client_id':self.client_id,'refresh_token':self.refresh_token,'grant_type':'refresh_token'}
                request=urllib.request.Request('https://id.twitch.tv/oauth2/token',data=urllib.parse.urlencode(form).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
                with urllib.request.urlopen(request,timeout=15) as response: renewed=json.loads(response.read())
                self.token=renewed['access_token']; self.refresh_token=renewed.get('refresh_token',self.refresh_token); token=self.token
                if self.save_tokens: self.save_tokens(self.token,self.refresh_token)
    async def run(self):
        backoff=2
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(TWITCH_EVENTSUB,ping_interval=20,ping_timeout=20,close_timeout=5) as ws:
                    first=json.loads(await asyncio.wait_for(ws.recv(),15))
                    if first.get('metadata',{}).get('message_type')!='session_welcome': raise RuntimeError('Twitch did not send an EventSub welcome message.')
                    session=first['payload']['session']['id']
                    user=self._api('/users?id='+urllib.parse.quote(self.user_id)).get('data',[])
                    if not user: raise RuntimeError('Could not verify the authorized Twitch account.')
                    channel=self._api('/users?login='+urllib.parse.quote(self.channel)).get('data',[])
                    if not channel: raise RuntimeError('Twitch channel not found: '+self.channel)
                    broadcaster_id=channel[0]['id']; self.broadcaster_id=broadcaster_id
                    self.on_event('Twitch','emote_channel','','',{'channel_id':broadcaster_id})
                    try:
                        sets={}
                        for path in ('/chat/badges/global','/chat/badges?broadcaster_id='+urllib.parse.quote(broadcaster_id)):
                            for item in (self._api(path).get('data') or []):
                                sets.setdefault(str(item.get('set_id','')).lower(),{}).update({str(v.get('id')):(v.get('image_url_2x') or v.get('image_url_1x')) for v in (item.get('versions') or []) if isinstance(v,dict)})
                        self.on_event('Twitch','badge_sets','','',{'twitch':sets})
                    except Exception: pass
                    sub={'type':'channel.chat.message','version':'1','condition':{'broadcaster_user_id':broadcaster_id,'user_id':self.user_id},'transport':{'method':'websocket','session_id':session}}
                    self._api('/eventsub/subscriptions',sub)
                    try: self._api('/eventsub/subscriptions',{'type':'channel.chat.clear_user_messages','version':'1','condition':{'broadcaster_user_id':broadcaster_id,'user_id':self.user_id},'transport':{'method':'websocket','session_id':session}})
                    except Exception: pass
                    self._subscribe_suspicious(broadcaster_id,session)
                    self._subscribe_events(broadcaster_id,session)
                    self._say('Twitch chat connected: #'+self.channel+' (EventSub)'); backoff=2
                    target_ws=ws; handoff=None
                    while not self.stop_event.is_set():
                        raw=await self._recv(target_ws,45)
                        if raw is None: break
                        packet=json.loads(raw); meta=packet.get('metadata',{}); kind=meta.get('message_type')
                        if kind=='session_reconnect':
                            handoff=packet['payload']['session'].get('reconnect_url')
                            if handoff: break
                        elif kind=='notification': self._dispatch(meta.get('subscription_type'),packet['payload']['event'])
                        elif kind=='revocation': raise RuntimeError('Twitch chat authorization was revoked: '+str(packet.get('payload',{}).get('subscription',{}).get('status','unknown')))
                    if handoff and not self.stop_event.is_set():
                        await self._follow_reconnect(handoff)
                    else: break
            except urllib.error.HTTPError as e:
                detail=e.read().decode('utf-8','replace')
                reason='Twitch API error ('+str(e.code)+'): '+detail[:500]
            except asyncio.TimeoutError: reason='Twitch EventSub timed out; reconnecting…'
            except Exception as e: reason='Twitch chat disconnected: '+str(e)
            if self.stop_event.is_set(): break
            self._say(reason+' (retrying)'); await asyncio.sleep(backoff); backoff=min(backoff*2,30)
        self._say('Twitch chat disconnected.')
    async def _follow_reconnect(self,url):
        async with websockets.connect(url,ping_interval=20,ping_timeout=20,close_timeout=5) as ws:
            welcome=json.loads(await asyncio.wait_for(ws.recv(),15))
            if welcome.get('metadata',{}).get('message_type')!='session_welcome': raise RuntimeError('Twitch reconnect did not complete.')
            self._say('Twitch chat reconnected: #'+self.channel)
            while not self.stop_event.is_set():
                raw=await self._recv(ws,45)
                if raw is None: return
                packet=json.loads(raw); meta=packet.get('metadata',{}); kind=meta.get('message_type')
                if kind=='notification': self._dispatch(meta.get('subscription_type'),packet['payload']['event'])
                elif kind=='session_reconnect':
                    next_url=packet.get('payload',{}).get('session',{}).get('reconnect_url')
                    if next_url: await self._follow_reconnect(next_url)
                    return
                elif kind=='revocation': raise RuntimeError('Twitch chat authorization was revoked.')

class App:
    def _load_connection_settings(self):
        try:
            with open(os.path.join(APP_DIR,'connection_settings.json'),'r',encoding='utf-8') as f: return json.load(f)
        except (OSError,json.JSONDecodeError): return {}
    def _save_connection_settings(self):
        try:
            with open(self.settings_path,'w',encoding='utf-8') as f: json.dump(self.settings,f,indent=2)
        except OSError as e: self.set_status('Could not save connection settings: '+str(e))
    def _read_refresh_token(self):
        key=self.settings.get('twitch_client_id') or DEFAULT_TWITCH_CLIENT_ID
        if keyring:
            try: return keyring.get_password('ChatVoice Local',key) or keyring.get_password(LEGACY_KEYRING,key)
            except Exception: pass
        if win32crypt:
            try:
                with open(os.path.join(APP_DIR,'twitch_refresh.dpapi'),'rb') as f: return win32crypt.CryptUnprotectData(f.read(),None,None,None,0)[1].decode('utf-8')
            except Exception: return None
        return None
    def _save_tokens(self,access_token,refresh_token):
        # May run on a background thread, so it must not read Tk variables.
        self.twitch_token=access_token; self.twitch_refresh_token=refresh_token
        if not refresh_token: return
        key=self.settings.get('twitch_client_id') or DEFAULT_TWITCH_CLIENT_ID; errors=[]
        if keyring:
            try: keyring.set_password('ChatVoice Local',key,refresh_token); return
            except Exception as e: errors.append(str(e))
        if win32crypt:
            try:
                blob=win32crypt.CryptProtectData(refresh_token.encode('utf-8'),'ChatVoice Local',None,None,None,0)[1]
                with open(os.path.join(APP_DIR,'twitch_refresh.dpapi'),'wb') as f: f.write(blob)
                return
            except Exception as e: errors.append(str(e))
        self.set_status('Twitch token is active, but secure refresh-token storage failed: '+('; '.join(errors) or 'no secure storage is available'))
    def __init__(self,root):
        self.root=root; self._set_app_icon(); root.title(APP_TITLE+' — Twitch + Kick chat'); root.geometry('980x940'); root.minsize(820,760)
        self.messages=queue.Queue(MAX_QUEUE); self.ui_events=queue.Queue(); self.engine=SpeechEngine(); self.engine.notify=self.set_status; register_zh11_voices(load_zh11_voice_ids()); self.readers=[]; self.twitch_chat_connected=False; self.kick_chat_connected=False; self.custom_voice_dir='custom_voices'; self.saved_voice_dir='saved_voices'; os.makedirs(self.custom_voice_dir,exist_ok=True); os.makedirs(self.saved_voice_dir,exist_ok=True); self.voice_catalog={}; self.worker_stop=threading.Event(); self.speaker_turn=0; self.activity_user_tag_counter=0; self.activity_entries=[]
        self.tts_muted=False; self.clear_hotkey_ok=False; self.mute_hotkey_ok=False; self.mute_hotkey_thread_id=None; self.tts_paused=False; self.tts_resume_event=threading.Event(); self.tts_resume_event.set(); self.discard_paused_pending=False; self.worker_has_pending=False; self.held_items=[]; self.last_blocked_user=None
        self.settings_path=os.path.join(APP_DIR,'connection_settings.json')
        self.settings=self._load_connection_settings(); self.twitch_token=None; self.twitch_user_id=None; self.twitch_oauth=None; self.auth_generation=0
        default_chat_voices=['American English — Heart (af_heart)','American English — Bella (af_bella)','American English — Adam (am_adam)','British English — George (bm_george)','American English — Nicole (af_nicole)']
        saved_chat_voices=self.settings.get('chat_voice_slots',default_chat_voices)
        self.chat_voice_slots=(list(saved_chat_voices[:5])+default_chat_voices[len(saved_chat_voices[:5]):]) if isinstance(saved_chat_voices,list) else default_chat_voices
        self.chat_voice_slots=[normalize_voice_label(v) for v in self.chat_voice_slots]
        self.user_voice_assignments={key:int(value) for key,value in self.settings.get('user_voice_assignments',{}).items() if str(value).isdigit() and 0<=int(value)<5} if isinstance(self.settings.get('user_voice_assignments',{}),dict) else {}
        self.chat_voice_users={key:(key.split('|',1)[0],key.split('|',1)[1]) for key in self.user_voice_assignments if '|' in key}
        # Paid voice codes ("Heart: hello" only for viewers who cheered Bits / gifted Kicks). On by default.
        self.paid_voices_enabled=tk.BooleanVar(value=bool(self.settings.get('paid_voices_enabled',True)))
        self.paid_reply=tk.BooleanVar(value=bool(self.settings.get('paid_voices_reply',True)))
        def _saved_price(name):
            try: return max(1,min(1000000,int(self.settings.get(name,50))))
            except (TypeError,ValueError): return 50
        self.paid_scope=tk.StringVar(value=self.settings.get('paid_voices_scope') if self.settings.get('paid_voices_scope') in ('codes','all') else 'codes')
        self.paid_free_mods=tk.BooleanVar(value=bool(self.settings.get('paid_free_mods',False))); self.paid_free_vips=tk.BooleanVar(value=bool(self.settings.get('paid_free_vips',False)))
        self.paid_bits=tk.IntVar(value=_saved_price('paid_bits_per_30')); self.paid_kicks=tk.IntVar(value=_saved_price('paid_kicks_per_30'))
        self.paid_voice_access=self._load_paid_voice_access(); self.paid_notice_last={}
        # Extra paid-voice options: subscriber rules, free trial, minimum cheer, time cap, reminders, free-for-everyone timer.
        self.paid_sub_mode=tk.StringVar(value='pay'); self.paid_sub_bits=tk.IntVar(value=25); self.paid_sub_kicks=tk.IntVar(value=25)
        self.paid_min_bits=tk.IntVar(value=10); self.paid_min_kicks=tk.IntVar(value=1); self.paid_max_days=tk.IntVar(value=365)
        self.paid_trial_mode=tk.StringVar(value='off'); self.paid_trial_amount=tk.IntVar(value=5)
        self.paid_remind=tk.BooleanVar(value=False); self.paid_remind_days=tk.IntVar(value=3)
        self.paid_free_until=0.0; self.paid_trials={}; self.paid_history=[]
        self._apply_paid_extra_settings()
        # Word block list: messages with a word from blocked_words.txt (in any disguise) are never read aloud.
        self.blocklist_on=tk.BooleanVar(value=bool(self.settings.get('filter_blocklist',True))); self.blocked_word_hits=0
        self.word_blocker=word_blocker.WordBlocker(os.path.join(APP_DIR,'blocked_words.txt')); self.word_blocker.ensure_file(); self.word_blocker.load()
        self.dock=None; self.dock_lock=threading.Lock(); self.dock_messages=collections.deque(maxlen=400); self.dock_next_id=1; self.dock_notices=collections.deque(maxlen=30); self.dock_notice_id=1; self.dock_banned=collections.deque(maxlen=50); self.dock_banned_id=0; self.speaking_now=None; self.dock_state={'send':{},'mod':{},'timeout':''}
        self.kick_reader=None; self.kick_login=None; self.kick_tokens=None; self.kick_slug=''; self.kick_user_id=''; self.kick_token_lock=threading.RLock()
        self.engine.clone_model=self.settings.get('clone_model','')
        if self.settings.get('kick_token_relay'): kick_auth.RELAY_URL=self.settings['kick_token_relay']
        self.ignore_users_path=os.path.join(APP_DIR,'ignored_users.json')
        self.ignored_users=self._load_ignored_users()
        self.feed=dashboard.EventFeed(); self.feed.load(os.path.join(APP_DIR,'feed_today.json'))
        # Emotes: 7TV, BetterTTV, FrankerFaceZ, Twitch and Kick, shown in Activity and in the OBS dock.
        self.emote_on=tk.BooleanVar(value=bool(self.settings.get('emotes_enabled',True)))
        self.emote_provider={p:tk.BooleanVar(value=bool(self.settings.get('emotes_'+p,True))) for p in emotes.PROVIDER_LABELS}
        self.emote_animate=tk.BooleanVar(value=bool(self.settings.get('emotes_animated',True)))
        self.badge_on=tk.BooleanVar(value=bool(self.settings.get('badges_enabled',True)))
        self.emote_skip_speech=tk.BooleanVar(value=bool(self.settings.get('emotes_skip_speech',False)))
        self.emote_size=tk.StringVar(value=self.settings.get('emotes_size') if self.settings.get('emotes_size') in EMOTE_SIZES else 'Medium')
        self.emote_store=emotes.EmoteStore(os.path.join(APP_DIR,'emote_cache'))
        self.emote_images=emotes.EmoteImages(self.emote_store,EMOTE_SIZES[self.emote_size.get()],lambda key:self.ui_events.put(('emote_ready',key))) if PIL_OK else None
        self.emote_channel_key={}; self.emote_pending={}; self.emote_anim={}
        self.emote_store.enabled.update({p:v.get() for p,v in self.emote_provider.items()})
        # Chat Moderation: automatic ban-evader handling (Twitch). Off until you switch it on.
        self.evader_enabled=tk.BooleanVar(value=bool(self.settings.get('modrules_evader_enabled',False)))
        self.evader_likely=tk.StringVar(value=self._evader_choice(self.settings.get('modrules_evader_likely'),'ban'))
        self.evader_possible=tk.StringVar(value=self._evader_choice(self.settings.get('modrules_evader_possible'),'nothing'))
        try: _evader_secs=max(1,min(1209600,int(self.settings.get('modrules_evader_timeout',600))))
        except (TypeError,ValueError): _evader_secs=600
        self.evader_timeout=tk.IntVar(value=_evader_secs)
        self.evader_silence=tk.BooleanVar(value=bool(self.settings.get('modrules_evader_silence',True)))
        _allow=self.settings.get('modrules_evader_allow',[])
        self.evader_allow=sorted({self._normalize_username(n) for n in _allow if self._normalize_username(n)}) if isinstance(_allow,list) else []
        self.evader_marks={}; self.evader_recent={}; self.evader_log=[]; self.evader_available=None
        self.worker=threading.Thread(target=self._speech_worker,daemon=True); self.worker.start(); self._build_ui(); self._start_mute_hotkey(); self._refresh_presets(); root.after(100,self._poll_ui); root.after(1200,self._emote_startup); root.after(600,self._restore_twitch_session); root.after(900,self._restore_kick_session); root.after(2500,self._preload_selected_clone); root.after(1200,self._warm_up_voices); root.after(1500,self._start_dock_server); self._watch_voice_vars(); root.protocol('WM_DELETE_WINDOW',self.close)
    def _build_ui(self):
        self.root.title(APP_TITLE+' — Twitch + Kick chat'); self.root.geometry('1180x860'); self.root.minsize(980,680)
        self._apply_theme()
        self._build_shell()
        self.client_id=tk.StringVar(value=self.settings.get('twitch_client_id',DEFAULT_TWITCH_CLIENT_ID)); self.channel=tk.StringVar(value=self.settings.get('twitch_channel',self.settings.get('twitch_login',''))); self.twitch_user=tk.StringVar(value=self.settings.get('twitch_login',''))
        self.twitch_refresh_token=self._read_refresh_token()
        self._build_connection_page(self.pages['Login & Chat'])
        self._build_voice_page(self.pages['Voice'])
        self._build_chat_voices_page(self.five_voice_panel)
        self._build_filter_page(self.pages['Advanced Filter'])
        self._build_blocked_page(self.pages['Blocked users'])
        self._build_activity_page(self.pages['Activity'])
        dashboard.build_dashboard_page(self,self.pages['Dashboard'],PAL,FONT_FAMILY,PAGE_BG)
        self._build_chat_page(self.pages['Chat'])
        self.root.after(4000,lambda:dashboard.poll(self)); self.root.after(1000,lambda:dashboard.tick_uptime(self))
        try: has_login=bool(self.twitch_refresh_token or self._kick_load('refresh_token'))
        except Exception: has_login=False
        self.show_page('Dashboard' if has_login else 'Login & Chat'); self._refresh_side_connections()
    def show_page(self,name):
        for page in self.pages.values(): page.pack_forget()
        self.pages[name].pack(fill='both',expand=True)
        self.current_page=name
        if name=='Dashboard': dashboard.poll(self,repeat=False)
        elif name=='Chat': self._load_chat_tab()
        for key,button in getattr(self,'nav_buttons',{}).items():
            active=key==name
            button.configure(style='NavActive.TButton' if active else 'Nav.TButton')
            self.nav_marks[key].configure(bg=PAL['accent'] if active else PAL['ink'])
    def _apply_theme(self):
        global FONT_FAMILY
        try: families=set(tkfont.families(self.root))
        except Exception: families=set()
        FONT_FAMILY=next((f for f in ('Segoe UI Variable Text','Segoe UI','Helvetica Neue','Helvetica') if f in families),'Segoe UI')
        for font_name in ('TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont','TkCaptionFont','TkSmallCaptionFont','TkIconFont','TkTooltipFont'):
            try: tkfont.nametofont(font_name).configure(family=FONT_FAMILY,size=10)
            except Exception: pass
        P=PAL; F=FONT_FAMILY
        self.root.configure(bg=P['bg'])
        style=ttk.Style(self.root); style.theme_use('clam')
        style.configure('.',background=P['bg'],foreground=P['text'],font=(F,10),bordercolor=P['line'],lightcolor=P['line'],darkcolor=P['line'],troughcolor=P['raised'],focuscolor=P['bg'],selectbackground=P['accent_dim'],selectforeground='#ffffff')
        # Surfaces: one content colour everywhere so labels never show a different-coloured patch behind them.
        style.configure('TFrame',background=P['bg']); style.configure('Panel.TFrame',background=P['bg']); style.configure('Page.TFrame',background=PAGE_BG); style.configure('Side.TFrame',background=P['ink'])
        style.configure('TLabel',background=P['bg'],foreground=P['text'])
        style.configure('Muted.TLabel',foreground=P['muted'])
        style.configure('Title.TLabel',background=PAGE_BG,font=(F,22,'bold'))
        style.configure('Subtitle.TLabel',background=PAGE_BG,foreground=P['muted'],font=(F,10))
        style.configure('CardTitle.TLabel',font=(F,11,'bold'))
        style.configure('Brand.TLabel',background=P['ink'],font=(F,17,'bold'))
        style.configure('Side.TLabel',background=P['ink'],foreground=P['text'])
        style.configure('SideMuted.TLabel',background=P['ink'],foreground=P['muted'],font=(F,9))
        style.configure('Status.TLabel',background=P['ink'],foreground='#c9cfda',font=(F,10))
        style.configure('TLabelframe',background=P['bg'],bordercolor=P['line'],lightcolor=P['line'],darkcolor=P['line'],relief='solid',borderwidth=1)
        style.configure('TLabelframe.Label',background=P['bg'],foreground=P['text'],font=(F,11,'bold'))
        style.configure('TSeparator',background=P['line'])
        # Buttons: flat fills, no bevel.
        flat=lambda c:dict(background=c,bordercolor=c,lightcolor=c,darkcolor=c,focuscolor=c)
        style.configure('TButton',foreground=P['text'],padding=(16,9),borderwidth=1,relief='flat',anchor='center',**flat(P['raised']))
        style.map('TButton',background=[('disabled',P['bg']),('pressed',P['line']),('active',P['hover'])],foreground=[('disabled',P['faint'])],
                  bordercolor=[('disabled',P['line']),('focus','#566073'),('active',P['hover'])],lightcolor=[('active',P['hover'])],darkcolor=[('active',P['hover'])])
        style.configure('Accent.TButton',foreground='#ffffff',font=(F,10,'bold'),padding=(18,10),**flat(P['accent']))
        style.map('Accent.TButton',background=[('disabled',P['raised']),('pressed',P['accent_lo']),('active',P['accent_hi'])],foreground=[('disabled',P['faint'])],
                  bordercolor=[('disabled',P['raised']),('pressed',P['accent_lo']),('active',P['accent_hi'])],lightcolor=[('pressed',P['accent_lo']),('active',P['accent_hi'])],darkcolor=[('pressed',P['accent_lo']),('active',P['accent_hi'])])
        style.configure('Outline.TButton',foreground=P['muted'],background=P['bg'],bordercolor=P['line'],lightcolor=P['bg'],darkcolor=P['bg'],focuscolor=P['bg'])
        style.map('Outline.TButton',background=[('pressed',P['line']),('active',P['raised'])],foreground=[('active',P['text']),('disabled',P['faint'])],
                  bordercolor=[('active',P['hover'])],lightcolor=[('active',P['raised'])],darkcolor=[('active',P['raised'])])
        nav_hover='#171b22'; nav_on='#1b2029'
        style.configure('Nav.TButton',foreground=P['muted'],anchor='w',padding=(16,11),font=(F,10),**flat(P['ink']))
        style.map('Nav.TButton',background=[('active',nav_hover)],foreground=[('active',P['text'])],bordercolor=[('active',nav_hover)],lightcolor=[('active',nav_hover)],darkcolor=[('active',nav_hover)])
        style.configure('NavActive.TButton',foreground=P['text'],anchor='w',padding=(16,11),font=(F,10,'bold'),**flat(nav_on))
        style.map('NavActive.TButton',background=[('active',nav_on)],bordercolor=[('active',nav_on)],lightcolor=[('active',nav_on)],darkcolor=[('active',nav_on)])
        # Inputs: raised fields with a hairline border that turns accent-coloured when focused.
        for kind in ('TEntry','TCombobox','TSpinbox'):
            style.configure(kind,fieldbackground=P['raised'],background=P['raised'],foreground=P['text'],bordercolor=P['line'],lightcolor=P['line'],darkcolor=P['line'],insertcolor=P['text'],arrowcolor=P['muted'],padding=(8,6),selectbackground=P['accent_dim'],selectforeground='#ffffff')
            style.map(kind,bordercolor=[('focus',P['accent'])],lightcolor=[('focus',P['accent'])],darkcolor=[('focus',P['accent'])],
                      fieldbackground=[('disabled',P['bg']),('readonly',P['raised'])],foreground=[('disabled',P['faint'])],arrowcolor=[('disabled',P['faint'])])
        style.map('TCombobox',selectbackground=[('readonly',P['raised'])],selectforeground=[('readonly',P['text'])],background=[('active',P['hover']),('pressed',P['hover'])])
        style.map('TSpinbox',background=[('active',P['hover']),('pressed',P['hover'])])
        for kind in ('TCheckbutton','TRadiobutton'):
            style.configure(kind,background=P['bg'],foreground=P['text'],indicatorbackground=P['raised'],indicatorforeground='#ffffff',upperbordercolor=P['hover'],lowerbordercolor=P['hover'],indicatormargin=(0,0,8,0),padding=(2,4),focuscolor=P['bg'])
            style.map(kind,background=[('active',P['bg'])],foreground=[('disabled',P['faint'])],indicatorbackground=[('selected',P['accent']),('pressed',P['hover']),('disabled',P['bg'])],
                      upperbordercolor=[('selected',P['accent'])],lowerbordercolor=[('selected',P['accent'])])
        for orient in ('Vertical','Horizontal'):
            style.configure(orient+'.TScrollbar',background=P['hover'],troughcolor=P['bg'],bordercolor=P['bg'],lightcolor=P['hover'],darkcolor=P['hover'],arrowcolor=P['muted'],gripcount=0,relief='flat',arrowsize=14)
            style.map(orient+'.TScrollbar',background=[('active',P['faint']),('pressed',P['faint'])],lightcolor=[('active',P['faint']),('pressed',P['faint'])],darkcolor=[('active',P['faint']),('pressed',P['faint'])])
        style.configure('Horizontal.TScale',background=P['accent'],troughcolor=P['raised'],bordercolor=P['line'],lightcolor=P['accent'],darkcolor=P['accent'],sliderlength=18)
        style.map('Horizontal.TScale',background=[('active',P['accent_hi'])],lightcolor=[('active',P['accent_hi'])],darkcolor=[('active',P['accent_hi'])])
        style.configure('Treeview',background=P['raised'],fieldbackground=P['raised'],foreground=P['text'],bordercolor=P['line'],lightcolor=P['raised'],darkcolor=P['raised'],rowheight=30,borderwidth=0,font=(F,10))
        style.configure('Treeview.Heading',background=P['hover'],foreground=P['muted'],bordercolor=P['hover'],lightcolor=P['hover'],darkcolor=P['hover'],relief='flat',padding=(10,8),font=(F,9,'bold'))
        style.map('Treeview',background=[('selected',P['accent_dim'])],foreground=[('selected','#ffffff')])
        style.map('Treeview.Heading',background=[('active',P['line'])])
        # Classic Tk widgets (combobox dropdown, listboxes) are themed through the option database.
        r=self.root
        for pattern in ('*TCombobox*Listbox','*Listbox'):
            r.option_add(pattern+'.background',P['raised']); r.option_add(pattern+'.foreground',P['text'])
            r.option_add(pattern+'.selectBackground',P['accent'] if 'TCombobox' in pattern else P['accent_dim']); r.option_add(pattern+'.selectForeground','#ffffff')
            r.option_add(pattern+'.highlightThickness',0); r.option_add(pattern+'.borderWidth',0); r.option_add(pattern+'.relief','flat')
    def _build_shell(self):
        P=PAL
        shell=tk.Frame(self.root,bg=P['bg']); shell.pack(fill='both',expand=True)
        side=ttk.Frame(shell,style='Side.TFrame',width=232); side.pack(side='left',fill='y'); side.pack_propagate(False)
        tk.Frame(shell,bg=P['line'],width=1).pack(side='left',fill='y')
        ttk.Label(side,text='ChatVoice Local',style='Brand.TLabel').pack(anchor='w',padx=22,pady=(26,2))
        ttk.Label(side,text='Reads Twitch and Kick chat aloud',style='SideMuted.TLabel',wraplength=190).pack(anchor='w',padx=22,pady=(0,26))
        self.pages={}; self.nav_buttons={}; self.nav_marks={}
        content=ttk.Frame(shell,style='Page.TFrame'); content.pack(side='left',fill='both',expand=True)
        self.status=tk.StringVar(value='Ready. Start with Login & Chat.')
        statusbar=ttk.Frame(content,style='Side.TFrame'); statusbar.pack(fill='x',side='bottom')
        ttk.Label(statusbar,textvariable=self.status,style='Status.TLabel',wraplength=860,justify='left').pack(anchor='w',padx=40,pady=11)
        tk.Frame(content,bg=P['line'],height=1).pack(fill='x',side='bottom')
        self.page_host=ttk.Frame(content,style='Page.TFrame',padding=(40,32,40,14)); self.page_host.pack(fill='both',expand=True)
        HOVER='#171b22'
        foot=tk.Frame(side,bg=P['ink']); foot.pack(side='bottom',fill='x',pady=(0,18))
        tk.Frame(side,bg=P['line'],height=1).pack(side='bottom',fill='x',padx=18,pady=(0,10))
        # Header: a real nav item (same look, same left edge) that opens the Login & Chat page.
        hrow=tk.Frame(foot,bg=P['ink']); hrow.pack(fill='x',pady=1)
        hmark=tk.Frame(hrow,bg=P['ink'],width=3); hmark.pack(side='left',fill='y')
        hbtn=ttk.Button(hrow,text='Chat connections',style='Nav.TButton',command=lambda:self.show_page('Login & Chat'),cursor='hand2'); hbtn.pack(side='left',fill='x',expand=True,padx=(7,10))
        self.nav_buttons['Login & Chat']=hbtn; self.nav_marks['Login & Chat']=hmark
        self.side_dots={}; self.side_rows={}; self.side_x={}; self.side_names={}
        for platform in ('Twitch','Kick'):
            outer=tk.Frame(foot,bg=P['ink']); outer.pack(fill='x',pady=1)
            tk.Frame(outer,bg=P['ink'],width=3).pack(side='left',fill='y')
            line=tk.Frame(outer,bg=P['ink'],cursor='hand2'); line.pack(side='left',fill='x',expand=True,padx=(7,10))
            dot=tk.Label(line,text='●',bg=P['ink'],fg=P['idle'],font=(FONT_FAMILY,10),cursor='hand2'); dot.pack(side='left',padx=(17,0),pady=8)
            name=tk.Label(line,text=platform,bg=P['ink'],fg=P['text'],font=(FONT_FAMILY,10),cursor='hand2'); name.pack(side='left',padx=(9,0))
            # Pink X on the right: only visible while signed in; click = log off.
            x=tk.Label(line,text='✕',bg=P['accent'],fg='#ffffff',font=(FONT_FAMILY,9,'bold'),padx=8,pady=2,cursor='hand2')
            x.bind('<Button-1>',lambda e,pl=platform:self._side_logout(pl))
            x.bind('<Enter>',lambda e,w=x:w.configure(bg=P['accent_hi'])); x.bind('<Leave>',lambda e,w=x:w.configure(bg=P['accent']))
            for w in (line,dot,name):
                w.bind('<Button-1>',lambda e,pl=platform:self._side_connect(pl))
                w.bind('<Enter>',lambda e,l=line,d=dot,n=name:[o.configure(bg=HOVER) for o in (l,d,n)])
                w.bind('<Leave>',lambda e,l=line,d=dot,n=name:[o.configure(bg=P['ink']) for o in (l,d,n)])
            self.side_dots[platform]=dot; self.side_rows[platform]=line; self.side_x[platform]=x; self.side_names[platform]=name
        self.pages['Login & Chat']=ttk.Frame(self.page_host,style='Page.TFrame')
        names=['Dashboard','Activity','Chat','Voice','Advanced Filter','Blocked users']
        labels={'Chat':'Chat setup','Voice':'Voice setup'}
        for name in names:
            row=tk.Frame(side,bg=P['ink']); row.pack(fill='x',pady=1)
            mark=tk.Frame(row,bg=P['ink'],width=3); mark.pack(side='left',fill='y')
            button=ttk.Button(row,text=labels.get(name,name),style='Nav.TButton',command=lambda n=name:self.show_page(n),cursor='hand2'); button.pack(side='left',fill='x',expand=True,padx=(7,10))
            self.nav_buttons[name]=button; self.nav_marks[name]=mark
            self.pages[name]=ttk.Frame(self.page_host,style='Page.TFrame')
    def _page_header(self,parent,title,description,info=False):
        if info and not getattr(self,'_tab_mode',False):  # short header: the description appears when you hover over the title or the ⓘ
            head=tk.Frame(parent,bg=PAGE_BG); head.pack(anchor='w',pady=(0,20))
            label=ttk.Label(head,text=title,style='Title.TLabel'); label.pack(side='left')
            mark=tk.Label(head,text='ⓘ',bg=PAGE_BG,fg=PAL['faint'],font=(FONT_FAMILY,13)); mark.pack(side='left',padx=(10,0),pady=(6,0))
            for widget in (label,mark): dashboard.HoverTip(widget,description,PAL,FONT_FAMILY)
            return
        if getattr(self,'_tab_mode',False):  # inside a Chat tab: the tab already names the section, keep only the description
            ttk.Label(parent,text=description,style='Subtitle.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(0,18)); return
        ttk.Label(parent,text=title,style='Title.TLabel').pack(anchor='w')
        ttk.Label(parent,text=description,style='Subtitle.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(6,22))
    def _card(self,parent,title,mark=None,nested=False):
        """Section label above a bordered card. nested=True is for use inside another card (label only, no second box)."""
        bg=PAL['bg'] if nested else PAGE_BG
        head=tk.Frame(parent,bg=bg); head.pack(fill='x',anchor='n',pady=(0,8))
        if mark: tk.Label(head,text='●',bg=bg,fg=mark,font=(FONT_FAMILY,10)).pack(side='left',padx=(0,6))
        tk.Label(head,text=title.upper(),bg=bg,fg=PAL['muted'] if mark else PAL['faint'],font=(FONT_FAMILY,9,'bold')).pack(side='left')
        if nested:
            inner=ttk.Frame(parent,style='Panel.TFrame',padding=(0,0,0,14)); inner.pack(fill='x',anchor='n'); inner._outer=inner
            return inner
        box=tk.Frame(parent,bg=PAL['bg'],highlightbackground=PAL['line'],highlightthickness=1); box.pack(fill='x',anchor='n',pady=(0,24))
        inner=ttk.Frame(box,style='Panel.TFrame',padding=22); inner.pack(fill='both',expand=True)
        inner._outer=box
        return inner
    def _build_connection_page(self,page):
        """One simple row per platform: name, status, Login / Log out. To add TikTok or YouTube later, add one entry to `accounts`
        (plus a branch in _signed_in) and the row, the sidebar status and the refresh logic follow."""
        self._page_header(page,'Login & Chat','Log in to the platforms you want to read. A green dot means that platform’s chat is connected.',info=True)
        self.connection_state=tk.StringVar(value='Twitch: Login required')
        self.kick_connection_state=tk.StringVar(value='Kick: Not connected')
        self.kick_account_state=tk.StringVar(value='Not signed in (guest chat reading)')
        self.kick_channel=tk.StringVar(value=self.settings.get('kick_channel',''))
        self.accounts=[
            {'name':'Twitch','state':self.connection_state,'account':None,'login':self.login_twitch,'reconnect':self.reconnect_twitch_chat},
            {'name':'Kick','state':self.kick_connection_state,'account':self.kick_account_state,'login':self.login_kick,'reconnect':self.reconnect_kick_chat},
        ]
        for acc in self.accounts:
            name=acc['name']
            box=tk.Frame(page,bg=PAL['bg'],highlightbackground=PAL['line'],highlightthickness=1); box.pack(fill='x',pady=(0,12))
            inner=ttk.Frame(box,style='Panel.TFrame',padding=(20,16)); inner.pack(fill='x')
            left=ttk.Frame(inner,style='Panel.TFrame'); left.pack(side='left',fill='x',expand=True)
            head=ttk.Frame(left,style='Panel.TFrame'); head.pack(anchor='w')
            dot=ttk.Label(head,text='●',foreground=PAL['idle'],font=(FONT_FAMILY,13)); dot.pack(side='left',padx=(0,8))
            setattr(self,name.lower()+'_presence_dot',dot)
            ttk.Label(head,text=name+' account',font=(FONT_FAMILY,13,'bold')).pack(side='left')
            ttk.Label(left,textvariable=acc['state'],style='Muted.TLabel').pack(anchor='w',pady=(4,0))
            if acc['account'] is not None: ttk.Label(left,textvariable=acc['account'],style='Muted.TLabel').pack(anchor='w')
            right=ttk.Frame(inner,style='Panel.TFrame'); right.pack(side='right')
            acc['reconnect_button']=ttk.Button(right,text='Reconnect',style='Outline.TButton',command=acc['reconnect'])
            acc['main_button']=ttk.Button(right,text='Login',style='Accent.TButton',width=11,command=acc['login']); acc['main_button'].pack(side='right',padx=(8,0))
            if name=='Kick':
                # Reading someone else's public Kick chat without logging in stays available, tucked away.
                guest=ttk.Frame(left,style='Panel.TFrame'); open_link=tk.Label(left,text='Read another Kick channel without logging in',bg=PAL['bg'],fg=PAL['muted'],font=(FONT_FAMILY,9,'underline'),cursor='hand2'); open_link.pack(anchor='w',pady=(10,0))
                ttk.Label(guest,text='Kick channel').pack(side='left')
                ttk.Entry(guest,textvariable=self.kick_channel,width=24).pack(side='left',padx=10)
                self.kick_login_button=ttk.Button(guest,text='Connect Kick chat',style='Outline.TButton',command=self.connect_kick); self.kick_login_button.pack(side='left')
                open_link.bind('<Button-1>',lambda _e,g=guest:(g.pack_forget() if g.winfo_ismapped() else g.pack(anchor='w',pady=(8,0))))
        self.twitch_reconnect_button=self.accounts[0]['reconnect_button']; self.kick_reconnect_button=self.accounts[1]['reconnect_button']
        self._refresh_account_rows()
    def _refresh_account_rows(self):
        """Login becomes Log out (and Reconnect appears) while that account is signed in."""
        for acc in getattr(self,'accounts',[]):
            try:
                signed=self._signed_in(acc['name'])
                if signed:
                    acc['main_button'].configure(text='Log out',style='Outline.TButton',command=lambda n=acc['name']:self._side_logout(n))
                    acc['reconnect_button'].pack(side='right',after=acc['main_button'])
                else:
                    acc['main_button'].configure(text='Login',style='Accent.TButton',command=acc['login'])
                    acc['reconnect_button'].pack_forget()
            except Exception: pass
    def _signed_in(self,platform):
        if platform=='Twitch': return bool(self.twitch_token or self.twitch_refresh_token) and bool(self.twitch_user.get().strip() or self.twitch_user_id)
        return bool(self.kick_tokens)
    def _refresh_side_connections(self):
        """Show the pink X next to a platform in the sidebar only while that account is signed in."""
        self._refresh_account_rows()
        for platform,x in getattr(self,'side_x',{}).items():
            try:
                if self._signed_in(platform): x.pack(side='right',pady=4)
                else: x.pack_forget()
            except Exception: pass
    def _side_connect(self,platform):
        """Sidebar click: sign in if needed (opens the Twitch/Kick authorization page), otherwise show the Login & Chat page."""
        if platform=='Twitch':
            if self._signed_in('Twitch'):
                self.show_page('Login & Chat')
                if not getattr(self,'twitch_chat_connected',False): self.reconnect_twitch_chat()
            else: self.login_twitch()
        else:
            if self._signed_in('Kick'):
                self.show_page('Login & Chat')
                if not getattr(self,'kick_chat_connected',False): self.connect_kick()
            else: self.login_kick()
    def _side_logout(self,platform):
        if platform=='Twitch': self.logout_twitch()
        else:
            if not messagebox.askyesno('Log out of Kick','Log out '+(self.kick_slug or 'the Kick account')+' and stop reading its chat?'): return
            self.logout_kick()
            if self.kick_reader: self.kick_reader.stop(); self.kick_reader=None
            self.kick_connection_state.set('Kick: Not connected'); self._set_platform_presence('Kick',False)
            self.kick_login_button.configure(text='Connect Kick chat')
        self._refresh_side_connections()
    def _set_platform_presence(self,platform,connected):
        connected=bool(connected); color=PAL['ok'] if connected else PAL['idle']
        if platform=='Twitch':
            self.twitch_chat_connected=connected
            if hasattr(self,'twitch_presence_dot'): self.twitch_presence_dot.configure(foreground=color)
        else:
            self.kick_chat_connected=connected
            if hasattr(self,'kick_presence_dot'): self.kick_presence_dot.configure(foreground=color)
        dots=getattr(self,'side_dots',None)
        if dots and platform in dots: dots[platform].configure(fg=color)
        self._refresh_side_connections()
    def _build_tts_page(self,page):
        self._page_header(page,'Text to Speech','Choose one voice, separate Twitch and Kick voices, or assign one of five voices to each chatter.',info=True)
        card=self._card(page,'Voice and playback')
        card._outer.pack_configure(fill='both',expand=True); card.pack_configure(fill='both',expand=True)
        canvas=tk.Canvas(card,bg=PAL['bg'],highlightthickness=0)
        scrollbar=ttk.Scrollbar(card,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True)
        content=ttk.Frame(canvas,style='Panel.TFrame')
        content_window=canvas.create_window((0,0),window=content,anchor='nw')
        def update_tts_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            bounds=canvas.bbox('all')
            needs_scroll=bool(bounds and bounds[3]-bounds[1]>canvas.winfo_height())
            if needs_scroll and not scrollbar.winfo_manager(): scrollbar.pack(side='right',fill='y')
            elif not needs_scroll and scrollbar.winfo_manager(): scrollbar.pack_forget()
        content.bind('<Configure>',update_tts_scroll)
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(content_window,width=event.width))
        def scroll_tts(event):
            try: target=canvas.winfo_containing(event.x_root,event.y_root)
            except (KeyError,tk.TclError): return  # an open dropdown list can't be resolved by Tk
            if target and (target==canvas or str(target).startswith(str(content))):
                canvas.yview_scroll(int(-event.delta/120),'units')
        canvas.bind_all('<MouseWheel>',scroll_tts)
        self.voice=tk.StringVar(value=normalize_voice_label(self.settings.get('tts_voice','American English — Heart (af_heart)'))); self.speed=tk.DoubleVar(value=1.0); self.volume=tk.DoubleVar(value=.8)
        voices=list(KOKORO_VOICE_INFO); self.builtin_voices=list(voices)
        self.windows_sapi_voices=self.engine.list_windows_voices()
        self.builtin_voices.extend(self.engine.WINDOWS_SAPI_PREFIX+name for name in self.windows_sapi_voices)
        self.tts_voice_box=None
        saved_twitch_voice=normalize_voice_label(self.settings.get('twitch_tts_voice',self.voice.get()))
        saved_kick_voice=normalize_voice_label(self.settings.get('kick_tts_voice',self.voice.get()))
        self.twitch_tts_voice=tk.StringVar(value=saved_twitch_voice if isinstance(saved_twitch_voice,str) else self.voice.get())
        self.kick_tts_voice=tk.StringVar(value=saved_kick_voice if isinstance(saved_kick_voice,str) else self.voice.get())
        saved_mode=self.settings.get('tts_voice_mode')
        if saved_mode not in VOICE_MODES:
            saved_mode='five' if self.settings.get('chat_voice_mode',False) else 'platform'
        self.tts_voice_mode=tk.StringVar(value=saved_mode)
        self.chat_voice_enabled=tk.BooleanVar(value=saved_mode=='five')
        mode_bar=tk.Frame(content,bg=PAL['raised'],highlightbackground=PAL['line'],highlightthickness=1)
        mode_bar.grid(row=0,column=0,columnspan=2,sticky='w',padx=12,pady=(2,14))
        self.tts_mode_buttons={}
        for mode,title in (('single','Single voice'),('platform','Twitch + Kick'),('five','Five voices'),('random','Random voices')):
            button=tk.Button(mode_bar,text=title,command=lambda selected=mode:self._select_tts_voice_mode(selected),relief='flat',bd=0,width=16,pady=8,fg='white',activeforeground='white',font=(FONT_FAMILY,10,'bold'),cursor='hand2',highlightthickness=0)
            button.pack(side='left',padx=3,pady=3)
            self.tts_mode_buttons[mode]=button
        self.tts_voice_rows={}
        for row,label_text in ((1,'Voice'),(2,'Twitch chat voice'),(3,'Kick chat voice')):
            row_frame=ttk.Frame(content,style='Panel.TFrame')
            ttk.Label(row_frame,text=label_text,width=19).pack(side='left')
            if row==1:
                box=ttk.Combobox(row_frame,textvariable=self.voice,values=self.builtin_voices,state='readonly',width=46)
                box.bind('<<ComboboxSelected>>',lambda _event:self._save_single_voice_setting())
                self.tts_voice_box=box
            else:
                var=self.twitch_tts_voice if row==2 else self.kick_tts_voice
                box=ttk.Combobox(row_frame,textvariable=var,values=self.builtin_voices,state='readonly',width=46)
                box.bind('<<ComboboxSelected>>',lambda _event:self._save_platform_voice_settings())
                if row==2: self.twitch_voice_box=box
                else: self.kick_voice_box=box
            box.pack(side='left',padx=12,pady=5)
            row_frame.grid(row=row,column=0,columnspan=2,sticky='w',padx=12,pady=2)
            self.tts_voice_rows[row]=row_frame
        self.five_voice_panel=ttk.Frame(content,style='Panel.TFrame')
        self.five_voice_panel.grid(row=4,column=0,columnspan=2,sticky='nsew',padx=4,pady=(3,6))
        self.random_last_voice=None
        self.random_group_vars={}
        self.random_voice_panel=ttk.Frame(content,style='Panel.TFrame')
        self.random_voice_panel.grid(row=4,column=0,columnspan=2,sticky='nsew',padx=4,pady=(3,6))
        ttk.Label(self.random_voice_panel,text='Every message is read by a different random voice, never the same one twice in a row. Choose which voices may be picked. Voices for other languages read English chat poorly, so leave them off unless your chat uses that language.',style='Muted.TLabel',wraplength=680).grid(row=0,column=0,columnspan=2,sticky='w',padx=8,pady=(0,8))
        saved_groups=self.settings.get('random_voice_groups')
        chosen_groups=set(saved_groups) if isinstance(saved_groups,list) else set(RANDOM_DEFAULT_GROUPS)
        for index,(key,text) in enumerate(self._random_group_options()):
            var=tk.BooleanVar(value=key in chosen_groups)
            cell=tk.Frame(self.random_voice_panel,bg=PAL['bg']); cell.grid(row=1+index//2,column=index%2,sticky='w',padx=8,pady=6)
            ToggleSwitch(cell,var,bg=PAL['bg']).pack(side='left')
            cell_label=tk.Label(cell,text=text,bg=PAL['bg'],fg=PAL['text'],font=(FONT_FAMILY,10),cursor='hand2'); cell_label.pack(side='left',padx=(10,0))
            cell_label.bind('<Button-1>',lambda _e,v=var:v.set(not v.get()))
            var.trace_add('write',lambda *_a:self._save_random_voice_groups())
            self.random_group_vars[key]=var
        self.random_pool_text=tk.StringVar()
        ttk.Label(self.random_voice_panel,textvariable=self.random_pool_text,style='Muted.TLabel',wraplength=680).grid(row=99,column=0,columnspan=2,sticky='w',padx=8,pady=(8,0))
        self._update_random_pool_text()
        ttk.Label(content,text='Speed').grid(row=5,column=0,sticky='w'); ttk.Scale(content,from_=.7,to=1.3,variable=self.speed,length=300).grid(row=5,column=1,sticky='w',padx=12,pady=8)
        ttk.Label(content,text='Volume').grid(row=6,column=0,sticky='w'); ttk.Scale(content,from_=0,to=1,variable=self.volume,length=300).grid(row=6,column=1,sticky='w',padx=12,pady=8)
        ttk.Button(content,text='Test voice',style='Accent.TButton',command=self.test_voice).grid(row=7,column=1,sticky='w',padx=12,pady=(18,0))
        tk.Frame(content,bg=PAL['line'],height=1).grid(row=8,column=0,columnspan=2,sticky='ew',padx=12,pady=(22,16))
        tk.Label(content,text='SHORTCUTS',bg=PAL['bg'],fg=PAL['faint'],font=(FONT_FAMILY,9,'bold')).grid(row=9,column=0,columnspan=2,sticky='w',padx=12,pady=(0,8))
        self.shortcut_state_labels={}
        shortcut_list=tk.Frame(content,bg=PAL['bg']); shortcut_list.grid(row=10,column=0,columnspan=2,sticky='ew',padx=12)
        shortcut_list.columnconfigure(2,weight=1)
        for index,(keys,text,state_key) in enumerate((('Ctrl + 7','Clear the speech queue',None),('Ctrl + 8','Pause / resume TTS','pause'),('Ctrl + 9','Mute / unmute the sound','mute'))):
            tk.Label(shortcut_list,text=keys,bg=PAL['raised'],fg=PAL['text'],font=(FONT_FAMILY,9,'bold'),padx=10,pady=4,width=8).grid(row=index,column=0,sticky='w',pady=4)
            tk.Label(shortcut_list,text=text,bg=PAL['bg'],fg=PAL['text'],font=(FONT_FAMILY,10)).grid(row=index,column=1,sticky='w',padx=(14,0),pady=4)
            state=tk.Label(shortcut_list,text='',bg=PAL['bg'],fg=PAL['accent'],font=(FONT_FAMILY,9,'bold')); state.grid(row=index,column=2,sticky='w',padx=(14,0))
            if state_key: self.shortcut_state_labels[state_key]=state
        tk.Label(content,text='These work from anywhere in Windows, even while this window is in the background. Pausing and muting keep Twitch and Kick chat connected.',bg=PAL['bg'],fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=680).grid(row=11,column=0,columnspan=2,sticky='w',padx=12,pady=(10,0))
        self._refresh_tts_voice_mode()
    def _refresh_tts_voice_mode(self):
        mode=self.tts_voice_mode.get()
        for row in self.tts_voice_rows.values(): row.grid_remove()
        if mode=='single': self.tts_voice_rows[1].grid()
        elif mode=='platform':
            self.tts_voice_rows[2].grid(); self.tts_voice_rows[3].grid()
        for name,button in self.tts_mode_buttons.items():
            active=name==mode
            button.configure(bg=PAL['accent'] if active else PAL['raised'],activebackground=PAL['accent_hi'] if active else PAL['hover'],fg='#ffffff' if active else PAL['muted'],activeforeground='#ffffff')
        if mode=='five': self.five_voice_panel.grid()
        else: self.five_voice_panel.grid_remove()
        if mode=='random': self.random_voice_panel.grid()
        else: self.random_voice_panel.grid_remove()
    def _set_app_icon(self):
        # Window and taskbar icon: icon.ico on Windows, icon.png elsewhere. A missing file just keeps the default icon.
        ico=os.path.join(APP_DIR,'icon.ico'); png=os.path.join(APP_DIR,'icon.png')
        try:
            if sys.platform=='win32' and os.path.isfile(ico): self.root.iconbitmap(default=ico)
            elif os.path.isfile(png):
                self.icon_image=tk.PhotoImage(file=png); self.root.iconphoto(True,self.icon_image)
        except tk.TclError: pass
    def _random_group_options(self):
        counts={}
        for label in KOKORO_VOICE_INFO:
            key=label.split(' — ',1)[0]; counts[key]=counts.get(key,0)+1
        options=[(key,f'{key} ({count} voices)'+(' – downloads an extra model' if key.endswith('v1.1') else '')) for key,count in counts.items()]
        if self.windows_sapi_voices: options.append((RANDOM_SAPI_GROUP,f'{RANDOM_SAPI_GROUP} installed on this PC ({len(self.windows_sapi_voices)})'))
        options.append((RANDOM_MIX_GROUP,RANDOM_MIX_GROUP))
        return options
    def _random_voice_pool(self):
        groups={key for key,var in self.random_group_vars.items() if var.get()}
        pool=[label for label in KOKORO_VOICE_INFO if label.split(' — ',1)[0] in groups]
        if RANDOM_SAPI_GROUP in groups: pool.extend(self.engine.WINDOWS_SAPI_PREFIX+name for name in self.windows_sapi_voices)
        if RANDOM_MIX_GROUP in groups and hasattr(self,'voice_catalog'):
            pool.extend(label for label in self.voice_catalog if label.startswith(('Blend: ','Saved mix: ','Custom: ')))
        return pool
    def _pick_random_voice(self):
        pool=self._random_voice_pool()
        if not pool: return self.voice.get()
        choice=random.choice([voice for voice in pool if voice!=self.random_last_voice] or pool)
        self.random_last_voice=choice
        return choice
    def _update_random_pool_text(self):
        count=len(self._random_voice_pool())
        self.random_pool_text.set(f'{count} voices are in the random pool.' if count else 'Nothing is selected, so the voice from Single voice is used instead.')
    def _save_random_voice_groups(self):
        self.settings['random_voice_groups']=[key for key,var in self.random_group_vars.items() if var.get()]
        self._save_connection_settings(); self._update_random_pool_text()
    def _save_single_voice_setting(self):
        self.settings['tts_voice']=self.voice.get()
        self._save_connection_settings()
    def _select_tts_voice_mode(self,mode):
        if mode not in VOICE_MODES: return
        self.tts_voice_mode.set(mode)
        self.chat_voice_enabled.set(mode=='five')
        self.settings['tts_voice_mode']=mode
        self.settings['chat_voice_mode']=mode=='five'
        if mode=='five':
            for key in self.chat_voice_users:
                self.user_voice_assignments.setdefault(key,random.randrange(5))
            self.chat_voice_mode_status.set('Five-voice mode is on. New chatters are assigned randomly.')
        else:
            self.chat_voice_mode_status.set('Five-voice mode is off. Turn it on under Text to Speech.')
        self._save_chat_voice_settings()
        self._refresh_tts_voice_mode()
        self._refresh_chat_voice_users()
    def _save_platform_voice_settings(self):
        self.settings['twitch_tts_voice']=self.twitch_tts_voice.get()
        self.settings['kick_tts_voice']=self.kick_tts_voice.get()
        self._save_connection_settings()
    def _build_chat_voices_page(self,page):
        ttk.Label(page,text='Choose five voices and manage chatter assignments. In chat, viewers can choose their own slot by typing !Voice1, !Voice2, !Voice3, !Voice4, or !Voice5.',style='Muted.TLabel',wraplength=800,justify='left').pack(anchor='w',pady=(0,16))
        slots=self._card(page,'Choose your five voices',nested=True)
        ttk.Label(slots,text='You can choose the same voice more than once. Existing chatter assignments stay tied to a slot if you change that slot’s voice.',style='Muted.TLabel',wraplength=800).pack(anchor='w',pady=(0,10))
        self.chat_voice_slot_vars=[]; self.chat_voice_boxes=[]
        all_voices=self.builtin_voices+list(self.voice_catalog)
        for index in range(5):
            row=ttk.Frame(slots,style='Panel.TFrame'); row.pack(fill='x',pady=4)
            ttk.Label(row,text=f'Voice {index+1}',width=12).pack(side='left')
            var=tk.StringVar(value=self.chat_voice_slots[index]); self.chat_voice_slot_vars.append(var)
            box=ttk.Combobox(row,textvariable=var,values=all_voices,state='readonly',width=52); box.pack(side='left',padx=8)
            box.bind('<<ComboboxSelected>>',lambda _event,i=index:self._save_chat_voice_slot(i))
            self.chat_voice_boxes.append(box)
        self.chat_voice_mode_status=tk.StringVar(value='Five-voice mode is on.' if self.chat_voice_enabled.get() else 'Five-voice mode is off. Turn it on under Text to Speech.')
        ttk.Label(slots,textvariable=self.chat_voice_mode_status,style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        people=self._card(page,'Assign a voice to a chatter',nested=True)
        ttk.Label(people,text='New chatters get a random voice the first time they speak. Select someone here to change their assigned slot.',style='Muted.TLabel',wraplength=800).pack(anchor='w',pady=(0,9))
        search_row=ttk.Frame(people,style='Panel.TFrame'); search_row.pack(fill='x',pady=(0,8))
        ttk.Label(search_row,text='Search').pack(side='left')
        self.chat_voice_search=tk.StringVar(); search=ttk.Entry(search_row,textvariable=self.chat_voice_search,width=32); search.pack(side='left',padx=8)
        search.bind('<KeyRelease>',self._refresh_chat_voice_users)
        self.chat_voice_tree=ttk.Treeview(people,columns=('platform','username','voice'),show='headings',height=10,selectmode='browse')
        for column,title,width in (('platform','Platform',100),('username','Chatter',220),('voice','Assigned voice',410)):
            self.chat_voice_tree.heading(column,text=title); self.chat_voice_tree.column(column,width=width,anchor='w')
        self.chat_voice_tree.pack(fill='both',expand=True,pady=(0,8))
        self.chat_voice_tree.bind('<<TreeviewSelect>>',self._select_chat_voice_user)
        assign_row=ttk.Frame(people,style='Panel.TFrame'); assign_row.pack(fill='x')
        ttk.Label(assign_row,text='Assign selected chatter to').pack(side='left')
        self.chat_voice_assignment=tk.StringVar(value='Voice 1')
        self.chat_voice_assignment_box=ttk.Combobox(assign_row,textvariable=self.chat_voice_assignment,state='readonly',width=56,values=[f'Voice {i+1} — {self.chat_voice_slots[i]}' for i in range(5)])
        self.chat_voice_assignment_box.pack(side='left',padx=8)
        ttk.Button(assign_row,text='Assign voice',command=self.assign_chat_voice).pack(side='left')
        self._refresh_chat_voice_users()
    def _save_chat_voice_slot(self,index):
        self.chat_voice_slots[index]=self.chat_voice_slot_vars[index].get()
        self._save_chat_voice_settings()
        self._refresh_chat_voice_users()
        self._warm_up_voices()
    def _save_chat_voice_settings(self):
        self.settings['chat_voice_slots']=list(self.chat_voice_slots)
        self.settings['user_voice_assignments']=dict(self.user_voice_assignments)
        self.settings['chat_voice_mode']=bool(self.chat_voice_enabled.get())
        self._save_connection_settings()
    def _toggle_chat_voice_mode(self):
        self._select_tts_voice_mode('five' if self.chat_voice_enabled.get() else 'platform')
    def _chat_voice_key(self,platform,username):
        return str(platform).title()+'|'+self._normalize_username(username)
    def _note_chat_user(self,platform,username):
        name=str(username or '').strip()
        if not name: return None
        key=self._chat_voice_key(platform,name)
        is_new=key not in self.chat_voice_users
        self.chat_voice_users[key]=(str(platform).title(),name)
        assigned=False
        if self.chat_voice_enabled.get() and key not in self.user_voice_assignments:
            self.user_voice_assignments[key]=random.randrange(5); assigned=True
            self._save_chat_voice_settings()
        if is_new or assigned: self._refresh_chat_voice_users()
        return self.user_voice_assignments.get(key) if self.chat_voice_enabled.get() else None
    # ---- Paid voice codes -------------------------------------------------------------------------------------------
    def _load_paid_voice_access(self):
        raw=self.settings.get('paid_voice_access',{}); result={}
        if isinstance(raw,dict):
            for key,item in raw.items():
                try: result[str(key)]={'platform':str(item.get('platform','')).title(),'name':str(item.get('name','')),'expires':float(item.get('expires',0)),'reminded':float(item.get('reminded',0) or 0)}
                except (AttributeError,TypeError,ValueError): continue
        return result
    def _save_paid_access(self):
        cutoff=time.time()-30*86400
        self.paid_voice_access={k:v for k,v in self.paid_voice_access.items() if v.get('expires',0)>cutoff}
        self.settings['paid_voice_access']=dict(self.paid_voice_access); self._save_connection_settings()
    @staticmethod
    def _paid_left_text(seconds):
        seconds=max(0,int(seconds)+60); days,rest=divmod(seconds,86400); hours,rest=divmod(rest,3600); minutes=rest//60
        if days: return f'{days} d'+(f' {hours} h' if hours else '')
        if hours: return f'{hours} h'+(f' {minutes} min' if minutes else '')
        return f'{max(1,minutes)} min'
    def _paid_int(self,var,name,default,lo,hi):
        try: return max(lo,min(hi,int(var.get())))
        except (ValueError,tk.TclError):
            try: return max(lo,min(hi,int(self.settings.get(name,default))))
            except (TypeError,ValueError): return default
    def _paid_price(self,unit,badges=None):
        if badges is not None and self.paid_sub_mode.get()=='less' and self._is_sub(badges):
            if unit=='kicks': return self._paid_int(self.paid_sub_kicks,'paid_kicks_per_30_subs',25,1,1000000)
            return self._paid_int(self.paid_sub_bits,'paid_bits_per_30_subs',25,1,1000000)
        name='paid_kicks_per_30' if unit=='kicks' else 'paid_bits_per_30'
        var=self.paid_kicks if unit=='kicks' else self.paid_bits
        try: return max(1,min(1000000,int(var.get())))
        except (ValueError,tk.TclError):
            try: return max(1,int(self.settings.get(name,50)))
            except (TypeError,ValueError): return 50
    def _paid_keys(self,platform,user,user_id=None):
        platform=str(platform).title(); keys=[]
        if user_id: keys.append(platform+'|id:'+str(user_id))
        name=self._normalize_username(user)
        if name: keys.append(platform+'|name:'+name)
        return keys
    def _is_streamer(self,platform,user):
        """The channel owner never needs to pay for voice codes."""
        platform=str(platform).title(); name=self._normalize_username(user).replace('-','_')
        if not name: return False
        if platform=='Twitch': own=[self.settings.get('twitch_channel',''),self.settings.get('twitch_login','')]
        else: own=[self.settings.get('kick_channel',''),getattr(self,'kick_slug','')]
        return name in {self._normalize_username(item).replace('-','_') for item in own if item}
    def _paid_expiry(self,platform,user,user_id=None):
        return max([self.paid_voice_access.get(key,{}).get('expires',0) for key in self._paid_keys(platform,user,user_id)]+[0])
    PAID_SUB_LABELS={'pay':'Pay like everyone','free':'Free','less':'Pay less (own price)'}
    PAID_TRIAL_LABELS={'off':'No free trial','messages':'First messages','minutes':'First minutes'}
    @staticmethod
    def _is_sub(badges):
        return bool({str(b).lower() for b in (badges or [])}&{'subscriber','founder'})
    def _apply_paid_extra_settings(self,snapshot=None):
        s=dict(snapshot if snapshot is not None else self.settings)
        def num(name,default,lo,hi):
            try: return max(lo,min(hi,int(s.get(name,default))))
            except (TypeError,ValueError): return default
        mode=s.get('paid_sub_mode'); self.paid_sub_mode.set(mode if mode in self.PAID_SUB_LABELS else 'pay')
        self.paid_sub_bits.set(num('paid_bits_per_30_subs',25,1,1000000)); self.paid_sub_kicks.set(num('paid_kicks_per_30_subs',25,1,1000000))
        self.paid_min_bits.set(num('paid_min_bits',10,1,1000000)); self.paid_min_kicks.set(num('paid_min_kicks',1,1,1000000))
        self.paid_max_days.set(num('paid_max_days',365,1,3650))
        mode=s.get('paid_trial_mode'); self.paid_trial_mode.set(mode if mode in self.PAID_TRIAL_LABELS else 'off')
        self.paid_trial_amount.set(num('paid_trial_amount',5,1,1000))
        self.paid_remind.set(bool(s.get('paid_remind',False))); self.paid_remind_days.set(num('paid_remind_days',3,1,30))
        try: self.paid_free_until=float(s.get('paid_free_until',0) or 0)
        except (TypeError,ValueError): self.paid_free_until=0.0
        if self.paid_free_until<=time.time(): self.paid_free_until=0.0
        self.paid_trials=self._load_paid_trials(s); self.paid_history=self._load_paid_history(s)
        if hasattr(self,'paid_sub_label'): self.paid_sub_label.set(self.PAID_SUB_LABELS[self.paid_sub_mode.get()])
        if hasattr(self,'paid_trial_label'):
            self.paid_trial_label.set(self.PAID_TRIAL_LABELS[self.paid_trial_mode.get()])
            self.paid_trial_unit.set({'messages':'messages','minutes':'minutes'}.get(self.paid_trial_mode.get(),''))
        self._update_free_status()
    def _is_duplicate_payment(self,platform,user,amount,unit,message_id=None):
        """True when this cheer / gift was already counted (Twitch can resend events after a reconnect; Kick can repeat a gift)."""
        try: amount=int(amount or 0)
        except (TypeError,ValueError): amount=0
        now=time.time(); seen=getattr(self,'_paid_seen',None)
        if seen is None: seen=self._paid_seen={}
        for old in [k for k,t in seen.items() if now-t>600]: seen.pop(old,None)
        if message_id: key=('id',str(platform).title(),str(message_id))
        else: key=(str(platform).title(),self._normalize_username(user),amount,unit)
        seen_at=seen.get(key)
        duplicate=seen_at is not None and (bool(message_id) or now-seen_at<3)
        if not duplicate: seen[key]=now
        return duplicate
    def _remember_badges(self,platform,user,user_id,badges):
        """Keeps the last badges a viewer chatted with, so a Kick gift (which carries no badges) can still use the subscriber price."""
        if not badges: return
        store=getattr(self,'_badge_memory',None)
        if store is None: store=self._badge_memory={}
        keys=self._paid_keys(platform,user,user_id)
        if not keys: return
        for key in keys: store[key]=list(badges)
        while len(store)>3000: store.pop(next(iter(store)))
    def _recall_badges(self,platform,user,user_id=None):
        store=getattr(self,'_badge_memory',None) or {}
        for key in self._paid_keys(platform,user,user_id):
            if key in store: return store[key]
        return None
    def _free_role(self,badges):
        """The chat role (from the platform's badges) that may use the paid features for free, or None."""
        badges=[str(b).lower() for b in (badges or [])]
        if 'broadcaster' in badges: return 'broadcaster'
        if self.paid_sub_mode.get()=='free' and self._is_sub(badges): return 'subscriber'
        if self.paid_free_mods.get() and 'moderator' in badges: return 'moderator'
        if self.paid_free_vips.get() and 'vip' in badges: return 'VIP'
        return None
    def _has_paid_voices(self,platform,user,user_id=None,badges=None):
        if not self.paid_voices_enabled.get() or self._is_streamer(platform,user) or self._free_role(badges) or self._free_window_left()>0: return True
        return self._paid_expiry(platform,user,user_id)>time.time()
    def _load_paid_history(self,source=None):
        raw=(source if source is not None else self.settings).get('paid_history',[]); result=[]
        if isinstance(raw,list):
            for item in raw[-5000:]:
                try: result.append({'t':float(item['t']),'platform':str(item.get('platform','')),'name':str(item.get('name','')),'source':str(item.get('source','')),'amount':int(item.get('amount',0) or 0),'unit':str(item.get('unit','')),'days':float(item.get('days',0) or 0)})
                except (AttributeError,KeyError,TypeError,ValueError): continue
        return result
    def _log_paid(self,platform,user,source,amount,unit,days):
        """Adds a line to the payment history (kept in the settings file, newest 5000 lines). The caller saves."""
        self.paid_history.append({'t':time.time(),'platform':str(platform).title(),'name':str(user or '').strip(),'source':source,'amount':int(amount or 0),'unit':unit,'days':round(float(days),3)})
        del self.paid_history[:-5000]
        self.settings['paid_history']=list(self.paid_history)
    def _grant_paid_seconds(self,platform,user,user_id,seconds,cap=True,source='manual',amount=0,unit=''):
        keys=self._paid_keys(platform,user,user_id)
        if not keys or seconds<=0: return 0
        now=time.time(); current=self._paid_expiry(platform,user,user_id); start=max(current,now)
        expires=start+seconds
        if cap:
            # Stored time never grows past the cap (time a viewer already has beyond it is left alone).
            limit=now+self._paid_int(self.paid_max_days,'paid_max_days',365,1,3650)*86400
            expires=min(expires,max(limit,current))
        for key in keys[1:]: self.paid_voice_access.pop(key,None)
        self.paid_voice_access[keys[0]]={'platform':str(platform).title(),'name':str(user or '').strip(),'expires':expires,'reminded':0}
        self._log_paid(platform,user,source,amount,unit,max(0,expires-start)/86400)
        self._save_paid_access(); self._refresh_paid_voices()
        return expires
    def _handle_payment(self,platform,user,user_id,amount,unit,badges=None):
        """A viewer cheered Bits (Twitch) or gifted Kicks (Kick): turn it into paid time, in proportion to the 30-day price."""
        try: amount=int(amount or 0)
        except (TypeError,ValueError): amount=0
        if amount<=0: return
        label='Bits' if unit=='bits' else 'Kicks'
        minimum=self._paid_int(self.paid_min_kicks if unit=='kicks' else self.paid_min_bits,'paid_min_kicks' if unit=='kicks' else 'paid_min_bits',1,1,1000000)
        if amount<minimum:
            self.set_status(f'{user} sent {amount} {label}: under the minimum of {minimum}, so no time was added.'); return
        expires=self._grant_paid_seconds(platform,user,user_id,amount/self._paid_price(unit,badges)*30*86400,source=unit,amount=amount,unit=label)
        if not expires: return
        until=time.strftime('%b %d',time.localtime(expires))
        # _report also shows the message as an alert in the OBS dock.
        self._report(f'{user} ({platform}) paid {amount} {label}: '+self._paid_what()+f' unlocked until {until}.')
        if self.paid_voices_enabled.get() and self.paid_reply.get():
            names=self._voice_code_list(); example=(names[0] if names else 'Heart')+': hello'
            self._bot_say(platform,f'Thanks {user}! {amount} {label} unlocks '+('reading your chat' if self.paid_scope.get()=='all' else 'voice codes')+f' until {until}.'+('' if self.paid_scope.get()=='all' else f' Try "{example}"'))
    # ---- Free for everyone timer
    def _free_window_left(self):
        return max(0,self.paid_free_until-time.time())
    def _start_free_window(self,hours):
        self.paid_free_until=time.time()+hours*3600
        self.settings['paid_free_until']=self.paid_free_until; self._save_connection_settings()
        label=f'{hours} hour'+('' if hours==1 else 's')
        self._report(f'Free for everyone: {self._paid_what()} is unlocked for all viewers for the next {label}.')
        if self.paid_voices_enabled.get() and self.paid_reply.get():
            for platform in ('Twitch','Kick'): self._bot_say(platform,f'Free for everyone! {self._paid_what().capitalize()} is unlocked for all viewers for the next {label}.')
        self._update_free_status()
    def _stop_free_window(self):
        if self.paid_free_until<=0: return
        self.paid_free_until=0.0
        self.settings['paid_free_until']=0; self._save_connection_settings()
        self._report('The free period for everyone has ended.')
        if self.paid_voices_enabled.get() and self.paid_reply.get():
            for platform in ('Twitch','Kick'): self._bot_say(platform,'The free period is over. '+self._paid_what().capitalize()+' is back to the normal rules.')
        self._update_free_status()
    def _update_free_status(self):
        if not hasattr(self,'paid_free_status'): return
        left=self._free_window_left()
        self.paid_free_status.set(f'Active: free for everyone, {self._paid_left_text(left)} left.' if left>0 else 'Not active. Everyone follows the normal rules.')
    def _start_free_tick(self):
        if getattr(self,'_free_tick_started',False): return
        self._free_tick_started=True; self._tick_free_window()
    def _tick_free_window(self):
        try:
            if self.paid_free_until>0 and time.time()>=self.paid_free_until: self._stop_free_window()
            else: self._update_free_status()
        except Exception: pass
        self.root.after(10000,self._tick_free_window)
    # ---- Free trial for new viewers
    def _load_paid_trials(self,source=None):
        raw=(source if source is not None else self.settings).get('paid_trials',{}); result={}; cutoff=time.time()-180*86400
        if isinstance(raw,dict):
            for key,item in raw.items():
                try:
                    start=float(item.get('start',0))
                    if start>cutoff: result[str(key)]={'start':start,'count':int(item.get('count',0))}
                except (AttributeError,TypeError,ValueError): continue
        return result
    def _trial_entry(self,platform,user,user_id=None):
        for key in self._paid_keys(platform,user,user_id):
            if key in self.paid_trials: return self.paid_trials[key]
        return None
    def _trial_left_text(self,platform,user,user_id=None):
        mode=self.paid_trial_mode.get()
        if mode not in ('messages','minutes'): return ''
        amount=self._paid_int(self.paid_trial_amount,'paid_trial_amount',5,1,1000); entry=self._trial_entry(platform,user,user_id)
        if mode=='messages':
            left=amount-(entry['count'] if entry else 0)
            return (f'{left} free trial message'+('' if left==1 else 's')) if left>0 else ''
        left=amount*60-((time.time()-entry['start']) if entry else 0)
        return f'{self._paid_left_text(left)} of free trial' if left>0 else ''
    def _use_trial(self,platform,user,user_id=None):
        """True when this viewer may use the paid feature for free right now on their trial (and counts it)."""
        mode=self.paid_trial_mode.get()
        if mode not in ('messages','minutes'): return False
        keys=self._paid_keys(platform,user,user_id)
        if not keys: return False
        amount=self._paid_int(self.paid_trial_amount,'paid_trial_amount',5,1,1000); entry=self._trial_entry(platform,user,user_id); now=time.time(); is_new=entry is None
        if is_new: entry={'start':now,'count':0}
        if mode=='messages':
            if entry['count']>=amount: return False
            entry['count']+=1
        elif now-entry['start']>=amount*60: return False
        else: entry['count']+=1
        self.paid_trials[keys[0]]=entry
        for key in keys[1:]: self.paid_trials.pop(key,None)
        if is_new or mode=='messages':
            self.settings['paid_trials']={k:dict(v) for k,v in self.paid_trials.items()}; self._save_connection_settings()
        return True
    def _paid_what(self):
        return 'chat reading' if self.paid_scope.get()=='all' else 'voice codes'
    def _unlock_hint(self,platform,badges=None):
        kick=str(platform).title()=='Kick'; unit='Kicks' if kick else 'Bits'; count=self._paid_price('kicks' if kick else 'bits',badges)
        minimum=self._paid_int(self.paid_min_kicks if kick else self.paid_min_bits,'paid_min_kicks' if kick else 'paid_min_bits',1,1,1000000)
        verb='gifts' if kick else 'cheers'
        extra=f'{verb} of {minimum}+ {unit} add time' if minimum>1 else 'any amount adds time'
        return f'{count} {unit} unlocks 30 days ({extra})'
    def _notice_unpaid(self,platform,user,badges=None):
        keys=self._paid_keys(platform,user)
        if not keys or not self.paid_reply.get() or not self._can_send(platform): return
        now=time.time(); tag='hint|'+keys[0]
        if now-self.paid_notice_last.get(tag,0)<1800: return
        self.paid_notice_last[tag]=now
        if now-self.paid_notice_last.get('any|'+str(platform).title(),0)<15: return
        self.paid_notice_last['any|'+str(platform).title()]=now
        perk='chat is read aloud for supporters' if self.paid_scope.get()=='all' else 'voice codes are a supporter perk'
        self._bot_say(platform,f'@{user} {perk}: {self._unlock_hint(platform,badges)}. Type !myvoice to check your time.')
    def _answer_myvoice(self,platform,user,user_id=None,badges=None):
        keys=self._paid_keys(platform,user)
        if not keys: return
        now=time.time(); tag='my|'+keys[0]
        if now-self.paid_notice_last.get(tag,0)<20: return
        self.paid_notice_last[tag]=now
        left=self._paid_expiry(platform,user,user_id)-now; trial=self._trial_left_text(platform,user,user_id)
        if self._is_streamer(platform,user): text=f'@{user} the channel owner is always unlocked.'
        elif self._free_role(badges): text=f'@{user} you are a {self._free_role(badges)} here, so you can use it for free.'
        elif self._free_window_left()>0: text=f'@{user} everyone has free access right now ({self._paid_left_text(self._free_window_left())} left).'
        elif left>0: text=f'@{user} you have {self._paid_left_text(left)} of '+self._paid_what()+' left.'
        elif trial: text=f'@{user} you have {trial} left. After that: {self._unlock_hint(platform,badges)}.'
        else: text=f'@{user} you have no paid time yet: {self._unlock_hint(platform,badges)}.'
        self._bot_say(platform,text)
    def _maybe_remind_expiry(self,platform,user,user_id=None):
        """Once per paid period: when a viewer chats while their paid time is about to end, remind them in chat."""
        if not (self.paid_voices_enabled.get() and self.paid_remind.get()) or self._free_window_left()>0: return
        item=None
        for key in self._paid_keys(platform,user,user_id):
            item=self.paid_voice_access.get(key)
            if item: break
        if not item: return
        now=time.time(); left=item.get('expires',0)-now
        days=self._paid_int(self.paid_remind_days,'paid_remind_days',3,1,30)
        if left<=0 or left>days*86400 or item.get('reminded')==item.get('expires'): return
        if now-self.paid_notice_last.get('any|'+str(platform).title(),0)<15: return
        if self._bot_say(platform,f'@{user} your '+self._paid_what()+f' runs out in {self._paid_left_text(left)}. To keep it: {self._unlock_hint(platform)}.'):
            self.paid_notice_last['any|'+str(platform).title()]=now
            item['reminded']=item.get('expires'); self._save_paid_access()
    def _dock_supporters(self):
        """Viewers with paid time left, longest first, for the OBS dock's supporter list."""
        now=time.time(); rows=[]
        for item in sorted(self.paid_voice_access.values(),key=lambda it:-it.get('expires',0)):
            left=item.get('expires',0)-now
            if left<=0: break
            rows.append({'name':item.get('name') or '?','platform':item.get('platform','Twitch'),'left':self._paid_left_text(left)})
            if len(rows)>=60: break
        return rows
    def _bot_say(self,platform,text):
        platform=str(platform).title()
        if platform not in ('Twitch','Kick') or not self._can_send(platform): return False
        text=str(text)[:450]
        if not hasattr(self,'recent_bot_lines'): self.recent_bot_lines=[]
        self.recent_bot_lines.append((platform,re.sub(r'\s+',' ',text).strip().lower(),time.time()))
        uid=self.twitch_user_id if platform=='Twitch' else self.kick_user_id
        threading.Thread(target=lambda:self._send_to_platform(platform,text,uid),daemon=True).start()
        return True
    def _refresh_paid_voices(self):
        if not hasattr(self,'paid_tree'): return
        for child in self.paid_tree.get_children(): self.paid_tree.delete(child)
        self.paid_tree_keys={}; now=time.time()
        for index,(key,item) in enumerate(sorted(self.paid_voice_access.items(),key=lambda pair:-pair[1].get('expires',0))):
            left=item.get('expires',0)-now
            status=(self._paid_left_text(left)+' left') if left>0 else 'expired'
            self.paid_tree_keys[str(index)]=key
            self.paid_tree.insert('', 'end', iid=str(index), values=(item.get('name') or key.split(':',1)[-1], item.get('platform',''), status))
    def _add_paid_access(self):
        name=self.paid_add_name.get().strip().lstrip('@')
        if not name:
            messagebox.showwarning('Paid voices','Type the viewer’s name first.'); return
        try: days=max(1,min(3650,int(self.paid_add_days.get())))
        except (ValueError,tk.TclError): days=30
        self._grant_paid_seconds(self.paid_add_platform.get(),name,None,days*86400,cap=False,source='manual')
        self.paid_add_name.set('')
    def _remove_paid_access(self):
        removed=False
        for iid in self.paid_tree.selection():
            key=getattr(self,'paid_tree_keys',{}).get(iid)
            item=self.paid_voice_access.pop(key,None) if key else None
            if item is not None:
                removed=True; self._log_paid(item.get('platform',''),item.get('name') or key.split(':',1)[-1],'removed',0,'',-max(0,item.get('expires',0)-time.time())/86400)
        if removed: self._save_paid_access(); self._refresh_paid_voices()
    def _adjust_paid_selected(self,sign):
        """Adds days to (sign=1) or takes days off (sign=-1) the viewers selected in the list, using the Days box."""
        keys=[getattr(self,'paid_tree_keys',{}).get(iid) for iid in self.paid_tree.selection()]
        keys=[key for key in keys if key in self.paid_voice_access]
        if not keys:
            messagebox.showinfo('Paid voices','Select a viewer in the list first.'); return
        try: days=max(1,min(3650,int(self.paid_add_days.get())))
        except (ValueError,tk.TclError): days=30
        now=time.time(); delta=days*86400
        for key in keys:
            item=self.paid_voice_access[key]; old=item.get('expires',0)
            new=max(old,now)+delta if sign>0 else max(old-delta,min(old,now))
            item['expires']=new; item['reminded']=0
            self._log_paid(item.get('platform',''),item.get('name') or key.split(':',1)[-1],'adjust',0,'',(new-max(old,now) if sign>0 else new-old)/86400)
        self._save_paid_access(); self._refresh_paid_voices()
    def _export_paid_history(self):
        if not self.paid_history:
            messagebox.showinfo('Paid voices','There is no payment history yet.'); return
        path=filedialog.asksaveasfilename(title='Export payment history',defaultextension='.csv',initialfile='paid_voices_history.csv',filetypes=[('CSV file','*.csv'),('All files','*.*')])
        if not path: return
        def safe(text):
            text=str(text)
            return "'"+text if text[:1] in ('=','+','-','@') else text
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                writer=csv.writer(f); writer.writerow(['Date','Platform','Viewer','What','Amount','Unit','Days added'])
                for entry in self.paid_history:
                    writer.writerow([time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(entry['t'])),entry['platform'],safe(entry['name']),entry['source'],entry['amount'] or '',entry['unit'],entry['days']])
            self.set_status(f'Payment history exported ({len(self.paid_history)} lines).')
        except OSError as exc: messagebox.showerror('Export payment history',str(exc))
    def _is_bot_reply_echo(self,platform,content):
        """True for the echo of our own !voicelist reply, so it is never read aloud."""
        text=re.sub(r'\s+',' ',content or '').strip().lower(); platform=str(platform).title(); now=time.time()
        lines=[item for item in getattr(self,'recent_bot_lines',[]) if now-item[2]<180]
        self.recent_bot_lines=lines
        for index,(line_platform,line_text,_stamp) in enumerate(lines):
            if line_platform==platform and line_text==text:
                del self.recent_bot_lines[index]; return True
        return False
    def _answer_voicelist(self,platform):
        """A viewer typed !voicelist: reply in chat with the voice codes (at most once a minute per platform)."""
        platform=str(platform).title()
        if platform not in ('Twitch','Kick'): return
        if not hasattr(self,'voicelist_last'): self.voicelist_last={}
        now=time.time()
        if now-self.voicelist_last.get(platform,0)<60: return
        if not self._can_send(platform):
            self.set_status('Someone typed !voicelist on '+platform+', but you are not signed in there, so the app cannot reply.'); return
        names=self._voice_code_list()
        if not names: return
        self.voicelist_last[platform]=now
        lines=['Voice codes: start your message with a voice name and a colon, e.g. "'+names[0]+': hello there". Available voices:']
        current=''
        for name in names:
            candidate=current+', '+name if current else name
            if len(candidate)>420: lines.append(current); current=name
            else: current=candidate
        if current: lines.append(current)
        lines=lines[:6]
        uid=self.twitch_user_id if platform=='Twitch' else self.kick_user_id
        if not hasattr(self,'recent_bot_lines'): self.recent_bot_lines=[]
        for line in lines: self.recent_bot_lines.append((platform,re.sub(r'\s+',' ',line).strip().lower(),now))
        def send_all():
            for line in lines:
                self._send_to_platform(platform,line,uid); time.sleep(1.3)
        threading.Thread(target=send_all,daemon=True).start()
        self.set_status('Answered !voicelist on '+platform+'.')
    def _apply_chat_voice_command(self,platform,username,content):
        if self.tts_voice_mode.get()!='five' or not username: return False
        match=re.fullmatch(r'!voice([1-5])',str(content or '').strip(),flags=re.IGNORECASE)
        if not match: return False
        self._note_chat_user(platform,username)
        key=self._chat_voice_key(platform,username)
        slot=int(match.group(1))-1
        self.user_voice_assignments[key]=slot
        self._save_chat_voice_settings()
        self._refresh_chat_voice_users()
        self.set_status(f'{username} chose Voice {slot+1} for {str(platform).title()} chat.')
        return True
    def _refresh_chat_voice_users(self,_event=None):
        if not hasattr(self,'chat_voice_tree'): return
        for child in self.chat_voice_tree.get_children(): self.chat_voice_tree.delete(child)
        query=self.chat_voice_search.get().strip().casefold()
        self.chat_voice_rows={}
        for platform,name in sorted(self.chat_voice_users.values(),key=lambda item:(item[0].casefold(),item[1].casefold())):
            key=self._chat_voice_key(platform,name)
            if query and query not in platform.casefold() and query not in name.casefold(): continue
            slot=self.user_voice_assignments.get(key)
            voice=f'Voice {slot+1} — {self.chat_voice_slots[slot]}' if slot is not None else 'Not assigned yet'
            iid=str(len(self.chat_voice_rows)); self.chat_voice_rows[iid]=key
            self.chat_voice_tree.insert('', 'end', iid=iid, values=(platform,name,voice))
        slots=[f'Voice {i+1} — {self.chat_voice_slots[i]}' for i in range(5)]
        if hasattr(self,'chat_voice_assignment_box'): self.chat_voice_assignment_box.configure(values=slots)
        if hasattr(self,'chat_voice_boxes'):
            values=self.builtin_voices+list(self.voice_catalog)
            for box in self.chat_voice_boxes: box.configure(values=values)
    def _select_chat_voice_user(self,_event=None):
        selected=self.chat_voice_tree.selection()
        if not selected: return
        key=self.chat_voice_rows.get(selected[0]); slot=self.user_voice_assignments.get(key)
        if slot is not None: self.chat_voice_assignment_box.current(slot)
    def assign_chat_voice(self):
        selected=self.chat_voice_tree.selection()
        if not selected:
            messagebox.showinfo('Assign chatter voice','Select a chatter from the list first.')
            return
        slot=self.chat_voice_assignment_box.current()
        if not 0<=slot<5:
            messagebox.showwarning('Assign chatter voice','Choose one of the five voice slots.')
            return
        key=self.chat_voice_rows[selected[0]]
        self.user_voice_assignments[key]=slot
        self._save_chat_voice_settings(); self._refresh_chat_voice_users()
        self.set_status('Assigned '+key.split('|',1)[1]+' to Voice '+str(slot+1)+'.')
    def _build_create_page(self,page):
        self._page_header(page,'Create Speech','Build a voice mix, press Test to hear it, then save the ones you like as reusable voices or presets.',info=True)
        holder=ttk.Frame(page,style='Page.TFrame'); holder.pack(fill='both',expand=True)
        create_canvas=tk.Canvas(holder,bg=PAGE_BG,highlightthickness=0)
        create_scrollbar=ttk.Scrollbar(holder,orient='vertical',command=create_canvas.yview)
        create_canvas.configure(yscrollcommand=create_scrollbar.set)
        create_scrollbar.pack(side='right',fill='y'); create_canvas.pack(side='left',fill='both',expand=True)
        body=ttk.Frame(create_canvas,style='Page.TFrame')
        create_window=create_canvas.create_window((0,0),window=body,anchor='nw')
        def update_create_scroll(_event=None):
            create_canvas.configure(scrollregion=create_canvas.bbox('all'))
            bounds=create_canvas.bbox('all')
            needs_scroll=bool(bounds and bounds[3]-bounds[1]>create_canvas.winfo_height())
            if needs_scroll and not create_scrollbar.winfo_manager(): create_scrollbar.pack(side='right',fill='y')
            elif not needs_scroll and create_scrollbar.winfo_manager(): create_scrollbar.pack_forget()
        body.bind('<Configure>',update_create_scroll)
        create_canvas.bind('<Configure>',lambda event:(create_canvas.itemconfigure(create_window,width=event.width),update_create_scroll()))
        def scroll_create(event):
            if getattr(self,'current_page','')!='Voice': return
            try: target=create_canvas.winfo_containing(event.x_root,event.y_root)
            except (KeyError,tk.TclError): return  # an open dropdown list can't be resolved by Tk
            if target and str(target).startswith(str(create_canvas)): create_canvas.yview_scroll(int(-event.delta/120),'units')
        create_canvas.bind_all('<MouseWheel>',scroll_create,add='+')
        page=body
        create_card=self._card(page,'Create voices')
        create_bar=tk.Frame(create_card,bg=PAL['raised'],highlightbackground=PAL['line'],highlightthickness=1); create_bar.pack(anchor='w',pady=(0,18))
        self.create_tab_buttons={}; self.create_panels={}
        for key,title in (('mixer','Voice mixer'),('presets','Presets and packs'),('clone','Voice cloning')):
            button=tk.Button(create_bar,text=title,command=lambda selected=key:self._select_create_tab(selected),relief='flat',bd=0,width=18,pady=8,fg='white',activeforeground='white',font=(FONT_FAMILY,10,'bold'),cursor='hand2',highlightthickness=0)
            button.pack(side='left',padx=3,pady=3); self.create_tab_buttons[key]=button
        studio=ttk.Frame(create_card,style='Panel.TFrame'); self.create_panels['mixer']=studio
        self.studio_mode=tk.StringVar(value='Blend voices'); self.speaker_count=tk.IntVar(value=2); self.smoothness=tk.DoubleVar(value=35)
        self.studio_voice1=tk.StringVar(value='American English — Heart (af_heart)')
        self.voice2=tk.StringVar(value='American English — Bella (af_bella)'); self.voice3=tk.StringVar(value='American English — Adam (am_adam)'); self.weight1=tk.IntVar(value=50); self.weight2=tk.IntVar(value=50); self.weight3=tk.IntVar(value=0)
        ttk.Label(studio,text='Mode').grid(row=0,column=0,sticky='w',pady=5)
        self.studio_mode_box=ttk.Combobox(studio,textvariable=self.studio_mode,state='readonly',width=27,values=['Blend voices','Sentence split','Take turns per message','Take turns per word'])
        self.studio_mode_box.grid(row=0,column=1,sticky='w',padx=10)
        self.studio_speaker_count_label=ttk.Label(studio,text='Speakers')
        self.studio_speaker_count_label.grid(row=0,column=2,sticky='e')
        self.studio_speaker_count_spin=ttk.Spinbox(studio,from_=2,to=3,width=5,textvariable=self.speaker_count)
        self.studio_speaker_count_spin.grid(row=0,column=3,sticky='w',padx=8)
        self.voice_boxes=[]; self.studio_speaker_rows=[]; self.studio_speaker_labels=[]; self.studio_mix_labels=[]; self.studio_mix_spins=[]
        for row,(label,var,weight) in enumerate((('Speaker 1',self.studio_voice1,self.weight1),('Speaker 2',self.voice2,self.weight2),('Speaker 3',self.voice3,self.weight3)),1):
            speaker_row=ttk.Frame(studio,style='Panel.TFrame')
            speaker_row.grid(row=row,column=0,columnspan=4,sticky='w',pady=3)
            speaker_label=ttk.Label(speaker_row,text=label,width=12); speaker_label.pack(side='left')
            box=ttk.Combobox(speaker_row,textvariable=var,values=self.builtin_voices,state='readonly',width=46); box.pack(side='left',padx=10)
            mix_label=ttk.Label(speaker_row,text='Mix %'); mix_label.pack(side='left',padx=(8,4))
            mix_spin=ttk.Spinbox(speaker_row,from_=0,to=100,width=6,textvariable=weight); mix_spin.pack(side='left')
            self.voice_boxes.append(box); self.studio_speaker_rows.append(speaker_row); self.studio_speaker_labels.append(speaker_label); self.studio_mix_labels.append(mix_label); self.studio_mix_spins.append(mix_spin)
        self.studio_smooth_label=ttk.Label(studio,text='Transition smoothness')
        self.studio_smooth_label.grid(row=4,column=0,sticky='w',pady=8)
        self.studio_smooth_scale=ttk.Scale(studio,from_=0,to=100,variable=self.smoothness,length=230)
        self.studio_smooth_scale.grid(row=4,column=1,sticky='w',padx=10)
        self.studio_smooth_hint=ttk.Label(studio,text='0 = clean cuts  •  100 = longer crossfades',style='Muted.TLabel')
        self.studio_smooth_hint.grid(row=4,column=2,columnspan=2,sticky='w')
        self.studio_mode_hint=ttk.Label(studio,text='',style='Muted.TLabel',wraplength=800)
        self.studio_mode_hint.grid(row=5,column=0,columnspan=4,sticky='w',pady=(6,0))
        test_row=ttk.Frame(studio,style='Panel.TFrame'); test_row.grid(row=6,column=0,columnspan=4,sticky='w',pady=(14,0))
        ttk.Label(test_row,text='Test text').pack(side='left')
        self.studio_test_text=tk.StringVar(value=STUDIO_TEST_TEXT)
        ttk.Entry(test_row,textvariable=self.studio_test_text,width=48).pack(side='left',padx=10)
        ttk.Button(test_row,text='▶  Test this setup',style='Accent.TButton',command=self.test_studio_setup).pack(side='left')
        self.save_blended_button=ttk.Button(test_row,text='Save this blend as a voice…',command=self.save_blended_voice)
        self.studio_mode_box.bind('<<ComboboxSelected>>',lambda _event:self._refresh_studio_mode())
        self.speaker_count.trace_add('write',lambda *_args:self._refresh_studio_mode())
        self._refresh_studio_mode()
        actions=ttk.Frame(create_card,style='Panel.TFrame'); self.create_panels['presets']=actions
        self.preset_name=tk.StringVar(); ttk.Button(actions,text='Save setup preset',command=self.save_voice_preset).grid(row=0,column=0,padx=5,pady=5)
        self.preset_combo=ttk.Combobox(actions,textvariable=self.preset_name,state='readonly',width=23); self.preset_combo.grid(row=0,column=1,padx=8)
        ttk.Button(actions,text='Load preset',command=self.load_voice_preset).grid(row=0,column=2,padx=5)
        ttk.Button(actions,text='Import custom voice pack',command=self.import_voice_pack).grid(row=1,column=0,columnspan=2,sticky='w',padx=5,pady=8)
        ttk.Label(actions,text='Imports Kokoro .pt/.pth voice packs. To use a WAV or MP3 recording of a voice, use the voice cloning section below.',style='Muted.TLabel',wraplength=760).grid(row=2,column=0,columnspan=3,sticky='w',pady=(6,0))
        clone=ttk.Frame(create_card,style='Panel.TFrame'); self.create_panels['clone']=clone
        ttk.Label(clone,text='Quick cloning: pick a clear 5–10 second recording of one voice and it can speak new text in that voice. Fine-tuning: train a dedicated voice from about 10 minutes of recordings for a closer match. Everything runs on this PC with your NVIDIA card; no paid service is used. The first setup downloads large free open-source files. Use a recording you own or have permission to use.',style='Muted.TLabel',wraplength=800).pack(anchor='w',pady=(0,10))
        row=ttk.Frame(clone,style='Panel.TFrame'); row.pack(fill='x')
        self.clone_name=tk.StringVar(); self.clone_language=tk.StringVar(value='English')
        ttk.Label(row,text='Voice name').pack(side='left'); ttk.Entry(row,textvariable=self.clone_name,width=20).pack(side='left',padx=(6,14))
        ttk.Label(row,text='Speech language').pack(side='left'); ttk.Combobox(row,textvariable=self.clone_language,state='readonly',width=18,values=list(CLONE_LANGUAGES)).pack(side='left',padx=6)
        ttk.Button(clone,text='Add voice from recording…',command=self.add_cloned_voice).pack(anchor='w',pady=(12,5))
        tune=ttk.Frame(clone,style='Panel.TFrame'); tune.pack(fill='x',pady=(8,0))
        self.finetune_model=tk.StringVar(value='0.6B (fits a 12 GB card)'); self.finetune_length=tk.StringVar(value='Normal (5 rounds)')
        ttk.Label(tune,text='Fine-tune model').pack(side='left'); ttk.Combobox(tune,textvariable=self.finetune_model,state='readonly',width=24,values=['0.6B (fits a 12 GB card)','1.7B (needs 16 GB or more)']).pack(side='left',padx=(6,14))
        ttk.Label(tune,text='Training length').pack(side='left'); ttk.Combobox(tune,textvariable=self.finetune_length,state='readonly',width=18,values=['Short (3 rounds)','Normal (5 rounds)','Long (10 rounds)']).pack(side='left',padx=6)
        ttk.Button(clone,text='Fine-tune a voice from ~10 minutes of recordings…',command=self.start_finetune).pack(anchor='w',pady=(8,5))
        ttk.Button(clone,text='Refresh voice list',command=self.refresh_voice_list).pack(anchor='w',pady=(0,5))
        ttk.Button(clone,text='Delete cloned voices…',command=self.delete_cloned_voices).pack(anchor='w',pady=(0,5))
        ttk.Button(clone,text='Install local clone engine',command=self.install_clone_engine).pack(anchor='w')
        self._refresh_studio_mode()
        self._select_create_tab('mixer')
    def _select_create_tab(self,tab):
        if tab not in self.create_panels: tab='mixer'
        for panel in self.create_panels.values(): panel.pack_forget()
        self.create_panels[tab].pack(fill='x',anchor='n')
        for key,button in self.create_tab_buttons.items():
            active=key==tab
            button.configure(bg=PAL['accent'] if active else PAL['raised'],activebackground=PAL['accent_hi'] if active else PAL['hover'],fg='#ffffff' if active else PAL['muted'],activeforeground='#ffffff')
    def _refresh_studio_mode(self):
        if not hasattr(self,'studio_speaker_rows'): return
        mode=self.studio_mode.get()
        try: count=max(2,min(3,int(self._num(self.speaker_count,2))))
        except (ValueError,tk.TclError): count=2
        multi=mode!='Single voice'
        if multi:
            self.studio_speaker_count_label.grid(); self.studio_speaker_count_spin.grid()
        else:
            self.studio_speaker_count_label.grid_remove(); self.studio_speaker_count_spin.grid_remove()
        for index,row in enumerate(self.studio_speaker_rows):
            visible=index==0 or (multi and index<count)
            if visible: row.grid()
            else: row.grid_remove()
            self.studio_speaker_labels[index].configure(text='Voice' if mode=='Single voice' and index==0 else f'Speaker {index+1}')
            show_mix=mode=='Blend voices' and visible
            for widget in (self.studio_mix_labels[index],self.studio_mix_spins[index]):
                if show_mix and not widget.winfo_manager(): widget.pack(side='left',padx=(8,4) if widget is self.studio_mix_labels[index] else 0)
                elif not show_mix and widget.winfo_manager(): widget.pack_forget()
        show_smooth=mode=='Sentence split'
        for widget in (self.studio_smooth_label,self.studio_smooth_scale,self.studio_smooth_hint):
            if show_smooth: widget.grid()
            else: widget.grid_remove()
        if hasattr(self,'save_blended_button'):
            if mode=='Blend voices':
                if not self.save_blended_button.winfo_manager(): self.save_blended_button.pack(side='left',padx=8)
            elif self.save_blended_button.winfo_manager(): self.save_blended_button.pack_forget()
        hints={
            'Single voice':'One voice speaks the full message.',
            'Blend voices':'Mix the selected voices into one voice. Press Test to hear it, change the Mix % until you like it, then save it as a voice. Voices must use the same language.',
            'Sentence split':'Each sentence switches to the next speaker. Transition smoothness controls how the audio joins.',
            'Take turns per message':'Each message is spoken by one selected speaker. Test plays each speaker in turn.',
            'Take turns per word':'Each word switches to the next speaker. This can sound choppy because words are generated separately.',
        }
        self.studio_mode_hint.configure(text=hints.get(mode,''))
    @staticmethod
    def _normalize_username(value):
        return str(value or '').strip().lstrip('@').strip().casefold()
    def _load_ignored_users(self):
        try:
            with open(self.ignore_users_path,'r',encoding='utf-8') as f: data=json.load(f)
            if isinstance(data,dict) and isinstance(data.get('blocks'),list):
                result=set()
                for item in data['blocks']:
                    if isinstance(item,dict):
                        name=self._normalize_username(item.get('username')); platform=str(item.get('platform','')).title()
                        if name and platform in ('Twitch','Kick'): result.add((platform,name))
                return result
            names=data.get('users',[]) if isinstance(data,dict) else data
            # Migrate older shared blocks to both platform-specific entries.
            return {(platform,self._normalize_username(name)) for name in names if self._normalize_username(name) for platform in ('Twitch','Kick')}
        except (OSError,json.JSONDecodeError,TypeError):
            return set()
    def _save_ignored_users(self):
        try:
            with open(self.ignore_users_path,'w',encoding='utf-8') as f: json.dump({'blocks':[{'platform':platform,'username':name} for platform,name in sorted(self.ignored_users)]},f,ensure_ascii=False,indent=2)
        except OSError as e:
            messagebox.showerror('Ignore users','Could not save the ignore list. '+str(e))
    def _refresh_ignored_users(self):
        rows=sorted(self.ignored_users)
        if hasattr(self,'ignore_combo'):
            query=self._normalize_username(self.ignore_search.get())
            self.ignore_combo.configure(values=[self._blocked_label(platform,name) for platform,name in rows if not query or query in name or query in platform.casefold()])
        self._refresh_blocked_list()
    @staticmethod
    def _blocked_label(platform,name):
        return f'{platform} — {name}'
    def _refresh_blocked_list(self,_event=None):
        if not hasattr(self,'blocked_list'): return
        query=self._normalize_username(self.blocked_search.get())
        matches=[(platform,name) for platform,name in sorted(self.ignored_users) if not query or query in name or query in platform.casefold()]
        self.blocked_rows=matches
        self.blocked_list.delete(0,'end')
        for platform,name in matches: self.blocked_list.insert('end',self._blocked_label(platform,name))
        self.blocked_count.set(f'{len(self.ignored_users)} platform blocks')
        if hasattr(self,'undo_block_button'):
            keys=self.last_blocked_user or []
            available=bool(keys) and all(key in self.ignored_users for key in keys)
            label=('Undo last block: '+', '.join(self._blocked_label(*key) for key in keys)) if keys else 'Undo last block'
            self.undo_block_button.configure(state='normal' if available else 'disabled',text=label)
    def filter_ignored_users(self,_event=None):
        self._refresh_ignored_users()
    def add_ignored_user(self,username=None,platform=None):
        name=self._normalize_username(username if username is not None else self.ignore_username.get())
        if not name: return
        selected=platform or (self.ignore_platform.get() if hasattr(self,'ignore_platform') else 'Both')
        platforms=('Twitch','Kick') if selected=='Both' else (selected,)
        added=[]
        for target in platforms:
            key=(target,name)
            if key not in self.ignored_users: self.ignored_users.add(key); added.append(key)
        if added: self.last_blocked_user=added
        self._save_ignored_users(); self._refresh_ignored_users(); self.ignore_username.set('')
        self.set_status(('Blocked '+self._blocked_label(*added[-1])+' from TTS.' if added else name+' is already blocked for the selected platform.'))
    def unblock_ignored_user(self,username=None,platform=None):
        if isinstance(username,tuple): platform,name=username
        elif platform: name=self._normalize_username(username)
        else:
            label=str(username if username is not None else self.ignore_search.get() if hasattr(self,'ignore_search') else '')
            if ' — ' not in label:
                messagebox.showinfo('Unblock user','Choose a platform and username from the dropdown first.')
                return
            platform,raw_name=label.split(' — ',1); name=self._normalize_username(raw_name)
        key=(str(platform).title(),name)
        if key not in self.ignored_users:
            messagebox.showinfo('Unblock user','Choose a username from the dropdown first.')
            return
        self.ignored_users.remove(key)
        if self.last_blocked_user and key in self.last_blocked_user:
            self.last_blocked_user=[item for item in self.last_blocked_user if item!=key] or None
        self._save_ignored_users()
        if hasattr(self,'ignore_search'): self.ignore_search.set('')
        self._refresh_ignored_users()
        if hasattr(self,'blocked_search'): self.blocked_search.set('')
        self.set_status('Unblocked '+self._blocked_label(*key)+'. Their messages can be read aloud again on that platform.')
    @staticmethod
    def _normalize_word_substitutions(items):
        result=[]
        if not isinstance(items,list): return result
        for item in items:
            if not isinstance(item,dict): continue
            word=str(item.get('word','')).strip()
            if not word: continue
            replacement=str(item.get('replacement','')).strip() or ' '
            result.append({'word':word,'replacement':replacement})
        return result
    def _filter_section(self,parent,title):
        tk.Label(parent,text=title.upper(),bg=PAGE_BG,fg=PAL['faint'],font=(FONT_FAMILY,9,'bold')).pack(anchor='w',pady=(0,8))
        box=tk.Frame(parent,bg=FILTER_CARD,highlightbackground=PAL['line'],highlightthickness=1); box.pack(fill='x',pady=(0,24))
        return box
    def _filter_row(self,parent,title,desc,make_control,first=False,toggle_var=None):
        C=FILTER_CARD
        if not first: tk.Frame(parent,bg=PAL['line'],height=1).pack(fill='x',padx=20)
        row=tk.Frame(parent,bg=C); row.pack(fill='x',padx=20,pady=14)
        ctl=tk.Frame(row,bg=C); ctl.pack(side='right',padx=(24,0))
        make_control(ctl)
        text=tk.Frame(row,bg=C); text.pack(side='left',fill='x',expand=True)
        heading=tk.Label(text,text=title,bg=C,fg=PAL['text'],font=(FONT_FAMILY,10,'bold'),anchor='w'); heading.pack(anchor='w')
        labels=[heading]
        if desc:
            detail=tk.Label(text,text=desc,bg=C,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=420); detail.pack(anchor='w',pady=(3,0))
            text.bind('<Configure>',lambda event,label=detail:label.configure(wraplength=max(200,event.width-4)))
            labels.append(detail)
        if toggle_var is not None:
            for label in labels:
                label.configure(cursor='hand2'); label.bind('<Button-1>',lambda _e,var=toggle_var:var.set(not var.get()))
        return row
    def _filter_toggle(self,parent,title,desc,var,first=False):
        return self._filter_row(parent,title,desc,lambda ctl:ToggleSwitch(ctl,var).pack(),first=first,toggle_var=var)
    @staticmethod
    def _format_duration(seconds):
        seconds=int(seconds)
        if seconds<60: return f'{seconds} sec'
        minutes,sec=divmod(seconds,60)
        if minutes<60: return f'{minutes} min'+(f' {sec} sec' if sec else '')
        hours,minutes=divmod(minutes,60)
        if hours<24: return f'{hours} h'+(f' {minutes} min' if minutes else '')
        days,hours=divmod(hours,24)
        return f'{days} d'+(f' {hours} h' if hours else '')
    def _build_filter_page(self,page):
        self._page_header(page,'Advanced Filter','Choose which chat messages appear in the speech queue. All incoming messages remain visible in Activity.',info=True)
        holder=ttk.Frame(page,style='Page.TFrame'); holder.pack(fill='both',expand=True)
        canvas=tk.Canvas(holder,bg=PAGE_BG,highlightthickness=0)
        scrollbar=ttk.Scrollbar(holder,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left',fill='both',expand=True)
        card=ttk.Frame(canvas,style='Page.TFrame')
        body_window=canvas.create_window((0,0),window=card,anchor='nw')
        def update_filter_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            bounds=canvas.bbox('all')
            needs_scroll=bool(bounds and bounds[3]-bounds[1]>canvas.winfo_height())
            if needs_scroll and not scrollbar.winfo_manager(): scrollbar.pack(side='right',fill='y',before=canvas)
            elif not needs_scroll and scrollbar.winfo_manager(): scrollbar.pack_forget()
        card.bind('<Configure>',update_filter_scroll)
        canvas.bind('<Configure>',lambda event:(canvas.itemconfigure(body_window,width=event.width-(0 if scrollbar.winfo_manager() else 0)),update_filter_scroll()))
        def scroll_filter(event):
            if getattr(self,'current_page','')!='Advanced Filter': return
            try: target=canvas.winfo_containing(event.x_root,event.y_root)
            except (KeyError,tk.TclError): return  # an open dropdown list can't be resolved by Tk
            if target and str(target).startswith(str(canvas)): canvas.yview_scroll(int(-event.delta/120),'units')
        canvas.bind_all('<MouseWheel>',scroll_filter,add='+')
        tab_bar=tk.Frame(card,bg=PAL['raised'],highlightbackground=PAL['line'],highlightthickness=1); tab_bar.pack(anchor='w',pady=(0,22))
        self.filter_tab=tk.StringVar(value='rules'); self.filter_tab_buttons={}
        for key,title in (('rules','Message rules'),('words','Word substitutions'),('paid','Paid voices'),('feed','Feed setup')):
            button=tk.Button(tab_bar,text=title,command=lambda selected=key:self._select_filter_tab(selected),relief='flat',bd=0,width=18,pady=8,fg='white',activeforeground='white',font=(FONT_FAMILY,10,'bold'),cursor='hand2',highlightthickness=0)
            button.pack(side='left',padx=3,pady=3); self.filter_tab_buttons[key]=button
        filters=ttk.Frame(card,style='Page.TFrame'); self.filter_rules_panel=filters
        self.read_username=tk.BooleanVar(value=bool(self.settings.get('filter_read_username',True))); self.skip_links=tk.BooleanVar(value=bool(self.settings.get('filter_skip_links',True))); self.skip_commands=tk.BooleanVar(value=bool(self.settings.get('filter_skip_commands',True))); self.only_highlighted=tk.BooleanVar(value=bool(self.settings.get('filter_only_highlighted',False)))
        self.skip_bots=tk.BooleanVar(value=bool(self.settings.get('filter_skip_bots',False)))
        try: minimum_length=max(1,min(100,int(self.settings.get('filter_min_length',1))))
        except (TypeError,ValueError): minimum_length=1
        self.min_length=tk.IntVar(value=minimum_length)
        try: saved_timeout=max(1,min(1209600,int(self.settings.get('timeout_seconds',600))))
        except (TypeError,ValueError): saved_timeout=600
        self.timeout_seconds=tk.IntVar(value=saved_timeout); self.read_own_sent=tk.BooleanVar(value=bool(self.settings.get('filter_read_own_sent',False))); self.voice_codes=tk.BooleanVar(value=bool(self.settings.get('filter_voice_codes',True)))
        try: saved_skip=max(0,min(100,int(self.settings.get('queue_skip_limit',10))))
        except (TypeError,ValueError): saved_skip=10
        self.queue_skip=tk.IntVar(value=saved_skip)
        def spin(var,lo,hi,inc,unit,width=7,extra=None):
            def make(ctl):
                ttk.Spinbox(ctl,from_=lo,to=hi,increment=inc,textvariable=var,width=width).pack(side='left')
                tk.Label(ctl,text=unit,bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9)).pack(side='left',padx=(8,0))
                if extra: extra(ctl)
            return make
        def pair(bits_var,kicks_var,inc=10,width=8):
            def make(ctl):
                for name,var,unit in (('Twitch',bits_var,'Bits'),('Kick',kicks_var,'Kicks')):
                    tk.Label(ctl,text=name,bg=FILTER_CARD,fg=PAL['text'],font=(FONT_FAMILY,9,'bold')).pack(side='left',padx=(0 if name=='Twitch' else 18,6))
                    ttk.Spinbox(ctl,from_=1,to=1000000,increment=inc,textvariable=var,width=width).pack(side='left')
                    tk.Label(ctl,text=unit,bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9)).pack(side='left',padx=(6,0))
            return make
        # --- What gets read ---
        box=self._filter_section(filters,'What gets read')
        self._filter_toggle(box,'Read username','Say the chatter’s name before each message.',self.read_username,first=True)
        self._filter_toggle(box,'Skip links','Messages that contain a link are left out of the speech queue.',self.skip_links)
        self._filter_toggle(box,'Skip !commands','Ignore messages that start with “!”, like !lurk or !discord.',self.skip_commands)
        self._filter_toggle(box,'Skip bot accounts','Ignore messages sent by bot accounts.',self.skip_bots)
        self._filter_toggle(box,'Only read highlighted messages','Twitch only. Everything else stays visible in Activity but is not spoken.',self.only_highlighted)
        self._filter_toggle(box,'Read my own messages','Speak the messages you send from Activity aloud.',self.read_own_sent)
        # --- Limits & timing ---
        box=self._filter_section(filters,'Limits & timing')
        self._filter_row(box,'Minimum message length','Messages shorter than this are skipped.',spin(self.min_length,1,100,1,'characters'),first=True)
        self.timeout_human=tk.StringVar()
        def refresh_timeout_text(*_args):
            try: self.timeout_human.set('= '+self._format_duration(max(1,int(self.timeout_seconds.get()))))
            except (ValueError,tk.TclError): self.timeout_human.set('')
        def timeout_extra(ctl): tk.Label(ctl,textvariable=self.timeout_human,bg=FILTER_CARD,fg=PAL['link'],font=(FONT_FAMILY,9,'bold'),width=11,anchor='w').pack(side='left',padx=(10,0))
        self.timeout_seconds.trace_add('write',refresh_timeout_text); refresh_timeout_text()
        self._filter_row(box,'Timeout length','Used when you click Timeout on a name in Activity or the OBS dock. Kick rounds up to whole minutes.',spin(self.timeout_seconds,1,1209600,60,'seconds',width=9,extra=timeout_extra))
        self._filter_row(box,'Skip to the latest message','Keeps TTS in step with a busy chat: when more than this many messages are waiting, older ones are dropped and only the newest is read. 0 turns this off.',spin(self.queue_skip,0,100,1,'waiting',width=6))
        # --- Chat voices ---
        box=self._filter_section(filters,'Chat voices')
        self._filter_toggle(box,'Let chat pick a voice with a code','Viewers start a message with a voice name, e.g. “Heart: hello there”.',self.voice_codes,first=True)
        self.voicelist_command=tk.BooleanVar(value=bool(self.settings.get('filter_voicelist_command',True)))
        self.voicelist_command.trace_add('write',lambda *_a:self._save_filter_settings())
        self._filter_toggle(box,'Answer !voicelist in chat','When a viewer types !voicelist, reply in chat with all voice codes (at most once a minute per platform). Needs you signed in to that platform.',self.voicelist_command)
        tk.Frame(box,bg=PAL['line'],height=1).pack(fill='x',padx=20)
        codes=tk.Frame(box,bg=FILTER_CARD); codes.pack(fill='x',padx=20,pady=(14,18))
        tk.Label(codes,text='Voice codes viewers can use (not read aloud)',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9)).pack(anchor='w',pady=(0,10))
        self.voice_codes_label=ChipFlow(codes); self.voice_codes_label.pack(fill='x')
        self.voice_codes_label.set_names(self._voice_code_list())
        for variable in (self.read_username,self.skip_links,self.skip_commands,self.only_highlighted,self.skip_bots,self.min_length,self.timeout_seconds,self.read_own_sent,self.voice_codes,self.queue_skip):
            variable.trace_add('write',lambda *_args:self._save_filter_settings())
        self.word_substitutions=self._normalize_word_substitutions(self.settings.get('word_substitutions',[]))
        substitutions=ttk.Frame(card,style='Page.TFrame'); self.filter_words_panel=substitutions
        box=self._filter_section(substitutions,'Replace before speaking')
        inner=tk.Frame(box,bg=FILTER_CARD); inner.pack(fill='x',padx=20,pady=18)
        tk.Label(inner,text='Replace words or phrases before they are spoken. Leave “Replace with” blank to remove the match from speech. Click a row in the list to edit it. Activity keeps the original message.',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=700).pack(anchor='w',pady=(0,14))
        self.substitution_word=tk.StringVar(); self.substitution_replacement=tk.StringVar()
        form=tk.Frame(inner,bg=FILTER_CARD); form.pack(fill='x',pady=(0,16))
        form.columnconfigure(0,weight=1); form.columnconfigure(1,weight=1)
        tk.Label(form,text='Word or phrase',bg=FILTER_CARD,fg=PAL['text'],font=(FONT_FAMILY,9,'bold')).grid(row=0,column=0,sticky='w')
        tk.Label(form,text='Replace with',bg=FILTER_CARD,fg=PAL['text'],font=(FONT_FAMILY,9,'bold')).grid(row=0,column=1,sticky='w',padx=(12,0))
        word_entry=ttk.Entry(form,textvariable=self.substitution_word); word_entry.grid(row=1,column=0,sticky='ew',pady=(5,0))
        word_entry.bind('<Return>',lambda _event:self._add_word_substitution())
        replacement_entry=ttk.Entry(form,textvariable=self.substitution_replacement); replacement_entry.grid(row=1,column=1,sticky='ew',padx=(12,0),pady=(5,0))
        replacement_entry.bind('<Return>',lambda _event:self._add_word_substitution())
        self._substitution_edit_index=None
        self.substitution_add_button=ttk.Button(form,text='Add',style='Accent.TButton',command=self._add_word_substitution); self.substitution_add_button.grid(row=1,column=2,padx=(12,0),pady=(5,0))
        self.substitution_cancel_button=ttk.Button(form,text='Cancel',style='Outline.TButton',command=self._cancel_substitution_edit); self.substitution_cancel_button.grid(row=1,column=3,padx=(8,0),pady=(5,0)); self.substitution_cancel_button.grid_remove()
        self.substitution_tree=ttk.Treeview(inner,columns=('word','replacement'),show='headings',height=7,selectmode='browse')
        self.substitution_tree.heading('word',text='Incoming word or phrase'); self.substitution_tree.column('word',width=300,anchor='w')
        self.substitution_tree.heading('replacement',text='Spoken instead'); self.substitution_tree.column('replacement',width=300,anchor='w')
        self.substitution_tree.pack(fill='x',pady=(0,12))
        self.substitution_tree.bind('<<TreeviewSelect>>',self._load_selected_substitution)
        substitution_actions=tk.Frame(inner,bg=FILTER_CARD); substitution_actions.pack(anchor='w')
        ttk.Button(substitution_actions,text='Remove selected',style='Outline.TButton',command=self._remove_word_substitution).pack(side='left')
        ttk.Button(substitution_actions,text='Import .txt…',style='Outline.TButton',command=self._import_substitutions).pack(side='left',padx=(8,0))
        ttk.Button(substitution_actions,text='Export .txt…',style='Outline.TButton',command=self._export_substitutions).pack(side='left',padx=(8,0))
        self._refresh_word_substitutions()
        box=self._filter_section(substitutions,'Blocked words')
        self.blocklist_status=tk.StringVar(value='')
        self._filter_toggle(box,'Never read blocked words','Messages that contain a word from the list are not read aloud, even when the word is disguised: spaced out, stretched letters, number or symbol swaps, look-alike letters, censored with * and so on. Activity still shows the message.',self.blocklist_on,first=True)
        inner=tk.Frame(box,bg=FILTER_CARD); inner.pack(fill='x',padx=20,pady=18)
        tk.Label(inner,text='The list is a plain text file, one word or phrase per line, saved next to the program as blocked_words.txt. It starts empty: add the words you want blocked, or import a ready-made .txt list. When you save the file the program reloads it by itself. The notes at the top of the file explain the special lines.',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=700).pack(anchor='w',pady=(0,12))
        actions=tk.Frame(inner,bg=FILTER_CARD); actions.pack(anchor='w')
        ttk.Button(actions,text='Open list',style='Accent.TButton',command=self._open_blocklist).pack(side='left')
        ttk.Button(actions,text='Import .txt…',style='Outline.TButton',command=self._import_blocklist).pack(side='left',padx=(8,0))
        ttk.Button(actions,text='Reload',style='Outline.TButton',command=self._reload_blocklist).pack(side='left',padx=(8,0))
        tk.Label(inner,textvariable=self.blocklist_status,bg=FILTER_CARD,fg=PAL['text'],font=(FONT_FAMILY,9,'bold'),anchor='w').pack(anchor='w',pady=(12,0))
        self.blocklist_on.trace_add('write',lambda *_args:self._save_filter_settings())
        self._update_blocklist_status()
        # --- Paid voices ---
        paid=ttk.Frame(card,style='Page.TFrame'); self.filter_paid_panel=paid
        box=self._filter_section(paid,'Free for everyone')
        self.paid_free_status=tk.StringVar(value='')
        def make_free(ctl):
            for hours in (1,2,4): ttk.Button(ctl,text=f'{hours} h',style='Outline.TButton',width=5,command=lambda h=hours:self._start_free_window(h)).pack(side='left',padx=(0,6))
            ttk.Button(ctl,text='Stop',style='Accent.TButton',width=6,command=self._stop_free_window).pack(side='left')
        self._filter_row(box,'Free access timer','Press a button to give every viewer free access for that many hours, for example for a free weekend or a special stream. It stops by itself, or press Stop. Your other settings stay as they are.',make_free,first=True)
        tk.Label(box,textvariable=self.paid_free_status,bg=FILTER_CARD,fg=PAL['accent'],font=(FONT_FAMILY,9,'bold'),anchor='w').pack(anchor='w',padx=20,pady=(0,14))
        self._start_free_tick()
        box=self._filter_section(paid,'Paid voice codes')
        self._filter_toggle(box,'Require payment','On: viewers need paid time (Bits / Kicks). Off: everybody can use everything for free.',self.paid_voices_enabled,first=True)
        scope_labels={'codes':'Voice codes only','all':'Everything (reading chat)'}
        self.paid_scope_label=tk.StringVar(value=scope_labels.get(self.paid_scope.get(),scope_labels['codes']))
        def make_scope(ctl):
            box_=ttk.Combobox(ctl,textvariable=self.paid_scope_label,values=list(scope_labels.values()),state='readonly',width=26); box_.pack()
            box_.bind('<<ComboboxSelected>>',lambda _e:self.paid_scope.set('all' if self.paid_scope_label.get()==scope_labels['all'] else 'codes'))
        self._filter_row(box,'What payment unlocks','Voice codes only: unpaid viewers are still read, in the normal voice, without the voice code (“Heart: hello”). Everything: unpaid viewers are not read aloud at all until they pay. Your own account is always read.',make_scope)
        self._filter_toggle(box,'Moderators use it for free','On: moderators never need paid time. Off: moderators pay like everyone else.',self.paid_free_mods)
        self._filter_toggle(box,'VIPs use it for free','On: VIPs never need paid time. Off: VIPs pay like everyone else.',self.paid_free_vips)
        sub_labels=self.PAID_SUB_LABELS
        self.paid_sub_label=tk.StringVar(value=sub_labels.get(self.paid_sub_mode.get(),sub_labels['pay']))
        def make_sub(ctl):
            box_=ttk.Combobox(ctl,textvariable=self.paid_sub_label,values=list(sub_labels.values()),state='readonly',width=22); box_.pack()
            box_.bind('<<ComboboxSelected>>',lambda _e:self.paid_sub_mode.set(next((k for k,v in sub_labels.items() if v==self.paid_sub_label.get()),'pay')))
        self._filter_row(box,'Subscribers','Free: subscribers never need paid time. Pay less: subscribers pay the lower prices set below. The subscriber badge arrives with every chat message, like the moderator and VIP badges.',make_sub)
        self._filter_row(box,'Price for 30 days','What a viewer pays to unlock 30 days: Bits cheered in Twitch chat, or Kicks gifted in Kick chat. Any amount counts in proportion (double the amount = double the time) and time adds up.',pair(self.paid_bits,self.paid_kicks))
        self._filter_row(box,'Subscriber price for 30 days','Only used when Subscribers is set to Pay less. Twitch subscribers pay this many Bits, Kick subscribers this many Kicks.',pair(self.paid_sub_bits,self.paid_sub_kicks))
        self._filter_row(box,'Minimum payment','Cheers and gifts under this amount are ignored, so 1-Bit spam does not create tiny bits of time. Set 1 to count everything.',pair(self.paid_min_bits,self.paid_min_kicks,inc=5))
        self._filter_row(box,'Maximum stored time','Paid time can never grow past this many days from today, however much a viewer pays. Time you add by hand is not limited.',spin(self.paid_max_days,1,3650,30,'days',width=7))
        trial_labels=self.PAID_TRIAL_LABELS
        self.paid_trial_label=tk.StringVar(value=trial_labels.get(self.paid_trial_mode.get(),trial_labels['off']))
        self.paid_trial_unit=tk.StringVar(value={'messages':'messages','minutes':'minutes'}.get(self.paid_trial_mode.get(),''))
        def make_trial(ctl):
            def changed(_e=None):
                mode=next((k for k,v in trial_labels.items() if v==self.paid_trial_label.get()),'off')
                self.paid_trial_mode.set(mode); self.paid_trial_unit.set({'messages':'messages','minutes':'minutes'}.get(mode,''))
            box_=ttk.Combobox(ctl,textvariable=self.paid_trial_label,values=list(trial_labels.values()),state='readonly',width=15); box_.pack(side='left'); box_.bind('<<ComboboxSelected>>',changed)
            ttk.Spinbox(ctl,from_=1,to=1000,increment=1,textvariable=self.paid_trial_amount,width=5).pack(side='left',padx=(8,0))
            tk.Label(ctl,textvariable=self.paid_trial_unit,bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),width=9,anchor='w').pack(side='left',padx=(6,0))
        self._filter_row(box,'Free trial for new viewers','Lets each viewer try the paid feature once before paying: their first messages that use it, or the minutes after the first one. !myvoice shows what is left.',make_trial)
        self._filter_toggle(box,'Reply in chat','Thank viewers when they pay, tell an unpaid viewer how to unlock (at most once per 30 minutes each, and one hint per platform every 15 seconds), and answer !myvoice with the time left. Needs you signed in to that platform.',self.paid_reply)
        self._filter_toggle(box,'Expiry reminder','When a viewer whose paid time is about to run out chats, tell them once how to keep it. Needs you signed in to that platform.',self.paid_remind)
        self._filter_row(box,'Remind when this many days are left','',spin(self.paid_remind_days,1,30,1,'days',width=6))
        for variable in (self.paid_voices_enabled,self.paid_free_mods,self.paid_free_vips,self.paid_scope,self.paid_bits,self.paid_kicks,self.paid_reply): variable.trace_add('write',lambda *_args:self._save_filter_settings())
        for variable in (self.paid_sub_mode,self.paid_sub_bits,self.paid_sub_kicks,self.paid_min_bits,self.paid_min_kicks,self.paid_max_days,self.paid_trial_mode,self.paid_trial_amount,self.paid_remind,self.paid_remind_days): variable.trace_add('write',lambda *_args:self._save_filter_settings())
        box=self._filter_section(paid,'Viewers with paid time')
        inner=tk.Frame(box,bg=FILTER_CARD); inner.pack(fill='x',padx=20,pady=18)
        tk.Label(inner,text='Your own channel account always has access. Add friends or mods below to give them voice codes for free.',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=700).pack(anchor='w',pady=(0,14))
        self.paid_add_name=tk.StringVar(); self.paid_add_platform=tk.StringVar(value='Twitch'); self.paid_add_days=tk.IntVar(value=30)
        form=tk.Frame(inner,bg=FILTER_CARD); form.pack(fill='x',pady=(0,16)); form.columnconfigure(0,weight=1)
        for column,text in enumerate(('Viewer name','Platform','Days')): tk.Label(form,text=text,bg=FILTER_CARD,fg=PAL['text'],font=(FONT_FAMILY,9,'bold')).grid(row=0,column=column,sticky='w',padx=(0 if column==0 else 12,0))
        name_entry=ttk.Entry(form,textvariable=self.paid_add_name); name_entry.grid(row=1,column=0,sticky='ew',pady=(5,0)); name_entry.bind('<Return>',lambda _event:self._add_paid_access())
        ttk.Combobox(form,textvariable=self.paid_add_platform,values=('Twitch','Kick'),state='readonly',width=9).grid(row=1,column=1,padx=(12,0),pady=(5,0))
        ttk.Spinbox(form,from_=1,to=3650,increment=1,textvariable=self.paid_add_days,width=7).grid(row=1,column=2,padx=(12,0),pady=(5,0))
        ttk.Button(form,text='Give access',style='Accent.TButton',command=self._add_paid_access).grid(row=1,column=3,padx=(12,0),pady=(5,0))
        self.paid_tree=ttk.Treeview(inner,columns=('viewer','platform','left'),show='headings',height=8,selectmode='extended')
        for column,text,width in (('viewer','Viewer',260),('platform','Platform',110),('left','Time left',200)):
            self.paid_tree.heading(column,text=text); self.paid_tree.column(column,width=width,anchor='w')
        self.paid_tree.pack(fill='x',pady=(0,12)); self.paid_tree_keys={}
        actions=tk.Frame(inner,bg=FILTER_CARD); actions.pack(anchor='w')
        ttk.Button(actions,text='Remove selected',style='Outline.TButton',command=self._remove_paid_access).pack(side='left')
        ttk.Button(actions,text='Add days to selected',style='Outline.TButton',command=lambda:self._adjust_paid_selected(1)).pack(side='left',padx=(8,0))
        ttk.Button(actions,text='Take days off selected',style='Outline.TButton',command=lambda:self._adjust_paid_selected(-1)).pack(side='left',padx=(8,0))
        ttk.Button(actions,text='Export history (CSV)',style='Outline.TButton',command=self._export_paid_history).pack(side='left',padx=(8,0))
        tk.Label(inner,text='The Days box above is also the number of days used by the add and take-off buttons. The history lists every payment and every change, so you can check who paid what.',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=700).pack(anchor='w',pady=(10,0))
        self._refresh_paid_voices()
        # --- Feed setup: which events the Event feed shows (Activity page and the OBS feed dock) ---
        feed_panel=ttk.Frame(card,style='Page.TFrame'); self.filter_feed_panel=feed_panel
        _hidden=self.settings.get('feed_hidden_groups',[]); _hidden=set(_hidden) if isinstance(_hidden,list) else set()
        self.feed_show={group:tk.BooleanVar(value=group not in _hidden) for group,_title,_desc in dashboard.FEED_GROUPS}
        box=self._filter_section(feed_panel,'Event feed')
        for index,(group,title,desc) in enumerate(dashboard.FEED_GROUPS):
            self._filter_toggle(box,title,desc,self.feed_show[group],first=index==0)
            self.feed_show[group].trace_add('write',lambda *_a:self._feed_setup_changed())
        tk.Label(box,text='Everything is shown by default. The totals at the top of Activity keep counting every event, even when it is hidden here. This setting applies to the Event feed in the app and in the OBS feed dock, so there is nothing to set up in the dock itself.',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=700).pack(anchor='w',padx=20,pady=(4,16))
        self._select_filter_tab('rules')
    def _feed_setup_changed(self):
        self._save_filter_settings()
        try: dashboard.render_feed(self)
        except Exception: pass
        self.dock_feed_sig=None   # makes the OBS feed dock pick up the change on its next refresh
    def _select_filter_tab(self,tab):
        if tab not in ('rules','words','paid','feed'): tab='rules'
        self.filter_tab.set(tab)
        panels={'rules':self.filter_rules_panel,'words':self.filter_words_panel,'paid':self.filter_paid_panel,'feed':self.filter_feed_panel}
        if tab=='paid': self._refresh_paid_voices()
        for panel in panels.values(): panel.pack_forget()
        panels[tab].pack(fill='x',anchor='n')
        for key,button in self.filter_tab_buttons.items():
            active=key==tab
            button.configure(bg=PAL['accent'] if active else PAL['raised'],activebackground=PAL['accent_hi'] if active else PAL['hover'],fg='#ffffff' if active else PAL['muted'],activeforeground='#ffffff')
    def _save_filter_settings(self):
        if not hasattr(self,'only_highlighted'): return
        try: minimum_length=max(1,int(self.min_length.get()))
        except (ValueError,tk.TclError):
            try: minimum_length=max(1,int(self.settings.get('filter_min_length',1)))
            except (TypeError,ValueError): minimum_length=1
        self.settings.update({'filter_read_username':self.read_username.get(),'filter_skip_links':self.skip_links.get(),'filter_skip_commands':self.skip_commands.get(),'filter_only_highlighted':self.only_highlighted.get(),'filter_skip_bots':self.skip_bots.get(),'filter_min_length':minimum_length,'timeout_seconds':self._timeout_seconds(),'filter_read_own_sent':self.read_own_sent.get(),'filter_voice_codes':self.voice_codes.get(),'filter_voicelist_command':self.voicelist_command.get() if hasattr(self,'voicelist_command') else True,'queue_skip_limit':self._queue_skip_limit(),'word_substitutions':list(self.word_substitutions) if hasattr(self,'word_substitutions') else []})
        if hasattr(self,'paid_voices_enabled'): self.settings.update({'paid_voices_enabled':self.paid_voices_enabled.get(),'paid_voices_scope':self.paid_scope.get(),'paid_free_mods':self.paid_free_mods.get(),'paid_free_vips':self.paid_free_vips.get(),'paid_bits_per_30':self._paid_price('bits'),'paid_kicks_per_30':self._paid_price('kicks'),'paid_voices_reply':self.paid_reply.get(),'paid_sub_mode':self.paid_sub_mode.get(),'paid_bits_per_30_subs':self._paid_int(self.paid_sub_bits,'paid_bits_per_30_subs',25,1,1000000),'paid_kicks_per_30_subs':self._paid_int(self.paid_sub_kicks,'paid_kicks_per_30_subs',25,1,1000000),'paid_min_bits':self._paid_int(self.paid_min_bits,'paid_min_bits',10,1,1000000),'paid_min_kicks':self._paid_int(self.paid_min_kicks,'paid_min_kicks',1,1,1000000),'paid_max_days':self._paid_int(self.paid_max_days,'paid_max_days',365,1,3650),'paid_trial_mode':self.paid_trial_mode.get(),'paid_trial_amount':self._paid_int(self.paid_trial_amount,'paid_trial_amount',5,1,1000),'paid_remind':self.paid_remind.get(),'paid_remind_days':self._paid_int(self.paid_remind_days,'paid_remind_days',3,1,30)})
        if hasattr(self,'blocklist_on'): self.settings['filter_blocklist']=self.blocklist_on.get()
        if hasattr(self,'feed_show'): self.settings['feed_hidden_groups']=[group for group,var in self.feed_show.items() if not var.get()]
        self._save_connection_settings()
    def _update_blocklist_status(self):
        if not hasattr(self,'blocklist_status'): return
        count=self.word_blocker.entries
        text=(f'{count} '+('entry' if count==1 else 'entries')+' loaded from blocked_words.txt.') if count else 'The list is empty, so no messages are blocked yet.'
        if self.blocked_word_hits: text+=f' {self.blocked_word_hits} '+('message' if self.blocked_word_hits==1 else 'messages')+' blocked this session.'
        self.blocklist_status.set(text)
    def _open_blocklist(self):
        self.word_blocker.ensure_file(); path=self.word_blocker.path
        try:
            if hasattr(os,'startfile'): os.startfile(path)
            elif sys.platform=='darwin': subprocess.Popen(['open',path])
            else: subprocess.Popen(['xdg-open',path])
        except Exception as exc: messagebox.showerror('Blocked words','Could not open the list: '+str(exc)+'\n\nThe file is here:\n'+path)
    def _reload_blocklist(self):
        self.word_blocker.load(); self._update_blocklist_status()
    def _import_blocklist(self):
        source=filedialog.askopenfilename(title='Import a word list (.txt)',filetypes=[('Text file','*.txt'),('All files','*.*')])
        if not source: return
        try: added=self.word_blocker.add_from_file(source)
        except OSError as exc:
            messagebox.showerror('Blocked words',str(exc)); return
        self._update_blocklist_status()
        self.set_status(f'Blocked words: {added} new '+('entry' if added==1 else 'entries')+' added.')
    def _refresh_word_substitutions(self):
        if not hasattr(self,'substitution_tree'): return
        if getattr(self,'_substitution_edit_index',None) is not None: self._cancel_substitution_edit()
        for child in self.substitution_tree.get_children(): self.substitution_tree.delete(child)
        for index,item in enumerate(self.word_substitutions):
            replacement='␠ (space)' if item['replacement'] and item['replacement'].isspace() else item['replacement']
            self.substitution_tree.insert('', 'end', iid=str(index), values=(item['word'],replacement))
    def _add_word_substitution(self):
        word=self.substitution_word.get().strip(); replacement_input=self.substitution_replacement.get()
        if not word:
            messagebox.showinfo('Word substitution','Enter a word or phrase to replace. Leave the replacement blank to remove it from speech.')
            return
        replacement=replacement_input.strip() or ' '
        index=getattr(self,'_substitution_edit_index',None)
        if index is not None and 0<=index<len(self.word_substitutions):
            # Editing a row: change it in place (the word itself may be renamed); drop any other row with the same word.
            self.word_substitutions[index]={'word':word,'replacement':replacement}
            self.word_substitutions=[item for position,item in enumerate(self.word_substitutions) if position==index or item['word'].casefold()!=word.casefold()]
        else:
            existing=next((item for item in self.word_substitutions if item['word'].casefold()==word.casefold()),None)
            if existing: existing['replacement']=replacement
            else: self.word_substitutions.append({'word':word,'replacement':replacement})
        self._cancel_substitution_edit(); self._refresh_word_substitutions(); self._save_filter_settings()
    def _load_selected_substitution(self,_event=None):
        selected=self.substitution_tree.selection()
        if not selected: return
        index=int(selected[0])
        if not 0<=index<len(self.word_substitutions): return
        item=self.word_substitutions[index]
        self._substitution_edit_index=index
        self.substitution_word.set(item['word'])
        self.substitution_replacement.set('' if item['replacement'].isspace() else item['replacement'])
        self.substitution_add_button.config(text='Save change'); self.substitution_cancel_button.grid()
    def _cancel_substitution_edit(self):
        self._substitution_edit_index=None
        self.substitution_word.set(''); self.substitution_replacement.set('')
        self.substitution_add_button.config(text='Add'); self.substitution_cancel_button.grid_remove()
        selection=self.substitution_tree.selection()
        if selection: self.substitution_tree.selection_remove(selection)
    def _remove_word_substitution(self):
        selected=self.substitution_tree.selection()
        if not selected: return
        index=int(selected[0])
        if 0<=index<len(self.word_substitutions):
            self.word_substitutions.pop(index)
            self._cancel_substitution_edit(); self._refresh_word_substitutions(); self._save_filter_settings()
    def _import_substitutions(self):
        source=filedialog.askopenfilename(title='Import replacements (.txt)',filetypes=[('Text file','*.txt'),('All files','*.*')])
        if not source: return
        try:
            with open(source,'r',encoding='utf-8-sig',errors='replace') as f: lines=f.read().splitlines()
        except OSError as exc:
            messagebox.showerror('Word substitutions',str(exc)); return
        added=changed=0
        for line in lines:
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            word,_separator,spoken=line.partition('=')
            word=word.strip(); replacement=spoken.strip() or ' '
            if not word: continue
            existing=next((item for item in self.word_substitutions if item['word'].casefold()==word.casefold()),None)
            if existing:
                if existing['replacement']!=replacement: existing['replacement']=replacement; changed+=1
            else:
                self.word_substitutions.append({'word':word,'replacement':replacement}); added+=1
        self._refresh_word_substitutions(); self._save_filter_settings()
        self.set_status(f'Replacements: {added} added, {changed} changed.')
    def _export_substitutions(self):
        path=filedialog.asksaveasfilename(title='Export replacements',defaultextension='.txt',initialfile='replacements.txt',filetypes=[('Text file','*.txt')])
        if not path: return
        try:
            with open(path,'w',encoding='utf-8',newline='\r\n') as f:
                f.write('# ChatVoice Local replacements: word = what is spoken instead (empty = removed from speech)\n')
                for item in self.word_substitutions:
                    f.write(item['word']+' = '+('' if item['replacement'].isspace() else item['replacement'])+'\n')
        except OSError as exc:
            messagebox.showerror('Word substitutions',str(exc)); return
        self.set_status(f'Replacements: {len(self.word_substitutions)} saved to {os.path.basename(path)}.')
    def _replace_words_for_speech(self,text):
        entries=sorted(self.word_substitutions,key=lambda entry:len(entry['word']),reverse=True)
        if not entries: return text
        # One capture group per entry; lastindex says which entry matched, so no case-folded dictionary lookup can miss.
        pattern=re.compile('|'.join('(?<!\\w)('+re.escape(item['word'])+')(?!\\w)' for item in entries),re.IGNORECASE)
        return pattern.sub(lambda match:entries[match.lastindex-1]['replacement'],text)
    # ---- Chat Moderation: automatic ban-evader handling (Twitch) ---------------------------------------------
    EVADER_LABELS={'nothing':'Do nothing','timeout':'Time out','ban':'Ban'}
    @staticmethod
    def _evader_choice(value,default):
        return value if value in ('nothing','timeout','ban') else default
    def _evader_timeout_seconds(self):
        try: return max(1,min(1209600,int(self.evader_timeout.get())))
        except (ValueError,tk.TclError):
            try: return max(1,min(1209600,int(self.settings.get('modrules_evader_timeout',600))))
            except (TypeError,ValueError): return 600
    def _save_modrules_settings(self):
        if not hasattr(self,'evader_enabled'): return
        self.settings.update({'modrules_evader_enabled':self.evader_enabled.get(),'modrules_evader_likely':self.evader_likely.get(),'modrules_evader_possible':self.evader_possible.get(),'modrules_evader_timeout':self._evader_timeout_seconds(),'modrules_evader_silence':self.evader_silence.get(),'modrules_evader_allow':list(self.evader_allow)})
        self._save_connection_settings()
    CHAT_TABS=('Chat Moderation','Chat controls','Channel rewards','Voice rewards','Emotes')
    VOICE_TABS=('Text to Speech','Create Speech','Audio output')
    def _make_tabs(self,key,page,title,description,names):
        """A page with a tab strip: returns {tab name: frame}. Tab state lives in self.tab_current[key]."""
        P=PAL
        for attr in ('tab_frames','tab_labels','tab_marks','tab_current'):
            if not hasattr(self,attr): setattr(self,attr,{})
        self._page_header(page,title,description,info=True)
        bar=tk.Frame(page,bg=PAGE_BG); bar.pack(fill='x')
        tk.Frame(page,bg=P['line'],height=1).pack(fill='x',pady=(0,18))
        body=ttk.Frame(page,style='Page.TFrame'); body.pack(fill='both',expand=True)
        frames=self.tab_frames[key]={}; labels=self.tab_labels[key]={}; marks=self.tab_marks[key]={}
        self.tab_current[key]=names[0]
        for name in names:
            cell=tk.Frame(bar,bg=PAGE_BG); cell.pack(side='left',padx=(0,6))
            label=tk.Label(cell,text=name,bg=PAGE_BG,fg=P['muted'],font=(FONT_FAMILY,10),padx=14,pady=8,cursor='hand2'); label.pack()
            mark=tk.Frame(cell,bg=PAGE_BG,height=2); mark.pack(fill='x')
            label.bind('<Button-1>',lambda e,n=name:self.show_tab(key,n))
            label.bind('<Enter>',lambda e,l=label:l.configure(fg=P['text']))
            label.bind('<Leave>',lambda e,l=label,n=name:l.configure(fg=P['text'] if n==self.tab_current[key] else P['muted']))
            labels[name]=label; marks[name]=mark
            frames[name]=ttk.Frame(body,style='Page.TFrame')
        return frames
    def show_tab(self,key,name,load=True):
        for frame in self.tab_frames[key].values(): frame.pack_forget()
        self.tab_frames[key][name].pack(fill='both',expand=True)
        self.tab_current[key]=name
        for tab in self.tab_frames[key]:
            active=tab==name
            self.tab_labels[key][tab].configure(fg=PAL['text'] if active else PAL['muted'],font=(FONT_FAMILY,10,'bold' if active else 'normal'))
            self.tab_marks[key][tab].configure(bg=PAL['accent'] if active else PAGE_BG)
        if load and key=='Chat': self._load_chat_tab()
    def _build_chat_page(self,page):
        """One page for everything chat-related: chat controls, channel points and moderation rules, as tabs."""
        frames=self._make_tabs('Chat',page,'Chat setup','Chat modes, channel rewards, voice rewards, emotes and automatic moderation rules, all in one place.',self.CHAT_TABS)
        self._tab_mode=True
        try:
            channel_tools.build_controls_page(self,frames['Chat controls'],PAL,FONT_FAMILY,PAGE_BG,ToggleSwitch)
            channel_tools.build_rewards_page(self,frames['Channel rewards'],PAL,FONT_FAMILY,PAGE_BG)
            self._build_reward_voices_page(frames['Voice rewards'])
            self._build_modrules_page(frames['Chat Moderation'])
            self._build_emotes_page(frames['Emotes'])
        finally: self._tab_mode=False
        self.show_tab('Chat','Chat Moderation',load=False)
    def _build_voice_page(self,page):
        """Text to Speech and Create Speech together, as tabs."""
        frames=self._make_tabs('Voice',page,'Voice setup','Pick the voices that read chat aloud, build new ones, and choose where the sound goes.',self.VOICE_TABS)
        self._tab_mode=True
        try:
            self._build_tts_page(frames['Text to Speech'])
            self._build_create_page(frames['Create Speech'])
            self._build_audio_page(frames['Audio output'])
        finally: self._tab_mode=False
        self.show_tab('Voice','Text to Speech',load=False)
    def _load_chat_tab(self):
        tab=self.tab_current.get('Chat')
        if tab=='Channel rewards': channel_tools.load_rewards(self)
        elif tab=='Chat controls': channel_tools.load_controls(self)
        elif tab=='Emotes': self._refresh_emote_status()
    # ---- Reward voices: channel point rewards and Custom Power-ups that read the viewer's text in a chosen voice ----
    def _build_reward_voices_page(self,page):
        P=PAL
        saved=self.settings.get('reward_voices') or {}
        self.reward_voice_on=tk.BooleanVar(value=bool(saved.get('enabled',True)))
        self.reward_rules=[r for r in (saved.get('rules') or []) if isinstance(r,dict) and r.get('reward') and r.get('voice')]
        self.seen_redeem_names=[]
        ttk.Label(page,text='Pick a voice for a channel point reward or a Custom Power-up. When a viewer redeems it, the text they type is read aloud in that voice. Turn on "Viewer must enter text" for the reward (or Power-up) in Twitch, then type its exact name below. Use * to match every redemption.',style='Subtitle.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(0,10))
        top=ttk.Frame(page,style='Page.TFrame'); top.pack(fill='x',pady=(0,10))
        ToggleSwitch(top,self.reward_voice_on,bg=P['bg']).pack(side='left')
        self.reward_voice_on.trace_add('write',lambda *_:self._save_reward_voices())
        ttk.Label(top,text='Read reward messages aloud in their own voice',style='Muted.TLabel').pack(side='left',padx=10)
        edit=ttk.Frame(page,style='Page.TFrame'); edit.pack(fill='x',pady=(0,6))
        self.reward_name_var=tk.StringVar(); self.reward_voice_var=tk.StringVar(value=self.voice.get()); self.reward_sayname=tk.BooleanVar(value=False)
        ttk.Label(edit,text='Reward / Power-up',style='Muted.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(edit,text='Voice',style='Muted.TLabel').grid(row=0,column=1,sticky='w',padx=(10,0))
        name_box=ttk.Combobox(edit,textvariable=self.reward_name_var,width=30,postcommand=lambda:name_box.configure(values=self._reward_name_choices()))
        name_box.grid(row=1,column=0,sticky='w')
        voice_box=ttk.Combobox(edit,textvariable=self.reward_voice_var,width=40,state='readonly',postcommand=lambda:voice_box.configure(values=self.builtin_voices+list(self.voice_catalog)))
        voice_box.grid(row=1,column=1,sticky='w',padx=(10,0))
        ttk.Checkbutton(edit,text="Say the viewer's name first",variable=self.reward_sayname).grid(row=1,column=2,sticky='w',padx=(12,0))
        buttons=ttk.Frame(page,style='Page.TFrame'); buttons.pack(fill='x',pady=(0,10))
        ttk.Button(buttons,text='Add / update',command=self._add_reward_rule).pack(side='left')
        ttk.Button(buttons,text='Test voice',style='Outline.TButton',command=lambda:self._test_with_voice(self.reward_voice_var.get() or self.voice.get(),'This is a test of the reward voice.')).pack(side='left',padx=8)
        ttk.Button(buttons,text='Remove selected',style='Outline.TButton',command=self._remove_reward_rule).pack(side='left')
        self.reward_tree=ttk.Treeview(page,columns=('reward','voice','name'),show='headings',height=8,selectmode='browse')
        for key,title,width in (('reward','Reward / Power-up',250),('voice','Voice',330),('name','Says name',90)):
            self.reward_tree.heading(key,text=title); self.reward_tree.column(key,width=width,anchor='w')
        self.reward_tree.pack(fill='x')
        self.reward_tree.bind('<<TreeviewSelect>>',self._pick_reward_rule)
        self.reward_note=tk.StringVar(value='Custom Power-ups need the Bits permission. If none arrive, log out of Twitch and log in again once to approve it.')
        ttk.Label(page,textvariable=self.reward_note,style='Muted.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(10,0))
        self._refresh_reward_tree()
    def _reward_name_choices(self):
        names=['*']
        try: names+=[str(r.get('title') or '') for r in getattr(self,'reward_rows',{}).values()]
        except Exception: pass
        names+=self.seen_redeem_names
        out=[]
        for n in names:
            if n and n not in out: out.append(n)
        return out
    def _refresh_reward_tree(self):
        self.reward_tree.delete(*self.reward_tree.get_children())
        for index,rule in enumerate(self.reward_rules):
            self.reward_tree.insert('', 'end', iid=str(index), values=(rule['reward'],rule['voice'],'Yes' if rule.get('say_name') else 'No'))
    def _save_reward_voices(self):
        self.settings['reward_voices']={'enabled':bool(self.reward_voice_on.get()),'rules':self.reward_rules}
        self._save_connection_settings()
    def _add_reward_rule(self):
        name=(self.reward_name_var.get() or '').strip(); voice=(self.reward_voice_var.get() or '').strip()
        if not name or not voice:
            messagebox.showinfo('Reward voices','Type the reward or Power-up name and pick a voice first.'); return
        rule={'reward':name,'voice':voice,'say_name':bool(self.reward_sayname.get())}
        for index,old in enumerate(self.reward_rules):
            if old['reward'].lower()==name.lower(): self.reward_rules[index]=rule; break
        else: self.reward_rules.append(rule)
        self._save_reward_voices(); self._refresh_reward_tree()
        self.reward_note.set('Saved: "'+name+'" will be read in '+voice+'.')
    def _remove_reward_rule(self):
        picked=self.reward_tree.selection()
        if not picked: return
        try: del self.reward_rules[int(picked[0])]
        except (ValueError,IndexError): return
        self._save_reward_voices(); self._refresh_reward_tree()
    def _pick_reward_rule(self,_event=None):
        picked=self.reward_tree.selection()
        if not picked: return
        try: rule=self.reward_rules[int(picked[0])]
        except (ValueError,IndexError): return
        self.reward_name_var.set(rule['reward']); self.reward_voice_var.set(rule['voice']); self.reward_sayname.set(bool(rule.get('say_name')))
    def _maybe_speak_redemption(self,platform,user,metadata):
        """A channel point reward or Custom Power-up was redeemed: read the viewer's text in the voice chosen for it."""
        if metadata.get('feed') not in ('points','powerup'): return
        title=str(metadata.get('reward') or '').strip()
        if title and title not in self.seen_redeem_names: self.seen_redeem_names.append(title); del self.seen_redeem_names[:-50]
        if not self.reward_voice_on.get(): return
        rule=next((r for r in self.reward_rules if r['reward'].lower()==title.lower()),None) or next((r for r in self.reward_rules if r['reward']=='*'),None)
        if not rule: return
        text=str(metadata.get('input') or '').strip()
        if not text: return
        if (str(platform).title(),self._normalize_username(user)) in self.ignored_users: return
        if self.blocklist_on.get() and self.word_blocker.active and self.word_blocker.matches(text):
            self.blocked_word_hits+=1; self._update_blocklist_status(); return
        text=re.sub(r'https?://\S+|www\.\S+','',text)
        text=self._replace_words_for_speech(text)
        text=re.sub(r'\s+',' ',text).strip()[:MAX_MESSAGE_LENGTH]
        if not text: return
        if rule.get('say_name') and user: text=user+' says: '+text
        chosen=rule['voice']
        if chosen not in self.builtin_voices and chosen not in self.voice_catalog: chosen=self.voice.get()
        settings=self._voice_config()
        settings['mode']='Take turns per message'; settings['selected_voice']=chosen; settings['voices'][0]=chosen
        settings['route']=self._route_for('reward'); settings['kind']='reward'
        info=self.voice_catalog.get(chosen)
        if info and info.get('kind')=='preset': settings['voice_profiles'][chosen]=info
        self._queue_speech((text,platform,user,settings),'message')
    # ---- Audio output: what you hear vs what OBS hears ----
    def _build_audio_page(self,page):
        P=PAL; saved=self.settings.get('audio_out') or {}
        self.audio_chat_me=tk.BooleanVar(value=bool(saved.get('chat_me',True))); self.audio_chat_stream=tk.BooleanVar(value=bool(saved.get('chat_stream',False)))
        self.audio_reward_me=tk.BooleanVar(value=bool(saved.get('reward_me',True))); self.audio_reward_stream=tk.BooleanVar(value=bool(saved.get('reward_stream',True)))
        self.reward_ignore_clear=tk.BooleanVar(value=bool(saved.get('reward_ignore_clear',False))); self.reward_ignore_pause=tk.BooleanVar(value=bool(saved.get('reward_ignore_pause',False))); self.reward_ignore_mute=tk.BooleanVar(value=bool(saved.get('reward_ignore_mute',False)))
        self.audio_me_name=tk.StringVar(value=saved.get('me') or 'System default'); self.audio_stream_name=tk.StringVar(value=saved.get('stream') or 'Not set')
        self.audio_note=tk.StringVar(value='')
        self.engine.proxy=stream_proxy.ProxyClient(self._stream_proxy_command,notify=lambda message:self.engine.notify(message),cwd=APP_DIR)
        ttk.Label(page,text='Choose who hears what. Chat can stay on your own speakers while channel point and Power-up voices also go to the stream. Only have one audio output? Pick "ChatVoice Stream Audio" as the stream output and capture that window in OBS with Application Audio Capture. Prefer a virtual audio cable (the free VB-CABLE works well)? Pick that device instead.',style='Subtitle.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(0,12))
        dev=ttk.Frame(page,style='Page.TFrame'); dev.pack(fill='x',pady=(0,12))
        ttk.Label(dev,text='My speakers / headphones',style='Muted.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(dev,text='Stream output (what OBS captures)',style='Muted.TLabel').grid(row=2,column=0,sticky='w',pady=(8,0))
        self.audio_me_box=ttk.Combobox(dev,textvariable=self.audio_me_name,state='readonly',width=52); self.audio_me_box.grid(row=1,column=0,sticky='w')
        self.audio_stream_box=ttk.Combobox(dev,textvariable=self.audio_stream_name,state='readonly',width=52); self.audio_stream_box.grid(row=3,column=0,sticky='w')
        ttk.Button(dev,text='Refresh devices',style='Outline.TButton',command=self._refresh_audio_devices).grid(row=1,column=1,sticky='w',padx=10)
        ttk.Button(dev,text='Show stream audio window',style='Outline.TButton',command=self._show_stream_proxy).grid(row=3,column=1,sticky='w',padx=10)
        for box in (self.audio_me_box,self.audio_stream_box): box.bind('<<ComboboxSelected>>',lambda _e:self._apply_audio_settings())
        grid=ttk.Frame(page,style='Page.TFrame'); grid.pack(fill='x',pady=(0,10))
        ttk.Label(grid,text='',style='Muted.TLabel').grid(row=0,column=0)
        ttk.Label(grid,text='Heard on my speakers',style='Muted.TLabel').grid(row=0,column=1,padx=14)
        ttk.Label(grid,text='Heard on the stream',style='Muted.TLabel').grid(row=0,column=2,padx=14)
        for row,(title,mine,stream) in enumerate((('Chat voices',self.audio_chat_me,self.audio_chat_stream),('Channel point and Power-up voices',self.audio_reward_me,self.audio_reward_stream)),start=1):
            ttk.Label(grid,text=title).grid(row=row,column=0,sticky='w',pady=4)
            ttk.Checkbutton(grid,variable=mine,command=self._apply_audio_settings).grid(row=row,column=1)
            ttk.Checkbutton(grid,variable=stream,command=self._apply_audio_settings).grid(row=row,column=2)
        guard=ttk.Frame(page,style='Page.TFrame'); guard.pack(fill='x',pady=(0,10))
        ttk.Label(guard,text='Channel point and Power-up voices ignore these shortcuts (they keep playing, also on the stream)',style='Muted.TLabel').pack(anchor='w')
        guard_row=ttk.Frame(guard,style='Page.TFrame'); guard_row.pack(anchor='w',pady=(4,0))
        for text,variable in (('Ctrl+7  clear queue',self.reward_ignore_clear),('Ctrl+8  pause',self.reward_ignore_pause),('Ctrl+9  mute',self.reward_ignore_mute)):
            ttk.Checkbutton(guard_row,text=text,variable=variable,command=self._apply_audio_settings).pack(side='left',padx=(0,18))
        tests=ttk.Frame(page,style='Page.TFrame'); tests.pack(fill='x',pady=(0,10))
        ttk.Button(tests,text='Test my speakers',style='Outline.TButton',command=lambda:self._test_audio_route(('me',))).pack(side='left')
        ttk.Button(tests,text='Test stream output',style='Outline.TButton',command=lambda:self._test_audio_route(('stream',))).pack(side='left',padx=8)
        ttk.Label(page,textvariable=self.audio_note,style='Muted.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(0,10))
        ttk.Label(page,text='Setup for OBS with one output (nothing to install): 1) Pick "ChatVoice Stream Audio" as the stream output above; a small window with that name opens. 2) In OBS add a source: Application Audio Capture, window "ChatVoice Stream Audio". 3) Voices ticked under "Heard on the stream" play from that window, so you hear them on your speakers and OBS gets only them. Voices ticked only for speakers never reach OBS. Keep that window open (minimizing it is fine).\n\nSetup for OBS with a virtual cable: 1) Install VB-CABLE (vb-audio.com/Cable). 2) Pick "CABLE Input" as the stream output above. 3) In OBS add a source: Audio Output Capture, device "CABLE Output". 4) Leave the chat voices unticked under "Heard on the stream" if you do not want chat on stream. Tip: in OBS you can mute that source or set Audio Monitoring to "Monitor Off" so you do not hear it twice.',style='Subtitle.TLabel',wraplength=720,justify='left').pack(anchor='w')
        self._refresh_audio_devices()
    def _refresh_audio_devices(self):
        self.audio_devices=dict(self.engine.list_output_devices())
        names=list(self.audio_devices)
        self.audio_me_box.configure(values=['System default']+names); self.audio_stream_box.configure(values=['Not set',stream_proxy.PROXY_CHOICE]+names)
        self._apply_audio_settings()
    def _apply_audio_settings(self):
        me=self.audio_me_name.get(); stream=self.audio_stream_name.get(); devices=getattr(self,'audio_devices',{})
        via_window=stream==stream_proxy.PROXY_CHOICE
        self.engine.devices={'me':devices.get(me) if me!='System default' else None,'stream':('proxy' if via_window else devices.get(stream)) if stream!='Not set' else None}
        client=self.engine.proxy
        if client is not None:
            if via_window: threading.Thread(target=client.start,args=(self.engine.devices['me'],),daemon=True).start()
            else: client.close()
        notes=[]
        if me not in ('System default',) and me not in devices: notes.append('"'+me+'" is not available right now, so your speakers use the system default.')
        if stream!='Not set' and not via_window and stream not in devices: notes.append('"'+stream+'" is not available right now. Plug it in or install the virtual cable, then click Refresh devices.')
        if (self.audio_chat_stream.get() or self.audio_reward_stream.get()) and stream=='Not set': notes.append('Stream is ticked but no stream output is chosen yet, so that sound plays on your speakers.')
        if not (self.audio_chat_me.get() or self.audio_chat_stream.get()) or not (self.audio_reward_me.get() or self.audio_reward_stream.get()): notes.append('A row with nothing ticked plays on your speakers.')
        if via_window and (self.audio_chat_stream.get() or self.audio_reward_stream.get()): notes.append('Stream sound plays from the ChatVoice Stream Audio window on your speakers, so you hear it too and OBS captures only that window.')
        if stream!='Not set' and stream==me: notes.append('Both devices are the same, so the sound plays once and OBS would hear everything on it.')
        self.audio_note.set(' '.join(notes))
        self.settings['audio_out']={'me':me,'stream':stream,'chat_me':bool(self.audio_chat_me.get()),'chat_stream':bool(self.audio_chat_stream.get()),'reward_me':bool(self.audio_reward_me.get()),'reward_stream':bool(self.audio_reward_stream.get()),'reward_ignore_clear':bool(self.reward_ignore_clear.get()),'reward_ignore_pause':bool(self.reward_ignore_pause.get()),'reward_ignore_mute':bool(self.reward_ignore_mute.get())}
        self.engine.reward_ignore_mute=bool(self.reward_ignore_mute.get())
        self._save_connection_settings()
    def _stream_proxy_command(self,port,token,device):
        base=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,os.path.join(APP_DIR,'main.py')]
        return base+['--stream-proxy','--port',str(port),'--token',token,'--device',str(device)]
    def _show_stream_proxy(self):
        if self.audio_stream_name.get()!=stream_proxy.PROXY_CHOICE:
            messagebox.showinfo('Stream audio window','Pick "'+stream_proxy.PROXY_CHOICE+'" as the stream output first.'); return
        def work():
            client=self.engine.proxy
            if client.running() or client.start(self.engine.devices.get('me')): client.show_window()
        threading.Thread(target=work,daemon=True).start()
    def _route_for(self,category):
        """Where a kind of speech plays: ('me',), ('stream',) or both. 'chat' or 'reward'."""
        try:
            mine=self.audio_chat_me.get() if category=='chat' else self.audio_reward_me.get()
            stream=self.audio_chat_stream.get() if category=='chat' else self.audio_reward_stream.get()
        except Exception: return ('me',)
        route=tuple(t for t,on in (('me',mine),('stream',stream)) if on)
        return route or ('me',)
    def _test_audio_route(self,route):
        cfg=self._voice_config(); cfg['route']=route
        where='your speakers' if route==('me',) else 'the stream output'
        self._queue_test_speech('This is a test of '+where+'.',cfg)
    def _build_modrules_page(self,page):
        self._page_header(page,'Chat Moderation','Rules that moderate chat for you while this app is running. Switch each rule on or off. Nothing here is active until you turn it on.',info=True)
        holder=ttk.Frame(page,style='Page.TFrame'); holder.pack(fill='both',expand=True)
        canvas=tk.Canvas(holder,bg=PAGE_BG,highlightthickness=0)
        scrollbar=ttk.Scrollbar(holder,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left',fill='both',expand=True)
        card=ttk.Frame(canvas,style='Page.TFrame')
        body_window=canvas.create_window((0,0),window=card,anchor='nw')
        def update_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            bounds=canvas.bbox('all')
            needs=bool(bounds and bounds[3]-bounds[1]>canvas.winfo_height())
            if needs and not scrollbar.winfo_manager(): scrollbar.pack(side='right',fill='y',before=canvas)
            elif not needs and scrollbar.winfo_manager(): scrollbar.pack_forget()
        card.bind('<Configure>',update_scroll)
        canvas.bind('<Configure>',lambda event:(canvas.itemconfigure(body_window,width=event.width),update_scroll()))
        def scroll_page(event):
            if getattr(self,'current_page','')!='Chat': return
            try: target=canvas.winfo_containing(event.x_root,event.y_root)
            except (KeyError,tk.TclError): return
            if target and str(target).startswith(str(canvas)): canvas.yview_scroll(int(-event.delta/120),'units')
        canvas.bind_all('<MouseWheel>',scroll_page,add='+')
        # --- Ban evaders ---
        box=self._filter_section(card,'Ban evaders (Twitch)')
        self.evader_status=tk.StringVar(value='Starts when Twitch chat is connected.')
        self._filter_toggle(box,'Automatically act on ban evaders','Twitch flags chatters who look like a banned person on a new account. When this is on, the app bans or times them out for you, right when they type. Needs your Twitch account to be the broadcaster or a moderator of the channel. Twitch chat only: Kick has no ban-evader detection.',self.evader_enabled,first=True)
        def action_row(var,title,desc):
            label_var=tk.StringVar(value=self.EVADER_LABELS[var.get()])
            def make(ctl):
                combo=ttk.Combobox(ctl,textvariable=label_var,values=list(self.EVADER_LABELS.values()),state='readonly',width=14); combo.pack()
                combo.bind('<<ComboboxSelected>>',lambda _e:var.set(next(k for k,v in self.EVADER_LABELS.items() if v==label_var.get())))
            self._filter_row(box,title,desc,make)
        action_row(self.evader_likely,'Likely ban evaders','Twitch is fairly sure this is a banned person on a new account.')
        action_row(self.evader_possible,'Possible ban evaders','Twitch is less sure. Be careful: innocent people can be flagged here, so “Do nothing” or “Time out” is the safer choice.')
        self.evader_timeout_text=tk.StringVar()
        def refresh_timeout_text(*_a):
            try: self.evader_timeout_text.set('= '+self._format_duration(max(1,int(self.evader_timeout.get()))))
            except (ValueError,tk.TclError): self.evader_timeout_text.set('')
        def make_timeout(ctl):
            ttk.Spinbox(ctl,from_=1,to=1209600,increment=60,textvariable=self.evader_timeout,width=9).pack(side='left')
            tk.Label(ctl,text='seconds',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9)).pack(side='left',padx=(8,0))
            tk.Label(ctl,textvariable=self.evader_timeout_text,bg=FILTER_CARD,fg=PAL['link'],font=(FONT_FAMILY,9,'bold'),width=11,anchor='w').pack(side='left',padx=(10,0))
        self.evader_timeout.trace_add('write',refresh_timeout_text); refresh_timeout_text()
        self._filter_row(box,'Timeout length','Used when an option above is set to “Time out”.',make_timeout)
        self._filter_toggle(box,'Never read suspected evaders aloud','Their messages stay in Activity but are not spoken, even when you chose “Do nothing” (this also covers people Twitch has restricted).',self.evader_silence)
        tk.Frame(box,bg=PAL['line'],height=1).pack(fill='x',padx=20)
        tk.Label(box,textvariable=self.evader_status,bg=FILTER_CARD,fg=PAL['accent'],font=(FONT_FAMILY,9,'bold'),anchor='w',justify='left',wraplength=640).pack(anchor='w',padx=20,pady=14)
        for variable in (self.evader_likely,self.evader_possible,self.evader_timeout,self.evader_silence): variable.trace_add('write',lambda *_a:self._save_modrules_settings())
        self.evader_enabled.trace_add('write',lambda *_a:(self._save_modrules_settings(),self._evader_enabled_changed()))
        # --- Never act on these accounts ---
        box=self._filter_section(card,'Never act on these accounts')
        inner=tk.Frame(box,bg=FILTER_CARD); inner.pack(fill='x',padx=20,pady=18)
        tk.Label(inner,text='Twitch flags can be wrong. Add the login name of a friend or a regular here and they are never banned or timed out by this rule. When you unban someone from the list below, they are added here automatically.',bg=FILTER_CARD,fg=PAL['muted'],font=(FONT_FAMILY,9),justify='left',anchor='w',wraplength=700).pack(anchor='w',pady=(0,12))
        form=tk.Frame(inner,bg=FILTER_CARD); form.pack(fill='x',pady=(0,12))
        self.evader_allow_name=tk.StringVar()
        entry=ttk.Entry(form,textvariable=self.evader_allow_name); entry.pack(side='left',fill='x',expand=True)
        entry.bind('<Return>',lambda _e:self._evader_allow_add())
        ttk.Button(form,text='Add',style='Accent.TButton',command=self._evader_allow_add).pack(side='left',padx=(12,0))
        self.evader_allow_tree=ttk.Treeview(inner,columns=('name',),show='headings',height=4,selectmode='browse')
        self.evader_allow_tree.heading('name',text='Twitch login'); self.evader_allow_tree.column('name',width=300,anchor='w')
        self.evader_allow_tree.pack(fill='x',pady=(0,12))
        ttk.Button(inner,text='Remove selected',style='Outline.TButton',command=self._evader_allow_remove).pack(anchor='w')
        self._evader_allow_refresh()
        # --- Recent automatic actions ---
        box=self._filter_section(card,'Recent automatic actions')
        inner=tk.Frame(box,bg=FILTER_CARD); inner.pack(fill='x',padx=20,pady=18)
        self.evader_tree=ttk.Treeview(inner,columns=('time','user','verdict','action','result'),show='headings',height=7,selectmode='browse')
        for key,title,width in (('time','Time',70),('user','User',150),('verdict','Twitch says',90),('action','Action',90),('result','Result',260)):
            self.evader_tree.heading(key,text=title); self.evader_tree.column(key,width=width,anchor='w')
        self.evader_tree.pack(fill='x',pady=(0,12))
        buttons=tk.Frame(inner,bg=FILTER_CARD); buttons.pack(anchor='w')
        ttk.Button(buttons,text='Unban selected (and never act on them again)',style='Accent.TButton',command=self._evader_unban_selected).pack(side='left')
        ttk.Button(buttons,text='Clear list',style='Outline.TButton',command=self._evader_log_clear).pack(side='left',padx=(8,0))
        self._evader_log_refresh()
    def _evader_enabled_changed(self):
        if self.evader_enabled.get() and self.evader_available is False:
            self.set_status('Ban-evader protection is on but not active yet: '+self.evader_status.get().replace('Not active: ',''))
        elif self.evader_enabled.get() and self.evader_available is None:
            self.set_status('Ban-evader protection is on. It starts when Twitch chat is connected.')
    def _evader_status_update(self,reason,meta):
        channel=(meta or {}).get('channel') or ''
        self.evader_available=not reason
        if reason:
            self.evader_status.set('Not active: '+reason)
            if self.evader_enabled.get(): self._report('Ban-evader protection is not active: '+reason)
        else: self.evader_status.set('Active: watching #'+channel+' for ban evaders.')
    def _evader_mark(self,platform,user_id):
        """'banned' / 'silenced' while a flagged chatter's messages must not be shown / read; None otherwise."""
        if str(platform).title()!='Twitch' or not user_id: return None
        item=self.evader_marks.get(str(user_id))
        if not item: return None
        if item[0]<time.time(): self.evader_marks.pop(str(user_id),None); return None
        return item[1]
    def _handle_suspicious(self,user,text,meta):
        if not self.evader_enabled.get(): return
        user_id=str(meta.get('user_id') or ''); broadcaster_id=str(meta.get('broadcaster_id') or '')
        evaluation=str(meta.get('evaluation') or 'unknown').lower(); status=str(meta.get('low_trust_status') or '').lower()
        login=self._normalize_username(meta.get('login') or user)
        if not user_id or user_id==str(self.twitch_user_id or '') or user_id==broadcaster_id or login in self.evader_allow: return
        now=time.time()
        if len(self.evader_marks)>500: self.evader_marks={k:v for k,v in self.evader_marks.items() if v[0]>now}
        if len(self.evader_recent)>500: self.evader_recent={k:v for k,v in self.evader_recent.items() if now-v<600}
        action={'likely':self.evader_likely.get(),'possible':self.evader_possible.get()}.get(evaluation,'nothing')
        silent=self.evader_silence.get() and (evaluation=='likely' or status=='restricted' or action!='nothing')
        if silent: self.evader_marks[user_id]=(now+600,'silenced')
        if action=='nothing' or not broadcaster_id: return
        key=(broadcaster_id,user_id)
        if now-self.evader_recent.get(key,0)<120: return  # one attempt at a time; a flagged chatter sends many messages
        self.evader_recent[key]=now
        duration=self._evader_timeout_seconds() if action=='timeout' else None
        threading.Thread(target=self._evader_act,args=(broadcaster_id,user_id,user,login,evaluation,action,duration),daemon=True).start()
    def _evader_act(self,broadcaster_id,user_id,name,login,evaluation,action,duration):
        reason='Suspected ban evasion ('+evaluation+'): automatic action by ChatVoice Local'
        entry={'time':time.strftime('%H:%M:%S'),'user':name,'login':login,'user_id':user_id,'broadcaster_id':broadcaster_id,'verdict':evaluation,'action':action,'ok':False,'result':''}
        try:
            q=urllib.parse.urlencode({'broadcaster_id':broadcaster_id,'moderator_id':self.twitch_user_id})
            data={'user_id':user_id,'reason':reason}
            if duration: data['duration']=duration
            self._twitch_helix('POST','/moderation/bans?'+q,{'data':data})
            entry['ok']=True; entry['result']='Banned' if action=='ban' else 'Timed out '+self._format_duration(duration)
        except Exception as exc:
            text=str(exc)
            if 'already' in text.lower(): entry['ok']=True; entry['result']='Already banned or timed out'
            else: entry['result']='Failed: '+text[:160]
        self.ui_events.put(('evader_log',entry))
    def _evader_logged(self,entry):
        self.evader_log.append(entry); del self.evader_log[:-200]
        name=entry['user']
        if entry['ok']:
            if entry['action']=='ban':
                self.evader_marks[str(entry['user_id'])]=(time.time()+600,'banned')
                self._purge_banned('Twitch',name)
            self._report(('Auto-banned ' if entry['action']=='ban' else 'Auto-timeout for ')+name+' on Twitch (Twitch: '+entry['verdict']+' ban evader).'+('' if entry['action']=='ban' else ' '+entry['result']+'.'))
        else: self._report('Auto '+('ban' if entry['action']=='ban' else 'timeout')+' of '+name+' failed: '+entry['result'])
        self._evader_log_refresh()
    def _evader_log_refresh(self):
        if not hasattr(self,'evader_tree'): return
        self.evader_tree.delete(*self.evader_tree.get_children())
        for index in range(len(self.evader_log)-1,-1,-1):
            e=self.evader_log[index]
            self.evader_tree.insert('','end',iid=str(index),values=(e['time'],e['user'],e['verdict'].title(),self.EVADER_LABELS.get(e['action'],e['action']),e['result']))
    def _evader_log_clear(self):
        self.evader_log=[]; self._evader_log_refresh()
    def _evader_unban_selected(self):
        selection=self.evader_tree.selection()
        if not selection: messagebox.showinfo('Chat Moderation','Select a row in the list first.'); return
        entry=self.evader_log[int(selection[0])]
        if not entry['ok']: messagebox.showinfo('Chat Moderation','That action did not go through, so there is nothing to undo.'); return
        if not messagebox.askyesno('Unban '+entry['user'],'Unban '+entry['user']+' on Twitch and never ban or time them out automatically again?'): return
        self._evader_allow_add(entry['login'])
        self.evader_marks.pop(str(entry['user_id']),None)
        def run():
            try:
                q=urllib.parse.urlencode({'broadcaster_id':entry['broadcaster_id'],'moderator_id':self.twitch_user_id,'user_id':entry['user_id']})
                self._twitch_helix('DELETE','/moderation/bans?'+q)
                self._report('Unbanned '+entry['user']+' on Twitch. They are on the never-act list now.')
            except Exception as exc: self._report('Twitch unban failed: '+str(exc))
        threading.Thread(target=run,daemon=True).start()
    def _evader_allow_add(self,name=None):
        login=self._normalize_username(name if name is not None else self.evader_allow_name.get())
        if not login: return
        if login not in self.evader_allow: self.evader_allow=sorted(self.evader_allow+[login]); self._save_modrules_settings()
        self.evader_allow_name.set(''); self._evader_allow_refresh()
    def _evader_allow_remove(self):
        selection=self.evader_allow_tree.selection()
        if not selection: return
        login=str(selection[0])
        self.evader_allow=[n for n in self.evader_allow if n!=login]; self._save_modrules_settings(); self._evader_allow_refresh()
    def _evader_allow_refresh(self):
        self.evader_allow_tree.delete(*self.evader_allow_tree.get_children())
        for login in self.evader_allow: self.evader_allow_tree.insert('','end',iid=login,values=(login,))
    def _build_blocked_page(self,page):
        self._page_header(page,'Blocked users','Each platform and username pair is separate. Block on Twitch without affecting the same name on Kick, or the other way around.',info=True)
        card=self._card(page,'Blocked from TTS')
        self.blocked_count=tk.StringVar(value='0 platform blocks')
        ttk.Label(card,text='Click a username in Activity to block that platform, or add a name here. These blocks only stop TTS; they do not ban users.',style='Muted.TLabel',wraplength=800).pack(anchor='w',pady=(0,9))
        add_row=ttk.Frame(card,style='Panel.TFrame'); add_row.pack(fill='x',pady=(0,12))
        self.ignore_username=tk.StringVar()
        self.ignore_platform=tk.StringVar(value='Both')
        self.ignore_entry=ttk.Entry(add_row,textvariable=self.ignore_username,width=28); self.ignore_entry.pack(side='left',padx=(0,8))
        self.ignore_entry.bind('<Return>',lambda _event:self.add_ignored_user())
        ttk.Combobox(add_row,textvariable=self.ignore_platform,state='readonly',width=10,values=['Both','Twitch','Kick']).pack(side='left',padx=(0,8))
        ttk.Button(add_row,text='Block username',command=self.add_ignored_user).pack(side='left')
        top=ttk.Frame(card,style='Panel.TFrame'); top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='Search usernames').pack(side='left')
        self.blocked_search=tk.StringVar()
        search=ttk.Entry(top,textvariable=self.blocked_search,width=34); search.pack(side='left',padx=9)
        search.bind('<KeyRelease>',self._refresh_blocked_list)
        ttk.Label(top,textvariable=self.blocked_count,style='Muted.TLabel').pack(side='right')
        self.blocked_list=tk.Listbox(card,height=16,selectmode='browse',exportselection=False)
        self.blocked_list.pack(fill='both',expand=True,pady=(2,10))
        actions=ttk.Frame(card,style='Panel.TFrame'); actions.pack(fill='x')
        ttk.Button(actions,text='Unblock selected platform',command=self._unblock_selected_user).pack(side='left')
        self.undo_block_button=ttk.Button(actions,text='Undo last block',command=self._undo_last_block,state='disabled'); self.undo_block_button.pack(side='left',padx=8)
        ttk.Button(actions,text='Export backup…',command=self.export_backup).pack(side='right')
        ttk.Button(actions,text='Import backup…',command=self.import_backup).pack(side='right',padx=8)
        self._refresh_ignored_users()
    def _unblock_selected_user(self):
        selected=self.blocked_list.curselection()
        if not selected:
            messagebox.showinfo('Unblock user','Select a username from the list first.')
            return
        platform,name=self.blocked_rows[selected[0]]
        self.unblock_ignored_user(name,platform)
    def _undo_last_block(self):
        keys=self.last_blocked_user
        if not keys: return
        for key in keys: self.ignored_users.discard(key)
        self.last_blocked_user=None
        self._save_ignored_users(); self._refresh_ignored_users()
        self.set_status('Undid the latest block for '+', '.join(self._blocked_label(*key) for key in keys)+'.')
    def export_backup(self):
        path=filedialog.asksaveasfilename(title='Export ChatVoice Local backup',defaultextension='.json',initialfile='ChatVoice_Local_Backup.json',filetypes=[('JSON backup','*.json')])
        if not path: return
        try:
            app_dir=APP_DIR
            presets={}
            try:
                with open(os.path.join(app_dir,'voice_presets.json'),'r',encoding='utf-8') as f: presets=json.load(f)
            except (OSError,json.JSONDecodeError): pass
            settings={key:value for key,value in self.settings.items() if not any(secret in key.casefold() for secret in ('token','secret','password')) and key!='twitch_client_id'}
            settings.update({'twitch_channel':self.channel.get().strip(),'kick_channel':self.kick_channel.get().strip()})
            backup={'format':'ChatVoice Local backup','version':3,'settings':settings,'blocked_users':[{'platform':platform,'username':name} for platform,name in sorted(self.ignored_users)],'voice_presets':presets}
            with open(path,'w',encoding='utf-8') as f: json.dump(backup,f,ensure_ascii=False,indent=2)
            self.set_status('Backup exported. Twitch sign-in tokens are not included.')
            messagebox.showinfo('Backup exported','Settings, blocked users, and voice presets were saved. Sign-in tokens are not included.')
        except Exception as e: messagebox.showerror('Export backup',str(e))
    def import_backup(self):
        path=filedialog.askopenfilename(title='Import ChatVoice Local backup',filetypes=[('JSON backup','*.json'),('All files','*.*')])
        if not path: return
        try:
            with open(path,'r',encoding='utf-8') as f: backup=json.load(f)
            if not isinstance(backup,dict) or backup.get('format') not in ('ChatVoice Local backup',LEGACY_BACKUP_FORMAT): raise ValueError('This file is not a ChatVoice Local backup.')
            settings=backup.get('settings',{}); users=backup.get('blocked_users',[]); presets=backup.get('voice_presets',{})
            if not isinstance(settings,dict) or not isinstance(users,list) or not isinstance(presets,dict): raise ValueError('The backup contents are not valid.')
            if not messagebox.askyesno('Import backup','Apply saved channel settings, merge platform-specific blocked users and voice presets? Sign-in tokens are not imported.'):
                return
            _access_before=dict(self.paid_voice_access); _history_before=list(self.paid_history)
            self.settings.update({key:value for key,value in settings.items() if not any(secret in key.casefold() for secret in ('token','secret','password')) and key!='twitch_client_id'})
            _merged=self._load_paid_voice_access()
            for _key,_item in _access_before.items():
                if _key not in _merged or _item.get('expires',0)>_merged[_key].get('expires',0): _merged[_key]=_item
            self.settings['paid_voice_access']=_merged
            _lines={json.dumps(item,sort_keys=True):item for item in _history_before+self._load_paid_history(self.settings)}
            self.settings['paid_history']=sorted(_lines.values(),key=lambda item:item.get('t',0))[-5000:]
            _imported=dict(self.settings); self._apply_paid_extra_settings(_imported); self.blocklist_on.set(bool(_imported.get('filter_blocklist',True)))
            self.read_username.set(bool(self.settings.get('filter_read_username',True)))
            self.skip_links.set(bool(self.settings.get('filter_skip_links',True)))
            self.skip_commands.set(bool(self.settings.get('filter_skip_commands',True)))
            self.only_highlighted.set(bool(self.settings.get('filter_only_highlighted',False)))
            self.skip_bots.set(bool(self.settings.get('filter_skip_bots',False)))
            try: self.min_length.set(max(1,min(100,int(self.settings.get('filter_min_length',1)))))
            except (TypeError,ValueError): self.min_length.set(1)
            self.word_substitutions=self._normalize_word_substitutions(self.settings.get('word_substitutions',[]))
            self._refresh_word_substitutions(); self._save_filter_settings()
            for item in users:
                if isinstance(item,dict):
                    name=self._normalize_username(item.get('username')); platform=str(item.get('platform','')).title()
                    if name and platform in ('Twitch','Kick'): self.ignored_users.add((platform,name))
                elif isinstance(item,str):
                    name=self._normalize_username(item)
                    if name: self.ignored_users.update((platform,name) for platform in ('Twitch','Kick'))
            app_dir=APP_DIR
            preset_path=os.path.join(app_dir,'voice_presets.json')
            try:
                with open(preset_path,'r',encoding='utf-8') as f: current_presets=json.load(f)
            except (OSError,json.JSONDecodeError): current_presets={}
            current_presets.update(presets)
            with open(preset_path,'w',encoding='utf-8') as f: json.dump(current_presets,f,ensure_ascii=False,indent=2)
            self._save_connection_settings(); self._save_ignored_users(); self._refresh_ignored_users(); self._refresh_presets()
            self.channel.set(self.settings.get('twitch_channel',self.twitch_user.get()))
            self.kick_channel.set(self.settings.get('kick_channel',''))
            self.twitch_tts_voice.set(self.settings.get('twitch_tts_voice',self.voice.get()))
            self.kick_tts_voice.set(self.settings.get('kick_tts_voice',self.voice.get()))
            self.voice.set(self.settings.get('tts_voice',self.voice.get()))
            imported_mode=self.settings.get('tts_voice_mode')
            if imported_mode not in VOICE_MODES:
                imported_mode='five' if self.settings.get('chat_voice_mode',False) else 'platform'
            self.tts_voice_mode.set(imported_mode)
            self.chat_voice_enabled.set(imported_mode=='five')
            imported_groups=self.settings.get('random_voice_groups')
            if isinstance(imported_groups,list):
                for key,var in self.random_group_vars.items(): var.set(key in imported_groups)
                self._update_random_pool_text()
            self._refresh_tts_voice_mode()
            saved_slots=self.settings.get('chat_voice_slots')
            if isinstance(saved_slots,list):
                defaults=['American English — Heart (af_heart)','American English — Bella (af_bella)','American English — Adam (am_adam)','British English — George (bm_george)','American English — Nicole (af_nicole)']
                self.chat_voice_slots=(list(saved_slots[:5])+defaults[len(saved_slots[:5]):])
                assignments=self.settings.get('user_voice_assignments',{})
                self.user_voice_assignments={key:int(value) for key,value in assignments.items() if str(value).isdigit() and 0<=int(value)<5} if isinstance(assignments,dict) else {}
                self.chat_voice_users={key:(key.split('|',1)[0],key.split('|',1)[1]) for key in self.user_voice_assignments if '|' in key}
                for index,var in enumerate(self.chat_voice_slot_vars): var.set(self.chat_voice_slots[index])
                self.chat_voice_mode_status.set('Five-voice mode is on.' if self.chat_voice_enabled.get() else 'Five-voice mode is off. Turn it on under Text to Speech.')
                self._refresh_chat_voice_users()
            self.paid_voice_access=self._load_paid_voice_access(); self.paid_voices_enabled.set(bool(self.settings.get('paid_voices_enabled',True))); self.paid_reply.set(bool(self.settings.get('paid_voices_reply',True)))
            self.paid_free_mods.set(bool(self.settings.get('paid_free_mods',False))); self.paid_free_vips.set(bool(self.settings.get('paid_free_vips',False)))
            self.paid_scope.set(self.settings.get('paid_voices_scope') if self.settings.get('paid_voices_scope') in ('codes','all') else 'codes')
            if hasattr(self,'paid_scope_label'): self.paid_scope_label.set('Everything (reading chat)' if self.paid_scope.get()=='all' else 'Voice codes only')
            for var,name in ((self.paid_bits,'paid_bits_per_30'),(self.paid_kicks,'paid_kicks_per_30')):
                try: var.set(max(1,int(self.settings.get(name,50))))
                except (TypeError,ValueError): var.set(50)
            self._refresh_paid_voices()
            self.set_status('Backup imported. Sign in to Twitch again if the imported settings use a different client setup.')
            messagebox.showinfo('Backup imported','Settings, blocked users, and voice presets were imported. Sign-in tokens were not changed.')
        except Exception as e: messagebox.showerror('Import backup',str(e))
    def _build_activity_page(self,page):
        self._page_header(page,'Activity','Live chat on the left, channel events on the right. Click a username to open their card. In the send box, !title and !category change your stream title and category on the platform chosen in the dropdown (they are not sent to chat).',info=True)
        send=ttk.Frame(page,style='Page.TFrame'); send.pack(fill='x',side='bottom',pady=(10,0))
        self.send_target=tk.StringVar(value='All'); self.send_text=tk.StringVar()
        ttk.Combobox(send,textvariable=self.send_target,values=('All','Twitch','Kick'),state='readonly',width=8).pack(side='left',padx=(0,8))
        entry=ttk.Entry(send,textvariable=self.send_text); entry.pack(side='left',fill='x',expand=True,ipady=4)
        entry.bind('<Return>',self._send_chat_message)
        ttk.Button(send,text='Send',command=self._send_chat_message).pack(side='left',padx=(8,0))
        row=ttk.Frame(page,style='Page.TFrame'); row.pack(fill='x',side='bottom',pady=(14,0))
        ttk.Button(row,text='Stop audio + clear queue',command=self.stop_audio).pack(side='left'); ttk.Button(row,text='Dock link Activity',style='Outline.TButton',command=lambda:self.show_dock_link('activity')).pack(side='left',padx=8); ttk.Button(row,text='Dock link Feed',style='Outline.TButton',command=lambda:self.show_dock_link('feed')).pack(side='left',padx=8)
        dashboard.build_session_tiles(self,page,PAL,FONT_FAMILY,PAGE_BG)
        split=tk.PanedWindow(page,orient='horizontal',bg=PAGE_BG,sashwidth=12,sashrelief='flat',bd=0,opaqueresize=True,showhandle=False)
        split.pack(fill='both',expand=True)
        left=tk.Frame(split,bg=PAGE_BG); right=tk.Frame(split,bg=PAGE_BG)
        split.add(left,stretch='always',minsize=380); split.add(right,stretch='never',minsize=260,width=400)
        tk.Label(left,text='CHAT',bg=PAGE_BG,fg=PAL['faint'],font=(FONT_FAMILY,9,'bold')).pack(anchor='w',pady=(0,6))
        holder=tk.Frame(left,bg=PAL['line'],padx=1,pady=1); holder.pack(fill='both',expand=True)
        dashboard.build_feed_panel(self,right,PAL,FONT_FAMILY,PAGE_BG)
        inner=tk.Frame(holder,bg=PAL['log']); inner.pack(fill='both',expand=True)
        scroll=ttk.Scrollbar(inner,orient='vertical'); scroll.pack(side='right',fill='y')
        self.log=tk.Text(inner,height=20,state='disabled',wrap='word',bg=PAL['log'],fg=PAL['text'],insertbackground=PAL['text'],selectbackground=PAL['accent_dim'],selectforeground='#ffffff',relief='flat',borderwidth=0,highlightthickness=0,font=(FONT_FAMILY,11),padx=16,pady=12,spacing1=3,spacing3=3,yscrollcommand=scroll.set)
        scroll.configure(command=self.log.yview)
        self.log.pack(side='left',fill='both',expand=True)
        self.log.tag_configure('platform_twitch',foreground=PAL['twitch'],font=(FONT_FAMILY,10,'bold'))
        self.log.tag_configure('platform_kick',foreground=PAL['kick'],font=(FONT_FAMILY,10,'bold'))
        self.log.tag_configure('sys',foreground=PAL['muted'])
    def log_line(self,s): self.ui_events.put(('log',s))
    def set_status(self,s): self.ui_events.put(('status',s))
    def _insert_entry(self,entry):
        """Writes one entry as exactly one line at the end of the Activity log (the log must be in the normal state)."""
        if entry['type']=='text':
            self.log.insert('end',str(entry['text']).replace('\r',' ').replace('\n',' ')+'\n',('sys',))
            return
        self.log.insert('end',str(entry['platform'])+'  ',('platform_'+str(entry['platform']).lower(),))
        if entry.get('badges') and self.badge_on.get() and self.emote_images is not None:
            for badge in entry['badges']:
                sprite=self.emote_images.group_photo([badge.key])
                if sprite: self.log.image_create('end',image=sprite[0],padx=1,align='center')
                else:
                    self.log.insert('end',' ',('emp:'+badge.key,)); self.emote_pending.setdefault(badge.key,set()).add(badge.key)
            self.log.insert('end',' ')
        username=entry['user']
        tag='activity_user_'+str(self.activity_user_tag_counter); self.activity_user_tag_counter+=1
        self.log.insert('end',username,(tag,))
        self.log.tag_configure(tag,foreground=PAL['link'],font=(FONT_FAMILY,11,'bold'))
        self.log.tag_bind(tag,'<Enter>',lambda _event,t=tag:(self.log.configure(cursor='hand2'),self.log.tag_configure(t,underline=True)))
        self.log.tag_bind(tag,'<Leave>',lambda _event,t=tag:(self.log.configure(cursor='xterm'),self.log.tag_configure(t,underline=False)))
        self.log.tag_bind(tag,'<Button-1>',lambda event,name=username,platform=entry['platform'],meta=entry.get('meta',{}):self._activity_click(event,name,platform,meta))
        tokens=entry.get('tokens')
        if tokens and self.emote_on.get() and self.emote_images is not None:
            self.log.insert('end',': ')
            self._insert_tokens(tokens)
            self.log.insert('end','\n')
        else:
            content=str(entry['content'] or '').replace('\r',' ').replace('\n',' ')
            self.log.insert('end',': '+content+'\n')
    def _insert_tokens(self,tokens):
        # Words and emotes in order. A zero-width emote (7TV overlay) sticks to the emote before it.
        items=[]
        for t in tokens:
            if isinstance(t,str): items.append(t.replace('\r',' ').replace('\n',' ')); continue
            if t.zero_width and items:
                if isinstance(items[-1],str) and not items[-1].strip() and len(items)>1 and isinstance(items[-2],list): items.pop()
                if isinstance(items[-1],list): items[-1].append(t); continue
            items.append([t])
        for item in items:
            if isinstance(item,str): self.log.insert('end',item); continue
            keys=[e.key for e in item]; gk='+'.join(keys); sprite=self.emote_images.group_photo(keys)
            if sprite:
                self.log.image_create('end',image=sprite[0],padx=1,align='center'); self._emote_animate(gk,sprite)
            else:
                self.log.insert('end',' '.join(e.name for e in item),('emp:'+gk,))
                for k in keys: self.emote_pending.setdefault(k,set()).add(gk)
    def _emote_animate(self,gk,sprite):
        if len(sprite[1])>1 and self.emote_animate.get() and gk not in self.emote_anim:
            if len(self.emote_anim)>=80: self.emote_anim.pop(next(iter(self.emote_anim)))
            self.emote_anim[gk]={'photo':sprite[0],'frames':sprite[1],'delays':sprite[2],'i':0,'due':time.monotonic()+sprite[2][0]/1000.0}
    def _emote_ready(self,key):
        """A background download/decode finished: swap the emote name text in Activity for the image."""
        groups=self.emote_pending.pop(key,set())
        if not groups or self.emote_images is None: return
        self.log.configure(state='normal')
        try:
            for gk in groups:
                keys=gk.split('+'); sprite=self.emote_images.group_photo(keys)
                if not sprite:
                    for k in keys:
                        if k!=key: self.emote_pending.setdefault(k,set()).add(gk)
                    continue
                tag='emp:'+gk; ranges=[str(r) for r in self.log.tag_ranges(tag)]
                for i in range(len(ranges)-2,-1,-2):
                    self.log.delete(ranges[i],ranges[i+1]); self.log.image_create(ranges[i],image=sprite[0],padx=1,align='center')
                self.log.tag_delete(tag); self._emote_animate(gk,sprite)
        finally: self.log.configure(state='disabled')
    def _emote_tick(self):
        try:
            if self.emote_anim and self.emote_animate.get() and self.log.winfo_viewable() and self.root.state()!='iconic':
                now=time.monotonic()
                for st in list(self.emote_anim.values()):
                    if now>=st['due']:
                        st['i']=(st['i']+1)%len(st['frames']); st['photo'].paste(st['frames'][st['i']]); st['due']=now+st['delays'][st['i']]/1000.0
        except Exception: pass
        self.root.after(40,self._emote_tick)
    def _render_activity(self):
        self.log.configure(state='normal')
        self.log.delete('1.0','end')
        for name in self.log.tag_names():
            if name.startswith('activity_user_') or name.startswith('emp:'): self.log.tag_delete(name)
        self.emote_pending.clear()
        for entry in self.activity_entries: self._insert_entry(entry)
        self.log.see('end'); self.log.configure(state='disabled')
    def _add_activity_entry(self,entry):
        """Adds one line without redrawing the whole log; the log keeps the newest ACTIVITY_MAX lines."""
        self.activity_entries.append(entry)
        self.log.configure(state='normal')
        while len(self.activity_entries)>ACTIVITY_MAX:
            del self.activity_entries[0]; self.log.delete('1.0','2.0')
        self._insert_entry(entry)
        self.log.see('end'); self.log.configure(state='disabled')
    def _tokenize_chat(self,platform,content,metadata):
        """Cuts a chat message into words and emotes (None when it has no emote or emotes are off)."""
        if not self.emote_on.get(): return None
        plat=str(platform).title()
        raw=metadata.get('raw') if plat=='Kick' else content
        tokens=self.emote_store.tokenize(plat,raw or content,metadata.get('fragments') if plat=='Twitch' else None,self.emote_channel_key.get(plat))
        if not self.emote_store.has_emotes(tokens): return None
        metadata['emote_names']=sorted({t.name for t in tokens if not isinstance(t,str)})
        return tokens
    def log_chat_message(self,platform,user,content,metadata=None):
        metadata=metadata if metadata is not None else {}
        tokens=self._tokenize_chat(platform,content,metadata)
        badges=self.emote_store.badges_for(platform,metadata) if self.badge_on.get() else []
        try:
            if self.badge_on.get() and self.paid_voices_enabled.get() and self._paid_expiry(platform,user,metadata.get('user_id'))>time.time(): badges.append(self.emote_store.voice_badge())  # microphone: has paid voice time
        except Exception: pass
        self._add_activity_entry({'type':'chat','platform':platform,'user':str(user or 'viewer'),'content':content,'meta':metadata,'tokens':tokens,'badges':badges})
        with self.dock_lock:
            meta=metadata
            item={'id':self.dock_next_id,'platform':str(platform).title(),'user':str(user or 'viewer')[:100],'content':str(content or '')[:600],'user_id':str(meta.get('user_id') or ''),'message_id':str(meta.get('message_id') or '')}
            if tokens: item['parts']=emotes.EmoteStore.to_parts(tokens)[:80]
            if badges: item['badges']=[{'k':b.key,'n':b.name} for b in badges]
            self.dock_messages.append(item)
            self.dock_next_id+=1
    # ---- Emotes ----
    def _emote_startup(self):
        threading.Thread(target=self.emote_store.prune_cache,daemon=True).start()
        self.emote_store.load_globals(done=lambda:self.ui_events.put(('emote_status',None)))
        self.root.after(40,self._emote_tick)
    def _emote_channel(self,platform,channel_id):
        plat=str(platform).lower(); cid=str(channel_id or '')
        if not cid.isdigit(): return
        self.emote_channel_key[str(platform).title()]=plat+':'+cid
        self.emote_store.load_channel(plat,cid,done=lambda:self.ui_events.put(('emote_status',None)))
    def _dock_emote(self,key):
        """Image bytes for the OBS dock (called from the dock server thread)."""
        try: data=self.emote_store.image_bytes(key)
        except Exception: return None
        return (data,emotes.sniff_type(data)) if data else None
    def _build_emotes_page(self,page):
        P=PAL
        ttk.Label(page,text='Shows 7TV, BetterTTV and FrankerFaceZ emotes (global and the ones of the channel you read) plus Twitch and Kick emotes, in Activity and in the OBS dock, like Chatterino does. 7TV zero-width emotes are drawn on top of the emote before them.',style='Subtitle.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(0,12))
        def toggle(text,var):
            r=ttk.Frame(page,style='Page.TFrame'); r.pack(fill='x',pady=4)
            ToggleSwitch(r,var,bg=P['bg']).pack(side='left'); ttk.Label(r,text=text,style='Muted.TLabel').pack(side='left',padx=10)
        toggle('Show emote images',self.emote_on)
        toggle('Show badges before names (mod, VIP, sub... and a microphone for paid voice time)',self.badge_on)
        toggle('Animate animated emotes',self.emote_animate)
        toggle('Do not read emote names aloud (names are read as words when this is off)',self.emote_skip_speech)
        src=ttk.Frame(page,style='Page.TFrame'); src.pack(fill='x',pady=(12,4))
        ttk.Label(src,text='Sources',style='Muted.TLabel').pack(side='left',padx=(0,12))
        for key,label in emotes.PROVIDER_LABELS.items(): ttk.Checkbutton(src,text=label,variable=self.emote_provider[key]).pack(side='left',padx=(0,12))
        sz=ttk.Frame(page,style='Page.TFrame'); sz.pack(fill='x',pady=(8,4))
        ttk.Label(sz,text='Emote size',style='Muted.TLabel').pack(side='left',padx=(0,12))
        ttk.Combobox(sz,textvariable=self.emote_size,values=tuple(EMOTE_SIZES),state='readonly',width=10).pack(side='left')
        self.emote_status_var=tk.StringVar()
        ttk.Label(page,textvariable=self.emote_status_var,style='Muted.TLabel',wraplength=720,justify='left').pack(anchor='w',pady=(16,8))
        ttk.Button(page,text='Reload emotes',style='Outline.TButton',command=self._reload_emotes).pack(anchor='w')
        for var in (self.emote_on,self.badge_on,self.emote_animate,self.emote_skip_speech,self.emote_size,*self.emote_provider.values()):
            var.trace_add('write',lambda *_:self._emote_settings_changed())
        self._refresh_emote_status()
    def _reload_emotes(self):
        self.emote_store.reload(list(self.emote_channel_key.values()),done=lambda:self.ui_events.put(('emote_status',None)))
        self._refresh_emote_status()
    def _refresh_emote_status(self):
        if not hasattr(self,'emote_status_var'): return
        counts,errors=self.emote_store.counts()
        lines=[]
        if self.emote_store.global_state=='loading': lines.append('Loading emote lists…')
        for key in emotes.THIRD_PARTY: lines.append('%s: %d global, %d channel'%(emotes.PROVIDER_LABELS[key],counts[key][0],counts[key][1]))
        if not self.emote_channel_key: lines.append('Channel emotes load once Twitch or Kick chat is connected.')
        if not PIL_OK: lines.append('Pillow is not installed, so images are not drawn in Activity (the OBS dock still shows them). Start with run.bat again to install it.')
        if errors: lines.append('Could not load: '+'; '.join(errors[:3]))
        self.emote_status_var.set('\n'.join(lines))
    def _emote_settings_changed(self):
        try:
            self.emote_store.enabled.update({p:v.get() for p,v in self.emote_provider.items()})
            self.settings.update({'badges_enabled':self.badge_on.get(),'emotes_enabled':self.emote_on.get(),'emotes_animated':self.emote_animate.get(),'emotes_skip_speech':self.emote_skip_speech.get(),'emotes_size':self.emote_size.get()})
            for p,v in self.emote_provider.items(): self.settings['emotes_'+p]=v.get()
            self._save_connection_settings()
            if self.emote_images is not None and self.emote_images.height!=EMOTE_SIZES.get(self.emote_size.get(),26):
                self.emote_anim.clear(); self.emote_images.set_height(EMOTE_SIZES.get(self.emote_size.get(),26))
            if not self.emote_animate.get():
                for st in self.emote_anim.values(): st['photo'].paste(st['frames'][0])
                self.emote_anim.clear()
            self._render_activity()
        except tk.TclError: pass
    def _append_activity_text(self,text):
        self._add_activity_entry({'type':'text','text':str(text)})
    def _can_moderate_kick(self):
        norm=lambda v:re.sub(r'[^a-z0-9]','',str(v).lower())
        channel=self.kick_reader.channel if self.kick_reader else ''
        return bool(self.kick_tokens and self.kick_user_id and channel and norm(channel)==norm(self.kick_slug))
    def _queue_skip_limit(self):
        try: return max(0,min(100,int(self.queue_skip.get())))
        except (ValueError,tk.TclError,AttributeError):
            try: return max(0,min(100,int(self.settings.get('queue_skip_limit',10))))
            except (TypeError,ValueError): return 10
    def _skip_backlog(self):
        """When too many messages are waiting, drop them all so the newest one is read next."""
        limit=self._queue_skip_limit()
        if not limit or self.tts_paused or self.messages.qsize()<limit: return
        dropped=0
        while True:
            try: self.messages.get_nowait()
            except queue.Empty: break
            else: self.messages.task_done(); dropped+=1
        if dropped: self.set_status('Skipped '+str(dropped)+' waiting message'+('s' if dropped!=1 else '')+' to keep up with chat.')
    def _timeout_seconds(self):
        try: value=int(self.timeout_seconds.get())
        except (ValueError,tk.TclError,AttributeError):
            try: value=int(self.settings.get('timeout_seconds',600))
            except (TypeError,ValueError): value=600
        return max(1,min(1209600,value))
    @staticmethod
    def _format_duration(seconds):
        for size,word in ((86400,'day'),(3600,'hour'),(60,'minute')):
            if seconds>=size and seconds%size==0:
                n=seconds//size; return str(n)+' '+word+('s' if n!=1 else '')
        return str(seconds)+' seconds'
    def _can_moderate_twitch(self):
        norm=lambda v:re.sub(r'[^a-z0-9]','',str(v).lower())
        channel=norm(self.channel.get())
        return bool(self.twitch_token and self.twitch_user_id and channel and channel==norm(self.twitch_user.get()))
    def _can_moderate(self,platform):
        platform=str(platform).title()
        return self._can_moderate_kick() if platform=='Kick' else (self._can_moderate_twitch() if platform=='Twitch' else False)
    def _activity_click(self,event,name,platform,meta):
        return self._show_viewer_card(event,name,platform,meta)
    def _show_viewer_card(self,event,name,platform,meta):
        """Click a name in Activity: profile on top, then app-only mute, then the real Twitch/Kick moderation."""
        old=getattr(self,'_viewer_card',None)
        if old is not None: old.close()
        self._viewer_card=viewer_card.ViewerCard(self,event,name,platform,meta,PAL,FONT_FAMILY)
        return 'break'
    def _show_mod_menu(self,event,name,platform,meta):
        return self._show_viewer_card(event,name,platform,meta)
    def _moderate(self,action,name,platform,meta,confirm=True):
        platform=str(platform).title(); seconds=self._timeout_seconds()
        if confirm and action in ('ban','timeout'):
            question=('Ban '+name+' from the stream on '+platform+'?') if action=='ban' else ('Time out '+name+' on '+platform+' for '+self._format_duration(seconds)+'?')
            if not messagebox.askyesno(platform+' moderation',question): return
        broadcaster=self.kick_user_id if platform=='Kick' else self.twitch_user_id
        user_id=str(meta.get('user_id') or ''); message_id=meta.get('message_id')
        if action=='ban': self._purge_banned(platform,name)
        def run():
            try:
                if platform=='Kick':
                    token=self._kick_access_token()
                    if not token: raise kick_auth.KickAuthError('You are not signed in to Kick.')
                    minutes=max(1,min(10080,-(-seconds//60)))
                    if action=='delete': kick_auth.delete_message(token,message_id); done='Deleted a message from '+name+'.'
                    elif action=='unban': kick_auth.unban_user(token,broadcaster,user_id); done='Unbanned '+name+' on Kick.'
                    elif action=='ban': kick_auth.ban_user(token,broadcaster,user_id); done='Banned '+name+' on Kick.'
                    else: kick_auth.ban_user(token,broadcaster,user_id,minutes); done='Timed out '+name+' on Kick for '+str(minutes)+' minute'+('s' if minutes!=1 else '')+'.'
                else:
                    q=urllib.parse.urlencode({'broadcaster_id':broadcaster,'moderator_id':broadcaster})
                    if action=='delete': self._twitch_helix('DELETE','/moderation/chat?'+q+'&'+urllib.parse.urlencode({'message_id':message_id})); done='Deleted a message from '+name+'.'
                    elif action=='unban': self._twitch_helix('DELETE','/moderation/bans?'+q+'&'+urllib.parse.urlencode({'user_id':user_id})); done='Unbanned '+name+' on Twitch.'
                    else:
                        data={'user_id':user_id,'reason':'Moderated from ChatVoice Local'}
                        if action=='timeout': data['duration']=seconds
                        self._twitch_helix('POST','/moderation/bans?'+q,{'data':data})
                        done=('Banned '+name+' on Twitch.') if action=='ban' else ('Timed out '+name+' on Twitch for '+self._format_duration(seconds)+'.')
                self._report(done)
            except Exception as exc: self._report(platform+' moderation failed: '+str(exc))
        threading.Thread(target=run,daemon=True).start()
    def _twitch_renew(self,stale_token):
        """Renews the Twitch access token unless the chat reader already did (refresh tokens are single use)."""
        with _TWITCH_RENEW_LOCK:
            if self.twitch_token and self.twitch_token!=stale_token: return True
            if not self.twitch_refresh_token: return False
            form={'client_id':self.settings.get('twitch_client_id') or DEFAULT_TWITCH_CLIENT_ID,'refresh_token':self.twitch_refresh_token,'grant_type':'refresh_token'}
            req=urllib.request.Request('https://id.twitch.tv/oauth2/token',data=urllib.parse.urlencode(form).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
            try:
                with urllib.request.urlopen(req,timeout=15) as response: grant=json.loads(response.read())
            except Exception: return False
            self._save_tokens(grant['access_token'],grant.get('refresh_token',self.twitch_refresh_token)); return True
    def _twitch_helix(self,method,path,payload=None):
        """Helix call from a worker thread. Returns the parsed JSON ({} when empty)."""
        client_id=self.settings.get('twitch_client_id') or DEFAULT_TWITCH_CLIENT_ID
        for attempt in range(2):
            token=self.twitch_token
            if not token: raise RuntimeError('You are not signed in to Twitch.')
            headers={'Authorization':'Bearer '+token,'Client-Id':client_id}
            data=None
            if payload is not None: data=json.dumps(payload).encode('utf-8'); headers['Content-Type']='application/json'
            req=urllib.request.Request(TWITCH_API+path,data=data,method=method,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=15) as response: raw=response.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                body=exc.read().decode('utf-8','replace')
                try: message=json.loads(body).get('message') or body
                except ValueError: message=body
                if exc.code==401 and attempt==0 and 'scope' not in message.lower() and self._twitch_renew(token): continue
                if exc.code in (401,403) and 'scope' in message.lower():
                    raise RuntimeError('Twitch login lacks this permission. Log out of Twitch on the Login & Chat page, then log in again and approve the new permissions.')
                raise RuntimeError('Twitch returned HTTP '+str(exc.code)+': '+message[:200])
            except (urllib.error.URLError,TimeoutError): raise RuntimeError('Could not reach Twitch. Check your internet connection.')
    def _can_send(self,platform):
        if platform=='Twitch': return bool(self.twitch_token and self.twitch_user_id)
        return bool(self.kick_tokens and self.kick_user_id)
    def _send_chat_message(self,event=None):
        text=re.sub(r'\s+',' ',self.send_text.get()).strip()[:500]
        if text and self._send_text(self.send_target.get(),text): self.send_text.set('')
        return 'break'
    def _send_text(self,target,text):
        """Sends text to Twitch, Kick or both (used by the Activity send bar and the OBS dock). Returns True when it was sent."""
        text=re.sub(r'\s+',' ',str(text or '')).strip()[:500]
        if not text: return False
        if quick_commands.handle(self,target,text): return True  # !title / !category: handled here, never sent to chat
        wanted=['Twitch','Kick'] if target=='All' else [target]
        platforms=[p for p in wanted if self._can_send(p)]
        if not platforms:
            self._report('Sign in to '+(' or '.join(wanted) if target=='All' else target)+' first to send messages.'); return False
        now=time.time()
        self.recent_sent=[item for item in getattr(self,'recent_sent',[]) if now-item[2]<120]
        for platform in platforms:
            self.recent_sent.append((platform,text.lower(),now))
            uid=self.twitch_user_id if platform=='Twitch' else self.kick_user_id
            threading.Thread(target=self._send_to_platform,args=(platform,text,uid),daemon=True).start()
        return True
    def _report(self,text):
        """Status bar message that the OBS dock also shows."""
        self.ui_events.put(('status',text))
        with self.dock_lock:
            self.dock_notices.append({'id':self.dock_notice_id,'text':str(text)[:300]}); self.dock_notice_id+=1
    # ---- OBS dock -------------------------------------------------------------------------------------------
    def _start_dock_server(self):
        self.dock_feed_data={'v':0,'entries':[],'totals':{}}; self.dock_feed_sig=None
        key=self.settings.get('dock_key')
        if not key: key=secrets.token_urlsafe(12); self.settings['dock_key']=key; self._save_connection_settings()
        try:
            self.dock=dock_server.DockServer(key,self._dock_snapshot,lambda payload:(self.ui_events.put(('dock_action',payload)),{'ok':True})[1],self._dock_feed_snapshot,self._dock_emote)
            self.dock.start()
        except OSError as exc:
            self.dock=None; self.set_status('OBS dock is unavailable: '+str(exc)); return
        self._refresh_dock_state()
    def _refresh_dock_state(self):
        try:
            state={'send':{p:self._can_send(p) for p in ('Twitch','Kick')},'mod':{p:self._can_moderate(p) for p in ('Twitch','Kick')},'timeout':self._format_duration(self._timeout_seconds()),'supporters':self._dock_supporters(),'free_left':int(self._free_window_left())}
            with self.dock_lock: state['banned']=list(self.dock_banned); state['banned_id']=self.dock_banned_id; self.dock_state=state
        except Exception: pass
        try: self._refresh_dock_feed()
        except Exception: pass
        self.root.after(1000,self._refresh_dock_state)
    def _refresh_dock_feed(self):
        """Copy the event feed for the feed dock (runs on the UI thread; the dock server thread only reads the copy)."""
        feed=self.feed; hidden=dashboard.hidden_groups(self); entries=[entry for entry in feed.entries if dashboard.TYPES[entry['type']][1] not in hidden]
        sig=(len(entries),entries[-1]['ts'] if entries else 0,tuple(sorted(feed.totals.items())),tuple(sorted(hidden)))
        if sig==self.dock_feed_sig: return
        self.dock_feed_sig=sig
        rows=[]
        for entry in reversed(entries[-150:]):
            label,group=dashboard.TYPES[entry['type']]
            rows.append({'label':label,'group':group,'user':entry['user'],'platform':entry['platform'],'text':entry['text'],'time':time.strftime('%H:%M',time.localtime(entry['ts']))})
        data={'v':self.dock_feed_data['v']+1,'entries':rows,'totals':dict(feed.totals)}
        with self.dock_lock: self.dock_feed_data=data
    def _dock_feed_snapshot(self,version):
        with self.dock_lock: data=self.dock_feed_data
        return {'same':True,'v':data['v']} if version==data['v'] else data
    def _dock_snapshot(self,after,notice_after):
        with self.dock_lock:
            reset=after>self.dock_next_id-1
            base=0 if reset else after
            if notice_after>self.dock_notice_id-1: notice_after=0
            return {'messages':[item for item in self.dock_messages if item['id']>base],'notices':[item for item in self.dock_notices if item['id']>notice_after],'state':dict(self.dock_state),'reset':reset}
    def _dock_action(self,payload):
        kind=payload.get('type')
        if kind=='send':
            target=str(payload.get('target') or 'All')
            if target in ('All','Twitch','Kick'): self._send_text(target,payload.get('text'))
        elif kind=='mute_tts':
            platform=str(payload.get('platform') or '').title(); name=self._normalize_username(str(payload.get('name') or '')[:100])
            if platform in ('Twitch','Kick') and name:
                if (platform,name) in self.ignored_users: self._report(name+' is already muted from TTS.')
                else: self.add_ignored_user(name,platform); self._report('Muted '+name+' from TTS on '+platform+' (app only, not a timeout). Undo it on the Blocked users page.')
        elif kind=='moderate':
            action=str(payload.get('action') or ''); platform=str(payload.get('platform') or '').title()
            if action not in ('delete','timeout','ban','unban') or platform not in ('Twitch','Kick'): return
            meta={'user_id':str(payload.get('user_id') or '')[:40],'message_id':str(payload.get('message_id') or '')[:80]}
            if not self._can_moderate(platform): self._report('Moderation needs you signed in to '+platform+' in your own channel.'); return
            if action=='delete' and not meta['message_id']: self._report('That message cannot be deleted (no message id).'); return
            if action!='delete' and not meta['user_id']: self._report('That user cannot be moderated (no user id).'); return
            self._moderate(action,str(payload.get('name') or 'user')[:100],platform,meta,confirm=False)
    def show_dock_link(self,kind='activity'):
        feed=kind=='feed'; title='Dock link Feed' if feed else 'Dock link Activity'
        if not self.dock or not self.dock.url(kind):
            messagebox.showwarning(title,'The dock server could not start. Check the status bar for the reason.'); return
        url=self.dock.url(kind)
        self.root.clipboard_clear(); self.root.clipboard_append(url)
        what='the event feed (Bits, Kicks, subs, follows, raids and channel points)' if feed else 'live chat with the send bar and moderation buttons'
        name='ChatVoice Feed' if feed else 'ChatVoice Activity'
        messagebox.showinfo(title,'The '+title.lower()+' was copied to your clipboard. It shows '+what+'.\n\nIn OBS: Docks > Custom Browser Docks... > type a name such as '+name+' > paste the link in the URL box > Apply.\n\nKeep this app running while you use the dock. Keep the link private: anyone who has it on this PC can see your dock'+('.' if feed else ', send chat and moderate as you.'))
    def _send_to_platform(self,platform,text,uid):
        try:
            if platform=='Kick':
                token=self._kick_access_token()
                if not token: raise kick_auth.KickAuthError('You are not signed in to Kick.')
                kick_auth.send_chat(token,uid,text)
            else:
                result=self._twitch_helix('POST','/chat/messages',{'broadcaster_id':str(uid),'sender_id':str(uid),'message':text})
                item=(result.get('data') or [{}])[0]
                if item.get('is_sent') is False: raise RuntimeError((item.get('drop_reason') or {}).get('message') or 'Twitch did not accept the message.')
        except Exception as exc: self._report(platform+' message not sent: '+str(exc))
    def _is_own_sent_echo(self,platform,user,content):
        """True for the echo of a message that was just sent from the Activity bar (so TTS does not read your own text)."""
        if self.read_own_sent.get(): return False
        norm=lambda v:re.sub(r'[^a-z0-9]','',str(v).lower())
        platform=str(platform).title(); own=norm(self.twitch_user.get()) if platform=='Twitch' else norm(self.kick_slug)
        if not own or norm(user)!=own: return False
        text=re.sub(r'\s+',' ',content or '').strip().lower(); now=time.time()
        for index,(sent_platform,sent_text,stamp) in enumerate(getattr(self,'recent_sent',[])):
            if sent_platform==platform and sent_text==text and now-stamp<120:
                del self.recent_sent[index]; return True
        return False
    def _purge_banned(self,platform,username):
        """A banned person disappears at once: Activity, OBS dock, waiting speech, and the message being spoken right now."""
        platform=str(platform).title(); name=self._normalize_username(username)
        if not name: return
        self._purge_activity(platform,name)
        with self.dock_lock:
            kept=[item for item in self.dock_messages if not (item['platform']==platform and self._normalize_username(item['user'])==name)]
            if len(kept)!=len(self.dock_messages):
                self.dock_messages.clear(); self.dock_messages.extend(kept)
            self.dock_banned.append({'platform':platform,'user':name}); self.dock_banned_id+=1
        match=lambda item:len(item)>2 and str(item[1]).title()==platform and self._normalize_username(item[2])==name
        removed=0
        with self.messages.mutex:
            kept=[item for item in self.messages.queue if not match(item)]
            removed=len(self.messages.queue)-len(kept)
            if removed:
                self.messages.queue.clear(); self.messages.queue.extend(kept)
                self.messages.unfinished_tasks=max(0,self.messages.unfinished_tasks-removed)
                self.messages.not_full.notify_all()
                if self.messages.unfinished_tasks==0: self.messages.all_tasks_done.notify_all()
        current=self.speaking_now
        if current and current[0]==platform and self._normalize_username(current[1])==name:
            self.engine.cancel(); sd.stop()
        self.set_status('Removed '+str(username)+' from Activity and stopped their speech.')
    def _purge_activity(self,platform,username):
        """Removes a banned person's messages from the Activity list."""
        name=self._normalize_username(username); platform=str(platform).title()
        self.activity_entries=[entry for entry in self.activity_entries if not (entry['type']=='chat' and str(entry['platform']).title()==platform and self._normalize_username(entry['user'])==name)]
        self._render_activity()
    def _ignore_from_activity(self,username,platform=None):
        name=self._normalize_username(username)
        key=(str(platform).title(),name) if platform else None
        if key in self.ignored_users:
            self.set_status(self._blocked_label(*key)+' is already blocked from TTS.')
            return 'break'
        source=(' on '+platform) if platform else ''
        confirmed=messagebox.askyesno('Block from TTS?',f'Stop reading messages from {name}{source} aloud?\n\nTheir messages stay visible in Activity so you can still ban or time them out. This blocks TTS only for {platform or "the selected platform"}. It does not ban them on Twitch or Kick.')
        if confirmed: self.add_ignored_user(name,platform)
        return 'break'
    def _restore_twitch_session(self):
        if not self.twitch_refresh_token: return
        self.connection_state.set('Twitch: Restoring saved login…')
        gen=self.auth_generation
        def restore():
            try:
                form={'client_id':self.settings.get('twitch_client_id',DEFAULT_TWITCH_CLIENT_ID),'refresh_token':self.twitch_refresh_token,'grant_type':'refresh_token'}
                req=urllib.request.Request('https://id.twitch.tv/oauth2/token',data=urllib.parse.urlencode(form).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
                with urllib.request.urlopen(req,timeout=15) as response: grant=json.loads(response.read())
                token=grant['access_token']
                validate=urllib.request.Request('https://id.twitch.tv/oauth2/validate',headers={'Authorization':'OAuth '+token})
                with urllib.request.urlopen(validate,timeout=10) as response: account=json.loads(response.read())
                if account.get('client_id')!=form['client_id'] or not twitch_has_required_scopes(account.get('scopes',[])): raise RuntimeError('Twitch authorization needs renewal. Sign in again and approve chat reading.')
                if gen!=self.auth_generation: return
                self._save_tokens(token,grant.get('refresh_token',self.twitch_refresh_token))
                self.ui_events.put(('oauth_success',(token,account.get('login','user'),account.get('user_id',''))))
            except Exception as e:
                if gen==self.auth_generation: self.set_status('Twitch login needs renewal. Click Login on the Twitch account. '+str(e))
        threading.Thread(target=restore,daemon=True).start()
    def login_twitch(self):
        try:
            if self.twitch_token and self.twitch_user_id:
                self.reconnect_twitch_chat(); return
            self.settings['twitch_client_id']=self.client_id.get().strip(); self._save_connection_settings()
            if self.twitch_oauth: self.twitch_oauth.stop()
            self.twitch_oauth=TwitchDeviceOAuth(self.client_id.get(),self._twitch_oauth_callback,self.set_status); self.twitch_oauth.start()
        except Exception as e: messagebox.showerror('Twitch login',str(e))
    def _twitch_oauth_callback(self,token,data,refresh_token):
        self._save_tokens(token,refresh_token)
        self.settings['twitch_login']=data.get('login','user'); self._save_connection_settings()
        self.ui_events.put(('oauth_success',(token,data.get('login','user'),data.get('user_id',''))))
    def _forget_twitch_login(self):
        key=self.settings.get('twitch_client_id') or DEFAULT_TWITCH_CLIENT_ID
        if keyring:
            for _service in ('ChatVoice Local',LEGACY_KEYRING):
                try: keyring.delete_password(_service,key)
                except Exception: pass
        try: os.remove(os.path.join(APP_DIR,'twitch_refresh.dpapi'))
        except OSError: pass
    def logout_twitch(self):
        who=self.twitch_user.get().strip()
        if who or self.twitch_token or self.twitch_refresh_token:
            if not messagebox.askyesno('Log out of Twitch','Log out '+(who or 'the saved Twitch account')+' and stop reading its chat?\n\nAfterwards you can sign in with a different Twitch account.'): return
        self.auth_generation+=1
        for reader in self.readers: reader.stop()
        self.readers=[]
        if self.twitch_oauth: self.twitch_oauth.stop(); self.twitch_oauth=None
        old_token=self.twitch_token; client_id=self.client_id.get().strip()
        self.twitch_token=None; self.twitch_user_id=None; self.twitch_refresh_token=None
        self.twitch_user.set(''); self.channel.set('')
        self.settings.pop('twitch_login',None); self.settings.pop('twitch_channel',None); self._save_connection_settings()
        self._forget_twitch_login()
        if old_token:
            def revoke():
                try:
                    data=urllib.parse.urlencode({'client_id':client_id,'token':old_token}).encode()
                    urllib.request.urlopen(urllib.request.Request('https://id.twitch.tv/oauth2/revoke',data=data,headers={'Content-Type':'application/x-www-form-urlencoded'}),timeout=10).close()
                except Exception: pass
            threading.Thread(target=revoke,daemon=True).start()
        self._set_platform_presence('Twitch',False)
        self.evader_available=None
        if hasattr(self,'evader_status'): self.evader_status.set('Starts when Twitch chat is connected.')
        self.connection_state.set('Twitch: Login required')
        self.set_status('Logged out of Twitch. Click Login on the Twitch account to sign in with another account.')
        self._refresh_side_connections()
    def connect_twitch(self):
        for old_reader in self.readers: old_reader.stop()
        self.readers=[]
        token=self.twitch_token; ch=self.channel.get().strip()
        self._set_platform_presence('Twitch',False)
        if not token and self.twitch_refresh_token:
            try:
                token_request=urllib.request.Request('https://id.twitch.tv/oauth2/token',data=urllib.parse.urlencode({'client_id':self.client_id.get().strip(),'refresh_token':self.twitch_refresh_token,'grant_type':'refresh_token'}).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
                with urllib.request.urlopen(token_request,timeout=15) as response: renewed=json.loads(response.read())
                token=renewed['access_token']; self._save_tokens(token,renewed.get('refresh_token',self.twitch_refresh_token))
                validation=urllib.request.Request('https://id.twitch.tv/oauth2/validate',headers={'Authorization':'OAuth '+token})
                with urllib.request.urlopen(validation,timeout=10) as response: account=json.loads(response.read())
                self.twitch_user_id=account.get('user_id'); self.twitch_user.set(account.get('login',''))
                if not twitch_has_required_scopes(account.get('scopes',[])): raise RuntimeError('Saved authorization does not include chat reading. Click Login on the Twitch account again and approve the permission.')
            except Exception as e:
                messagebox.showwarning('Twitch login','Your saved Twitch login could not be renewed. Click Login on the Twitch account again.\n\n'+str(e)); return
        if not token: messagebox.showwarning('Twitch','Log in to Twitch first.'); return
        if not ch:
            ch=self.twitch_user.get().strip()
            if ch: self.channel.set(ch)
        if not ch: messagebox.showwarning('Twitch','Log in with Twitch first, or enter the channel login to read.'); return
        self.settings['twitch_channel']=ch.lstrip('#').lower(); self._save_connection_settings()
        if self.twitch_oauth: self.twitch_oauth.stop(); self.twitch_oauth=None
        if not self.twitch_user_id and self.twitch_refresh_token:
            try:
                user_req=urllib.request.Request('https://id.twitch.tv/oauth2/validate',headers={'Authorization':'OAuth '+token})
                with urllib.request.urlopen(user_req,timeout=10) as response: validated=json.loads(response.read())
                self.twitch_user_id=validated.get('user_id'); self.twitch_user.set(validated.get('login',''))
            except Exception: messagebox.showwarning('Twitch','Log in with Twitch again, then connect chat.'); return
        r=TwitchReader(token,self.client_id.get().strip(),self.twitch_user_id,ch,self.handle_event,self.set_status,self.twitch_refresh_token,self._save_tokens); self.readers.append(r); r.start()
        self.connection_state.set('Twitch: Connecting to #'+ch+'…')
        if hasattr(self,'evader_status'): self.evader_status.set('Checking permission…')
    def reconnect_twitch_chat(self):
        if not self.twitch_token and not self.twitch_refresh_token:
            self.connection_state.set('Twitch: Login required')
            messagebox.showwarning('Twitch chat','Sign in with Twitch before reconnecting chat.')
            return
        for reader in self.readers: reader.stop()
        self.readers=[]
        self._set_platform_presence('Twitch',False)
        self.connection_state.set('Twitch: Reconnecting…')
        self.connect_twitch()
    KICK_KEYRING='ChatVoice Local Kick'
    def _kick_client_id(self): return (self.settings.get('kick_client_id') or kick_auth.DEFAULT_CLIENT_ID).strip()
    def _kick_secret_load(self):
        if keyring:
            try:
                value=keyring.get_password(self.KICK_KEYRING,'client_secret') or keyring.get_password(LEGACY_KEYRING+' Kick','client_secret')
                if value: return value
            except Exception: pass
        return self.settings.get('kick_client_secret','')
    def _kick_store(self,name,value):
        if keyring:
            try:
                if value: keyring.set_password(self.KICK_KEYRING,name,value)
                else:
                    keyring.delete_password(self.KICK_KEYRING,name)
                    try: keyring.delete_password(LEGACY_KEYRING+' Kick',name)
                    except Exception: pass
                return
            except Exception: pass
        if name=='client_secret':  # no secure store available: keep it in the settings file
            self.settings['kick_client_secret']=value; self._save_connection_settings()
    def _kick_load(self,name):
        if keyring:
            try: return keyring.get_password(self.KICK_KEYRING,name) or keyring.get_password(LEGACY_KEYRING+' Kick',name) or ''
            except Exception: pass
        return ''
    def _kick_access_token(self):
        """Called from the Kick reader thread: a valid access token, refreshed when it has expired, or '' for guest mode."""
        with self.kick_token_lock:
            if not self.kick_tokens: return ''
            if time.time()>=self.kick_tokens.get('expires_at',0) and self.kick_tokens.get('refresh_token'):
                try:
                    self.kick_tokens=kick_auth.refresh(self._kick_client_id(),self._kick_secret_load(),self.kick_tokens['refresh_token'])
                    self._kick_store('refresh_token',self.kick_tokens['refresh_token'])
                except Exception as exc:
                    self.set_status('Kick login expired ('+str(exc)+'). Reading chat as a guest; sign in again to use your account.')
                    self.kick_tokens=None; return ''
            return (self.kick_tokens or {}).get('access_token','')
    def login_kick(self):
        client_id=self._kick_client_id()
        if self.kick_login: self.kick_login.stop()
        self.kick_login=kick_auth.KickLogin(client_id,self._kick_secret_load(),lambda tokens,uid,name,slug:self.ui_events.put(('kick_login_success',(tokens,uid,name,slug))),self.set_status,lambda message:self.ui_events.put(('kick_login_error',message)),scopes=kick_auth.scopes(bool(self.settings.get('kick_full_scopes',True))))
        try: self.kick_login.start(); self.kick_account_state.set('Waiting for you to approve in the browser…')
        except Exception as exc: messagebox.showerror('Login with Kick',str(exc))
    def logout_kick(self):
        if self.kick_login: self.kick_login.stop(); self.kick_login=None
        tokens=self.kick_tokens or {}
        if tokens: threading.Thread(target=kick_auth.revoke,args=(tokens.get('access_token',''),tokens.get('refresh_token','')),daemon=True).start()
        self.kick_tokens=None; self.kick_slug=''; self.kick_user_id=''; self._kick_store('refresh_token',''); self.kick_account_state.set('Not signed in (guest chat reading)')
        self.set_status('Signed out of Kick. Click Reconnect Kick to read chat as a guest.')
        self._refresh_side_connections()
    def _restore_kick_session(self):
        refresh_token=self._kick_load('refresh_token'); client_id=self._kick_client_id()
        if not (refresh_token and client_id): return
        self.kick_account_state.set('Restoring Kick login…')
        def restore():
            try:
                tokens=kick_auth.refresh(client_id,self._kick_secret_load(),refresh_token)
                uid,name,slug=kick_auth.whoami(tokens['access_token'])
                self.ui_events.put(('kick_login_success',(tokens,uid,name,slug)))
            except Exception as exc:
                self.ui_events.put(('status','Saved Kick login could not be restored: '+str(exc)))
                self.ui_events.put(('kick_login_failed',None))
        threading.Thread(target=restore,daemon=True).start()
    def connect_kick(self):
        channel=self.kick_channel.get().strip().lstrip('@') or self.kick_slug
        if not channel:
            messagebox.showwarning('Kick chat','Enter your Kick channel name first.'); return
        self.kick_channel.set(channel)
        if self.kick_reader: self.kick_reader.stop()
        self._set_platform_presence('Kick',False)
        self.settings['kick_channel']=channel.lower(); self._save_connection_settings()
        self.kick_connection_state.set('Kick: Connecting…')
        self.kick_login_button.configure(text='Reconnect Kick chat')
        self.kick_reader=KickReader(channel,self.handle_event,self.set_status,token_provider=self._kick_access_token)
        try: self.kick_reader.start()
        except Exception as e:
            self.kick_connection_state.set('Kick: Not connected')
            self._set_platform_presence('Kick',False)
            messagebox.showerror('Kick chat',str(e))
    def reconnect_kick_chat(self):
        if not self.kick_channel.get().strip() and not self.kick_slug:
            messagebox.showwarning('Kick chat','Enter your Kick channel name first.')
            return
        self.kick_connection_state.set('Kick: Reconnecting…')
        self.connect_kick()
    def disconnect(self):
        for r in self.readers: r.stop()
        self.readers=[]; self.set_status('Disconnected.')
        self.connection_state.set('Twitch: Disconnected')
        self._set_platform_presence('Twitch',False)
        if self.kick_reader: self.kick_reader.stop(); self.kick_reader=None
        self.kick_connection_state.set('Kick: Not connected')
        self._set_platform_presence('Kick',False)
        self.kick_login_button.configure(text='Connect Kick chat')
    def handle_event(self,platform,kind,user,content,metadata):
        # Network reader threads never touch Tk variables; marshal events to the UI thread.
        self.ui_events.put(('incoming',(platform,kind,user,content,metadata)))
    def _handle_event_ui(self,platform,kind,user,content,metadata):
        content=(content or '').strip()
        if kind=='banned': self._purge_banned(platform,user); return
        if kind=='suspicious': self._handle_suspicious(user,content,metadata); return
        if kind=='suspicious_status': self._evader_status_update(content,metadata); return
        if kind=='feed':
            dashboard.feed_add(self,platform,metadata.get('feed'),user,content,metadata.get('amount',0),metadata.get('user_id',''))
            self._maybe_speak_redemption(platform,user,metadata); return
        if kind=='feed_status': self._report(content); return
        if kind=='emote_channel': self._emote_channel(platform,metadata.get('channel_id')); return
        if kind=='badge_sets':
            if 'twitch' in metadata: self.emote_store.set_twitch_badges(metadata['twitch'])
            if 'kick_sub' in metadata: self.emote_store.set_kick_sub_badges(metadata['kick_sub'])
            return
        evader_mark=self._evader_mark(platform,metadata.get('user_id')) if kind=='message' else None
        if evader_mark=='banned': return
        if kind=='message': self.log_chat_message(platform,user,content,metadata)
        if (str(platform).title(),self._normalize_username(user)) in self.ignored_users: return
        if kind=='message' and self._is_bot_reply_echo(platform,content): return
        if kind=='message' and self._is_own_sent_echo(platform,user,content): return
        if kind=='message': self._remember_badges(platform,user,metadata.get('user_id'),metadata.get('badges'))
        if kind=='kicks':
            if self._is_duplicate_payment(platform,user,metadata.get('kicks'),'kicks'): return
            dashboard.feed_add(self,platform,'kicks',user,'',metadata.get('kicks'),metadata.get('user_id'))
            self._handle_payment(platform,user,metadata.get('user_id'),metadata.get('kicks'),'kicks',metadata.get('badges') or self._recall_badges(platform,user,metadata.get('user_id'))); return
        if kind=='message' and metadata.get('bits'):
            if self._is_duplicate_payment(platform,user,metadata.get('bits'),'bits',metadata.get('message_id')): return
            dashboard.feed_add(self,platform,'bits',user,content,metadata.get('bits'),metadata.get('user_id'))
            self._handle_payment(platform,user,metadata.get('user_id'),metadata.get('bits'),'bits',metadata.get('badges'))
        if evader_mark: return  # suspected ban evader: shown in Activity, never read aloud
        if kind=='message' and re.fullmatch(r'!myvoice',content,flags=re.IGNORECASE) and self.paid_voices_enabled.get() and self.paid_reply.get():
            self._answer_myvoice(platform,user,metadata.get('user_id'),metadata.get('badges')); return
        if kind=='message' and re.fullmatch(r'!voicelist',content,flags=re.IGNORECASE):
            if getattr(self,'voicelist_command',None) is not None and self.voicelist_command.get() and self.voice_codes.get():
                self._answer_voicelist(platform); return
        if kind=='message' and self._apply_chat_voice_command(platform,user,content): return
        if kind=='message': self._maybe_remind_expiry(platform,user,metadata.get('user_id'))
        voice_slot=None
        if True:
            if not content: return
            if self.skip_bots.get() and (metadata.get('is_bot') or self._normalize_username(user) in KNOWN_BOT_NAMES): return
            if self.skip_commands.get() and content.startswith('!'): return
            if self.skip_links.get() and re.search(r'(https?://|www\.)',content,re.I): return
            blocked_name=False
            if self.blocklist_on.get() and self.word_blocker.active:
                if self.word_blocker.matches(content):
                    self.blocked_word_hits+=1; self._update_blocklist_status(); return
                blocked_name=bool(user) and self.word_blocker.matches(user)
            highlighted=bool(metadata.get('msg-id') in ('highlighted-message','channel-points') or metadata.get('highlighted') or metadata.get('is_highlighted') or metadata.get('message_type')=='channel_points_highlighted')
            if self.only_highlighted.get() and str(platform).title()=='Twitch' and not highlighted: return
            _uid=metadata.get('user_id'); _badges=metadata.get('badges')
            if self.paid_scope.get()=='all' and not self._has_paid_voices(platform,user,_uid,_badges) and not self._use_trial(platform,user,_uid):
                self._notice_unpaid(platform,user,_badges); return
            segments=split_voice_segments(content,getattr(self,'voice_aliases',VOICE_ALIASES)) if self.voice_codes.get() else [(None,content)]
            if segments and self.paid_scope.get()!='all' and any(label for label,_text in segments) and not self._has_paid_voices(platform,user,_uid,_badges) and not self._use_trial(platform,user,_uid):
                # Not paid (and no free trial left): drop the voice codes and read the whole message in the normal voice.
                self._notice_unpaid(platform,user,_badges)
                segments=[(None,' '.join(text for _label,text in segments))]
            if kind=='message' and self.tts_voice_mode.get()=='five': voice_slot=self._note_chat_user(platform,user)
            parts=[]; skip_names=set(metadata.get('emote_names') or ()) if self.emote_skip_speech.get() else set()
            for voice_label,text in segments:
                text=re.sub(r'https?://\S+|www\.\S+','',text)
                if skip_names: text=' '.join(w for w in text.split() if w not in skip_names)
                text=self._replace_words_for_speech(text)
                text=re.sub(r'\s+',' ',text).strip()
                if text: parts.append((voice_label,text))
            if not parts or sum(len(t) for _l,t in parts)<int(self._num(self.min_length,1,1,100)): return
            speech_parts=[]; remaining=MAX_MESSAGE_LENGTH
            for voice_label,text in parts:
                if remaining<=0: break
                speech_parts.append((voice_label,text[:remaining])); remaining-=len(text)
            if speech_parts[0][0] is None and self.read_username.get() and user and not blocked_name: speech_parts[0]=(None,user+' says: '+speech_parts[0][1])
        base_settings=self._voice_config()
        self._skip_backlog()
        for voice_label,spoken_text in speech_parts:
            settings=copy.deepcopy(base_settings) if len(speech_parts)>1 else base_settings
            if voice_label:
                chosen=voice_label
            elif voice_slot is not None:
                chosen=self.chat_voice_slots[voice_slot]
            else:
                mode=self.tts_voice_mode.get()
                if mode=='platform': chosen=self.twitch_tts_voice.get() if str(platform).title()=='Twitch' else self.kick_tts_voice.get()
                elif mode=='random': chosen=self._pick_random_voice()
                else: chosen=self.voice.get()
            settings['mode']='Take turns per message'
            settings['route']=self._route_for('chat')
            settings['selected_voice']=chosen
            settings['voices'][0]=chosen
            selected_info=self.voice_catalog.get(chosen)
            if selected_info and selected_info.get('kind')=='preset':
                settings['voice_profiles'][chosen]=selected_info
            self._queue_speech((spoken_text,platform,user,settings),'message')
    def _queue_speech(self,item,label='message'):
        if self.tts_muted and label!='message':
            self.set_status('Muted. Press Ctrl+9 to unmute, then try again.')
            return False
        if self.tts_paused:
            while self.messages.qsize()>=PAUSED_QUEUE_LIMIT:
                try: self.messages.get_nowait(); self.messages.task_done()
                except queue.Empty: break
        try:
            self.messages.put_nowait(item)
            return True
        except queue.Full:
            self.log_line('Speech queue full: '+label+' skipped.')
            return False
    @staticmethod
    def _num(var,default,low=None,high=None):
        # Tk raises TclError when a spinbox is empty or half-typed; fall back to the default instead.
        try: value=float(var.get())
        except (tk.TclError,ValueError,TypeError): value=float(default)
        if low is not None: value=max(low,value)
        if high is not None: value=min(high,value)
        return value
    def _voice_config(self):
        count=max(2,min(3,int(self._num(self.speaker_count,2))))
        voices=[self.studio_voice1.get(),self.voice2.get(),self.voice3.get()]
        profiles={name:info for name,info in self.voice_catalog.items() if info.get('kind')!='preset'}
        return {'mode':self.studio_mode.get(),'voices':voices,'voice_profiles':profiles,'weights':[self._num(self.weight1,50,0),self._num(self.weight2,50,0),self._num(self.weight3,0,0)],'speaker_count':count,'speed':self._num(self.speed,1.0,0.25),'volume':self._num(self.volume,.8,0,1),'smoothness':self._num(self.smoothness,35,0)}
    def save_voice_preset(self):
        from tkinter import simpledialog
        name=simpledialog.askstring('Save voice preset','Name this voice setup:')
        if not name: return
        path='voice_presets.json'
        try:
            try:
                with open(path,'r',encoding='utf-8') as f: presets=json.load(f)
            except (FileNotFoundError,json.JSONDecodeError): presets={}
            presets[name]=self._voice_config()
            with open(path,'w',encoding='utf-8') as f: json.dump(presets,f,indent=2)
            self.preset_name.set(name); self._refresh_presets()
            self.set_status('Saved voice preset: '+name)
        except Exception as e: messagebox.showerror('Save preset',str(e))
    def _refresh_voice_catalog(self):
        self.voice_catalog={}
        for blend_name,(first,second),(weight_a,weight_b) in BUILTIN_BLENDS:
            label_a,label_b=KOKORO_ID_TO_LABEL[first],KOKORO_ID_TO_LABEL[second]
            self.voice_catalog['Blend: '+blend_name]={'kind':'preset','config':{'mode':'Blend voices','voices':[label_a,label_b,label_a],'voice_profiles':{},'weights':[weight_a,weight_b,0],'speaker_count':2,'speed':1.0,'volume':0.8,'smoothness':35}}
        language_map_path=os.path.join(self.saved_voice_dir,'voice_languages.json')
        try:
            with open(language_map_path,'r',encoding='utf-8') as f: saved_languages=json.load(f)
        except (OSError,json.JSONDecodeError): saved_languages={}
        for folder,tag in ((self.custom_voice_dir,'Custom'),(self.saved_voice_dir,'Saved mix')):
            os.makedirs(folder,exist_ok=True)
            for filename in sorted(os.listdir(folder)):
                if filename.lower().endswith(('.pt','.pth')):
                    label=f"{tag}: {os.path.splitext(filename)[0]}"
                    saved_lang,_,saved_repo=(saved_languages.get(os.path.splitext(filename)[0],'a') if tag=='Saved mix' else 'a').partition('|')
                    self.voice_catalog[label]={'kind':'pack','path':os.path.abspath(os.path.join(folder,filename)),'lang':saved_lang or 'a','repo':saved_repo or None}
        profiles_path=os.path.join(self.custom_voice_dir,'clones.json')
        try:
            with open(profiles_path,'r',encoding='utf-8') as f: clones=json.load(f)
        except (OSError,json.JSONDecodeError): clones={}
        for key,profile in clones.items():
            language=profile.get('language','English'); language=language if language in CLONE_LANGUAGES else 'Auto'
            label='My cloned voices — '+profile.get('name',key)+' ('+language+')'
            self.voice_catalog[label]={'kind':'clone','lang':language,'key':key,'reference_audio':os.path.abspath(os.path.join(self.custom_voice_dir,profile.get('file','')))}
        tuned_root=os.path.join(self.custom_voice_dir,'finetuned')
        if os.path.isdir(tuned_root):
            for folder_name in sorted(os.listdir(tuned_root)):
                try:
                    with open(os.path.join(tuned_root,folder_name,'voice.json'),'r',encoding='utf-8') as f: meta=json.load(f)
                    model_dir=os.path.abspath(os.path.join(tuned_root,folder_name,meta.get('model','model')))
                except (OSError,json.JSONDecodeError): continue
                if not os.path.isdir(model_dir): continue
                language=meta.get('language','English'); language=language if language in CLONE_LANGUAGES else 'Auto'
                self.voice_catalog['My fine-tuned voices — '+meta.get('name',folder_name)+' ('+language+')']={'kind':'clone','lang':language,'folder':folder_name,'model_dir':model_dir,'speaker':meta.get('speaker',folder_name),'reference_audio':''}
        presets_path=os.path.join(APP_DIR,'voice_presets.json')
        try:
            with open(presets_path,'r',encoding='utf-8') as f: presets=json.load(f)
        except (OSError,json.JSONDecodeError): presets={}
        if isinstance(presets,dict):
            for name,config in presets.items():
                if isinstance(config,dict):
                    self.voice_catalog['Voice setup: '+str(name)]={'kind':'preset','config':config}
        self._rebuild_voice_aliases()
        values=self.builtin_voices+list(self.voice_catalog)
        if hasattr(self,'tts_voice_box'): self.tts_voice_box.configure(values=values)
        if hasattr(self,'twitch_voice_box'): self.twitch_voice_box.configure(values=values)
        if hasattr(self,'kick_voice_box'): self.kick_voice_box.configure(values=values)
        studio_values=self.builtin_voices+[name for name,info in self.voice_catalog.items() if info.get('kind')!='preset']
        for box in getattr(self,'voice_boxes',[]):
            box.configure(values=studio_values)
        if hasattr(self,'chat_voice_boxes'):
            for box in self.chat_voice_boxes: box.configure(values=values)
        if hasattr(self,'chat_voice_tree'): self._refresh_chat_voice_users()
        if hasattr(self,'random_pool_text'): self._update_random_pool_text()

    def _voice_source(self, label):
        entry=self.voice_catalog.get(label)
        if entry and entry.get('kind')=='pack': return entry['path']
        return label

    def import_voice_pack(self):
        path=filedialog.askopenfilename(title='Import a Kokoro voice pack',filetypes=[('Kokoro voice packs','*.pt *.pth'),('All files','*.*')])
        if not path: return
        if not path.lower().endswith(('.pt','.pth')):
            messagebox.showwarning('Unsupported voice file','Please select a compatible Kokoro .pt or .pth voice pack. Audio recordings such as WAV/MP3 cannot be imported directly.')
            return
        name=simpledialog.askstring('Name custom voice','Give this voice a name:',initialvalue=os.path.splitext(os.path.basename(path))[0])
        if not name: return
        name=self._clean_voice_name(name,'Name custom voice')
        if not name: return
        safe=re.sub(r'[^A-Za-z0-9 _-]','',name).strip().replace(' ','_') or 'custom_voice'
        target=os.path.join(self.custom_voice_dir,safe+os.path.splitext(path)[1].lower())
        if os.path.exists(target) and not messagebox.askyesno('Replace voice?','A voice with that name exists. Replace it?'): return
        try:
            shutil.copy2(path,target); self._refresh_voice_catalog()
            label='Custom: '+safe
            self.studio_voice1.set(label)
            self.set_status('Imported custom Kokoro voice pack: '+safe+'. It is available in the Text to Speech voice lists.')
        except Exception as e: messagebox.showerror('Import voice pack',str(e))

    def save_blended_voice(self):
        if self.studio_mode.get()!='Blend voices':
            messagebox.showinfo('Blend mode required','Select “Blend voices” first, choose your source voices and percentages, then save the blend.')
            return
        name=simpledialog.askstring('Save blended voice','Name your new blended voice:')
        if not name: return
        safe=re.sub(r'[^A-Za-z0-9 _-]','',name).strip().replace(' ','_') or 'blended_voice'
        try:
            import torch
            count=max(2,min(3,int(self._num(self.speaker_count,2))))
            labels=[self.studio_voice1.get(),self.voice2.get(),self.voice3.get()][:count]
            weights=[max(0.0,float(v)) for v in (self._num(self.weight1,50,0),self._num(self.weight2,50,0),self._num(self.weight3,0,0))[:count]]
            infos=[self.engine._voice_info(v,self.voice_catalog) for v in labels]
            if any(info.get('kind')=='clone' for info in infos): raise ValueError('Cloned voices cannot be blended with standard Kokoro voices.')
            if len({voice_group(info) for info in infos})>1: raise ValueError('Blend voices from the same language and the same Kokoro model only (v1.1 voices cannot be mixed with standard voices).')
            total=sum(weights)
            if total<=0: raise ValueError('At least one blend percentage must be greater than zero.')
            weights=[v/total for v in weights]
            packs=[self.engine.load_voice_pack(v,self.voice_catalog) for v in labels]
            mixed=packs[0]*weights[0]
            for pack,weight in zip(packs[1:],weights[1:]): mixed=mixed+pack*weight
            target=os.path.join(self.saved_voice_dir,safe+'.pt')
            if os.path.exists(target) and not messagebox.askyesno('Replace saved voice?','A saved voice with that name exists. Replace it?'): return
            torch.save(mixed,target)
            language_map_path=os.path.join(self.saved_voice_dir,'voice_languages.json')
            try:
                with open(language_map_path,'r',encoding='utf-8') as f: language_map=json.load(f)
            except (OSError,json.JSONDecodeError): language_map={}
            language_map[safe]=infos[0].get('lang','a')+('|'+infos[0]['repo'] if infos[0].get('repo') else '')
            with open(language_map_path,'w',encoding='utf-8') as f: json.dump(language_map,f,indent=2)
            self._refresh_voice_catalog()
            self.studio_voice1.set('Saved mix: '+safe)
            self.set_status('Saved reusable blended voice: '+safe)
            messagebox.showinfo('Voice saved','Your blended Kokoro voice was saved and added to the voice lists. You can select it in future sessions.')
        except Exception as e:
            messagebox.showerror('Save blended voice','Could not create this blend. Check that the selected voices are compatible Kokoro voice packs.\n\n'+str(e))

    @staticmethod
    def _prepare_reference(audio,rate):
        """Mono, silence trimmed at both ends, at most 10 s (cut at a pause), volume brought to a normal level."""
        x=audio.mean(axis=1) if audio.ndim>1 else audio
        frame=max(1,int(rate*0.02)); n=len(x)//frame
        if n<5: return x
        rms=np.sqrt((x[:n*frame].reshape(n,frame)**2).mean(axis=1))
        loud=np.where(rms>max(0.003,float(rms.max())*0.04))[0]
        if len(loud):
            start=max(0,int(loud[0])-5); end=min(n,int(loud[-1])+6); rms=rms[start:end]; x=x[start*frame:end*frame]
        limit=int(10*rate)
        if len(x)>limit:
            lo,hi=int(7*rate/frame),int(10*rate/frame)
            cut=(lo+int(np.argmin(rms[lo:hi])))*frame if hi<=len(rms) else limit
            x=x[:cut]
        peak=float(np.max(np.abs(x))) if len(x) else 0.0
        return (x*(0.9/peak)).astype(np.float32) if peak>1e-4 else x
    def add_cloned_voice(self):
        path=filedialog.askopenfilename(title='Choose a short voice recording',filetypes=[('Audio files','*.wav *.mp3 *.flac *.ogg'),('All files','*.*')])
        if not path: return
        try:
            info=sf.info(path); duration=info.frames/info.samplerate
            if duration < 3:
                messagebox.showwarning('Recording length',f'This recording is only {duration:.1f} seconds. Choose a clear voice sample of 3 to 10 seconds (WAV or MP3).'); return
            if duration > 12 and not messagebox.askyesno('Recording is long',f'This recording is {duration:.1f} seconds. Quick cloning works best with about 5 to 10 seconds of clear, continuous speech, so only the first part (cut at a pause) will be used.\n\nFor a more accurate voice from a longer recording, use \"Fine-tune a voice\" instead.\n\nContinue with quick cloning?'): return
            name=self.clone_name.get().strip() or simpledialog.askstring('Name cloned voice','Give your cloned voice a name:')
            if not name: return
            name=self._clean_voice_name(name,'Name cloned voice')
            if not name: return
            language=self.clone_language.get()
            safe=re.sub(r'[^A-Za-z0-9 _-]','',name).strip().replace(' ','_') or 'cloned_voice'
            folder=os.path.join(self.custom_voice_dir,'clones'); os.makedirs(folder,exist_ok=True)
            filename=safe+'.wav'; target=os.path.join(folder,filename)
            if os.path.exists(target) and not messagebox.askyesno('Replace voice?','A cloned voice with that name exists. Replace it?'): return
            # Whatever the format (WAV, MP3, FLAC, OGG), the cloning engine gets a cleaned-up mono WAV.
            audio,rate=sf.read(path,dtype='float32',always_2d=True)
            sf.write(target,self._prepare_reference(audio,rate),rate,subtype='PCM_16')
            try: os.remove(os.path.splitext(target)[0]+'.txt')  # the engine writes down what is said again for the new recording
            except OSError: pass
            profile_path=os.path.join(self.custom_voice_dir,'clones.json')
            try:
                with open(profile_path,'r',encoding='utf-8') as f: profiles=json.load(f)
            except (OSError,json.JSONDecodeError): profiles={}
            profiles[safe]={'name':name,'language':language,'file':os.path.join('clones',filename)}
            with open(profile_path,'w',encoding='utf-8') as f: json.dump(profiles,f,ensure_ascii=False,indent=2)
            self._refresh_voice_catalog()
            label=next(label for label,item in self.voice_catalog.items() if item.get('kind')=='clone' and item.get('reference_audio') and os.path.basename(item['reference_audio'])==filename)
            self.studio_voice1.set(label); self.clone_name.set('')
            self.studio_mode.set('Take turns per message'); self._refresh_studio_mode()
            installed=self._clone_engine_installed()
            if installed:
                self.set_status('Added local cloned voice: '+name+'.')
                messagebox.showinfo('Voice added','The voice is saved on this PC and selected as Speaker 1. Press “Test this setup” to hear it (set the mode to Take turns per message). The first use downloads the voice model (several GB) and can take several minutes.')
            else:
                self.set_status('Added local cloned voice: '+name+'. Install the local clone engine before using it.')
                messagebox.showinfo('Voice added','The voice is saved on this PC. Before it can speak, click “Install local clone engine” once (at the bottom of this page) and wait until the setup window says it is complete.')
        except getattr(sf,'LibsndfileError',RuntimeError):
            messagebox.showerror('Add cloned voice','This audio file could not be read. Use a WAV file, or update the audio library with: .venv\\Scripts\\python.exe -m pip install -U soundfile (needed for MP3).')
        except Exception as e: messagebox.showerror('Add cloned voice',str(e))

    def install_clone_engine(self):
        script=os.path.join(APP_DIR,'setup_voice_clone.ps1')
        if not os.path.isfile(script):
            messagebox.showerror('Local voice cloning','The setup file is missing from this app folder.'); return
        try:
            self.engine.stop_clone_worker()
            subprocess.Popen(['powershell.exe','-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',script],cwd=os.path.dirname(script))
            messagebox.showinfo('Local voice cloning','A setup window has opened. It uses Python 3.12 (and offers to install it for you if it is missing), then downloads the free open-source Qwen3-TTS engine and its dependencies (including PyTorch for your NVIDIA card) into this app folder. This is a large download. Keep that window open until it says setup is complete. If it fails, the window shows why and saves a copy as clone_setup.log.')
        except Exception as e: messagebox.showerror('Local voice cloning',str(e))

    def start_finetune(self):
        if not self._clone_engine_installed():
            messagebox.showinfo('Fine-tune a voice','Click \"Install local clone engine\" first and wait until the setup window says it is complete.'); return
        name=self.clone_name.get().strip()
        if not name:
            messagebox.showinfo('Fine-tune a voice','Type a name for the new voice in the Voice name box first.'); return
        name=self._clean_voice_name(name,'Fine-tune a voice')
        if not name: return
        paths=filedialog.askopenfilenames(title='Choose your recording(s) - about 10 minutes of clear speech in total',filetypes=[('Audio files','*.wav *.mp3 *.flac *.ogg'),('All files','*.*')])
        if not paths: return
        try: minutes=sum(sf.info(p).frames/sf.info(p).samplerate for p in paths)/60
        except Exception: messagebox.showerror('Fine-tune a voice','One of these files could not be read.'); return
        if minutes<2 and not messagebox.askyesno('Short recording',f'These recordings add up to only {minutes:.1f} minutes. About 10 minutes gives a much better voice.\n\nStart anyway?'): return
        size='1.7B' if self.finetune_model.get().startswith('1.7B') else '0.6B'
        epochs={'Short (3 rounds)':3,'Normal (5 rounds)':5,'Long (10 rounds)':10}.get(self.finetune_length.get(),5)
        if not messagebox.askyesno('Start training',f'This trains a Qwen3-TTS {size} voice from {minutes:.1f} minutes of audio, {epochs} rounds. It uses your graphics card heavily and can take from 15 minutes to over an hour. The voice engine will be stopped while it trains.\n\nStart now?'): return
        try:
            self.engine.stop_clone_worker()
            python=os.path.join(APP_DIR,'.qwen3tts-env','Scripts','python.exe')
            command=[python,os.path.join(APP_DIR,'qwen3tts_train.py'),'--name',name,'--language',self.clone_language.get(),'--model-size',size,'--epochs',str(epochs),'--app-dir',APP_DIR,'--recordings']+list(paths)
            subprocess.Popen(command,cwd=APP_DIR,creationflags=getattr(subprocess,'CREATE_NEW_CONSOLE',0))
            self.clone_name.set('')
            messagebox.showinfo('Training started','A training window has opened. Keep it open until it says Done. Then click \"Refresh voice list\" here and pick your new voice in Create Speech.')
        except Exception as e: messagebox.showerror('Fine-tune a voice',str(e))
    def _rebuild_voice_aliases(self):
        """Chat voice codes: the built-in names plus the names of your own voices (clones, fine-tuned, custom packs, saved mixes)."""
        aliases=dict(VOICE_ALIASES); shown=[name for _l,_g,voices in KOKORO_VOICE_GROUPS for _id,name in voices]
        for label in self.voice_catalog:
            for prefix in ('My cloned voices — ','My fine-tuned voices — ','Custom: ','Saved mix: '):
                if not label.startswith(prefix): continue
                name=label[len(prefix):]
                if prefix.startswith('My'): name=re.sub(r'\s*\([^()]*\)$','',name)
                name=re.sub(r'\s+','_',name.strip())
                if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',name):
                    aliases[name.lower()]=label
                    if name not in shown: shown.append(name)
        self.voice_aliases=aliases; self.voice_code_names=shown
        if hasattr(self,'voice_codes_label'): self.voice_codes_label.set_names(self._voice_code_list())
    def _voice_code_list(self):
        names=getattr(self,'voice_code_names',None) or [name for _l,_g,voices in KOKORO_VOICE_GROUPS for _id,name in voices]
        seen=set(); unique=[]
        for name in names:
            if name.lower() not in seen: seen.add(name.lower()); unique.append(name)
        return unique
    def _voice_codes_text(self):
        names=getattr(self,'voice_code_names',None) or [name for _l,_g,voices in KOKORO_VOICE_GROUPS for _id,name in voices]
        return 'Voice codes viewers can use (not read aloud): '+', '.join(names)
    def _clean_voice_name(self,name,title):
        """Voice names double as chat voice codes ("checkers123: hello"), so: one word, letters/digits/underscore, starting with a letter."""
        name=(name or '').strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',name):
            messagebox.showwarning(title,'Voice names cannot contain spaces. Use only letters, numbers and underscores, start with a letter, and keep it under 32 characters (for example checkers123 or my_voice).\n\nThe name is also the code chat types to use the voice, like "checkers123: hello".')
            return None
        return name
    DEFAULT_VOICE_LABEL='American English — Heart (af_heart)'
    def delete_cloned_voices(self):
        def current():
            return [(label,info) for label,info in self.voice_catalog.items() if info.get('kind')=='clone']
        if not current():
            messagebox.showinfo('Delete cloned voices','There are no cloned or fine-tuned voices to delete. (The standard voices are never listed here and cannot be deleted.)'); return
        win=tk.Toplevel(self.root); win.title('Delete cloned voices'); win.configure(bg=PAL['bg']); win.transient(self.root); win.geometry('680x400')
        ttk.Label(win,text='Select the cloned or fine-tuned voices you want to delete (hold Ctrl or Shift to pick several). Standard voices are not listed and are never touched.',wraplength=640,style='Muted.TLabel').pack(anchor='w',padx=16,pady=(14,8))
        holder=ttk.Frame(win); holder.pack(fill='both',expand=True,padx=16)
        listbox=tk.Listbox(holder,selectmode='extended',activestyle='none',bg=PAL['raised'],fg=PAL['text'],selectbackground=PAL['accent'],selectforeground='#ffffff',highlightthickness=0,borderwidth=0,font=('Segoe UI',10))
        scroll=ttk.Scrollbar(holder,orient='vertical',command=listbox.yview); listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y'); listbox.pack(side='left',fill='both',expand=True)
        shown=[]
        def fill():
            shown[:]=[label for label,_info in current()]
            listbox.delete(0,'end')
            for label in shown: listbox.insert('end',label.replace('My cloned voices — ','Quick clone: ').replace('My fine-tuned voices — ','Fine-tuned: '))
        def delete_selected():
            picked=[shown[i] for i in listbox.curselection()]
            if not picked:
                messagebox.showinfo('Delete cloned voices','Select at least one voice in the list first.',parent=win); return
            if not messagebox.askyesno('Delete voices','Delete '+str(len(picked))+' voice'+('' if len(picked)==1 else 's')+'? The recordings and trained files for '+('it' if len(picked)==1 else 'them')+' are removed from this PC and this cannot be undone.\n\nAnything using '+('it' if len(picked)==1 else 'them')+' (voice slots, presets) switches to the standard voice Heart.',parent=win): return
            problems=self._delete_clone_labels(picked)
            fill()
            if problems: messagebox.showwarning('Delete cloned voices','Some files could not be removed:\n\n'+'\n'.join(problems),parent=win)
            if not shown: win.destroy()
        buttons=ttk.Frame(win); buttons.pack(fill='x',padx=16,pady=14)
        ttk.Button(buttons,text='Delete selected voices',command=delete_selected).pack(side='left')
        ttk.Button(buttons,text='Close',command=win.destroy).pack(side='right')
        fill()
    def _delete_clone_labels(self,labels):
        self.engine.stop_clone_worker()  # a trained voice can be loaded by the engine; release its files first
        problems=[]
        profile_path=os.path.join(self.custom_voice_dir,'clones.json')
        try:
            with open(profile_path,'r',encoding='utf-8') as f: clones=json.load(f)
        except (OSError,json.JSONDecodeError): clones={}
        for label in labels:
            info=self.voice_catalog.get(label)
            if not info or info.get('kind')!='clone': continue
            try:
                if info.get('folder'):
                    shutil.rmtree(os.path.join(self.custom_voice_dir,'finetuned',info['folder']))
                    shutil.rmtree(os.path.join(APP_DIR,'finetune_work',info.get('speaker') or info['folder']),ignore_errors=True)
                else:
                    clones.pop(info.get('key'),None)
                    ref=info.get('reference_audio') or ''
                    for path in (ref,os.path.splitext(ref)[0]+'.txt') if ref else ():
                        if os.path.isfile(path): os.remove(path)
            except OSError as e: problems.append(label+': '+str(e))
        try:
            with open(profile_path,'w',encoding='utf-8') as f: json.dump(clones,f,ensure_ascii=False,indent=2)
        except OSError as e: problems.append('Could not update clones.json: '+str(e))
        self._refresh_voice_catalog()
        gone=set(label for label in labels if label not in self.voice_catalog)
        self._reset_deleted_voice_selections(gone)
        self.set_status('Deleted '+str(len(gone))+' cloned voice'+('' if len(gone)==1 else 's')+'.')
        return problems
    def _reset_deleted_voice_selections(self,gone):
        if not gone: return
        default=self.DEFAULT_VOICE_LABEL
        boxes=[getattr(self,name,None) for name in ('tts_voice_box','twitch_voice_box','kick_voice_box')]+list(getattr(self,'voice_boxes',[]))+list(getattr(self,'chat_voice_boxes',[]))
        for box in boxes:
            if box is None: continue
            try:
                variable=str(box.cget('textvariable'))
                if variable and self.root.globalgetvar(variable) in gone: self.root.globalsetvar(variable,default)
            except tk.TclError: pass
        changed=False
        for index,label in enumerate(self.chat_voice_slots):
            if label in gone: self.chat_voice_slots[index]=default; changed=True
        if changed:
            for index,var in enumerate(getattr(self,'chat_voice_slot_vars',[])): var.set(self.chat_voice_slots[index])
            self._save_chat_voice_settings(); self._refresh_chat_voice_users()
        presets_path=os.path.join(APP_DIR,'voice_presets.json')
        try:
            with open(presets_path,'r',encoding='utf-8') as f: presets=json.load(f)
            touched=False
            for config in presets.values() if isinstance(presets,dict) else ():
                if not isinstance(config,dict): continue
                if isinstance(config.get('voices'),list):
                    fixed=[default if v in gone else v for v in config['voices']]
                    if fixed!=config['voices']: config['voices']=fixed; touched=True
                if config.get('selected_voice') in gone: config['selected_voice']=default; touched=True
            if touched:
                with open(presets_path,'w',encoding='utf-8') as f: json.dump(presets,f,ensure_ascii=False,indent=2)
        except (OSError,json.JSONDecodeError): pass
        self._refresh_voice_catalog()
    def refresh_voice_list(self):
        self._refresh_voice_catalog(); self.set_status('Voice list refreshed.')

    def _refresh_presets(self):
        self._refresh_voice_catalog()
        try:
            with open('voice_presets.json','r',encoding='utf-8') as f: presets=json.load(f)
            self.preset_combo.configure(values=sorted(presets))
        except (FileNotFoundError,json.JSONDecodeError): pass
    def load_voice_preset(self):
        name=self.preset_name.get()
        if not name: return
        try:
            with open('voice_presets.json','r',encoding='utf-8') as f: cfg=json.load(f)[name]
            self.studio_mode.set(cfg.get('mode','Single voice')); self.speaker_count.set(cfg.get('speaker_count',2))
            voices=cfg.get('voices',['af_heart','af_bella','am_adam']); weights=cfg.get('weights',[50,50,0])
            self.studio_voice1.set(voices[0]); self.voice2.set(voices[1]); self.voice3.set(voices[2])
            self.weight1.set(weights[0]); self.weight2.set(weights[1]); self.weight3.set(weights[2])
            self.speed.set(cfg.get('speed',1)); self.volume.set(cfg.get('volume',.8)); self.smoothness.set(cfg.get('smoothness',35))
            self._refresh_studio_mode()
            self.set_status('Loaded voice preset: '+name)
        except Exception as e: messagebox.showerror('Load preset',str(e))
    def _queue_test_speech(self,text,cfg,label='voice test'):
        if self.tts_paused:
            self.set_status('TTS is paused. Resume it on Text to Speech to hear tests.')
            return False
        self.set_status('Playing test…')
        return self._queue_speech((text,'Test','',cfg),label)
    def test_studio_setup(self):
        # Plays the Create Speech setup exactly as configured, with the text from the Test text box.
        cfg=self._voice_config()
        text=re.sub(r'\s+',' ',self.studio_test_text.get() or '').strip()[:MAX_MESSAGE_LENGTH] or STUDIO_TEST_TEXT
        mode=cfg['mode']
        if any(self.voice_catalog.get(label,{}).get('kind')=='clone' for label in cfg['voices'][:cfg['speaker_count']]):
            if mode!='Take turns per message':
                if not messagebox.askyesno('Cloned voice','Cloned voices only work in the "Take turns per message" mode.\n\nSwitch to that mode now?'): return
                self.studio_mode.set('Take turns per message'); self._refresh_studio_mode(); cfg=self._voice_config(); mode=cfg['mode']
            if not self._clone_engine_installed():
                messagebox.showinfo('Cloned voice','The local clone engine is not installed yet. Click "Install local clone engine" at the bottom of this page and wait until the setup window says it is complete.'); return
        if mode=='Blend voices' and sum(cfg['weights'][:cfg['speaker_count']])<=0:
            messagebox.showinfo('Blend voices','At least one Mix % must be greater than zero.')
            return
        if mode=='Take turns per message':
            for index in range(cfg['speaker_count']):
                turn=dict(cfg); turn['selected_voice']=cfg['voices'][index]
                if not self._queue_test_speech(f'Speaker {index+1}. '+text,turn): break
        else:
            self._queue_test_speech(text,cfg)
    @staticmethod
    def _clone_engine_installed():
        return all(os.path.exists(os.path.join(APP_DIR,*part)) for part in (('.qwen3tts-env','Scripts','python.exe'),('qwen3tts-engine','finetuning','sft_12hz.py'),('qwen3tts_worker.py',)))
    def _test_with_voice(self,label,text):
        cfg=self._voice_config()
        cfg['mode']='Take turns per message'; cfg['selected_voice']=label; cfg['voices'][0]=label
        info=self.voice_catalog.get(label)
        if info and info.get('kind')=='preset': cfg['voice_profiles'][label]=info
        return self._queue_test_speech(text,cfg)
    def test_voice(self):
        # Text to Speech page: plays the voice(s) the current mode would use for chat.
        mode=self.tts_voice_mode.get()
        if mode=='platform': picks=[('Twitch chat voice',self.twitch_tts_voice.get()),('Kick chat voice',self.kick_tts_voice.get())]
        elif mode=='five': picks=[(f'Voice {index+1}',label) for index,label in enumerate(self.chat_voice_slots)]
        elif mode=='random': picks=[('Random voice',self._pick_random_voice())]
        else: picks=[('Voice',self.voice.get())]
        for name,label in picks:
            if not self._test_with_voice(label,name+'. This is a test of the selected voice.'): break
        if mode=='random' and picks: self.set_status('Random voice test: '+picks[0][1])
    @staticmethod
    def _item_kind(item):
        try: return (item[3] or {}).get('kind','chat')
        except (IndexError,TypeError,AttributeError): return 'chat'
    def _reward_ignores(self,which):
        """Audio output page: channel point and Power-up voices ignore Ctrl+7 ('clear'), Ctrl+8 ('pause') or Ctrl+9 ('mute')."""
        try: return bool(getattr(self,'reward_ignore_'+which).get())
        except (AttributeError,tk.TclError): return False
    def _speech_worker(self):
        while not self.worker_stop.is_set():
            from_queue=True
            if self.held_items and not self.tts_paused: item=self.held_items.pop(0); from_queue=False  # chat that waited out a pause while a reward played
            else:
                try: item=self.messages.get(timeout=.3)
                except queue.Empty: continue
            self.worker_has_pending=True
            try:
                reward=self._item_kind(item)=='reward'
                if self.tts_paused and not reward and self._reward_ignores('pause'):
                    self.held_items.append(item); continue  # waits for Ctrl+8 again, but rewards behind it are not stuck
                if not (reward and self._reward_ignores('pause')): self.tts_resume_event.wait()
                if self.worker_stop.is_set(): break
                if self.discard_paused_pending:
                    self.discard_paused_pending=False
                    if not (reward and self._reward_ignores('clear')): continue
                text,platform,user,settings=item; self.speaking_now=(str(platform).title(),user)
                self.engine.route=tuple((settings or {}).get('route') or ('me',)); self.engine.current_kind='reward' if reward else 'chat'
                self.set_status('Speaking: '+(user or platform)); self.engine.speak_config(text,settings)
            except Exception as e:
                self.log_line('TTS error: '+str(e)); self.set_status('TTS error: '+str(e).splitlines()[0][:160] if str(e) else 'TTS error. See Activity for details.')
                if item[1]=='Test': self.ui_events.put(('alert',str(e) or 'The voice test failed.'))
            finally:
                self.speaking_now=None; self.engine.current_kind=''
                self.worker_has_pending=False
                self.discard_paused_pending=False
                if from_queue: self.messages.task_done()
        sd.stop()
    def stop_audio(self):
        self.engine.cancel(); sd.stop()
        self.clear_speech_queue()
        self.set_status('Audio stopped and queue cleared.')
    def clear_speech_queue(self):
        keep_rewards=self._reward_ignores('clear'); keep=[]
        if self.tts_paused and self.worker_has_pending: self.discard_paused_pending=True
        while True:
            try: item=self.messages.get_nowait()
            except queue.Empty: break
            else:
                self.messages.task_done()
                if keep_rewards and self._item_kind(item)=='reward': keep.append(item)  # channel point voices can ignore Ctrl+7
        for item in keep:
            try: self.messages.put_nowait(item)
            except queue.Full: break
        self.held_items[:]=[item for item in self.held_items if keep_rewards and self._item_kind(item)=='reward']
        self.set_status('Speech queue cleared. Chat connections are unchanged.')
    def _refresh_shortcut_state(self):
        labels=getattr(self,'shortcut_state_labels',None)
        if not labels: return
        try:
            labels['pause'].configure(text='●  Paused' if self.tts_paused else '')
            labels['mute'].configure(text='●  Muted' if self.tts_muted else '')
        except tk.TclError: pass
    def toggle_tts_pause(self):
        if self.tts_paused:
            self.tts_paused=False; self.tts_resume_event.set(); self._refresh_shortcut_state(); self.set_status('TTS resumed. Chat connections stayed active.')
        else:
            self.tts_paused=True; self.tts_resume_event.clear()
            if not (self._reward_ignores('pause') and self.engine.current_kind=='reward'): self.engine.cancel(); sd.stop()
            self._refresh_shortcut_state(); self.set_status('TTS paused. Chat remains connected and queued speech will wait.')
    def _start_mute_hotkey(self):
        # Ctrl+9 works from anywhere in Windows (RegisterHotKey). It is also bound inside the app as a fallback.
        self.root.bind_all('<Control-Key-9>',lambda _event:(self.toggle_mute(),'break')[1])
        self.root.bind_all('<Control-Key-8>',lambda _event:(self.toggle_tts_pause(),'break')[1])
        self.root.bind_all('<Control-Key-7>',lambda _event:(self.clear_speech_queue(),'break')[1])
        if os.name!='nt': return
        ready=threading.Event()
        def loop():
            try:
                import ctypes
                from ctypes import wintypes
                user32=ctypes.windll.user32; kernel32=ctypes.windll.kernel32
                self.mute_hotkey_thread_id=kernel32.GetCurrentThreadId()
                self.mute_hotkey_ok=bool(user32.RegisterHotKey(None,1,0x0002|0x4000,0x39))  # Ctrl + 9, no key repeat
                self.pause_hotkey_ok=bool(user32.RegisterHotKey(None,2,0x0002|0x4000,0x38))  # Ctrl + 8, no key repeat
                self.clear_hotkey_ok=bool(user32.RegisterHotKey(None,3,0x0002|0x4000,0x37))  # Ctrl + 7, no key repeat
                ready.set()
                if not (self.mute_hotkey_ok or self.pause_hotkey_ok or self.clear_hotkey_ok): return
                message=wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(message),None,0,0)>0:
                    if message.message==0x0312: self.ui_events.put(({1:'toggle_mute',2:'toggle_pause',3:'clear_queue'}.get(message.wParam,'toggle_pause'),None))
                user32.UnregisterHotKey(None,1); user32.UnregisterHotKey(None,2); user32.UnregisterHotKey(None,3)
            except Exception: ready.set()
        threading.Thread(target=loop,daemon=True).start()
        ready.wait(2)
        if not self.mute_hotkey_ok or not getattr(self,'pause_hotkey_ok',True) or not getattr(self,'clear_hotkey_ok',True): self.set_status('Ctrl+7 (clear queue), Ctrl+8 (pause) or Ctrl+9 (mute) only works while this window is in front (another program already uses it as a global shortcut).')
    def _stop_mute_hotkey(self):
        if (self.mute_hotkey_ok or getattr(self,'pause_hotkey_ok',False) or getattr(self,'clear_hotkey_ok',False)) and self.mute_hotkey_thread_id and os.name=='nt':
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self.mute_hotkey_thread_id,0x0012,0,0)
            except Exception: pass
    def toggle_mute(self):
        # Mute only turns the sound off. Chat keeps being read in the background (silently), nothing is paused or thrown away.
        self.tts_muted=not self.tts_muted; self.engine.muted=self.tts_muted
        if self.tts_muted:
            if not (self._reward_ignores('mute') and self.engine.current_kind=='reward'): sd.stop()
            self._refresh_shortcut_state(); self.set_status('Sound muted. Chat keeps moving; press Ctrl+9 to hear it again.')
        else:
            self._refresh_shortcut_state(); self.set_status('Sound unmuted. You will hear chat again from the next sentence.')
    def clear_log(self): self.activity_entries.clear(); self._render_activity()
    def _poll_ui(self):
        try:
            while True:
                try: kind,value=self.ui_events.get_nowait()
                except queue.Empty: break
                try: self._process_ui_event(kind,value)
                except Exception as exc: self._report_ui_error(kind,exc)
        finally:
            self.root.after(100,self._poll_ui)
    def _report_ui_error(self,kind,exc):
        # One bad event must never stop the UI loop; show it in Activity and keep going.
        try:
            self._append_activity_text('Internal error while handling '+str(kind)+': '+type(exc).__name__+': '+str(exc))
            self.status.set('An internal error was logged in Activity. Chat is still running.')
        except Exception: pass
    def _process_ui_event(self,kind,value):
        if kind=='log': self._append_activity_text(value)
        elif kind=='emote_ready': self._emote_ready(value)
        elif kind=='emote_status': self._refresh_emote_status()
        elif kind=='toggle_mute': self.toggle_mute()
        elif kind=='toggle_pause': self.toggle_tts_pause()
        elif kind=='clear_queue': self.clear_speech_queue()
        elif kind=='dock_action': self._dock_action(value)
        elif kind=='purge_activity': self._purge_activity(*value)
        elif kind=='dash_status': dashboard.apply_status(self,*value)
        elif kind=='dash_categories': dashboard.apply_categories(self,value)
        elif kind=='dash_saved': dashboard.saved(self)
        elif kind=='dash_kick_categories': dashboard.apply_kick_categories(self,value)
        elif kind=='dash_kick_saved': dashboard.kick_saved(self)
        elif kind=='rewards_data': channel_tools.apply_rewards(self,value)
        elif kind=='rewards_reload': channel_tools.load_rewards(self)
        elif kind=='controls_data': channel_tools.apply_controls(self,value)
        elif kind=='controls_reload': channel_tools.load_controls(self)
        elif kind=='announce_done': self.announce_text.set('')
        elif kind=='viewer_card': value[0].apply(value[1])
        elif kind=='evader_log': self._evader_logged(value)
        elif kind=='alert': messagebox.showerror('Voice test failed',value)
        elif kind=='status':
            self.status.set(value)
            if value.startswith(('Twitch chat connected','Twitch chat reconnected','Twitch chat disconnected','Twitch API error','Twitch EventSub timed out','Twitch login needs renewal','Twitch login failed')) or (value.startswith('Twitch') and '(retrying)' in value):
                self.connection_state.set(value)
            if value.startswith(('Twitch chat connected','Twitch chat reconnected')):
                self._set_platform_presence('Twitch',True)
            elif value.startswith(('Twitch chat disconnected','Twitch API error','Twitch EventSub timed out','Twitch login needs renewal','Twitch login failed')) or (value.startswith('Twitch') and '(retrying)' in value):
                self._set_platform_presence('Twitch',False)
            if value.startswith('Kick chat connected'):
                self.kick_connection_state.set(value)
                self._set_platform_presence('Kick',True)
            elif value.startswith('Kick chat disconnected'):
                self.kick_connection_state.set('Kick: Not connected')
                self._set_platform_presence('Kick',False)
            elif value.startswith('Kick chat reconnecting') or value.startswith('Connecting to Kick chat'):
                self.kick_connection_state.set(value)
                self._set_platform_presence('Kick',False)
        elif kind=='incoming': self._handle_event_ui(*value)
        elif kind=='kick_login_success':
            tokens,uid,name,slug=value; self.kick_tokens=tokens; self.kick_slug=slug; self.kick_user_id=uid
            self._kick_store('refresh_token',tokens.get('refresh_token',''))
            self.kick_account_state.set('Signed in as '+(name or slug)); self.status.set('Kick login recognized.'); self._refresh_side_connections()
            if not self.kick_channel.get().strip(): self.kick_channel.set(slug)
            if self.kick_reader or self.kick_channel.get().strip(): self.root.after(100,self.connect_kick)
        elif kind=='kick_login_error':
            self.kick_account_state.set('Not signed in (guest chat reading)'); messagebox.showerror('Login with Kick',value+'\n\nIf it mentions the sign-in service, check that the Wix site is published and KICK_CLIENT_SECRET is set (see README).')
        elif kind=='kick_login_failed': self.kick_account_state.set('Not signed in (guest chat reading)')
        elif kind=='oauth_success':
            token,login,user_id=value; self.twitch_token=token; self.twitch_user_id=user_id; self.twitch_user.set(login); self.channel.set(login); self._refresh_side_connections()
            self.connection_state.set('Twitch: Logged in as '+login); self.status.set('Twitch login recognized. Connecting to your chat…'); self.root.after(100,self.connect_twitch)
    def _selected_clone_profiles(self):
        labels=[self.voice.get(),self.twitch_tts_voice.get(),self.kick_tts_voice.get()]+list(self.chat_voice_slots)
        found=[]
        for label in labels:
            info=self.voice_catalog.get(label)
            if info and info.get('kind')=='clone' and info not in found: found.append(info)
        return found
    def _after_voice_change(self):
        self._preload_selected_clone(); self._warm_up_voices()
    def _warm_up_voices(self):
        """Background: load the voices in use first (with a tiny test render), then every English voice so chat voice codes start instantly."""
        try:
            chosen=[self.voice.get(),self.twitch_tts_voice.get(),self.kick_tts_voice.get(),self.studio_voice1.get(),self.voice2.get(),self.voice3.get()]+list(self.chat_voice_slots)
        except Exception: return
        everything=[label for label,info in KOKORO_VOICE_INFO.items() if info.get('lang') in ('a','b')]
        profiles={}
        def run():
            self.engine.warm_up(chosen,profiles,render=True)
            self.engine.warm_up(everything,profiles,render=False)
        threading.Thread(target=run,daemon=True).start()
    def _preload_selected_clone(self,*_):
        # Load the cloned voice now so the first chat message does not wait for the model to start.
        if not self._clone_engine_installed(): return
        profiles=self._selected_clone_profiles()
        if profiles: threading.Thread(target=self.engine.preload_clone,args=(profiles[0],),daemon=True).start()
    def _watch_voice_vars(self):
        for var in (self.voice,self.twitch_tts_voice,self.kick_tts_voice):
            var.trace_add('write',lambda *_: self.root.after(400,self._after_voice_change))
    def close(self):
        if self.kick_login: self.kick_login.stop()
        if self.dock: self.dock.stop()
        self.disconnect()
        if self.twitch_oauth: self.twitch_oauth.stop()
        self.worker_stop.set(); self.tts_resume_event.set(); sd.stop(); self._stop_mute_hotkey()
        if self.engine.proxy is not None: self.engine.proxy.close()
        process=self.engine.clone_process
        if process and process.poll() is None:
            try:
                process.stdin.write(json.dumps({'shutdown':True})+'\n'); process.stdin.flush(); process.wait(timeout=2)
            except Exception:
                try: process.terminate()
                except Exception: pass
        self.root.destroy()

def run_selftest():
    # `ChatVoice Local.exe --selftest` (run by build_exe.py) checks that a packaged build contains everything it needs.
    results=[]
    def check(name,function):
        try: results.append(('OK',name,str(function() or '')))
        except Exception as exc: results.append(('FAIL',name,f'{type(exc).__name__}: {exc}'))
    def window():
        root=tk.Tk(); root.withdraw(); root.update(); root.destroy()
    def phonemes(lang,repo=None,text='Hello world.'):
        pipeline=KPipeline(lang_code=lang,repo_id=repo,model=False) if repo else KPipeline(lang_code=lang,model=False)
        result=next(pipeline(text)).phonemes
        assert result,'no phonemes produced'
        return result
    check('window system (Tk)',window)
    check('English text engine',lambda:phonemes('a'))
    check('Chinese text engine (v1.1)',lambda:phonemes('z',ZH11_REPO,'你好，世界。'))
    check('audio output library',lambda:sd.__version__)
    check('secure password storage',lambda:'keyring '+('available' if keyring else 'missing'))
    def emote_images():
        from PIL import features
        if not (PIL_OK and features.check('webp')): raise RuntimeError('Pillow with WebP support is missing')
        return 'Pillow with WebP'
    check('emote images (Pillow)',emote_images)
    lines=[f'{status}  {name}  {detail}'.rstrip() for status,name,detail in results]
    ok=all(status=='OK' for status,_,_ in results)
    try:
        with open(os.path.join(APP_DIR,'selftest_result.txt'),'w',encoding='utf-8') as fh: fh.write('\n'.join(lines)+'\n'+('SELFTEST PASSED' if ok else 'SELFTEST FAILED')+'\n')
    except OSError: pass
    print('\n'.join(lines))
    return ok

if __name__=='__main__':
    import multiprocessing; multiprocessing.freeze_support()
    if '--selftest' in sys.argv: sys.exit(0 if run_selftest() else 1)
    if sys.platform=='win32':
        # Gives the program its own taskbar identity so Windows shows our icon instead of Python's.
        try:
            import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ChatVoiceLocal.App')
        except Exception: pass
    root=tk.Tk()
    try: App(root); root.mainloop()
    except Exception as exc: messagebox.showerror('Startup error',str(exc))
