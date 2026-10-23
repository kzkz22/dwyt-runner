"""The runner's interface languages (round 9, decision 19.24): the window, the tray, the setup wizard, the command
line, the settings descriptions and the messages a job's failure sends to the central.

The source texts are the Hungarian ones in the code and the templates; a translation is a `locale/<code>.po` file
(msgid = the Hungarian text), read here without extra packages. Lookups go: the chosen language -> English -> the
Hungarian text itself.

Which language: the `ui_language` setting; `auto` = the paired account's interface language (the central sends it with
every claim answer, kept in `languages.json` next to the settings), before pairing the system's language, and English
when that is none of the translated ones.
"""
from __future__ import annotations

import ast
import contextvars
import json
import locale
import os
import sys
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
SOURCE = "hu"
FALLBACK = "en"
PSEUDO = "xx"  # tests: every translated text marked
UI_LANGUAGES = ("hu", "en", "de", "it", "es", "fr")  # interfaces with their own translation
NAMES = {"hu": "Magyar", "en": "English", "de": "Deutsch", "it": "Italiano", "es": "Español", "fr": "Français"}
# how local models of ~30B write in each content language (the central's table): a note for the Operation tab
WEAK_LOCAL = ("lt", "sk", "sl", "hr", "bg", "lv")
ENDONYMS = {"lt": "lietuvių", "sk": "slovenčina", "sl": "slovenščina", "hr": "hrvatski", "bg": "български",
            "lv": "latviešu"}

_chosen = SOURCE
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("runner_language", default=None)


def current() -> str:
    return _current.get() or _chosen


def set_language(code: str | None) -> None:
    """The process-wide language (the window, the worker)."""
    global _chosen
    _chosen = code if code in UI_LANGUAGES or code == PSEUDO else FALLBACK


@contextmanager
def language(code: str | None) -> Iterator[None]:
    token = _current.set(code if code in UI_LANGUAGES or code == PSEUDO else FALLBACK)
    try:
        yield
    finally:
        _current.reset(token)


def _unquote(chunk: str) -> str:
    return ast.literal_eval(chunk) if chunk.startswith('"') else ""


def read_po(path: Path) -> dict[str, str]:
    """msgid -> msgstr of a .po file (plain entries; fuzzy and empty ones left out)."""
    out: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    fuzzy = False
    target = None

    def flush() -> None:
        if msgid is not None and msgstr is not None:
            key, value = "".join(msgid), "".join(msgstr)
            if key and value and not fuzzy:
                out[key] = value

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                flush()
                msgid, msgstr, target, fuzzy = None, None, None, True
            continue
        if line.startswith("msgid "):
            if msgstr is not None:
                flush()
                fuzzy = False if msgid is not None else fuzzy
            msgid, msgstr, target = [_unquote(line[6:])], None, "id"
        elif line.startswith("msgstr "):
            msgstr, target = [_unquote(line[7:])], "str"
        elif line.startswith('"'):
            (msgid if target == "id" else msgstr).append(_unquote(line))
    flush()
    return out


@lru_cache(maxsize=None)
def catalog(code: str) -> dict[str, str]:
    path = LOCALE_DIR / f"{code}.po"
    return read_po(path) if path.is_file() else {}


def gettext(message: str) -> str:
    code = current()
    if code == SOURCE or not message:
        return message
    if code == PSEUDO:
        return "⟦" + message + "⟧"
    return catalog(code).get(message) or catalog(FALLBACK).get(message) or message


_ = gettext


def N_(message: str) -> str:
    """Marks a text for extraction where it is defined (a table); translated where it is shown."""
    return message


def tr(value: Any) -> Any:
    """The strings in a value (a tuple of texts …) in the current language."""
    if isinstance(value, str):
        return gettext(value)
    if isinstance(value, (tuple, list)):
        return type(value)(tr(v) for v in value)
    return value


class Labels(dict):
    """A table of texts by key: reading a value gives it in the current language (the keys stay)."""

    def __getitem__(self, key):
        return tr(super().__getitem__(key))

    def get(self, key, default=None):
        return tr(super().get(key, default))

    def items(self):
        return [(k, tr(v)) for k, v in super().items()]

    def values(self):
        return [tr(v) for v in super().values()]


def labels(table: dict) -> Labels:
    return Labels(table)


# ---- which language -------------------------------------------------------------------------------------------

def system_language() -> str | None:
    """The operating system's interface language as a two-letter code, when it can be told."""
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid)
            if name:
                return name.split("_")[0].lower()
        except Exception:  # noqa: BLE001 - not available
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var, "")
        if value and value not in ("C", "POSIX", "C.UTF-8"):
            return value.split(":")[0].split("_")[0].split(".")[0].lower() or None
    try:
        name = locale.getlocale()[0]
    except ValueError:
        name = None
    return name.split("_")[0].lower() if name else None


def account_languages(folder: Path) -> dict[str, str]:
    """What the central last said about the paired account: {"ui": "de", "content": "de"} (or {})."""
    try:
        data = json.loads((folder / "languages.json").read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if k in ("ui", "content") and isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def remember_account(folder: Path, header: str | None) -> bool:
    """Keep the claim answer's `Dwyt-Languages: ui=de; content=de`; True when it changed."""
    if not header:
        return False
    found = {}
    for part in header.split(";"):
        key, _sep, value = part.strip().partition("=")
        if key in ("ui", "content") and value.strip().isalpha() and len(value.strip()) <= 3:
            found[key] = value.strip().lower()
    if not found or found == account_languages(folder):
        return False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "languages.json").write_text(json.dumps(found), encoding="utf-8")
    except OSError:
        return False
    return True


def choose(setting: str | None, folder: Path | None) -> str:
    """The interface language: the setting, else the account's, else the system's, else English."""
    if setting in UI_LANGUAGES:
        return setting
    forced = os.environ.get("DWYT_RUNNER_LANG", "")
    if forced in UI_LANGUAGES or forced == PSEUDO:
        return forced
    if folder is not None:
        ui = account_languages(folder).get("ui")
        if ui in UI_LANGUAGES:
            return ui
        if ui:  # a round-2 account language: its interface is English
            return FALLBACK
    system = system_language()
    return system if system in UI_LANGUAGES else FALLBACK


def weak_local_language(folder: Path) -> str | None:
    """The account's content language by its own name when local models write it noticeably worse, else None."""
    content = account_languages(folder).get("content", "")
    return ENDONYMS.get(content) if content in WEAK_LOCAL else None


def apply(cfg: Any) -> str:
    """Set the process-wide language from the settings (and what the central said); returns it."""
    from . import config
    code = choose(getattr(cfg, "ui_language", "auto"), config.home())
    set_language(code)
    return code
