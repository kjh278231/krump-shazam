"""Per-video records: which songs were found in a YouTube or Instagram video (docs/video-records.md).

When the extension searches a video tab it sends the video's key. A final confirmed or unsure result
of a shared recording becomes one event object, videos/<platform>/<id>/<recording id>.json (S3, or a
local folder). GET /video sums a video's events up into the songs other users see: confirmed results
and unsure ones a user marked 맞음, minus those marked 틀림. Where in the video a song plays is not
recorded: reading the video's position meant scripting the tab, and doing that while the tab was
captured made its playback stutter.

Pure functions and the two stores only: no numpy, boto3 or app imports, so tests/test_videos.py runs
without Panako. The S3 client is passed in.
"""
from __future__ import annotations

import json
import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = 1  # event format ("v"); events of another version are skipped
KEY_ID = {"yt": re.compile(r"[A-Za-z0-9_-]{11}"), "ig": re.compile(r"[A-Za-z0-9_-]{5,64}")}
REC_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # recording ids are stamps like 20261019-101500-123-ab12

SHOW_SCORE = 2     # one confirmed event is enough to show a song
MAX_EVENTS = 1000  # one S3 LIST page


def parse_key(key) -> tuple[str, str] | None:
    """'yt:<11 chars>' or 'ig:<code>' -> (platform, id). The extension normalises URLs (video.js
    videoKey); the server only checks the form."""
    if not isinstance(key, str):
        return None
    platform, sep, vid = key.partition(":")
    rx = KEY_ID.get(platform)
    return (platform, vid) if sep and rx and rx.fullmatch(vid) else None


def event_key(key: str, rec_id: str) -> str | None:
    """S3 key of an event, or None when the video key or recording id is malformed (no path tricks)."""
    parsed = parse_key(key)
    if not parsed or not isinstance(rec_id, str) or not REC_ID.fullmatch(rec_id):
        return None
    return f"videos/{parsed[0]}/{parsed[1]}/{rec_id}.json"


def make_event(rec_id: str, key: str, verdict: str, method: str, songs: list[dict], song_id: int | None,
               build: str, today: str) -> dict | None:
    """The stored record of one final result (docs/video-records.md). Only fields that may be shown
    to other users: no title, URL, time of day or audio. None for results not worth keeping."""
    if verdict not in ("confirmed", "check") or not songs or not event_key(key, rec_id):
        return None
    head = songs[0]
    return {
        "v": VERSION, "id": rec_id, "video": key, "verdict": verdict, "method": method,
        "song": {"path": head["path"], "id": song_id, "match_start": head["match_start"],
                 "score": head["score"], "second": songs[1]["score"] if len(songs) > 1 else 0},
        "feedback": None, "build": build, "date": today,
    }


def weight(e: dict) -> int:
    """How much an event counts towards showing its song: 틀림 -3, confirmed or 맞음 +2, else 0."""
    if e.get("feedback") == "wrong":
        return -3
    return 2 if e["verdict"] == "confirmed" or e.get("feedback") == "correct" else 0


def _valid(e) -> bool:
    try:
        s = e["song"]
        return (e["v"] == VERSION and parse_key(e["video"]) is not None
                and e["verdict"] in ("confirmed", "check") and e.get("feedback") in (None, "correct", "wrong")
                and isinstance(s["path"], str) and s["path"] != "" and isinstance(s["match_start"], (int, float))
                and (s.get("id") is None or isinstance(s["id"], int)))
    except (KeyError, TypeError, AttributeError):
        return False


def _same_song(a: dict, b: dict, same) -> bool:
    if a["path"] == b["path"]:
        return True
    return isinstance(a.get("id"), int) and isinstance(b.get("id"), int) and same(a["id"], b["id"])


def summarize(events: list[dict], same) -> list[dict]:
    """GET /video's songs before they are looked up in the library: one entry per song (the same
    path, or content ids that same(id_a, id_b) links), shown when its events score SHOW_SCORE or more
    (weight). status: "confirmed" (a confirmed event counts) or "verified" (only unsure ones a user
    marked 맞음). The song's path is its most frequent among the events that count, with all their
    content ids (server/app.py finds a moved file again); match_start is their median. Best first.
    Malformed events and other format versions are skipped."""
    groups: list[list[dict]] = []
    for e in events:
        if not _valid(e):
            continue
        g = next((g for g in groups if _same_song(g[0]["song"], e["song"], same)), None)
        if g is None:
            groups.append([e])
        else:
            g.append(e)
    out = []
    for g in groups:
        score = sum(weight(e) for e in g)
        if score < SHOW_SCORE:
            continue
        good = [e for e in g if weight(e) > 0]
        paths = [e["song"]["path"] for e in good]
        path = max(dict.fromkeys(paths), key=paths.count)
        ids = list(dict.fromkeys(e["song"]["id"] for e in good if isinstance(e["song"].get("id"), int)))
        out.append({"status": "confirmed" if any(e["verdict"] == "confirmed" for e in good) else "verified",
                    "score": score,
                    "song": {"path": path, "id": ids[0] if ids else None, "ids": ids,
                             "match_start": statistics.median(e["song"]["match_start"] for e in good)}})
    out.sort(key=lambda s: -s["score"])
    return out


class DirStore:
    """Events as files under root (local server: KF_VIDEO_DIR, default recordings/videos/), at the
    S3 key without its videos/ prefix."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.Lock()

    def _path(self, key: str, rec_id: str) -> Path | None:
        k = event_key(key, rec_id)
        return self.root / k.removeprefix("videos/") if k else None

    def put(self, event: dict) -> None:
        p = self._path(event["video"], event["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def list(self, key: str) -> list[dict]:
        p = self._path(key, "x")
        if p is None or not p.parent.is_dir():
            return []
        out = []
        for f in sorted(p.parent.glob("*.json"))[:MAX_EVENTS]:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def update_feedback(self, key: str, rec_id: str, correct: bool) -> bool:
        p = self._path(key, rec_id)
        with self.lock:
            if p is None or not p.exists():
                return False
            e = json.loads(p.read_text(encoding="utf-8"))
            e["feedback"] = "correct" if correct else "wrong"
            self.put(e)
        return True


class S3Store:
    """Events in the bucket under videos/. One LIST page per video (MAX_EVENTS), objects read in
    parallel."""

    def __init__(self, client, bucket: str, threads: int = 16):
        self.s3, self.bucket, self.threads = client, bucket, threads

    def put(self, event: dict) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=event_key(event["video"], event["id"]),
                           Body=json.dumps(event, ensure_ascii=False).encode(), ContentType="application/json")

    def _get(self, key: str) -> dict | None:
        try:
            return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        except self.s3.exceptions.NoSuchKey:
            return None
        except ValueError:
            return None

    def list(self, key: str) -> list[dict]:
        k = event_key(key, "x")
        if k is None:
            return []
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=k.rsplit("/", 1)[0] + "/", MaxKeys=MAX_EVENTS)
        keys = [o["Key"] for o in resp.get("Contents", []) if o["Key"].endswith(".json")]
        if len(keys) <= 1:
            found = [self._get(k) for k in keys]
        else:
            with ThreadPoolExecutor(min(self.threads, len(keys))) as pool:
                found = list(pool.map(self._get, keys))
        return [e for e in found if e is not None]

    def update_feedback(self, key: str, rec_id: str, correct: bool) -> bool:
        k = event_key(key, rec_id)
        e = self._get(k) if k else None
        if e is None:
            return False
        e["feedback"] = "correct" if correct else "wrong"
        self.put(e)
        return True
