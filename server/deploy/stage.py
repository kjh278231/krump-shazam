"""Assemble build/lambda/, the Docker build context of the Lambda image, and build/runtime/, the
large data files the function downloads from S3 at start (aws_setup.py data uploads them).

Image (no music files):
  app/poc/*.py, app/server/app.py         code
  app/db/rhythm_cohort.npz, rhythm_cohort_wide.npz, same_audio.csv, exclude.txt
  app/db/catalog.json                     library keys (music/ is not in the image)
  app/db/albums.json, covers/             album list (server/build_albums.py, run it first)
  app/db/album_links.json, store_covers/  store links and art (server/fetch_store_covers.py)
  app/tools/Panako/build/libs/*.jar
build/runtime/ (S3 runtime/):
  data.mdb.parts/                         fingerprint DB, copied without the Windows map-size
                                          preallocation (32 GB file -> ~1.2 GB), as zstd parts
  rhythm.npz                              rhythm index
  pair.npz.parts/                         pair-hash index (poc/pairfp.py build, 0.2 GB), as zstd parts
Lambda reads rarely used image files at ~7 MB/s, so these are not in the image.

Runs only in the main checkout at origin/main (release.py, CLAUDE.md) and records that commit in
build/commit.txt, which aws_setup.py checks before uploading.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))
from common import DB_DIR, MUSIC_DIR, PANAKO_JAR, ROOT, list_audio  # noqa: E402
from release import STAGED, pushed_main  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "build" / "lambda"


PART_BYTES = 32 << 20


def split_compress(src: Path, out: Path) -> None:
    """A file (data.mdb, pair.npz) as independently zstd-compressed 32 MB parts plus manifest.json, so
    the function can download and decompress them in parallel (data.mdb shrinks by half, pair.npz by
    14%; Lambda's S3 download bandwidth, ~80 MB/s, is the cold-start bottleneck)."""
    import zstandard

    out.mkdir(parents=True)
    size = src.stat().st_size
    cctx = zstandard.ZstdCompressor(level=3, threads=-1)
    parts = []
    with src.open("rb") as fh:
        for i in range((size + PART_BYTES - 1) // PART_BYTES):
            name = f"{i:04d}.zst"
            (out / name).write_bytes(cctx.compress(fh.read(PART_BYTES)))
            parts.append(name)
    (out / "manifest.json").write_text(json.dumps({"size": size, "part_bytes": PART_BYTES, "parts": parts}))


def main() -> None:
    import lmdb

    commit = pushed_main()
    STAGED.unlink(missing_ok=True)  # set again only once staging succeeds
    app = OUT / "app"
    for sub in ("poc", "server", "db"):
        shutil.rmtree(app / sub, ignore_errors=True)
    (app / "poc").mkdir(parents=True)
    (app / "server").mkdir()
    for name in ("common.py", "grouping.py", "pairfp.py", "rhythm.py"):
        shutil.copy2(ROOT / "poc" / name, app / "poc" / name)
    shutil.copy2(ROOT / "server" / "app.py", app / "server" / "app.py")
    for name in ("Dockerfile", "requirements.txt", ".dockerignore"):
        shutil.copy2(HERE / name, OUT / name)

    db = app / "db"
    db.mkdir()
    for name in ("rhythm_cohort.npz", "rhythm_cohort_wide.npz", "same_audio.csv", "exclude.txt", "albums.json",
                 "album_links.json"):
        shutil.copy2(ROOT / "db" / name, db / name)
    shutil.copytree(ROOT / "db" / "covers", db / "covers")
    shutil.copytree(ROOT / "db" / "store_covers", db / "store_covers")
    catalog = {"indexed_root": str(MUSIC_DIR),
               "files": [p.relative_to(MUSIC_DIR).as_posix() for p in list_audio()]}
    (db / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    runtime = ROOT / "build" / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True)
    shutil.copy2(ROOT / "db" / "rhythm.npz", runtime / "rhythm.npz")
    split_compress(ROOT / "db" / "pair.npz", runtime / "pair.npz.parts")
    # Env.copy writes only the used pages (compact=True crashes on this Windows build).
    tmp = ROOT / "build" / "dbcopy"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    env = lmdb.open(str(DB_DIR / "db"), readonly=True, lock=False, max_dbs=2, map_size=64 << 30)
    env.copy(str(tmp))
    env.close()
    split_compress(tmp / "data.mdb", runtime / "data.mdb.parts")
    shutil.rmtree(tmp)

    jar = app / "tools" / "Panako" / "build" / "libs"
    jar.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PANAKO_JAR, jar / PANAKO_JAR.name)

    for d in (OUT, runtime):
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        print(f"{d}: {size / 1e9:.2f} GB")
    print(f"{len(catalog['files'])} library keys")
    STAGED.write_text(commit)
    print("staged from", commit[:7])


if __name__ == "__main__":
    main()
