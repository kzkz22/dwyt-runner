"""The operating modes (plan 15b, decision 18.4): Felhő, Vegyes, Saját gép, Egyéni. A mode is a preset of
a few settings; which mode the current settings make is read back from them (anything else is Egyéni).
Choosing a mode never removes a key or an address: Saját gép only makes the local model server the
primary one. Cloud transcription is still made only when someone asks for it (no silent switch)."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from . import gateway, localllm
from .config import Config

MODES = {"felho": "Felhő", "vegyes": "Vegyes", "sajat": "Saját gép", "egyeni": "Egyéni"}


@dataclass
class Box:
    title: str
    where: str
    cost: str
    local: bool


def current(cfg: Config) -> str:
    local_whisper = bool(cfg.whisper_builtin or cfg.whisper_url)
    local_llm = localllm.uses_local(cfg)
    if not local_whisper and not local_llm:
        return "felho"
    if local_whisper and not local_llm:
        return "vegyes"
    if local_whisper and local_llm:
        return "sajat"
    return "egyeni"


def apply(cfg: Config, mode: str) -> list[str]:
    """Set the mode's settings on cfg; returns what is still missing for it to work, in sentences."""
    missing: list[str] = []
    if mode == "felho":
        cfg.whisper_builtin, cfg.llm_prefer_local = False, False
    elif mode == "vegyes":
        cfg.whisper_builtin = not cfg.whisper_url
        cfg.llm_prefer_local = False
    elif mode == "sajat":
        cfg.whisper_builtin = not cfg.whisper_url
        cfg.llm_prefer_local = True
        if not cfg.local_llm:
            missing.append("Válaszd ki a helyi modellszervert (Ollama vagy LM Studio).")
        if not (cfg.llm_private_model or cfg.llm_public_model):
            missing.append("Add meg, melyik helyi modell írja az összefoglalókat.")
    if mode in ("felho", "vegyes"):
        if not cfg.llm_url:
            missing.append("Add meg a felhős LLM címét és kulcsát (például OpenRouter) a Kulcsok lapon.")
        elif gateway.is_gateway(cfg.llm_url) and not cfg.assemblyai_key:
            missing.append("Az összefoglalóhoz add meg az AssemblyAI-kulcsot a Kulcsok lapon.")
        elif not gateway.is_gateway(cfg.llm_url) and not cfg.llm_key and "openrouter.ai" in cfg.llm_url:
            missing.append("Add meg az OpenRouter-kulcsot a Kulcsok lapon.")
        elif not cfg.llm_public_model:
            missing.append("Add meg, melyik felhős modell írja az összefoglalókat.")
    if mode == "felho" and not cfg.assemblyai_key:
        missing.append("Felhős átíráshoz add meg az AssemblyAI-kulcsot; nélküle csak a YouTube-feliratok "
                       "használhatók.")
    return missing


def preview(cfg: Config) -> dict[str, list[Box]]:
    """The picture for every mode, as it would look after choosing it (the window switches between them
    before saving); Egyéni shows the settings as they are."""
    from dataclasses import replace
    out = {}
    for mode in MODES:
        trial = replace(cfg)
        if mode != "egyeni":
            apply(trial, mode)
        out[mode] = flow(trial)
    return out


def _provider(url: str) -> str:
    host = urlsplit(url).hostname or url
    if gateway.is_gateway(url):
        return "AssemblyAI"
    return "OpenRouter" if "openrouter.ai" in host else host


def flow(cfg: Config) -> list[Box]:
    """The four boxes of the mode picture: source, transcription, language model, library."""
    if cfg.whisper_url:
        transcribe = Box("Átírás", "a GPU-gépen (Whisper)", "ingyenes", True)
    elif cfg.whisper_builtin:
        transcribe = Box("Átírás", "ezen a gépen (Whisper)", "ingyenes", True)
    elif cfg.assemblyai_key:
        transcribe = Box("Átírás", "felhőben (AssemblyAI), ha kéred", "az AssemblyAI-kreditből"
                         if gateway.is_gateway(cfg.llm_url) else "fizetős, a te kulcsoddal", False)
    else:
        transcribe = Box("Átírás", "YouTube-feliratból", "ingyenes", False)
    if cfg.llm_prefer_local and localllm.server(cfg) is None:
        llm = Box("Összefoglaló, fordítás, kérdések", "ezen a gépen (Ollama vagy LM Studio)", "ingyenes", True)
    elif localllm.uses_local(cfg):
        server = localllm.server(cfg)
        llm = Box("Összefoglaló, fordítás, kérdések", f"ezen a gépen ({server.title})", "ingyenes", True)
    elif gateway.is_trial(cfg.llm_url, cfg.llm_public_model):
        llm = Box("Összefoglaló, fordítás, kérdések", "felhőben (AssemblyAI, próba-modell)",
                  "az AssemblyAI-kreditből", False)
    elif cfg.llm_url:
        llm = Box("Összefoglaló, fordítás, kérdések", f"felhőben ({_provider(cfg.llm_url)})",
                  "fizetős, a te kulcsoddal", False)
    else:
        llm = Box("Összefoglaló, fordítás, kérdések", "nincs beállítva", "", False)
    return [Box("YouTube-videó", "vagy saját felvétel", "", False), transcribe, llm,
            Box("dwyt könyvtár", "bárhonnan olvasható", "", False)]
