"""VivaJot wake word bench: compare models on the same audio, the way the speaker detects.

Reports, for every model and cutoff:
  - false wakes per hour on continuous talk audio
  - recall on "VivaJot" at several voice levels, from normal to very soft
and picks the most sensitive cutoff that stays inside the false-wake limit.

Mirrors ESPHome micro_wake_word (streaming_model.cpp, micro_wake_word.cpp):
  - features scaled to int8 with the firmware's integer math
  - quantized cutoff int(cutoff * 255); fires when the sum of the last 5 raw outputs > cutoff * 5
  - VAD (stock vad.tflite, cutoff 0.5, window 5) must also pass, or the wake is blocked
  - after a wake, 100 10 ms slices below cutoff before the next one can fire

Run on Colab or locally:
  python bench.py --model deployed=vivajot.tflite --model new=new.tflite --vad vad.tflite \
      --talk talk_audio/ --pos positives/ --out results/
  python bench.py --selftest
"""
import argparse
import glob
import json
import os

import numpy as np

CUTOFFS = [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.97]
WINDOW = 5
COOLDOWN_SLICES = 100
STRIDE = 3
VAD_CUTOFF = int(0.5 * 255)
# Peak level at the model's input. 300 is about a quiet answer in the hub's STT log (peak 314).
LEVELS = {'normal': 6000, 'quiet': 1500, 'soft': 400, 'very_soft': 120}
NOISE_RMS = 30          # room noise floor, about -60 dBFS
ROOM_SEEDS = (1, 2, 3)  # the same three rooms for every model
FALSE_WAKE_LIMIT = 1.0  # per hour of continuous talk; a few a day with the TV on


def load_interpreter(path):
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        from tensorflow.lite.python.interpreter import Interpreter
    i = Interpreter(model_path=path)
    i.allocate_tensors()
    return i


def load_audio(path):
    import soundfile as sf
    d, sr = sf.read(path, dtype='float32', always_2d=True)
    d = d.mean(axis=1)
    if sr != 16000:
        if sr > 16000:  # anti-alias below 8 kHz before interpolating
            t = np.arange(-40, 41)
            fir = np.sinc(2 * 7600 / sr * t) * np.hamming(len(t))
            d = np.convolve(d, fir / fir.sum(), mode='same')
        d = np.interp(np.arange(int(len(d) * 16000 / sr)) * sr / 16000, np.arange(len(d)), d).astype(np.float32)
    return (np.clip(d, -1, 1) * 32767).astype(np.int16)


def features(pcm):
    """int8 features exactly as the firmware feeds them to the model, one row per 10 ms."""
    from pymicro_features import MicroFrontend
    fe = MicroFrontend()
    raw = pcm.tobytes()
    out, pos = [], 0
    while pos + 320 < len(raw):
        r = fe.process_samples(raw[pos:pos + 320])
        pos += r.samples_read * 2
        if r.features:
            out.append(r.features)
    if not out:
        return np.zeros((0, 40), dtype=np.int8)
    # pymicro_features returns value * 0.0390625; undo that to get the frontend's integers.
    vals = np.rint(np.array(out, dtype=np.float64) / 0.0390625).astype(np.int32)
    q = (vals * 256 + 333) // 666 - 128
    return np.clip(q, -128, 127).astype(np.int8)


class Stream:
    def __init__(self, path):
        self.i = load_interpreter(path)
        self.inp = self.i.get_input_details()[0]
        self.out = self.i.get_output_details()[0]

    def run(self, feats):
        """Raw uint8 outputs, one per 30 ms, from a fresh streaming state."""
        self.i.reset_all_variables()
        n = len(feats) // STRIDE
        res = np.zeros(n, dtype=np.uint8)
        for k in range(n):
            self.i.set_tensor(self.inp['index'], feats[k * STRIDE:(k + 1) * STRIDE].reshape(self.inp['shape']))
            self.i.invoke()
            res[k] = self.i.get_tensor(self.out['index']).flat[0]
        return res


def detect(probs, vad, cutoff):
    """Indices of inferences that wake the speaker at this cutoff. vad=None skips the VAD gate."""
    c = int(cutoff * 255)
    buf = [0] * WINDOW
    slot = 0
    ignore = -COOLDOWN_SLICES
    last = 0
    vbuf = [0] * WINDOW
    hits = []
    for k, p in enumerate(probs):
        p = int(p)
        slot = (slot + 1) % WINDOW
        ignore = min(ignore + 2 * (last < c), 0)  # the two slices before this inference
        buf[slot] = p
        last = p
        if p < c:
            ignore = min(ignore + 1, 0)
        if vad is None:
            vad_on = True
        else:
            vbuf[slot] = int(vad[k])
            vad_on = sum(vbuf) > VAD_CUTOFF * WINDOW
        if ignore >= 0 and sum(buf) > c * WINDOW and vad_on:
            hits.append(k)
            buf = [0] * WINDOW
            last = 0
            ignore = -COOLDOWN_SLICES
    return hits


def room_impulse(seed, rt60=0.45, direct=0.35):
    """Small synthetic room, so every model hears exactly the same reverb."""
    rng = np.random.default_rng(seed)
    n = int(rt60 * 16000)
    tail = rng.normal(size=n) * np.exp(-6.9 * np.arange(n) / n)
    ir = (1 - direct) * tail / np.sqrt((tail ** 2).sum())
    ir[0] += direct
    return ir


def place_in_room(pcm, peak, seed, pad_s=1.5):
    """Render a clip at the mic: room reverb, a set peak level, and the room's noise floor."""
    rng = np.random.default_rng(seed + 1000)
    ir = room_impulse(seed)
    n = len(pcm) + len(ir) - 1
    x = np.fft.irfft(np.fft.rfft(pcm.astype(np.float64), n) * np.fft.rfft(ir, n), n)
    x *= peak / max(np.abs(x).max(), 1.0)
    pad = int(pad_s * 16000)
    x = np.concatenate([np.zeros(pad), x, np.zeros(pad)])
    x += rng.normal(scale=NOISE_RMS, size=len(x))
    return np.clip(np.rint(x), -32768, 32767).astype(np.int16)


def false_wakes(streams, vad_stream, files, max_hours=None):
    """{model: {cutoff: wakes per hour}} plus the hours of audio and each wake's file and time."""
    counts = {m: {c: 0 for c in CUTOFFS} for m in streams}
    times = {m: [] for m in streams}
    secs = 0.0
    for f in files:
        feats = features(load_audio(f))
        vad = vad_stream.run(feats)
        for m, s in streams.items():
            probs = s.run(feats)
            for c in CUTOFFS:
                hits = detect(probs, vad, c)
                counts[m][c] += len(hits)
                if c == 0.90:
                    times[m] += [(os.path.basename(f), round(h * 0.03, 1)) for h in hits]
        secs += len(feats) * 0.01
        if max_hours and secs >= max_hours * 3600:
            break
    hours = secs / 3600
    faph = {m: {c: counts[m][c] / max(hours, 1e-9) for c in CUTOFFS} for m in streams}
    return faph, hours, times


def recall(streams, vad_stream, files, levels=LEVELS):
    """{model: {level: {cutoff: fraction of clips that woke}}}. Each clip is heard in every room."""
    got = {m: {l: {c: 0 for c in CUTOFFS} for l in levels} for m in streams}
    n = 0
    for f in files:
        pcm = load_audio(f)
        for seed in ROOM_SEEDS:
            n += 1
            for l, peak in levels.items():
                feats = features(place_in_room(pcm, peak, seed))
                vad = vad_stream.run(feats)
                for m, s in streams.items():
                    probs = s.run(feats)
                    for c in CUTOFFS:
                        got[m][l][c] += bool(detect(probs, vad, c))
    return {m: {l: {c: v / max(n, 1) for c, v in d.items()} for l, d in lv.items()} for m, lv in got.items()}, n


def pick_cutoff(faph_by_cutoff, limit=FALSE_WAKE_LIMIT):
    """Most sensitive cutoff inside the limit: keep real calls, accept occasional false wakes."""
    ok = [c for c in CUTOFFS if faph_by_cutoff[c] <= limit]
    return min(ok) if ok else None


def mine(stream, vad_stream, path, cutoff=0.5, seconds=3.0):
    """Hard negatives: the float spectrogram leading up to each wake, in training's scale."""
    pcm = load_audio(path)
    feats = features(pcm)
    hits = detect(stream.run(feats), vad_stream.run(feats), cutoff)
    n = int(seconds * 100)
    spec = (feats.astype(np.float32) + 128) * 0.1019607857
    return [spec[max(0, h * STRIDE - n):h * STRIDE] for h in hits if h * STRIDE >= n]


def report(faph, hours, rec, n_clips):
    lines = [f'# Wake word bench\n\nTalk audio: {hours:.2f} h. Clips: {n_clips} (clip x room).\n',
             '## False wakes per hour on talk\n',
             '| Model | ' + ' | '.join(f'{c:.2f}' for c in CUTOFFS) + ' |',
             '|---|' + '---|' * len(CUTOFFS)]
    for m, d in faph.items():
        lines.append(f'| {m} | ' + ' | '.join(f'{d[c]:.1f}' for c in CUTOFFS) + ' |')
    for level in LEVELS:
        lines += [f'\n## Recall, {level} voice\n',
                  '| Model | ' + ' | '.join(f'{c:.2f}' for c in CUTOFFS) + ' |',
                  '|---|' + '---|' * len(CUTOFFS)]
        for m in rec:
            lines.append(f'| {m} | ' + ' | '.join(f'{rec[m][level][c] * 100:.0f}%' for c in CUTOFFS) + ' |')
    lines += [f'\n## Best setting (false wakes <= {FALSE_WAKE_LIMIT}/h)\n',
              '| Model | Cutoff | False wakes/h | ' + ' | '.join(LEVELS) + ' |',
              '|---|---|---|' + '---|' * len(LEVELS)]
    for m in faph:
        c = pick_cutoff(faph[m])
        if c is None:
            lines.append(f'| {m} | none meets the limit | | ' + ' | '.join('' for _ in LEVELS) + ' |')
        else:
            lines.append(f'| {m} | {c:.2f} | {faph[m][c]:.1f} | '
                         + ' | '.join(f'{rec[m][l][c] * 100:.0f}%' for l in LEVELS) + ' |')
    return '\n'.join(lines) + '\n'


def selftest():
    # One long peak fires once; the buffer resets and it must drop below cutoff for 1 s to refire.
    assert detect([0] * 40 + [255] * 20 + [0] * 40, None, 0.9) == [44]
    # Average just under cutoff never fires, even with single outputs at 100%.
    assert detect([0] * 40 + [255, 200] * 40, None, 0.95) == []
    # No wake during the start-up second.
    assert detect([255] * 20, None, 0.9) == []
    # VAD off blocks the wake; VAD on lets it through.
    p = [0] * 40 + [255] * 10
    assert detect(p, [0] * 50, 0.9) == []
    assert detect(p, [255] * 50, 0.9) == [44]
    # Two wakes need 100 slices (34 inferences) below cutoff between them.
    assert len(detect([0] * 40 + [255] * 5 + [0] * 40 + [255] * 5, None, 0.9)) == 2
    assert len(detect([0] * 40 + [255] * 5 + [0] * 20 + [255] * 5, None, 0.9)) == 1
    # cutoff quantizes like the firmware
    assert int(0.90 * 255) == 229
    assert pick_cutoff({c: (0.5 if c >= 0.8 else 9.0) for c in CUTOFFS}) == 0.80
    print('selftest OK')


def audio_files(d):
    return sorted(f for e in ('wav', 'flac', 'mp3', 'ogg') for f in glob.glob(os.path.join(d, '**', '*.' + e), recursive=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', action='append', default=[], help='name=path.tflite')
    ap.add_argument('--vad', default='vad.tflite')
    ap.add_argument('--talk', help='folder of continuous talk audio')
    ap.add_argument('--pos', help='folder of "VivaJot" clips')
    ap.add_argument('--out', default='bench_results')
    ap.add_argument('--max-hours', type=float)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    streams = {n: Stream(p) for n, p in (m.split('=', 1) for m in a.model)}
    vad = Stream(a.vad)
    faph, hours, times = false_wakes(streams, vad, audio_files(a.talk), a.max_hours)
    rec, n = recall(streams, vad, audio_files(a.pos))
    os.makedirs(a.out, exist_ok=True)
    text = report(faph, hours, rec, n)
    with open(os.path.join(a.out, 'bench.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    with open(os.path.join(a.out, 'bench.json'), 'w', encoding='utf-8') as f:
        json.dump({'hours': hours, 'clips': n, 'false_wakes_per_hour': faph, 'recall': rec, 'wakes_at_0.90': times}, f, indent=1)
    print(text)


if __name__ == '__main__':
    main()
