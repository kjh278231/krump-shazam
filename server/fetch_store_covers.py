"""Write db/store_covers/<id>.jpg: the store's album art (db/album_links.json) shrunk to a small
JPEG, shown next to the purchase link when a song is found. Albums without store art keep the
tag cover of db/covers/. Run after db/album_links.json or db/albums.json changes;
server/deploy/stage.py copies the folder into the image.

Only the image CDNs are fetched (Bandcamp's f4.bcbits.com, knkrtworld's images.zoogletools.com,
Gumroad's public-files.gumroad.com), one request per album.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_albums import ROOT, thumbnail  # noqa: E402

LINKS = ROOT / "db" / "album_links.json"
OUT = ROOT / "db" / "store_covers"
COVER_PX = 144  # shown at 72 px in the popup; 2x for high-DPI screens


def main() -> None:
    albums = json.loads(LINKS.read_text(encoding="utf-8"))["albums"]
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    done, failed = 0, []
    for aid, a in sorted(albums.items()):
        url = a.get("cover")
        if not url:
            continue
        if "bcbits.com" in url:
            url = url.rsplit("_", 1)[0] + "_16.jpg"  # 700 px instead of 1200 px
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "krump-track-finder"})
            with urllib.request.urlopen(req, timeout=30) as r:
                small = thumbnail(r.read(), COVER_PX)
        except Exception as e:  # noqa: BLE001
            small, url = None, f"{url} ({e})"
        if small:
            (OUT / f"{aid}.jpg").write_bytes(small)
            done += 1
        else:
            failed.append(f"{aid} {url}")
        time.sleep(0.2)
    size = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"{done} store covers ({size / 1e6:.1f} MB) -> {OUT}")
    for f in failed:
        print("failed:", f)


if __name__ == "__main__":
    main()
