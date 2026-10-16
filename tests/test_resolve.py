"""The page resolver (plan 19a item 18, decision 19.10): pages modelled on what tools/oldal_proba.py found."""
import json

import pytest
import yt_dlp
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from conftest import Server
from dwyt_runner import config, resolve, work
from dwyt_runner.errors import WorkError
from test_runner import CAPTIONS, VIDEO, _run_once, _video

TELEX_URL = "https://telex.hu/video/2026/09/27/balogh-petya-uzletember-interju-gen-eu-holnap"
TELEX = """<html><head><title>Telex</title>
<meta property="og:title" content="Balogh Petya: akinek több tízmilliós cipője van">
<meta property="og:site_name" content="Telex">
<meta property="og:description" content="Balogh Péter üzletember a pénzről.">
<meta property="article:published_time" content="2026-09-27T08:00:00+02:00"></head>
<body><article>
<p>Mit gondol Balogh Péter a befektetésről és az iskolarendszerről? Erről beszélgettünk vele.</p>
<ul><li>Miért nem munkával kereste a pénzt, akinek több tízmilliós cipője van?</li></ul></article>
<script>window.__NUXT__={"a":{"embed":{"videoId":"dQw4w9WgXcQ"},
"audio":"https:\\/\\/audio-assets.telex.hu\\/20260927\\/1790529806-1lgh12nhg186dgfdjcd.mp3"},
"rel":["https:\\/\\/audio-assets.telex.hu\\/20261016\\/1792141068-t1tcd3dphdhm7gnbajh.mp3"]}</script>
<a href="https://open.spotify.com/playlist/76n3vG5PuenBaZyYG0KlXV">Spotify</a></body></html>"""

PODCAST_444 = """<html><head><meta property="og:title" content="Saját Tőke"></head><body>
<iframe src="https://www.youtube.com/embed/W8lQgNHrXLI?feature=oembed"></iframe>
<a href="https://open.spotify.com/episode/2pm2ubFtjOyvl0OQ2K6hF8">Spotify</a>
<a href="https://podcasts.apple.com/us/podcast/x/id1896237486?i=1000792218236">Apple</a></body></html>"""

INDEX = """<html><body><iframe src="https://indaplay.hu/hu/embed/index/arutluk/ez-a-terapias?autoplay=1"></iframe>
<iframe src="https://playlist.megaphone.fm?e=DNOW3404329014"></iframe></body></html>"""

INDAVIDEO = """<html><head><meta property="og:video" content="https://assets.indavideo.hu/swf/player.swf?vID=3bd2eb26b3">
<meta property="og:video:url" content="http://embed.indavideo.hu/player/video/3bd2eb26b3?autostart=1"></head><body>
<iframe src="//embed.indavideo.hu/player/video/3bd2eb26b3"></iframe></body></html>"""


def knows(url):
    return any(h in url for h in ("player.megaphone.fm", "podcasts.apple.com", "embed.indavideo.hu"))


def test_telex_page_prefers_the_youtube_video_then_the_days_audio():
    page = resolve.read(TELEX_URL, knows, html=TELEX)
    kinds = [(c.kind, c.url.rsplit("/", 1)[-1]) for c in page.candidates]
    assert kinds == [("youtube", "watch?v=dQw4w9WgXcQ"), ("dayfile", "1790529806-1lgh12nhg186dgfdjcd.mp3"),
                     ("file", "1792141068-t1tcd3dphdhm7gnbajh.mp3")]  # Spotify is left out
    assert page.title == "Balogh Petya: akinek több tízmilliós cipője van" and page.site == "Telex"
    assert page.text.startswith("Balogh Péter üzletember a pénzről.") and "iskolarendszerről" in page.text
    assert "tízmilliós cipője" in page.text and page.published.startswith("2026-09-27")
    summary = page.summary(resolve.choose(page, "captions"))
    assert summary["chosen"]["video_id"] == "dQw4w9WgXcQ" and len(summary["candidates"]) == 3
    picked = resolve.choose(page, "local", page.candidates[1].url)
    assert picked.kind == "dayfile"
    with pytest.raises(WorkError):
        resolve.choose(page, "local", "https://example.com/other.mp3")  # only what is on the page


def test_444_index_and_indavideo_pages():
    first = resolve.read("https://444.hu/2026/09/30/ai", knows, html=PODCAST_444).candidates
    assert [c.kind for c in first] == ["youtube", "podcast"] and first[0].video_id == "W8lQgNHrXLI"
    index = resolve.read("https://index.hu/kultur/2026/10/10/x/", knows, html=INDEX).candidates
    assert [(c.kind, c.url) for c in index] == [("player", "https://player.megaphone.fm/DNOW3404329014")]
    inda = resolve.read("https://indavideo.hu/video/URTECH", knows, html=INDAVIDEO).candidates
    assert len(inda) == 1 and inda[0].kind == "player" and "embed.indavideo.hu/player/video/3bd2eb26b3" in inda[0].url


def test_nothing_on_the_page_or_no_youtube_for_captions():
    empty = resolve.read("https://example.hu/cikk", knows, html="<html><p>Csak szöveg, felvétel nélkül.</p></html>")
    with pytest.raises(WorkError) as info:
        resolve.choose(empty, "local")
    assert "nem találtam videót vagy hangot" in str(info.value) and info.value.retry == "never"
    audio_only = resolve.read("https://index.hu/kultur/2026/10/10/x/", knows, html=INDEX)
    with pytest.raises(WorkError) as info:
        resolve.choose(audio_only, "captions")
    assert info.value.retry == "next_step" and "nincs YouTube-videó" in str(info.value)


def test_a_page_job_uses_the_youtube_captions_and_sends_the_page_back(cfg, central, monkeypatch):
    app = FastAPI()

    @app.get("/video/2026/09/27/oldal")
    def page():
        return HTMLResponse(TELEX)
    site = Server(app)
    page_url = site.url + "/video/2026/09/27/oldal"
    monkeypatch.setattr(resolve, "known", lambda url: False)
    cfg.allowed_domains = ["youtube.com", "127.0.0.1"]
    yt_dlp.REGISTRY["videos"][VIDEO] = _video()
    yt_dlp.REGISTRY["captions"]["https://captions.example/hu"] = json.dumps(CAPTIONS).encode()
    central.add_job(40, "transcribe", {"source": {"kind": "url", "url": page_url},
                                       "options": {"mode": "captions", "language": None}})
    _run_once(cfg, central)
    res = central.results[40]
    assert res["transcript"]["mode"] == "captions" and res["video"]["title"] == "Kerti videó"
    page_info = res["page"]
    assert page_info["chosen"] == {"kind": "youtube", "url": VIDEO, "label": "YouTube-videó", "video_id": "dQw4w9WgXcQ"}
    assert page_info["site"] == "Telex" and "iskolarendszerről" in page_info["text"]
    assert ("40", "page") in [(str(j), s) for j, s in central.beats]
    site.stop()


def test_a_failed_page_job_says_what_it_found_and_where_the_403_came_from(cfg, central, monkeypatch):
    app = FastAPI()

    @app.get("/video/oldal")
    def page():
        return HTMLResponse('<html><head><meta property="og:title" content="Ürmissziók"></head><body>'
                            '<iframe src="https://embed.indavideo.hu/player/video/abc123"></iframe></body></html>')
    site = Server(app)
    monkeypatch.setattr(resolve, "known", lambda url: "embed.indavideo.hu" in url)
    cfg.allowed_domains = ["indavideo.hu", "127.0.0.1"]
    cfg.whisper_url = "http://127.0.0.1:9"  # never reached: the download fails first
    embed = "https://embed.indavideo.hu/player/video/abc123"
    yt_dlp.REGISTRY["errors"][embed] = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    central.add_job(41, "transcribe", {"source": {"kind": "url", "url": site.url + "/video/oldal"},
                                       "options": {"mode": "local", "model": "large-v3", "language": None}})
    _run_once(cfg, central)
    failure = central.failures[41]
    assert failure["error"].startswith(f"Az oldalon ezt találtam: Beágyazott lejátszó (embed.indavideo.hu), {embed}.")
    assert "A(z) embed.indavideo.hu oldal megtagadta" in failure["error"] and "YouTube" not in failure["error"]
    assert failure["retry"] == "later"
    site.stop()


def test_a_direct_file_is_not_read_as_a_page():
    assert resolve.is_file("https://example.com/podcast/ep1.MP3?x=1")
    assert not resolve.is_file("https://telex.hu/video/2026/09/27/interju")


def test_media_from_the_pages_own_site_or_an_allowed_one():
    assert work._allowed_media("https://audio-assets.telex.hu/a.mp3", "https://telex.hu/video/x", ["youtube.com"])
    assert work._allowed_media("https://player.megaphone.fm/X", "https://index.hu/x", ["megaphone.fm"])
    assert not work._allowed_media("https://evil.example/a.mp3", "https://telex.hu/video/x", ["youtube.com"])


def test_the_old_default_domain_list_grows_and_a_chosen_one_stays():
    cfg = config.Config()
    cfg.allowed_domains = ["youtube.com", "youtu.be"]
    config.save(cfg)
    assert "telex.hu" in config.load().allowed_domains
    cfg.allowed_domains = ["youtube.com"]
    config.save(cfg)
    assert config.load().allowed_domains == ["youtube.com"]


def test_the_page_text_goes_into_the_summary_as_background(cfg, central, llm):
    central.add_job(41, "summarize", {"role": "public", "language": "hu",
                                      "meta": {"title": "Interjú", "page_text": "Az oldal felvezetője."},
                                      "transcript": {"segments": [{"start": 0, "end": 1, "speaker": None,
                                                                   "text": "Jó napot."}]}})
    _run_once(cfg, central)
    prompt = llm.calls[-1]["messages"][1]["content"]
    assert "csak háttér, nem a felvétel része" in prompt and "Az oldal felvezetője." in prompt
