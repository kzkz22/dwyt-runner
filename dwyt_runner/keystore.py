"""Where the runner keeps its secrets (plan 15b, decision 18.10): the operating system's key store when it
has one (Windows Credential Manager, macOS Keychain, the Secret Service of a Linux desktop), otherwise the
settings file, readable only by its owner (a server without a desktop session).

config.json then holds an empty value and the note "secrets": "keyring"; the keys themselves are under the
service name "dwyt-runner". Keys already in the file move to the key store the first time the runner sees a
usable one. `secret_store` = auto (default) | keyring | file.
"""
from __future__ import annotations

import logging

SERVICE = "dwyt-runner"
log = logging.getLogger("dwyt-runner")
_usable: bool | None = None
why_not = ""  # the last probe's failure, for the message


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None, None
    return keyring, fail


def usable() -> bool:
    """A real key store answers (probed once: set, read back, delete)."""
    global _usable, why_not
    if _usable is not None:
        return _usable
    keyring, fail = _keyring()
    _usable = False
    if keyring is None:
        why_not = "a keyring csomag nincs telepítve"
        return False
    try:
        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring) or "chainer" in type(backend).__name__.lower() and \
                not getattr(backend, "backends", None):
            why_not = f"nincs kulcstároló ({type(backend).__name__})"
            return False
        keyring.set_password(SERVICE, "_probe", "ok")
        _usable = keyring.get_password(SERVICE, "_probe") == "ok"
        keyring.delete_password(SERVICE, "_probe")
    except Exception as exc:  # noqa: BLE001 - a locked or broken store: fall back to the file
        why_not = f"{type(exc).__name__}: {exc}"
        log.warning("key store not usable: %s", why_not)
        _usable = False
    return _usable


def reset() -> None:
    global _usable
    _usable = None


def name() -> str:
    keyring, _ = _keyring()
    if not keyring or not usable():
        return "a beállítási fájl (csak a te felhasználód olvashatja)"
    backend = type(keyring.get_keyring()).__module__
    if "Windows" in backend or "windows" in backend:
        return "a Windows hitelesítőadat-kezelője"
    if "macOS" in backend or "OS_X" in backend:
        return "a macOS kulcskarikája"
    if "SecretService" in backend or "libsecret" in backend or "kwallet" in backend.lower():
        return "a rendszer kulcstárolója (Secret Service)"
    return "a rendszer kulcstárolója"


def get(key: str) -> str:
    keyring, _ = _keyring()
    try:
        return keyring.get_password(SERVICE, key) or ""
    except Exception as exc:  # noqa: BLE001
        log.warning("key store: cannot read %s: %s", key, exc)
        return ""


def put(key: str, value: str) -> None:
    keyring, _ = _keyring()
    if value:
        keyring.set_password(SERVICE, key, value)
    else:
        try:
            keyring.delete_password(SERVICE, key)
        except Exception:  # noqa: BLE001 - nothing stored under that name
            pass


# ---- checks: does the key work? (one cheap request each) ----------------------------------------

def check_assemblyai(cfg) -> tuple[bool, str]:
    import httpx
    if not cfg.assemblyai_key:
        return False, "nincs megadva"
    try:
        r = httpx.get(cfg.assemblyai_url.rstrip("/") + "/v2/transcript", params={"limit": 1},
                      headers={"authorization": cfg.assemblyai_key}, timeout=15)
    except httpx.HTTPError as exc:
        return False, f"nem érhető el ({exc})"
    if r.status_code == 200:
        return True, "rendben"
    if r.status_code in (401, 403):
        return False, "a kulcsot nem fogadja el"
    return False, f"HTTP {r.status_code}"


def check_llm(cfg) -> tuple[bool, str]:
    import httpx

    from . import localllm
    eff = localllm.effective(cfg)
    if not eff.llm_url:
        return False, "nincs megadva LLM-cím"
    base = eff.llm_url.rstrip("/")
    headers = {"Authorization": f"Bearer {eff.llm_key}"} if eff.llm_key else {}
    if "openrouter.ai" in base:
        # OpenRouter lists its models to anyone: only the key endpoint says whether the key is good
        for path in ("/key", "/auth/key"):
            try:
                r = httpx.get(base + path, headers=headers, timeout=15)
            except httpx.HTTPError as exc:
                return False, f"nem érhető el ({exc})"
            if r.status_code == 200:
                return True, "rendben"
            if r.status_code in (401, 403):
                return False, "a kulcsot nem fogadja el"
    url = (base if base.endswith("/v1") else base + "/v1") + "/models"
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {eff.llm_key}"} if eff.llm_key else {}, timeout=15)
    except httpx.HTTPError as exc:
        return False, f"nem érhető el ({exc})"
    if r.status_code == 200:
        return True, "rendben"
    if r.status_code in (401, 403):
        return False, "a kulcsot nem fogadja el"
    return False, f"HTTP {r.status_code}"
