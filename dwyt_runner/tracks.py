"""The tracks of an own file (upload, watched folder): which audio track to transcribe, and the subtitles embedded in
it as a transcript (plan 19a item 20, decision 19.12).

ffmpeg's banner lists the streams (there is no ffprobe in imageio-ffmpeg): language, codec, channels, the title from
the stream's metadata, default / forced. Text subtitles (SubRip, ASS/SSA, WebVTT, mov_text) can be read out as SRT;
picture subtitles (PGS, VobSub, DVB) cannot.

Choice: an explicit track the user picked on the item page (options audio_track / subtitle_track, indexes within
their kind), else the stated language, else the default audio track and a subtitle in its language.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from . import formatting, tools
from .errors import WorkError

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
ISO3 = {"eng": "en", "hun": "hu", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "spa": "es", "ita": "it",
        "por": "pt", "rus": "ru", "pol": "pl", "cze": "cs", "ces": "cs", "slo": "sk", "slk": "sk", "slv": "sl",
        "hrv": "hr", "srp": "sr", "rum": "ro", "ron": "ro", "bul": "bg", "dut": "nl", "nld": "nl", "swe": "sv",
        "dan": "da", "nor": "no", "nob": "no", "fin": "fi", "tur": "tr", "gre": "el", "ell": "el", "jpn": "ja",
        "kor": "ko", "chi": "zh", "zho": "zh", "ara": "ar", "ukr": "uk", "heb": "he", "hin": "hi", "est": "et",
        "lav": "lv", "lit": "lt", "cat": "ca", "ice": "is", "isl": "is", "vie": "vi", "tha": "th", "ind": "id"}
_STREAM = re.compile(r"^\s*Stream #\d+:(\d+)(?:\[[^\]]*\])?(?:\(([A-Za-z-]+)\))?: "
                     r"(Audio|Subtitle|Video|Data|Attachment): ([^,\s(]+)(.*)$")
_TITLE = re.compile(r"^\s+title\s*:\s*(.+)$", re.I)
_CHANNELS = re.compile(r",\s*(mono|stereo|\d\.\d(?:\([a-z]+\))?|\d+ channels)\s*(?:,|$)")


def language(code: str | None) -> str | None:
    code = (code or "").strip().lower().split("-")[0]
    if not code or code in ("und", "unk", "mis", "zxx", "mul"):
        return None
    return ISO3.get(code, code if len(code) == 2 else None)


@dataclass
class Track:
    index: int              # within its kind: the N of ffmpeg's -map 0:a:N / 0:s:N
    language: str | None    # two-letter code when known
    codec: str
    title: str | None = None
    channels: str | None = None
    default: bool = False
    forced: bool = False
    text: bool = True       # subtitles only: can be read out as text

    def plain(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass
class Tracks:
    audio: list[Track]
    subtitles: list[Track]

    def report(self, audio_used: int | None, subtitle_used: int | None) -> dict[str, Any]:
        """For the result (protocol: transcribe result `tracks`)."""
        return {"audio": [t.plain() for t in self.audio][:20],
                "subtitles": [{**t.plain(), "text": t.text} for t in self.subtitles][:40],
                "audio_used": audio_used, "subtitle_used": subtitle_used}


def parse(banner: str) -> Tracks:
    audio: list[Track] = []
    subs: list[Track] = []
    current: Track | None = None
    in_input = False
    for line in banner.splitlines():
        if line.startswith("Input #"):
            in_input = True
        elif line.startswith("Output #"):
            break
        if not in_input:
            continue
        m = _STREAM.match(line)
        if m:
            _, lang, kind, codec, rest = m.groups()
            current = None
            flags = rest.lower()
            if kind == "Audio":
                ch = _CHANNELS.search(rest + ",")
                current = Track(len(audio), language(lang), codec, channels=ch.group(1) if ch else None,
                                default="(default)" in flags)
                audio.append(current)
            elif kind == "Subtitle":
                current = Track(len(subs), language(lang), codec, default="(default)" in flags,
                                forced="(forced)" in flags, text=codec.lower() in TEXT_CODECS)
                subs.append(current)
            continue
        t = _TITLE.match(line)
        if t and current is not None and current.title is None:
            current.title = t.group(1).strip()[:120]
    return Tracks(audio, subs)


def read(path: str) -> Tracks:
    try:
        proc = subprocess.run([tools.ffmpeg(), "-nostdin", "-hide_banner", "-i", path], capture_output=True,
                              timeout=120, check=False, **tools.no_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkError(f"Az ffmpeg nem tudta beolvasni a fájlt: {exc}") from exc
    return parse(proc.stderr.decode("utf-8", "replace"))


def describe(tracks: Tracks) -> str:
    """The file's tracks in one Hungarian sentence, for error messages."""
    def name(t: Track, sub: bool) -> str:
        parts = [t.language or "ismeretlen nyelv"]
        if sub and not t.text:
            parts.append("képként")
        if t.forced:
            parts.append("kényszerített")
        if t.default and not sub:
            parts.append("alapértelmezett")
        return parts[0] + (f" ({', '.join(parts[1:])})" if parts[1:] else "")
    audio = ", ".join(name(t, False) for t in tracks.audio) or "nincs"
    subs = ", ".join(name(t, True) for t in tracks.subtitles) or "nincs"
    return f"A fájlban: hangsáv: {audio}; felirat: {subs}."


def _picked(options: dict[str, Any], key: str, count: int, what: str) -> int | None:
    value = options.get(key)
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise WorkError(f"Hibás {what}-sorszám a munkában.") from None
    if not 0 <= n < count:
        raise WorkError(f"A választott {what} nincs a fájlban.")
    return n


def choose_audio(tracks: Tracks, options: dict[str, Any]) -> int | None:
    """The audio track to use (None: the file has no audio track)."""
    if not tracks.audio:
        return None
    picked = _picked(options, "audio_track", len(tracks.audio), "hangsáv")
    if picked is not None:
        return picked
    wanted = language(options.get("language"))
    if wanted:
        for t in tracks.audio:
            if t.language == wanted:
                return t.index
    return next((t.index for t in tracks.audio if t.default), 0)


def choose_subtitle(tracks: Tracks, options: dict[str, Any], audio: int | None) -> Track:
    picked = _picked(options, "subtitle_track", len(tracks.subtitles), "felirat")
    if picked is not None:
        track = tracks.subtitles[picked]
        if not track.text:
            raise WorkError("Ez a felirat képként van tárolva, szövegként nem olvasható ki. Válassz helyi vagy felhős "
                            "átírást.")
        return track
    usable = [t for t in tracks.subtitles if t.text]
    wanted = language(options.get("language"))
    if not wanted and audio is not None:
        wanted = tracks.audio[audio].language
    if wanted:
        found = [t for t in usable if t.language == wanted]
    else:
        found = usable
    if not found:
        what = f" {wanted} nyelvű" if wanted else ""
        pictures = " (csak képként tárolt felirat van benne)" if tracks.subtitles and not usable else ""
        raise WorkError(f"A fájlban nincs{what} szöveges felirat{pictures}. Válassz helyi vagy felhős átírást; az elem "
                        "oldalán látszik, milyen sávok vannak benne.", "next_step")
    # full subtitles before forced ones (those only translate the foreign bits), the default first
    found.sort(key=lambda t: (t.forced, not t.default, t.index))
    return found[0]


def audio_only(path: str, index: int, workdir: str) -> str:
    """One audio track of the file, copied without re-encoding (the transcription modes take the first one)."""
    dst = os.path.join(workdir, "track.mka")
    try:
        subprocess.run([tools.ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-i", path, "-map", f"0:a:{index}",
                        "-c", "copy", "-vn", "-sn", dst], check=True, capture_output=True, timeout=3600,
                       **tools.no_window())
    except subprocess.CalledProcessError as exc:
        raise WorkError("Az ffmpeg nem tudta kivenni a hangsávot: "
                        + exc.stderr.decode("utf-8", "replace")[-300:]) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkError(f"Az ffmpeg nem indult el vagy nem végzett: {exc}") from exc
    return dst


# ---- subtitles as a transcript -------------------------------------------------------------------------------

_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_TAG = re.compile(r"<[^>]+>|\{[^}]*\}")
_SOUND = re.compile(r"\[[^\]]*\]|♪+")


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[dict[str, Any]]:
    """Cues of an SRT file as words for formatting.segments_from_words (tags and sound notes removed)."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("﻿", "")):
        lines = [x for x in block.strip().splitlines() if x.strip()]
        for i, line in enumerate(lines):
            m = _TIME.search(line)
            if m:
                body = " ".join(lines[i + 1:])
                body = _SOUND.sub(" ", _TAG.sub("", body).replace("\\N", " "))
                body = " ".join(body.split()).strip(" -")
                if body:
                    cues.append({"text": body, "start": _seconds(*m.groups()[:4]), "end": _seconds(*m.groups()[4:])})
                break
    return cues


def subtitle_text(path: str, index: int) -> str:
    try:
        proc = subprocess.run([tools.ffmpeg(), "-nostdin", "-loglevel", "error", "-i", path, "-map", f"0:s:{index}",
                               "-f", "srt", "-"], check=True, capture_output=True, timeout=600, **tools.no_window())
    except subprocess.CalledProcessError as exc:
        raise WorkError("Az ffmpeg nem tudta kiolvasni a feliratot: "
                        + exc.stderr.decode("utf-8", "replace")[-300:]) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkError(f"Az ffmpeg nem indult el vagy nem végzett: {exc}") from exc
    return proc.stdout.decode("utf-8", "replace")


def transcript(path: str, track: Tracks, sub: Track, audio: int | None) -> dict[str, Any]:
    segments = formatting.clean_segments(formatting.segments_from_words(parse_srt(subtitle_text(path, sub.index))))
    if not segments:
        raise WorkError("A felirat üres.", "next_step")
    for seg in segments:
        seg["speaker"] = None
    lang = sub.language or (track.audio[audio].language if audio is not None else None)
    warnings = ["A fájlba ágyazott feliratból készült: nincsenek benne beszélők."]
    if sub.forced:
        warnings.append("Kényszerített felirat: csak az idegen nyelvű részeket tartalmazza.")
    return {"mode": "captions", "model": f"embedded-{sub.codec.lower()}", "caption_kind": "embedded",
            "language": lang, "diarized": False, "speakers": [], "warnings": warnings, "segments": segments}
