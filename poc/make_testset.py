"""Generate 30 s query clips with synthetic live-event degradations.

Conditions (per sampled track):
  clean        - untouched excerpt
  pink10       - pink noise at 10 dB SNR
  crowd3       - crowd babble + applause/claps + cheer swells at 3 dB SNR
  crowd0_room  - crowd at 0 dB SNR plus room reverb and a 7 kHz low-pass (phone/stream-like)
  speed+3      - played 3% faster (tempo and pitch, like a DJ pitch fader) + crowd at 6 dB
  speed-3      - played 3% slower + crowd at 6 dB
Held-out tracks (not in the DB) get the crowd3 treatment to measure false positives.

Usage: python poc/make_testset.py [--n 100] [--n-holdout 40] [--seed 1]
"""
from __future__ import annotations

import argparse
import csv
import random
import subprocess
import wave
from pathlib import Path

import numpy as np

from common import FFMPEG, HOLDOUT_FILE, ROOT, list_audio, panako_id

SR = 22050
CLIP_S = 30
OUT_DIR = ROOT / "poc" / "testset"
FFPROBE = str(Path(FFMPEG).with_name("ffprobe.exe")) if FFMPEG.endswith(".exe") else "ffprobe"


def duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def decode(path: Path, offset: float, seconds: float) -> np.ndarray:
    out = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{offset:.2f}", "-t", f"{seconds:.2f}",
                          "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"],
                         capture_output=True)
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def write_wav(path: Path, x: np.ndarray) -> None:
    peak = np.max(np.abs(x)) or 1.0
    x = x / peak * 0.9 if peak > 0.9 else x
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2)) + 1e-12)


def band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    X[1:] /= np.sqrt(f[1:])
    X[0] = 0
    return np.fft.irfft(X, n)


def envelope(n: int, rng: np.random.Generator, rate_hz: float) -> np.ndarray:
    """Slowly varying positive envelope (random, smoothed)."""
    k = max(2, int(n / SR * rate_hz) + 2)
    pts = rng.uniform(0.2, 1.0, k)
    return np.interp(np.arange(n), np.linspace(0, n - 1, k), pts)


def crowd(n: int, rng: np.random.Generator) -> np.ndarray:
    # Babble: many speech-band noise "voices" each with its own syllable-rate modulation.
    babble = np.zeros(n)
    for _ in range(12):
        v = band(rng.standard_normal(n), rng.uniform(150, 400), rng.uniform(1800, 3500))
        babble += v * envelope(n, rng, rng.uniform(2, 6))
    # Applause: dense random clap transients during a few bursts.
    claps = np.zeros(n)
    clap_len = int(0.012 * SR)
    clap_shape = np.exp(-np.linspace(0, 6, clap_len))
    for _ in range(rng.integers(2, 5)):
        start = rng.integers(0, n - SR * 3)
        length = int(rng.uniform(2, 6) * SR)
        for t in rng.integers(start, min(n - clap_len, start + length), int(length / SR * 40)):
            claps[t:t + clap_len] += rng.standard_normal(clap_len) * clap_shape * rng.uniform(0.3, 1)
    claps = band(claps, 800, 9000)
    # Cheers: broadband swells.
    cheer = band(rng.standard_normal(n), 300, 6000) * envelope(n, rng, 0.3) ** 3
    parts = [babble / rms(babble), claps / rms(claps) * 0.8, cheer / rms(cheer) * 0.6]
    return sum(parts)


def mix(music: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    noise = noise * (rms(music) / rms(noise)) / (10 ** (snr_db / 20))
    return music + noise


def room(x: np.ndarray, rng: np.random.Generator, rt60: float = 0.6) -> np.ndarray:
    n_ir = int(rt60 * SR)
    ir = rng.standard_normal(n_ir) * np.exp(-6.9 * np.arange(n_ir) / n_ir)
    ir[0] = 4.0
    ir /= np.sqrt(np.sum(ir ** 2))
    n = len(x) + n_ir
    y = np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(ir, n), n)[:len(x)]
    return band(y, 60, 7000)


def resample_speed(x: np.ndarray, factor: float) -> np.ndarray:
    idx = np.arange(0, len(x) - 1, factor)
    return np.interp(idx, np.arange(len(x)), x)


def make_conditions(x: np.ndarray, rng: np.random.Generator) -> dict[str, np.ndarray]:
    n = len(x)
    return {
        "clean": x,
        "pink10": mix(x, pink(n, rng), 10),
        "crowd3": mix(x, crowd(n, rng), 3),
        "crowd0_room": room(mix(x, crowd(n, rng), 0), rng),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--n-holdout", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    pyrng = random.Random(args.seed)

    holdout = set(HOLDOUT_FILE.read_text(encoding="utf-8").splitlines())
    indexed = [p for p in list_audio() if str(p) not in holdout]
    # A held-out file whose byte-identical copy was indexed is not really "unknown".
    indexed_ids = {panako_id(p) for p in indexed}
    unknown = sorted(h for h in holdout if panako_id(h) not in indexed_ids)
    picks = pyrng.sample(indexed, min(args.n, len(indexed)))
    held = pyrng.sample(unknown, min(args.n_holdout, len(unknown)))

    rows = []
    jobs = [(p, True) for p in picks] + [(Path(p), False) for p in held]
    for i, (track, in_db) in enumerate(jobs):
        dur = duration(track)
        if dur < CLIP_S + 5:
            continue
        # Read a bit extra so speed changes still leave a full clip.
        offset = pyrng.uniform(min(10, dur * 0.1), max(10.0, dur - CLIP_S * 1.1 - 2))
        raw = decode(track, offset, CLIP_S * 1.05)
        if len(raw) < SR * CLIP_S:
            continue
        x = raw[:SR * CLIP_S]
        if in_db:
            conds = make_conditions(x, rng)
            for name, f in (("speed+3", 1.03), ("speed-3", 0.97)):
                y = resample_speed(raw, f)[:SR * CLIP_S]
                conds[name] = mix(y, crowd(len(y), rng), 6)
        else:
            conds = {"holdout_crowd3": mix(x, crowd(len(x), rng), 3)}
        for name, y in conds.items():
            out = OUT_DIR / name / f"clip{i:04d}_{name}.wav"
            write_wav(out, y.astype(np.float64))
            rows.append({"clip": str(out), "condition": name,
                         "expected": str(track) if in_db else "", "offset": f"{offset:.2f}",
                         "source": str(track)})
        print(f"[{i + 1}/{len(jobs)}] {track.name}", flush=True)

    with (OUT_DIR / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["clip", "condition", "expected", "offset", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} clips -> {OUT_DIR / 'manifest.csv'}")


if __name__ == "__main__":
    main()
