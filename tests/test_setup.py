"""The setup steps in the window (plan 15b, docs/minta-7 1–6)."""
import re
import time
import webbrowser

import pytest
from fastapi.testclient import TestClient

from dwyt_runner import autostart, config, keystore, machine, whispercpp
from dwyt_runner.app import wizard
from dwyt_runner.app.runtime import Runtime
from dwyt_runner.app.server import COOKIE, create


def csrf(page: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: urls.append(url) or True)
    return urls


@pytest.fixture
def setup_window(opened):
    runtime = Runtime(start_worker=False)
    api, state = create(runtime, "testserver")
    client = TestClient(api)
    r = client.get(f"/belepes?t={state.launch_code}", follow_redirects=False)
    assert COOKIE in r.cookies
    for _ in range(100):  # the first machine check
        if not runtime.machine["scanning"]:
            break
        time.sleep(0.05)
    return client, runtime, r.headers["location"]


def fake_machine(runtime, servers=(), vram=12.0):
    m = machine.Machine(system="Windows", cpu="AMD Ryzen 7 7700", threads=16, ram_gb=32, disk_free_gb=214,
                        gpus=[machine.Gpu(name="NVIDIA GeForce RTX 4070", vram_gb=vram, vendor="nvidia")],
                        servers=list(servers), scanned_at=time.time())
    runtime.machine.update(m=m, at=time.time(), scanning=False)
    return m


def test_a_new_runner_opens_on_the_setup_steps_and_can_skip_them(setup_window):
    client, runtime, first = setup_window
    assert first == "/telepites/1"
    page = client.get("/telepites/1").text
    assert "Üdvözöl a dwyt futtató" in page and "A munka csak akkor halad" in page
    client.post("/telepites/kihagyas", data={"csrf": csrf(page)})
    assert wizard.load()["done"] and not wizard.needed(config.load())
    assert "A telepítő lépései" in client.get("/allapot").text  # still reachable while not paired


def test_machine_step_shows_lamps_and_the_recommendation(setup_window, monkeypatch):
    client, runtime, _ = setup_window
    fake_machine(runtime, [machine.ModelServer("ollama", installed=True, running=True, models=["qwen3:8b"])])
    page = client.get("/telepites/2").text
    assert "Mire képes a géped?" in page and "RTX 4070" in page and "Vegyes" in page
    assert '<option value="qwen3:8b">Ollama: qwen3:8b</option>' in page
    assert "kapja: az Ollama" in page and "kiveszem a memóriából" in page  # the test run never waits
    fake_machine(runtime, [])
    assert "kiveszem a memóriából" not in client.get("/telepites/2").text
    fake_machine(runtime, [machine.ModelServer("ollama", installed=True, running=True, models=["qwen3:8b"])])
    started = []
    monkeypatch.setattr(runtime, "start_trial", lambda llm="", server="": started.append((llm, server)))
    client.post("/telepites/2", data={"csrf": csrf(page), "llm": "qwen3:8b", "server": "ollama"})
    assert started == [("qwen3:8b", "ollama")]
    runtime.machine.update(m=None)
    assert "data-machine-scanning" in client.get("/telepites/2").text


def test_mode_step_sets_the_mode_and_starts_the_whisper_download(setup_window, monkeypatch):
    client, runtime, _ = setup_window
    fake_machine(runtime)
    page = client.get("/telepites/3").text
    assert page.count("data-mode-card") == 4 and "ajánlott" in page and page.count("data-flow-for=") == 4
    sajat = page.split('data-flow-for="sajat"')[1].split("data-flow-for")[0]
    assert "ezen a gépen (Ollama vagy LM Studio)" in sajat and "OpenRouter" not in sajat
    assert 'value="vegyes" checked' in page  # nothing chosen yet: the recommended one
    monkeypatch.setattr(whispercpp, "prepared", lambda cfg, model: False)
    started = []
    monkeypatch.setattr(runtime, "start_prepare", lambda model: started.append(model))
    r = client.post("/telepites/3", data={"csrf": csrf(page), "mode": "vegyes"}, follow_redirects=False)
    assert r.headers["location"] == "/telepites/4" and started == ["large-v3"]
    cfg = config.load()
    assert cfg.whisper_builtin and cfg.llm_url == wizard.CLOUD_LLM_URL and wizard.load()["mode"] == "vegyes"


def test_ingredients_ask_for_what_the_mode_needs(setup_window, monkeypatch):
    client, runtime, _ = setup_window
    fake_machine(runtime)
    config.save(config.Config(whisper_builtin=True, llm_url=wizard.CLOUD_LLM_URL))
    wizard.save(mode="vegyes")
    monkeypatch.setattr(whispercpp, "prepared", lambda cfg, model: True)
    page = client.get("/telepites/4").text
    assert "Ami a(z) Vegyes módhoz kell" in page and "Kész: a beszédfelismerő a gépen van." in page
    assert "Előbb add meg" in page and 'value="qwen/qwen3.8-flash"' in page  # the key is missing
    tried = []
    monkeypatch.setattr(keystore, "check_llm", lambda cfg: tried.append(cfg.llm_key) or (True, "rendben"))
    r = client.post("/telepites/4", data={"csrf": csrf(page), "action": "llm", "key": " sk-or-1 ",
                                          "model": "qwen/qwen3.8-flash"}, follow_redirects=True)
    assert tried == ["sk-or-1"] and "sk-or-1" not in r.text and "Kipróbáltuk: rendben." in r.text
    assert 'href="/telepites/5">Tovább' in r.text
    cfg = config.load()
    assert (cfg.llm_key, cfg.llm_public_model) == ("sk-or-1", "qwen/qwen3.8-flash")


def test_own_machine_mode_picks_a_local_model_or_downloads_one(setup_window, monkeypatch):
    client, runtime, _ = setup_window
    fake_machine(runtime, [machine.ModelServer("ollama", installed=True, running=True, models=["qwen3:8b"])])
    config.save(config.Config(whisper_builtin=True, llm_prefer_local=True))
    wizard.save(mode="sajat")
    monkeypatch.setattr(whispercpp, "prepared", lambda cfg, model: True)
    page = client.get("/telepites/4").text
    assert "Ollama" in page and "<option>qwen3:8b</option>" in page and 'value="qwen3:30b" required' in page
    pulls = []
    monkeypatch.setattr(runtime, "start_pull", lambda kind, name: pulls.append((kind, name)))
    client.post("/telepites/4", data={"csrf": csrf(page), "action": "pull", "server": "ollama", "name": "qwen3:14b"})
    assert pulls == [("ollama", "qwen3:14b")]
    r = client.post("/telepites/4", data={"csrf": csrf(page), "action": "local", "server": "ollama",
                                          "model": "qwen3:8b"}, follow_redirects=True)
    cfg = config.load()
    assert (cfg.local_llm, cfg.llm_private_model, cfg.llm_public_model) == ("ollama", "qwen3:8b", "qwen3:8b")
    assert "Kiválasztva: qwen3:8b." in r.text and 'href="/telepites/5">Tovább' in r.text
    fake_machine(runtime, [])
    assert "Az Ollama letöltése" in client.get("/telepites/4").text  # no server: offer Ollama


def test_whisper_download_progress_reaches_the_page(setup_window, monkeypatch):
    client, runtime, _ = setup_window
    import threading
    go = threading.Event()

    def prepare(cfg, model, step):
        step("beszédfelismerő modell")
        whispercpp.ON_DOWNLOAD("ggml-large-v3.bin", 2 * 1024 ** 3, 3 * 1024 ** 3)
        go.wait(10)
    monkeypatch.setattr(whispercpp, "prepare", prepare)
    runtime.start_prepare("large-v3")
    for _ in range(50):
        p = client.get("/api/telepites").json()["prepare"]
        if p["percent"]:
            break
        time.sleep(0.05)
    assert p == {"running": True, "ready": False, "error": "", "percent": 66,
                 "text": "beszédfelismerő modell · 66% (2,0 GB / 3,0 GB)"}
    go.set()
    for _ in range(50):
        if not runtime.prepare["running"]:
            break
        time.sleep(0.05)
    assert runtime.prepare["ready"] and whispercpp.ON_DOWNLOAD is None


def test_pairing_step_opens_the_approval_page_with_the_code(setup_window, central, opened):
    client, runtime, _ = setup_window
    page = client.get("/telepites/5").text
    central.approved = False
    r = client.post("/telepites/5", data={"csrf": csrf(page), "server": central.url, "name": "dolgozószoba"},
                    follow_redirects=True)
    assert "ABC-234" in r.text and "data-watch=\"pairing\"" in r.text
    assert opened == [f"{central.url}/futtatok?kod=ABC-234&nev=dolgoz%C3%B3szoba"]
    central.approved = True
    for _ in range(50):
        if runtime.pairing.get("state") == "done":
            break
        time.sleep(0.2)
    assert "Párosítva." in client.get("/telepites/5").text
    runtime.shutdown()
    if runtime.thread:
        runtime.thread.join(timeout=60)


def test_last_step_sets_login_start_and_opens_dwyt(setup_window, opened, monkeypatch):
    client, runtime, _ = setup_window
    config.save(config.Config(server="https://dwyt.example", token="tok-1"))
    switched = []
    monkeypatch.setattr(autostart, "enabled", lambda platform=None: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda on, platform=None: switched.append(on))
    page = client.get("/telepites/6").text
    assert 'name="login_start" checked' in page and "Kék:" in page
    r = client.post("/telepites/6", data={"csrf": csrf(page), "login_start": "on", "open_dwyt": "on"},
                    follow_redirects=False)
    assert r.headers["location"] == "/allapot" and switched == [True] and wizard.load()["done"]
    assert opened == ["https://dwyt.example/uj"]


def test_ingredient_rules():
    cfg = config.Config()
    items = {i.key: i for i in wizard.ingredients(cfg, "felho", {}, False)}
    assert items["whisper"].need == "nem kell" and items["llm"].need == "kell"
    assert items["assemblyai"].need == "ajánlott" and items["assemblyai"].lamp == "yellow"
    assert not wizard.can_go_on(list(items.values()))
    cfg.llm_url, cfg.llm_key, cfg.llm_public_model = wizard.CLOUD_LLM_URL, "k", "m/x"
    assert wizard.can_go_on(wizard.ingredients(cfg, "felho", {}, False))  # AssemblyAI is only recommended
    assert not wizard.can_go_on(wizard.ingredients(cfg, "vegyes", {}, False))  # Whisper not here yet
    assert wizard.can_go_on(wizard.ingredients(cfg, "vegyes", {"running": True}, False))  # downloading is enough
    assert wizard.ingredients(config.Config(whisper_url="http://gpu"), "vegyes", {}, False)[0].need == "nem kell"
    assert wizard.cloud_model_default(config.Config(llm_public_model="qwen3:8b")) == wizard.CLOUD_MODEL
    assert wizard.suggested_local_model(24, "ollama") == "qwen3:30b"
    assert wizard.suggested_local_model(16, "lmstudio", 62) == "qwen/qwen3-30b-a3b"  # MoE with system memory
    assert wizard.suggested_local_model(16, "lmstudio", 16) == "qwen/qwen3-8b"
    assert wizard.suggested_local_model(8, "ollama", 16, ["qwen3:8b"]) == ""  # already there


def test_openrouter_keys_are_checked_where_a_wrong_key_shows(monkeypatch):
    import httpx

    class R:
        def __init__(self, code):
            self.status_code = code
    asked = []

    def get(url, headers=None, timeout=None, **kw):
        asked.append(url)
        good = headers == {"Authorization": "Bearer good"}
        return R(200 if url.endswith("/models") or good else 401)
    monkeypatch.setattr(httpx, "get", get)
    cfg = config.Config(llm_url="https://openrouter.ai/api/v1", llm_key="bad")
    assert keystore.check_llm(cfg) == (False, "a kulcsot nem fogadja el")  # the model list would have said yes
    assert asked == ["https://openrouter.ai/api/v1/key"]
    cfg.llm_key = "good"
    assert keystore.check_llm(cfg) == (True, "rendben")
