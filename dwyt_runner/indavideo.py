"""Indavideo's video file, asked for the way its player does (plan 19a item 18, 2026-10-17).

yt-dlp's IndavideoEmbed reads the player's video data without a JSONP `callback`, and the download tokens in that
answer (`filesh`) are refused (HTTP 403). The player asks with `callback=jQuery…` and `Referer: https://embed.
indavideo.hu/`, and those tokens work (tried with tools/inda_proba.py in dwyt-docs). So for an Indavideo player the
runner asks the same way and hands yt-dlp the direct file address (the smallest one: only the sound is needed).
The token is short-lived, so this runs right before the download.
"""
from __future__ import annotations

import json
import random
import re
import time

import httpx

from .errors import WorkError
from .i18n import gettext as _

DATA = "https://amfphp.indavideo.hu/SYm0json.php/player.playerHandler.getVideoData"
ORIGIN = "https://embed.indavideo.hu/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PLAYER = re.compile(r"^https?://(?:embed\.)?indavideo\.hu/player/video/([0-9a-f]+)", re.I)


def player_hash(url: str) -> str | None:
    m = PLAYER.match(url)
    return m.group(1).lower() if m else None


def _jsonp(text: str) -> dict:
    text = text.strip()
    if not text.startswith("{"):
        text = text[text.index("(") + 1:text.rindex(")")]
    return json.loads(text)


def direct(url: str, data_url: str | None = None, timeout: float = 20) -> str | None:
    """The direct video file of an Indavideo player address (None: not an Indavideo player)."""
    video = player_hash(url)
    if video is None:
        return None
    now = int(time.time() * 1000)
    callback = f"jQuery{random.randrange(10**17, 10**18)}_{now}"
    ask = f"{data_url or DATA}/{video}/12////?https%3A%2F%2Findavideo.hu%2F&callback={callback}&_={now + 1}"
    try:
        resp = httpx.get(ask, timeout=timeout, headers={"User-Agent": UA, "Referer": ORIGIN})
    except httpx.HTTPError as exc:
        raise WorkError(_("Az Indavideo nem érhető el ({exc}); később újrapróbálom.").format(exc=exc), "later") from exc
    if resp.status_code >= 400:
        raise WorkError(_("Az Indavideo nem adta meg a videó adatait (HTTP {status_code}).").format(
            status_code=resp.status_code),
                        "later" if resp.status_code >= 500 or resp.status_code == 429 else "never")
    try:
        data = _jsonp(resp.text)["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise WorkError(_("Az Indavideo válasza nem olvasható; lehet, hogy a lejátszója megváltozott.")) from exc
    files = data.get("video_files") or []
    if isinstance(files, dict):
        files = list(files.values())
    tokens = data.get("filesh") or {}
    found = []
    for raw in files:
        m = re.search(r"\.(\d{3,4})\.mp4", str(raw))
        height = m.group(1) if m else (next(iter(tokens)) if len(tokens) == 1 else None)
        if height and tokens.get(height):
            found.append((int(height), f"{raw}&token={tokens[height]}"))
    if not found:
        raise WorkError(_("Az Indavideo nem adott letölthető fájlt ehhez a videóhoz (lehet, hogy privát vagy törölt)."))
    return min(found)[1]
