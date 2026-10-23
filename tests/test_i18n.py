"""Round 9 (decision 19.24): the runner's window, messages and settings in the account's language."""
import html
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from dwyt_runner import i18n
from dwyt_runner.app.runtime import Runtime
from dwyt_runner.app.server import create

ROOT = Path(__file__).resolve().parent.parent
HU = re.compile(r"[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű]{3,}")  # any word left unmarked (names would show up too: none are)
PAGES = ["/allapot", "/mukodes", "/kulcsok", "/telepites/1", "/telepites/3", "/telepites/4", "/telepites/5",
         "/telepites/6"]


def _enter(client, state):
    client.get(f"/belepes?t={state.launch_code}", follow_redirects=False)


def unmarked(page: str) -> list[str]:
    page = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<svg\b.*?</svg>|<code\b.*?</code>", " ", page, flags=re.S)
    while "⟦" in page:  # nested (a translated text inside another): innermost first
        new = re.sub(r"⟦[^⟦⟧]*⟧", " ", page)
        if new == page:
            break
        page = new
    texts = re.findall(r">([^<>]+)<", page) + re.findall(r'(?:title|placeholder|aria-label|data-busy)="([^"]*)"', page)
    return sorted({html.unescape(t).strip() for t in texts if HU.search(html.unescape(t))})


def test_the_catalogue_is_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "i18n_extract.py"), "--check"], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_window_text_goes_through_translation(monkeypatch):
    monkeypatch.setenv("DWYT_RUNNER_LANG", "xx")
    api, state = create(Runtime(start_worker=False), "testserver")
    client = TestClient(api)
    _enter(client, state)
    left = {url: unmarked(client.get(url).text) for url in PAGES}
    assert {u: x for u, x in left.items() if x} == {}


def test_the_language_choice(tmp_path, monkeypatch):
    monkeypatch.delenv("DWYT_RUNNER_LANG", raising=False)
    monkeypatch.setattr(i18n, "system_language", lambda: "fr")
    assert i18n.choose("auto", tmp_path) == "fr"  # before pairing: the system's
    assert i18n.remember_account(tmp_path, "ui=de; content=hu")
    assert not i18n.remember_account(tmp_path, "ui=de; content=hu")  # unchanged
    assert i18n.choose("auto", tmp_path) == "de" and i18n.choose("it", tmp_path) == "it"
    i18n.remember_account(tmp_path, "ui=lv; content=lv")
    assert i18n.choose("auto", tmp_path) == "en"  # a round-2 account language: English interface
    monkeypatch.setattr(i18n, "system_language", lambda: "ja")
    assert i18n.choose("auto", None) == "en"


def test_catalogues_are_complete_and_keep_the_placeholders():
    from babel.messages.pofile import read_po
    placeholders = re.compile(r"%\([^)]+\)s|%%|\{[^{}]*\}")
    with (i18n.LOCALE_DIR / "messages.pot").open("rb") as f:
        wanted = {m.id for m in read_po(f) if m.id}
    for code in i18n.UI_LANGUAGES:
        if code == i18n.SOURCE:
            continue
        got = i18n.read_po(i18n.LOCALE_DIR / f"{code}.po")  # the runner's own reader, as at runtime
        assert [m for m in wanted if not got.get(m)] == [], code
        wrong = [m for m in wanted if sorted(placeholders.findall(m)) != sorted(placeholders.findall(got[m]))]
        assert wrong == [], (code, wrong[:5])


def test_the_window_in_german(monkeypatch):
    monkeypatch.setenv("DWYT_RUNNER_LANG", "de")
    api, state = create(Runtime(start_worker=False), "testserver")
    client = TestClient(api)
    _enter(client, state)
    page = client.get("/mukodes").text
    assert '<html lang="de">' in page and "Betrieb" in page
    from dwyt_runner import settings
    with i18n.language("de"):
        described = settings.describe()
    assert described["groups"][0]["title"] == "Verbindung zu Skipwise"


def test_a_weak_local_language_is_pointed_out_on_the_operation_tab(monkeypatch):
    from types import SimpleNamespace
    from dwyt_runner import config, localllm
    monkeypatch.setattr(localllm, "server", lambda cfg: SimpleNamespace(title="Ollama"))
    api, state = create(Runtime(start_worker=False), "testserver")
    client = TestClient(api)
    _enter(client, state)
    monkeypatch.setattr(localllm, "uses_local", lambda cfg: True)
    assert "tartalomnyelve" not in client.get("/mukodes").text  # nothing known about the account yet
    assert i18n.remember_account(config.home(), "ui=hu; content=lt")
    page = client.get("/mukodes").text
    assert "A fiókod tartalomnyelve: lietuvių." in page
    monkeypatch.setattr(localllm, "uses_local", lambda cfg: False)  # summaries in the cloud: no note
    assert "tartalomnyelve" not in client.get("/mukodes").text
    i18n.remember_account(config.home(), "ui=hu; content=de")
    monkeypatch.setattr(localllm, "uses_local", lambda cfg: True)
    assert "tartalomnyelve" not in client.get("/mukodes").text


def test_the_windows_installer_speaks_the_six_languages():
    iss = (ROOT / "packaging" / "windows" / "dwyt-runner.iss").read_text(encoding="utf-8-sig")
    names = re.findall(r'^Name: "(\w+)"; MessagesFile:', iss, flags=re.M)
    assert names[0] == "en" and sorted(names) == sorted(i18n.UI_LANGUAGES)  # English for any other Windows
    used = set(re.findall(r"\{cm:(\w+)\}|CustomMessage\('(\w+)'\)", iss))
    used = {a or b for a, b in used}
    for message in used:
        for code in i18n.UI_LANGUAGES:
            assert re.search(rf"^{code}\.{message}=.+", iss, flags=re.M), (code, message)


def test_the_claim_answer_sets_the_language_and_the_settings_are_reported_again(cfg, central, monkeypatch):
    import threading
    from dwyt_runner import config, worker
    from dwyt_runner.protocol import Central
    monkeypatch.delenv("DWYT_RUNNER_LANG", raising=False)
    monkeypatch.setattr(i18n, "system_language", lambda: "hu")
    cfg.remote_settings = "on"
    config.save(cfg)
    central.languages = "ui=fr; content=fr"
    worker.loop(config.load(), threading.Event(), Central(central.url, "tok-1"), once=True)
    assert i18n.current() == "fr"
    assert i18n.account_languages(config.home()) == {"ui": "fr", "content": "fr"}
    groups = central.reports[-1]["describe"]["groups"]
    assert groups[0]["title"] == "Connexion à Skipwise"  # reported again, now in French
