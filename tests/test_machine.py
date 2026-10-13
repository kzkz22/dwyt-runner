"""The machine check (decision 18.8): reading the hardware, the judgement, what changed."""
import json
import subprocess
import sys

import pytest

from dwyt_runner import machine
from dwyt_runner.machine import GREEN, RED, YELLOW, Gpu, Machine, ModelServer


def m(gpus=(), ram=32.0, threads=16, free=200.0, servers=()):
    return Machine(system="Windows 11", cpu="AMD Ryzen 7 7700", threads=threads, ram_gb=ram, gpus=list(gpus),
                   disk_free_gb=free, servers=list(servers) or [ModelServer("ollama"), ModelServer("lmstudio")])


def lamps(a):
    return {x.key: (x.level, x.value) for x in a.rows + a.abilities}


def test_nvidia_smi_output_is_read():
    gpus = machine.parse_nvidia_smi("NVIDIA GeForce RTX 5080, 16303, 581.42\nNVIDIA GeForce RTX 3090, 24576, 581.42\n"
                                    "\n, , \n")
    assert [(g.name, g.vram_gb, g.driver, g.backend) for g in gpus] == [
        ("NVIDIA GeForce RTX 5080", 15.9, "581.42", "CUDA"), ("NVIDIA GeForce RTX 3090", 24.0, "581.42", "CUDA")]


def test_linux_cards_from_sys(tmp_path, monkeypatch):
    monkeypatch.setattr(machine.shutil, "which", lambda name: None)  # no lspci
    for card, vendor, vram in (("card0", "0x1002", str(16 * 1024 ** 3)), ("card1", "0x8086", None)):
        dev = tmp_path / card / "device"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text(vendor + "\n")
        if vram:
            (dev / "mem_info_vram_total").write_text(vram)
    (tmp_path / "card0-DP-1").mkdir()  # a connector, not a card
    gpus = machine.linux_gpus(tmp_path)
    assert [(g.vendor, g.name, g.vram_gb, g.backend) for g in gpus] == [
        ("amd", "AMD videókártya", 16.0, "Vulkan"), ("intel", "Intel videóvezérlő", None, "Vulkan")]


def test_a_big_card_recommends_own_machine():
    a = machine.assess(m([Gpu("nvidia", "NVIDIA GeForce RTX 3090", 24.0, "581.42")],
                         servers=[ModelServer("ollama", True, True, "0.12", ["qwen3:8b", "gemma3:12b"])]))
    got = lamps(a)
    assert got["gpu"] == (GREEN, "NVIDIA GeForce RTX 3090 · 24 GB · illesztőprogram 581.42")
    assert got["server"] == (GREEN, "Ollama 0.12 · fut · 2 modell (qwen3:8b, gemma3:12b)")
    assert got["whisper"] == (GREEN, "gyors") and got["llm_small"] == (GREEN, "jó") and "llm_large" not in got
    assert a.mode == "sajat" and "Saját gép" in a.mode_reason


def test_a_mid_card_recommends_mixed_and_says_small_models_are_weaker():
    a = machine.assess(m([Gpu("nvidia", "NVIDIA GeForce RTX 4070", 12.0, "581.42")]))
    got = lamps(a)
    assert got["whisper"][0] == GREEN
    assert got["llm_small"] == (YELLOW, "működik, de gyengébb")
    assert "gyengébbek" in next(x.note for x in a.abilities if x.key == "llm_small")
    assert got["llm_large"] == (RED, "nem fér el")
    assert got["server"] == (YELLOW, "nincs (Ollama és LM Studio nem található)")
    assert a.mode == "vegyes"


def test_no_card_means_slow_or_cloud():
    built_in = Gpu("amd", "AMD Radeon 780M Graphics", 0.5)
    a = machine.assess(m([built_in], ram=31.3, threads=16))
    got = lamps(a)
    assert got["gpu"] == (YELLOW, "nincs külön videókártya (AMD Radeon 780M Graphics)")
    assert got["cpu"][0] == GREEN  # 31,3 GB counts as 32
    assert got["whisper"] == (YELLOW, "lassú")
    assert a.mode == "felho"
    weak = machine.assess(m([], ram=7.8, threads=4, free=3.0))
    got = lamps(weak)
    assert got["cpu"][0] == YELLOW and got["whisper"] == (RED, "nem ajánlott") and got["disk"][0] == RED


def test_other_vendors_and_small_cards_are_explained():
    a = machine.assess(m([Gpu("amd", "AMD Radeon RX 7600", 8.0)]))
    gpu = next(x for x in a.rows if x.key == "gpu")
    assert gpu.level == GREEN and "Vulkan" in gpu.note
    a = machine.assess(m([Gpu("nvidia", "NVIDIA GeForce GTX 1650", 4.0)]))
    assert lamps(a)["whisper"] == (YELLOW, "működik")
    assert lamps(a)["gpu"][0] == YELLOW


def test_installed_but_stopped_server():
    a = machine.assess(m(servers=[ModelServer("ollama"), ModelServer("lmstudio", installed=True)]))
    assert lamps(a)["server"] == (YELLOW, "LM Studio telepítve, de nem fut")


def test_changes_since_the_last_scan():
    old = m([Gpu("nvidia", "NVIDIA GeForce RTX 4070", 12.0)])
    new = m([Gpu("nvidia", "NVIDIA GeForce RTX 5080", 16.0)], ram=64.0)
    assert machine.changes(None, new) == []
    assert machine.changes(old, old) == []
    said = machine.changes(old, new)
    assert said[0].startswith("Új videókártyát találtam: NVIDIA GeForce RTX 5080, 16 GB (eddig: NVIDIA GeForce "
                              "RTX 4070, 12 GB). Futtassunk próbát?")
    assert said[1] == "Változott a memória: 64 GB (eddig 32 GB)."
    assert machine.changes(old, m([]))[0].startswith("Nem találom a videókártyát")


def test_snapshot_round_trip_and_the_quiet_check_at_start(tmp_path, monkeypatch):
    monkeypatch.setenv("DWYT_RUNNER_HOME", str(tmp_path))
    scans = iter([m([Gpu("nvidia", "NVIDIA GeForce RTX 4070", 12.0)]), m([Gpu("nvidia", "NVIDIA GeForce RTX 5080",
                                                                             16.0)])])
    monkeypatch.setattr(machine, "scan", lambda cfg=None: next(scans))
    from dwyt_runner import config
    assert machine.watch_at_start(config.Config()) == []  # first start: nothing to compare with
    back = machine.load_snapshot()
    assert back.best_gpu.name == "NVIDIA GeForce RTX 4070" and back.servers[0].kind == "ollama"
    assert machine.watch_at_start(config.Config())[0].startswith("Új videókártyát találtam: NVIDIA GeForce RTX 5080")
    (tmp_path / "machine.json").write_text("{broken", encoding="utf-8")
    assert machine.load_snapshot() is None


def test_model_servers_are_found(monkeypatch):
    answers = {
        f"{machine.OLLAMA_URL}/api/version": {"version": "0.12.3"},
        f"{machine.OLLAMA_URL}/api/tags": {"models": [{"name": "qwen3:8b"}, {"name": "gemma3:12b"}]},
        f"{machine.LMSTUDIO_URL}/v1/models": {"data": [{"id": "qwen3-8b"}, {"id": "text-embedding-nomic"}]},
    }
    monkeypatch.setattr(machine, "_get_json", lambda url, timeout=1.5: answers.get(url))
    o, lm = machine.ollama(), machine.lmstudio()
    assert (o.running, o.version, o.models) == (True, "0.12.3", ["qwen3:8b", "gemma3:12b"])
    assert (lm.running, lm.models) == (True, ["qwen3-8b"])
    monkeypatch.setattr(machine, "_get_json", lambda url, timeout=1.5: None)
    assert machine.ollama().running is False


def test_report_and_the_command(tmp_path):
    text = machine.report(m(), machine.assess(m()))
    assert "Helyi modellszerver" in text and "[figyelem]" in text and "próbafuttatás" in text
    env = {"DWYT_RUNNER_HOME": str(tmp_path), "PYTHONIOENCODING": "utf-8", "PATH": "", "SYSTEMROOT": "C:\\Windows"}
    out = subprocess.run([sys.executable, "-m", "dwyt_runner.cli", "machine", "--json"], capture_output=True,
                         text=True, encoding="utf-8", env=env, timeout=60, check=True).stdout
    data = json.loads(out)
    assert data["assessment"]["mode"] in ("felho", "vegyes", "sajat") and data["changes"] == []
    assert (tmp_path / "machine.json").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="registry")
def test_windows_registry_cards_do_not_crash():
    for g in machine.windows_gpus():
        assert g.name
