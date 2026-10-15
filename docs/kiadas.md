# Kiadás és aláírás

A futtató csak a projekt kulcsával aláírt kiadást telepíti magától (terv 18.20). A kiadást a `release` munkafolyamat készíti, amikor a `main` ágra új változatszám kerül: wheel, Windows-telepítő, Linux AppImage, `latest.json` (változat, fájlnevek, méretek, SHA-256) és annak minisign-aláírása.

## Az aláíró kulcs (egyszer)

A kulcspárt a tulajdonos hozza létre a saját gépén; a titkos fele soha nem kerül a repóba.

1. minisign telepítése: Windows `winget install jedisct1.minisign`, Debian/Ubuntu `sudo apt install minisign`, macOS `brew install minisign`.
2. Kulcspár, jelszó nélkül (a GitHub Actions nem tud jelszót beírni):
   ```
   minisign -G -W -p minisign.pub -s minisign.key
   ```
3. A titkos kulcs a GitHubra: a `dwyt-runner` repó → Settings → Secrets and variables → Actions → **New repository secret**. Név: `MINISIGN_SECRET_KEY`; érték: a `minisign.key` fájl **teljes** tartalma (mindkét sor).
4. A `minisign.key` fájlt tedd biztonságos helyre (például jelszókezelőbe), és töröld a gépről. Ha elveszik, új kulcs kell; ha kiszivárog, azonnal cseréld.
5. A nyilvános kulcs (`minisign.pub`, ez nem titok) két helyre kerül: `packaging/minisign.pub` (a munkafolyamat ezzel ellenőrzi az aláírást) és `dwyt_runner/release.py` `PUBLIC_KEY` (a fájl második sora). Ettől a változattól kezdve frissít a futtató magától.

## Egy kiadás

1. `pyproject.toml` és `dwyt_runner/__init__.py`: az új változatszám.
2. Commit és push a `main` ágra. Ha ehhez a változathoz még nincs kiadás, a `release` munkafolyamat kiadja (a `v0.11.0` címkét is ő hozza létre); címkét kézzel nem kell tenni.
3. A munkafolyamat felépíti és kipróbálja a csomagokat (a Windows-telepítőt csendben telepíti és eltávolítja), aláírja a `latest.json`-t, és közzéteszi a kiadást. A Futtatók oldal letöltőgombjai a változatszám nélküli másolatokra mutatnak (`dwyt-runner-setup.exe`, `dwyt-runner-x86_64.AppImage`).

Ha a `packaging/` mappa változik, de a változatszám nem, a munkafolyamat csak épít és kipróbál.

## Kulcscsere

Új kulcspár, a titok cseréje a GitHubon, az új nyilvános kulcs a két helyre, és egy kiadás. A régi kulccsal aláírt kiadásokat az új futtató már nem fogadja el, a régi futtatók pedig az újat nem: azokat egyszer kézzel kell frissíteni.
