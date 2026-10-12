"""What runs behind the runner's model names, for the central's Runners page and model pickers.

With dwyt-airouter's LiteLLM the answer comes from its /model/info: each name carries a `dwyt` block
(written by `dwyt-airouter generate`) saying what the name is for, which model runs behind it,
locally or in the cloud, with thinking or without, and what happens when the GPU host is off.
Only those fields are passed on; keys and addresses never are. Other servers give no such
details; the owner's own notes (llm_model_notes) fill in the description.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Config

REFRESH_S = 3600
_cache: dict[str, Any] = {"at": 0.0, "key": None, "value": ({}, "")}


def _text(value: Any, limit: int) -> str | None:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) and value.strip() else None


def _clean(block: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, limit in (("description", 200), ("target", 160), ("target_label", 160), ("provider", 40)):
        if _text(block.get(key), limit):
            out[key] = _text(block.get(key), limit)
    if block.get("where") in ("local", "cloud"):
        out["where"] = block["where"]
    for key in ("context", "thinking_budget"):
        if isinstance(block.get(key), int) and not isinstance(block.get(key), bool) and block[key] > 0:
            out[key] = block[key]
    for key in ("vision", "thinking"):
        if isinstance(block.get(key), bool):
            out[key] = block[key]
    return out


def _sanitize(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    out = _clean(block)
    fallback = block.get("fallback")
    if isinstance(fallback, list):
        out["fallback"] = [{**_clean(f), **({"name": _text(f.get("name"), 100)} if _text(f.get("name"), 100) else {})}
                           for f in fallback[:3] if isinstance(f, dict)]
    return out


def names(cfg: Config) -> list[str]:
    out: list[str] = []
    for name in [cfg.llm_public_model, cfg.llm_translate_model, cfg.llm_ask_model, cfg.llm_private_model,
                 *cfg.llm_models, *cfg.llm_private_models]:
        if name and name not in out:
            out.append(name)
    return out[:40]


def _root(url: str) -> str:
    url = url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def fetch(cfg: Config, timeout: float = 5.0) -> tuple[dict[str, dict[str, Any]], str]:
    """(info per name, server label). Never raises: without details the names are simply undescribed."""
    wanted = names(cfg)
    info: dict[str, dict[str, Any]] = {}
    server = ""
    if cfg.llm_url and wanted:
        headers = {"Authorization": f"Bearer {cfg.llm_key}"} if cfg.llm_key else {}
        for path in ("/v1/model/info", "/model/info"):
            try:
                resp = httpx.get(_root(cfg.llm_url) + path, headers=headers, timeout=timeout)
            except httpx.HTTPError:
                break
            if resp.status_code != 200:
                continue
            try:
                rows = resp.json().get("data") or []
            except ValueError:
                break
            server = "LiteLLM"
            for row in rows if isinstance(rows, list) else []:
                name = row.get("model_name") if isinstance(row, dict) else None
                if name in wanted and name not in info:
                    block = _sanitize((row.get("model_info") or {}).get("dwyt"))
                    if block:
                        info[name] = block
            break
        if not server:
            server = "OpenRouter" if "openrouter.ai" in cfg.llm_url else "OpenAI-kompatibilis szerver"
    if server == "OpenRouter":  # everything there runs in the cloud
        for name in wanted:
            info.setdefault(name, {"where": "cloud", "target_label": name, "provider": "OpenRouter"})
    for name, note in (cfg.llm_model_notes or {}).items():
        if name in wanted and _text(note, 200):
            info.setdefault(name, {})["description"] = _text(note, 200)
    return info, server


def cached(cfg: Config) -> tuple[dict[str, dict[str, Any]], str]:
    """fetch(), at most once an hour (or when the model settings change)."""
    key = (cfg.llm_url, tuple(names(cfg)), tuple(sorted((cfg.llm_model_notes or {}).items())))
    if _cache["key"] != key or time.monotonic() - _cache["at"] >= REFRESH_S:
        _cache.update(at=time.monotonic(), key=key, value=fetch(cfg))
    return _cache["value"]


def reset() -> None:
    _cache.update(at=0.0, key=None, value=({}, ""))
