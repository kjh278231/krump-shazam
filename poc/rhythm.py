"""Rhythm (onset-envelope) matching: a reverb-robust fallback for when Panako finds nothing.

Room recordings smear the spectral peaks Panako fingerprints, but the timing of drum hits
survives. Each track is reduced to band-wise onset envelopes (~43 frames/s); a query is
cross-correlated against them and scored as a z-score of the correlation peak.
"""
from __future__ import annotations

import subprocess

import numpy as np

from common import FFMPEG

SR = 11025
N_FFT = 1024
HOP = 256
FPS = SR / HOP
# Low end is lost in camera/phone recordings; keep bands that survive a room.
BANDS = [(150, 600), (600, 2500), (2500, 5500)]


def decode(path, offset: float = 0, duration: float | None = None) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error", "-ss", str(offset)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, dtype=np.float32)


def envelope(y: np.ndarray) -> np.ndarray:
    """(bands, frames) float32: positive spectral flux per band, locally normalised."""
    if len(y) < N_FFT:
        return np.zeros((len(BANDS), 0), np.float32)
    n = 1 + (len(y) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    S = np.abs(np.fft.rfft(y[idx] * np.hanning(N_FFT), axis=1)).T  # (freq, frames)
    S = np.log1p(100 * S)
    f = np.fft.rfftfreq(N_FFT, 1 / SR)
    out = []
    for lo, hi in BANDS:
        b = S[(f >= lo) & (f < hi)]
        flux = np.maximum(0, np.diff(b, axis=1, prepend=b[:, :1])).sum(0)
        k = int(FPS * 1.5)  # remove the slow trend (~1.5 s), keep the beat
        flux = flux - np.convolve(flux, np.ones(k) / k, mode="same")
        out.append(flux / (flux.std() + 1e-9))
    return np.asarray(out, np.float32)


class RhythmIndex:
    """Whole-library search. The concatenated library envelopes are split into overlapping
    chunks whose FFTs are precomputed (overlap-save), so one query costs a multiply + inverse
    FFT per chunk."""

    N = 1 << 16           # chunk length (frames)
    MAX_Q = 2048          # longest query in frames (~47 s)

    def __init__(self, path):
        z = np.load(path)
        self.offsets, self.ids, self.paths = z["offsets"], z["ids"], z["paths"]
        data = z["data"].astype(np.float32)
        self.total = data.shape[1]
        self.hop = self.N - self.MAX_Q
        starts = np.arange(0, self.total, self.hop)
        pad = np.zeros((data.shape[0], starts[-1] + self.N - self.total), np.float32)
        data = np.concatenate([data, pad], axis=1)
        self.F = np.stack([np.fft.rfft(data[:, s:s + self.N], axis=1) for s in starts]).astype(np.complex64)
        self.track_of = None

    def _corr(self, q: np.ndarray) -> np.ndarray:
        """Correlation at every global frame position (length = chunks * hop)."""
        Q = np.conj(np.fft.rfft(q, self.N, axis=1)).astype(np.complex64)
        out = np.empty((len(self.F), self.hop), np.float32)
        for i in range(0, len(self.F), 64):  # batches keep memory bounded
            c = np.fft.irfft((self.F[i:i + 64] * Q[None]).sum(1), self.N, axis=1)
            out[i:i + 64] = c[:, :self.hop]
        return out.reshape(-1)[:self.total] / (q.shape[0] * q.shape[1])

    def search(self, q: np.ndarray, speeds=(1.0,), top: int = 10) -> list[dict]:
        """Best positions per track over the given playback speeds (query speed / library speed)."""
        best = np.full(len(self.ids), -np.inf, np.float32)
        best_pos = np.zeros(len(self.ids), np.int64)
        best_speed = np.ones(len(self.ids), np.float32)
        lengths = np.diff(self.offsets)
        for s in speeds:
            # Query frame k was heard at library time k*s: stretch the query onto the library timeline.
            m = int(round(q.shape[1] * s))
            qs = np.stack([np.interp(np.arange(m) / s, np.arange(q.shape[1]), b) for b in q]).astype(np.float32)
            c = self._corr(qs)
            # A window must lie inside one track.
            tr = np.searchsorted(self.offsets, np.arange(len(c)), side="right") - 1 if self.track_of is None else self.track_of
            self.track_of = tr
            valid = (np.arange(len(c)) + m) <= self.offsets[tr + 1]
            c = np.where(valid, c, -np.inf)
            mx = np.maximum.reduceat(c, self.offsets[:-1][lengths > 0])
            idx = np.nonzero(lengths > 0)[0]
            better = mx > best[idx]
            if better.any():
                ti = idx[better]
                best[ti] = mx[better]
                best_speed[ti] = s
                # position of the max within each improved track
                for t in ti:
                    a, b = self.offsets[t], self.offsets[t + 1]
                    best_pos[t] = int(np.argmax(c[a:b]))
        self.last_best = best  # per-track best score of the last search (for calibration)
        order = np.argsort(-best)[:top]
        return [{"id": int(self.ids[t]), "path": str(self.paths[t]), "score": float(best[t]),
                 "pos": best_pos[t] / FPS, "speed": float(best_speed[t])} for t in order]


SPEEDS = (0.98, 0.99, 1.0, 1.01, 1.02)
SPEEDS_FAST = (0.99, 1.0, 1.01)
# Calibrated on realtest/rhythm_eval.csv + rhythm_split.csv: shown candidates were right for
# 32/35 room-reverb clips and 6/6 windows of a real hall video; 6/39 unknown songs still got one,
# so rhythm results are never "confirmed", only offered for a by-ear check.
SHOW_RATIO = 1.3


def identify(index: RhythmIndex, y: np.ndarray, same, top: int = 3) -> dict | None:
    """Rhythm candidates for audio `y` (SR mono), or None when nothing stands out.

    `same(id_a, id_b)` says whether two library ids hold the same recording. A candidate is
    shown when the best song beats the best *different* song by SHOW_RATIO, or when two
    overlapping halves of the query independently agree on song and position.
    """
    def ranked(e):
        hits = index.search(e, speeds=SPEEDS, top=12)
        songs = []
        for h in hits:
            if not any(same(h["id"], s["id"]) for s in songs):
                songs.append(h)
        return songs

    songs = ranked(envelope(y))
    if not songs:
        return None
    ratio = songs[0]["score"] / songs[1]["score"] if len(songs) > 1 else 99.0
    consistent = False
    dur = len(y) / SR
    if ratio < SHOW_RATIO and dur >= 24:  # second chance; halves of >= 16 s each
        third = dur / 3
        a = ranked(envelope(y[: int(2 * third * SR)]))[0]
        b = ranked(envelope(y[int(third * SR):]))[0]
        consistent = (same(a["id"], songs[0]["id"]) and a["id"] == b["id"]
                      and abs((b["pos"] - a["pos"]) - third * a["speed"]) < 1.0)
    if ratio < SHOW_RATIO and not consistent:
        return None
    return {"songs": songs[:top], "ratio": ratio, "consistent": consistent}


def xcorr(ref: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Valid-mode cross-correlation summed over bands, normalised per query frame."""
    n = ref.shape[1] + q.shape[1]
    nfft = 1 << (n - 1).bit_length()
    c = np.fft.irfft((np.fft.rfft(ref, nfft) * np.conj(np.fft.rfft(q, nfft))).sum(0), nfft)
    return c[: ref.shape[1] - q.shape[1] + 1] / (q.shape[1] * q.shape[0])
