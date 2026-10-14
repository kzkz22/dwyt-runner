"""A fresh yt-dlp (decision 18.12)."""
import re
import sys
import threading
import time

from dwyt_runner import errors, worker, ytdlp_update


def test_versions_compare_as_dates():
    assert ytdlp_update.newer("2026.10.9", "2026.9.30") and not ytdlp_update.newer("2026.9.30", "2026.10.9")
    assert ytdlp_update.newer("2026.10.9.232140", "2026.10.9") and not ytdlp_update.newer("", "2026.1.1")


def test_check_installs_the_newer_one_and_forgets_the_old_module(monkeypatch):
    calls = []
    versions = iter(["2026.9.30", "2026.10.9"])
    monkeypatch.setattr(ytdlp_update, "installed", lambda: next(versions))
    monkeypatch.setattr(ytdlp_update, "latest", lambda timeout=15: "2026.10.9")
    monkeypatch.setattr(ytdlp_update.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    class Done:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    sys.modules["yt_dlp.fake_part"] = sys  # something loaded from the old version
    st = ytdlp_update.check()
    assert calls == [["/usr/bin/uv", "pip", "install", "--python", sys.executable, "--upgrade",
                      "yt-dlp[default,curl-cffi]"]]
    assert st["updated"] == "2026.9.30 → 2026.10.9" and st["error"] == "" and "yt_dlp.fake_part" not in sys.modules


def test_no_network_is_an_error_to_show_not_a_crash(monkeypatch):
    def offline(timeout=15):
        raise OSError("nincs hálózat")
    monkeypatch.setattr(ytdlp_update, "latest", offline)
    st = ytdlp_update.check()
    assert "nincs hálózat" in st["error"] and not st["running"]
    assert not ytdlp_update.due(True)  # a failed try also waits a day


def test_a_403_asks_for_a_check_and_retries_later(monkeypatch):
    ytdlp_update.STATE["checked"] = time.time() - 2 * ytdlp_update.AFTER_403_S
    err = errors.from_ytdlp(Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden"),
                            "letölteni a hangot")
    assert err.retry == "later" and "újabb yt-dlp" in str(err)
    assert ytdlp_update.due(True) and not ytdlp_update.due(False)


def test_the_idle_worker_updates_before_asking_for_work(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(ytdlp_update, "check", lambda update=True: seen.append(update))
    ytdlp_update.STATE["checked"] = 0.0  # never checked: due
    stop = threading.Event()

    class Central:
        def claim(self, caps, versions):
            stop.set()
            return None
    worker.loop(cfg, stop, Central())
    assert seen == [True]


def test_machine_page_shows_the_version_and_looks_for_a_newer_one(monkeypatch):
    from fastapi.testclient import TestClient

    from dwyt_runner.app.runtime import Runtime
    from dwyt_runner.app.server import create
    runtime = Runtime(start_worker=False)
    for _ in range(100):
        if not runtime.machine["scanning"]:
            break
        time.sleep(0.05)
    api, state = create(runtime, "testserver")
    client = TestClient(api)
    client.get(f"/belepes?t={state.launch_code}")
    ytdlp_update.STATE.update(installed="2026.9.30", latest="2026.10.9")
    page = client.get("/gepem").text
    assert "Telepítve: 2026.9.30" in page and "Újabb van: 2026.10.9" in page
    ran = threading.Event()
    monkeypatch.setattr(ytdlp_update, "check", lambda update=True: ran.set())
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/gepem/ytdlp", data={"csrf": token})
    assert ran.wait(5)


def test_uv_is_found_outside_a_short_service_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp_update.shutil, "which", lambda name: None)  # systemd: no ~/.local/bin on PATH
    monkeypatch.setattr(ytdlp_update.Path, "home", lambda: tmp_path)
    assert ytdlp_update.find_uv() is None
    uv = tmp_path / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    uv.parent.mkdir(parents=True)
    uv.write_text("")
    assert ytdlp_update.find_uv() == str(uv)
    assert ytdlp_update.install_command()[0] == str(uv)
