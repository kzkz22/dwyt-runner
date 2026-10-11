"""Cloud mode: AssemblyAI pre-recorded API with the user's own key (ported from yt-transcribe).

  POST   /v2/upload          raw audio          -> {upload_url}
  POST   /v2/transcript      {audio_url, ...}   -> {id, status}
  GET    /v2/transcript/{id}                    -> status, words, utterances
  DELETE /v2/transcript/{id} removes the transcript and the uploaded audio at the provider
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Callable

import httpx

from . import formatting, media
from .config import Config
from .errors import WorkError

MODELS = {"universal-2": "universal-2", "universal-3-5-pro": "universal-3-5-pro"}
PRO_LANGUAGES = frozenset({"en", "es", "fr", "de", "it", "pt", "ar", "da", "nl", "fi", "he", "hi",
                           "ja", "zh", "no", "sv", "tr", "vi"})
PRICE_PER_HOUR = {"universal-2": 0.15, "universal-3-5-pro": 0.21}
DIARIZATION_PER_HOUR = 0.02
TIMEOUT_S = 3600


def resolve_model(requested: str | None, language: str | None) -> str:
    """auto: Universal-3.5 Pro for a stated language it supports, else Universal-2."""
    requested = (requested or "auto").lower()
    lang = (language or "").split("-")[0].lower() or None
    if requested == "auto":
        return MODELS["universal-3-5-pro"] if lang in PRO_LANGUAGES else MODELS["universal-2"]
    if requested not in MODELS:
        raise WorkError(f"Ismeretlen felhős modell: {requested}.")
    if requested == "universal-3-5-pro" and lang is not None and lang not in PRO_LANGUAGES:
        raise WorkError(f"A universal-3-5-pro nem támogatja ezt a nyelvet: {lang}. Válaszd az auto-t.")
    return MODELS[requested]


def estimate_cost(api_model: str, seconds: float, diarize: bool) -> float:
    rate = PRICE_PER_HOUR.get(api_model, 0) + (DIARIZATION_PER_HOUR if diarize else 0)
    return round(rate * max(seconds, 0) / 3600, 4)


class AssemblyAI:
    def __init__(self, cfg: Config) -> None:
        self.http = httpx.Client(base_url=cfg.assemblyai_url.rstrip("/"), timeout=httpx.Timeout(60, connect=15),
                                 headers={"authorization": cfg.assemblyai_key})

    def call(self, method: str, path: str, **kw) -> dict[str, Any]:
        try:
            resp = self.http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise WorkError(f"Az AssemblyAI nem érhető el ({exc}); később újrapróbálom.", "later") from exc
        if resp.status_code in (401, 403):
            raise WorkError("Az AssemblyAI elutasította a kulcsot; ellenőrizd: dwyt-runner config set "
                            "assemblyai_key ...")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise WorkError(f"Az AssemblyAI most nem fogad kérést (HTTP {resp.status_code}).", "later")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except ValueError:
                detail = resp.text
            raise WorkError(f"Az AssemblyAI hibát adott (HTTP {resp.status_code}): {str(detail)[:300]}")
        return resp.json() if resp.content else {}


def _request_body(options: dict[str, Any], api_model: str, upload_url: str) -> dict:
    diarize = bool(options.get("diarize", True))
    body: dict = {"audio_url": upload_url, "speech_models": [api_model], "speaker_labels": diarize,
                  "punctuate": True, "format_text": True}
    if options.get("language"):
        body["language_code"], body["language_detection"] = options["language"], False
    else:
        body["language_detection"] = True
    if diarize and options.get("num_speakers"):
        body["speakers_expected"] = int(options["num_speakers"])
    return body


def _words(transcript: dict) -> list[dict]:
    source = []
    if transcript.get("utterances"):
        for utt in transcript["utterances"]:
            for word in utt.get("words") or []:
                source.append({**word, "speaker": word.get("speaker") or utt.get("speaker")})
    else:
        source = transcript.get("words") or []
    return [{"text": w.get("text", ""), "start": (w.get("start") or 0) / 1000,
             "end": (w.get("end") or 0) / 1000, "speaker": w.get("speaker")} for w in source]


def run(fetch: media.Fetch | str, options: dict[str, Any], cfg: Config, stage: Callable[[str], None],
        cancelled: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """fetch: where the media comes from (media.from_url, media.from_file); a URL string also works."""
    if isinstance(fetch, str):
        fetch = media.from_url(fetch, cfg)
    if not cfg.assemblyai_key:
        raise WorkError("Ennek a futtatónak nincs AssemblyAI-kulcsa (dwyt-runner config set assemblyai_key ...).")
    api_model = resolve_model(options.get("model"), options.get("language"))
    stage("download")
    info = fetch.info()
    workdir = tempfile.mkdtemp(prefix="dwyt_", dir=cfg.work_dir or None)
    api = AssemblyAI(cfg)
    transcript_id = None
    started = time.monotonic()
    try:
        source = fetch.get(workdir)
        info = fetch.info()
        if cancelled():
            raise WorkError("megszakítva")
        stage("prepare-audio")
        flac = media.to_flac(source, workdir)
        stage("upload")
        with open(flac, "rb") as fh:
            upload = api.call("POST", "/v2/upload", content=fh, timeout=httpx.Timeout(1800, connect=15),
                              headers={"content-type": "application/octet-stream",
                                       "content-length": str(os.path.getsize(flac))})
        stage("transcribe")
        created = api.call("POST", "/v2/transcript", json=_request_body(options, api_model, upload["upload_url"]))
        transcript_id = created["id"]
        transcript = created
        deadline = time.monotonic() + TIMEOUT_S
        while transcript.get("status") not in ("completed", "error"):
            if time.monotonic() > deadline:
                raise WorkError("Az AssemblyAI egy órán belül nem végzett.", "later")
            if cancelled():
                raise WorkError("megszakítva")
            time.sleep(3)
            transcript = api.call("GET", f"/v2/transcript/{transcript_id}")
        if transcript["status"] == "error":
            raise WorkError(f"Az AssemblyAI nem tudta átírni a hangot: {transcript.get('error')}")
        deleted = False
        if cfg.cloud_delete_after:
            try:
                api.call("DELETE", f"/v2/transcript/{transcript_id}")
                deleted = True
            except WorkError:
                pass
            transcript_id = None
        seconds = float(transcript.get("audio_duration") or info.get("duration") or 0)
        language = (transcript.get("language_code") or options.get("language") or "en").split("_")[0].lower()
        segments = formatting.clean_segments(formatting.segments_from_words(_words(transcript)))
        if not segments:
            raise WorkError("A hangban nem volt felismerhető beszéd.")
        diarize = bool(options.get("diarize", True))
        diarized = diarize and any(seg.get("speaker") for seg in segments)
        warnings = []
        if diarize and not diarized:
            warnings.append("A szolgáltató nem adott beszélőket ehhez a felvételhez.")
        speakers: list[dict] = []
        if diarized:
            speakers = formatting.relabel_speakers(segments, formatting.speaker_word(language),
                                                   formatting.unknown_word(language))
        else:
            for seg in segments:
                seg["speaker"] = None
        used = transcript.get("speech_model_used") or api_model
        return {"video": media.video_meta(info),
                "transcript": {"mode": "cloud", "model": used, "language": language, "diarized": diarized,
                               "speakers": speakers, "warnings": warnings, "segments": segments,
                               "elapsed_s": round(time.monotonic() - started, 1),
                               "cloud": {"provider": "assemblyai", "audio_seconds": seconds,
                                         "estimated_cost_usd": estimate_cost(used, seconds, diarize),
                                         "deleted_at_provider": deleted}}}
    finally:
        if transcript_id and cfg.cloud_delete_after:
            try:
                api.call("DELETE", f"/v2/transcript/{transcript_id}")
            except WorkError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)
