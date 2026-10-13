"""The external tools: ffmpeg (from imageio-ffmpeg) and deno (from the deno package, for
yt-dlp's YouTube JavaScript challenges). Both come as Python wheels, so nothing needs
installing by hand on Linux, Windows or macOS."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from importlib import metadata


def no_window() -> dict:
    """subprocess kwargs: no console window flashing up from the console-less app on Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


@functools.lru_cache(maxsize=1)
def ffmpeg() -> str:
    found = os.environ.get("DWYT_FFMPEG") or shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@functools.lru_cache(maxsize=1)
def deno() -> str | None:
    try:
        from deno import find_deno_bin
        return find_deno_bin()
    except Exception:  # noqa: BLE001 - not installed for this platform
        return shutil.which("deno")


def versions() -> dict[str, str]:
    out = {}
    for name in ("yt-dlp", "imageio-ffmpeg", "deno"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return out


def ytdlp_options(cookies_file: str = "", outdir: str | None = None) -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True}
    path = deno()
    if path:
        opts["js_runtimes"] = {"deno": {"path": path}}
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    if outdir:
        opts["format"] = "bestaudio/best"
        opts["outtmpl"] = os.path.join(outdir, "audio.%(ext)s")
    return opts
