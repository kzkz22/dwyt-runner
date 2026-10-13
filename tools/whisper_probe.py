# /// script
# requires-python = ">=3.10"
# dependencies = ["yt-dlp[default]", "deno", "imageio-ffmpeg", "numpy", "sherpa-onnx", "rapidfuzz"]
# ///
"""whisper.cpp + sherpa-onnx compared with WhisperX on Hungarian videos (plan 15b, round 7b check).

Run on the Windows PC (or any machine with Python + uv):

    uv run whisper_probe.py JAVITOTT.txt --compare EREDETI.txt

The reference is a dwyt TXT export the user corrected by hand (the `URL:` line names the video;
`[mm:ss] Szereplő N: text` lines). For each reference the script downloads the audio, runs
whisper.cpp (large-v3-turbo and large-v3) on the GPU if it can, tells the speakers apart with
sherpa-onnx (pyannote segmentation 3.0 + two speaker-embedding models; each once with
automatic clustering, once with the true number of speakers), and compares every version with the
reference: word error rate, character error rate, speakers found, and how many words got the
right speaker. `--compare` adds other transcripts of the same video (e.g. the WhisperX one).

Output (`whisper-probe-out/`): report.md, report.html (word-level differences), and every
version as a dwyt-style TXT. Downloads are cached in `whisper-probe-cache/`.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import unicodedata
import urllib.request
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HF = "https://huggingface.co"
WHISPER_MODELS = {"large-v3-turbo": f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
                  "large-v3": f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"}
SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION = f"{SHERPA}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDINGS = {  # the folder name's typo is upstream's
    "3dspeaker": f"{SHERPA}/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
    "wespeaker": f"{SHERPA}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"}
RELEASES = "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"
TIME = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.*)$")
SPEAKER = re.compile(r"^((?:Szereplő|Speaker|SPEAKER)[ _]?\d+|[^:]{1,40}):\s+(.*)$")


# ---- transcripts -------------------------------------------------------------------------------

@dataclass
class Word:
    text: str
    speaker: str | None = None
    start: float | None = None
    end: float | None = None


@dataclass
class Transcript:
    name: str
    url: str = ""
    words: list[Word] = field(default_factory=list)
    seconds: float | None = None  # how long it took
    note: str = ""

    @property
    def speakers(self) -> set[str]:
        return {w.speaker for w in self.words if w.speaker}


def parse_txt(path: Path) -> Transcript:
    """A dwyt TXT export: header lines (URL: ...), then '[mm:ss] Szereplő N: text' paragraphs."""
    t = Transcript(path.stem)
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if line.lower().startswith("url:"):
            t.url = line[4:].strip()
            continue
        m = TIME.match(line)
        if not m:
            continue
        h_or_m, m_or_s, s, rest = m.groups()
        start = (int(h_or_m) * 3600 + int(m_or_s) * 60 + int(s)) if s else (int(h_or_m) * 60 + int(m_or_s))
        speaker = None
        sm = SPEAKER.match(rest)
        if sm and not sm.group(1).strip().startswith(">>"):
            speaker, rest = sm.group(1).strip(), sm.group(2)
        for token in rest.split():
            t.words.append(Word(token, speaker, float(start)))
    return t


def write_txt(t: Transcript, path: Path) -> None:
    lines = [f"# {t.name}", f"URL: {t.url}", ""]
    para: list[str] = []
    who: str | None = None
    start = 0.0

    def flush() -> None:
        if para:
            label = f" {who}:" if who else ""
            lines.append(f"[{int(start // 60):02d}:{int(start % 60):02d}]{label} {' '.join(para)}")
            lines.append("")
    for w in t.words:
        if not para or w.speaker != who:
            flush()
            para, who, start = [], w.speaker, w.start or 0.0
        para.append(w.text)
    flush()
    path.write_text("\n".join(lines), encoding="utf-8")


# ---- comparison --------------------------------------------------------------------------------

def norm(word: str) -> str:
    """Lowercase, punctuation off (letters with accents and digits stay)."""
    word = unicodedata.normalize("NFC", word.lower())
    return "".join(ch for ch in word if ch.isalnum())


def norm_words(words: list[Word]) -> list[tuple[str, Word]]:
    out = []
    for w in words:
        n = norm(w.text)
        if n:
            out.append((n, w))
    return out


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Levenshtein alignment: ('ok'|'sub'|'del'|'ins', ref index, hyp index)."""
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        back[i][0] = 1
        r = ref[i - 1]
        for j in range(1, m + 1):
            sub = prev[j - 1] + (r != hyp[j - 1])
            dele = prev[j] + 1
            ins = cur[j - 1] + 1
            best = min(sub, dele, ins)
            cur[j] = best
            back[i][j] = 0 if best == sub else (1 if best == dele else 2)
        prev = cur
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        b = back[i][j]
        if i > 0 and j > 0 and b == 0:
            ops.append(("ok" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and (j == 0 or b == 1):
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    return ops[::-1]


def error_rate(ops) -> float:
    errors = sum(1 for op, _, _ in ops if op != "ok")
    total = sum(1 for op, _, _ in ops if op != "ins")
    return errors / max(1, total)


def char_error_rate(ref: list[str], hyp: list[str]) -> float:
    a, b = " ".join(ref), " ".join(hyp)
    try:
        from rapidfuzz.distance import Levenshtein
        return Levenshtein.distance(a, b) / max(1, len(a))
    except ImportError:
        pass
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j - 1] + (a[i - 1] != b[j - 1]), prev[j] + 1, cur[j - 1] + 1)
        prev = cur
    return prev[-1] / max(1, len(a))


def speaker_accuracy(pairs: list[tuple[str | None, str | None]]) -> float | None:
    """Share of aligned words whose speaker is right, with the best one-to-one renaming of the
    found speakers to the reference ones (greedy on co-occurrence counts)."""
    pairs = [(r, h) for r, h in pairs if r]
    if not pairs:
        return None
    counts: dict[tuple[str, str], int] = {}
    for r, h in pairs:
        if h:
            counts[(r, h)] = counts.get((r, h), 0) + 1
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for (r, h), _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if h not in mapping and r not in used:
            mapping[h] = r
            used.add(r)
    right = sum(1 for r, h in pairs if h and mapping.get(h) == r)
    return right / len(pairs)


@dataclass
class Score:
    wer: float
    cer: float
    speakers_found: int
    speaker_acc: float | None
    ops: list
    ref_words: list
    hyp_words: list


def score(ref: Transcript, hyp: Transcript) -> Score:
    rw, hw = norm_words(ref.words), norm_words(hyp.words)
    ops = align([n for n, _ in rw], [n for n, _ in hw])
    pairs = [(rw[i][1].speaker, hw[j][1].speaker) for op, i, j in ops if op in ("ok", "sub")]
    acc = speaker_accuracy(pairs) if hyp.speakers else None
    return Score(error_rate(ops), char_error_rate([n for n, _ in rw], [n for n, _ in hw]), len(hyp.speakers), acc,
                 ops, rw, hw)


# ---- downloads and tools -----------------------------------------------------------------------

def say(msg: str) -> None:
    print(msg, flush=True)


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"  letöltés: {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "dwyt-whisper-probe"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f, 1 << 20)
    tmp.replace(dest)
    return dest


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def fetch_audio(url: str, cache: Path) -> Path:
    import yt_dlp
    key = re.sub(r"[^A-Za-z0-9_-]", "_", url.rsplit("=", 1)[-1])[:40]
    wav = cache / f"{key}.wav"
    if wav.exists():
        return wav
    say(f"  hang letöltése: {url}")
    opts = {"format": "bestaudio/best", "outtmpl": str(cache / f"{key}.%(ext)s"), "quiet": True,
            "no_warnings": True, "ffmpeg_location": ffmpeg()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        src = Path(ydl.prepare_filename(info))
    subprocess.run([ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(wav)], check=True)
    return wav


def has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


def whisper_cli(cache: Path, given: str | None) -> tuple[str, str]:
    """(path to whisper-cli, which build). A given path wins; else the latest official release."""
    if given:
        return given, "megadott"
    exe = "whisper-cli.exe" if os.name == "nt" else "whisper-cli"
    found = shutil.which("whisper-cli")
    if os.name != "nt":
        if found:
            return found, "PATH"
        raise SystemExit("Linuxon/macOS-en add meg a whisper-cli helyét: --whisper-cli /út/whisper-cli")
    with urllib.request.urlopen(urllib.request.Request(RELEASES, headers={"User-Agent": "dwyt-whisper-probe"}),
                                timeout=30) as resp:
        release = json.load(resp)
    assets = [a for a in release.get("assets", []) if a["name"].endswith(".zip") and "x64" in a["name"]]
    order = (["cublas", "cuda"] if has_nvidia() else []) + ["blas", "bin-x64"]
    for want in order:
        for asset in assets:
            if want in asset["name"]:
                folder = cache / "whisper.cpp" / asset["name"][:-4]
                if not folder.exists():
                    z = download(asset["browser_download_url"], cache / "whisper.cpp" / asset["name"])
                    with zipfile.ZipFile(z) as zf:
                        zf.extractall(folder)
                for path in folder.rglob(exe):
                    probe = subprocess.run([str(path), "--help"], capture_output=True, check=False)
                    if probe.returncode == 0:
                        return str(path), f"{release.get('tag_name')} {asset['name']}"
    raise SystemExit("Nem találtam használható whisper.cpp-t; töltsd le kézzel, és add meg: --whisper-cli")


def backend_of(log: str) -> str:
    """The line of whisper.cpp's log that says what it ran on."""
    lines = log.splitlines()
    for pattern in (r"using \S+ backend", r"ggml_cuda_init|CUDA|Vulkan|Metal|BLAS"):
        hit = next((ln.strip() for ln in lines if re.search(pattern, ln)), "")
        if hit:
            return hit
    return ""


def transcribe(cli: str, model: Path, wav: Path, out: Path, language: str) -> tuple[list[Word], float, str]:
    """whisper.cpp with one word per segment (-ml 1 -sow): words with start and end."""
    base = out.with_suffix("")
    t0 = time.monotonic()
    proc = subprocess.run([cli, "-m", str(model), "-f", str(wav), "-l", language, "-ml", "1", "-sow", "-oj",
                           "-of", str(base)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False)
    took = time.monotonic() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"whisper.cpp hiba: {proc.stderr[-2000:]}")
    backend = backend_of(proc.stderr)
    data = json.loads(Path(f"{base}.json").read_text(encoding="utf-8", errors="replace"))
    words = []
    for seg in data.get("transcription", []):
        text = (seg.get("text") or "").strip()
        if not text or text.startswith("[_"):
            continue
        off = seg.get("offsets") or {}
        words.append(Word(text, None, off.get("from", 0) / 1000, off.get("to", 0) / 1000))
    return words, took, backend


def read_wav(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
        rate = w.getframerate()
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0, rate


def segmentation_model(cache: Path) -> Path:
    folder = cache / "sherpa"
    model = folder / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    if not model.exists():
        archive = download(SEGMENTATION, folder / Path(SEGMENTATION).name)
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(folder, filter="data")
            except TypeError:  # Python without extraction filters
                tf.extractall(folder)
    return model


def diarize(wav: Path, cache: Path, speakers: int | None,
            embedding: str = "3dspeaker") -> tuple[list[tuple[float, float, str]], float]:
    """sherpa-onnx speaker turns: (start, end, 'Szereplő N'), numbered by first appearance."""
    import sherpa_onnx
    seg = segmentation_model(cache)
    emb = download(EMBEDDINGS[embedding], cache / "sherpa" / Path(EMBEDDINGS[embedding]).name)
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speakers or -1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise RuntimeError("sherpa-onnx: hibás beállítás")
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples, rate = read_wav(wav)
    if rate != sd.sample_rate:
        raise RuntimeError(f"a hang {rate} Hz, a modell {sd.sample_rate} Hz-et vár")
    t0 = time.monotonic()
    result = sd.process(samples).sort_by_start_time()
    took = time.monotonic() - t0
    names: dict[int, str] = {}
    turns = []
    for r in result:
        names.setdefault(r.speaker, f"Szereplő {len(names) + 1}")
        turns.append((float(r.start), float(r.end), names[r.speaker]))
    return turns, took


def assign_speakers(words: list[Word], turns: list[tuple[float, float, str]]) -> list[Word]:
    """Each word gets the speaker of the turn it overlaps most (or the nearest turn)."""
    out = []
    for w in words:
        s, e = w.start or 0.0, w.end if w.end is not None else (w.start or 0.0)
        best, best_overlap, best_gap = None, 0.0, float("inf")
        for ts, te, who in turns:
            overlap = min(e, te) - max(s, ts)
            if overlap > best_overlap:
                best, best_overlap = who, overlap
            elif best_overlap <= 0:
                gap = ts - e if ts > e else s - te
                if gap < best_gap:
                    best, best_gap = who, gap
        out.append(Word(w.text, best, w.start, w.end))
    return out


# ---- report ------------------------------------------------------------------------------------

def pct(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.1f}%".replace(".", ",")


def diff_html(s: Score) -> str:
    parts = []
    for op, i, j in s.ops:
        if op == "ok":
            parts.append(html.escape(s.hyp_words[j][1].text))
        elif op == "sub":
            parts.append(f'<del>{html.escape(s.ref_words[i][1].text)}</del><ins>{html.escape(s.hyp_words[j][1].text)}</ins>')
        elif op == "del":
            parts.append(f"<del>{html.escape(s.ref_words[i][1].text)}</del>")
        else:
            parts.append(f"<ins>{html.escape(s.hyp_words[j][1].text)}</ins>")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("refs", nargs="+", type=Path, help="javított dwyt TXT-átiratok (viszonyítási alap)")
    ap.add_argument("--compare", nargs="*", type=Path, default=[], help="más átiratok ugyanarról (pl. WhisperX)")
    ap.add_argument("--models", default="large-v3-turbo,large-v3")
    ap.add_argument("--language", default="hu")
    ap.add_argument("--whisper-cli", help="a whisper-cli(.exe) helye, ha nem a hivatalos kiadást töltené le")
    ap.add_argument("--embeddings", default="3dspeaker,wespeaker", help="beszélőfelismerő modellek")
    ap.add_argument("--no-diarize", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("whisper-probe-out"))
    ap.add_argument("--cache", type=Path, default=Path("whisper-probe-cache"))
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    others = [parse_txt(p) for p in args.compare]
    cli, build = whisper_cli(args.cache, args.whisper_cli)
    say(f"whisper.cpp: {build} ({cli})")
    md = ["# whisper.cpp + sherpa-onnx próba", "",
          f"Gép: {platform.platform()} · {platform.processor() or ''} · NVIDIA: {'igen' if has_nvidia() else 'nem'}",
          f"whisper.cpp: {build}", ""]
    page = [("<!doctype html><meta charset='utf-8'><title>whisper probe</title><style>"
             "body{font:15px/1.6 system-ui;max-width:1100px;margin:24px auto;padding:0 16px}"
             "del{background:#fdd;color:#900}ins{background:#dfd;color:#060;text-decoration:none}"
             "details{margin:10px 0}</style><h1>whisper.cpp + sherpa-onnx próba</h1>")]
    for ref_path in args.refs:
        ref = parse_txt(ref_path)
        if not ref.url:
            say(f"{ref_path}: nincs URL: sor, kihagyom")
            continue
        say(f"\n== {ref_path.name}")
        wav = fetch_audio(ref.url, args.cache)
        with wave.open(str(wav), "rb") as w:
            duration = w.getnframes() / w.getframerate()
        variants: list[Transcript] = [t for t in others if t.url == ref.url]
        failures: list[str] = []
        backend = ""
        for v in variants:
            v.note = "meglévő átirat"
        # the speaker turns do not depend on the Whisper model: once per video
        turns: list[tuple[str, list, float]] = []
        if not args.no_diarize:
            for emb in [e.strip() for e in args.embeddings.split(",") if e.strip()]:
                for label, n in (("automatikus", None), ("ismert szám", len(ref.speakers) or None)):
                    say(f"  sherpa-onnx {emb} ({label}) …")
                    try:
                        found, dtook = diarize(wav, args.cache, n, emb)
                    except Exception as exc:  # noqa: BLE001 - one broken variant must not end the run
                        failures.append(f"sherpa-onnx {emb} ({label}): {exc}")
                        continue
                    turns.append((f"sherpa-onnx {emb}, {label}", found, dtook))
        for name in [m.strip() for m in args.models.split(",") if m.strip()]:
            say(f"  whisper.cpp {name} …")
            try:
                model = download(WHISPER_MODELS[name], args.cache / "models" / Path(WHISPER_MODELS[name]).name)
                words, took, backend_line = transcribe(cli, model, wav,
                                                       args.out / f"{ref_path.stem}.{name}.json", args.language)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"whisper.cpp {name}: {exc}")
                continue
            backend = backend or backend_line
            variants.append(Transcript(f"whisper.cpp {name}", ref.url, words, took, backend_line))
            for label, found, dtook in turns:
                variants.append(Transcript(f"whisper.cpp {name} + {label}", ref.url, assign_speakers(words, found),
                                           took + dtook, backend_line))
        minutes = f"{duration / 60:.1f}".replace(".", ",")
        md += [f"## {ref.name}", "",
               f"{ref.url} · {minutes} perc · {len(ref.speakers)} beszélő a javított átiratban", "",
               "| Változat | Szóhiba (WER) | Karakterhiba | Beszélők | Jó beszélő a szavaknál | Idő | Valós idő × |",
               "| --- | --- | --- | --- | --- | --- | --- |"]
        page.append(f"<h2>{html.escape(ref.name)}</h2><p>{html.escape(ref.url)}</p>")
        for v in variants:
            s = score(ref, v)
            speed = f"{duration / v.seconds:.1f}".replace(".", ",") if v.seconds else "–"
            took = f"{v.seconds:.0f} mp" if v.seconds else "–"
            md.append(f"| {v.name} | {pct(s.wer)} | {pct(s.cer)} | {s.speakers_found or '–'} | {pct(s.speaker_acc)} "
                      f"| {took} | {speed} |")
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", v.name)
            write_txt(v, args.out / f"{ref_path.stem}.{safe}.txt")
            page.append(f"<details><summary><b>{html.escape(v.name)}</b> – WER {pct(s.wer)}, beszélők "
                        f"{pct(s.speaker_acc)}</summary><p>{diff_html(s)}</p></details>")
        md += ["", f"GPU/backend: {backend or '–'}", ""]
        md += [f"Hiba: {f}" for f in failures] + ([""] if failures else [])
    (args.out / "report.md").write_text("\n".join(md), encoding="utf-8")
    (args.out / "report.html").write_text("\n".join(page), encoding="utf-8")
    say(f"\nKész: {args.out / 'report.md'} és {args.out / 'report.html'}")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
