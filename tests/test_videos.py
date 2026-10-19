"""server/videos.py: video keys, sharing rules, stores.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import videos  # noqa: E402

KEY = "yt:dQw4w9WgXcQ"
SONG_A = {"path": "A - Album/A - Album - 01 One.mp3", "id": 1}
SONG_B = {"path": "B - Album/B - Album - 02 Two.mp3", "id": 2}
SONG_A_COPY = {"path": "Bundle/One (copy).mp3", "id": 11}  # same audio as SONG_A, other file
SAME = {frozenset((1, 11))}


def same(a: int, b: int) -> bool:
    return a == b or frozenset((a, b)) in SAME


def event(song=SONG_A, verdict="confirmed", feedback=None, rec_id="20261019-101500-123-ab12", match_start=43.0,
          key=KEY):
    return {"v": 1, "id": rec_id, "video": key, "verdict": verdict, "method": "pair",
            "song": {**song, "match_start": match_start, "score": 30, "second": 5},
            "feedback": feedback, "build": "test", "date": "2026-10-19"}


def shown(*evs):
    return videos.summarize(list(evs), same)


class Keys(unittest.TestCase):
    def test_parse_key(self):
        self.assertEqual(videos.parse_key("yt:dQw4w9WgXcQ"), ("yt", "dQw4w9WgXcQ"))
        self.assertEqual(videos.parse_key("ig:C1a2B3c4D5e"), ("ig", "C1a2B3c4D5e"))
        self.assertEqual(videos.parse_key("ig:DA-b_9"), ("ig", "DA-b_9"))
        for bad in ("yt:short", "yt:dQw4w9WgXcQx", "tt:dQw4w9WgXcQ", "yt:../../etc/x", "yt:dQw4w9WgX/Q",
                    "ig:abc", "ig:" + "a" * 65, "dQw4w9WgXcQ", "", None, 3, "yt:"):
            self.assertIsNone(videos.parse_key(bad), bad)

    def test_event_key(self):
        self.assertEqual(videos.event_key(KEY, "20261019-101500-123-ab12"),
                         "videos/yt/dQw4w9WgXcQ/20261019-101500-123-ab12.json")
        for bad in ("../x", "a/b", "..", "", "x" * 65, None):
            self.assertIsNone(videos.event_key(KEY, bad), bad)
        self.assertIsNone(videos.event_key("yt:bad", "20261019"))


class Events(unittest.TestCase):
    SONGS = [{"path": SONG_A["path"], "score": 30, "match_start": 43.0}, {"path": SONG_B["path"], "score": 5}]

    def test_make_event(self):
        e = videos.make_event("20261019-101500-123-ab12", KEY, "confirmed", "pair", self.SONGS, 1, "abc1234", "2026-10-19")
        self.assertEqual(e["song"], {"path": SONG_A["path"], "id": 1, "match_start": 43.0, "score": 30, "second": 5})
        self.assertEqual((e["video"], e["verdict"], e["feedback"], e["build"], e["date"]),
                         (KEY, "confirmed", None, "abc1234", "2026-10-19"))
        self.assertTrue(videos._valid(e))
        self.assertNotIn("title", json.dumps(e))
        self.assertIsNone(videos.make_event("x", KEY, "fail", "fingerprint", [], None, "", ""))
        self.assertIsNone(videos.make_event("../x", KEY, "confirmed", "pair", self.SONGS, 1, "", ""))
        self.assertIsNone(videos.make_event("x", "yt:bad", "confirmed", "pair", self.SONGS, 1, "", ""))


class Summarize(unittest.TestCase):
    def test_share_rules(self):
        def status(*evs):
            s = shown(*evs)
            return s[0]["status"] if s else None
        self.assertEqual(status(event()), "confirmed")
        self.assertIsNone(status(event(verdict="check")))
        self.assertEqual(status(event(verdict="check", feedback="correct")), "verified")
        self.assertIsNone(status(event(verdict="check", feedback="wrong")))
        self.assertIsNone(status(event(), event(feedback="wrong")))
        self.assertEqual(status(event(), event(), event(), event(feedback="wrong")), "confirmed")
        self.assertEqual(status(event(verdict="check", feedback="correct"), event()), "confirmed")

    def test_same_song(self):
        s = shown(event(song=SONG_A, match_start=40), event(song=SONG_A_COPY, match_start=50), event(song=SONG_A, match_start=42))
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["song"]["path"], s[0]["song"]["ids"], s[0]["song"]["match_start"], s[0]["score"]),
                         (SONG_A["path"], [1, 11], 42, 6))

    def test_order_and_hidden(self):
        s = shown(event(song=SONG_B), event(song=SONG_A), event(song=SONG_A), event(song=SONG_B, verdict="check"))
        self.assertEqual([x["song"]["path"] for x in s], [SONG_A["path"], SONG_B["path"]])
        s = shown(event(song=SONG_B, verdict="check"), event(song=SONG_A))
        self.assertEqual([x["song"]["path"] for x in s], [SONG_A["path"]])  # an unsure find nobody confirmed

    def test_wrong_does_not_pick_the_path(self):
        s = shown(event(song=SONG_A_COPY, feedback="wrong"), event(song=SONG_A), event(song=SONG_A), event(song=SONG_A))
        self.assertEqual((s[0]["song"]["path"], s[0]["song"]["ids"]), (SONG_A["path"], [1]))

    def test_bad_events(self):
        good = event()
        bad = [{**good, "v": 2}, {k: v for k, v in good.items() if k != "song"}, {**good, "verdict": "fail"},
               {**good, "song": {**good["song"], "match_start": None}}, {**good, "video": "yt:bad"},
               {**good, "feedback": "maybe"}, {**good, "song": {**good["song"], "id": "1"}}, "not a dict", None]
        self.assertEqual(shown(*bad), [])
        self.assertEqual(len(shown(*bad, good)), 1)


class FakeS3:
    """list_objects_v2 / get_object / put_object over a dict."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k} for k in keys]} if keys else {}


class Stores(unittest.TestCase):
    def check_store(self, store):
        store.put(event(rec_id="r1"))
        store.put(event(rec_id="r2", song=SONG_B))
        store.put(event(rec_id="r3", key="yt:otherVideo1"))
        self.assertEqual(sorted(e["id"] for e in store.list(KEY)), ["r1", "r2"])
        self.assertEqual(store.list("yt:noRecords00"), [])
        self.assertEqual(store.list("yt:../../../x"), [])
        self.assertTrue(store.update_feedback(KEY, "r1", False))
        self.assertEqual({e["id"]: e["feedback"] for e in store.list(KEY)}, {"r1": "wrong", "r2": None})
        self.assertFalse(store.update_feedback(KEY, "missing", True))
        self.assertFalse(store.update_feedback(KEY, "../r1", True))
        self.assertFalse(store.update_feedback("yt:bad", "r1", True))

    def test_dir_store(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "videos"
            self.check_store(videos.DirStore(root))
            self.assertTrue((root / "yt" / "dQw4w9WgXcQ" / "r1.json").exists())
            self.assertEqual([p.name for p in Path(d).iterdir()], ["videos"])  # nothing outside root

    def test_s3_store(self):
        s3 = FakeS3()
        self.check_store(videos.S3Store(s3, "bucket"))
        self.assertIn("videos/yt/dQw4w9WgXcQ/r1.json", s3.objects)
        self.assertTrue(all(k.startswith("videos/") for k in s3.objects))


if __name__ == "__main__":
    unittest.main()
