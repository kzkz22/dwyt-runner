"""One job: check it, run it, return the result shape of the protocol."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

from . import captions, cloud, summarize
from .config import Config
from .errors import WorkError
from .llm import Llm

KINDS = ("transcribe", "summarize")


def capabilities(cfg: Config) -> dict[str, Any]:
    modes = ["captions"] + (["cloud"] if cfg.assemblyai_key else [])
    roles = [r for r, model in (("public", cfg.llm_public_model), ("private", cfg.llm_private_model))
             if cfg.llm_url and model]
    kinds = ["transcribe"] + (["summarize"] if roles else [])
    return {"kinds": kinds, "transcribe_modes": modes, "llm_roles": roles,
            "allowed_domains": cfg.allowed_domains, "local_sources": False}


def domain_allowed(url: str, allowed: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(d == "*" or host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def run(job: dict[str, Any], cfg: Config, stage: Callable[[str], None],
        cancelled: Callable[[], bool]) -> dict[str, Any]:
    """Validate the job strictly (the central service is not trusted blindly) and run it."""
    kind, data = job.get("kind"), job.get("input") or {}
    if kind == "transcribe":
        source, options = data.get("source") or {}, data.get("options") or {}
        url = str(source.get("url") or "")
        if source.get("kind") not in ("youtube", "url") or not url.startswith(("https://", "http://")):
            raise WorkError("Ezt a forrást ez a futtató nem dolgozza fel.")
        if not domain_allowed(url, cfg.allowed_domains):
            raise WorkError(f"A(z) {urlsplit(url).hostname} oldal nincs az engedélyezett tartományok között "
                            "(allowed_domains).")
        mode = options.get("mode")
        if mode == "captions":
            if source.get("kind") != "youtube":
                raise WorkError("YouTube-feliratot csak YouTube-videóhoz lehet kérni.")
            return captions.run(url, options, cfg, stage)
        if mode == "cloud":
            return cloud.run(url, options, cfg, stage, cancelled)
        raise WorkError(f"Ez a futtató nem tud ilyen módot: {mode}.")
    if kind == "summarize":
        role = data.get("role")
        model = {"public": cfg.llm_public_model, "private": cfg.llm_private_model}.get(role)
        if not cfg.llm_url or not model:
            raise WorkError(f"Ennek a futtatónak nincs modellje ehhez ({role}).")
        return summarize.run(data, Llm(cfg.llm_url, cfg.llm_key), model, stage)
    raise WorkError(f"Ismeretlen munkafajta: {kind}.")
