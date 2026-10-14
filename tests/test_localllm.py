"""The local model server (decision 18.5): Ollama and LM Studio, faked with their documented APIs."""
import json

import pytest
from conftest import Server
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from dwyt_runner import cli, config, localllm, modelinfo, work
from dwyt_runner.errors import WorkError
from dwyt_runner.llm import Llm


class FakeOllama:
    def __init__(self) -> None:
        self.reset()
        app = FastAPI()
        fake = self

        @app.get("/api/version")
        def version():
            return {"version": "0.12.3"} if fake.up else JSONResponse({}, status_code=503)

        @app.get("/api/tags")
        def tags():
            return {"models": [{"name": n, "size": 5 * 1024 ** 3} for n in fake.models]}

        @app.get("/api/ps")
        def ps():
            return {"models": [{"name": n, "size_vram": 6 * 1024 ** 3, "context_length": fake.ctx}
                               for n in fake.loaded]}

        @app.post("/api/pull")
        async def pull(request: Request):
            body = await request.json()
            fake.calls.append(("pull", body))

            def lines():
                yield json.dumps({"status": "pulling manifest"}) + "\n"
                if body["model"] == "nincs-ilyen":
                    yield json.dumps({"error": "pull model manifest: file does not exist"}) + "\n"
                    return
                for done in (0, 50, 100):
                    yield json.dumps({"status": "downloading", "digest": "sha256:1", "total": 100,
                                      "completed": done}) + "\n"
                fake.models.append(body["model"])
                yield json.dumps({"status": "success"}) + "\n"
            return StreamingResponse(lines(), media_type="application/x-ndjson")

        @app.post("/api/generate")
        async def generate(request: Request):
            body = await request.json()
            fake.calls.append(("generate", body))
            if body.get("keep_alive") == 0:
                fake.loaded = [n for n in fake.loaded if n != body["model"]]
            elif body["model"] not in fake.loaded:
                fake.loaded.append(body["model"])
            return {"model": body["model"], "done": True}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            body = await request.json()
            fake.calls.append(("chat", body))
            return {"model": body["model"], "choices": [{"message": {"content": "Helyi válasz."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3}}

        self.server = Server(app)
        self.url = self.server.url

    def reset(self) -> None:
        self.up, self.models, self.loaded, self.calls, self.ctx = True, ["qwen3:8b"], [], [], 4096


class FakeLmStudio:
    def __init__(self) -> None:
        self.reset()
        app = FastAPI()
        fake = self

        def denied(request: Request):
            if fake.token and request.headers.get("authorization") != f"Bearer {fake.token}":
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return None

        @app.get("/api/v1/models")
        def models(request: Request):
            if denied(request):
                return denied(request)
            return {"models": [
                {"type": "llm", "key": k, "size_bytes": 9 * 1024 ** 3, "max_context_length": 40960,
                 "loaded_instances": [{"id": k + ":1", "config": {"context_length": fake.ctx.get(k, 4096)}}]
                 if k in fake.loaded else []} for k in fake.models] + [
                {"type": "embedding", "key": "text-embedding-nomic", "size_bytes": 1, "loaded_instances": []}]}

        @app.post("/api/v1/models/download")
        async def download(request: Request):
            body = await request.json()
            fake.calls.append(("download", body))
            if body["model"] in fake.models:
                return {"status": "already_downloaded"}
            fake.polls = 0
            return {"job_id": "job_1", "status": "downloading", "total_size_bytes": 200, "downloaded_bytes": 0}

        @app.get("/api/v1/models/download/status/{job_id}")
        def status(job_id: str):
            fake.polls += 1
            if fake.fail:
                return {"job_id": job_id, "status": "failed"}
            if fake.polls < 2:
                return {"job_id": job_id, "status": "downloading", "total_size_bytes": 200, "downloaded_bytes": 100}
            fake.models.append("google/gemma-3-12b")
            return {"job_id": job_id, "status": "completed", "total_size_bytes": 200, "downloaded_bytes": 200}

        @app.post("/api/v1/models/load")
        async def load(request: Request):
            body = await request.json()
            fake.calls.append(("load", body))
            if body.get("context_length", 4096) > fake.memory:
                return JSONResponse({"error": "Failed to load model: insufficient memory"}, status_code=500)
            fake.loaded.append(body["model"])
            fake.ctx[body["model"]] = body.get("context_length", 4096)
            return {"type": "llm", "instance_id": body["model"], "status": "loaded"}

        @app.post("/api/v1/models/unload")
        async def unload(request: Request):
            body = await request.json()
            fake.calls.append(("unload", body))
            fake.loaded = [k for k in fake.loaded if k + ":1" != body["instance_id"]]
            return {"instance_id": body["instance_id"]}

        self.server = Server(app)
        self.url = self.server.url

    def reset(self) -> None:
        self.models, self.loaded, self.calls, self.token, self.fail, self.polls = ["qwen/qwen3-8b"], [], [], "", \
            False, 0
        self.ctx, self.memory = {}, 10 ** 9


@pytest.fixture(scope="module")
def ollama():
    o = FakeOllama()
    yield o
    o.server.stop()


@pytest.fixture(scope="module")
def lmstudio():
    s = FakeLmStudio()
    yield s
    s.server.stop()


@pytest.fixture(autouse=True)
def fresh(ollama, lmstudio):
    ollama.reset()
    lmstudio.reset()
    modelinfo.reset()


def test_ollama_models_pull_load_unload(ollama):
    s = localllm.Ollama(ollama.url)
    assert s.running() and s.version() == "0.12.3"
    seen = []
    s.pull("gemma3:12b", lambda frac, status: seen.append(frac))
    assert seen[-1] == 1.0 and 0.5 in seen and ("pull", {"model": "gemma3:12b", "stream": True}) in ollama.calls
    with pytest.raises(localllm.LocalLlmError, match="nem tudja letölteni: nincs-ilyen"):
        s.pull("nincs-ilyen")
    s.load("gemma3:12b")
    assert ollama.calls[-1] == ("generate", {"model": "gemma3:12b", "keep_alive": "30m"})
    got = {m.name: (m.size_gb, m.loaded, m.vram_gb) for m in s.models()}
    assert got == {"qwen3:8b": (5.0, False, None), "gemma3:12b": (5.0, True, 6.0)}
    assert s.unload_all() == ["gemma3:12b"]
    assert ollama.calls[-1] == ("generate", {"model": "gemma3:12b", "keep_alive": 0}) and s.loaded() == []


def test_lmstudio_download_load_unload_and_token(lmstudio):
    s = localllm.LmStudio(lmstudio.url)
    assert [m.name for m in s.models()] == ["qwen/qwen3-8b"]  # embedding models are not offered
    seen = []
    s.pull("google/gemma-3-12b", lambda frac, status: seen.append((frac, status)), poll_s=0.01)
    assert seen[0] == (0.0, "downloading") and seen[-1] == (1.0, "completed")
    s.pull("qwen/qwen3-8b")  # already there
    lmstudio.fail = True
    with pytest.raises(localllm.LocalLlmError, match="nem tudta letölteni"):
        localllm.LmStudio(lmstudio.url).pull("other/model", poll_s=0.01)
    s.load("qwen/qwen3-8b")
    assert [m.name for m in s.loaded()] == ["qwen/qwen3-8b"]
    s.unload("qwen/qwen3-8b")
    assert lmstudio.calls[-1] == ("unload", {"instance_id": "qwen/qwen3-8b:1"}) and s.loaded() == []
    lmstudio.token = "lm-secret"
    with pytest.raises(localllm.LocalLlmError, match="lmstudio_token"):
        s.models()
    assert localllm.LmStudio(lmstudio.url, "lm-secret").models()


def test_starting_a_stopped_server(ollama, monkeypatch):
    s = localllm.Ollama(ollama.url)
    ollama.up = False
    monkeypatch.setattr(localllm.Ollama, "program", lambda self: None)
    assert s.ensure_running(wait_s=1) is False  # not installed
    started = []

    def start(self, program):
        started.append(program)
        ollama.up = True
    monkeypatch.setattr(localllm.Ollama, "program", lambda self: "/opt/ollama/ollama")
    monkeypatch.setattr(localllm.Ollama, "_start", start)
    assert s.ensure_running(wait_s=5) is True and started == ["/opt/ollama/ollama"]
    ollama.up = False
    monkeypatch.setattr(localllm.Ollama, "_start", lambda self, program: None)
    with pytest.raises(localllm.LocalLlmError, match="nem indult el 1 másodperc alatt"):
        s.ensure_running(wait_s=1)


def test_unreachable_server_is_a_later_retry():
    s = localllm.Ollama("http://127.0.0.1:9")
    with pytest.raises(localllm.LocalLlmError) as exc:
        s.models()
    assert exc.value.retry == "later" and isinstance(exc.value, WorkError)


def test_local_server_serves_the_summaries(ollama, monkeypatch):
    cfg = config.Config(local_llm="ollama", local_llm_url=ollama.url, llm_public_model="qwen3:8b",
                        llm_private_model="qwen3:8b")
    eff = localllm.effective(cfg)
    assert eff.llm_url == ollama.url + "/v1" and localllm.uses_local(cfg)
    assert localllm.effective(config.Config(local_llm="ollama", llm_url="https://openrouter.ai/api/v1")).llm_url == \
        "https://openrouter.ai/api/v1"  # an explicit URL wins
    caps = work.capabilities(cfg)
    assert caps["llm_roles"] == ["public", "private"] and caps["llm_server"] == "Ollama"
    assert caps["llm_model_info"]["qwen3:8b"] == {"where": "local", "target_label": "qwen3:8b", "provider": "Ollama"}
    localllm.before_llm_job(eff)
    answer = Llm(eff.llm_url, eff.llm_key).chat("qwen3:8b", "rendszer", "kérdés")
    assert answer.text == "Helyi válasz." and ollama.calls[-1][0] == "chat"
    ollama.up = False
    monkeypatch.setattr(localllm.Ollama, "program", lambda self: None)
    with pytest.raises(WorkError, match="nincs telepítve"):
        localllm.before_llm_job(eff)


def test_the_llm_command(ollama, capsys):
    cfg = config.Config(local_llm="ollama", local_llm_url=ollama.url)
    config.save(cfg)
    assert cli.local_llm("pull", "gemma3:12b") == 0
    assert cli.local_llm("load", "gemma3:12b") == 0
    assert cli.local_llm("status", None) == 0
    out = capsys.readouterr().out
    assert "100%" in out and "Betöltve: gemma3:12b" in out
    assert "Ollama 0.12.3: fut" in out and "gemma3:12b  5,0 GB  betöltve, 6,0 GB a videókártyán" in out
    assert cli.local_llm("unload", None) == 0
    assert "Kiürítve: gemma3:12b" in capsys.readouterr().out
    config.save(config.Config())
    assert cli.local_llm("status", None) == 1
    assert "config set local_llm" in capsys.readouterr().out
    assert config.masked(config.Config(lmstudio_token="abcdefgh"))["lmstudio_token"] == "***efgh"


def test_lmstudio_gets_the_model_loaded_with_enough_context(lmstudio):
    s = localllm.LmStudio(lmstudio.url)
    lmstudio.loaded, lmstudio.ctx = ["qwen/qwen3-8b"], {"qwen/qwen3-8b": 4096}  # loaded on first use, 4096
    assert s.ensure_context("qwen/qwen3-8b", 32768) == 32768
    assert ("unload", {"instance_id": "qwen/qwen3-8b:1"}) in lmstudio.calls
    assert ("load", {"model": "qwen/qwen3-8b", "context_length": 32768}) in lmstudio.calls
    lmstudio.calls.clear()
    assert s.ensure_context("qwen/qwen3-8b", 32768) == 32768 and lmstudio.calls == []  # already enough
    assert s.ensure_context("qwen/qwen3-8b", 131072) == 40960  # never more than the model's own maximum
    lmstudio.reset()
    lmstudio.memory = 10000  # the card holds only a smaller context: halve down
    assert s.ensure_context("qwen/qwen3-8b", 32768) == 8192
    lmstudio.memory = 100
    with pytest.raises(WorkError, match="legalább 8192 token"):
        s.ensure_context("qwen/qwen3-8b", 32768)
    lmstudio.reset()


def test_ollama_tells_the_context_it_runs_with(ollama):
    s = localllm.Ollama(ollama.url)
    ollama.ctx = 8192
    assert s.ensure_context("qwen3:8b", 32768) == 8192 and "qwen3:8b" in ollama.loaded
    ollama.reset()


def test_a_long_summary_is_cut_to_the_local_context():
    from dwyt_runner import summarize
    from dwyt_runner.llm import Answer, chars_for

    class Model:
        def __init__(self):
            self.sizes = []

        def chat(self, model, system, user, temperature=0.3):
            self.sizes.append(len(user))
            return Answer(text="- [00:01] jegyzet " + "x" * 300, model=model, prompt_tokens=1, completion_tokens=1,
                          seconds=0.1)
    segs = [{"start": i * 5.0, "end": i * 5.0 + 5, "text": f"Ez a {i}. mondat, elég hosszú ahhoz, hogy számítson.",
             "speaker": None} for i in range(800)]
    m = Model()
    out = summarize.run({"transcript": {"segments": segs, "language": "hu"}, "meta": {}}, m, "qwen/qwen3-8b",
                        context_tokens=4096)
    limit = chars_for(4096, summarize.MAX_CHARS)
    assert out["chunked"] and limit < 6000
    assert all(size < limit + 3000 for size in m.sizes)  # every request fits (the part plus the instructions)
    assert chars_for(None, 120000) == 120000 and chars_for(262144, 120000) == 120000


def test_a_question_background_is_cut_to_the_local_context():
    from dwyt_runner import ask
    from dwyt_runner.llm import Answer

    class Model:
        def complete(self, model, messages, temperature=None):
            self.system = messages[0]["content"]
            return Answer(text="Válasz.", model=model, prompt_tokens=1, completion_tokens=1, seconds=0.1)
    long = [{"start": i, "text": "szó " * 40} for i in range(2000)]  # ~330 000 characters
    job = {"scope": "item", "sources": [{"title": "Videó", "transcript": long}],
           "messages": [{"role": "user", "content": "Miről szól?"}]}
    m = Model()
    ask.run(job, m, "qwen/qwen3-8b", context_tokens=8192)
    assert len(m.system) < 20000 and ask.CUT_NOTE.strip() in m.system
    ask.run(job, m, "cloud", context_tokens=None)
    assert ask.CUT_NOTE.strip() not in m.system


def test_an_ollama_the_runner_starts_gets_the_context_setting(monkeypatch, ollama):
    seen = {}
    monkeypatch.setattr(localllm.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, env=kw.get("env")))
    cfg = config.Config(local_llm="ollama", local_llm_url=ollama.url, local_llm_context=16384)
    localllm.before_llm_job(cfg)  # sets what a start would use
    localllm.Ollama(ollama.url)._start("/opt/ollama/ollama")
    assert seen["cmd"] == ["/opt/ollama/ollama", "serve"] and seen["env"]["OLLAMA_CONTEXT_LENGTH"] == "16384"


def test_a_failed_start_says_what_the_server_said(monkeypatch, ollama):
    s = localllm.Ollama(ollama.url)
    ollama.up = False
    monkeypatch.setattr(localllm.Ollama, "program", lambda self: "/opt/ollama/ollama")

    def popen(cmd, **kw):
        kw["stdout"].write("Error: listen tcp 127.0.0.1:11434: bind: address already in use\n")
    monkeypatch.setattr(localllm.subprocess, "Popen", popen)
    with pytest.raises(localllm.LocalLlmError, match="address already in use"):
        s.ensure_running(wait_s=1)
    ollama.up = True
