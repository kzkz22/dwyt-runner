"""Updating the runner itself from signed releases (plan 15b, decisions 18.12, 18.20).

Three ways the runner can be installed, each updated with its own package from the same signed release:
  uv        `uv tool install` (servers, the one-line installer, developers): the wheel, installed with uv
  inno      the Windows installer: the new installer, run silently (it closes the app and starts it again)
  appimage  the Linux AppImage: the file is replaced, the app starts again
The package kind is written into the bundled Python at build time (`dwyt-runner-package.json` in sys.prefix);
without it the runner is a uv install.

Only when idle, and only files the signed list names (release.py). The desktop app updates by itself
(`auto_update` auto = on there); a server only tells (dwyt shows the newer version, "Frissítés most").
The version before the last update is remembered, and "Előző változat" installs it again from its own
signed release.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from . import __version__, config, release, tools

log = logging.getLogger("dwyt-runner")
ASSET_FOR = {"uv": "wheel", "inno": "windows", "appimage": "appimage"}
MARKER = "dwyt-runner-package.json"
CHECK_EVERY_S = 24 * 3600
APP = False  # set by the desktop app: auto_update "auto" means on there
ON_RESTART: Callable[[str], None] | None = None  # the app's way to restart ("restart") or quit ("quit")

STATE: dict[str, Any] = {"latest": "", "checked": 0.0, "running": False, "error": "", "message": ""}
_lock = threading.Lock()


class UpdateError(Exception):
    """A user-facing reason (Hungarian)."""


def kind() -> str:
    if os.environ.get("APPIMAGE") and Path(os.environ["APPIMAGE"]).is_file():
        return "appimage"
    try:
        data = json.loads((Path(sys.prefix) / MARKER).read_text(encoding="utf-8"))
        if data.get("kind") in ASSET_FOR:
            return str(data["kind"])
    except (OSError, ValueError):
        pass
    return "uv"


def signed() -> bool:
    """Signed updates are possible (the project's public key is in this version)."""
    return bool(release.PUBLIC_KEY)


def auto(cfg) -> bool:
    if cfg.auto_update == "on":
        return True
    if cfg.auto_update == "off":
        return False
    return APP


def folder() -> Path:
    return config.data_home() / "updates"


def _state_file() -> Path:
    return folder() / "state.json"


def history() -> dict[str, Any]:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(before: str, after: str) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    data = history()
    data.update(previous=before, current=after, at=time.time())
    _state_file().write_text(json.dumps(data), encoding="utf-8")


# ---- checking and downloading -----------------------------------------------------------------------

def check(timeout: float = 20) -> release.Release | None:
    """The newest signed release (STATE updated); None when there is no key or the check failed."""
    if not signed():
        return None
    STATE["checked"] = time.time()
    try:
        rel = release.latest(timeout=timeout)
    except (release.BadSignature, httpx.HTTPError, ValueError) as exc:
        STATE["error"] = f"A frissítések nem ellenőrizhetők: {exc}"[:300]
        log.warning("update check: %s", exc)
        return None
    STATE.update(latest=rel.version, error="")
    return rel


def download(rel: release.Release, asset_kind: str, timeout: float = 60) -> Path:
    """The release's file for this package kind, checked against the signed SHA-256."""
    asset = rel.assets.get(asset_kind)
    if asset is None:
        raise UpdateError(f"A(z) {rel.version} kiadásban nincs ilyen csomag ({asset_kind}).")
    folder().mkdir(parents=True, exist_ok=True)
    final = folder() / asset.name
    part = final.with_name(final.name + ".part")
    digest = hashlib.sha256()
    done = 0
    limit = asset.size + 1024 if asset.size else 2 * 1024 ** 3
    try:
        with httpx.stream("GET", rel.url(asset_kind), follow_redirects=True,
                          timeout=httpx.Timeout(timeout, connect=15),
                          headers={"User-Agent": f"dwyt-runner/{__version__}"}) as resp:
            resp.raise_for_status()
            with open(part, "wb") as fh:
                for chunk in resp.iter_bytes(1024 * 1024):
                    done += len(chunk)
                    if done > limit:
                        raise UpdateError("A letöltött fájl nagyobb, mint az aláírt listában.")
                    digest.update(chunk)
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"A frissítés letöltése nem sikerült: {exc}") from exc
    if digest.hexdigest() != asset.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("A letöltött fájl nem egyezik az aláírt listával; nem telepítem.")
    os.replace(part, final)
    return final


# ---- installing -----------------------------------------------------------------------------------------

def extras() -> list[str]:
    return [name for name, module in (("local", "sherpa_onnx"), ("desktop", "webview"))
            if importlib.util.find_spec(module) is not None]


def _install_uv(path: Path) -> str:
    from .ytdlp_update import find_uv
    uv = find_uv()
    if uv is None:
        raise UpdateError("Nincs uv ezen a gépen, amivel frissíthetnék.")
    ext = extras()
    spec = f"dwyt-runner{'[' + ','.join(ext) + ']' if ext else ''} @ {path.resolve().as_uri()}"
    proc = subprocess.run([uv, "tool", "install", "--force", "--python", "3.13", spec], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=1200, check=False,
                          **tools.no_window())
    if proc.returncode != 0:
        raise UpdateError(((proc.stderr or proc.stdout).strip()[-300:]) or f"uv: kilépési kód {proc.returncode}")
    return "restart"


def _install_inno(path: Path) -> str:
    # the installer closes this app (Restart Manager) and starts it again when it is done
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True)
    return "quit"


def _install_appimage(path: Path) -> str:
    target = Path(os.environ["APPIMAGE"])
    new = target.with_name(target.name + ".new")
    shutil.copyfile(path, new)
    os.chmod(new, 0o755)
    os.replace(new, target)
    path.unlink(missing_ok=True)
    return "restart"


INSTALLERS = {"uv": _install_uv, "inno": _install_inno, "appimage": _install_appimage}


def install(rel: release.Release, how: str | None = None) -> str:
    """Download, check and install `rel` for this package kind; returns "restart" or "quit"."""
    how = how or kind()
    path = download(rel, ASSET_FOR[how])
    action = INSTALLERS[how](path)
    _remember(__version__, rel.version)
    log.info("updated %s -> %s (%s)", __version__, rel.version, how)
    return action


def update_now(target: str | None = None) -> tuple[bool, str, str]:
    """(ok, message, action). target: a version to go back to (its own signed release), else the newest."""
    if not signed():
        return False, "Ebben a változatban még nincs aláírt frissítés.", ""
    with _lock:
        if STATE["running"]:
            return False, "A frissítés már folyamatban van.", ""
        STATE.update(running=True, message="")
    try:
        if target:
            rel = release.for_version(target)
        else:
            rel = check()
            if rel is None:
                raise UpdateError(STATE["error"] or "A frissítés nem ellenőrizhető.")
            if not release.newer(rel.version):
                return True, f"A legújabb változat fut ({__version__}).", ""
        action = install(rel)
        message = f"{__version__} → {rel.version} telepítve; " + ("újraindulok." if action == "restart" else
                                                               "a telepítő újraindítja a futtatót.")
        STATE["message"] = message
        return True, message, action
    except (UpdateError, release.BadSignature, httpx.HTTPError, OSError, subprocess.SubprocessError) as exc:
        STATE["error"] = str(exc)[:300]
        log.warning("update: %s", exc)
        return False, str(exc)[:300], ""
    finally:
        STATE["running"] = False


def finish(action: str) -> None:
    """Start the new version: the app restarts itself; a command-line runner exits or replaces itself."""
    if not action:
        return
    if ON_RESTART is not None:
        ON_RESTART(action)
        return
    from . import remote
    if action == "quit":
        raise remote.RestartNow(0)
    remote.restart()


def due(cfg) -> bool:
    return signed() and time.time() - STATE["checked"] >= CHECK_EVERY_S


def run_if_due(cfg) -> None:
    """Called by the idle worker: the daily check, and with auto_update the update itself."""
    if not due(cfg):
        return
    rel = check()
    if rel is None or not release.newer(rel.version) or not auto(cfg):
        return
    ok, message, action = update_now()
    if ok and action:
        finish(action)
