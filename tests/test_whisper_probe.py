"""tools/whisper_probe.py: the round 7b quality check (reference parsing, scoring, a run with fakes)."""
import importlib.util
import json
import os
import stat
import sys
import wave
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("whisper_probe", Path(__file__).parents[1] / "tools" / "whisper_probe.py")
probe = importlib.util.module_from_spec(SPEC)
sys.modules["whisper_probe"] = probe
SPEC.loader.exec_module(probe)

REFERENCE = """﻿# Easy Hungarian 1 - Where do you want to travel?
URL: https://www.youtube.com/watch?v=1oA1PkMwTpU
Csatorna: Easy Languages
Beszélők: Szereplő 1, Szereplő 2

[00:00] Szereplő 1: Sziasztok! Hova szeretnél elutazni?

[00:26] Szereplő 2: Most fogok utazni Berlinbe.

[01:02:03] Szereplő 1: Akkor nem.
"""


def test_reference_is_parsed_from_the_dwyt_export(tmp_path):
    path = tmp_path / "ref.txt"
    path.write_text(REFERENCE, encoding="utf-8")
    t = probe.parse_txt(path)
    assert t.url == "https://www.youtube.com/watch?v=1oA1PkMwTpU"
    assert [w.text for w in t.words][:3] == ["Sziasztok!", "Hova", "szeretnél"]
    assert t.speakers == {"Szereplő 1", "Szereplő 2"}
    assert t.words[3].start == 0 and t.words[4].start == 26 and t.words[-1].start == 3723
    # written back and read again: the same words and speakers
    probe.write_txt(t, tmp_path / "back.txt")
    again = probe.parse_txt(tmp_path / "back.txt")
    assert [(w.text, w.speaker) for w in again.words] == [(w.text, w.speaker) for w in t.words]


def test_word_errors_ignore_case_and_punctuation():
    ref = [probe.norm(w) for w in "Hollandiából jöttél, igaz?".split()]
    hyp = [probe.norm(w) for w in "hollandiába jöttél igaz igen".split()]
    ops = probe.align(ref, hyp)
    assert [op for op, _, _ in ops] == ["sub", "ok", "ok", "ins"]
    assert probe.error_rate(ops) == pytest.approx(2 / 3)
    assert probe.char_error_rate(["abc"], ["abd"]) == pytest.approx(1 / 3)
    assert probe.error_rate(probe.align(["a", "b"], [])) == 1.0


def test_speakers_are_matched_whatever_their_numbers():
    pairs = [("Szereplő 1", "Szereplő 2")] * 6 + [("Szereplő 2", "Szereplő 1")] * 3 + [("Szereplő 2", "Szereplő 2")]
    assert probe.speaker_accuracy(pairs) == pytest.approx(0.9)
    assert probe.speaker_accuracy([("Szereplő 1", None)]) == 0
    assert probe.speaker_accuracy([(None, "Szereplő 1")]) is None


def test_words_take_the_speaker_of_the_turn_they_overlap_most():
    words = [probe.Word("a", None, 0.0, 1.0), probe.Word("b", None, 1.8, 2.4), probe.Word("c", None, 9.0, 9.5)]
    turns = [(0.0, 2.0, "Szereplő 1"), (2.0, 5.0, "Szereplő 2")]
    assert [w.speaker for w in probe.assign_speakers(words, turns)] == ["Szereplő 1", "Szereplő 2", "Szereplő 2"]


def test_backend_line_prefers_the_one_naming_the_backend():
    log = "ggml_cuda_init: found 1 CUDA devices\nwhisper_backend_init_gpu: using CUDA0 backend\n"
    assert probe.backend_of(log) == "whisper_backend_init_gpu: using CUDA0 backend"
    assert probe.backend_of("nothing here") == ""


FAKE_CLI = """#!{python}
import json, sys
args = sys.argv[1:]
if "--help" in args:
    sys.exit(0)
base = args[args.index("-of") + 1]
words = ["Sziasztok!", "Hova", "szeretnél", "utazni?", "Most", "fogok", "utazni", "Berlinbe."]
segs = [{{"text": " " + w, "offsets": {{"from": i * 3000, "to": i * 3000 + 2500}}}} for i, w in enumerate(words)]
segs.insert(0, {{"text": "[_BEG_]", "offsets": {{"from": 0, "to": 0}}}})
open(base + ".json", "w", encoding="utf-8").write(json.dumps({{"transcription": segs}}))
sys.stderr.write("whisper_backend_init_gpu: using CUDA0 backend\\n")
"""


@pytest.fixture
def run(tmp_path, monkeypatch):
    script = tmp_path / "fake_whisper_cli.py"
    script.write_text(FAKE_CLI.format(python=sys.executable), encoding="utf-8")
    if os.name == "nt":
        cli = tmp_path / "whisper-cli.cmd"
        cli.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        cli = script
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    wav = tmp_path / "audio.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000 * 30)
    monkeypatch.setattr(probe, "fetch_audio", lambda url, cache: wav)
    models = tmp_path / "cache" / "models"
    models.mkdir(parents=True)
    for url in probe.WHISPER_MODELS.values():
        (models / Path(url).name).write_bytes(b"fake model")
    ref = tmp_path / "JAVITOTT.txt"
    ref.write_text(REFERENCE.replace("[01:02:03] Szereplő 1: Akkor nem.", ""), encoding="utf-8")
    old = tmp_path / "EREDETI.txt"
    old.write_text(REFERENCE.replace("Szereplő 2", "Szereplő 1").replace("Berlinbe", "Berlinben"), encoding="utf-8")

    def go(*extra):
        assert probe.main([str(ref), "--compare", str(old), "--whisper-cli", str(cli), "--out", str(tmp_path / "out"),
                           "--cache", str(tmp_path / "cache"), *extra]) == 0
        return (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    return go


def test_full_run_with_fakes(run, monkeypatch):
    calls = []

    def fake_diarize(wav, cache, speakers, embedding="3dspeaker"):
        calls.append((speakers, embedding))
        if embedding == "wespeaker":
            raise RuntimeError("letöltés sikertelen")
        return [(0.0, 12.0, "Szereplő 1"), (12.0, 30.0, "Szereplő 2")], 1.5
    monkeypatch.setattr(probe, "diarize", fake_diarize)
    report = run()
    # once per video and speaker model, not again for each Whisper model
    assert calls == [(None, "3dspeaker"), (2, "3dspeaker"), (None, "wespeaker"), (2, "wespeaker")]
    assert "| EREDETI | 37,5% |" in report  # one word wrong and two extra, out of eight
    assert "| whisper.cpp large-v3-turbo | 12,5% |" in report  # "utazni?" for "elutazni?"
    assert "| whisper.cpp large-v3 + sherpa-onnx 3dspeaker, ismert szám | 12,5% | " in report
    assert "| 2 | 100,0% |" in report  # both speakers found, every word right
    assert "GPU/backend: whisper_backend_init_gpu: using CUDA0 backend" in report
    assert "Hiba: sherpa-onnx wespeaker (automatikus): letöltés sikertelen" in report


def test_run_without_speakers(run, tmp_path):
    report = run("--no-diarize", "--models", "large-v3-turbo")
    assert "sherpa-onnx 3dspeaker" not in report and "large-v3 |" not in report
    page = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "<del>Hova</del>" not in page and "<del>szeretnél" not in page
    assert "<ins>utazni?</ins>" in page or "<del>elutazni?</del><ins>utazni?</ins>" in page
    txt = json.dumps(sorted(p.name for p in (tmp_path / "out").iterdir()))
    assert "JAVITOTT.whisper.cpp_large-v3-turbo.txt" in txt


def test_download_tries_the_next_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)  # no curl either
    good = tmp_path / "model.onnx"
    good.write_bytes(b"model")
    dest = tmp_path / "cache" / "model.onnx"
    probe.download([(tmp_path / "missing.onnx").as_uri(), good.as_uri()], dest)
    assert dest.read_bytes() == b"model"
    with pytest.raises(RuntimeError, match="letöltés sikertelen"):
        probe.download((tmp_path / "missing.onnx").as_uri(), tmp_path / "cache" / "other.onnx")
    assert not (tmp_path / "cache" / "other.onnx.part").exists()


def test_whisper_cli_may_be_given_as_the_release_folder(tmp_path):
    exe = tmp_path / "whisper.cpp-1.9.5" / "Release" / ("whisper-cli.exe" if os.name == "nt" else "whisper-cli")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    (exe.parent / "whisper-server.exe").write_bytes(b"")
    path, build = probe.whisper_cli(tmp_path, str(tmp_path / "whisper.cpp-1.9.5"))
    assert path == str(exe) and build == "megadott (Release)"
    with pytest.raises(SystemExit, match="Nincs whisper-cli"):
        empty = tmp_path / "empty"
        empty.mkdir()
        probe.whisper_cli(tmp_path, str(empty))


def test_blocked_program_is_explained():
    err = OSError(22, "Egy alkalmazásvezérlési házirend letiltotta ezt a fájlt")
    err.winerror = 4551
    assert "alkalmazásvezérlése" in probe.blocked_hint(err)
    assert probe.blocked_hint(RuntimeError("x")) == ""
