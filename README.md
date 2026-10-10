# dwyt-runner

A **dwyt** futtatója. A saját gépeden vagy VPS-eden fut (Linux, Windows, macOS), és kifelé kapcsolódik a dwyt központhoz ([dwyt-central](https://github.com/kzkz22/dwyt-central)): onnan kér munkát, és oda adja le az eredményt. Nyitott port, VPN vagy routerbeállítás nem kell.

Amit végez:

- **YouTube-felirat** letöltése (gyors, ingyenes, beszélők nélkül);
- **felhős átírás** az AssemblyAI-jal, a **saját kulcsoddal** (a költség a tiéd);
- **magyar összefoglaló** bármely OpenAI-kompatibilis szerverrel: LiteLLM, OpenRouter, llama-server, Ollama.

A kulcsaid csak itt, a gépeden vannak; a központ soha nem látja őket. A YouTube a te IP-címedről, a te sávszélességeddel töltődik. Helyi Whisper (saját GPU) a 2. körben jön.

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
   ```

   Saját LiteLLM-mel (például a dwyt-airouterrel) az `llm_url` a LiteLLM címe, a modellnevek az ott beállított nevek.
5. Ellenőrzés: `dwyt-runner check`
6. Indítás: `dwyt-runner run` (leállítás: Ctrl+C)

A beállítások helye: Windowson `%APPDATA%\dwyt-runner\config.json`, macOS-en `~/Library/Application Support/dwyt-runner/config.json`, Linuxon `~/.config/dwyt-runner/config.json`. A fájlt csak te olvashatod; a `config show` a kulcsokat elrejtve mutatja.

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

`uv tool upgrade dwyt-runner`. Ez a yt-dlp-t is frissíti; a YouTube gyakran változik, ezért ha a letöltés hibázni kezd, először ezt érdemes futtatni. Ha a központ új protokollt kér, a futtató kiírja, és leáll.

## Fejlesztés

```bash
uv venv && uv pip install -e ".[test]"
uv run pytest -q
```

A tesztek nem érnek el semmilyen külső szolgáltatást: a központ, a yt-dlp, az AssemblyAI és az LLM is utánzat (`tests/`).

## Licenc

MIT, lásd [LICENSE](LICENSE).
