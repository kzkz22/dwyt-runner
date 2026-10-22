"""What the runner is doing and has done: the current job and a short history (history.json), for the
desktop window's Status tab (plan 15b, round 7c). Titles only, never transcripts or keys."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from . import config

KIND_LABELS = {"transcribe": "Átírás", "summarize": "Összefoglaló", "translate": "Fordítás", "ask": "Kérdés",
               "categorize": "Kategorizálás", "chapters": "Fejezetek"}
STAGE_LABELS = {
    "starting": "indul", "download": "letöltés", "captions": "felirat letöltése", "prepare-audio": "hang előkészítése",
    "upload": "feltöltés a felhőbe", "transcribe": "átírás", "diarize": "beszélők szétválasztása",
    "waiting-for-gpu": "vár a videókártyára", "download-model": "modell letöltése (első használat)",
    "summarize": "összefoglaló írása", "translate": "fordítás", "ask": "válasz írása", "write": "mentés",
    "page": "felvétel keresése az oldalon", "categorize": "kategória választása", "chapters": "fejezetek írása",
}
HISTORY_MAX = 200
_lock = threading.Lock()
CURRENT: dict[str, Any] = {}


def title_of(job: dict[str, Any]) -> str:
    inp = job.get("input") or {}
    meta = inp.get("meta") or {}
    source = inp.get("source") or {}
    if job.get("kind") == "ask":
        return "„" + str(inp.get("question") or "")[:80] + "”"
    return str(meta.get("title") or inp.get("title") or source.get("name") or source.get("url") or "")[:160]


def where_of(job: dict[str, Any], cfg: config.Config) -> str:
    """'ezen a gépen' or 'felhőben' (or the AI router), as the window shows it."""
    kind = job.get("kind")
    inp = job.get("input") or {}
    if kind == "transcribe":
        mode = (inp.get("options") or {}).get("mode")
        if mode == "cloud":
            return "felhőben"
        if mode == "local":
            return "a GPU-gépen" if cfg.whisper_url else "ezen a gépen"
        return "YouTube-felirat"
    from . import localllm
    return "ezen a gépen" if localllm.uses_local(cfg) else "felhőben"


def start(job: dict[str, Any], cfg: config.Config) -> None:
    with _lock:
        CURRENT.clear()
        CURRENT.update(id=job.get("job_id"), kind=job.get("kind"), title=title_of(job), where=where_of(job, cfg),
                       stage="starting", progress=None, started=time.time())


def stage(name: str) -> None:
    with _lock:
        if CURRENT:
            CURRENT["stage"] = name


def progress(data: dict[str, Any]) -> None:
    with _lock:
        if CURRENT:
            CURRENT["progress"] = data


def finish(outcome: str, error: str = "") -> None:
    with _lock:
        if not CURRENT:
            return
        entry = {"kind": CURRENT.get("kind"), "title": CURRENT.get("title"), "where": CURRENT.get("where"),
                 "outcome": outcome, "error": error[:300], "at": time.time(),
                 "seconds": round(time.time() - (CURRENT.get("started") or time.time()), 1)}
        CURRENT.clear()
    items = history()
    items.append(entry)
    path = config.home() / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items[-HISTORY_MAX:], ensure_ascii=False), encoding="utf-8")


def history() -> list[dict[str, Any]]:
    try:
        data = json.loads((config.home() / "history.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def current() -> dict[str, Any]:
    with _lock:
        return dict(CURRENT)


def today() -> dict[str, int]:
    """Today's finished jobs per kind."""
    start_of_day = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    out: dict[str, int] = {}
    for e in history():
        if e.get("at", 0) >= start_of_day and e.get("outcome") == "done":
            out[e.get("kind") or "?"] = out.get(e.get("kind") or "?", 0) + 1
    return out
