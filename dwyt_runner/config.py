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

SECRET_KEYS = {"token", "assemblyai_key", "llm_key", "lmstudio_token"}
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
    # or local transcription on this machine: whisper.cpp + sherpa-onnx (models downloaded once)
    whisper_builtin: bool = False
    whisper_cli: str = ""          # empty: PATH, or on Windows the official release downloaded by the runner
    whisper_threads: int = 0       # 0: whisper.cpp decides
    diarize_threshold: float = 0.8  # automatic speaker count: larger = fewer speakers
    # one video card for both: before a local transcription the local model server's models are unloaded;
    # polite mode waits (at most polite_wait_min) while someone else is using a model
    polite_mode: bool = True
    polite_wait_min: int = 10
    # summaries: any OpenAI-compatible server (LiteLLM, OpenRouter, llama-server, Ollama ...)
    llm_url: str = ""
    llm_key: str = ""
    # a local model server on this machine (Ollama or LM Studio): used when llm_url is empty; the runner starts it,
    # downloads, loads and unloads its models
    local_llm: str = ""          # ollama | lmstudio
    local_llm_url: str = ""      # empty: the server's usual address on 127.0.0.1
    lmstudio_token: str = ""     # only if the API token is switched on in LM Studio
    llm_prefer_local: bool = False  # "own machine" mode: the local server even when llm_url is set
    llm_public_model: str = ""   # for public sources (YouTube, web pages)
    llm_private_model: str = ""  # for own recordings: point it to a local model
    llm_translate_model: str = ""  # optional: translations of public sources with another model
    llm_ask_model: str = ""      # optional: questions about the library (public sources); a thinking model fits
    # models the user may pick on the central's item page (besides the ones above)
    llm_models: list[str] = field(default_factory=list)          # for public sources
    llm_private_models: list[str] = field(default_factory=list)  # for own recordings: the owner's explicit consent
    # what the model names are for, shown on the central's Runners page when the LLM server does not say it
    # (dwyt-airouter's LiteLLM does): "name=description; name2=description"
    llm_model_notes: dict[str, str] = field(default_factory=dict)
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
    # where the secrets above live: auto = the system key store when there is one, else this file
    secret_store: str = "auto"         # auto | keyring | file

    @property
    def paired(self) -> bool:
        return bool(self.server and self.token)


def path() -> Path:
    return home() / "config.json"


def _keyring_wanted(cfg: Config) -> bool:
    from . import keystore
    if cfg.secret_store == "file":
        return False
    return keystore.usable()


def load() -> Config:
    p = path()
    if not p.exists():
        return Config()
    data = json.loads(p.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Config)}
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    if data.get("secrets") == "keyring":
        from . import keystore
        if keystore.usable():
            for key in SECRET_KEYS:
                setattr(cfg, key, keystore.get(key))
        else:
            import logging
            logging.getLogger("dwyt-runner").error("the secrets are in the system key store, which does not answer")
    elif any(getattr(cfg, k) for k in SECRET_KEYS) and _keyring_wanted(cfg):
        save(cfg)  # keys still in the file: move them to the key store
    return cfg


def save(cfg: Config) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    if _keyring_wanted(cfg):
        from . import keystore
        for key in SECRET_KEYS:
            keystore.put(key, data[key])
            data[key] = ""
        data["secrets"] = "keyring"
    else:
        data["secrets"] = "file"
        if cfg.secret_store == "file":
            from . import keystore
            if keystore.usable():  # moved back to the file: no stale copies in the key store
                for key in SECRET_KEYS:
                    keystore.put(key, "")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
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
    """A setting from text, checked against its description (settings.py); ValueError says why."""
    from . import settings
    setattr(cfg, key, settings.parse(key, value))
