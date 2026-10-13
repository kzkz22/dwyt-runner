# Eszközök

## whisper_probe.py – whisper.cpp + sherpa-onnx próba (7b)

Összeveti a whisper.cpp-t (large-v3-turbo, large-v3) és a sherpa-onnx beszélő-szétválasztást egy kézzel
javított dwyt-átirattal (TXT-letöltés, benne az `URL:` sor), és ha megadod, a meglévő (WhisperX) átirattal is.

```powershell
# egyszer: uv telepítése (rendszergazdai jog nem kell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

uv run whisper_probe.py "JAVÍTOTT - videó.txt" --compare "EREDETI - videó.txt"
```

Mér:
- **szöveg:** whisper.cpp három beállítással (alap; beszédfelismeréssel, VAD; VAD + előző szöveg nélkül), szóhiba
  (WER), karakterhiba, futásidő;
- **beszélők** a legjobb szövegen: a sherpa-onnx két beszédszakasz-modellje (pyannote 3.0, reverb-v1) × három
  hangazonosító modellje (3D-Speaker, WeSpeaker, TitaNet) a valódi beszélőszámmal, utána a legjobb pár automatikus
  beszélőszámmal több küszöbbel. Két mérőszám: *jó beszélő* (egy-az-egyben) és *tisztaság* (a fölösleges beszélő
  nem hiba, mert névadással összevonható; a keveredés az).

Eredmény: `whisper-probe-out/report.md`, `report.html` (szavankénti eltérések), és minden változat TXT-ben. A
letöltések (`whisper-probe-cache/`) megmaradnak a következő futáshoz.

Kapcsolók: `--models large-v3-turbo,large-v3`, `--text-variants alap,vad,vad-mc0`, `--segmentations pyannote,reverb`,
`--embeddings 3dspeaker,wespeaker,titanet`, `--thresholds 0.5,0.7,0.9`, `--no-diarize`, `--whisper-cli ÚT` (a
whisper-cli.exe vagy a kicsomagolt kiadás mappája), `--language hu`.

NVIDIA-kártyánál a szkript a hivatalos CUDA-s kiadást tölti le. Vulkan-os (AMD/Intel videóvezérlős) Windows-kiadás
nincs, ott processzoron fut. Az aláíratlan `whisper-cli.exe`-t a Windows alkalmazásvezérlése letilthatja.
