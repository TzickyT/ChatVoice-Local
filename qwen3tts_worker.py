"""Background worker for ChatVoice Local voice cloning (Qwen3-TTS).

Runs inside the separate .qwen3tts-env Python environment. The app talks to it
through one JSON object per line on stdin/stdout. Everything the libraries
print goes to stderr (qwen3tts.log in the app folder).
"""
import contextlib
import json
import os
import re
import sys

CLONE_MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-Base'
LANGUAGES = {'English', 'Chinese', 'Japanese', 'Korean', 'German', 'French', 'Russian', 'Portuguese', 'Spanish', 'Italian'}
WHISPER_CODES = {'English': 'en', 'Chinese': 'zh', 'Japanese': 'ja', 'Korean': 'ko', 'German': 'de', 'French': 'fr',
                 'Russian': 'ru', 'Portuguese': 'pt', 'Spanish': 'es', 'Italian': 'it'}

engine_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
os.makedirs(engine_dir, exist_ok=True)
os.chdir(engine_dir)


def reply(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


try:
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel
    reply({'ready': True, 'cuda': bool(torch.cuda.is_available())})
except Exception as exc:  # the app shows this message plus the end of the log
    reply({'ready': False, 'error': 'Could not load the Qwen3-TTS libraries: ' + str(exc)})
    raise SystemExit(1)

_models = {}      # only one model is kept in memory so a 12 GB card is never overfilled
_prompts = {}
_whisper = None


def load_model(path):
    if path in _models:
        return _models[path]
    _models.clear()
    _prompts.clear()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    cuda = torch.cuda.is_available()
    kwargs = {'device_map': 'cuda:0' if cuda else 'cpu', 'dtype': torch.bfloat16 if cuda else torch.float32}
    print('Loading voice model: ' + path, file=sys.stderr, flush=True)
    try:
        model = Qwen3TTSModel.from_pretrained(path, attn_implementation='sdpa', **kwargs)
    except TypeError:
        model = Qwen3TTSModel.from_pretrained(path, **kwargs)
    _models[path] = model
    return model


def transcribe(audio_path, language):
    """Writes down what is said in the reference clip (CPU, small model) so the cloner can match it."""
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel('small', device='cpu', compute_type='int8')
    segments, _info = _whisper.transcribe(audio_path, language=WHISPER_CODES.get(language), beam_size=1, vad_filter=True)
    return ' '.join(segment.text.strip() for segment in segments).strip()


def reference_text(audio_path, language, given):
    sidecar = os.path.splitext(audio_path)[0] + '.txt'
    if given and given.strip():
        return given.strip()
    try:
        with open(sidecar, 'r', encoding='utf-8') as fh:
            text = fh.read().strip()
        if text:
            return text
    except OSError:
        pass
    try:
        text = transcribe(audio_path, language)
    except Exception as exc:
        print('Transcription failed (' + str(exc) + '); using the speaker fingerprint only.', file=sys.stderr, flush=True)
        return ''
    if text:
        try:
            with open(sidecar, 'w', encoding='utf-8') as fh:
                fh.write(text)
        except OSError:
            pass
    return text


def token_limit(text):
    # The model makes ~12 audio tokens per second and people speak ~15 characters per second, so
    # 4 tokens per character is a generous ceiling that still stops a runaway generation early.
    return int(max(48, min(1024, len(text) * 4)))


_SENTENCE_END = re.compile(r'(?<=[.!?。！？…])\s+|\n+')


def split_text(text, first_max=140, later_max=220):
    """Splits a chat message into sentences so the first one can start playing while the rest are made."""
    pieces = [p.strip() for p in _SENTENCE_END.split(text.strip()) if p and p.strip()]
    chunks = []
    for piece in pieces:
        limit = first_max if not chunks else later_max
        if chunks and len(chunks[-1]) < 25 and len(chunks[-1]) + len(piece) + 1 <= limit:
            chunks[-1] += ' ' + piece      # never speak tiny fragments on their own
        else:
            chunks.append(piece)
    return chunks or [text.strip()]


def to_audio(wavs):
    audio = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    if hasattr(audio, 'detach'):
        audio = audio.detach().float().cpu().numpy()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def clone_prompt(request, model, model_path, language):
    reference = request['reference_audio']
    ref_text = reference_text(reference, language, request.get('ref_text'))
    key = (model_path, reference, os.path.getmtime(reference), ref_text)
    if key not in _prompts:
        if ref_text:
            _prompts[key] = model.create_voice_clone_prompt(ref_audio=reference, ref_text=ref_text, x_vector_only_mode=False)
        else:
            _prompts[key] = model.create_voice_clone_prompt(ref_audio=reference, x_vector_only_mode=True)
    return _prompts[key]


def prepare(request):
    """Loads the model and builds the voice prompt ahead of time, then makes one tiny sound to warm the GPU up."""
    language = request.get('lang') if request.get('lang') in LANGUAGES else 'Auto'
    with torch.inference_mode():
        if request.get('kind') == 'finetuned':
            model = load_model(request['model_dir'])
            model.generate_custom_voice(text='Hello.', speaker=request['speaker'], language=language, max_new_tokens=48)
        else:
            model_path = request.get('model') or CLONE_MODEL
            model = load_model(model_path)
            prompt = clone_prompt(request, model, model_path, language)
            model.generate_voice_clone(text='Hello.', language=language, voice_clone_prompt=prompt, max_new_tokens=48)


def generate_chunks(request):
    """Yields (audio, sample_rate) for each sentence of the message."""
    language = request.get('lang') if request.get('lang') in LANGUAGES else 'Auto'
    finetuned = request.get('kind') == 'finetuned'
    if finetuned:
        model = load_model(request['model_dir'])
    else:
        model_path = request.get('model') or CLONE_MODEL
        model = load_model(model_path)
        prompt = clone_prompt(request, model, model_path, language)
    for piece in split_text(request['text']):
        with torch.inference_mode():
            if finetuned:
                wavs, sample_rate = model.generate_custom_voice(
                    text=piece, speaker=request['speaker'], language=language, max_new_tokens=token_limit(piece))
            else:
                wavs, sample_rate = model.generate_voice_clone(
                    text=piece, language=language, voice_clone_prompt=prompt, max_new_tokens=token_limit(piece))
        yield to_audio(wavs), sample_rate


for line in sys.stdin:
    try:
        request = json.loads(line)
        if request.get('shutdown'):
            break
        if request.get('prepare'):
            with contextlib.redirect_stdout(sys.stderr):
                prepare(request)
            reply({'ok': True, 'prepared': True})
            continue
        if not (request.get('text') or '').strip():
            raise ValueError('There is no text to speak.')
        base, ext = os.path.splitext(request['output_path'])
        with contextlib.redirect_stdout(sys.stderr):
            for index, (audio, sample_rate) in enumerate(generate_chunks(request)):
                path = '%s_%d%s' % (base, index, ext or '.wav')
                sf.write(path, audio, sample_rate)
                reply({'chunk': path})      # the app starts playing this while the next sentence is generated
        reply({'ok': True})
    except Exception as exc:
        reply({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)})
