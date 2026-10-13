"""One video card (decision 18.7): the local model server makes room for a built-in transcription."""
import pytest
from test_localllm import FakeOllama

from dwyt_runner import config, gpuswap, localllm


@pytest.fixture(scope="module")
def ollama_server():
    o = FakeOllama()
    yield o
    o.server.stop()


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = 0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.sleeps += 1


@pytest.fixture
def swap(ollama_server, monkeypatch):
    ollama_server.reset()
    ollama_server.loaded = ["gemma3:12b"]
    ollama_server.models.append("gemma3:12b")
    localllm.USED_BY_RUNNER.clear()
    clock = Clock()
    monkeypatch.setattr(gpuswap, "_now", clock.now)
    monkeypatch.setattr(gpuswap, "_sleep", clock.sleep)
    monkeypatch.setattr(gpuswap, "has_card", lambda: True)
    load = {"util": []}
    monkeypatch.setattr(gpuswap, "gpu_util", lambda: load["util"].pop(0) if load["util"] else 0)
    cfg = config.Config(whisper_builtin=True, local_llm="ollama", local_llm_url=ollama_server.url)
    return cfg, clock, load, ollama_server


def test_nothing_to_do_without_a_local_server_card_or_with_the_gpu_host(swap, monkeypatch):
    cfg, clock, _, ollama = swap
    assert gpuswap.before_whisper(config.Config(whisper_builtin=True), lambda s: None) == []
    cfg.whisper_url = "http://router:8080/upstream/whisper"  # llama-swap frees the card itself
    assert gpuswap.before_whisper(cfg, lambda s: None) == []
    cfg.whisper_url = ""
    monkeypatch.setattr(gpuswap, "has_card", lambda: False)  # on the processor nothing to free
    assert gpuswap.before_whisper(cfg, lambda s: None) == []
    assert ollama.loaded == ["gemma3:12b"]


def test_the_runners_own_model_is_unloaded_at_once(swap):
    cfg, clock, _, ollama = swap
    localllm.mark_used("gemma3:12b")
    assert gpuswap.before_whisper(cfg, lambda s: None) == ["gemma3:12b"]
    assert ollama.loaded == [] and clock.sleeps == 0
    assert ("generate", {"model": "gemma3:12b", "keep_alive": 0}) in ollama.calls


def test_polite_mode_waits_while_the_card_is_busy(swap):
    cfg, clock, load, ollama = swap
    load["util"] = [80, 75, 60, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    stages = []
    assert gpuswap.before_whisper(cfg, stages.append) == ["gemma3:12b"]
    # busy at the start and for two more polls (80 is the first look), then a quiet minute
    assert clock.t == pytest.approx((2 + gpuswap.IDLE_S // gpuswap.POLL_S) * gpuswap.POLL_S)
    assert set(stages) == {"waiting-for-gpu"} and ollama.loaded == []


def test_polite_mode_gives_up_after_the_limit(swap):
    cfg, clock, load, ollama = swap
    cfg.polite_wait_min = 2
    load["util"] = [90] * 100
    assert gpuswap.before_whisper(cfg, lambda s: None) == ["gemma3:12b"]
    assert clock.t == pytest.approx(120) and ollama.loaded == []


def test_requests_to_the_model_count_as_use(swap, monkeypatch):
    cfg, clock, load, ollama = swap
    expiries = iter(["t1", "t2", "t3"])
    real = localllm.Ollama.models

    def models(self):  # every request moves Ollama's expiry time on
        out = real(self)
        for m in out:
            if m.loaded:
                m.expires_at = next(expiries, "t3")
        return out
    monkeypatch.setattr(localllm.Ollama, "models", models)
    gpuswap.before_whisper(cfg, lambda s: None)
    # t1: the first look, t2: the start of the wait, t3 after one poll; then a quiet minute
    assert clock.t == pytest.approx((1 + gpuswap.IDLE_S // gpuswap.POLL_S) * gpuswap.POLL_S)


def test_impolite_mode_and_a_model_that_left_by_itself(swap):
    cfg, clock, load, ollama = swap
    cfg.polite_mode = False
    load["util"] = [90] * 10
    assert gpuswap.before_whisper(cfg, lambda s: None) == ["gemma3:12b"] and clock.sleeps == 0
    ollama.loaded = ["gemma3:12b"]
    cfg.polite_mode = True

    def leave(s):
        clock.t += s
        ollama.loaded = []  # Ollama's own idle timeout
    gpuswap._sleep = leave
    try:
        assert gpuswap.before_whisper(cfg, lambda s: None) == []
    finally:
        gpuswap._sleep = clock.sleep


def test_cancel_while_waiting(swap):
    cfg, clock, load, ollama = swap
    load["util"] = [90] * 100
    with pytest.raises(Exception, match="megszakítva"):
        gpuswap.before_whisper(cfg, lambda s: None, cancelled=lambda: clock.t > 25)
    assert ollama.loaded == ["gemma3:12b"]
    gpuswap.after_whisper()
    assert gpuswap.STATE["phase"] == "idle"
