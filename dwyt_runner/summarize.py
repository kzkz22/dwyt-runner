"""Summaries in Hungarian from the original-language transcript (ported from yt-transcribe-webui).

The whole transcript goes in one request when it fits; longer ones are first condensed part
by part into timestamped notes, and the summary is written from those.
"""
from __future__ import annotations

from typing import Any, Callable

from . import formatting
from .errors import WorkError
from .llm import Llm, Usage, chars_for

PROMPT_VERSION = 1
MAX_CHARS = 120000
LANGUAGE_NAMES = {"hu": "magyar"}

SYSTEM = (
    "Videók és hangfelvételek átiratából készítesz összefoglalót. Mindig {language} nyelven írsz, "
    "akkor is, ha az átirat más nyelvű. Csak arra támaszkodj, ami az átiratban elhangzik; ne találj ki "
    "semmit, és ne egészítsd ki saját tudással. A neveket, mozaikszavakat és szakkifejezéseket eredeti "
    "alakjukban hagyd; ha nem {language} nyelvűek, az első előfordulásuknál írd mögéjük zárójelben a "
    "jelentésüket {language} nyelven."
)

FORMAT = """Pontosan ebben a szerkezetben válaszolj, Markdownban, más bevezetés vagy zárás nélkül:

## Összefoglaló
5–10 mondat, amely az egész felvételt lefedi, az elejétől a végéig.

## Fő pontok
- [időbélyeg] egy fő gondolat egy mondatban
(5–12 pont, időrendben. Az időbélyeget pontosan úgy másold át, ahogy az átirat sorának elején áll, például [12:34] vagy [01:02:03].)
{speakers_block}"""

SPEAKERS_BLOCK = """
## Beszélők
- Címke: egy mondat arról, mit képviselt vagy mivel járult hozzá a beszélgetéshez
(Minden beszélő-címkéhez egy sor, a címkét pontosan úgy írd, ahogy az átiratban áll, például „Szereplő 1”. Valódi nevet vagy szerepet csak zárójelben adj hozzá, és csak akkor, ha az átiratból egyértelmű, például mert megszólítják vagy bemutatkozik.)"""

NOTES_PROMPT = """Ez egy hosszú átirat {part}. része ({parts} részből). Írj róla tömör, időrendi jegyzetet {language} nyelven:
pontokba szedve a fontos állításokat, mindegyik elején az átirat időbélyegével ([12:34] alakban, ahogy az átiratban áll),
és ha vannak beszélő-címkék, azzal, hogy ki mondta. Bevezetés és összegzés nélkül.

Átirat:
{text}"""

DESCRIBE = {"captions": "YouTube-felirat", "cloud": "felhős átírás", "local": "helyi Whisper"}


def transcript_lines(segments: list[dict]) -> list[str]:
    out = []
    for turn in formatting.turns(segments, max_chars=400):
        who = f" {turn['speaker']}:" if turn["speaker"] else ""
        out.append(f"[{formatting.hms(turn['start'])}]{who} {turn['text']}")
    return out


def _header(meta: dict, t: dict) -> str:
    lines = [f"Cím: {meta.get('title') or meta.get('url') or 'ismeretlen'}"]
    if meta.get("channel"):
        lines.append(f"Csatorna: {meta['channel']}")
    if meta.get("duration_s"):
        lines.append(f"Hossz: {formatting.hms(meta['duration_s'])}")
    if meta.get("page_text"):  # the page the recording was found on (19.10): background, not part of the recording
        lines.append("Az oldal szövege, ahol a felvétel megjelent (csak háttér, nem a felvétel része):\n"
                     + str(meta["page_text"])[:3000])
    lines.append(f"Az átirat nyelve: {t.get('language') or 'ismeretlen'}")
    lines.append(f"Az átirat forrása: {DESCRIBE.get(t.get('mode'), t.get('mode'))}")
    if t.get("mode") == "captions" and t.get("caption_kind") == "auto":
        lines.append("Megjegyzés: automatikus feliratból készült, felismerési hibák lehetnek benne; "
                     "a nyilvánvaló elhallásokat értelem szerint kezeld.")
    if t.get("diarized") and t.get("speakers"):
        lines.append("Beszélők: " + ", ".join(str(s) for s in t["speakers"]))
    return "\n".join(lines)


def _chunks(lines: list[str], max_chars: int) -> list[str]:
    parts, cur, size = [], [], 0
    for line in lines:
        if cur and size + len(line) + 1 > max_chars:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        parts.append("\n".join(cur))
    return parts


def run(job_input: dict[str, Any], llm: Llm, model: str, stage: Callable[[str], None] = lambda s: None,
        max_chars: int = MAX_CHARS, progress: Callable[[dict[str, Any]], None] = lambda p: None,
        context_tokens: int | None = None) -> dict[str, Any]:
    t = job_input["transcript"]
    meta = job_input.get("meta") or {}
    target = job_input.get("language") or "hu"
    lang = LANGUAGE_NAMES.get(target, target)
    lines = transcript_lines(t["segments"])
    if not lines:
        raise WorkError("Az átirat üres, nincs mit összefoglalni.")
    system = SYSTEM.format(language=lang)
    max_chars = chars_for(context_tokens, max_chars)  # a local model's context decides the part size
    body = "\n".join(lines)
    chunked = len(body) > max_chars
    usage = Usage()
    stage("summarize")
    if chunked:
        parts = _chunks(lines, max_chars)
        notes = []
        for i, part in enumerate(parts, 1):  # the last step (part parts+1) writes the summary itself
            progress({"part": i, "parts": len(parts) + 1, "model": model})
            notes.append(usage.add(llm.chat(model, system, NOTES_PROMPT.format(part=i, parts=len(parts),
                                                                               language=lang, text=part))).text)
        progress({"part": len(parts) + 1, "parts": len(parts) + 1, "model": model})
        while len("\n\n".join(notes)) > max_chars and len(notes) > 1:  # the notes themselves too long: merge
            merged = _chunks(notes, max_chars)
            notes = [usage.add(llm.chat(model, system, NOTES_PROMPT.format(part=i, parts=len(merged), language=lang,
                                                                           text=part))).text
                     for i, part in enumerate(merged, 1)]
        material = ("Az átirat túl hosszú volt, ezért részenként jegyzet készült róla. A jegyzetek:\n\n"
                    + "\n\n".join(notes))
    else:
        progress({"model": model})
        material = "Átirat:\n" + body
    speakers = SPEAKERS_BLOCK if (t.get("diarized") and t.get("speakers")) else ""
    user = f"{_header(meta, t)}\n\n{FORMAT.format(speakers_block=speakers)}\n\n{material}"
    answer = usage.add(llm.chat(model, system, user))
    return {"text": answer.text, "model": model, "language": target, "prompt_version": PROMPT_VERSION,
            "chunked": chunked, "input_chars": len(body), **usage.facts()}
