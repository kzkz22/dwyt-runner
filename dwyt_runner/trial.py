"""The test run (plan 15b, decision 18.8): how fast this machine really is, in plain sentences.

It transcribes a 33-second sample with the built-in local Whisper (three times the 11-second public-domain
excerpt of J. F. Kennedy's inaugural address that whisper.cpp also tests with) and lets the local model
server write a short Hungarian summary. Downloads and the first load are left out of the timing. From the
measured speed it says how long an hour of video would take, and recommends an operating mode; it only
recommends. It also says what the measurement cannot: that a small local model's Hungarian is weaker than
a cloud model's. The result is kept in trial.json (the window's "last test run").
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from . import config, localllm, machine, tools, whispercpp
from .config import Config
from .errors import WorkError
from .llm import Llm
from .machine import GREEN, RED, YELLOW, Lamp

SAMPLE = Path(__file__).parent / "data" / "sample-jfk.mp3"
SAMPLE_REPEAT = 3
SUMMARY_TOKENS = 700         # a typical summary's length in tokens, for the estimate
FAST_TOKENS_S = 15          # below this a big model is good but slow
BIG_MODEL_GB = 15            # a model file this big is a 30B-class model (or a MoE of that size)
SUMMARY_TEXT = (
    "Az önkormányzat tegnapi ülésén a képviselők elfogadták a város új közlekedési tervét. A terv szerint jövő "
    "tavasztól a belváros három utcája gyalogos övezet lesz, a buszjáratok pedig sűrűbben közlekednek reggel és "
    "délután. A kerékpárutak hálózatát két év alatt negyven kilométerrel bővítik, és a vasútállomásnál fedett "
    "kerékpártárolót építenek. A polgármester szerint a cél az, hogy a belvárosban csökkenjen az autóforgalom és a "
    "zaj. Az ellenzéki képviselők a parkolóhelyek megszűnését kifogásolták, ezért a tervbe bekerült egy új "
    "parkolóház a város szélén. A lakók a jövő hónapban egy lakossági fórumon mondhatják el a véleményüket.")


@dataclass
class TrialResult:
    at: float = 0.0
    gpu: str = ""                       # the card it ran with (the window says "still with the old card")
    whisper_model: str = ""
    whisper_speed: float | None = None  # audio seconds per second
    whisper_device: str = ""
    llm_model: str = ""
    llm_tokens_s: float | None = None
    lamps: list[Lamp] = field(default_factory=list)
    mode: str = ""
    mode_reason: str = ""
    errors: list[str] = field(default_factory=list)


def path() -> Path:
    return config.home() / "trial.json"


def save(r: TrialResult) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(asdict(r), ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> TrialResult | None:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        data["lamps"] = [Lamp(**x) for x in data.get("lamps", [])]
        return TrialResult(**data)
    except (OSError, ValueError, TypeError):
        return None


def sample_wav(workdir: str) -> tuple[str, float]:
    """The sample, repeated, as 16 kHz WAV; (path, seconds)."""
    dst = os.path.join(workdir, "sample.wav")
    subprocess.run([tools.ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-stream_loop", str(SAMPLE_REPEAT - 1),
                    "-i", str(SAMPLE), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst],
                   check=True, capture_output=True, timeout=120)
    import wave
    with wave.open(dst, "rb") as w:
        return dst, w.getnframes() / w.getframerate()


def _minutes(seconds: float) -> str:
    m = seconds / 60
    if m < 1:
        return "kevesebb mint 1 perc"
    return f"kb. {m:.0f} perc" if m < 90 else "kb. " + f"{m / 60:.1f}".replace(".", ",") + " óra"


def whisper_lamp(speed: float, device: str, model: str) -> Lamp:
    hour = 3600 / speed
    where = "a videókártyán" if device == "gpu" else "a processzoron"
    note = f"{model}, {where} · egy 1 órás videó {_minutes(hour)} alatt kész, beszélőkkel"
    if speed >= 6:
        return Lamp("whisper", "Helyi átírás", "gyors", GREEN, note)
    if speed >= 1:
        return Lamp("whisper", "Helyi átírás", "működik, lassabban", YELLOW, note)
    return Lamp("whisper", "Helyi átírás", "nagyon lassú", RED, note + "; az átírás felhőben ajánlott")


def llm_lamp(model: str, tokens_s: float, size_gb: float | None) -> Lamp:
    per = SUMMARY_TOKENS / tokens_s if tokens_s else 0
    note = f"{model} · {tokens_s:.0f} token/mp · egy összefoglaló {_minutes(per) if per >= 60 else f'kb. {per:.0f} mp'}"
    if tokens_s < 5:
        return Lamp("llm", "Helyi összefoglaló", "túl lassú", RED, note + "; az összefoglaló felhőben ajánlott")
    if size_gb and size_gb >= BIG_MODEL_GB:
        if tokens_s < FAST_TOKENS_S:  # good text, but a long video's summary (several parts) takes many minutes
            return Lamp("llm", "Helyi összefoglaló", "jó, de lassú", YELLOW, note + "; nagy modell, a magyar szöveg "
                        "közelít a felhőshöz, de egy hosszú videó összefoglalója több percig tart")
        return Lamp("llm", "Helyi összefoglaló", "jó", GREEN, note + "; nagy modell, a magyar szöveg közelít a "
                    "felhőshöz")
    return Lamp("llm", "Helyi összefoglaló", "működik, de gyengébb", YELLOW,
                note + ", a magyar szöveg gyengébb a felhősnél")


def measure_whisper(cfg: Config, model: str, stage) -> tuple[float, str]:
    """(audio seconds per second, device); downloads happen before the clock starts."""
    whispercpp.whisper_model(model, stage)
    whispercpp.vad_model(stage)
    whispercpp.whisper_cli(cfg, stage)
    whispercpp.speaker_models(stage)
    workdir = tempfile.mkdtemp(prefix="dwyt_trial_", dir=cfg.work_dir or None)
    try:
        wav, seconds = sample_wav(workdir)
        started = time.monotonic()
        raw = whispercpp.transcribe(wav, {"language": "en", "diarize": True}, model, cfg, workdir, stage,
                                    lambda: False, 900)
        # loading the model takes the same few seconds for 30 s or an hour of audio: not part of the speed
        took = max(time.monotonic() - started - float(raw.get("load_s") or 0), 0.1)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not raw.get("segments"):
        raise WorkError("A próbafelvételből nem lett szöveg; a whisper.cpp nem működik rendesen.")
    return seconds / took, raw.get("device") or "cpu"


def measure_llm(cfg: Config, model: str) -> tuple[float, float | None]:
    """(tokens per second, the model file's size in GB); the first, loading request is not timed."""
    eff = localllm.effective(cfg)
    localllm.before_llm_job(eff)
    llm = Llm(eff.llm_url, eff.llm_key, timeout=600)
    llm.chat(model, "Válaszolj egy szóval.", "Kész vagy?", temperature=0)  # loads the model
    answer = llm.chat(model, "Foglald össze a szöveget magyarul, három mondatban.", SUMMARY_TEXT, temperature=0.2)
    tokens = answer.completion_tokens or max(1, len(answer.text) // 3)
    size = next((m.size_gb for m in localllm.server(cfg).models() if m.name == model), None)
    return tokens / max(answer.seconds, 0.1), size


def run(cfg: Config | None = None, whisper_model: str = "large-v3", stage=lambda s: None,
        llm_model: str = "") -> TrialResult:
    cfg = cfg or config.load()
    m = machine.scan(cfg)
    r = TrialResult(at=time.time(), gpu=m.best_gpu.name if m.best_gpu else "", whisper_model=whisper_model)
    tried = replace(cfg, whisper_builtin=True, whisper_url="")
    try:
        r.whisper_speed, r.whisper_device = measure_whisper(tried, whisper_model, stage)
        r.lamps.append(whisper_lamp(r.whisper_speed, r.whisper_device, whisper_model))
    except WorkError as exc:
        r.errors.append(f"Helyi átírás: {exc}")
        r.lamps.append(Lamp("whisper", "Helyi átírás", "nem sikerült", RED, str(exc)[:300]))
    model = llm_model or cfg.llm_private_model or cfg.llm_public_model
    if localllm.server(cfg) and model:
        try:
            stage("summarize")
            r.llm_model = model
            r.llm_tokens_s, size = measure_llm(cfg, model)
            r.lamps.append(llm_lamp(model, r.llm_tokens_s, size))
        except WorkError as exc:
            r.errors.append(f"Helyi összefoglaló: {exc}")
            r.lamps.append(Lamp("llm", "Helyi összefoglaló", "nem sikerült", RED, str(exc)[:300]))
    else:
        r.lamps.append(Lamp("llm", "Helyi összefoglaló", "nincs beállítva", YELLOW,
                            "nincs helyi modellszerver vagy modell (local_llm, llm_private_model)"))
    whisper = next(x for x in r.lamps if x.key == "whisper")
    llm = next(x for x in r.lamps if x.key == "llm")
    if whisper.level == GREEN and llm.level == GREEN:
        r.mode, reason = "sajat", "a **Saját gép** módot ajánljuk: az átírás és az összefoglaló is itt készül, ingyen"
    elif whisper.level in (GREEN, YELLOW):
        r.mode, reason = "vegyes", ("a **Vegyes** módot ajánljuk: az átírás itt, ingyen készül, az összefoglaló a "
                                    "felhőben, jó minőségben")
    else:
        r.mode, reason = "felho", "a **Felhő** módot ajánljuk: ezen a gépen a helyi átírás túl lassú"
    r.mode_reason = "A próba alapján " + reason + ". Bármelyiket választhatod."
    save(r)
    return r


def report(r: TrialResult) -> str:
    lines = [f"Próbafuttatás · {time.strftime('%Y-%m-%d %H:%M', time.localtime(r.at))}"
             + (f" · {r.gpu}" if r.gpu else ""), ""]
    for x in r.lamps:
        lines.append(f"  {x.label}: {x.value}  [{machine.LAMP_TEXT[x.level]}]")
        if x.note:
            lines.append(f"    {x.note}")
    lines += ["", r.mode_reason.replace("**", "")]
    return "\n".join(lines)
