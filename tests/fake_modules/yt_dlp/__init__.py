"""A stand-in for yt_dlp in the tests: videos, caption files and errors come from REGISTRY."""
from __future__ import annotations

import os
import shutil

REGISTRY: dict = {"videos": {}, "captions": {}, "errors": {}, "audio": None, "calls": []}


def reset() -> None:
    REGISTRY.update(videos={}, captions={}, errors={}, audio=None, calls=[])


class _Resp:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class YoutubeDL:
    def __init__(self, params: dict | None = None) -> None:
        self.params = params or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        REGISTRY["calls"].append((url, download, self.params))
        if url in REGISTRY["errors"]:
            raise Exception(REGISTRY["errors"][url])
        info = REGISTRY["videos"].get(url)
        if info is None:
            raise Exception(f"ERROR: Unsupported URL: {url}")
        if download:
            target = self.params["outtmpl"].replace("%(ext)s", "wav")
            shutil.copy(REGISTRY["audio"], target)
            assert os.path.exists(target)
        return info

    def urlopen(self, url: str) -> _Resp:
        return _Resp(REGISTRY["captions"][url])
