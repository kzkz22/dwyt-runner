"""One video card for the language model and Whisper (plan 15b, decision 18.7).

Before a built-in local transcription the runner frees the card: it unloads the models of the local
model server (Ollama / LM Studio), as llama-swap does on the AI router. Models only this runner used are
unloaded at once. A model someone else loaded may be in use (a chat in LM Studio, another program on
Ollama); in polite mode (default) the runner first waits until it has been idle for a minute, at most
polite_wait_min (10) minutes, then unloads it anyway. Signs of use: the video card's load (nvidia-smi)
and, on Ollama, the model's expiry time, which moves on with every request.

The server reloads a model by itself on its next request, so nothing is lost; the first answer after a
transcription just takes the loading time. `STATE` tells the window (7c) what is going on, so it can
show the warning and the orange tray icon.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from . import localllm, machine
from .config import Config
from .errors import WorkError
from .i18n import gettext as _

log = logging.getLogger("dwyt-runner")
POLL_S = 10
IDLE_S = 60
BUSY_UTIL = 20  # % video card load that counts as someone working

STATE: dict[str, Any] = {"phase": "idle", "models": [], "since": 0.0}
_sleep = time.sleep
_now = time.monotonic


def gpu_util() -> int | None:
    smi = machine.nvidia_smi()
    if not smi:
        return None
    out = machine._run([smi, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"])
    values = [int(v) for v in out.split() if v.strip().isdigit()]
    return max(values) if values else None


def has_card() -> bool:
    return machine.nvidia_smi() is not None


def _activity(server: localllm.LocalServer) -> tuple[tuple, int | None]:
    loaded = server.loaded()
    return tuple(sorted((m.name, m.expires_at) for m in loaded)), gpu_util()


def wait_until_idle(server: localllm.LocalServer, max_s: float, stage: Callable[[str], None],
                    cancelled: Callable[[], bool]) -> bool:
    """True when the models were idle for IDLE_S; False when max_s ran out first."""
    deadline = _now() + max_s
    last, _x = _activity(server)
    quiet_since = _now()
    while _now() < deadline:
        if cancelled():
            raise WorkError(_("megszakítva"))
        stage("waiting-for-gpu")
        _sleep(POLL_S)
        now, util = _activity(server)
        if not now:  # unloaded meanwhile (an idle timeout, or the user)
            return True
        if now != last or (util is not None and util >= BUSY_UTIL):
            quiet_since = _now()
            last = now
        elif _now() - quiet_since >= IDLE_S:
            return True
    return False


def before_whisper(cfg: Config, stage: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False
                   ) -> list[str]:
    """Free the video card for a built-in transcription; returns what was unloaded."""
    if not cfg.whisper_builtin or cfg.whisper_url or not has_card():
        return []  # the GPU host's llama-swap does this itself; on the processor nothing to free
    server = localllm.server(cfg)
    if server is None or not server.running():
        return []
    try:
        loaded = [m.name for m in server.loaded()]
        if not loaded:
            return []
        STATE.update(phase="waiting", models=loaded, since=time.time())
        foreign = [n for n in loaded if n not in localllm.USED_BY_RUNNER]
        if foreign and cfg.polite_mode:
            log.info("polite mode: waiting while %s may be in use (at most %s min)", ", ".join(foreign),
                     cfg.polite_wait_min)
            if not wait_until_idle(server, cfg.polite_wait_min * 60, stage, cancelled):
                log.info("polite mode: %s min passed, unloading anyway", cfg.polite_wait_min)
        STATE.update(phase="swapping")
        done = server.unload_all()
        if done:
            log.info("unloaded for the transcription: %s", ", ".join(done))
        return done
    except localllm.LocalLlmError as exc:  # the server went away: the card is free anyway
        log.warning("model swap: %s", exc)
        return []
    finally:
        STATE.update(phase="whisper" if STATE["phase"] != "idle" else "idle")


def after_whisper() -> None:
    STATE.update(phase="idle", models=[], since=0.0)
