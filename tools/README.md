# Eszközök

## whisper_probe.py – whisper.cpp + sherpa-onnx próba (7b)

Összeveti a whisper.cpp-t (large-v3-turbo, large-v3) és a sherpa-onnx beszélő-szétválasztást egy kézzel
javított dwyt-átirattal (TXT-letöltés, benne az `URL:` sor), és ha megadod, a meglévő (WhisperX) átirattal is.

```powershell
# egyszer: uv telepítése (rendszergazdai jog nem kell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

uv run whisper_probe.py "JAVÍTOTT - videó.txt" --compare "EREDETI - videó.txt"
```

Mér: szóhiba (WER), karakterhiba, megtalált beszélők száma, a szavak hány százaléka kapta a jó beszélőt,
futásidő. Eredmény: `whisper-probe-out/report.md`, `report.html` (szavankénti eltérések), és minden változat
TXT-ben. A letöltések (`whisper-probe-cache/`, kb. 5 GB a két modellel) megmaradnak a következő futáshoz.

Kapcsolók: `--models large-v3-turbo`, `--embeddings 3dspeaker`, `--no-diarize`, `--whisper-cli ÚT`
(saját whisper.cpp), `--language hu`.
