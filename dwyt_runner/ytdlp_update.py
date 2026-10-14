"""Keeping yt-dlp fresh (plan 15b, decision 18.12: "yt-dlp always by itself").

YouTube changes often, and a stale yt-dlp then fails on some videos (HTTP 403 on the media, missing
formats). The runner compares its yt-dlp with the newest on PyPI and upgrades it into its own environment
(with uv, which installed it, else pip) while it is idle: at start, then daily, and soon after a 403. The
new version is used from the next job on: yt_dlp is imported inside the functions that use it, so dropping
it from sys.modules is enough (no job runs at that moment).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from importlib import metadata
from typing import Any

import httpx

from . import tools

log = logging.getLogger("dwyt-runner")
PYPI = "https://pypi.org/pypi/yt-dlp/json"
SPEC = "yt-dlp[default,curl-cffi]"
DAILY_S = 24 * 3600
AFTER_403_S = 3600  # a 403 asks for a check, at most once an hour

STATE: dict[str, Any] = {"installed": "", "latest": "", "checked": 0.0, "running": False, "error": "",
                         "updated": ""}
_lock = threading.Lock()
_wanted_soon = threading.Event()


def installed() -> str:
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return ""


def latest(timeout: float = 15) -> str:
    resp = httpx.get(PYPI, timeout=timeout, headers={"User-Agent": "dwyt-runner"})
    resp.raise_for_status()
    return str(resp.json()["info"]["version"])


def _key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in version.replace("-", ".").split("."))


def newer(candidate: str, current: str) -> bool:
    return bool(candidate) and (not current or _key(candidate) > _key(current))


def install_command() -> list[str] | None:
    """uv when it is there (it installed the runner), else this Python's pip; None when neither."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, "--upgrade", SPEC]
    try:
        import pip  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", SPEC]


def _forget_loaded() -> None:
    for name in [m for m in sys.modules if m == "yt_dlp" or m.startswith("yt_dlp.")]:
        del sys.modules[name]


def check(update: bool = True, timeout: float = 15) -> dict[str, Any]:
    """Look up the newest version and, with update, install it. Never raises: the state says what happened."""
    with _lock:
        if STATE["running"]:
            return dict(STATE)
        STATE.update(running=True, error="", checked=time.time())  # a failed try also waits a day
    try:
        STATE["installed"] = installed()
        STATE["latest"] = latest(timeout)
        if update and newer(STATE["latest"], STATE["installed"]):
            cmd = install_command()
            if cmd is None:
                raise RuntimeError("nincs uv és pip sem, amivel frissíthetném")
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=600, check=False, **tools.no_window())
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout).strip()[-300:] or f"kilépési kód {proc.returncode}")
            _forget_loaded()
            before, STATE["installed"] = STATE["installed"], installed() or STATE["latest"]
            STATE["updated"] = f"{before} → {STATE['installed']}"
            log.info("yt-dlp updated: %s", STATE["updated"])
    except Exception as exc:  # noqa: BLE001 - no network, PyPI down, no permission: try again later
        STATE["error"] = str(exc)[:300]
        log.warning("yt-dlp update check: %s", exc)
    finally:
        STATE["running"] = False
    return dict(STATE)


def want_soon() -> None:
    """A download was refused (403): check before the next job, if the last check is old enough."""
    if time.time() - STATE["checked"] >= AFTER_403_S:
        _wanted_soon.set()


def due(auto: bool) -> bool:
    """Should the idle worker check now?"""
    if not auto:
        return False
    return _wanted_soon.is_set() or time.time() - STATE["checked"] >= DAILY_S


def run_if_due(auto: bool) -> None:
    if due(auto):
        _wanted_soon.clear()
        check(update=True)
