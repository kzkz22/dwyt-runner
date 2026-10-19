"""The tracks of an own file (decision 19.12): the audio track by language or by pick, and the embedded subtitles
as a transcript. The files are real Matroska files made with ffmpeg."""
import hashlib
import subprocess

import pytest

from dwyt_runner import tools, tracks
from test_runner import _run_once

BANNER = """Input #0, matroska,webm, from 'film.mkv':
  Duration: 01:52:10.12, start: 0.000000, bitrate: 9000 kb/s
  Stream #0:0: Video: h264 (High), yuv420p(progressive), 1920x1080, 23.98 fps (default)
  Stream #0:1(eng): Audio: aac (LC), 48000 Hz, 5.1(side), fltp (default)
    Metadata:
      title           : English
  Stream #0:2[0x1101](hun): Audio: ac3, 48000 Hz, stereo, fltp, 192 kb/s
    Metadata:
      title           : Szinkron
  Stream #0:3(eng): Subtitle: subrip (default)
    Metadata:
      title           : English SDH
  Stream #0:4(hun): Subtitle: ass
  Stream #0:5(eng): Subtitle: hdmv_pgs_subtitle, 1920x1080
  Stream #0:6(hun): Subtitle: subrip (forced)
At least one output file must be specified
"""
SRT_EN = ("1\n00:00:00,500 --> 00:00:01,400\n<i>Hello there.</i>\n\n"
          "2\n00:00:01,500 --> 00:00:02,600\n[MUSIC PLAYING] ♪ How are you?\n\n"
          "3\n00:00:02,700 --> 00:00:02,900\n[APPLAUSE]\n")
SRT_HU = "1\n00:00:00,500 --> 00:00:02,000\n{\\an8}Szia, hogy vagy?\n"


def test_the_banner_lists_audio_and_subtitle_tracks():
    t = tracks.parse(BANNER)
    assert [(a.index, a.language, a.codec, a.channels, a.title, a.default) for a in t.audio] == [
        (0, "en", "aac", "5.1(side)", "English", True), (1, "hu", "ac3", "stereo", "Szinkron", False)]
    assert [(s.index, s.language, s.codec, s.text, s.forced) for s in t.subtitles] == [
        (0, "en", "subrip", True, False), (1, "hu", "ass", True, False), (2, "en", "hdmv_pgs_subtitle", False, False),
        (3, "hu", "subrip", True, True)]
    assert tracks.choose_audio(t, {}) == 0
    assert tracks.choose_audio(t, {"language": "hu"}) == 1
    assert tracks.choose_audio(t, {"language": "hu", "audio_track": 0}) == 0
    assert tracks.choose_subtitle(t, {}, 0).index == 0                    # the audio's language
    assert tracks.choose_subtitle(t, {"language": "hu"}, 1).index == 1   # the full one before the forced one
    with pytest.raises(Exception, match="képként"):
        tracks.choose_subtitle(t, {"subtitle_track": 2}, 0)
    with pytest.raises(Exception, match="nincs de nyelvű szöveges felirat") as exc:
        tracks.choose_subtitle(t, {"language": "de"}, 0)
    assert exc.value.retry == "next_step"
    assert tracks.describe(t) == ("A fájlban: hangsáv: en (alapértelmezett), hu; felirat: en, hu, en (képként), "
                                  "hu (kényszerített).")


def test_srt_cues_lose_tags_and_sound_notes():
    cues = tracks.parse_srt(SRT_EN)
    assert [c["text"] for c in cues] == ["Hello there.", "How are you?"]
    assert cues[0]["start"] == 0.5 and cues[1]["end"] == 2.6


@pytest.fixture(scope="module")
def film(tmp_path_factory) -> str:
    d = tmp_path_factory.mktemp("film")
    (d / "en.srt").write_text(SRT_EN, encoding="utf-8")
    (d / "hu.srt").write_text(SRT_HU, encoding="utf-8")
    path = d / "film.mkv"
    subprocess.run([tools.ffmpeg(), "-nostdin", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
                    "-i", str(d / "en.srt"), "-i", str(d / "hu.srt"),
                    "-map", "0:a", "-map", "1:a", "-map", "2:s", "-map", "3:s", "-c:a", "aac", "-c:s", "srt",
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English",
                    "-metadata:s:a:1", "language=hun", "-metadata:s:a:1", "title=Szinkron",
                    "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=hun",
                    "-disposition:a:0", "default", str(path)], check=True)
    return str(path)


def test_a_real_file_one_audio_track_taken_out(film, tmp_path):
    t = tracks.read(film)
    assert [a.language for a in t.audio] == ["en", "hu"] and [s.language for s in t.subtitles] == ["en", "hu"]
    out = tracks.audio_only(film, 1, str(tmp_path))
    alone = tracks.read(out)
    assert [(a.language, a.title) for a in alone.audio] == [("hu", "Szinkron")] and alone.subtitles == []


def _upload_job(central, job_id, film, options):
    data = open(film, "rb").read()
    central.files[job_id] = data
    central.add_job(job_id, "transcribe", {
        "source": {"kind": "upload", "sha256": hashlib.sha256(data).hexdigest(), "name": "film.mkv",
                   "size": len(data), "media": f"/runner/v1/jobs/{job_id}/media"},
        "options": options})


def test_captions_mode_reads_the_embedded_subtitles(cfg, central, film):
    _upload_job(central, 50, film, {"mode": "captions", "language": None})
    _run_once(cfg, central)
    res = central.results[50]
    tr = res["transcript"]
    assert tr["mode"] == "captions" and tr["caption_kind"] == "embedded" and tr["model"] == "embedded-subrip"
    assert tr["language"] == "en" and " ".join(s["text"] for s in tr["segments"]) == "Hello there. How are you?"
    assert res["tracks"]["audio_used"] == 0 and res["tracks"]["subtitle_used"] == 0
    assert [s["language"] for s in res["tracks"]["subtitles"]] == ["en", "hu"]
    _upload_job(central, 51, film, {"mode": "captions", "subtitle_track": 1})
    _run_once(cfg, central)
    assert central.results[51]["transcript"]["segments"][0]["text"] == "Szia, hogy vagy?"
    assert central.results[51]["transcript"]["language"] == "hu"


def test_captions_mode_without_a_subtitle_in_that_language_says_what_there_is(cfg, central, film):
    _upload_job(central, 52, film, {"mode": "captions", "language": "de"})
    _run_once(cfg, central)
    failure = central.failures[52]
    assert failure["retry"] == "next_step" and "nincs de nyelvű szöveges felirat" in failure["error"]
    assert "A fájlban: hangsáv: en (alapértelmezett), hu; felirat: en, hu." in failure["error"]


def test_local_mode_transcribes_the_chosen_audio_track(cfg, central, whisper, film):
    cfg.whisper_url = whisper.url
    _upload_job(central, 53, film, {"mode": "local", "model": "large-v3", "language": "hu"})
    _run_once(cfg, central)
    res = central.results[53]
    assert res["transcript"]["mode"] == "local" and res["tracks"]["audio_used"] == 1
    assert res["tracks"]["subtitle_used"] is None
    caps = central.claims[-1]["capabilities"]
    assert caps["embedded_subtitles"] is True
