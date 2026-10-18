"""What the pair-hash stage (poc/pairfp.py) changes on the battle-100 windows.

<dir>/windows.jsonl holds the deployed server's answer per 30 s window (poc/battle_test.py: Panako,
then the rhythm search). Each window is queried again with the pair stage alone, as server/app.py runs
it, on the first 10/15/20 s (the lengths of mid-recording checks) and on all 30 s. The report shows:
  - the 30 s answers before and after: a pair confirmation replaces whatever came after Panako
  - whether a pair answer names the song the server found, and for windows the server left empty,
    whether a neighbouring window (+-15 s) names the same song (likely right) or not
  - rhythm answers the pair stage confirms below its bar, by naming the same song (pairfp.AGREE)
  - when the recording would stop: the deployed rhythm rule (realtest/rhythm_early.jsonl,
    server/app.py RHYTHM_EARLY) against the first length at which the pair stage confirms, alone or
    by agreeing with the rhythm candidate of that length

Usage: python poc/eval_pair.py [realtest/battle100] [report]   -> <dir>/pair_eval.jsonl (resumable), pair_report.txt
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

from common import FFMPEG, MUSIC_DIR, ROOT, panako_id
from grouping import RIVAL_RATIO, group, load_links, verdict
from pairfp import AGREE, SR, PairIndex, confirm_score
from rhythm import STRONG_RATIO, WEAK_RATIO, WEAK_Z

LENGTHS = (10, 15, 20, 30)
SR_IN = 16000  # the server's decoding rate (PANAKO_SR)
WIN, HOP = 30, 15  # poc/battle_test.py
RHYTHM_EARLY = ((10, 1.5), (15, 1.3), (20, 1.2))  # server/app.py, first match wins
LINKS = load_links()
EXCLUDED = {l.strip() for l in (ROOT / "db" / "exclude.txt").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")}


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """server/app.py resample (FFT truncation)."""
    n_out = int(round(len(x) * sr_out / sr_in))
    X = np.fft.rfft(x)
    m = n_out // 2 + 1
    X = X[:m] if m <= len(X) else np.concatenate([X, np.zeros(m - len(X), X.dtype)])
    return (np.fft.irfft(X, n_out) * (n_out / len(x))).astype(np.float32)


@lru_cache(maxsize=None)
def _id(key: str) -> int:
    return panako_id(MUSIC_DIR / key)


def same(a: str, b: str) -> bool:
    return a == b or _id(a) == _id(b) or frozenset((_id(a), _id(b))) in LINKS


def pair_stage(index: PairIndex, x: np.ndarray) -> dict:
    t = time.time()
    songs = group([m for m in index.query(resample(x, SR_IN, SR)) if m.match_path not in EXCLUDED], LINKS)
    need = confirm_score(len(x) / SR_IN)
    v = verdict(songs, confirm=need, check=need)[0] if songs else "fail"
    top = songs[0].head if songs else None
    return {"key": top.match_path if top else "", "score": songs[0].score if songs else 0,
            "second": songs[1].score if len(songs) > 1 else 0, "confirmed": v == "confirmed",
            "seconds": round(time.time() - t, 3)}


def measure(d: Path, index: PairIndex) -> None:
    out = d / "pair_eval.jsonl"
    done = {json.loads(l)["id"] for l in out.open(encoding="utf-8")} if out.exists() else set()
    files = {p.stem: p for p in (d / "audio").glob("*")}
    for line in (d / "windows.jsonl").open(encoding="utf-8"):
        rec = json.loads(line)
        if rec["id"] in done:
            continue
        pcm = np.frombuffer(subprocess.run([FFMPEG, "-v", "error", "-i", str(files[rec["id"]]), "-ac", "1", "-ar",
                                            str(SR_IN), "-f", "s16le", "pipe:1"], capture_output=True, check=True).stdout,
                            dtype="<i2").astype(np.float32) / 32768

        def one(w):
            x = pcm[int(w["t"] * SR_IN):int((w["t"] + WIN) * SR_IN)]
            return {"t": w["t"], "by": {str(L): pair_stage(index, x[:L * SR_IN]) for L in LENGTHS if len(x) >= min(L, 15) * SR_IN}}

        with ThreadPoolExecutor(4) as ex:
            rows = list(ex.map(one, rec["windows"]))
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": rec["id"], "windows": rows}, ensure_ascii=False) + "\n")
        print(rec["id"], len(rows), "windows", sum(r["by"].get("30", {}).get("confirmed", False) for r in rows), "confirmed", flush=True)


def confirmed(b: dict | None, length: str) -> bool:
    """The server's rule on a stored result, so the thresholds can change without querying again (the
    stored length is exact where it matters: tail windows are >= 15 s and the bar is flat from 15 s)."""
    return bool(b) and b["score"] >= confirm_score(int(length)) and b["second"] < RIVAL_RATIO * b["score"]


def agrees(b: dict | None, rhythm_key: str) -> bool:
    """server/app.py's agreement rule on a stored result: the rhythm candidate is the pair stage's top
    song at AGREE or more, no rival at 0.6 of it (stored: the top song's head file only)."""
    return bool(b) and b["score"] >= AGREE and b["second"] < RIVAL_RATIO * b["score"] and same(b["key"], rhythm_key)


def early_candidate(b: dict | None) -> str | None:
    """Key of the rhythm candidate a partial check gets from a stored raw winner (rhythm.identify's
    strong/weak rule), or None."""
    if b and (b["agree"] and b["ratio"] >= STRONG_RATIO or b["ratio"] >= WEAK_RATIO or b["agree"] and b["z"] >= WEAK_Z):
        return b["path"].replace("\\", "/").split("/music/", 1)[1]  # stored under the main checkout's music/
    return None


def category(w: dict) -> str:
    if w.get("verdict") == "confirmed":
        return "fingerprint 확정"
    if not w.get("songs"):
        return "답 없음" if w.get("verdict") != "error" else "오류"
    if w["method"] == "rhythm":
        return f"rhythm {w['songs'][0].get('strength', '')}"
    return "fingerprint 추가 확인"


def report(d: Path) -> str:
    prod = {json.loads(l)["id"]: json.loads(l) for l in (d / "windows.jsonl").open(encoding="utf-8")}
    pair = {r["id"]: {w["t"]: w["by"] for w in r["windows"]} for r in map(json.loads, (d / "pair_eval.jsonl").open(encoding="utf-8"))}
    early = {(r["src"], r["t"]): r["by"] for r in map(json.loads, (ROOT / "realtest" / "rhythm_early.jsonl").open(encoding="utf-8"))
             if r["kind"] in ("pos", "neg")}
    table = collections.defaultdict(collections.Counter)
    stops = collections.Counter()
    seconds = []
    after = collections.Counter()
    new = []  # pair answers the deployed server did not give: (video, t, pair result, note)
    for vid, rec in prod.items():
        if vid not in pair:
            continue
        by_t = pair[vid]
        # Songs named by any answer per window (deployed or pair at 30 s), for the neighbour check.
        named = {w["t"]: [s["path"] for s in w.get("songs", [])[:1]] for w in rec["windows"]}
        for t, by in by_t.items():
            if confirmed(by.get("30"), "30"):
                named.setdefault(t, []).append(by["30"]["key"])
        for w in rec["windows"]:
            by = by_t.get(w["t"], {})
            seconds += [b["seconds"] for b in by.values()]
            cat = category(w)
            p_len = "30" if "30" in by else max(by, key=int, default="30")
            p = by.get(p_len)
            p_ok = confirmed(p, p_len)
            if cat == "fingerprint 확정":  # Panako confirmed first; the pair stage never runs
                col = "(Panako가 먼저 확정)"
                after[cat] += 1
            elif p_ok:
                if w.get("songs") and same(p["key"], w["songs"][0]["path"]):
                    col = "같은 곡 확정"
                elif w.get("songs"):
                    col = "다른 곡 확정"
                    new.append((vid, w["t"], p, w["songs"][0]["path"]))
                else:
                    # Windows 15 s apart share half their audio, so only those 30 s away are evidence.
                    near = any(same(p["key"], k) for t2 in (w["t"] - 2 * HOP, w["t"] + 2 * HOP) for k in named.get(t2, []))
                    col = "새 답 (30초 옆 창과 일치)" if near else "새 답 (근거 없음)"
                    new.append((vid, w["t"], p, "consistent" if near else ""))
                after["2점 해시 확정"] += 1
            elif cat.startswith("rhythm") and agrees(p, w["songs"][0]["path"]):
                col = "리듬과 일치 확정"
                after["리듬+2점 해시 확정"] += 1
            else:
                col = "확정 안 함"
                after[cat] += 1
            table[cat][col] += 1
            # When would the recording stop? Only windows with an answer at 30 s either way.
            before = next((sec for sec, ratio in RHYTHM_EARLY if (b := early.get((vid, w["t"]), {}).get(str(sec)))
                           and b["agree"] and b["ratio"] >= ratio and category(w).startswith("rhythm")), None)
            if cat == "fingerprint 확정":
                before = 10  # Panako confirmations mostly come at the first check
            e = early.get((vid, w["t"]), {})
            pair_at = next((L for L in (10, 15, 20) if confirmed(by.get(str(L)), str(L))
                            or (k := early_candidate(e.get(str(L)))) and agrees(by.get(str(L)), k)), None)
            if cat != "fingerprint 확정" and (cat != "답 없음" or pair_at or p_ok):
                now = min(x for x in (before, pair_at, 30) if x)
                stops[("before", before or (30 if cat != "답 없음" else None))] += 1
                stops[("after", now)] += 1
                if pair_at and pair_at == now:
                    stops[("after 확정", now)] += 1
    cols = ["(Panako가 먼저 확정)", "같은 곡 확정", "다른 곡 확정", "새 답 (30초 옆 창과 일치)", "새 답 (근거 없음)", "리듬과 일치 확정",
            "확정 안 함"]
    lines = [f"windows: {sum(sum(c.values()) for c in table.values())} in {len(pair)} videos",
             f"pair stage per query: mean {np.mean(seconds):.3f} s, 95th percentile {np.percentile(seconds, 95):.3f} s, max {max(seconds):.3f} s",
             "", "## 30 s answers: deployed server (rows) and the pair stage (columns)",
             f"{'':<22}" + "".join(f"{c:>22}" for c in cols)]
    for cat in ("fingerprint 확정", "fingerprint 추가 확인", "rhythm strong", "rhythm weak", "답 없음", "오류"):
        if cat in table:
            lines.append(f"{cat:<22}" + "".join(f"{table[cat][c]:>22}" for c in cols))
    lines += ["", "## 30 s answers after adding the pair stage"]
    lines += [f"   {k:<22}{v:5d}" for k, v in after.most_common()]
    lines += ["", "## when the recording stops (windows answered at 30 s by either)",
              "   before: " + ", ".join(f"{s} s: {stops[('before', s)]}" for s in (10, 15, 20, 30)) + f", no answer: {stops[('before', None)]}",
              "   after:  " + ", ".join(f"{s} s: {stops[('after', s)]}" for s in (10, 15, 20, 30)),
              "   after, stops before 30 s that are 확정 (pair alone or agreeing with rhythm): "
              + ", ".join(f"{s} s: {stops[('after 확정', s)]}" for s in (10, 15, 20))]
    lines += ["", "## pair answers that differ from or add to the deployed server (video, t, score/second, song | note)"]
    for vid, t, p, note in sorted(new, key=lambda x: (x[0], x[1])):
        lines.append(f"   {vid} {t:5d}  {p['score']:4d}/{p['second']:<4d} {p['key']}  |  {note}")
    text = "\n".join(lines)
    (d / "pair_report.txt").write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "report"]
    d = ROOT / (args[0] if args else "realtest/battle100")
    if "report" not in sys.argv:  # `report`: recompute from stored results only
        measure(d, PairIndex())
    print(report(d))
