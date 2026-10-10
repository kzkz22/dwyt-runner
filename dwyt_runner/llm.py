"""Chat completions on any OpenAI-compatible server (LiteLLM, OpenRouter, llama-server, Ollama)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import httpx

from .errors import WorkError

_THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class Answer:
    text: str
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    seconds: float
    reasoning_tokens: int | None = None


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    models: list = field(default_factory=list)

    def add(self, answer: Answer) -> Answer:
        self.prompt_tokens += answer.prompt_tokens or 0
        self.completion_tokens += answer.completion_tokens or 0
        self.reasoning_tokens += answer.reasoning_tokens or 0
        self.seconds += answer.seconds
        if answer.model and answer.model not in self.models:
            self.models.append(answer.model)
        return answer

    def facts(self) -> dict:
        return {"model_used": ", ".join(self.models) or None, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "reasoning_tokens": self.reasoning_tokens or None,
                "duration_s": round(self.seconds, 1)}


def chat_url(base: str) -> str:
    base = base.rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


class Llm:
    def __init__(self, base_url: str, api_key: str, timeout: float = 2400) -> None:
        self.url = chat_url(base_url)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> Answer:
        started = time.monotonic()
        body = {"model": model, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            resp = httpx.post(self.url, json=body, headers=self.headers,
                              timeout=httpx.Timeout(self.timeout, connect=10))
        except httpx.TimeoutException as exc:
            raise WorkError(f"A(z) {model} modell nem végzett időben; később újrapróbálom.", "later") from exc
        except httpx.HTTPError as exc:
            raise WorkError(f"Az LLM-szerver nem érhető el ({exc}); később újrapróbálom.", "later") from exc
        if resp.status_code in (408, 429) or resp.status_code >= 500:
            raise WorkError(f"A(z) {model} modell most nem érhető el (HTTP {resp.status_code}).", "later")
        if resp.status_code >= 400:
            raise WorkError(f"Az LLM-szerver elutasította a kérést (HTTP {resp.status_code}): {_error(resp)}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError) as exc:
            raise WorkError("Az LLM-szerver válasza nem értelmezhető.", "later") from exc
        text = _THINK.sub("", text).strip()
        if not text:
            raise WorkError("A modell üres választ adott.", "later")
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return Answer(text=text, model=data.get("model"), prompt_tokens=usage.get("prompt_tokens"),
                      completion_tokens=usage.get("completion_tokens"), seconds=time.monotonic() - started,
                      reasoning_tokens=details.get("reasoning_tokens"))


def _error(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
        return str(err.get("message") if isinstance(err, dict) else err or resp.text)[:400]
    except ValueError:
        return resp.text[:400]
