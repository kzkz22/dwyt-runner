"""The runner's local settings and secrets: one JSON file, readable only by its owner.

Location: DWYT_RUNNER_HOME if set; otherwise %APPDATA%\\dwyt-runner (Windows),
~/Library/Application Support/dwyt-runner (macOS), $XDG_CONFIG_HOME/dwyt-runner or
~/.config/dwyt-runner (Linux and others). The keys never leave this machine.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

SECRET_KEYS = {"token", "assemblyai_key", "llm_key"}
DEFAULT_DOMAINS = ["youtube.com", "youtu.be"]


def home() -> Path:
    given = os.environ.get("DWYT_RUNNER_HOME")
    if given:
        return Path(given)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "dwyt-runner"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "dwyt-runner"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dwyt-runner"


@dataclass
class Config:
    server: str = ""
    token: str = ""
    runner_id: int | None = None
    name: str = ""
    # cloud transcription (AssemblyAI) with your own key
    assemblyai_key: str = ""
    assemblyai_url: str = "https://api.assemblyai.com"
    cloud_delete_after: bool = True
    # local transcription: the Whisper GPU worker through llama-swap (dwyt-airouter + dwyt-gpu)
    whisper_url: str = ""        # e.g. http://AI-ROUTER:8080/upstream/whisper
    whisper_models: list[str] = field(default_factory=lambda: ["large-v3", "large-v3-turbo"])
    whisper_timeout_min: int = 60
    # summaries: any OpenAI-compatible server (LiteLLM, OpenRouter, llama-server, Ollama ...)
    llm_url: str = ""
    llm_key: str = ""
    llm_public_model: str = ""   # for public sources (YouTube, web pages)
    llm_private_model: str = ""  # for own recordings: point it to a local model
    llm_translate_model: str = ""  # optional: translations of public sources with another model
    # models the user may pick on the central's item page (besides the ones above)
    llm_models: list[str] = field(default_factory=list)          # for public sources
    llm_private_models: list[str] = field(default_factory=list)  # for own recordings: the owner's explicit consent
    # which sites this runner downloads from ("*" = any)
    allowed_domains: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    cookies_file: str = ""
    # own recordings: watched folders (the files never leave this machine) and uploads via the central
    watch_dirs: list[str] = field(default_factory=list)
    watch_mode: str = "local"          # local | cloud
    watch_model: str = ""              # empty: the first of whisper_models (local) or auto (cloud)
    watch_language: str = ""           # empty: detect
    watch_summarize: bool = True
    max_file_mb: int = 4096
    max_duration_min: int = 360
    work_dir: str = ""

    @property
    def paired(self) -> bool:
        return bool(self.server and self.token)


def path() -> Path:
    return home() / "config.json"


def load() -> Config:
    p = path()
    if not p.exists():
        return Config()
    data = json.loads(p.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})


def save(cfg: Config) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def masked(cfg: Config) -> dict:
    out = asdict(cfg)
    for key in SECRET_KEYS:
        if out.get(key):
            out[key] = "***" + str(out[key])[-4:]
    return out


def set_value(cfg: Config, key: str, value: str) -> None:
    kinds = {f.name: f.type for f in fields(Config)}
    if key not in kinds or key in ("token", "runner_id"):
        raise KeyError(key)
    current = getattr(cfg, key)
    if isinstance(current, bool):
        setattr(cfg, key, value.strip().lower() in ("1", "true", "yes", "on", "igen"))
    elif isinstance(current, int) and not isinstance(current, bool):
        setattr(cfg, key, int(value))
    elif isinstance(current, list):
        setattr(cfg, key, [v.strip() for v in value.split(",") if v.strip()])
    else:
        setattr(cfg, key, value.strip())
