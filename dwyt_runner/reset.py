"""Back to the start (plan 15b, decision 18.15): the window's "Alaphelyzet" and `dwyt-runner reset`.

Switches the start at login off, deletes the keys from the system key store, and deletes the settings
folder (config.json, setup.json, the machine check, the test run, the history, the watched-folder state).
The downloaded models stay unless asked: they are big and the same for every setup. The running window's
window.json and the open app.log are kept (the app that does the reset is still running). The runner stays
in the dwyt list until it is revoked there: the central has no way to take a token back from this side.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import autostart, config, keystore, machine

log = logging.getLogger("dwyt-runner")
KEEP = {"window.json", "app.log", "app.log.1", "app.log.2"}


def run(models: bool = False) -> list[str]:
    """What was done, in Hungarian sentences (the window and the CLI print them)."""
    done: list[str] = []
    try:
        if autostart.enabled():
            autostart.disable()
            done.append("A bejelentkezéskori indulást kikapcsoltam.")
    except OSError as exc:
        done.append(f"A bejelentkezéskori indulást nem sikerült kikapcsolni: {exc}")
    if keystore.usable():
        for key in sorted(config.SECRET_KEYS):
            keystore.put(key, "")
        done.append("A kulcsokat töröltem a rendszer kulcstárolójából.")
    home = config.home()
    models_dir = machine.models_dir()
    removed = 0
    if home.is_dir():
        for entry in home.iterdir():
            if entry.name in KEEP or _same(entry, models_dir):
                continue
            try:
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
                removed += 1
            except OSError as exc:
                done.append(f"Nem tudtam törölni: {entry} ({exc})")
    done.append("A beállításokat töröltem." if removed else "Beállítás nem volt.")
    if models and models_dir.exists():
        try:
            shutil.rmtree(models_dir)
            done.append("A letöltött modelleket töröltem.")
        except OSError as exc:
            done.append(f"A modelleket nem tudtam törölni: {exc}")
    elif models_dir.exists():
        done.append(f"A letöltött modellek megmaradtak: {models_dir}")
    log.info("reset: %s", " ".join(done))
    return done


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False
