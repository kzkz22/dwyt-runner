"""Fakes for the runner tests: the central service, yt-dlp (tests/fake_modules), AssemblyAI and an
OpenAI-compatible LLM server. Everything runs on localhost; no network, any OS."""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fake_modules"))  # the fake yt_dlp shadows the real one
sys.path.insert(0, str(HERE.parent))

import yt_dlp  # noqa: E402  (the fake)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, app: FastAPI) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        threading.Thread(target=self._server.run, daemon=True).start()
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True


class FakeCentral:
    def __init__(self) -> None:
        self.reset()
        app = FastAPI()
        fake = self

        def check(request: Request, auth: bool = True):
            if request.headers.get("dwyt-protocol") != "1":
                return JSONResponse({"error": "frissítsd"}, status_code=426)
            if auth and request.headers.get("authorization") != "Bearer tok-1":
                return JSONResponse({"error": "bad token"}, status_code=401)
            return None

        @app.post("/runner/v1/pair/start")
        async def pair_start(request: Request):
            return check(request, auth=False) or {"code": "ABC-234", "poll_id": "poll-123456789", "expires_in": 600}

        @app.post("/runner/v1/pair/poll")
        async def pair_poll(request: Request):
            if fake.approved:
                return {"runner_id": 7, "name": "teszt gép", "token": "tok-1"}
            return JSONResponse({"status": "pending"}, status_code=202)

        @app.post("/runner/v1/claim")
        async def claim(request: Request):
            bad = check(request)
            if bad:
                return bad
            body = await request.json()
            fake.claims.append(body)
            if not fake.queue:
                return Response(status_code=204)
            return fake.queue.pop(0)

        @app.post("/runner/v1/jobs/{job_id}/heartbeat")
        async def heartbeat(job_id: int, request: Request):
            bad = check(request)
            if bad:
                return bad
            body = await request.json()
            fake.beats.append((job_id, body.get("stage")))
            if body.get("progress"):
                fake.progress.append((job_id, body["progress"]))
            if job_id not in fake.known:
                return JSONResponse({"error": "no such job"}, status_code=404)
            return {"lease_s": 900, "cancel": job_id in fake.cancel}

        @app.put("/runner/v1/jobs/{job_id}/result")
        async def result(job_id: int, request: Request):
            bad = check(request)
            if bad:
                return bad
            if fake.result_failures:
                fake.result_failures -= 1
                return JSONResponse({"error": "busy"}, status_code=503)
            fake.results[job_id] = await request.json()
            return {"ok": True}

        @app.get("/runner/v1/jobs/{job_id}/media")
        async def media(job_id: int, request: Request):
            bad = check(request)
            if bad:
                return bad
            data = fake.files.get(job_id)
            if data is None:
                return JSONResponse({"error": "gone"}, status_code=410)
            rng = request.headers.get("range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                return Response(data[start:], status_code=206, media_type="application/octet-stream")
            if fake.cut_once:  # a broken transfer: half the file, then the runner resumes with Range
                fake.cut_once = False
                from fastapi.responses import StreamingResponse

                async def half():
                    yield data[: len(data) // 2]
                    raise RuntimeError("connection lost")
                return StreamingResponse(half(), media_type="application/octet-stream",
                                         headers={"content-length": str(len(data))})
            return Response(data, media_type="application/octet-stream")

        @app.post("/runner/v1/local-items")
        async def local_items(request: Request):
            bad = check(request)
            if bad:
                return bad
            body = await request.json()
            fake.registered.append(body)
            if body["options"].get("mode") not in ("local", "cloud"):
                return JSONResponse({"error": "rossz mód"}, status_code=422)
            job_id = 100 + len(fake.registered)
            fake.add_job(job_id, "transcribe", {"source": {"kind": "local", "sha256": body["sha256"],
                                                           "name": body["name"], "size": body["size"]},
                                                "options": body["options"]})
            return {"item_id": 50, "job_id": job_id, "existing": False}

        @app.post("/runner/v1/jobs/{job_id}/fail")
        async def fail(job_id: int, request: Request):
            bad = check(request)
            if bad:
                return bad
            fake.failures[job_id] = await request.json()
            return {"ok": True}

        self.server = Server(app)
        self.url = self.server.url

    def reset(self) -> None:
        self.queue, self.claims, self.beats, self.progress = [], [], [], []
        self.results, self.failures, self.known, self.cancel = {}, {}, set(), set()
        self.approved, self.result_failures = False, 0
        self.files, self.registered, self.cut_once = {}, [], False

    def add_job(self, job_id: int, kind: str, data: dict) -> None:
        self.known.add(job_id)
        self.queue.append({"job_id": job_id, "kind": kind, "lease_s": 900, "input": data})


class FakeAssembly:
    def __init__(self) -> None:
        self.reset()
        app = FastAPI()
        fake = self

        @app.post("/v2/upload")
        async def upload(request: Request):
            if request.headers.get("authorization") != "aai-key":
                return JSONResponse({"error": "Invalid API key"}, status_code=401)
            fake.uploaded = len(await request.body())
            return {"upload_url": "https://cdn.example/u1"}

        @app.post("/v2/transcript")
        async def create(request: Request):
            fake.requests.append(await request.json())
            return {"id": "t1", "status": "queued"}

        @app.get("/v2/transcript/{tid}")
        async def get(tid: str):
            return {"id": tid, "status": "completed", "language_code": "hu", "audio_duration": 12,
                    "speech_model_used": "universal-2", "utterances": [
                        {"speaker": "A", "words": [{"text": "Szia", "start": 0, "end": 400},
                                                   {"text": "mindenkinek.", "start": 400, "end": 1200}]},
                        {"speaker": "B", "words": [{"text": "Helló!", "start": 1500, "end": 2000}]}]}

        @app.delete("/v2/transcript/{tid}")
        async def delete(tid: str):
            fake.deleted.append(tid)
            return {}

        self.server = Server(app)
        self.url = self.server.url

    def reset(self) -> None:
        self.requests, self.deleted, self.uploaded = [], [], 0


class FakeLlm:
    def __init__(self) -> None:
        self.reset()
        app = FastAPI()
        fake = self

        @app.get("/v1/model/info")
        async def model_info(request: Request):
            fake.info_calls += 1
            if fake.info is None:
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return {"data": fake.info}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            body = await request.json()
            fake.calls.append(body)
            if fake.status != 200:
                return JSONResponse({"error": {"message": fake.message}}, status_code=fake.status)
            system = body["messages"][0]["content"]
            if system.startswith("Fordító vagy"):
                import re as _re
                user = body["messages"][1]["content"]
                rows = _re.findall(r"^(\d+)\| (.*)$", user.split("Fordítandó sorok:\n", 1)[1], _re.M)
                if fake.drop_once and len(rows) > 1:  # leave one line out: the runner must ask again
                    rows, fake.drop_once = rows[:-1], False
                answer = "\n".join(f"{i}| HU {t}" for i, t in rows) + "\nMAGYARÁZOTT: GPU (grafikus processzor)"
                return {"model": body["model"], "choices": [{"message": {"content": answer}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            if system.startswith("Egy videó- és hangfelvétel-könyvtár"):
                answer = f"<think>hmm</think>A kertről szól [00:00]. ({len(body['messages']) - 1} üzenet)"
                return {"model": body["model"], "choices": [{"message": {"content": answer}}],
                        "usage": {"prompt_tokens": 70, "completion_tokens": 9,
                                  "completion_tokens_details": {"reasoning_tokens": 30}}}
            answer = "<think>hmm</think>## Összefoglaló\nKert."
            return {"model": body["model"], "choices": [{"message": {"content": answer}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 5}}

        self.server = Server(app)
        self.url = self.server.url

    def reset(self) -> None:
        self.calls, self.status, self.message, self.drop_once = [], 200, "down", False
        self.info, self.info_calls = None, 0  # /model/info rows (LiteLLM); None = not a LiteLLM


class FakeWhisper:
    """The Whisper worker behind llama-swap: NDJSON progress lines, then a result or an error."""

    def __init__(self) -> None:
        self.reset()
        app = FastAPI()
        fake = self

        @app.post("/upstream/whisper/transcribe")
        async def transcribe(request: Request):
            body = await request.body()
            fake.requests.append({"query": dict(request.query_params), "size": len(body)})
            if fake.status != 200:
                return JSONResponse({"detail": fake.detail}, status_code=fake.status)

            async def lines():
                import asyncio
                import json as _json
                for stage in ("waiting-for-gpu", "transcribe", "diarize"):
                    yield _json.dumps({"stage": stage, "device": "cuda"}) + "\n"
                    await asyncio.sleep(fake.delay)
                if fake.error:
                    yield _json.dumps({"error": fake.error}) + "\n"
                elif not fake.cut:
                    yield _json.dumps({"result": {"language": "hu", "device": "cuda", "diarized": True,
                                                  "warnings": [], "segments": [
                        {"start": 0.5, "end": 2.0, "text": " Jó napot. ", "speaker": "SPEAKER_01"},
                        {"start": 2.2, "end": 4.0, "text": "Köszönöm.", "speaker": "SPEAKER_00"}]}}) + "\n"

            return StreamingResponse(lines(), media_type="application/x-ndjson")

        self.server = Server(app)
        self.url = self.server.url + "/upstream/whisper"

    def reset(self) -> None:
        self.requests, self.status, self.detail, self.error, self.cut, self.delay = [], 200, "", "", False, 0.0


@pytest.fixture(scope="session")
def whisper():
    w = FakeWhisper()
    yield w
    w.server.stop()


@pytest.fixture(scope="session")
def central():
    c = FakeCentral()
    yield c
    c.server.stop()


@pytest.fixture(scope="session")
def assembly():
    a = FakeAssembly()
    yield a
    a.server.stop()


@pytest.fixture(scope="session")
def llm():
    m = FakeLlm()
    yield m
    m.server.stop()


@pytest.fixture(scope="session")
def audio_file(tmp_path_factory) -> str:
    from dwyt_runner import tools
    path = tmp_path_factory.mktemp("audio") / "tone.wav"
    subprocess.run([tools.ffmpeg(), "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(path)], check=True)
    return str(path)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, central, assembly, llm, whisper, audio_file):
    monkeypatch.setenv("DWYT_RUNNER_HOME", str(tmp_path / "home"))
    central.reset()
    whisper.reset()
    assembly.reset()
    llm.reset()
    yt_dlp.reset()
    yt_dlp.REGISTRY["audio"] = audio_file
    from dwyt_runner import modelinfo, worker
    monkeypatch.setattr(worker, "DELIVERY_DELAYS", (0.05, 0.05, 0.05))
    modelinfo.reset()
    yield


@pytest.fixture
def cfg(central, assembly, llm):
    from dwyt_runner.config import Config
    return Config(server=central.url, token="tok-1", runner_id=7, name="teszt",
                  assemblyai_key="aai-key", assemblyai_url=assembly.url,
                  llm_url=llm.url + "/v1", llm_key="k", llm_public_model="osszefoglalo",
                  llm_private_model="osszefoglalo-helyi")
