"""Back to the start (decision 18.15) and where the models live."""
import json
import re
import sys
import threading

from conftest import KEYRING
from fastapi.testclient import TestClient

from dwyt_runner import autostart, cli, config, machine, reset, worker
from dwyt_runner.app.runtime import Runtime
from dwyt_runner.app.server import create


def test_models_live_in_the_local_data_folder_and_move_there_once(tmp_path, monkeypatch):
    home = config.home()
    old = home / "models"
    (old / "whisper.cpp" / "v1" / "bin").mkdir(parents=True)
    (old / "whisper.cpp" / "v1" / "bin" / "whisper-cli.exe").write_bytes(b"x")
    (old / "whisper").mkdir()
    (old / "whisper" / "ggml-large-v3.bin").write_bytes(b"model")
    config.save(config.Config(whisper_cli=str(old / "whisper.cpp" / "v1" / "bin" / "whisper-cli.exe")))
    monkeypatch.setenv("DWYT_RUNNER_DATA", str(tmp_path / "local"))
    target = machine.models_dir()
    assert target == tmp_path / "local" / "models" and not old.exists()
    assert (target / "whisper" / "ggml-large-v3.bin").read_bytes() == b"model"
    assert config.load().whisper_cli == str(target / "whisper.cpp" / "v1" / "bin" / "whisper-cli.exe")
    assert machine.models_dir() == target  # once is enough


def test_data_folder_per_system(monkeypatch, tmp_path):
    monkeypatch.delenv("DWYT_RUNNER_HOME", raising=False)
    monkeypatch.delenv("DWYT_RUNNER_DATA", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")
    assert config.data_home().name == "dwyt-runner" and "Local" in str(config.data_home())
    assert "Roaming" in str(config.home()) and "Roaming" not in str(config.data_home())
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert config.data_home() == tmp_path / "share" / "dwyt-runner"


def fill_home():
    home = config.home()
    config.save(config.Config(server="https://dwyt.example", token="tok-1", llm_key="sk-1", assemblyai_key="aai"))
    for name in ("setup.json", "trial.json", "machine.json", "history.json", "window.json"):
        (home / name).write_text("{}", encoding="utf-8")
    models = machine.models_dir()
    (models / "whisper").mkdir(parents=True)
    (models / "whisper" / "ggml-large-v3.bin").write_bytes(b"model")
    return home, models


def test_reset_deletes_settings_and_keys_and_keeps_the_models(monkeypatch):
    home, models = fill_home()
    assert KEYRING.backend.data[("dwyt-runner", "llm_key")] == "sk-1"
    switched = []
    monkeypatch.setattr(autostart, "enabled", lambda platform=None: True)
    monkeypatch.setattr(autostart, "disable", lambda platform=None: switched.append("off"))
    said = reset.run()
    assert switched == ["off"] and "A kulcsokat töröltem a rendszer kulcstárolójából." in said
    assert not any(k[0] == "dwyt-runner" for k in KEYRING.backend.data)
    assert sorted(p.name for p in home.iterdir()) == ["models", "window.json"]  # the running app's file stays
    assert (models / "whisper" / "ggml-large-v3.bin").exists() and not config.load().paired
    monkeypatch.setattr(autostart, "enabled", lambda platform=None: False)
    said = reset.run(models=True)
    assert any(line.startswith("A letöltött modelleket töröltem") for line in said) and not models.exists()


def test_reset_from_the_window_stops_the_worker_and_opens_the_setup(monkeypatch):
    fill_home()
    monkeypatch.setattr(autostart, "enabled", lambda platform=None: False)
    runtime = Runtime(start_worker=False)
    stopped = threading.Event()
    runtime.thread = threading.Thread(target=lambda: runtime.stop.wait(10) and stopped.set())
    runtime.thread.start()
    api, state = create(runtime, "testserver")
    client = TestClient(api)
    client.get(f"/belepes?t={state.launch_code}")
    page = client.get("/mukodes").text
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert 'action="/alaphelyzet"' in page and "GB" in page
    r = client.post("/alaphelyzet", data={"csrf": token}, follow_redirects=True)
    assert "Az alaphelyzethez pipáld be" in r.text and config.load().paired  # nothing without the tick
    r = client.post("/alaphelyzet", data={"csrf": token, "ertem": "on"}, follow_redirects=True)
    assert stopped.is_set() and not config.load().paired
    assert "Alaphelyzet kész." in r.text and "https://dwyt.example/futtatok" in r.text and "Üdvözöl" in r.text


def test_a_job_claimed_while_stopping_goes_back(cfg):
    stop = threading.Event()
    handed = []

    class Central:
        def claim(self, caps, versions):
            stop.set()  # quit or reset while the long poll was open
            return {"job_id": 9, "kind": "transcribe", "input": {}}

        def fail(self, job_id, error, retry):
            handed.append((job_id, retry))
    worker.loop(cfg, stop, Central())
    assert handed == [(9, "later")]


def test_reset_command(monkeypatch, capsys):
    fill_home()
    monkeypatch.setattr(autostart, "enabled", lambda platform=None: False)
    (config.home() / "window.json").write_text(json.dumps({"port": 9}), encoding="utf-8")  # nobody listens
    monkeypatch.setattr(sys, "argv", ["dwyt-runner", "reset", "--yes"])
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "A beállításokat töröltem." in out and "https://dwyt.example/futtatok" in out
    assert not config.load().paired


def test_reset_never_takes_models_unasked_and_drops_the_settings_first(tmp_path, monkeypatch):
    from dwyt_runner import keystore
    home, _ = fill_home()
    leftover = home / "models"  # e.g. a move that failed: still next to the settings
    monkeypatch.setenv("DWYT_RUNNER_DATA", str(tmp_path / "local"))
    (tmp_path / "local" / "models").mkdir(parents=True)
    monkeypatch.setattr(autostart, "enabled", lambda platform=None: False)
    said = reset.run()
    assert leftover.exists() and (tmp_path / "local" / "models").exists()
    assert sum("megmaradtak" in line for line in said) == 2
    config.save(config.Config(server="https://dwyt.example", token="tok-1", llm_key="sk-1"))

    def broken(key, value):
        raise RuntimeError("key store gone")
    monkeypatch.setattr(keystore, "put", broken)
    try:
        reset.run()
    except RuntimeError:
        pass
    assert not config.path().exists()  # whatever happens after, no settings that expect missing keys
