"""latest.json for a release (decision 18.20): the version and, per package kind, file name, size and SHA-256.
The runner trusts this list only with the project's minisign signature (latest.json.minisig).

    python packaging/latest_json.py 0.11.0 out/ > latest.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# the runner takes the file names from here, so installed runners find renamed files (0.17: skipwise-runner-*)
PATTERNS = {"wheel": "dwyt_runner-{v}-py3-none-any.whl", "windows": "skipwise-runner-setup-{v}.exe",
            "appimage": "skipwise-runner-{v}-x86_64.AppImage"}


def entry(path: Path) -> dict:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"name": path.name, "size": path.stat().st_size, "sha256": digest.hexdigest()}


def build(version: str, folder: Path) -> dict:
    assets = {}
    for kind, pattern in PATTERNS.items():
        path = folder / pattern.format(v=version)
        if not path.is_file():
            raise SystemExit(f"missing: {path.name}")
        assets[kind] = entry(path)
    return {"version": version, "assets": assets}


if __name__ == "__main__":
    print(json.dumps(build(sys.argv[1], Path(sys.argv[2])), indent=2))
