"""Write db/albums.json and db/covers/<id>.jpg: the album list the extension's library page shows
(GET /albums). Run after the library changes; server/deploy/stage.py copies both into the image.

An album is normally one top-level folder of music/. Titles and artists come from the files' tags
(album, album artist), else from the folder name ("Artist - Album"). A folder of loose tracks from
different albums (tags disagree) is split by album tag, and groups with the same title and artist
are merged across folders. The cover is the first embedded picture found (or a cover image file in
the folder), shrunk to a small JPEG. `ids` holds each track's Panako id: the fingerprint DB stores
byte-identical copies once, so a found song can name another album's copy, and the server looks
the album up by id.

Needs mutagen and Pillow (build-time only; the server just reads the output).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import mutagen
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "poc"))
from common import EXCLUDE_FILE, MUSIC_DIR, ROOT, list_audio, panako_id  # noqa: E402
from grouping import PREVIEW_RE  # noqa: E402

OUT = ROOT / "db" / "albums.json"
COVERS = ROOT / "db" / "covers"
COVER_PX = 120          # shown at 60 px; 2x for high-DPI screens
DOMINANT = 0.6          # a folder is one album when this share of its files agrees on the album tag
COVER_FILES = ("cover", "folder", "front", "album")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def first(tags, *names: str) -> str:
    for n in names:
        v = tags.get(n)
        v = getattr(v, "text", v)  # ID3 frames hold a list in .text, MP4 tags are lists
        if isinstance(v, list):
            v = v[0] if v else ""
        if v and str(v).strip():
            return str(v).strip()
    return ""


def read_tags(path: Path) -> dict:
    """Album, album artist, artist and the embedded front cover (bytes) of one file."""
    try:
        f = mutagen.File(path)
    except Exception:
        return {}
    if f is None or f.tags is None:
        return {}
    t = f.tags
    if hasattr(t, "getall"):  # ID3 (MP3, WAV)
        pics = sorted(t.getall("APIC"), key=lambda p: p.type != 3)  # front cover first
        return {"album": first(t, "TALB"), "album_artist": first(t, "TPE2"), "artist": first(t, "TPE1"),
                "cover": pics[0].data if pics else None}
    # MP4 (m4a)
    covr = t.get("covr")
    return {"album": first(t, "\xa9alb"), "album_artist": first(t, "aART"), "artist": first(t, "\xa9ART"),
            "cover": bytes(covr[0]) if covr else None}


def split_folder(name: str) -> tuple[str, str]:
    """'Artist - Album' -> (artist, album); a name without a separator is the album."""
    m = re.match(r"^(.+?)(?:\s+-\s*|\s*-\s+)(.+)$", name)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", name.strip())


def most_common(values: list[str], share: float = 0.0) -> str:
    values = [v for v in values if v]
    if not values:
        return ""
    v, n = Counter(values).most_common(1)[0]
    return v if n >= share * len(values) else ""


def thumbnail(data: bytes, px: int = COVER_PX) -> bytes | None:
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
    except Exception:
        return None
    w, h = im.size
    s = min(w, h)  # centre square crop
    im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)).resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80, optimize=True)
    return buf.getvalue()


def main() -> None:
    excluded = {l.strip() for l in EXCLUDE_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")} if EXCLUDE_FILE.exists() else set()
    by_folder: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for p in list_audio():
        key = p.relative_to(MUSIC_DIR).as_posix()
        # Preview/teaser snippets are hidden like in search results; long mixes are excluded.
        if key in excluded or PREVIEW_RE.search(p.name):
            continue
        folder = key.split("/", 1)[0] if "/" in key else ""
        by_folder[folder].append((key, read_tags(p)))

    # Step 1: groups within a folder.
    groups: list[dict] = []
    for folder, files in by_folder.items():
        f_artist, f_album = split_folder(folder) if folder else ("", "")
        # The album tag most files agree on ("?" = mostly untagged, "" = no majority).
        dominant = most_common([t.get("album") or "?" for _, t in files], DOMINANT) if folder else ""
        if dominant:
            parts = {f_album if dominant == "?" else dominant: files}
        else:
            parts = defaultdict(list)
            for k, t in files:
                parts[t.get("album") or f_album or "단일 곡"].append((k, t))
        for title, members in parts.items():
            tags = [t for _, t in members]
            artist = (most_common([t.get("album_artist", "") for t in tags])
                      or most_common([t.get("artist", "") for t in tags], 0.5)
                      or (f_artist if title in (f_album, dominant) else ""))  # "" = unknown
            groups.append({"title": title, "artist": artist, "folders": {folder}, "files": members})

    # Step 2: the same album split across folders (e.g. single tracks in a playlist folder).
    merged: dict[tuple[str, str], dict] = {}
    for g in groups:
        k = (g["title"].casefold(), g["artist"].casefold())
        if k in merged:
            merged[k]["files"] += g["files"]
            merged[k]["folders"] |= g["folders"]
        else:
            merged[k] = g

    shutil.rmtree(COVERS, ignore_errors=True)
    COVERS.mkdir(parents=True)
    albums = []
    for g in merged.values():
        files = sorted(g["files"], key=lambda kt: kt[0].lower())
        cover = None
        for _, t in files:
            if t.get("cover") and (cover := thumbnail(t["cover"])):
                break
        if cover is None:
            for folder in sorted(g["folders"]):
                if not folder:
                    continue
                imgs = sorted(p for p in (MUSIC_DIR / folder).rglob("*")
                              if p.suffix.lower() in IMAGE_EXTS and p.stem.lower().startswith(COVER_FILES))
                if imgs and (cover := thumbnail(imgs[0].read_bytes())):
                    break
        aid = hashlib.sha1(f"{g['title']}\n{g['artist']}".encode()).hexdigest()[:12]
        if cover:
            (COVERS / f"{aid}.jpg").write_bytes(cover)
        albums.append({"id": aid, "title": g["title"], "artist": g["artist"], "cover": bool(cover),
                       "tracks": [k for k, _ in files], "ids": [panako_id(MUSIC_DIR / k) for k, _ in files]})

    albums.sort(key=lambda a: (not a["artist"], a["artist"].casefold(), a["title"].casefold()))
    OUT.write_text(json.dumps(albums, ensure_ascii=False, indent=0), encoding="utf-8")
    size = sum(p.stat().st_size for p in COVERS.iterdir())
    print(f"{len(albums)} albums, {sum(len(a['tracks']) for a in albums)} tracks, "
          f"{sum(a['cover'] for a in albums)} covers ({size / 1e6:.1f} MB) -> {OUT}")


if __name__ == "__main__":
    main()
