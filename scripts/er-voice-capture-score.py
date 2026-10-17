#!/usr/bin/env python3
"""Score a capture of Elden Ring's output stream against the PCM the voice probe pushed.

    uv run --with numpy python3 scripts/er-voice-capture-score.py <capture.wav> [--ref target/voice-mock/tts-48k.pcm]

Prints the RMS of each 0.5 s window (s16 units, mono mix) and the peak normalised
cross-correlation of the reference (s16le 48 kHz mono, first 2 s) against the capture, with the
lag it occurred at. Read-only.
"""

import argparse
import pathlib
import wave

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('capture', type=pathlib.Path)
    ap.add_argument('--ref', type=pathlib.Path, default=REPO / 'target' / 'voice-mock' / 'tts-48k.pcm')
    ap.add_argument('--seconds', type=float, default=2.0)
    a = ap.parse_args()
    with wave.open(str(a.capture), 'rb') as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f'expected s16 capture, got {width * 8} bit')
    x = np.frombuffer(raw, '<i2').astype(np.float64).reshape(-1, ch).mean(axis=1)
    print(f'{a.capture.name}: {len(x) / rate:.2f} s, {rate} Hz, {ch} ch')
    win = rate // 2
    rms = [float(np.sqrt(np.mean(x[i:i + win] ** 2))) for i in range(0, len(x) - win + 1, win)]
    print('rms per 0.5 s: ' + ' '.join(f's{i}={v:.0f}' for i, v in enumerate(rms)))
    ref = np.frombuffer(a.ref.read_bytes()[: int(a.seconds * 48000) * 2], '<i2').astype(np.float64)
    if rate != 48000:
        raise SystemExit('capture is not 48 kHz')
    n = len(ref)
    if len(x) < n:
        raise SystemExit('capture shorter than the reference')
    ref = (ref - ref.mean()) / (np.linalg.norm(ref - ref.mean()) or 1.0)
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    corr = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(ref, size)), size)[: len(x) - n + 1]
    c1 = np.concatenate(([0.0], np.cumsum(x)))
    c2 = np.concatenate(([0.0], np.cumsum(x * x)))
    s1 = c1[n:] - c1[:-n]
    s2 = c2[n:] - c2[:-n]
    norm = np.sqrt(np.maximum(s2 - s1 * s1 / n, 1e-9))
    ncc = corr / norm
    i = int(np.argmax(np.abs(ncc)))
    print(f'peak ncc {ncc[i]:+.3f} at {i / rate:.3f} s (|ncc| max over all lags)')


if __name__ == '__main__':
    main()
