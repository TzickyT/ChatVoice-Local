"""Tiny local web server that powers the OBS dock (Docks > Custom Browser Docks).

It only listens on 127.0.0.1, every request needs the secret key from the dock link, and the page is plain HTML,
so OBS shows it like any other browser dock. Two pages: /dock (Activity: live chat from Twitch + Kick, a send bar,
moderation buttons) and /feed (the event feed: Bits, Kicks, subs, follows, raids, channel points).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

MAX_BODY = 16 * 1024

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChatVoice Local dock</title>
<style>
:root{color-scheme:dark;--bg:#141417;--raised:#202026;--hover:#2b2b33;--line:#2e2e37;--text:#efeff1;--muted:#9a9aa6;--accent:#e0316b;--twitch:#9146ff;--kick:#53fc18}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font:15px/1.4 "Segoe UI",system-ui,sans-serif}
body{display:flex;flex-direction:column}
#list{flex:1;overflow-y:auto;padding:8px 10px;word-wrap:break-word;overflow-wrap:anywhere}
.msg{padding:3px 0}
.badge{display:inline-block;width:18px;height:18px;line-height:18px;border-radius:4px;text-align:center;font-size:11px;font-weight:700;margin-right:6px;vertical-align:1px;color:#000}
.badge.twitch{background:var(--twitch);color:#fff}.badge.kick{background:var(--kick)}
.user{font-weight:700;cursor:pointer}.user:hover{text-decoration:underline}
.sep{color:var(--muted)}
.bimg{height:1.15em;vertical-align:middle;margin-right:3px;border-radius:3px}
.emote{height:1.7em;max-width:6em;vertical-align:middle;margin:-.25em 1px;object-fit:contain}
.zw{position:relative;display:inline-block;vertical-align:middle}.zw .emote.top{position:absolute;left:0;top:0;width:100%;height:100%;max-width:none;margin:0}.zw .emote.base{margin:-.25em 0}
#bar{display:flex;gap:6px;padding:8px;border-top:1px solid var(--line);background:var(--bg)}
select,input,button{font:inherit;color:var(--text);background:var(--raised);border:1px solid var(--line);border-radius:6px;padding:7px 9px}
input{flex:1;min-width:0}input:focus,select:focus{outline:1px solid var(--accent)}
button{cursor:pointer}button:hover:not(:disabled){background:var(--hover)}button:disabled{opacity:.45;cursor:default}
#send{background:var(--accent);border-color:var(--accent);font-weight:600}
#menu{position:fixed;z-index:5;background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:6px;min-width:190px;box-shadow:0 6px 20px #0008;display:none}
#menu .title{padding:4px 8px 6px;font-weight:700;border-bottom:1px solid var(--line);margin-bottom:4px}
#menu button{display:block;width:100%;text-align:left;margin:2px 0;border:0;background:transparent}
#menu .note{padding:4px 8px;color:var(--muted);font-size:13px}
#toast{position:fixed;left:10px;right:10px;top:8px;background:var(--raised);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:6px;padding:8px 10px;display:none;z-index:6}
#top{display:flex;align-items:center;gap:8px;padding:6px 8px;border-bottom:1px solid var(--line)}
#freeNote{color:var(--accent);font-size:13px;font-weight:600}
#sup{display:none;max-height:40%;overflow-y:auto;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--raised)}
#sup .row{display:flex;gap:6px;padding:2px 0}#sup .left{margin-left:auto;color:var(--muted);white-space:nowrap}
#offline{display:none;text-align:center;color:var(--muted);padding:6px;font-size:13px}
</style></head><body>
<div id="offline">Lost connection to ChatVoice Local. Is the app running?</div>
<div id="top"><button id="supBtn">Supporters</button><span id="freeNote"></span></div>
<div id="sup"></div>
<div id="list"></div>
<div id="toast"></div>
<div id="menu"></div>
<div id="bar">
 <select id="target"><option>All</option><option>Twitch</option><option>Kick</option></select>
 <input id="text" maxlength="500" placeholder="Send a message" autocomplete="off">
 <button id="send">Send</button>
</div>
<script>
const KEY=new URLSearchParams(location.search).get('key')||'';
const list=document.getElementById('list'),menu=document.getElementById('menu'),toast=document.getElementById('toast');
let supOpen=false,after=0,noticeAfter=0,state={send:{},mod:{},timeout:''},stick=true,toastTimer=null,confirmKey=null,confirmTimer=null;
function q(path,extra){return path+'?key='+encodeURIComponent(KEY)+(extra||'')}
async function post(body){
  const r=await fetch(q('/api/action'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok) throw new Error('HTTP '+r.status); return r.json();
}
function say(text){toast.textContent=text;toast.style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.style.display='none',5000)}
function colorFor(name){let h=0;for(const c of name)h=(h*31+c.charCodeAt(0))%360;return 'hsl('+h+' 75% 68%)'}
function emoteImg(p){const i=document.createElement('img');i.className='emote';i.alt=p.n;i.title=p.n;i.loading='lazy';i.src=q('/emote','&e='+encodeURIComponent(p.k));i.addEventListener('error',()=>{i.replaceWith(document.createTextNode(p.n))});return i}
function fillParts(box,parts){
  let last=null;
  for(const p of parts){
    if(p.t==='text'){box.appendChild(document.createTextNode(p.v));last=null;continue}
    if(p.z&&last){
      if(!last.classList.contains('zw')){const w=document.createElement('span');w.className='zw';last.replaceWith(w);last.classList.add('base');w.appendChild(last);last=w}
      const o=emoteImg(p);o.classList.add('top');last.appendChild(o);continue;
    }
    const img=emoteImg(p);box.appendChild(img);last=img;
  }
}
function addMessage(m){
  const row=document.createElement('div');row.className='msg';row.dataset.plat=m.platform;row.dataset.user=String(m.user).trim().toLowerCase();
  const badge=document.createElement('span');badge.className='badge '+m.platform.toLowerCase();badge.textContent=m.platform[0];
  const user=document.createElement('span');user.className='user';user.textContent=m.user;user.style.color=colorFor(m.user);
  user.addEventListener('click',e=>{e.stopPropagation();openMenu(m,e)});
  const sep=document.createElement('span');sep.className='sep';sep.textContent=': ';
  const text=document.createElement('span');
  if(Array.isArray(m.parts))fillParts(text,m.parts);else text.textContent=m.content;
  row.append(badge);
  for(const b of (m.badges||[])){const i=document.createElement('img');i.className='bimg';i.alt=b.n;i.title=b.n;i.src=q('/emote','&e='+encodeURIComponent(b.k));i.addEventListener('error',()=>i.remove());row.appendChild(i)}
  row.append(user,sep,text);list.appendChild(row);
}
function openMenu(m,e){
  menu.textContent='';confirmKey=null;
  const title=document.createElement('div');title.className='title';title.textContent=m.user+' ('+m.platform+')';menu.appendChild(title);
  const canMod=!!state.mod[m.platform];
  const mb=document.createElement('button');mb.textContent='Mute TTS only (app)';
  mb.addEventListener('click',async()=>{menu.style.display='none';try{await post({type:'mute_tts',platform:m.platform,name:m.user});say('Sent: Mute TTS only')}catch(err){say('Could not send: '+err.message)}});
  menu.appendChild(mb);
  if(!canMod){const n=document.createElement('div');n.className='note';n.textContent='Moderation needs you signed in to '+m.platform+' in your own channel.';menu.appendChild(n)}
  else{
    const add=(label,action,needsConfirm,enabled)=>{
      const b=document.createElement('button');b.textContent=label;b.disabled=!enabled;
      b.addEventListener('click',async()=>{
        if(needsConfirm&&confirmKey!==action){confirmKey=action;b.textContent='Click again to confirm';clearTimeout(confirmTimer);confirmTimer=setTimeout(()=>{confirmKey=null;b.textContent=label},4000);return}
        menu.style.display='none';
        try{await post({type:'moderate',action,platform:m.platform,name:m.user,user_id:m.user_id,message_id:m.message_id});say('Sent: '+label)}catch(err){say('Could not send: '+err.message)}
      });
      menu.appendChild(b);
    };
    add('Delete this message','delete',false,!!m.message_id);
    add('Timeout '+(state.timeout||''),'timeout',true,!!m.user_id);
    add('Ban from stream','ban',true,!!m.user_id);
    add('Unban','unban',false,!!m.user_id);
  }
  menu.style.display='block';
  const w=menu.offsetWidth,h=menu.offsetHeight;
  menu.style.left=Math.max(4,Math.min(e.clientX,innerWidth-w-4))+'px';
  menu.style.top=Math.max(4,Math.min(e.clientY,innerHeight-h-4))+'px';
}
function fmtLeft(sec){const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60);return h?h+' h '+m+' min':Math.max(1,m)+' min'}
function renderSupporters(){
  const sup=document.getElementById('sup'),rows=state.supporters||[];
  document.getElementById('supBtn').textContent='Supporters ('+rows.length+')';
  document.getElementById('freeNote').textContent=state.free_left>0?'Free for everyone: '+fmtLeft(state.free_left)+' left':'';
  sup.style.display=supOpen?'block':'none';
  if(!supOpen)return;
  sup.textContent='';
  if(!rows.length){const n=document.createElement('div');n.className='note';n.textContent='Nobody has paid time right now.';sup.appendChild(n);return}
  for(const r of rows){
    const row=document.createElement('div');row.className='row';
    const badge=document.createElement('span');badge.className='badge '+String(r.platform).toLowerCase();badge.textContent=String(r.platform)[0];
    const name=document.createElement('span');name.textContent=r.name;name.style.color=colorFor(r.name);name.style.fontWeight='700';
    const left=document.createElement('span');left.className='left';left.textContent=r.left;
    row.append(badge,name,left);sup.appendChild(row);
  }
}
document.getElementById('supBtn').addEventListener('click',()=>{supOpen=!supOpen;renderSupporters()});
document.addEventListener('click',e=>{if(!menu.contains(e.target))menu.style.display='none'});
list.addEventListener('scroll',()=>{stick=list.scrollTop+list.clientHeight>=list.scrollHeight-24});
let bannedSeen=null;
async function poll(){
  try{
    const r=await fetch(q('/api/state','&after='+after+'&notice='+noticeAfter));
    if(!r.ok) throw new Error('HTTP '+r.status);
    const s=await r.json();
    document.getElementById('offline').style.display='none';
    state=s.state||state;
    renderSupporters();
    if(s.reset){list.textContent='';after=0}
    for(const m of s.messages){addMessage(m);after=Math.max(after,m.id)}
    if(typeof s.state.banned_id==='number'){
      if(bannedSeen===null)bannedSeen=s.state.banned_id;
      const fresh=Math.min(s.state.banned_id-bannedSeen,(s.state.banned||[]).length);
      if(fresh>0){for(const b of s.state.banned.slice(-fresh)){for(const row of Array.from(list.children)){if(row.dataset.plat===b.platform&&row.dataset.user===b.user)row.remove()}}}
      bannedSeen=s.state.banned_id;
    }
    for(const n of s.notices){say(n.text);noticeAfter=Math.max(noticeAfter,n.id)}
    while(list.children.length>400)list.removeChild(list.firstChild);
    if(stick&&s.messages.length)list.scrollTop=list.scrollHeight;
    const t=document.getElementById('target');
    for(const o of t.options){o.disabled=(o.value!=='All'&&!state.send[o.value])}
  }catch(err){document.getElementById('offline').style.display='block'}
  setTimeout(poll,800);
}
async function send(){
  const input=document.getElementById('text'),text=input.value.trim();if(!text)return;
  input.value='';
  try{await post({type:'send',target:document.getElementById('target').value,text})}catch(err){say('Could not send: '+err.message)}
}
document.getElementById('send').addEventListener('click',send);
document.getElementById('text').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();send()}});
poll();
</script></body></html>
'''


FEED_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChatVoice Local feed</title>
<style>
:root{color-scheme:dark;--bg:#141417;--raised:#202026;--line:#2e2e37;--text:#efeff1;--muted:#9a9aa6;--accent:#e0316b;--twitch:#9146ff;--kick:#53fc18;--money:#ffd166;--follow:#3ddc84;--raid:#7cc7ff;--points:#b794ff}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font:15px/1.4 "Segoe UI",system-ui,sans-serif}
body{display:flex;flex-direction:column}
#top{display:flex;align-items:center;gap:8px;padding:8px;border-bottom:1px solid var(--line)}
#tiles{display:flex;flex-wrap:wrap;gap:6px;flex:1}
.tile{background:var(--raised);border:1px solid var(--line);border-radius:6px;padding:3px 9px;font-size:13px;color:var(--muted)}.tile b{color:var(--text);margin-left:4px}
#list{flex:1;overflow-y:auto;padding:8px 10px;overflow-wrap:anywhere}
.ev{padding:6px 0;border-bottom:1px solid var(--line)}
.label{font-weight:700;font-size:12px;letter-spacing:.04em;margin-right:6px}
.money .label{color:var(--money)}.follow .label{color:var(--follow)}.raid .label{color:var(--raid)}.points .label{color:var(--points)}
.user{font-weight:700}.meta{color:var(--muted);font-size:12px;margin-left:6px}.detail{color:var(--muted)}
.empty{color:var(--muted);text-align:center;padding:24px 10px}
#offline{display:none;text-align:center;color:var(--muted);padding:6px;font-size:13px}
</style></head><body>
<div id="offline">Lost connection to ChatVoice Local. Is the app running?</div>
<div id="top"><div id="tiles"></div>
</div>
<div id="list"></div>
<script>
const KEY=new URLSearchParams(location.search).get('key')||'';
const list=document.getElementById('list'),tiles=document.getElementById('tiles');
let version=-1,data={entries:[],totals:{}};
const NAMES=[['bits','Bits'],['kicks','Kicks'],['subs','Subs'],['gifted','Gifted'],['follows','Follows'],['raids','Raids'],['points','Points']];
function render(){
  tiles.textContent='';
  for(const [k,n] of NAMES){const t=document.createElement('span');t.className='tile';t.textContent=n;const b=document.createElement('b');b.textContent=String((data.totals||{})[k]||0);t.appendChild(b);tiles.appendChild(t)}
  list.textContent='';let shown=0;
  for(const e of data.entries){
    const row=document.createElement('div');row.className='ev '+e.group;
    const label=document.createElement('span');label.className='label';label.textContent=e.label;
    const user=document.createElement('span');user.className='user';user.textContent=e.user;
    const meta=document.createElement('span');meta.className='meta';meta.textContent=e.time+'  '+e.platform;
    const detail=document.createElement('div');detail.className='detail';detail.textContent=e.text;
    row.append(label,user,meta,detail);list.appendChild(row);shown++;
  }
  if(!shown){const n=document.createElement('div');n.className='empty';n.textContent='Nothing here yet.';list.appendChild(n)}
}
async function poll(){
  try{
    const r=await fetch('/api/feed?key='+encodeURIComponent(KEY)+'&v='+version);
    if(!r.ok) throw new Error('HTTP '+r.status);
    const s=await r.json();
    document.getElementById('offline').style.display='none';
    if(!s.same){data=s;version=s.v;render()}
  }catch(err){document.getElementById('offline').style.display='block'}
  setTimeout(poll,1000);
}
poll();
</script></body></html>
'''


class DockServer:
    def __init__(self, key, get_state, handle_action, get_feed=None, get_emote=None):
        self.key = key
        self.get_emote = get_emote          # (emote key) -> (bytes, content type) or None, for the emote images in the dock
        self.get_feed = get_feed            # (version) -> dict, for the feed dock
        self.get_state = get_state          # (after, notice_after) -> dict
        self.handle_action = handle_action  # dict -> dict
        self.httpd = None
        self.thread = None
        self.port = None

    def start(self, first_port=17892, attempts=10):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def _allowed_host(self):
                host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]').lower()
                return host in ('127.0.0.1', 'localhost')
            def _allowed_origin(self):
                origin = self.headers.get('Origin')
                if not origin: return True
                return origin.rstrip('/').lower() in ('http://127.0.0.1:%d' % server.port, 'http://localhost:%d' % server.port)
            def _reply(self, code, body, content_type='application/json; charset=utf-8', extra=None):
                data = body if isinstance(body, bytes) else body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                if not (extra and 'Cache-Control' in extra): self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                for name, value in (extra or {}).items(): self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)
            def _authorized(self, query):
                return self._allowed_host() and (query.get('key') or [''])[0] == server.key
            def do_GET(self):
                parsed = urlparse(self.path); query = parse_qs(parsed.query)
                if not self._authorized(query): return self._reply(403, '{"error":"forbidden"}')
                if parsed.path in ('/', '/dock'):
                    csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'"
                    return self._reply(200, PAGE, 'text/html; charset=utf-8', {'Content-Security-Policy': csp})
                if parsed.path == '/feed':
                    csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'"
                    return self._reply(200, FEED_PAGE, 'text/html; charset=utf-8', {'Content-Security-Policy': csp})
                if parsed.path == '/emote' and server.get_emote:
                    found = server.get_emote((query.get('e') or [''])[0][:80])
                    if not found: return self._reply(404, '{"error":"not found"}')
                    return self._reply(200, found[0], found[1], {'Cache-Control': 'private, max-age=86400'})
                if parsed.path == '/api/feed' and server.get_feed:
                    try: version = int((query.get('v') or ['-1'])[0])
                    except ValueError: return self._reply(400, '{"error":"bad request"}')
                    return self._reply(200, json.dumps(server.get_feed(version)))
                if parsed.path == '/api/state':
                    try: after = int((query.get('after') or ['0'])[0]); notice = int((query.get('notice') or ['0'])[0])
                    except ValueError: return self._reply(400, '{"error":"bad request"}')
                    return self._reply(200, json.dumps(server.get_state(after, notice)))
                self._reply(404, '{"error":"not found"}')
            def do_POST(self):
                parsed = urlparse(self.path); query = parse_qs(parsed.query)
                if not self._authorized(query) or not self._allowed_origin(): return self._reply(403, '{"error":"forbidden"}')
                if parsed.path != '/api/action': return self._reply(404, '{"error":"not found"}')
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if length <= 0 or length > MAX_BODY: raise ValueError
                    payload = json.loads(self.rfile.read(length).decode('utf-8'))
                    if not isinstance(payload, dict): raise ValueError
                except (ValueError, UnicodeDecodeError): return self._reply(400, '{"error":"bad request"}')
                try: result = server.handle_action(payload)
                except Exception as exc: return self._reply(500, json.dumps({'error': str(exc)[:200]}))
                self._reply(200, json.dumps(result))

        last_error = None
        for port in range(first_port, first_port + attempts):
            try:
                self.httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
                self.httpd.daemon_threads = True
                self.port = port
                break
            except OSError as exc: last_error = exc
        if not self.httpd: raise OSError('No free port for the OBS dock server: %s' % last_error)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.port

    def stop(self):
        if self.httpd:
            try: self.httpd.shutdown(); self.httpd.server_close()
            except Exception: pass
            self.httpd = None

    def url(self, kind='activity'):
        page = 'feed' if kind == 'feed' else 'dock'
        return 'http://127.0.0.1:%d/%s?key=%s' % (self.port, page, self.key) if self.port else ''
