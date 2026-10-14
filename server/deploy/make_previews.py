"""Convert the library to the low-bitrate preview copies the Lambda server serves 10 s excerpts from.

Writes build/previews/<library key>.mp3: 96 kbps constant bitrate, no ID3/Xing header, so second t
starts at byte t * 12000 (server/app.py PREVIEW_KBPS). Excluded files (db/exclude.txt) are skipped:
they are never reported, so never previewed. Existing outputs are kept, so the script can resume.

Upload afterwards:  aws s3 sync build/previews s3://<bucket>/previews/
"""
from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))
from common import EXCLUDE_FILE, FFMPEG, MUSIC_DIR, ROOT, list_audio  # noqa: E402

OUT = ROOT / "build" / "previews"
KBPS = 96


def convert(src: Path) -> str | None:
    key = src.relative_to(MUSIC_DIR).as_posix()
    dst = OUT / f"{key}.mp3"
    if dst.exists() and dst.stat().st_size > 0:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
                        "-c:a", "libmp3lame", "-b:a", f"{KBPS}k", "-ar", "44100",
                        "-write_xing", "0", "-id3v2_version", "0", "-f", "mp3", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        return f"FAIL {key}: {r.stderr.strip()[-200:]}"
    tmp.replace(dst)
    return None


def main() -> None:
    excluded = {l.strip() for l in EXCLUDE_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")}
    files = [p for p in list_audio() if p.relative_to(MUSIC_DIR).as_posix() not in excluded]
    print(f"{len(files)} files -> {OUT}", flush=True)
    failures = 0
    with ThreadPoolExecutor(8) as pool:
        for i, err in enumerate(pool.map(convert, files), 1):
            if err:
                failures += 1
                print(err, flush=True)
            if i % 250 == 0:
                print(f"{i}/{len(files)}", flush=True)
    print(f"done, {failures} failures", flush=True)


if __name__ == "__main__":
    main()
