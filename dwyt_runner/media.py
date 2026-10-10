"""Video metadata and audio: yt-dlp for the URL, ffmpeg for a small mono FLAC."""
from __future__ import annotations

import glob
import os
import subprocess
from typing import Any

from . import tools
from .config import Config
from .errors import WorkError, from_ytdlp


def video_meta(info: dict[str, Any]) -> dict[str, Any]:
    published = info.get("upload_date")
    if published and len(published) == 8:
        published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
    return {"title": info.get("title"), "channel": info.get("uploader") or info.get("channel"),
            "duration_s": int(info.get("duration") or 0) or None, "published": published}


def probe(url: str, cfg: Config) -> dict[str, Any]:
    """Metadata without downloading; refuses playlists, live streams and too long videos."""
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL(tools.ytdlp_options(cfg.cookies_file)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # yt-dlp raises many types
        raise from_ytdlp(exc, "beolvasni a videót") from exc
    check(info, cfg)
    return info


def check(info: dict[str, Any], cfg: Config) -> None:
    if info.get("_type") == "playlist":
        raise WorkError("Ez egy lejátszási lista; egyetlen videó URL-je kell.")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise WorkError("Élő adás vagy premier; a vége után dolgozható fel.", "later")
    duration = info.get("duration") or 0
    if duration and duration > cfg.max_duration_min * 60:
        raise WorkError(f"A videó {int(duration) // 60} perces, hosszabb a beállított "
                        f"{cfg.max_duration_min} percnél (max_duration_min).")


def download_audio(url: str, cfg: Config, outdir: str) -> str:
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL(tools.ytdlp_options(cfg.cookies_file, outdir)) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise from_ytdlp(exc, "letölteni a hangot") from exc
    files = [f for f in glob.glob(os.path.join(outdir, "audio.*")) if not f.endswith(".part")]
    if not files:
        raise WorkError("A yt-dlp nem hozott létre hangfájlt.")
    return files[0]


def to_flac(src: str, workdir: str) -> str:
    """Mono 16 kHz FLAC: lossless for speech models and a fraction of a video's size."""
    dst = os.path.join(workdir, "audio.flac")
    try:
        subprocess.run([tools.ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-i", src, "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "flac", dst],
                       check=True, capture_output=True, timeout=3600)
    except subprocess.CalledProcessError as exc:
        raise WorkError("Az ffmpeg nem tudta kinyerni a hangot: "
                        + exc.stderr.decode("utf-8", "replace")[-300:]) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkError(f"Az ffmpeg nem indult el vagy nem végzett: {exc}") from exc
    return dst
