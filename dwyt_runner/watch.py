"""Watched folders: new audio/video files are transcribed without leaving this machine.

Every scan (about once a minute, between job requests) lists the folders recursively. A file is
registered once its size and modification time stayed the same across two scans (it is not being
copied any more): its SHA-256 goes to the central service (local-items), which creates the item and
a transcription job that only this runner can claim. The job names the file by its hash; the
runner finds it again through its state file (watched.json next to config.json). Files are never
moved, changed or deleted; a file already handled (same path, size and time) is skipped.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from . import config
from .config import Config
from .media import file_sha256
from .protocol import Central, CentralError, RefusedItem

log = logging.getLogger("dwyt-runner")
EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma", ".amr", ".3gp",
              ".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi", ".wmv", ".mpeg", ".mpg", ".ts"}
SETTLE_S = 30            # a file must be this old and unchanged before it is registered
RETRY_ERROR_S = 3600     # a refused file is tried again after this long


def state_path() -> Path:
    return config.home() / "watched.json"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else {"files": {}}
    except (OSError, ValueError):
        return {"files": {}}


def save_state(state: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def path_for(sha256: str) -> str | None:
    """The watched file with this content (the newest registration), if it still exists."""
    for path, entry in sorted(load_state()["files"].items(), key=lambda kv: kv[1].get("registered_at", 0),
                              reverse=True):
        if entry.get("sha256") == sha256 and os.path.isfile(path):
            return path
    return None


def options(cfg: Config) -> dict[str, Any]:
    mode = cfg.watch_mode if cfg.watch_mode in ("local", "cloud") else "local"
    model = cfg.watch_model or ((cfg.whisper_models or ["large-v3"])[0] if mode == "local" else "auto")
    out: dict[str, Any] = {"mode": mode, "model": model}
    if cfg.watch_language:
        out["language"] = cfg.watch_language
    return out


def candidates(dirs: list[str]):
    for root in dirs:
        base = Path(root).expanduser()
        if not base.is_dir():
            log.warning("watched folder not found: %s", base)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith(".") or Path(name).suffix.lower() not in EXTENSIONS:
                    continue
                yield os.path.join(dirpath, name)


def scan(cfg: Config, central: Central, now: float | None = None) -> int:
    """One pass over the watched folders; returns how many files were registered."""
    if not cfg.watch_dirs:
        return 0
    now = time.time() if now is None else now
    state = load_state()
    files = state["files"]
    registered = 0
    for path in candidates(cfg.watch_dirs):
        try:
            st = os.stat(path)
        except OSError:
            continue
        entry = files.get(path)
        signature = [st.st_size, int(st.st_mtime)]
        if entry and entry.get("signature") == signature:
            if entry.get("state") in ("registered", "done"):
                continue
            if entry.get("state") == "error" and now - entry.get("tried_at", 0) < RETRY_ERROR_S:
                continue
            if entry.get("state") == "seen" and now - st.st_mtime < SETTLE_S:
                continue
        elif not entry or entry.get("signature") != signature:
            files[path] = {"signature": signature, "state": "seen", "seen_at": now}
            continue  # wait one more scan: it may still be copying
        if st.st_size > cfg.max_file_mb * 1024 ** 2:
            files[path] = {**files[path], "state": "error", "tried_at": now,
                           "error": f"nagyobb, mint {cfg.max_file_mb} MB (max_file_mb)"}
            continue
        sha = file_sha256(path)
        try:
            answer = central.local_item(sha, os.path.basename(path), st.st_size, options(cfg), cfg.watch_summarize)
        except RefusedItem as exc:
            files[path] = {**files[path], "sha256": sha, "state": "error", "tried_at": now, "error": str(exc)}
            log.warning("watched file refused: %s: %s", path, exc)
            continue
        except CentralError as exc:
            log.warning("could not register %s (%s); next scan", path, exc)
            break
        files[path] = {"signature": signature, "sha256": sha, "registered_at": now, "item_id": answer.get("item_id"),
                       "job_id": answer.get("job_id"), "state": "done" if answer.get("existing") else "registered"}
        registered += 1
        log.info("watched file registered: %s (item %s)", path, answer.get("item_id"))
    save_state(state)
    return registered
