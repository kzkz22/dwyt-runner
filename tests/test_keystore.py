"""Secrets in the system key store (decision 18.10), the file as the fallback, test requests for keys."""
import json

import keyring
from conftest import KEYRING
from keyring.backends import fail

from dwyt_runner import cli, config, keystore


def stored():
    return json.loads(config.path().read_text(encoding="utf-8"))


def test_secrets_go_to_the_key_store_not_the_file():
    cfg = config.Config(server="https://dwyt.example", token="tok-1", assemblyai_key="aai-key", llm_key="sk-1")
    config.save(cfg)
    data = stored()
    assert data["secrets"] == "keyring" and data["token"] == data["assemblyai_key"] == data["llm_key"] == ""
    assert data["server"] == "https://dwyt.example"
    assert KEYRING.backend.data[("dwyt-runner", "assemblyai_key")] == "aai-key"
    back = config.load()
    assert (back.token, back.assemblyai_key, back.llm_key) == ("tok-1", "aai-key", "sk-1")
    assert config.masked(back)["llm_key"] == "***sk-1"
    back.llm_key = ""
    config.save(back)
    assert ("dwyt-runner", "llm_key") not in KEYRING.backend.data


def test_keys_already_in_the_file_move_over():
    config.path().parent.mkdir(parents=True, exist_ok=True)
    config.path().write_text(json.dumps({"server": "https://x", "token": "tok-9", "llm_key": "sk-9"}), encoding="utf-8")
    cfg = config.load()
    assert cfg.token == "tok-9" and stored()["token"] == "" and stored()["secrets"] == "keyring"
    assert KEYRING.backend.data[("dwyt-runner", "token")] == "tok-9"


def test_without_a_key_store_the_file_keeps_them():
    keyring.set_keyring(fail.Keyring())
    keystore.reset()
    config.save(config.Config(token="tok-1", llm_key="sk-1"))
    assert stored()["secrets"] == "file" and stored()["llm_key"] == "sk-1"
    assert config.load().llm_key == "sk-1"
    assert "beállítási fájl" in keystore.name()


def test_the_file_by_choice_and_a_store_that_went_away(caplog):
    config.save(config.Config(token="tok-1", llm_key="sk-1"))
    cfg = config.load()
    cfg.secret_store = "file"
    config.save(cfg)
    assert stored()["llm_key"] == "sk-1" and ("dwyt-runner", "llm_key") not in KEYRING.backend.data
    cfg.secret_store = "auto"
    config.save(cfg)
    keyring.set_keyring(fail.Keyring())  # e.g. the desktop session is locked
    keystore.reset()
    gone = config.load()
    assert gone.llm_key == "" and gone.server == ""
    assert "nem válaszol" in caplog.text


def test_keys_are_tried(assembly, llm, capsys):
    cfg = config.Config(assemblyai_key="aai-key", assemblyai_url=assembly.url, llm_url=llm.url, llm_key="llm-key")
    assert keystore.check_assemblyai(cfg) == (True, "rendben")
    assert keystore.check_llm(cfg) == (True, "rendben")
    cfg.assemblyai_key, cfg.llm_key = "wrong", "wrong"
    assert keystore.check_assemblyai(cfg) == (False, "a kulcsot nem fogadja el")
    assert keystore.check_llm(cfg) == (False, "a kulcsot nem fogadja el")
    assert keystore.check_llm(config.Config())[0] is False
    config.save(cfg)
    assert cli.show_keys() == 1
    out = capsys.readouterr().out
    assert "A kulcsok helye: a rendszer kulcstárolója" in out
    assert "AssemblyAI (felhős átírás): a kulcsot nem fogadja el" in out
