"""Fine-tunes a Qwen3-TTS voice from one or more longer recordings (about 10 minutes).

Started by ChatVoice Local in its own console window, using the .qwen3tts-env Python.
Steps: cut the recordings into clips, write down what is said (local Whisper),
build the training file, run the official Qwen3-TTS fine-tuning scripts, and
register the finished voice so it shows up in the app.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

SR = 24000  # Qwen3-TTS works with 24 kHz mono audio
WHISPER_CODES = {'English': 'en', 'Chinese': 'zh', 'Japanese': 'ja', 'Korean': 'ko', 'German': 'de', 'French': 'fr',
                 'Russian': 'ru', 'Portuguese': 'pt', 'Spanish': 'es', 'Italian': 'it'}
BASE_MODELS = {'0.6B': 'Qwen/Qwen3-TTS-12Hz-0.6B-Base', '1.7B': 'Qwen/Qwen3-TTS-12Hz-1.7B-Base'}


def resample(x, rate):
    if rate == SR:
        return x.astype(np.float32)
    try:
        import torch
        import torchaudio
        return torchaudio.functional.resample(torch.from_numpy(x.astype(np.float32)), rate, SR).numpy()
    except Exception:
        n = int(len(x) * SR / rate)
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def load_mono(path):
    import soundfile as sf
    data, rate = sf.read(path, dtype='float32', always_2d=True)
    return resample(data.mean(axis=1), rate)


def split_audio(x, min_s=4.0, max_s=18.0, silence_s=0.35):
    """Returns (start, end) sample ranges of speech clips, cut at pauses."""
    frame = int(SR * 0.02)
    n = len(x) // frame
    if n < 10:
        return []
    rms = np.sqrt((x[:n * frame].reshape(n, frame) ** 2).mean(axis=1))
    threshold = max(0.004, float(np.percentile(rms, 20)) * 1.5)
    quiet = rms < threshold
    need = max(1, int(silence_s / 0.02))
    cuts, run_start = [0], None
    for i in range(n + 1):
        is_quiet = i < n and quiet[i]
        if is_quiet and run_start is None:
            run_start = i
        elif not is_quiet and run_start is not None:
            if i - run_start >= need:
                cuts.append((run_start + i) // 2)
            run_start = None
    cuts.append(n)
    pieces = [(a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
    max_f, min_f = int(max_s / 0.02), int(min_s / 0.02)
    # break pieces that are too long at their quietest point
    refined = []
    for a, b in pieces:
        while b - a > max_f:
            lo, hi = a + int(max_f * 0.6), a + max_f
            cut = lo + int(np.argmin(rms[lo:hi]))
            refined.append((a, cut)); a = cut
        refined.append((a, b))
    # join short neighbours into clips of a useful length
    clips, cur = [], None
    for a, b in refined:
        if cur is None:
            cur = [a, b]
        elif (cur[1] - cur[0] < min_f) and (b - cur[0] <= max_f):
            cur[1] = b
        else:
            clips.append(tuple(cur)); cur = [a, b]
    if cur is not None:
        if clips and (cur[1] - cur[0]) < int(2.0 / 0.02) and (cur[1] - clips[-1][0]) <= max_f:
            clips[-1] = (clips[-1][0], cur[1])
        else:
            clips.append(tuple(cur))
    pad = int(SR * 0.08)
    out = []
    for a, b in clips:
        s, e = max(0, a * frame - pad), min(len(x), b * frame + pad)
        if (e - s) / SR >= 2.0:
            out.append((s, e))
    return out


def pick_reference(clips):
    """clips: list of (path, seconds, loudness). Prefers one 5-10 s long and clearly audible."""
    good = [c for c in clips if 5.0 <= c[1] <= 10.0]
    if good:
        return max(good, key=lambda c: c[2])[0]
    longer = [c for c in clips if c[1] >= 3.0]
    return (max(longer, key=lambda c: c[2]) if longer else clips[0])[0]


def patch_attention(path):
    """The official scripts ask for FlashAttention 2, which is not available on Windows; use the built-in attention."""
    with open(path, 'r', encoding='utf-8') as fh:
        source = fh.read()
    if 'flash_attention_2' not in source:
        return path
    patched = os.path.join(os.path.dirname(path), 'patched_' + os.path.basename(path))
    with open(patched, 'w', encoding='utf-8') as fh:
        fh.write(source.replace('flash_attention_2', 'sdpa'))
    return patched


def find_checkpoint(output_dir):
    found = [p for p in glob.glob(os.path.join(output_dir, 'checkpoint*')) if os.path.isdir(p)]
    if found:
        def epoch(p):
            m = re.findall(r'\d+', os.path.basename(p))
            return int(m[-1]) if m else -1
        return sorted(found, key=epoch)[-1]
    if os.path.exists(os.path.join(output_dir, 'config.json')):
        return output_dir
    raise RuntimeError('Training finished but no checkpoint folder was found in ' + output_dir)


def run(cmd, cwd):
    print('\n> ' + ' '.join(cmd) + '\n', flush=True)
    code = subprocess.call(cmd, cwd=cwd)
    if code != 0:
        raise RuntimeError('A training step failed (exit code %d). The messages above say why.' % code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--language', default='English')
    ap.add_argument('--model-size', default='0.6B', choices=['0.6B', '1.7B'])
    ap.add_argument('--epochs', type=int, default=5)
    ap.add_argument('--batch-size', type=int, default=2)
    ap.add_argument('--lr', type=float, default=2e-5)
    ap.add_argument('--app-dir', required=True)
    ap.add_argument('--recordings', nargs='+', required=True)
    a = ap.parse_args()

    import soundfile as sf
    safe = re.sub(r'[^a-z0-9_]', '', a.name.lower().replace(' ', '_')) or 'my_voice'
    work = os.path.join(a.app_dir, 'finetune_work', safe)
    clips_dir = os.path.join(work, 'clips')
    out_dir = os.path.join(work, 'output')
    ft_dir = os.path.join(a.app_dir, 'qwen3tts-engine', 'finetuning')
    for script in ('prepare_data.py', 'sft_12hz.py'):
        if not os.path.isfile(os.path.join(ft_dir, script)):
            raise RuntimeError('The fine-tuning scripts are missing (' + script + '). Run "Install local clone engine" again.')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(clips_dir)

    print('Step 1/5: cutting the recordings into clips...', flush=True)
    clips = []
    for path in a.recordings:
        x = load_mono(path)
        peak = float(np.max(np.abs(x))) or 1.0
        x = x * (0.9 / peak)
        for s, e in split_audio(x):
            clip = x[s:e]
            target = os.path.join(clips_dir, 'clip_%04d.wav' % (len(clips) + 1))
            sf.write(target, clip, SR, subtype='PCM_16')
            clips.append((target, len(clip) / SR, float(np.sqrt((clip ** 2).mean()))))
    total = sum(c[1] for c in clips)
    print('  %d clips, %.1f minutes of speech.' % (len(clips), total / 60), flush=True)
    if total < 60:
        raise RuntimeError('Only %.0f seconds of usable speech were found. Fine-tuning needs at least a few minutes (about 10 is best).' % total)
    if total < 300:
        print('  Warning: less than 5 minutes of speech. It may work, but 10 minutes gives a better result.', flush=True)

    print('\nStep 2/5: writing down what is said in each clip (first run downloads a small speech model)...', flush=True)
    from faster_whisper import WhisperModel
    whisper = WhisperModel('small', device='cpu', compute_type='int8')
    ref_audio = pick_reference(clips)
    shutil.copy2(ref_audio, os.path.join(work, 'ref.wav'))
    ref_audio = os.path.join(work, 'ref.wav')
    rows = []
    for index, (path, seconds, _rms) in enumerate(clips, 1):
        segments, _ = whisper.transcribe(path, language=WHISPER_CODES.get(a.language), beam_size=1)
        text = ' '.join(s.text.strip() for s in segments).strip()
        print('  [%d/%d] %s' % (index, len(clips), text), flush=True)
        if len(text) >= 3:
            rows.append({'audio': os.path.abspath(path), 'text': text, 'ref_audio': os.path.abspath(ref_audio)})
    del whisper
    if len(rows) < 5:
        raise RuntimeError('Too few clips could be transcribed (%d). Use a clearer recording in the chosen language.' % len(rows))
    raw = os.path.join(work, 'train_raw.jsonl')
    with open(raw, 'w', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    print('\nStep 3/5: converting the clips for training...', flush=True)
    coded = os.path.join(work, 'train_with_codes.jsonl')
    prepare = patch_attention(os.path.join(ft_dir, 'prepare_data.py'))
    run([sys.executable, prepare, '--device', 'cuda:0', '--tokenizer_model_path', 'Qwen/Qwen3-TTS-Tokenizer-12Hz',
         '--input_jsonl', raw, '--output_jsonl', coded], ft_dir)

    print('\nStep 4/5: training (this takes a while; keep this window open)...', flush=True)
    sft = patch_attention(os.path.join(ft_dir, 'sft_12hz.py'))
    run([sys.executable, sft, '--init_model_path', BASE_MODELS[a.model_size], '--output_model_path', out_dir,
         '--train_jsonl', coded, '--batch_size', str(a.batch_size), '--lr', str(a.lr), '--num_epochs', str(a.epochs),
         '--speaker_name', safe], ft_dir)

    print('\nStep 5/5: adding the voice to ChatVoice Local...', flush=True)
    checkpoint = find_checkpoint(out_dir)
    final = os.path.join(a.app_dir, 'custom_voices', 'finetuned', safe)
    shutil.rmtree(final, ignore_errors=True)
    os.makedirs(final)
    shutil.copytree(checkpoint, os.path.join(final, 'model'))
    with open(os.path.join(final, 'voice.json'), 'w', encoding='utf-8') as fh:
        json.dump({'name': a.name, 'language': a.language, 'speaker': safe, 'model': 'model', 'base': BASE_MODELS[a.model_size]}, fh, ensure_ascii=False, indent=2)
    print('\nDone. Go back to ChatVoice Local and click "Refresh voice list". The voice is called "%s".' % a.name, flush=True)
    print('Earlier training checkpoints (if you want to compare) are in: ' + out_dir, flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('\nTraining stopped: ' + str(exc), flush=True)
    input('\nPress Enter to close this window')
