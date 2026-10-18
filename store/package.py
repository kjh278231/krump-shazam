"""Build the Chrome Web Store upload: build/store/krump-track-finder-<version>.zip.

The zip is extension/ as it is (config.js included: it holds the deployed endpoint, written by
server/deploy/aws_setup.py function), minus the local-server host permission, which only serves
development. Upload it in the developer dashboard (store/listing.md).
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extension"
OUT = ROOT / "build" / "store"
LOCAL = "http://127.0.0.1:8765/*"


def main() -> None:
    manifest = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    config = (SRC / "config.js").read_text(encoding="utf-8") if (SRC / "config.js").exists() else ""
    if "lambda-url" not in config:
        raise SystemExit("extension/config.js must point at the deployed Lambda (run aws_setup.py function)")
    manifest["host_permissions"] = [h for h in manifest["host_permissions"] if h != LOCAL]
    OUT.mkdir(parents=True, exist_ok=True)
    zip_path = OUT / f"krump-track-finder-{manifest['version']}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(SRC.rglob("*")):
            if p.is_file() and p.name != "manifest.json" and not p.name.startswith("."):
                z.write(p, p.relative_to(SRC).as_posix())
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    print(f"{zip_path} ({zip_path.stat().st_size / 1e3:.0f} KB, {len(names)} files)")
    for n in names:
        print(" ", n)


if __name__ == "__main__":
    main()
