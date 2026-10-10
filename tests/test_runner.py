import json
import threading

import yt_dlp
from dwyt_runner import config, work, worker

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _video(**extra):
    info = {"id": "dQw4w9WgXcQ", "title": "Kerti videó", "uploader": "Kertész", "duration": 120,
            "upload_date": "20260101", "language": "hu",
            "automatic_captions": {"hu-orig": [{"ext": "json3", "url": "https://captions.example/hu"}],
                                   "en": [{"ext": "json3", "url": "https://captions.example/en"}]}}
    info.update(extra)
    return info


CAPTIONS = {"events": [{"tStartMs": 0, "dDurationMs": 3000, "segs": [
    {"utf8": "Jó", "tOffsetMs": 0}, {"utf8": " napot.", "tOffsetMs": 500}]},
    {"tStartMs": 4000, "dDurationMs": 3000, "segs": [{"utf8": "[Zene]"}]},
    {"tStartMs": 8000, "dDurationMs": 3000, "segs": [{"utf8": "Ma", "tOffsetMs": 0},
                                                    {"utf8": " a kertről.", "tOffsetMs": 400}]}]}


def _run_once(cfg, central):
    worker.loop(cfg, threading.Event(), once=True)


def test_pairing_saves_the_token(central, monkeypatch):
    from dwyt_runner import cli
    monkeypatch.setattr(cli.time, "sleep", lambda s: setattr(central, "approved", True))
    assert cli.pair(central.url, "laptop") == 0
    saved = config.load()
    assert (saved.token, saved.runner_id, saved.name) == ("tok-1", 7, "laptop")
    assert "tok-1" not in json.dumps(config.masked(saved))
    assert oct(config.path().stat().st_mode)[-3:] in ("600", "666")  # 600 on POSIX; Windows has no modes


def test_captions_job(cfg, central):
    yt_dlp.REGISTRY["videos"][VIDEO] = _video()
    yt_dlp.REGISTRY["captions"]["https://captions.example/hu"] = json.dumps(CAPTIONS).encode()
    central.add_job(1, "transcribe", {"source": {"kind": "youtube", "url": VIDEO, "video_id": "dQw4w9WgXcQ"},
                                      "options": {"mode": "captions", "language": None}})
    _run_once(cfg, central)
    res = central.results[1]
    assert res["video"] == {"title": "Kerti videó", "channel": "Kertész", "duration_s": 120, "published": "2026-01-01"}
    t = res["transcript"]
    assert (t["mode"], t["caption_kind"], t["language"]) == ("captions", "auto", "hu")
    assert [s["text"] for s in t["segments"]] == ["Jó napot.", "Ma a kertről."]
    assert ("1", "captions") in [(str(j), s) for j, s in central.beats]
    caps = central.claims[0]["capabilities"]
    assert caps["transcribe_modes"] == ["captions", "cloud"] and caps["llm_roles"] == ["public", "private"]
    params = yt_dlp.REGISTRY["calls"][0][2]
    assert params.get("js_runtimes", {}).get("deno", {}).get("path")  # deno handed to yt-dlp


def test_no_captions_asks_for_the_next_step(cfg, central):
    yt_dlp.REGISTRY["videos"][VIDEO] = _video(automatic_captions={})
    central.add_job(2, "transcribe", {"source": {"kind": "youtube", "url": VIDEO}, "options": {"mode": "captions"}})
    _run_once(cfg, central)
    assert central.failures[2]["retry"] == "next_step" and "nincs felirata" in central.failures[2]["error"]


def test_youtube_refusal_is_retried_later(cfg, central):
    yt_dlp.REGISTRY["errors"][VIDEO] = "ERROR: [youtube] x: Sign in to confirm you're not a bot"
    central.add_job(3, "transcribe", {"source": {"kind": "youtube", "url": VIDEO}, "options": {"mode": "captions"}})
    _run_once(cfg, central)
    assert central.failures[3]["retry"] == "later"


def test_cloud_job_with_own_key(cfg, central, assembly):
    yt_dlp.REGISTRY["videos"][VIDEO] = _video()
    central.add_job(4, "transcribe", {"source": {"kind": "youtube", "url": VIDEO},
                                      "options": {"mode": "cloud", "model": "auto", "language": "hu",
                                                  "diarize": True}})
    _run_once(cfg, central)
    t = central.results[4]["transcript"]
    assert t["mode"] == "cloud" and t["diarized"] is True
    assert [s["speaker"] for s in t["segments"]] == ["Szereplő 1", "Szereplő 2"]
    assert t["cloud"]["deleted_at_provider"] is True and assembly.deleted == ["t1"]
    assert assembly.uploaded > 0  # a real FLAC made by ffmpeg
    assert assembly.requests[0]["speech_models"] == ["universal-2"]  # Hungarian: not Pro


def test_disallowed_domain_and_unknown_kind_are_refused(cfg, central):
    central.add_job(5, "transcribe", {"source": {"kind": "url", "url": "https://evil.example/x"},
                                      "options": {"mode": "cloud"}})
    _run_once(cfg, central)
    assert "engedélyezett tartományok" in central.failures[5]["error"]
    central.add_job(6, "shell", {"cmd": "rm -rf /"})
    _run_once(cfg, central)
    assert "Ismeretlen munkafajta" in central.failures[6]["error"]


def test_summary_job_and_delivery_retries(cfg, central, llm):
    central.result_failures = 2
    central.add_job(7, "summarize", {"role": "private", "language": "hu", "meta": {"title": "Saját felvétel"},
                                     "transcript": {"language": "en", "mode": "cloud", "diarized": True,
                                                    "speakers": ["Speaker 1"], "segments": [
                                                        {"start": 0, "end": 2, "speaker": "Speaker 1",
                                                         "text": "Hello garden."}]}})
    _run_once(cfg, central)
    res = central.results[7]
    assert res["text"] == "## Összefoglaló\nKert." and res["model"] == "osszefoglalo-helyi"
    assert res["prompt_version"] == 1 and res["prompt_tokens"] == 50
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "[00:00] Speaker 1: Hello garden." in prompt and "## Beszélők" in prompt


def test_llm_down_is_retried_later(cfg, central, llm):
    llm.status = 503
    central.add_job(8, "summarize", {"role": "public", "transcript": {"segments": [
        {"start": 0, "end": 1, "speaker": None, "text": "x"}]}})
    _run_once(cfg, central)
    assert central.failures[8]["retry"] == "later"


def test_capabilities_follow_the_settings():
    from dwyt_runner.config import Config
    caps = work.capabilities(Config())
    assert caps["kinds"] == ["transcribe"] and caps["transcribe_modes"] == ["captions"]
    assert caps["allowed_domains"] == ["youtube.com", "youtu.be"]


def test_config_set_values():
    from dwyt_runner.config import Config, set_value
    c = Config()
    set_value(c, "allowed_domains", "youtube.com, vimeo.com")
    set_value(c, "max_duration_min", "90")
    set_value(c, "cloud_delete_after", "nem")
    assert c.allowed_domains == ["youtube.com", "vimeo.com"] and c.max_duration_min == 90
    assert c.cloud_delete_after is False
    try:
        set_value(c, "token", "x")
        raise AssertionError("token must not be settable")
    except KeyError:
        pass
