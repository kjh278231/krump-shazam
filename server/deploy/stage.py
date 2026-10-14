"""Assemble build/lambda/, the Docker build context of the Lambda image, and build/runtime/, the
large data files the function downloads from S3 at start (aws_setup.py data uploads them).

Image (no music files):
  app/poc/*.py, app/server/app.py         code
  app/db/rhythm_cohort.npz, same_audio.csv, exclude.txt
  app/db/catalog.json                     library keys (music/ is not in the image)
  app/db/albums.json, covers/             album list (server/build_albums.py, run it first)
  app/tools/Panako/build/libs/*.jar
build/runtime/ (S3 runtime/):
  data.mdb.parts/                         fingerprint DB, copied without the Windows map-size
                                          preallocation (32 GB file -> ~1.2 GB), as zstd parts
  rhythm.npz                              rhythm index
Lambda reads rarely used image files at ~7 MB/s, so these two are not in the image.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))
from common import DB_DIR, MUSIC_DIR, PANAKO_JAR, ROOT, list_audio  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "build" / "lambda"


PART_BYTES = 32 << 20


def split_compress(src: Path, out: Path) -> None:
    """data.mdb as independently zstd-compressed 32 MB parts plus manifest.json, so the function
    can download and decompress them in parallel (about half the bytes; Lambda's S3 download
    bandwidth, ~80 MB/s, is the cold-start bottleneck)."""
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

    app = OUT / "app"
    for sub in ("poc", "server", "db"):
        shutil.rmtree(app / sub, ignore_errors=True)
    (app / "poc").mkdir(parents=True)
    (app / "server").mkdir()
    for name in ("common.py", "grouping.py", "rhythm.py"):
        shutil.copy2(ROOT / "poc" / name, app / "poc" / name)
    shutil.copy2(ROOT / "server" / "app.py", app / "server" / "app.py")
    for name in ("Dockerfile", "requirements.txt", ".dockerignore"):
        shutil.copy2(HERE / name, OUT / name)

    db = app / "db"
    db.mkdir()
    for name in ("rhythm_cohort.npz", "same_audio.csv", "exclude.txt", "albums.json"):
        shutil.copy2(ROOT / "db" / name, db / name)
    shutil.copytree(ROOT / "db" / "covers", db / "covers")
    catalog = {"indexed_root": str(MUSIC_DIR),
               "files": [p.relative_to(MUSIC_DIR).as_posix() for p in list_audio()]}
    (db / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    runtime = ROOT / "build" / "runtime"
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True)
    shutil.copy2(ROOT / "db" / "rhythm.npz", runtime / "rhythm.npz")
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


if __name__ == "__main__":
    main()
