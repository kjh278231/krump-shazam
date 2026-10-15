"""Rhythm (onset-envelope) matching: a reverb-robust fallback for when Panako finds nothing.

Room recordings smear the spectral peaks Panako fingerprints, but the timing of drum hits
survives. Each track is reduced to band-wise onset envelopes (~43 frames/s); a query is
cross-correlated against them and scored as a z-score of the correlation peak.
"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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

    def __init__(self, path, exclude=frozenset(), cohort=None, cohort_wide=None):
        """exclude: normcased paths never to return, or a predicate on the stored path. cohort: path
        of rhythm_cohort.npz (measured with SPEEDS_FAST); when given, `search` also reports
        z = (score - track mean) / track std. cohort_wide: the same for SPEEDS_WIDE."""
        z = np.load(path)
        self.offsets, self.ids, self.paths = z["offsets"], z["ids"], z["paths"]
        is_excluded = exclude if callable(exclude) else (lambda p: os.path.normcase(p) in exclude)
        self.excluded = np.array([is_excluded(str(p)) for p in self.paths])

        def load_cohort(p):
            if p is None or not Path(p).exists():
                return None
            c = np.load(p)
            return c["mean"].astype(np.float32), c["std"].astype(np.float32)
        self.cohort, self.cohort_wide = load_cohort(cohort), load_cohort(cohort_wide)
        data = z["data"].astype(np.float32)
        self.total = data.shape[1]
        self.hop = self.N - self.MAX_Q
        starts = np.arange(0, self.total, self.hop)
        pad = np.zeros((data.shape[0], starts[-1] + self.N - self.total), np.float32)
        data = np.concatenate([data, pad], axis=1)
        self.F = np.stack([np.fft.rfft(data[:, s:s + self.N], axis=1) for s in starts]).astype(np.complex64)
        lengths = np.diff(self.offsets)
        self.nonempty = np.nonzero(lengths > 0)[0]

    def _corr(self, q: np.ndarray) -> np.ndarray:
        """Correlation at every global frame position (length = chunks * hop)."""
        Q = np.conj(np.fft.rfft(q, self.N, axis=1)).astype(np.complex64)
        out = np.empty((len(self.F), self.hop), np.float32)
        for i in range(0, len(self.F), 64):  # batches keep memory bounded
            c = np.fft.irfft((self.F[i:i + 64] * Q[None]).sum(1), self.N, axis=1)
            out[i:i + 64] = c[:, :self.hop]
        return out.reshape(-1)[:self.total] / (q.shape[0] * q.shape[1])

    def _search_speed(self, q: np.ndarray, s: float) -> tuple[np.ndarray, np.ndarray]:
        """Best score and its position (frame within the track) per non-empty track at speed s."""
        # Query frame k was heard at library time k*s: stretch the query onto the library timeline.
        m = int(round(q.shape[1] * s))
        qs = np.stack([np.interp(np.arange(m) / s, np.arange(q.shape[1]), b) for b in q]).astype(np.float32)
        c = self._corr(qs)
        # A window must lie inside one track: drop the last m-1 start positions of each track.
        starts, ends = self.offsets[self.nonempty], self.offsets[self.nonempty + 1]
        for a, b in zip(starts, ends):
            c[max(a, b - m + 1):b] = -np.inf
        mx = np.maximum.reduceat(c, starts)
        pos = np.array([np.argmax(c[a:b]) for a, b in zip(starts, ends)], np.int64)
        return mx, pos

    def search_full(self, q: np.ndarray, speeds=(1.0,), top: int = 10, cohort=None,
                    threads: int = 1) -> tuple[list[dict], np.ndarray, int | None]:
        """Best positions per track over the given playback speeds (query speed / library speed).

        cohort: (mean, std) measured with these speeds (default self.cohort). threads: speeds
        searched at once (numpy FFTs release the GIL; each one holds ~0.5 GB of temporaries).
        Returns (top hits by raw score, per-track best raw score, id of the top track by z or None).
        """
        cohort = self.cohort if cohort is None else cohort
        best = np.full(len(self.ids), -np.inf, np.float32)
        best_pos = np.zeros(len(self.ids), np.int64)
        best_speed = np.ones(len(self.ids), np.float32)
        if threads > 1:
            with ThreadPoolExecutor(threads) as ex:
                per_speed = list(ex.map(lambda s: self._search_speed(q, s), speeds))
        else:
            per_speed = (self._search_speed(q, s) for s in speeds)
        idx = self.nonempty
        for s, (mx, pos) in zip(speeds, per_speed):  # speed order decides ties, as before
            better = mx > best[idx]
            ti = idx[better]
            best[ti] = mx[better]
            best_pos[ti] = pos[better]
            best_speed[ti] = s
        usable = np.isfinite(best) & ~self.excluded
        # Ranking stays on the raw score: z alone over-rewards tracks with a tiny impostor spread
        # (top-1 on room clips: raw 73/100, z 64/100). z is reported for the confidence check.
        rank = np.where(usable, best, -np.inf)
        z = np.full_like(best, np.nan)
        z_top = None
        if cohort is not None:
            with np.errstate(invalid="ignore"):  # tracks shorter than the query stay -inf -> nan
                z = (best - cohort[0]) / cohort[1]
            zr = np.where(usable & np.isfinite(z), z, -np.inf)
            z_top = int(self.ids[int(np.argmax(zr))])
        order = np.argsort(-rank)[:top]
        hits = [{"id": int(self.ids[t]), "path": str(self.paths[t]), "score": float(best[t]),
                 "z": float(z[t]), "pos": best_pos[t] / FPS, "speed": float(best_speed[t])}
                for t in order]
        return hits, best, z_top

    def search(self, q: np.ndarray, speeds=(1.0,), top: int = 10) -> list[dict]:
        """search_full for single-threaded calibration scripts: also keeps the per-track best raw
        score (last_best) and the z winner (last_z_top) on the index."""
        hits, self.last_best, self.last_z_top = self.search_full(q, speeds, top)
        return hits


def per_speed_scores(index: RhythmIndex, q: np.ndarray, speeds, threads: int = 1) -> np.ndarray:
    """(speeds, tracks) best raw score of each track at each speed, for calibration; the max over
    axis 0 is search()'s per-track score."""
    out = np.full((len(speeds), len(index.ids)), -np.inf, np.float32)
    with ThreadPoolExecutor(threads) as ex:
        for i, (mx, _) in enumerate(ex.map(lambda s: index._search_speed(q, s), speeds)):
            out[i, index.nonempty] = mx
    return out


SPEEDS = (0.98, 0.99, 1.0, 1.01, 1.02)
SPEEDS_FAST = (0.99, 1.0, 1.01)  # the cohort (rhythm_cohort.py) is measured with these
# Re-uploads and edits are sometimes played faster or slower (shorts hdnJN-k6ePg: 1.03). A 30 s
# query loses its match about 0.3% off the true speed, so the grid needs 0.005 steps.
SPEEDS_WIDE = tuple(round(0.95 + 0.005 * i, 3) for i in range(21))  # 0.95 .. 1.05
# Calibrated on realtest/rhythm_z.jsonl (exclusions applied). "agree" = the raw-score winner is
# also the cohort-normalised (z) winner, i.e. it is not just a track that matches everything.
#   strong: ratio >= 1.2 and agree           room clips 43 shown/42 right, unknown songs 3/39
#   weak:   ratio >= 1.3 or (agree, z >= 4)  room clips 54/52 (incl. strong), unknown songs 10/39
STRONG_RATIO = 1.2
WEAK_RATIO = 1.3
WEAK_Z = 4.0
# Second stage over SPEEDS_WIDE, only when the SPEEDS_FAST search shows nothing. Calibrated on
# realtest/rhythm_wide_report.txt (poc/eval_rhythm_wide.py); added on top of the first stage:
#   room clips played 1.2-4.8 % off speed: 13 more shown, all right (first stage alone: 6 shown/1 right)
#   unknown songs (39 at 1.0, 39 off speed): 1 more shown (strong), at 1.0 none
#   user captures: WLDLF 07 at 1.03 (shorts hdnJN-k6ePg) found, strong
# The 1.2/1.3/4.0 thresholds of the first stage added 3 unknown songs instead of 1.
WIDE_STRONG_RATIO = 1.25
WIDE_WEAK_RATIO = 1.3
WIDE_WEAK_Z = 5.0


def identify(index: RhythmIndex, y: np.ndarray, same, top: int = 3, wide: bool = False,
             threads: int = 1) -> dict | None:
    """Rhythm candidates for audio `y` (SR mono), or None when nothing stands out.

    `same(id_a, id_b)` says whether two library ids hold the same recording; the ratio is taken
    against the best *different* song. Never a confirmation: always offered for a by-ear check.
    wide: search SPEEDS_WIDE with its own cohort and thresholds (a second stage when the
    SPEEDS_FAST search found nothing; needs index.cohort_wide).
    """
    if wide and index.cohort_wide is None:
        return None
    if wide:
        speeds, cohort, strong_ratio, weak_ratio, weak_z = (SPEEDS_WIDE, index.cohort_wide,
                                                            WIDE_STRONG_RATIO, WIDE_WEAK_RATIO, WIDE_WEAK_Z)
    else:
        speeds, cohort, strong_ratio, weak_ratio, weak_z = SPEEDS_FAST, index.cohort, STRONG_RATIO, WEAK_RATIO, WEAK_Z
    hits, _, z_top = index.search_full(envelope(y), speeds=speeds, top=12, cohort=cohort, threads=threads)
    songs = []
    for h in hits:
        if not any(same(h["id"], s["id"]) for s in songs):
            songs.append(h)
    if not songs:
        return None
    best = songs[0]
    ratio = best["score"] / songs[1]["score"] if len(songs) > 1 else 99.0
    agree = z_top is not None and same(z_top, best["id"])
    if ratio >= strong_ratio and agree:
        strength = "strong"
    elif ratio >= weak_ratio or (agree and best["z"] >= weak_z):
        strength = "weak"
    else:
        return None
    return {"songs": songs[:top], "ratio": ratio, "z": best["z"], "strength": strength}


def xcorr(ref: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Valid-mode cross-correlation summed over bands, normalised per query frame."""
    n = ref.shape[1] + q.shape[1]
    nfft = 1 << (n - 1).bit_length()
    c = np.fft.irfft((np.fft.rfft(ref, nfft) * np.conj(np.fft.rfft(q, nfft))).sum(0), nfft)
    return c[: ref.shape[1] - q.shape[1] + 1] / (q.shape[1] * q.shape[0])
