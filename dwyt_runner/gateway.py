"""AssemblyAI's LLM Gateway as the language model (plan 19a item 17, decision 19.9): a new user can try dwyt with
one AssemblyAI account (50 $ of free credit) for transcription and summaries, without OpenRouter.

The gateway is OpenAI-compatible (https://llm-gateway.assemblyai.com/v1, EU: llm-gateway.eu.assemblyai.com) and
takes the AssemblyAI key. A free account reaches only the small trial model below (tried 2026-10-15: other models
answer "Your account does not have access to this LLM Gateway model"), streaming only, and few requests a minute.
With the default temperature the small model wrote invented words and Chinese characters into Hungarian text;
at 0.3 it was accurate, so its requests always carry a low temperature.
"""
from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from .config import Config

URL = "https://llm-gateway.assemblyai.com/v1"
EU_URL = "https://llm-gateway.eu.assemblyai.com/v1"
TRIAL_MODEL = "qwen3.5-4b-32k-fast"
TRIAL_CONTEXT = 32768
TRIAL_TEMPERATURE = 0.3
TRIAL_NOTE = "próba-modell: a magyar szöveg gyengébb lehet, a számokat és neveket érdemes ellenőrizni"


def is_gateway(url: str | None) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return host.startswith("llm-gateway.") and host.endswith("assemblyai.com")


def url_for(cfg: Config) -> str:
    """The gateway in the same region as the AssemblyAI transcription (the EU address when that is set)."""
    return EU_URL if ".eu." in (cfg.assemblyai_url or "") else URL


def with_key(cfg: Config) -> Config:
    """On the gateway the AssemblyAI key is the language model's key too (llm_key may still hold an OpenRouter
    key, kept for switching back)."""
    if is_gateway(cfg.llm_url) and cfg.assemblyai_key:
        return replace(cfg, llm_key=cfg.assemblyai_key)
    return cfg


def is_trial(url: str | None, model: str | None) -> bool:
    return is_gateway(url) and (model or "") == TRIAL_MODEL


def context(url: str | None, model: str | None) -> int | None:
    """The model's context in tokens when the runner knows it (the trial model), else None."""
    return TRIAL_CONTEXT if is_trial(url, model) else None


def use(cfg: Config) -> None:
    """Point the language model at the gateway with the trial model (the setup step's "Az AssemblyAI-kulccsal")."""
    cfg.llm_url = url_for(cfg)
    cfg.llm_public_model = TRIAL_MODEL
