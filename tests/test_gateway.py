"""AssemblyAI's LLM Gateway as a trial summary model (plan 19a item 17, decision 19.9)."""
import pytest

from dwyt_runner import config, gateway, keystore, localllm, modelinfo, modes
from dwyt_runner.llm import Llm
from test_runner import _run_once
from test_setup import csrf, fake_machine

SEGMENTS = {"segments": [{"start": 0, "end": 2, "speaker": None, "text": "A kertről beszélünk."}]}


@pytest.fixture
def on_gateway(cfg, llm, monkeypatch):
    """The fake LLM plays the gateway; llm_key keeps an OpenRouter key that must not be used there."""
    monkeypatch.setattr(gateway, "is_gateway", lambda url: (url or "").startswith(llm.url))
    monkeypatch.setattr(gateway, "URL", llm.url + "/v1")
    cfg.llm_url, cfg.llm_key, cfg.llm_public_model = llm.url + "/v1", "sk-or-own", gateway.TRIAL_MODEL
    return cfg


def test_real_gateway_addresses():
    assert gateway.is_gateway("https://llm-gateway.assemblyai.com/v1")
    assert gateway.is_gateway("https://llm-gateway.eu.assemblyai.com/v1")
    assert not gateway.is_gateway("https://openrouter.ai/api/v1") and not gateway.is_gateway("")
    assert gateway.url_for(config.Config(assemblyai_url="https://api.eu.assemblyai.com")) == gateway.EU_URL
    assert gateway.url_for(config.Config()) == gateway.URL


def test_a_summary_on_the_gateway_uses_the_assemblyai_key_and_a_low_temperature(on_gateway, central, llm):
    assert localllm.effective(on_gateway).llm_key == "aai-key"
    central.add_job(30, "summarize", {"role": "public", "language": "hu", "transcript": SEGMENTS})
    _run_once(on_gateway, central)
    assert central.results[30]["model"] == gateway.TRIAL_MODEL
    assert llm.auths[-1] == "Bearer aai-key"  # not the OpenRouter key kept in llm_key
    assert llm.calls[-1]["temperature"] == gateway.TRIAL_TEMPERATURE


def test_the_trial_model_keeps_its_temperature_even_where_the_model_would_choose(cfg, llm):
    Llm(cfg.llm_url, "k", fixed_temperature=0.3).complete("m", [{"role": "user", "content": "x"}], temperature=None)
    assert llm.calls[-1]["temperature"] == 0.3
    Llm(cfg.llm_url, "k").complete("m", [{"role": "user", "content": "x"}], temperature=None)
    assert "temperature" not in llm.calls[-1]


def test_too_many_requests_are_waited_out_then_handed_back(cfg, llm, monkeypatch):
    from dwyt_runner import llm as llm_mod
    from dwyt_runner.errors import WorkError
    llm.limited = 2  # a free account's few requests a minute
    answer = Llm(cfg.llm_url, "k").chat("osszefoglalo", "s", "u")
    assert answer.text.endswith("Kert.") and len(llm.calls) == 3
    monkeypatch.setattr(llm_mod, "RATE_TRIES", 1)
    llm.limited = 5
    with pytest.raises(WorkError) as info:
        Llm(cfg.llm_url, "k").chat("osszefoglalo", "s", "u")
    assert info.value.retry == "later" and "429" in str(info.value)


def test_model_info_says_trial_and_32k(on_gateway):
    modelinfo.reset()
    info, server = modelinfo.fetch(on_gateway)
    assert server == "AssemblyAI LLM Gateway"
    block = info[gateway.TRIAL_MODEL]
    assert block["trial"] is True and block["context"] == 32768 and block["where"] == "cloud"
    assert "próba-modell" in block["description"]


def test_key_check_with_a_tiny_streamed_answer(on_gateway, llm):
    good, text = keystore.check_llm(on_gateway)
    assert good and text == "rendben" and llm.calls[-1]["max_tokens"] == 5 and llm.calls[-1]["stream"]
    llm.access_denied = True
    on_gateway.llm_public_model = "gemini-2.5-flash-lite"
    good, text = keystore.check_llm(on_gateway)
    assert not good and "ingyenes fiókkal csak a qwen3.5-4b-32k-fast" in text
    llm.access_denied, llm.limited = False, 1
    assert keystore.check_llm(on_gateway)[0]  # busy is not a bad key


def test_mode_picture_and_missing_key(on_gateway):
    boxes = modes.flow(on_gateway)
    assert boxes[2].where == "felhőben (AssemblyAI, próba-modell)" and boxes[2].cost == "az AssemblyAI-kreditből"
    on_gateway.assemblyai_key = ""
    assert "Az összefoglalóhoz add meg az AssemblyAI-kulcsot a Kulcsok lapon." in modes.apply(on_gateway, "felho")


def test_setup_felho_starts_with_the_trial_and_can_switch(setup_window, llm, monkeypatch):
    client, runtime, _ = setup_window
    fake_machine(runtime)
    monkeypatch.setattr(gateway, "is_gateway", lambda url: (url or "").startswith(llm.url))
    monkeypatch.setattr(gateway, "URL", llm.url + "/v1")
    monkeypatch.setattr(keystore, "check_assemblyai", lambda cfg: (True, "rendben"))
    page = client.get("/telepites/3").text
    assert "egy AssemblyAI-regisztrációval kipróbálható" in page
    client.post("/telepites/3", data={"csrf": csrf(page), "mode": "felho"})
    cfg = config.load()
    assert gateway.is_gateway(cfg.llm_url) and cfg.llm_public_model == gateway.TRIAL_MODEL  # a new user: the trial
    page = client.get("/telepites/4").text
    assert "AssemblyAI-kulcs: átírás és próba-összefoglaló" in page and "Mire számíts a próba-modellel?" in page
    assert "Előbb add meg fent az AssemblyAI-kulcsot." in page and 'href="/telepites/5">Tovább' not in page
    page = client.post("/telepites/4", data={"csrf": csrf(page), "action": "assemblyai", "key": "aai-uj"},
                       follow_redirects=True).text
    assert "Kipróbáltuk a próba-összefoglalót: rendben." in page and "aai-uj" not in page
    assert llm.auths[-1] == "Bearer aai-uj" and 'href="/telepites/5">Tovább' in page
    page = client.post("/telepites/4", data={"csrf": csrf(page), "action": "provider", "provider": "openrouter"},
                       follow_redirects=True).text
    cfg = config.load()
    assert cfg.llm_url == "https://openrouter.ai/api/v1" and cfg.llm_public_model == "qwen/qwen3.8-flash"
    assert "OpenRouter-kulcs" in page and 'href="/telepites/5">Tovább' not in page  # its key is missing now
    client.post("/telepites/4", data={"csrf": csrf(page), "action": "provider", "provider": "gateway"})
    assert config.load().llm_public_model == gateway.TRIAL_MODEL


def test_mukodes_shows_the_trial_note_and_switches_to_openrouter(setup_window, llm, monkeypatch):
    client, runtime, _ = setup_window
    monkeypatch.setattr(gateway, "is_gateway", lambda url: (url or "").startswith(llm.url))
    config.save(config.Config(llm_url=llm.url + "/v1", llm_public_model=gateway.TRIAL_MODEL, assemblyai_key="a"))
    page = client.get("/mukodes").text
    assert "Próba-modell írja az összefoglalókat" in page and "felhőben (AssemblyAI, próba-modell)" in page
    r = client.post("/mukodes/osszefoglalo", data={"csrf": csrf(page), "provider": "openrouter"},
                    follow_redirects=False)
    from urllib.parse import unquote
    assert r.status_code == 303 and "OpenRouter-kulcsot" in unquote(r.headers["location"])
    assert config.load().llm_url == "https://openrouter.ai/api/v1"
    assert "Próba-modell írja" not in client.get("/mukodes").text


def test_window_fits_the_screen():
    from dwyt_runner.app import main

    class Screen:
        width, height = 1280, 680

    class Module:
        screens = [Screen()]
    assert main.window_size(Module) == (1040, 584)
    Screen.width, Screen.height = 2560, 1440
    assert main.window_size(Module) == (1040, 800)
    assert main.window_size(object()) == (1040, 800)


def test_main_guards_come_last():
    """`python -m module` runs the guard where it stands: everything after it does not exist yet (0.12.0 did not
    start on Windows because window_size came after the guard)."""
    import ast
    from pathlib import Path

    import dwyt_runner
    for path in Path(dwyt_runner.__file__).parent.rglob("*.py"):
        body = ast.parse(path.read_text(encoding="utf-8")).body
        guards = [i for i, node in enumerate(body) if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)]
        assert all(i == len(body) - 1 for i in guards), f"{path}: the __main__ guard is not the last statement"
