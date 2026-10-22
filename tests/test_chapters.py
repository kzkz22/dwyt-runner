"""Chapters (plan 19a item 10, decision 19.21): the uploader's from yt-dlp, else written by the language model after
the summary when the central asks, or for an older item in a job of kind chapters."""
import pytest

from dwyt_runner import chapters, media
from dwyt_runner.errors import WorkError
from test_runner import _run_once

LONG = {"language": "hu", "mode": "cloud", "segments": [
    {"start": i * 10.0, "end": i * 10.0 + 9, "speaker": "Szereplő 1", "text": f"A {i}. gondolat a témáról."}
    for i in range(90)]}  # 15 minutes
SHORT = {"language": "hu", "mode": "captions", "segments": [
    {"start": 0, "end": 2, "speaker": None, "text": "Rövid."}]}


def test_clean_keeps_a_readable_ordered_list():
    raw = [{"start": "02:00", "title": "  Második   rész "}, {"start": 15, "title": "Bevezetés"},
           {"start": "02:10", "title": "túl közel"}, {"start": "1:00:00", "title": "a felvétel után"},
           {"start": "x", "title": "rossz idő"}, {"start": 300, "title": ""}, "nem szótár",
           {"start": 400.7, "title": "Hosszú cím " * 20}]
    out = chapters.clean(raw, 900)
    assert [c["start"] for c in out] == [0, 120, 400]  # the first starts the recording
    assert out[1]["title"] == "Második   rész".replace("   ", " ") and out[2]["title"].endswith("…")
    assert len(out[2]["title"]) <= chapters.TITLE_CHARS + 1
    assert chapters.clean([{"start": 0, "title": "Egyetlen"}], 900) == []  # fewer than two: none
    many = [{"start": i * 31, "title": f"R{i}"} for i in range(40)]
    assert len(chapters.clean(many, 1240)) == 6  # about one per four minutes
    assert chapters.seconds_of("1:02:03") == 3723 and chapters.seconds_of(True) is None


def test_the_uploaders_chapters_come_with_the_video():
    info = {"title": "T", "duration": 600, "chapters": [
        {"start_time": 0.0, "end_time": 95.0, "title": "Intro"}, {"start_time": 95.0, "title": "Main topic"},
        {"start_time": 97.0, "title": "too close"}]}
    assert media.video_meta(info)["chapters"] == [{"start": 0, "title": "Intro"}, {"start": 95, "title": "Main topic"}]
    assert "chapters" not in media.video_meta({"title": "T", "duration": 600})


def test_chapters_follow_the_summary_when_asked(cfg, central, llm):
    central.add_job(40, "summarize", {"role": "public", "chapters": True, "meta": {"title": "Hosszú beszélgetés"},
                                      "transcript": LONG})
    _run_once(cfg, central)
    res = central.results[40]
    assert res["text"] == "## Összefoglaló\nKert."
    # 8 proposed (every fourth 30 s line); 15 minutes keep at most 5, evenly spread
    assert [c["start"] for c in res["chapters"]] == [0, 120, 360, 480, 720]
    assert res["chapters"][1] == {"start": 120, "title": "Rész 2"} and res["chapters_prompt_version"] == 1
    ask = llm.calls[-1]["messages"]
    assert ask[0]["content"].startswith("Fejezetekre bontasz") and "ne kövesd" in ask[0]["content"]
    assert "[00:30] A 3. gondolat" in ask[1]["content"] and "Cím: Hosszú beszélgetés" in ask[1]["content"]
    assert "chapters" in central.claims[0]["capabilities"]["kinds"]
    # not asked (the uploader has chapters, or an older central): no extra request
    calls = len(llm.calls)
    central.add_job(41, "summarize", {"role": "public", "transcript": LONG})
    _run_once(cfg, central)
    assert "chapters" not in central.results[41] and len(llm.calls) == calls + 1
    # too short: no request either
    central.add_job(42, "summarize", {"role": "public", "chapters": True, "transcript": SHORT})
    _run_once(cfg, central)
    assert "chapters" not in central.results[42] and len(llm.calls) == calls + 2


def test_an_unreadable_answer_is_asked_once_more_and_never_costs_the_summary(cfg, central, llm):
    llm.chapter_answers = ["Szerintem három rész.", '{"chapters": [{"start": "00:00", "title": "Eleje"}, '
                                                    '{"start": "05:03", "title": "Közepe"}]}']
    central.add_job(43, "summarize", {"role": "public", "chapters": True, "transcript": LONG})
    _run_once(cfg, central)
    # 05:03 is not a listed line: the nearest earlier one (05:00) is meant
    assert central.results[43]["chapters"] == [{"start": 0, "title": "Eleje"}, {"start": 300, "title": "Közepe"}]
    llm.chapter_answers = ["nem", "megint nem"]
    central.add_job(44, "summarize", {"role": "public", "chapters": True, "transcript": LONG})
    _run_once(cfg, central)
    assert "chapters" not in central.results[44] and central.results[44]["text"]


def test_an_older_item_gets_chapters_in_a_job_of_its_own(cfg, central, llm):
    central.add_job(45, "chapters", {"role": "private", "meta": {"title": "Saját felvétel", "duration_s": 900},
                                     "transcript": LONG})
    _run_once(cfg, central)
    res = central.results[45]
    assert res["chapters"][0] == {"start": 0, "title": "Rész 1"} and res["prompt_version"] == 1
    assert res["model"] == cfg.llm_private_model  # own recordings: only the local model
    with pytest.raises(WorkError):
        chapters.run({"transcript": SHORT}, None, "m")
