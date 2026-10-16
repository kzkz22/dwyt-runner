"""The page resolver (plan 19a item 18, decision 19.10): a page yt-dlp does not know — a news site's video or
podcast page (Telex, 444, HVG, Index, Indavideo) — usually has its recording somewhere in the HTML even when a
script puts the player on the page: a YouTube id in the page's data, an embedded player, an audio file. The runner
reads the page (no JavaScript), lists those candidates, keeps the ones yt-dlp can handle, and picks one:

    a YouTube video  >  a known embedded player or podcast host  >  an audio/video file from the article's day
    >  an Apple Podcasts episode  >  any other audio/video file

(the first one on the page within a kind). Spotify is DRM-protected and left out. The page's own text goes with the
result, as background for the summary; the other candidates too, so the user can pick another one on the item page.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from .errors import WorkError

MAX_BYTES = 5 * 1024 * 1024
TEXT_CHARS = 4000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_YT = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/|live/|v/)|youtu\.be/|"
                 r"[\"']videoId[\"']\s*:\s*[\"'])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_MEDIA = re.compile(r"https?:[^\"'\s<>()]+?\.(mp3|m4a|aac|ogg|opus|mp4|m3u8)(?:\?[^\"'\s<>()]*)?", re.I)
_IFRAME = re.compile(r"<iframe[^>]+src=[\"']([^\"']+)", re.I)
_LINK = re.compile(r"https?://[^\"'\s<>\\]+")
_DATE_IN_URL = re.compile(r"/(20\d\d)/(\d\d)/(\d\d)/|[_-](20\d\d)(\d\d)(\d\d)(?:[_-]|$)")
PRIORITY = {"youtube": 0, "player": 1, "dayfile": 2, "podcast": 3, "file": 4}
LABELS = {"youtube": "YouTube-videó", "player": "Beágyazott lejátszó", "dayfile": "Hangfájl a cikk napjáról",
          "podcast": "Podcast-epizód", "file": "Hang- vagy videófájl"}


@dataclass
class Candidate:
    kind: str     # youtube | player | dayfile | podcast | file
    url: str
    label: str
    at: int       # where it first appears on the page

    @property
    def video_id(self) -> str | None:
        return self.url.rsplit("v=", 1)[1] if self.kind == "youtube" else None


@dataclass
class Page:
    url: str
    title: str | None
    site: str
    text: str
    published: str | None
    candidates: list[Candidate]

    def summary(self, chosen: Candidate) -> dict[str, Any]:
        """What goes back to the central with the transcript (protocol: transcribe result `page`)."""
        return {"url": self.url, "title": self.title, "site": self.site, "published": self.published,
                "text": self.text, "chosen": _plain(chosen),
                "candidates": [_plain(c) for c in self.candidates[:6]]}


def _plain(c: Candidate) -> dict[str, Any]:
    out = {k: v for k, v in asdict(c).items() if k != "at"}
    if c.video_id:
        out["video_id"] = c.video_id
    return out


def is_file(url: str) -> bool:
    """A direct audio/video file: yt-dlp's generic extractor downloads it as it is, there is no page to read."""
    return bool(re.search(r"\.(mp3|m4a|aac|ogg|opus|wav|flac|mp4|m4v|mkv|webm|mov|m3u8)$", urlsplit(url).path, re.I))


def known(url: str) -> bool:
    """yt-dlp has an extractor of its own for this address (not only the generic one)."""
    try:
        from yt_dlp.extractor import gen_extractor_classes
    except ImportError:
        return False
    return any(ie.IE_NAME != "generic" and ie.IE_NAME != "DRM" and ie.suitable(url)
               for ie in gen_extractor_classes())


def fetch(url: str, timeout: float = 20) -> str:
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": UA, "Accept-Language": "hu,en;q=0.8"}) as resp:
            if resp.status_code >= 400:
                raise WorkError(f"Az oldal nem érhető el (HTTP {resp.status_code}).",
                                "later" if resp.status_code >= 500 or resp.status_code == 429 else "never")
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    break
            return body.decode(resp.encoding or "utf-8", "replace")
    except httpx.HTTPError as exc:
        raise WorkError(f"Az oldal nem érhető el ({exc}); később újrapróbálom.", "later") from exc


def _meta(html: str, name: str) -> str | None:
    key = re.escape(name)
    found = (re.search(rf"<meta[^>]+(?:property|name)=[\"']{key}[\"'][^>]*content=[\"']([^\"']*)", html, re.I)
             or re.search(rf"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]*(?:property|name)=[\"']{key}[\"']", html, re.I))
    if not found:
        return None
    return htmllib.unescape(found.group(1)).strip() or None


def _text(html: str) -> str:
    """The page's words: its description and the paragraphs of the article (plain text, at most TEXT_CHARS)."""
    body = html
    article = re.search(r"<article\b.*?</article>", html, re.S | re.I)
    if article:
        body = article.group(0)
    body = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", body, flags=re.S | re.I)
    parts: list[str] = []
    description = _meta(html, "og:description") or _meta(html, "description")
    if description:
        parts.append(description)
    for p in re.findall(r"<(?:p|li|h2|h3)\b[^>]*>(.*?)</(?:p|li|h2|h3)>", body, re.S | re.I):
        line = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", p)).split())
        if len(line) >= 30 and line not in parts:
            parts.append(line)
        if sum(len(x) for x in parts) > TEXT_CHARS:
            break
    return "\n".join(parts)[:TEXT_CHARS]


def _day(page_url: str) -> str | None:
    m = _DATE_IN_URL.search(page_url)
    if not m:
        return None
    y, mo, d = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
    return f"{y}{mo}{d}"


def _clean(url: str, page_url: str) -> str:
    url = htmllib.unescape(url.replace("\\/", "/").replace("\\u0026", "&")).strip().rstrip("\\")
    url = urljoin(page_url, "https:" + url if url.startswith("//") else url)
    parts = urlsplit(url)
    if parts.hostname == "playlist.megaphone.fm":  # the playlist player of one episode -> the episode player
        episode = (parse_qs(parts.query).get("e") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9]+", episode):
            return f"https://player.megaphone.fm/{episode}"
    return url


def candidates(html: str, page_url: str, is_known=None) -> list[Candidate]:
    is_known = is_known or known
    flat = html.replace("\\/", "/").replace("\\u0026", "&")
    found: dict[str, Candidate] = {}

    def add(kind: str, url: str, at: int) -> None:
        parts = urlsplit(url)  # the same player with other options (autostart, http/https) counts once
        key = url if kind == "youtube" else f"{parts.hostname}{parts.path.rstrip('/')}"
        if key in found and found[key].at > at:
            found[key].at = at
        if key not in found:
            host = urlsplit(url).hostname or ""
            label = LABELS[kind] + ("" if kind == "youtube" else f" ({host})")
            found[key] = Candidate(kind, url, label, at)

    for m in _YT.finditer(flat):
        add("youtube", f"https://www.youtube.com/watch?v={m.group(1)}", m.start())
    players = [(m.group(1), m.start()) for m in _IFRAME.finditer(html)]
    for name in ("og:video:url", "og:video:secure_url", "og:video", "twitter:player"):
        value = _meta(html, name)
        if value and not value.lower().split("?")[0].endswith(".swf"):
            players.append((value, html.find(value[:40]) if value[:40] in html else len(html)))
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") in ("VideoObject", "AudioObject", "PodcastEpisode"):
                for key in ("embedUrl", "contentUrl"):
                    if isinstance(item.get(key), str):
                        players.append((item[key], html.find(block)))
    for m in _LINK.finditer(flat):  # podcast hosts linked from the article
        link = m.group(0)
        if "podcasts.apple.com" in link and "i=" in link or "megaphone.fm" in link:
            players.append((link, m.start()))
    day = _day(page_url)
    for raw, at in players:
        url = _clean(raw, page_url)
        host = urlsplit(url).hostname or ""
        if "spotify.com" in host or not url.startswith(("https://", "http://")):
            continue  # DRM, and nothing else to do with it
        if _YT.search(url):
            add("youtube", f"https://www.youtube.com/watch?v={_YT.search(url).group(1)}", at)
        elif _MEDIA.fullmatch(url):
            add("dayfile" if day and day in url else "file", url, at)
        elif is_known(url):
            add("podcast" if "podcasts.apple.com" in host else "player", url, at)
    for m in _MEDIA.finditer(flat):
        url = _clean(m.group(0), page_url)
        add("dayfile" if day and day in url else "file", url, m.start())
    return sorted(found.values(), key=lambda c: (PRIORITY[c.kind], c.at))


def read(page_url: str, is_known=None, html: str | None = None) -> Page:
    html = fetch(page_url) if html is None else html
    title = _meta(html, "og:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = " ".join(htmllib.unescape(m.group(1)).split()) if m else None
    return Page(url=page_url, title=title, site=_meta(html, "og:site_name") or urlsplit(page_url).hostname or "",
                text=_text(html), published=_meta(html, "article:published_time"),
                candidates=candidates(html, page_url, is_known))


def choose(page: Page, mode: str | None, pick: str | None = None) -> Candidate:
    """The recording to transcribe: the user's pick (one of the page's candidates), else the best one. YouTube
    captions need a YouTube video on the page."""
    if not page.candidates:
        raise WorkError("Az oldalon nem találtam videót vagy hangot. Ha a felvétel a YouTube-on is fent van, küldd "
                        "be annak a linkjét.")
    if pick:
        chosen = next((c for c in page.candidates if c.url == pick), None)
        if chosen is None:
            raise WorkError("A választott felvétel már nincs az oldalon.")
    else:
        chosen = page.candidates[0]
    if mode == "captions" and chosen.kind != "youtube":
        raise WorkError("Az oldalon nincs YouTube-videó, így felirat sincs hozzá; válassz helyi vagy felhős átírást.",
                        "next_step")
    return chosen
