"""One job: check it, run it, return the result shape of the protocol."""
from __future__ import annotations

import os
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from . import ask, captions, cloud, local, localllm, media, modelinfo, summarize, translate, watch
from .config import Config
from .errors import WorkError
from .llm import Llm
from .protocol import Central, CentralError

KINDS = ("transcribe", "summarize", "translate", "ask")


def capabilities(cfg: Config) -> dict[str, Any]:
    cfg = localllm.effective(cfg)
    modes = ["captions"] + (["cloud"] if cfg.assemblyai_key else []) + (["local"] if cfg.whisper_url else [])
    roles = [r for r, model in (("public", cfg.llm_public_model), ("private", cfg.llm_private_model))
             if cfg.llm_url and model]
    kinds = ["transcribe"] + (["summarize", "translate", "ask"] if roles else [])
    info, server = modelinfo.cached(cfg) if roles else ({}, "")
    defaults = {"summarize": model_for(cfg, "summarize", "public"), "translate": model_for(cfg, "translate", "public"),
                "ask": model_for(cfg, "ask", "public"), "private": cfg.llm_private_model}
    return {"kinds": kinds, "transcribe_modes": modes, "llm_roles": roles,
            "llm_models": {role: offered_models(cfg, role) for role in roles},
            "llm_model_info": info, "llm_server": server,
            "llm_defaults": {k: v for k, v in defaults.items() if v} if roles else {},
            "allowed_domains": cfg.allowed_domains, "local_sources": False,
            "whisper_models": list(cfg.whisper_models) if cfg.whisper_url else [],
            "source_kinds": ["upload"] + (["local"] if cfg.watch_dirs else [])}


def model_for(cfg: Config, kind: str, role: str | None) -> str:
    """The model of a role; own recordings (private) always use the private model."""
    if role == "private":
        return cfg.llm_private_model
    if role == "public":
        own = {"translate": cfg.llm_translate_model, "ask": cfg.llm_ask_model}.get(kind, "")
        return own or cfg.llm_public_model
    return ""


def offered_models(cfg: Config, role: str) -> list[str]:
    """The models a user may choose for a role: the configured ones first, no duplicates.
    Own recordings (private) only ever get llm_private_model and llm_private_models."""
    if role == "private":
        names = [cfg.llm_private_model, *cfg.llm_private_models]
    else:
        names = [cfg.llm_public_model, cfg.llm_translate_model, cfg.llm_ask_model, *cfg.llm_models]
    out: list[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out[:30]


def domain_allowed(url: str, allowed: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(d == "*" or host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def _own_file(job: dict[str, Any], data: dict[str, Any], cfg: Config, stage: Callable[[str], None],
              cancelled: Callable[[], bool], central: Central | None) -> dict[str, Any]:
    """An own recording: uploaded to the central service, or a file of this runner's watched folder."""
    source, options = data["source"], data.get("options") or {}
    sha, name = str(source.get("sha256") or ""), str(source.get("name") or "felvétel")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise WorkError("Hibás fájlazonosító a munkában.")
    if source["kind"] == "upload":
        media_path = str(source.get("media") or "")
        if central is None or not re.fullmatch(r"/runner/v1/jobs/\d+/media", media_path):
            raise WorkError("Hibás letöltési cím a munkában.")

        def obtain(workdir: str) -> str:
            dest = os.path.join(workdir, "upload.bin")
            stage("download")
            try:
                central.download(media_path, dest, cfg.max_file_mb * 1024 ** 2)
            except CentralError as exc:
                raise WorkError(str(exc), "later") from exc
            return dest
    else:
        def obtain(workdir: str) -> str:
            path = watch.path_for(sha)
            if path is None:
                raise WorkError("A fájl már nincs meg a figyelt mappában (áthelyezték vagy törölték).")
            return path
    fetch = media.from_file(name, sha, obtain)
    mode = options.get("mode")
    if mode == "local":
        return local.run(fetch, options, cfg, stage, cancelled)
    if mode == "cloud":
        return cloud.run(fetch, options, cfg, stage, cancelled)
    raise WorkError(f"Saját felvételhez ez a mód nem kérhető: {mode}.")


def run(job: dict[str, Any], cfg: Config, stage: Callable[[str], None],
        cancelled: Callable[[], bool], central: Central | None = None,
        progress: Callable[[dict[str, Any]], None] = lambda p: None) -> dict[str, Any]:
    """Validate the job strictly (the central service is not trusted blindly) and run it."""
    kind, data = job.get("kind"), job.get("input") or {}
    if kind == "transcribe" and (data.get("source") or {}).get("kind") in ("upload", "local"):
        return _own_file(job, data, cfg, stage, cancelled, central)
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
        if mode == "local":
            return local.run(url, options, cfg, stage, cancelled)
        raise WorkError(f"Ez a futtató nem tud ilyen módot: {mode}.")
    if kind in ("summarize", "translate", "ask"):
        cfg = localllm.effective(cfg)
        role = data.get("role")
        model = model_for(cfg, kind, role)
        if not cfg.llm_url or not model:
            raise WorkError(f"Ennek a futtatónak nincs modellje ehhez ({role}).")
        wanted = data.get("model")
        if wanted:  # chosen on the item page: only a model this runner offers for this role
            if role not in ("public", "private") or wanted not in offered_models(cfg, role):
                raise WorkError(f"Ezt a modellt a futtató nem ajánlja fel ehhez: {str(wanted)[:80]} (llm_models, "
                                "llm_private_models).")
            model = str(wanted)
        localllm.before_llm_job(cfg)
        llm = Llm(cfg.llm_url, cfg.llm_key)
        if kind == "summarize":
            return summarize.run(data, llm, model, stage, progress=progress)
        if kind == "ask":
            return ask.run(data, llm, model, stage, progress=progress)
        return translate.run(data, llm, model, stage, cancelled, progress=progress)
    raise WorkError(f"Ismeretlen munkafajta: {kind}.")
