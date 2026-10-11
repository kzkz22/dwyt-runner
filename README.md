# dwyt-runner

A **dwyt** futtatója. A saját gépeden vagy VPS-eden fut (Linux, Windows, macOS), és kifelé kapcsolódik a dwyt központhoz ([dwyt-central](https://github.com/kzkz22/dwyt-central)): onnan kér munkát, és oda adja le az eredményt. Nyitott port, VPN vagy routerbeállítás nem kell.

Amit végez:

- **YouTube-felirat** letöltése (gyors, ingyenes, beszélők nélkül);
- **felhős átírás** az AssemblyAI-jal, a **saját kulcsoddal** (a költség a tiéd);
- **helyi átírás beszélőkkel** a saját GPU-don (Whisper; ehhez [dwyt-airouter](https://github.com/kzkz22/dwyt-airouter) és [dwyt-gpu](https://github.com/kzkz22/dwyt-gpu) kell);
- **magyar összefoglaló és fordítás** bármely OpenAI-kompatibilis szerverrel: LiteLLM, OpenRouter, llama-server, Ollama.

A kulcsaid csak itt, a gépeden vannak; a központ soha nem látja őket. A YouTube a te IP-címedről, a te sávszélességeddel töltődik.

- Terv és döntések: [dwyt-docs/docs/terv.md](https://github.com/kzkz22/dwyt-docs/blob/main/docs/terv.md) (8. fejezet)
- Protokoll: [dwyt-docs/docs/protokoll.md](https://github.com/kzkz22/dwyt-docs/blob/main/docs/protokoll.md)

## Telepítés

Az [`uv`](https://docs.astral.sh/uv/) csomagkezelővel, rendszergazdai jog nélkül; a Pythont, az ffmpeg-et és a YouTube-hoz kellő `deno`-t is hozza.

1. Az `uv` telepítése (egyszer):
   - **Windows** (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - **macOS, Linux**: `curl -LsSf https://astral.sh/uv/install.sh | sh`

   Utána nyiss új terminált.
2. A futtató: `uv tool install git+https://github.com/kzkz22/dwyt-runner`
3. Párosítás: `dwyt-runner pair https://dwyt.iodate.date`. Kiír egy kódot; a központ **Futtatók** oldalán írd be, és adj nevet a futtatónak.
4. Kulcsok és modellek (csak ami kell):

   ```
   dwyt-runner config set assemblyai_key KULCS            # felhős átírás
   dwyt-runner config set llm_url https://openrouter.ai/api/v1
   dwyt-runner config set llm_key KULCS
   dwyt-runner config set llm_public_model MODELLNÉV      # nyilvános videók összefoglalója
   dwyt-runner config set llm_private_model MODELLNÉV     # saját felvételekhez: helyi modell javasolt
   dwyt-runner config set llm_translate_model MODELLNÉV   # (nem kötelező) fordításhoz más modell a nyilvános videóknál
   ```

   Saját LiteLLM-mel (például a dwyt-airouterrel) az `llm_url` a LiteLLM címe, a modellnevek az ott beállított nevek.

   Ha a központ elemoldalán más modellt is választhatnál egy összefoglalóhoz vagy fordításhoz, sorold fel őket (nem kötelező):

   ```
   dwyt-runner config set llm_models cloud/okos,osszefoglalo-gyors   # nyilvános videókhoz
   dwyt-runner config set llm_private_models helyi-nagy              # saját felvételekhez (csak amit ide engedsz)
   ```

   A futtató csak ezeket (és a fenti alapmodelleket) fogadja el; saját felvétel szövege más modellhez nem kerül.

   Helyi Whisper (saját GPU, az AI-router llama-swapján át):

   ```
   dwyt-runner config set whisper_url http://AI-ROUTER-IP:8080/upstream/whisper
   dwyt-runner config set whisper_models large-v3,large-v3-turbo   # ami a GPU-gépen van
   ```

   Ha a GPU-gép épp nem érhető el, a helyi munka vár, és később újra próbálkozik. Felhőre nem vált át. A `whisper_timeout_min` (alapból 60) egy munka leghosszabb ideje; hosszú felvételnél a hossz négyszerese.
5. Ellenőrzés: `dwyt-runner check`
6. Indítás: `dwyt-runner run` (leállítás: Ctrl+C). A képességeit (melyik mód, melyik modell) induláskor jelenti be, ezért beállításváltozás után indítsd újra.

A beállítások helye: Windowson `%APPDATA%\dwyt-runner\config.json`, macOS-en `~/Library/Application Support/dwyt-runner/config.json`, Linuxon `~/.config/dwyt-runner/config.json`. A fájlt csak te olvashatod; a `config show` a kulcsokat elrejtve mutatja.

## Saját felvételek

Két úton:

- **Figyelt mappa** (ajánlott): a futtató a megadott mappák új hang- és videófájljait magától átíratja. A fájl el sem hagyja a gépedet; a központ csak az átiratot kapja. A fájlt a futtató nem mozgatja, nem módosítja és nem törli; egy fájlt egyszer dolgoz fel (a tartalma szerint).

  ```
  dwyt-runner config set watch_dirs "/home/zoli/Felvételek"        # több mappa vesszővel elválasztva
  dwyt-runner config set watch_mode local                          # local (alapból) vagy cloud
  dwyt-runner config set watch_language hu                         # üresen felismeri
  ```

  Utána indítsd újra a futtatót. A mappákat percenként nézi át; egy fájlt akkor vesz fel, ha már nem változik (a másolás befejeződött). Windowson a mappa például `C:\Users\Zoli\Felvételek`.
- **Feltöltés a böngészőből** a központ „Új átirat” oldalán. A fájl csak addig van a központon, amíg a futtatód le nem töltötte; a futtató a tartalmát ellenőrzi (SHA-256), mielőtt átíratja. A legnagyobb elfogadott méret: `max_file_mb` (alapból 4096).

Mindkét esetben a felvétel „saját”: más felhasználó nem kaphatja meg az eredményét, és az összefoglaló meg a fordítás csak az `llm_private_model` modellel készül.

## Mely oldalakról tölthet le

Alapból csak a YouTube-ról (`youtube.com`, `youtu.be`). Ha a központtól más oldalra szóló munka jönne, a futtató visszautasítja. Bővítés: `dwyt-runner config set allowed_domains youtube.com,youtu.be,vimeo.com` (`*` = bármi).

## Futás a háttérben

- **Linux szerveren, LXC-ben, VPS-en (rendszerszolgáltatás, ez az ajánlott):**
  ```bash
  wget -O /etc/systemd/system/dwyt-runner.service \
    https://raw.githubusercontent.com/kzkz22/dwyt-runner/main/deploy/dwyt-runner-system.service
  systemctl daemon-reload
  systemctl enable --now dwyt-runner
  systemctl status dwyt-runner        # napló: journalctl -u dwyt-runner -f
  ```
  A fájl rootként futtatja a programot (`/root/.local/bin/dwyt-runner`, beállítások: `/root/.config/dwyt-runner`). Ha másik felhasználóval telepítettél és párosítottál, a `User=`, `Environment=HOME=` és `ExecStart=` sort írd át az ő adataira.
- **Linux asztali gépen (felhasználói szolgáltatás, root nélkül):**
  ```bash
  mkdir -p ~/.config/systemd/user
  wget -O ~/.config/systemd/user/dwyt-runner.service \
    https://raw.githubusercontent.com/kzkz22/dwyt-runner/main/deploy/dwyt-runner.service
  systemctl --user daemon-reload
  systemctl --user enable --now dwyt-runner
  loginctl enable-linger $USER        # hogy kijelentkezés után is fusson
  systemctl --user status dwyt-runner # napló: journalctl --user -u dwyt-runner -f
  ```
  Itt minden parancsban kell a `--user`; a `--user` nélküli `systemctl` a rendszerszolgáltatást keresi, és „could not be found” hibát ad.
- **Windows:** Feladatütemező → Alapfeladat létrehozása → „Bejelentkezéskor” → program: `dwyt-runner`, argumentum: `run`.
- **macOS:** egyelőre egy terminálablakban: `dwyt-runner run`.

## Frissítés

`uv tool install --force git+https://github.com/kzkz22/dwyt-runner` (a GitHubról mindig a legfrissebbet telepíti; a `dwyt-runner --version` mutatja a változatot), utána indítsd újra a futtatót. A yt-dlp-t az `uv tool upgrade dwyt-runner` is frissíti; a YouTube gyakran változik, ezért ha a letöltés hibázni kezd, először ezt érdemes futtatni. Ha a központ új protokollt kér, a futtató kiírja, és leáll.

## Fejlesztés

```bash
uv venv && uv pip install -e ".[test]"
uv run pytest -q
```

A tesztek nem érnek el semmilyen külső szolgáltatást: a központ, a yt-dlp, az AssemblyAI és az LLM is utánzat (`tests/`).

## Licenc

MIT, lásd [LICENSE](LICENSE).
