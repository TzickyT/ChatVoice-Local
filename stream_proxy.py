"""Stream audio proxy.

OBS's "Application Audio Capture" picks up the sound of one program. To send only some of ChatVoice Local's voices to
the stream, those voices are played by this small helper program (its own window, its own process) instead of by the
main app. OBS captures the helper, the streamer still hears it on their normal speakers, and nobody needs a second
audio device or a virtual cable.

Two halves live here:
  ProxyClient  - used by the main app: starts the helper, sends it audio, tells it to stop.
  run()        - the helper itself (started with `--stream-proxy`).
"""
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time

PROXY_TITLE = 'ChatVoice Stream Audio'
PROXY_CHOICE = 'ChatVoice Stream Audio (for OBS Application Audio Capture)'
HEADER = struct.Struct('<cIII')  # kind, sample rate, channels, frames
CHUNK = 2048


def _read_exact(stream, count):
    data = b''
    while len(data) < count:
        piece = stream.read(count - len(data))
        if not piece: return None
        data += piece
    return data


# ---------------------------------------------------------------------------------------------------------------
# Main-app side
# ---------------------------------------------------------------------------------------------------------------
class ProxyClient:
    """command: function(port, token, device) -> argument list that starts the helper."""

    def __init__(self, command, notify=None, cwd=None):
        self.command = command; self.notify = notify or (lambda message: None); self.cwd = cwd
        self.proc = None; self.conn = None; self.device = None; self.lock = threading.RLock()

    def running(self):
        return self.proc is not None and self.proc.poll() is None and self.conn is not None

    def start(self, device=None):
        """Starts (or restarts, if the speaker device changed) the helper. Returns True when it is ready."""
        with self.lock:
            if self.running() and device == self.device: return True
            self.close()
            token = secrets.token_hex(8)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(0.3)
                flags = 0x08000000 if sys.platform == 'win32' else 0  # CREATE_NO_WINDOW: the helper has its own window
                self.proc = subprocess.Popen(self.command(listener.getsockname()[1], token, device), cwd=self.cwd,
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                conn = None; deadline = time.time() + 20
                while conn is None:  # wait for the helper, but give up at once if it already quit
                    try: conn, _addr = listener.accept()
                    except socket.timeout:
                        if self.proc.poll() is not None: raise RuntimeError('the helper program quit right after starting')
                        if time.time() > deadline: raise RuntimeError('the helper program did not answer')
                conn.settimeout(5)
                got = _read_exact(conn.makefile('rb'), len(token))
                if got is None or got.decode('ascii', 'ignore') != token: raise RuntimeError('the helper did not identify itself')
                conn.settimeout(None)
                self.conn = conn; self.device = device
                return True
            except Exception as exc:
                self.notify('Could not start the stream audio window: ' + str(exc).splitlines()[0][:140])
                self.close()
                return False
            finally:
                try: listener.close()
                except OSError: pass

    def _send(self, kind, rate=0, channels=0, frames=0, payload=b''):
        with self.lock:
            if not self.running(): return False
            try:
                self.conn.sendall(HEADER.pack(kind, rate, channels, frames) + payload)
                return True
            except OSError:
                self.close(); return False

    def play(self, audio, sample_rate):
        """audio: float32 numpy array, 1-D (mono) or 2-D (frames, channels). Returns True if the helper took it."""
        import numpy as np
        data = np.asarray(audio, dtype=np.float32)
        if data.ndim == 1: data = np.repeat(data[:, None], 2, axis=1)
        data = np.ascontiguousarray(data)
        return self._send(b'A', int(sample_rate), data.shape[1], data.shape[0], data.tobytes())

    def stop(self):
        return self._send(b'S')

    def show_window(self):
        return self._send(b'W')

    def close(self):
        with self.lock:
            conn, proc = self.conn, self.proc
            self.conn = None; self.proc = None
            if conn is not None:
                try: conn.sendall(HEADER.pack(b'Q', 0, 0, 0))
                except OSError: pass
                try: conn.close()
                except OSError: pass
            if proc is not None and proc.poll() is None:
                try: proc.wait(timeout=2)
                except Exception:
                    try: proc.terminate()
                    except Exception: pass


# ---------------------------------------------------------------------------------------------------------------
# Helper side
# ---------------------------------------------------------------------------------------------------------------
def _arg(argv, name, default=None):
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv): return argv[index + 1]
    return default


def run(argv=None):
    argv = list(sys.argv if argv is None else argv)
    port = int(_arg(argv, '--port', '0')); token = _arg(argv, '--token', '')
    device = _arg(argv, '--device')
    device = int(device) if device not in (None, '', 'None') else None
    headless = '--no-window' in argv
    if not port: return 1
    state = {'gen': 0, 'level': 0.0, 'status': 'Waiting for sound', 'quit': False, 'show': False}
    pending = []  # [(gen, rate, channels, raw)]
    cond = threading.Condition()

    conn = socket.create_connection(('127.0.0.1', port), timeout=10)
    conn.settimeout(None); conn.sendall(token.encode('ascii'))
    reader_file = conn.makefile('rb')

    def reader():
        try:
            while True:
                head = _read_exact(reader_file, HEADER.size)
                if head is None: break
                kind, rate, channels, frames = HEADER.unpack(head)
                if kind == b'A':
                    raw = _read_exact(reader_file, frames * channels * 4)
                    if raw is None: break
                    with cond: pending.append((state['gen'], rate, channels, raw)); cond.notify()
                elif kind == b'S':
                    with cond: state['gen'] += 1; del pending[:]; cond.notify()
                elif kind == b'W': state['show'] = True
                elif kind == b'Q': break
        except OSError:
            pass
        with cond: state['quit'] = True; cond.notify_all()

    def player():
        import numpy as np
        stream = None; fmt = None
        try:
            import sounddevice as sd
        except Exception as exc:
            state['status'] = 'Audio is unavailable: ' + str(exc).splitlines()[0][:100]; sd = None
        while True:
            with cond:
                while not pending and not state['quit']: cond.wait()
                if state['quit']: break
                gen, rate, channels, raw = pending.pop(0)
            if sd is None or gen != state['gen']: continue
            try:
                data = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)
                if fmt != (rate, channels):
                    if stream is not None: stream.close()
                    stream = sd.OutputStream(samplerate=rate, device=device, channels=channels, dtype='float32'); stream.start(); fmt = (rate, channels)
                state['status'] = 'Playing'
                for start in range(0, len(data), CHUNK):
                    if gen != state['gen'] or state['quit']: break
                    chunk = np.ascontiguousarray(data[start:start + CHUNK])
                    state['level'] = float(np.abs(chunk).max()); stream.write(chunk)
            except Exception as exc:
                state['status'] = 'Audio error: ' + str(exc).splitlines()[0][:100]
                try:
                    if stream is not None: stream.close()
                except Exception: pass
                stream = None; fmt = None
            else:
                state['status'] = 'Waiting for sound'
            state['level'] = 0.0
        try:
            if stream is not None: stream.close()
        except Exception: pass

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=player, daemon=True).start()

    if headless:
        with cond:
            while not state['quit']: cond.wait(0.5)
        return 0

    import tkinter as tk
    root = tk.Tk(); root.title(PROXY_TITLE); root.geometry('420x190'); root.resizable(False, False)
    bg, fg, muted, accent = '#14171c', '#eceff4', '#9aa3b2', '#ff4f8b'
    root.configure(bg=bg)
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    try:
        if sys.platform == 'win32': root.iconbitmap(os.path.join(here, 'icon.ico'))
    except Exception: pass
    tk.Label(root, text=PROXY_TITLE, bg=bg, fg=fg, font=('Segoe UI', 12, 'bold')).pack(anchor='w', padx=16, pady=(14, 2))
    tk.Label(root, text='In OBS: Sources, +, Application Audio Capture, Window, then pick "%s". Keep this window open (you can minimize it).' % PROXY_TITLE,
             bg=bg, fg=muted, font=('Segoe UI', 9), wraplength=388, justify='left').pack(anchor='w', padx=16)
    status = tk.StringVar(value=state['status'])
    tk.Label(root, textvariable=status, bg=bg, fg=fg, font=('Segoe UI', 10)).pack(anchor='w', padx=16, pady=(12, 4))
    meter = tk.Canvas(root, width=388, height=10, bg='#232933', highlightthickness=0); meter.pack(padx=16, anchor='w')
    bar = meter.create_rectangle(0, 0, 0, 10, fill=accent, width=0)
    root.protocol('WM_DELETE_WINDOW', root.iconify)  # closing only minimizes, so OBS keeps finding the window

    def tick():
        if state['quit']: root.destroy(); return
        if state['show']: state['show'] = False; root.deiconify(); root.lift()
        status.set(state['status'])
        meter.coords(bar, 0, 0, int(388 * min(1.0, state['level'])), 10)
        root.after(70, tick)
    tick()
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(run())
