"""The settings described once (decision 18.3): complete, typed, checked, in plain Hungarian."""
import json
from dataclasses import fields

import pytest

from dwyt_runner import cli, config, settings
from dwyt_runner.config import Config


def test_every_setting_is_described_once_and_secrets_never_leak():
    keys = [s.key for s in settings.SETTINGS]
    assert sorted(keys) == sorted(f.name for f in fields(Config)) and len(keys) == len(set(keys))
    assert {s.group for s in settings.SETTINGS} <= {g for g, _ in settings.GROUPS}
    for s in settings.SETTINGS:
        assert s.label and s.kind in ("text", "secret", "bool", "int", "float", "choice", "list", "map", "path",
                                      "url")
        for key, _ in s.show_if:
            assert key in settings.BY_KEY
    assert {s.key for s in settings.SETTINGS if s.kind == "secret"} == config.SECRET_KEYS
    data = json.loads(json.dumps(settings.describe()))
    flat = {s["key"]: s for g in data["groups"] for s in g["settings"]}
    assert flat["token"]["default"] == "" and flat["token"]["remote"] is False and flat["token"]["readonly"] is True
    assert flat["diarize_threshold"]["default"] == 0.8 and flat["polite_wait_min"]["unit"] == "perc"
    assert flat["local_llm"]["choices"][2] == {"value": "lmstudio", "label": "LM Studio"}
    assert flat["lmstudio_token"]["show_if"] == [{"key": "local_llm", "value": "lmstudio"}]


def test_values_are_checked():
    p = settings.parse
    assert p("whisper_builtin", "igen") is True and p("polite_mode", "nem") is False
    assert p("diarize_threshold", "0,75") == 0.75 and p("polite_wait_min", "5") == 5
    assert p("local_llm", "ollama") == "ollama" and p("local_llm", "") == ""
    assert p("llm_model_notes", "a=Egy; b=Kettő") == {"a": "Egy", "b": "Kettő"}
    assert p("llm_url", "https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"
    for key, raw, why in (("local_llm", "vllm", "ezek egyike"), ("polite_wait_min", "500", "0 és 120 között"),
                          ("diarize_threshold", "sok", "számot várok"), ("whisper_builtin", "talán", "igen vagy nem"),
                          ("llm_url", "openrouter.ai", "http"), ("server", "https://x", "nem kézzel"),
                          ("nincs_ilyen", "1", "Ismeretlen")):
        with pytest.raises(ValueError, match=why):
            p(key, raw)


def test_describe_command(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["dwyt-runner", "config", "describe"])
    cli.main()
    out = capsys.readouterr().out
    assert "Helyi átírás ezen a gépen" in out and "  polite_wait_min (perc)" in out and "token" not in out.split(
        "Felhős átírás")[0].split("Kapcsolat a dwyt-vel")[1].replace("lmstudio_token", "")
    monkeypatch.setattr("sys.argv", ["dwyt-runner", "config", "set", "local_llm", "vllm"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "Nem mentettem: Helyi modellszerver: ezek egyike: (üres), ollama, lmstudio" in capsys.readouterr().out
