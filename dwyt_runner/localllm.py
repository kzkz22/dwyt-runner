"""A local model server on this machine: Ollama or LM Studio (plan 15b, decision 18.5).

The runner finds the server, starts it when it is installed but not running (LM Studio: `lms server
start`; Ollama: `ollama serve`), downloads, loads and unloads models through the server's own API, and
uses its OpenAI-compatible endpoint (`/v1`) for summaries, translations and questions:

- Ollama (127.0.0.1:11434): /api/version, /api/tags, /api/ps, /api/pull (streamed), /api/generate with
  keep_alive to load ("30m") or unload (0);
- LM Studio (127.0.0.1:1234): /api/v1/models, /api/v1/models/download (+ /download/status/<job>),
  /api/v1/models/load, /api/v1/models/unload; an API token only if the user switched it on in LM Studio.

Unloading is what lets a Whisper job have the video card (decision 18.7, the next step). Nothing here
ever switches to a cloud model: the local server is what the settings name, or nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from .config import Config
from .errors import WorkError

KINDS = {"ollama": "Ollama", "lmstudio": "LM Studio"}
DEFAULT_URLS = {"ollama": "http://127.0.0.1:11434", "lmstudio": "http://127.0.0.1:1234"}
START_WAIT_S = 60
KEEP_LOADED = "30m"   # Ollama: how long a model stays in memory after a load or an answer


class LocalLlmError(WorkError):
    """A local server problem, in a sentence for the user (retry = later: the server may come back)."""


@dataclass
class LocalModel:
    name: str
    size_gb: float | None = None
    loaded: bool = False
    vram_gb: float | None = None   # Ollama tells how much of a loaded model is on the video card
    expires_at: str = ""           # Ollama: moves on with every request (a sign of use)


def _gb(n: float | int | None) -> float | None:
    return round(n / 1024 ** 3, 1) if n else None


class LocalServer:
    kind = ""

    def __init__(self, url: str = "", token: str = "", timeout: float = 5) -> None:
        self.url = (url or DEFAULT_URLS[self.kind]).rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout = timeout

    @property
    def title(self) -> str:
        return KINDS[self.kind]

    @property
    def openai_url(self) -> str:
        return f"{self.url}/v1"

    # -- HTTP --
    def _req(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        try:
            r = httpx.request(method, self.url + path, json=body, headers=self.headers,
                              timeout=timeout or self.timeout)
        except httpx.HTTPError as exc:
            raise LocalLlmError(f"A(z) {self.title} nem érhető el ({self.url}).", "later") from exc
        if r.status_code in (401, 403):
            raise LocalLlmError(f"A(z) {self.title} nem fogadta el a kérést (HTTP {r.status_code}). Ha bekapcsoltad "
                                "az API-tokent, add meg: dwyt-runner config set lmstudio_token ...")
        if r.status_code >= 400:
            raise LocalLlmError(f"A(z) {self.title} hibát adott (HTTP {r.status_code}): {_detail(r)}",
                                "later" if r.status_code >= 500 else "never")
        try:
            return r.json()
        except ValueError:
            return {}

    def running(self) -> bool:
        try:
            self.version()
            return True
        except LocalLlmError:
            return False

    # -- per server --
    def version(self) -> str:
        raise NotImplementedError

    def models(self) -> list[LocalModel]:
        raise NotImplementedError

    def pull(self, name: str, progress: Callable[[float | None, str], None] | None = None) -> None:
        raise NotImplementedError

    def load(self, name: str) -> None:
        raise NotImplementedError

    def unload(self, name: str) -> None:
        raise NotImplementedError

    def program(self) -> str | None:
        raise NotImplementedError

    def _start(self, program: str) -> None:
        raise NotImplementedError

    # -- shared --
    def loaded(self) -> list[LocalModel]:
        return [m for m in self.models() if m.loaded]

    def unload_all(self) -> list[str]:
        names = [m.name for m in self.loaded()]
        for name in names:
            self.unload(name)
        return names

    def installed(self) -> bool:
        return self.program() is not None

    def ensure_running(self, wait_s: float = START_WAIT_S) -> bool:
        """True when the server answers; starts it first if it is installed. False when it is not installed."""
        if self.running():
            return True
        program = self.program()
        if not program:
            return False
        self._start(program)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.running():
                return True
            time.sleep(1)
        raise LocalLlmError(f"A(z) {self.title} nem indult el {int(wait_s)} másodperc alatt. Indítsd el kézzel, "
                            "és próbáld újra.", "later")


class Ollama(LocalServer):
    kind = "ollama"

    def version(self) -> str:
        return str(self._req("GET", "/api/version").get("version") or "")

    def models(self) -> list[LocalModel]:
        tags = self._req("GET", "/api/tags").get("models") or []
        running = {m.get("name"): m for m in self._req("GET", "/api/ps").get("models") or []}
        out = [LocalModel(m["name"], _gb(m.get("size")), m["name"] in running,
                          _gb(running.get(m["name"], {}).get("size_vram")),
                          str(running.get(m["name"], {}).get("expires_at") or ""))
               for m in tags if isinstance(m, dict) and m.get("name")]
        return out

    def pull(self, name: str, progress: Callable[[float | None, str], None] | None = None) -> None:
        try:
            with httpx.stream("POST", self.url + "/api/pull", json={"model": name, "stream": True},
                              headers=self.headers, timeout=httpx.Timeout(60, read=600)) as r:
                if r.status_code >= 400:
                    r.read()
                    raise LocalLlmError(f"Az Ollama nem tudja letölteni: {name} ({_detail(r)}).")
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise LocalLlmError(f"Az Ollama nem tudja letölteni: {name} ({msg['error']}).")
                    total, done = msg.get("total"), msg.get("completed")
                    if msg.get("status") == "success":
                        if progress:
                            progress(1.0, "success")
                        return
                    if progress:
                        progress(done / total if total and done is not None else None, str(msg.get("status", "")))
        except httpx.HTTPError as exc:
            raise LocalLlmError(f"Megszakadt a letöltés: {name} ({exc}).", "later") from exc
        raise LocalLlmError(f"Az Ollama nem jelezte, hogy kész: {name}.", "later")

    def load(self, name: str) -> None:
        self._req("POST", "/api/generate", {"model": name, "keep_alive": KEEP_LOADED}, timeout=600)

    def unload(self, name: str) -> None:
        self._req("POST", "/api/generate", {"model": name, "keep_alive": 0}, timeout=120)

    def program(self) -> str | None:
        found = shutil.which("ollama")
        if found:
            return found
        if sys.platform == "win32":
            p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            return str(p) if p.exists() else None
        return None

    def _start(self, program: str) -> None:
        _detached([program, "serve"])


class LmStudio(LocalServer):
    kind = "lmstudio"

    def version(self) -> str:
        self._req("GET", "/api/v1/models")  # LM Studio tells no version here; answering is what matters
        return ""

    def models(self) -> list[LocalModel]:
        out = []
        for m in self._req("GET", "/api/v1/models").get("models") or []:
            if not isinstance(m, dict) or m.get("type") not in ("llm", None) or not m.get("key"):
                continue
            out.append(LocalModel(m["key"], _gb(m.get("size_bytes")), bool(m.get("loaded_instances"))))
        return out

    def pull(self, name: str, progress: Callable[[float | None, str], None] | None = None,
             poll_s: float = 2) -> None:
        job = self._req("POST", "/api/v1/models/download", {"model": name}, timeout=60)
        status = job.get("status")
        while status in ("downloading", "paused"):
            total, done = job.get("total_size_bytes"), job.get("downloaded_bytes")
            if progress:
                progress(done / total if total and done is not None else None, status)
            time.sleep(poll_s)
            job = self._req("GET", f"/api/v1/models/download/status/{job.get('job_id')}")
            status = job.get("status")
        if status not in ("completed", "already_downloaded"):
            raise LocalLlmError(f"Az LM Studio nem tudta letölteni: {name} ({status or 'ismeretlen hiba'}).")
        if progress:
            progress(1.0, status)

    def load(self, name: str) -> None:
        self._req("POST", "/api/v1/models/load", {"model": name}, timeout=600)

    def unload(self, name: str) -> None:
        for m in self._req("GET", "/api/v1/models").get("models") or []:
            if isinstance(m, dict) and m.get("key") == name:
                for inst in m.get("loaded_instances") or []:
                    self._req("POST", "/api/v1/models/unload", {"instance_id": inst.get("id")}, timeout=120)

    def program(self) -> str | None:
        found = shutil.which("lms")
        if found:
            return found
        for p in (Path.home() / ".lmstudio" / "bin" / "lms.exe", Path.home() / ".lmstudio" / "bin" / "lms"):
            if p.exists():
                return str(p)
        return None

    def _start(self, program: str) -> None:
        try:
            subprocess.run([program, "server", "start"], capture_output=True, timeout=START_WAIT_S, check=False,
                           **_no_window())
        except (OSError, subprocess.SubprocessError) as exc:
            raise LocalLlmError(f"Az LM Studio szerverét nem sikerült elindítani ({exc}). Nyisd meg az LM Studiót, "
                                "és a Developer lapon kapcsold be a szervert.", "later") from exc


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _detached(cmd: list[str]) -> None:
    """Start a server that outlives this call (and the runner)."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | \
            subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)  # noqa: S603 - a fixed program found on this machine
    except OSError as exc:
        raise LocalLlmError(f"Nem sikerült elindítani: {cmd[0]} ({exc}).", "later") from exc


def _detail(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        return str(err.get("message") if isinstance(err, dict) else err or r.text)[:300]
    except ValueError:
        return r.text[:300]


USED_BY_RUNNER: set[str] = set()  # models this runner's own jobs used: freeing them disturbs nobody


def mark_used(name: str) -> None:
    USED_BY_RUNNER.add(name)


def server(cfg: Config) -> LocalServer | None:
    """The local server the settings name (local_llm = ollama | lmstudio), or None."""
    cls = {"ollama": Ollama, "lmstudio": LmStudio}.get(cfg.local_llm)
    if cls is None:
        return None
    return cls(cfg.local_llm_url, cfg.lmstudio_token if cfg.local_llm == "lmstudio" else "")


def effective(cfg: Config) -> Config:
    """The settings with the local server as the LLM URL when no other URL is given."""
    s = server(cfg)
    if s is None or (cfg.llm_url and not cfg.llm_prefer_local):
        return cfg
    return replace(cfg, llm_url=s.openai_url, llm_key=cfg.lmstudio_token if cfg.local_llm == "lmstudio" else "")


def uses_local(cfg: Config) -> bool:
    s = server(cfg)
    return s is not None and effective(cfg).llm_url.rstrip("/") == s.openai_url


def before_llm_job(cfg: Config) -> None:
    """A job is about to use the local server: start it if needed; a missing server is a clear message."""
    if not uses_local(cfg):
        return
    s = server(cfg)
    if not s.ensure_running():
        raise LocalLlmError(f"A(z) {s.title} nincs telepítve ezen a gépen (local_llm = {cfg.local_llm}).")
