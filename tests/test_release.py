"""Signed releases and self-update (7c, decision 18.20). The fixtures were signed with the real minisign
(a throwaway test key; only its public half is here)."""
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import Server
from fastapi import FastAPI
from fastapi.responses import Response

from dwyt_runner import __version__, release, updater

DATA = Path(__file__).parent / "data" / "release"
PUB = (DATA / "test.pub").read_text().splitlines()[1]


def test_minisign_signatures_from_the_real_tool():
    body = (DATA / "latest.json").read_bytes()
    assert release.verify(body, (DATA / "latest.json.minisig").read_text(), PUB) == "dwyt-runner 9.9.9"
    legacy = (DATA / "legacy.json").read_bytes()  # the older, not prehashed form
    assert release.verify(legacy, (DATA / "legacy.json.minisig").read_text(), PUB) == "legacy"
    with pytest.raises(release.BadSignature):
        release.verify(body.replace(b"9.9.9", b"9.9.8"), (DATA / "latest.json.minisig").read_text(), PUB)
    sig = (DATA / "latest.json.minisig").read_text().replace("dwyt-runner 9.9.9", "dwyt-runner 9.9.8")
    with pytest.raises(release.BadSignature):  # the trusted comment is signed as well
        release.verify(body, sig, PUB)
    other = "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"  # minisign's documentation key
    with pytest.raises(release.BadSignature):
        release.verify(body, (DATA / "latest.json.minisig").read_text(), other)
    with pytest.raises(release.BadSignature):
        release.verify(body, (DATA / "latest.json.minisig").read_text(), "")


class FakeGithub:
    def __init__(self):
        app = FastAPI()
        self.wheel = b"foo"

        @app.get("/releases/{where:path}")
        def files(where: str):
            name = where.rsplit("/", 1)[-1]
            if name in ("latest.json", "latest.json.minisig"):
                return Response((DATA / name).read_bytes())
            if name.endswith(".whl"):
                return Response(self.wheel)
            return Response(status_code=404)
        self.server = Server(app)


@pytest.fixture(scope="module")
def github():
    g = FakeGithub()
    yield g
    g.server.stop()


@pytest.fixture
def signed(github, monkeypatch):
    github.wheel = b"foo"
    monkeypatch.setattr(release, "BASE", github.server.url + "/releases")
    monkeypatch.setattr(release, "PUBLIC_KEY", PUB)
    return github


def test_the_projects_key_is_the_one_in_packaging():
    pub = (Path(__file__).parent.parent / "packaging" / "minisign.pub").read_text().splitlines()[1].strip()
    assert release.PUBLIC_KEY == pub


def test_without_the_projects_key_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(release, "PUBLIC_KEY", "")
    assert not updater.signed() and updater.check() is None
    ok, message, action = updater.update_now()
    assert not ok and "még nincs aláírt frissítés" in message and action == ""


def test_update_from_a_signed_release(signed, monkeypatch):
    installed = []
    monkeypatch.setitem(updater.INSTALLERS, "uv", lambda path: installed.append(path.read_bytes()) or "restart")
    ok, message, action = updater.update_now()
    assert ok and action == "restart" and installed == [b"foo"] and "9.9.9" in message
    assert updater.history()["previous"] == __version__ and updater.history()["current"] == "9.9.9"
    assert updater.STATE["latest"] == "9.9.9"


def test_a_file_that_does_not_match_the_signed_list_is_refused(signed, monkeypatch):
    signed.wheel = b"evil"
    installed = []
    monkeypatch.setitem(updater.INSTALLERS, "uv", lambda path: installed.append(path) or "restart")
    ok, message, action = updater.update_now()
    assert not ok and "nem egyezik az aláírt listával" in message and installed == []
    assert not list(updater.folder().glob("*.whl*"))


def test_going_back_needs_that_versions_own_signed_list(signed):
    ok, message, _ = updater.update_now(target="1.2.3")  # the fake serves 9.9.9's list for any version
    assert not ok and "más változatot" in message


def test_the_idle_worker_updates_only_with_auto_update(signed, monkeypatch, cfg):
    calls = []
    monkeypatch.setattr(updater, "update_now", lambda target=None: calls.append(1) or (True, "", "restart"))
    finished = []
    monkeypatch.setattr(updater, "finish", lambda action: finished.append(action))
    updater.STATE["checked"] = 0
    updater.run_if_due(cfg)  # a command-line runner: auto = off, only the check
    assert updater.STATE["latest"] == "9.9.9" and calls == []
    updater.STATE["checked"] = 0
    updater.APP = True  # the desktop app updates by itself
    updater.run_if_due(cfg)
    assert calls == [1] and finished == ["restart"]
    updater.STATE["checked"] = 0
    cfg.auto_update = "off"
    updater.run_if_due(cfg)
    assert calls == [1]


def test_package_kind(tmp_path, monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert updater.kind() == "uv"
    (tmp_path / updater.MARKER).write_text(json.dumps({"kind": "inno"}))
    assert updater.kind() == "inno"
    image = tmp_path / "dwyt-runner.AppImage"
    image.write_bytes(b"old")
    monkeypatch.setenv("APPIMAGE", str(image))
    assert updater.kind() == "appimage"
    new = tmp_path / "downloaded.AppImage"
    new.write_bytes(b"new")
    assert updater._install_appimage(new) == "restart"
    assert image.read_bytes() == b"new" and os.access(image, os.X_OK) or sys.platform == "win32"


def test_uv_install_keeps_the_extras(monkeypatch, tmp_path):
    from dwyt_runner import ytdlp_update
    seen = []
    monkeypatch.setattr(ytdlp_update, "find_uv", lambda: "uv")
    monkeypatch.setattr(updater, "extras", lambda: ["local"])
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                        updater.subprocess.CompletedProcess(cmd, 0, "", ""))
    wheel = tmp_path / "dwyt_runner-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"x")
    assert updater._install_uv(wheel) == "restart"
    assert seen[0][:5] == ["uv", "tool", "install", "--force", "--python"]
    assert seen[0][-1] == f"dwyt-runner[local] @ {wheel.resolve().as_uri()}"


def test_latest_json_lists_every_package_with_its_hash(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent.parent / "packaging"))
    import latest_json
    for name in ("dwyt_runner-1.2.3-py3-none-any.whl", "dwyt-runner-setup-1.2.3.exe",
                 "dwyt-runner-1.2.3-x86_64.AppImage"):
        (tmp_path / name).write_bytes(b"foo")
    data = latest_json.build("1.2.3", tmp_path)
    rel = release.parse(json.dumps(data).encode())
    assert rel.version == "1.2.3" and set(rel.assets) == set(updater.ASSET_FOR.values())
    assert rel.assets["windows"].sha256 == "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"
    (tmp_path / "dwyt-runner-1.2.3-x86_64.AppImage").unlink()
    with pytest.raises(SystemExit):
        latest_json.build("1.2.3", tmp_path)
