"""Hungarian translation of a transcript, segment by segment (ported from yt-transcribe-webui).

Segments go to the model in numbered batches ("12| text"); every answer line must carry the
same number, so the timestamps stay exactly those of the original. Each batch also sees the end
of the previous batch (for context) and the terms already explained, so an acronym or technical
term gets its Hungarian note only at its first occurrence. Lines the model leaves out are asked
again in smaller batches; what is still missing is left out of the result and counted.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from . import langs
from .errors import WorkError
from .llm import Llm, Usage

PROMPT_VERSION = 1
TARGET = "hu"
BATCH_CHARS = 3000
BATCH_LINES = 40

LANGUAGE_NAMES = {
    "en": "angol", "de": "német", "fr": "francia", "es": "spanyol", "it": "olasz", "pt": "portugál",
    "nl": "holland", "pl": "lengyel", "cs": "cseh", "sk": "szlovák", "ro": "román", "ru": "orosz",
    "uk": "ukrán", "sv": "svéd", "fi": "finn", "da": "dán", "no": "norvég", "tr": "török", "ja": "japán",
    "zh": "kínai", "ko": "koreai", "hr": "horvát", "sr": "szerb", "sl": "szlovén", "el": "görög",
}

SYSTEM = """Fordító vagy. Egy videó vagy hangfelvétel átiratának sorait fordítod {source} nyelvről magyarra.

Szabályok:
- Minden bejövő sort külön fordíts le, a sorszámát megtartva, ebben az alakban: 12| a fordított szöveg Pontosan annyi sort adj vissza, ahány bejött, ugyanazokkal a sorszámokkal, sorrendben. Ne vonj össze és ne bonts szét sorokat, akkor sem, ha egy mondat több sorra esik.
- Természetes, beszélt magyar nyelven fordíts, a jelentést add vissza, ne szóról szóra.
- A neveket, márkaneveket, mozaikszavakat és szakkifejezéseket hagyd eredeti alakjukban. Mozaikszónál és nem magyar szakkifejezésnél az első előfordulásnál írd mögé zárójelben a magyar jelentését, például: „GPU (grafikus processzor)”. Amit a korábbi részekben már megmagyaráztál, azt ne magyarázd újra.
- Csak a fordított sorokat írd ki, semmi mást (Markdown-jelölés nélkül). Az utolsó sor után írj egy külön sort így: MAGYARÁZOTT: kifejezés1; kifejezés2 – azokkal a kifejezésekkel, amelyekhez ebben a részben magyarázatot írtál; ha nincs ilyen: MAGYARÁZOTT: -"""

# every other target language (round 9): one English instruction
SYSTEM_EN = """You are a translator. You translate the lines of a video or audio transcript from {source} into {target}.

Rules:
- Translate every incoming line separately, keeping its number, in this form: 12| the translated text Return exactly as many lines as came in, with the same numbers, in order. Do not merge or split lines, even when a sentence runs over several lines.
- Translate into natural, spoken {target}; render the meaning, not word for word.
- Keep names, brand names, acronyms and technical terms in their original form. For an acronym or a technical term that is not {target}, write its {target} meaning in brackets after its first occurrence, for example: "GPU (graphics processor)" in the target language. Do not explain again what you explained in earlier parts.
- Write only the translated lines, nothing else (no Markdown). After the last line write one separate line like this: EXPLAINED: term1; term2 – with the terms you explained in this part; if there are none: EXPLAINED: -"""

_LINE = re.compile(r"^\s*(\d+)\s*[|]\s?(.*)$")
_EXPLAINED = re.compile(r"^\s*\**\s*(?:MAGYAR[AÁ]ZOTT|EXPLAINED)\s*\**\s*:\s*\**\s*(.*)$", re.I)


def batches(texts: list[tuple[int, str]], max_chars: int = BATCH_CHARS,
            max_lines: int = BATCH_LINES) -> list[list[tuple[int, str]]]:
    out, cur, size = [], [], 0
    for idx, text in texts:
        if cur and (size + len(text) > max_chars or len(cur) >= max_lines):
            out.append(cur)
            cur, size = [], 0
        cur.append((idx, text))
        size += len(text) + 6
    if cur:
        out.append(cur)
    return out


def parse(answer: str, wanted: set[int]) -> tuple[dict[int, str], list[str]]:
    lines: dict[int, str] = {}
    explained: list[str] = []
    if "</think>" in answer:  # only the closing tag came through: drop the reasoning before it
        answer = answer.rsplit("</think>", 1)[1]
    last: int | None = None
    for raw in answer.splitlines():
        raw = raw.strip().strip("`*").strip()
        if not raw:
            continue
        m = _EXPLAINED.match(raw)
        if m:
            explained += [t.strip(" .\"'„”") for t in m.group(1).split(";")
                          if t.strip(" .\"'„”") and t.strip() != "-"]
            continue
        m = _LINE.match(raw)
        if m:
            last = None
            if int(m.group(1)) in wanted and int(m.group(1)) not in lines:
                text = m.group(2).strip().strip("`*").strip()
                if text:
                    lines[int(m.group(1))] = text
                    last = int(m.group(1))
        elif last is not None:  # the model broke one line in two: keep the continuation
            lines[last] += " " + raw
    return lines, explained


def _user_prompt(batch: list[tuple[int, str]], context: list[tuple[str, str]], explained: list[str],
                 hungarian: bool = True) -> str:
    words = (("Az előző sorok (csak a szövegkörnyezethez, ezeket ne fordítsd újra):",
              "Már megmagyarázott kifejezések (ezekhez ne írj újra magyarázatot): ", "Fordítandó sorok:")
             if hungarian else
             ("The previous lines (for context only, do not translate them again):",
              "Terms already explained (do not explain them again): ", "Lines to translate:"))
    parts = []
    if context:
        parts.append(words[0] + "\n" + "\n".join(f"- {orig}  →  {done}" for orig, done in context))
    if explained:
        parts.append(words[1] + "; ".join(explained[-80:]))
    parts.append(words[2] + "\n" + "\n".join(f"{idx}| {text}" for idx, text in batch))
    return "\n\n".join(parts)


def _halves(batch: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    if len(batch) <= 1:
        return [batch]
    mid = len(batch) // 2
    return [batch[:mid], batch[mid:]]


def translate_texts(llm: Llm, model: str, source_language: str | None, texts: list[tuple[int, str]],
                    progress: Callable[[int, int], None] = lambda part, parts: None,
                    cancelled: Callable[[], bool] = lambda: False,
                    target: str = TARGET) -> tuple[dict[int, str], Usage]:
    """Translate (idx, text) pairs into target; returns {idx: text} (missing ones absent) and the usage."""
    hungarian = langs.is_source(target)
    if hungarian:
        source = LANGUAGE_NAMES.get((source_language or "").split("-")[0].lower(), source_language or "ismeretlen")
        system = SYSTEM.format(source=source)
    else:
        system = SYSTEM_EN.format(source=langs.english_name(source_language), target=langs.english_name(target))
    usage = Usage()
    done: dict[int, str] = {}
    explained: list[str] = []
    originals = dict(texts)
    plan = batches([(i, t) for i, t in texts if t.strip()])
    for n, batch in enumerate(plan, 1):
        if cancelled():
            raise WorkError("megszakítva")
        progress(n, len(plan))
        pending = batch
        for attempt in range(3):  # whole batch, then the missing lines in halves
            context = [(originals[i], done[i]) for i in sorted(done)[-3:]]
            got: dict[int, str] = {}
            for piece in ([pending] if attempt == 0 else _halves(pending)):
                answer = usage.add(llm.chat(model, system, _user_prompt(piece, context, explained, hungarian),
                                            temperature=0.2))
                lines, new_terms = parse(answer.text, {i for i, _ in piece})
                got.update(lines)
                explained += [t for t in new_terms if t not in explained]
            done.update(got)
            pending = [(i, t) for i, t in pending if i not in done]
            if not pending:
                break
    return done, usage


def run(job_input: dict[str, Any], llm: Llm, model: str, stage: Callable[[str], None] = lambda s: None,
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[dict[str, Any]], None] = lambda p: None) -> dict[str, Any]:
    segments = job_input.get("segments") or []
    texts = [(int(seg["idx"]), str(seg.get("text") or "")) for seg in segments]
    if not any(t.strip() for _, t in texts):
        raise WorkError("Az átirat üres, nincs mit fordítani.")
    stage("translate")
    target = langs.code(job_input.get("target"))
    lines, usage = translate_texts(llm, model, job_input.get("source_language"), texts, cancelled=cancelled,
                                   progress=lambda part, parts: progress({"part": part, "parts": parts,
                                                                          "model": model}), target=target)
    if not lines:
        raise WorkError("A modell egyetlen sort sem adott vissza a várt alakban; később újrapróbálom.", "later")
    wanted = sum(1 for _, t in texts if t.strip())
    return {"language": target, "model": model, "prompt_version": PROMPT_VERSION,
            "segments": [{"idx": i, "text": lines[i]} for i in sorted(lines)],
            "missing": wanted - len(lines), **usage.facts()}
