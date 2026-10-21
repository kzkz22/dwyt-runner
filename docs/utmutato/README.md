# Útmutatók

A dwyt futtatója végzi a munkát (letöltés, felirat, átírás, összefoglaló, kérdések) a saját gépeden vagy szervereden; a kulcsaid nála maradnak. Válaszd ki, hová telepíted:

| Hová | Kinek | Útmutató |
| --- | --- | --- |
| Windows-gép | laikusoknak is: ablak, tálcaikon, a telepítő végigvezet | [Windows](windows.md) |
| Linux asztali gép | ugyanaz, AppImage-ként | [Linux asztal](linux-asztal.md) |
| Linux szerver (VPS, LXC, NAS) | egy paranccsal, beállítás a dwyt-ből | [Szerver](szerver.md) |

Mindegyikhez kell egy dwyt-fiók. A letöltések és a telepítőparancs a dwyt **Futtatók** oldalán, az „Új futtató” részen vannak.

**Melyik működési mód?** A telepítés közben a futtató megvizsgálja a gépet, és ajánl egyet:

- **Felhő:** az átírás az AssemblyAI-nál, az összefoglaló egy felhős nyelvi modellel készül, a saját kulcsaiddal. Kipróbálni elég egyetlen AssemblyAI-regisztráció (50 $ induló kredit, bankkártya nélkül): akkor az összefoglalót is az AssemblyAI írja, egy kis próba-modellel (a magyar szöveg gyengébb lehet); jobb minőséghez OpenRouter-kulcs vagy feltöltött AssemblyAI-egyenleg kell. Gyenge gépen is jó; a felhős átírás csak akkor indul, ha kifejezetten kéred, egyébként a YouTube saját feliratát használja.
- **Vegyes:** az átírás a gépeden (whisper.cpp), az összefoglaló a felhőben. Ajánlott 8–16 GB-os videókártyánál.
- **Saját gép:** minden a gépeden (az összefoglalót Ollama vagy LM Studio írja). Ingyenes, de a kis helyi modellek magyar összefoglalója gyengébb.
- **Egyéni:** például külön GPU-gép (dwyt-airouter, dwyt-gpu) vagy saját LiteLLM.

Bármelyik módnál igaz: a munka csak akkor halad, ha a futtató gépe be van kapcsolva.
