"""Client side of the central–runner protocol (dwyt-docs/docs/protokoll.md)."""
from __future__ import annotations

from typing import Any

import httpx

from . import __version__

PROTOCOL = 1


class CentralError(Exception):
    """The central service could not be reached or answered unexpectedly: try again later."""


class Unauthorized(Exception):
    """The token is invalid or revoked: pair again."""


class Outdated(Exception):
    """The central service does not speak this protocol version: update the runner."""


class JobGone(Exception):
    """The job is no longer ours (cancelled, deleted or reassigned)."""


class Central:
    def __init__(self, server: str, token: str = "", transport: httpx.BaseTransport | None = None,
                 timeout: float = 60) -> None:
        self.server = server.rstrip("/")
        self.token = token
        headers = {"Dwyt-Protocol": str(PROTOCOL), "User-Agent": f"dwyt-runner/{__version__}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=self.server, headers=headers, timeout=timeout, transport=transport)

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            resp = self.http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise CentralError(f"A központ nem érhető el ({self.server}): {exc}") from exc
        if resp.status_code == 401:
            raise Unauthorized(_message(resp))
        if resp.status_code == 426:
            raise Outdated(_message(resp))
        if resp.status_code in (404, 409, 410) and "/jobs/" in path:
            raise JobGone(_message(resp))
        if resp.status_code >= 500 or resp.status_code in (408, 429):
            raise CentralError(f"A központ hibát adott (HTTP {resp.status_code}).")
        return resp

    # pairing
    def pair_start(self) -> dict[str, Any]:
        resp = self._call("POST", "/runner/v1/pair/start")
        if resp.status_code != 200:
            raise CentralError(f"A párosítás nem indult el (HTTP {resp.status_code}): {_message(resp)}")
        return resp.json()

    def pair_poll(self, poll_id: str) -> dict[str, Any] | None:
        resp = self._call("POST", "/runner/v1/pair/poll", json={"poll_id": poll_id})
        if resp.status_code == 202:
            return None
        if resp.status_code != 200:
            raise CentralError(_message(resp))
        return resp.json()

    # jobs
    def claim(self, capabilities: dict[str, Any], tool_versions: dict[str, str]) -> dict[str, Any] | None:
        resp = self._call("POST", "/runner/v1/claim", timeout=90, json={
            "capabilities": capabilities, "runner_version": __version__, "tool_versions": tool_versions})
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:
            raise CentralError(f"Munkakérés: HTTP {resp.status_code}: {_message(resp)}")
        return resp.json()

    def heartbeat(self, job_id: int, stage: str | None = None, progress: dict | None = None) -> dict[str, Any]:
        resp = self._call("POST", f"/runner/v1/jobs/{job_id}/heartbeat", json={"stage": stage, "progress": progress})
        if resp.status_code != 200:
            raise CentralError(_message(resp))
        return resp.json()

    def result(self, job_id: int, result: dict[str, Any]) -> dict[str, Any]:
        resp = self._call("PUT", f"/runner/v1/jobs/{job_id}/result", json=result, timeout=300)
        if resp.status_code != 200:
            raise CentralError(f"Az eredményt a központ nem fogadta el (HTTP {resp.status_code}): {_message(resp)}")
        return resp.json()

    def fail(self, job_id: int, error: str, retry: str) -> None:
        resp = self._call("POST", f"/runner/v1/jobs/{job_id}/fail", json={"error": error[:4000], "retry": retry})
        if resp.status_code != 200:
            raise CentralError(_message(resp))


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error") or resp.text)[:500]
    except ValueError:
        return resp.text[:500]
