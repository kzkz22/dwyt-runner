"""Pure helpers: words -> sentence-sized segments, speaker relabelling, paragraphs, times.
(Ported from the old yt-transcribe API.)"""
from __future__ import annotations

from . import langs

UNKNOWN_WORD = {"hu": "Ismeretlen", "en": "Unknown"}


def speaker_word(language: str | None) -> str:
    """The label word in the transcript's own language ("Szereplő 1", "Sprecher 1"; round 9)."""
    return langs.speaker_word(language)


def unknown_word(language: str | None) -> str:
    return UNKNOWN_WORD.get((language or "").lower(), "Unknown")


def _num(value, fallback: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return fallback
    return fallback if f != f else f  # NaN


def clean_segments(raw_segments: list[dict]) -> list[dict]:
    """Keep only start/end/text/speaker, drop empty text, repair missing times."""
    out: list[dict] = []
    last_end = 0.0
    for seg in raw_segments:
        text = " ".join(str(seg.get("text", "")).split())
        if not text:
            continue
        start = _num(seg.get("start"), last_end)
        end = max(_num(seg.get("end"), start), start)
        out.append({"start": start, "end": end, "text": text, "speaker": seg.get("speaker")})
        last_end = end
    return out


def segments_from_words(words: list[dict], max_chars: int = 220, max_seconds: float = 15.0) -> list[dict]:
    """Group words (text, start, end, optional speaker) into sentence-sized segments: a segment
    ends at a speaker change, after . ! ? …, or when it would grow past the limits."""
    segments: list[dict] = []
    cur: dict | None = None
    for word in words:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        speaker = word.get("speaker")
        start, end = float(word.get("start") or 0), float(word.get("end") or 0)
        if cur is not None and (cur["speaker"] != speaker or len(cur["text"]) + 1 + len(text) > max_chars
                                or end - cur["start"] > max_seconds):
            segments.append(cur)
            cur = None
        if cur is None:
            cur = {"start": start, "end": end, "text": text, "speaker": speaker}
        else:
            cur["text"] += " " + text
            cur["end"] = end
        if text[-1] in ".!?…":
            segments.append(cur)
            cur = None
    if cur is not None:
        segments.append(cur)
    return segments


def relabel_speakers(segments: list[dict], word: str, unknown: str) -> list[dict]:
    """Rename provider speaker ids to '<word> 1..N' by first appearance; returns the summary list."""
    mapping: dict[str, str] = {}
    prev = None
    for seg in segments:
        raw = seg.get("speaker")
        if raw is None:
            seg["speaker"] = prev if prev is not None else unknown
        else:
            if raw not in mapping:
                mapping[raw] = f"{word} {len(mapping) + 1}"
            seg["speaker"] = mapping[raw]
        prev = seg["speaker"]
    totals: dict[str, float] = {}
    for seg in segments:
        totals[seg["speaker"]] = totals.get(seg["speaker"], 0.0) + (seg["end"] - seg["start"])
    spoken = sum(totals.values()) or 1.0
    order = list(mapping.values()) + ([unknown] if unknown in totals else [])
    return [{"label": label, "share": round(totals.get(label, 0.0) / spoken, 3)} for label in order]


def hms(seconds: float | None) -> str:
    s = int(max(seconds or 0, 0))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def turns(segments: list[dict], max_chars: int = 900, max_gap: float = 20.0) -> list[dict]:
    """Consecutive segments of one speaker as paragraphs."""
    out: list[dict] = []
    for seg in segments:
        cur = out[-1] if out else None
        if (cur is not None and cur["speaker"] == seg.get("speaker")
                and len(cur["text"]) + 1 + len(seg["text"]) <= max_chars and seg["start"] - cur["end"] <= max_gap):
            cur["text"] += " " + seg["text"]
            cur["end"] = seg["end"]
        else:
            out.append({"start": seg["start"], "end": seg["end"], "speaker": seg.get("speaker"), "text": seg["text"]})
    return out
