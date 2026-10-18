"""Pair-hash fingerprint: the second fingerprint stage, after Panako (server/app.py).

Why: Panako hashes three peaks, so in hall reverb and crowd noise a hash survives with p^3, where a
Shazam-style pair hash (Wang 2003) survives with p^2. On the 409 battle-100 windows the rhythm search
found, Panako confirms 29 and this index 290, with no answer on 622 clips of songs that are not in the
library (docs/fp-experiments-e1-e6.md, E5; experiment code on branch fp-experiments). Pairs are not
speed-invariant: videos played 3% fast or slow are left to Panako and the rhythm search.

Front end (audfprint conventions): 11025 Hz mono, 512-point STFT, hop 256 (23 ms), log magnitude, a
per-bin high-pass over time that emphasises onsets, local maxima over 31 bins x 9 frames, then at most
20 peaks per second (loudest first).
Hash: anchor (t1, f1) with each of its next 3 peaks within dt 1..63 frames and |df| <= 31 bins:
f1 (8 bits) | df (6 bits) | dt (6 bits) = 20 bits. Stored per hash: track and t1.
Match: offsets t_db - t_query per track, counted with +-1 frame tolerance; score = hashes at the best
offset. Queries are fingerprinted at 4 sub-hop shifts and the hashes pooled, as audfprint does.

The index holds the files of the fingerprint DB (its LMDB resource map) under their Panako ids, so
db/same_audio.csv links and grouping work on its matches unchanged.

Usage: python poc/pairfp.py build   -> db/pair.npz (~20 min)
"""
from __future__ import annotations

import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter

from common import DB_DIR, FFMPEG, MUSIC_DIR, ROOT, Match, list_audio

INDEX_FILE = ROOT / "db" / "pair.npz"
SR = 11025
NFFT = 512
HOP = 256
DENSITY = 20  # peaks per second
FANOUT = 3
FW, TW = 15, 4  # half-widths of the local-max neighbourhood (bins, frames)
DT_MAX = 63
DF_MAX = 31
MAX_BUCKET = 500  # hashes found in more places than this carry little information (audfprint keeps 100)
SHIFTS = 4
HASH_BITS = 20
T_BITS = 19  # an index entry is track << T_BITS | t1: t1 < 3.4 h, at most 8192 files
# An answer is confirmed from this many hashes at one offset (and no other song at 0.6 of that),
# interpolated between (seconds, score) points. Wrong answers on 3,842 test clips cut to 10-30 s
# (branch fp-experiments, poc/fpexp/e7_pair_lengths.py): songs not in the library and noisy or
# off-speed library songs score at most 20 at 10 s and 29 from 15 s on (shared drum loops: the
# matching hashes span the clip, at one offset). Both bars sit 3-4 above that.
CONFIRM = ((10, 24), (15, 32))


def confirm_score(seconds: float) -> float:
    return float(np.interp(seconds, *zip(*CONFIRM)))


# Below that bar, a rhythm candidate (server/app.py) is confirmed when it is this index's top song with
# AGREE or more (no other song at 0.6 of that), on up to AGREE_SECONDS of audio, the lengths measured.
# Branch fp-experiments, poc/fpexp/e9_rhythm_pair.py (E9c): on 622 clips of songs not in the library, cut
# to the 10-30 s the extension sends, the rhythm search's (wrong) candidates were the top song at 18 at
# most (30 s; 14 at 24 s, 11 at 15-20 s), each time with a rival at 0.6, so never by this rule. On
# battle-100 it confirms 50 more 30 s windows (414 -> 464).
AGREE = 20
AGREE_SECONDS = 30


def onset_filter(L: np.ndarray) -> np.ndarray:
    """scipy.signal.lfilter([1, -1], [1, -0.98], L, axis=1), bit for bit (float64, direct form II
    transposed). Importing scipy.signal adds ~1 s to a Lambda cold start."""
    L = L.astype(np.float64)
    y = np.empty_like(L)
    z = np.zeros(L.shape[0])
    for n in range(L.shape[1]):
        x = L[:, n]
        yn = x + z
        y[:, n] = yn
        z = -x - (-0.98) * yn
    return y


def peaks(y: np.ndarray, shift: int = 0) -> np.ndarray:
    """(t, f) peaks, sorted by time then frequency."""
    y = y[shift:]
    if len(y) < NFFT:
        return np.zeros((0, 2), np.int32)
    n = 1 + (len(y) - NFFT) // HOP
    frames = y[np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]] * np.hanning(NFFT)[None, :]
    S = np.abs(np.fft.rfft(frames, axis=1)).T.astype(np.float32)  # (bins, frames)
    if S.shape[1] < 3:
        return np.zeros((0, 2), np.int32)
    L = np.log(np.maximum(S, S.max() * 1e-6 + 1e-12))
    L -= L.mean()
    L = onset_filter(L)
    L = L[:256]  # 0 - 5.5 kHz
    is_max = (L == maximum_filter(L, size=(2 * FW + 1, 2 * TW + 1), mode="constant", cval=-np.inf)) & (L > 0)
    f, t = np.nonzero(is_max)
    v = L[f, t]
    # Density: keep the loudest peaks in each 1 s block.
    block = (t * HOP / SR).astype(np.int32)
    order = np.lexsort((-v, block))
    f, t, block = f[order], t[order], block[order]
    first = np.r_[0, np.flatnonzero(np.diff(block)) + 1]
    rank = np.arange(len(block)) - np.repeat(first, np.diff(np.r_[first, len(block)]))
    keep = rank < DENSITY
    pk = np.stack([t[keep], f[keep]], 1).astype(np.int32)
    return pk[np.lexsort((pk[:, 1], pk[:, 0]))]


def hashes(pk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(hash, t1) arrays."""
    n = len(pk)
    if n < 2:
        return np.zeros(0, np.uint32), np.zeros(0, np.int32)
    t, f = pk[:, 0], pk[:, 1]
    count = np.zeros(n, np.int32)
    hs, ts = [], []
    # Walk forward over later peaks in time order; each anchor takes its first FANOUT valid targets.
    for k in range(1, min(n, 64 * 6)):
        a = np.arange(n - k)
        b = a + k
        dt = t[b] - t[a]
        active = dt <= DT_MAX
        if not active.any():
            break
        ok = active & (dt >= 1) & (np.abs(f[b] - f[a]) <= DF_MAX) & (count[a] < FANOUT)
        a, b = a[ok], b[ok]
        count[a] += 1
        hs.append(((f[a].astype(np.uint32) & 255) << 12) | (((f[b] - f[a]).astype(np.uint32) & 63) << 6)
                  | ((t[b] - t[a]).astype(np.uint32) & 63))
        ts.append(t[a])
    if not hs:
        return np.zeros(0, np.uint32), np.zeros(0, np.int32)
    return np.concatenate(hs), np.concatenate(ts).astype(np.int32)


class PairIndex:
    """db/pair.npz: the entries of hash h are entries[offsets[h]:offsets[h + 1]] (211 MB for 51.7 M hashes,
    where sorted hash/track/time arrays took 520 MB); per file its Panako id and library key."""

    def __init__(self, path: Path = INDEX_FILE):
        z = np.load(path)
        self.offsets, self.entries = z["offsets"], z["entries"]
        self.ids, self.keys = z["ids"], [str(k) for k in z["keys"]]

    def query(self, y: np.ndarray, top: int = 10) -> list[Match]:
        """Best offset per track for SR audio, best first, as Panako matches (time_factor 1)."""
        qh, qt = [], []
        for s in range(SHIFTS):
            h, t = hashes(peaks(y, s * HOP // SHIFTS))
            qh.append(h)
            qt.append(t)
        qh, qt = np.concatenate(qh), np.concatenate(qt)
        lo = self.offsets[qh].astype(np.int64)
        n = self.offsets[qh + 1].astype(np.int64) - lo
        keep = (n > 0) & (n <= MAX_BUCKET)
        lo, n, qt = lo[keep], n[keep], qt[keep]
        if not len(n):
            return []
        rep = np.repeat(np.arange(len(n)), n)
        pos = np.repeat(lo - np.r_[0, np.cumsum(n)[:-1]], n) + np.arange(n.sum())
        e = self.entries[pos]
        tr = (e >> T_BITS).astype(np.int64)
        tq = qt[rep]
        key = tr * (1 << 24) + ((e & ((1 << T_BITS) - 1)).astype(np.int64) - tq + (1 << 23))
        uk, cnt = np.unique(key, return_counts=True)
        tot = cnt.copy()  # +-1 frame tolerance
        for d in (-1, 1):
            j = np.minimum(np.searchsorted(uk, uk + d), len(uk) - 1)
            tot += np.where(uk[j] == uk + d, cnt[j], 0)
        trk = uk >> 24
        order = np.lexsort((-tot, trk))
        best = order[np.r_[0, np.flatnonzero(np.diff(trk[order])) + 1]]  # best offset per track
        out = []
        for b in best[np.argsort(-tot[best])][:top]:
            sel = (np.abs(key - uk[b]) <= 1) & (tr == trk[b])
            qs, qe = tq[sel].min() * HOP / SR, tq[sel].max() * HOP / SR
            o = int((uk[b] & ((1 << 24) - 1)) - (1 << 23)) * HOP / SR
            i = int(trk[b])
            out.append(Match(query_path="", query_start=round(qs, 2), query_stop=round(qe, 2),
                             match_path=self.keys[i], match_id=int(self.ids[i]),
                             match_start=round(qs + o, 2), match_stop=round(qe + o, 2), score=int(tot[b]),
                             time_factor=1.0, freq_factor=1.0, seconds_with_match=0.0))
        return out


def fingerprint_db_files() -> list[tuple[int, str]]:
    """(Panako id, library key) of every file in the fingerprint DB."""
    import lmdb

    env = lmdb.open(str(DB_DIR / "db"), readonly=True, lock=False, max_dbs=2, map_size=64 << 30)
    canon = {p.relative_to(MUSIC_DIR).as_posix().lower(): p.relative_to(MUSIC_DIR).as_posix() for p in list_audio()}
    out = []
    with env.begin(db=env.open_db(b"panako_resource_map", create=False)) as txn:
        for k, v in txn.cursor():
            # value: duration (float), print count (int), indexed path (UTF-8); key: the id, big-endian
            rel = v[8:].decode("utf-8").replace("\\", "/").split("/music/", 1)[1]
            out.append((struct.unpack(">q", k)[0], canon.get(rel.lower(), rel)))
    env.close()
    return out


def track_hashes(key: str) -> tuple[np.ndarray, np.ndarray]:
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(MUSIC_DIR / key), "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "pipe:1"], capture_output=True)
    if out.returncode != 0:
        print(f"  could not decode {key}: {out.stderr[-200:]!r}", file=sys.stderr, flush=True)
    return hashes(peaks(np.frombuffer(out.stdout, np.float32)))


def build(workers: int = 4) -> None:
    files = fingerprint_db_files()
    t0 = time.time()
    H, T, I = [], [], []
    with ProcessPoolExecutor(workers) as ex:
        for i, (h, t) in enumerate(ex.map(track_hashes, [k for _, k in files], chunksize=4)):
            H.append(h)
            T.append(t)
            I.append(np.full(len(h), i, np.uint16))
            if i % 200 == 199:
                print(f"  {i + 1}/{len(files)} ({time.time() - t0:.0f} s)", flush=True)
    H, T, I = np.concatenate(H), np.concatenate(T), np.concatenate(I)
    save(INDEX_FILE, H, T, I, [i for i, _ in files], [k for _, k in files])
    print(f"{len(files)} files, {len(H):,} hashes, {INDEX_FILE.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f} s")


def save(path: Path, H: np.ndarray, T: np.ndarray, I: np.ndarray, ids: list[int], keys: list[str]) -> None:
    """Hash, t1 and file number per entry -> the PairIndex file."""
    assert len(ids) <= 1 << (32 - T_BITS) and T.max() < 1 << T_BITS
    order = np.argsort(H, kind="stable")
    offsets = np.r_[0, np.cumsum(np.bincount(H, minlength=1 << HASH_BITS))].astype(np.uint32)
    entries = ((I.astype(np.uint32) << T_BITS) | T.astype(np.uint32))[order]
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, offsets=offsets, entries=entries, ids=np.array(ids, np.int64), keys=np.array(keys))
    tmp.replace(path)


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        build()
    else:
        print(__doc__)
