"""Chat completions on any OpenAI-compatible server (LiteLLM, OpenRouter, llama-server, Ollama)."""
from __future__ import annotations

import re
import time
from typing import Any
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


CHARS_PER_TOKEN = 2.5  # careful: Hungarian and timestamps take more tokens per character than English prose
PROMPT_TOKENS = 1200   # the instructions around the text


def chars_for(context_tokens: int | None, limit: int) -> int:
    """How many characters of text fit next to the instructions and the answer in a model's context.
    A quarter of the context (at least 1024 tokens) is kept for the answer, thinking included."""
    if not context_tokens:
        return limit
    answer = max(1024, context_tokens // 4)
    return max(2000, min(limit, int((context_tokens - answer - PROMPT_TOKENS) * CHARS_PER_TOKEN)))


def chat_url(base: str) -> str:
    base = base.rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


class Llm:
    def __init__(self, base_url: str, api_key: str, timeout: float = 2400,
                 extra: dict[str, Any] | None = None) -> None:
        self.url = chat_url(base_url)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout
        self.extra = extra or {}  # server-specific request fields, e.g. OpenRouter's "reasoning"

    def chat(self, model: str, system: str, user: str, temperature: float = 0.3) -> Answer:
        return self.complete(model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                             temperature)

    def complete(self, model: str, messages: list[dict], temperature: float | None = 0.3) -> Answer:
        """A whole conversation; temperature None leaves it to the server (the model's preset)."""
        started = time.monotonic()
        body: dict = {"model": model, "messages": messages, **self.extra}
        if temperature is not None:
            body["temperature"] = temperature
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
            detail = _error(resp)
            if resp.status_code in (401, 403) or "No connected db" in detail:
                # LiteLLM answers "No connected db" when the key is not its master key (it would look
                # the key up in a database it does not have).
                raise WorkError(f"Az LLM-szerver nem fogadta el a kulcsot (HTTP {resp.status_code}: {detail[:200]}). "
                                "Ellenőrizd: dwyt-runner config set llm_key ... "
                                "(LiteLLM-nél a LITELLM_MASTER_KEY értéke), majd indítsd újra a futtatót.")
            raise WorkError(f"Az LLM-szerver elutasította a kérést (HTTP {resp.status_code}): {detail}")
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
