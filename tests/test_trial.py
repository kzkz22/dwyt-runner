"""The test run (decision 18.8): timed sample transcription and summary, plain sentences, a recommendation."""
import pytest
from test_localllm import FakeOllama
from test_whispercpp import builtin  # noqa: F401 - the fake whisper.cpp fixture

from dwyt_runner import cli, config, machine, trial
from dwyt_runner.machine import GREEN, RED, YELLOW


@pytest.fixture(scope="module")
def ollama_server():
    o = FakeOllama()
    yield o
    o.server.stop()


def test_the_sample_is_33_seconds_of_speech(tmp_path):
    wav, seconds = trial.sample_wav(str(tmp_path))
    assert 32 < seconds < 34


def test_lamps_in_plain_words():
    fast = trial.whisper_lamp(17.3, "gpu", "large-v3")
    assert (fast.level, fast.value) == (GREEN, "gyors")
    assert fast.note == "large-v3, a videókártyán · egy 1 órás videó kb. 3 perc alatt kész, beszélőkkel"
    slow = trial.whisper_lamp(0.8, "cpu", "large-v3")
    assert slow.level == RED and "kb. 75 perc" in slow.note and "felhőben" in slow.note
    assert trial.whisper_lamp(1.4, "cpu", "large-v3-turbo").level == YELLOW
    assert "kb. 2,5 óra" in trial.whisper_lamp(0.4, "cpu", "large-v3").note
    small = trial.llm_lamp("qwen3:8b", 45, 5.0)
    assert small.level == YELLOW and small.note == ("qwen3:8b · 45 token/mp · egy összefoglaló kb. 16 mp, a magyar "
                                                    "szöveg gyengébb a felhősnél")
    assert trial.llm_lamp("qwen3.6-35b-a3b", 38, 20.0).level == GREEN
    slow_big = trial.llm_lamp("qwen3.6-35b-a3b-apex", 9, 20.0)  # the RTX 5080 with experts in memory
    assert (slow_big.level, slow_big.value) == (YELLOW, "jó, de lassú")
    assert trial.llm_lamp("x", 2, 5.0).level == RED


def test_a_full_test_run(builtin, ollama_server, monkeypatch, capsys):  # noqa: F811
    cfg, _, _ = builtin
    ollama_server.reset()
    monkeypatch.setattr(machine, "scan", lambda c=None: machine.Machine(
        gpus=[machine.Gpu("nvidia", "NVIDIA GeForce RTX 5080", 16.0)]))
    cfg = config.Config(whisper_cli=cfg.whisper_cli, local_llm="ollama", local_llm_url=ollama_server.url,
                        llm_private_model="qwen3:8b")
    stages = []
    r = trial.run(cfg, "large-v3", stages.append)
    assert r.errors == [] and r.gpu == "NVIDIA GeForce RTX 5080"
    assert [(x.key, x.level) for x in r.lamps] == [("whisper", GREEN), ("llm", YELLOW)]
    assert r.mode == "vegyes" and "Vegyes" in r.mode_reason
    chats = [c for c in ollama_server.calls if c[0] == "chat"]
    assert len(chats) == 2 and "közlekedési tervét" in chats[1][1]["messages"][1]["content"]  # warm-up, then timed
    back = trial.load()
    assert back.mode == "vegyes" and back.lamps[0].value == "gyors"
    # the machine page shows it, and says when the card changed since
    config.save(cfg)
    monkeypatch.setattr(machine, "scan", lambda c=None: machine.Machine(
        gpus=[machine.Gpu("nvidia", "NVIDIA GeForce RTX 5090", 32.0)]))
    cli.show_machine(False)
    out = capsys.readouterr().out
    assert "Utolsó próbafuttatás" in out and "· NVIDIA GeForce RTX 5080 · még" in out
    assert "még a régi kártyával (NVIDIA GeForce RTX 5080)" in out
    assert "Ez csak becslés" in out  # another card: the estimate stands
    monkeypatch.setattr(machine, "scan", lambda c=None: machine.Machine(
        gpus=[machine.Gpu("nvidia", "NVIDIA GeForce RTX 5080", 16.0)]))
    cli.show_machine(False)
    out = capsys.readouterr().out
    assert "A próba alapján a Vegyes" in out and "Ez csak becslés" not in out and "régi kártyával" not in out


def test_slow_machine_and_no_local_model(builtin, monkeypatch):  # noqa: F811
    cfg, _, _ = builtin
    monkeypatch.setattr(trial, "measure_whisper", lambda c, m, s: (0.5, "cpu"))
    r = trial.run(config.Config(whisper_cli=cfg.whisper_cli), "large-v3")
    assert [(x.key, x.level, x.value) for x in r.lamps] == [("whisper", RED, "nagyon lassú"),
                                                            ("llm", YELLOW, "nincs beállítva")]
    assert r.mode == "felho"
    monkeypatch.setattr(trial, "measure_whisper", lambda c, m, s: (_ for _ in ()).throw(
        trial.WorkError("A whisper.cpp nem indult el")))
    r = trial.run(config.Config(), "large-v3")
    assert r.lamps[0].value == "nem sikerült" and r.errors == ["Helyi átírás: A whisper.cpp nem indult el"]
    assert "Próbafuttatás" in trial.report(r)


def test_model_load_is_not_part_of_the_speed(builtin, monkeypatch):  # noqa: F811
    cfg, _, _ = builtin
    clock = iter([100.0, 104.0])  # 4 s for the run, 1.5 s of it loading the model (the fake whisper.cpp says so)

    class Clock:
        monotonic = staticmethod(lambda: next(clock))
    monkeypatch.setattr(trial, "time", Clock)
    speed, device = trial.measure_whisper(cfg, "large-v3", lambda s: None)
    assert speed == pytest.approx(33.0 / 2.5, rel=0.05) and device == "gpu"
