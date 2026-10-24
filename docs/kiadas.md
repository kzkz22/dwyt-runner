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

## Windows-kódaláírás (SignPath, 19.27)

Az Intelligens alkalmazásvezérlés (Smart App Control) és a céges házirendek minden betöltött EXE-t és DLL-t
ellenőriznek, ezért a telepítőn kívül minden fájlt aláírunk, amelyiknek nincs saját érvényes aláírása: a beépített
Pythont, a `Skipwise.exe`-t, a csomagok `.pyd`/`.dll` fájljait és az eltávolítót. A tanúsítvány a SignPath
Foundationé (nyílt forrású projekteknek ingyenes); a kiadóként „SignPath Foundation” látszik. A szabályzat:
[code-signing.md](code-signing.md).

Egy kiadás így megy (a `release` munkafolyamat `windows` feladata):

1. `packaging/build.py windows-folder`: a telepítendő mappa.
2. `windows-tosign`: az Inno Setup egy próbafordítással elkészíti az eltávolítót (`.e32`), a PowerShell megnézi, melyik
   fájlnak nincs érvényes aláírása, és ezek egy mappába kerülnek (`.pyd` → `.pyd.dll`, `.e32` → `.e32.exe`, mert a
   SignPath ezeket a kiterjesztéseket írja alá). GitHub-artifact: `windows-tosign`.
3. SignPath, „package” konfiguráció ([packaging/signpath/package.xml](../packaging/signpath/package.xml)): első
   jóváhagyás.
4. `windows-putback`: az aláírt fájlok vissza (mind érvényes kell legyen), `windows-installer --signed-uninstaller`: a
   telepítő az aláírt eltávolítóval.
5. SignPath, „installer” konfiguráció ([packaging/signpath/installer.xml](../packaging/signpath/installer.xml)):
   második jóváhagyás.
6. `windows-verify`: a telepítő és a mappa minden EXE/DLL/PYD fájlja érvényesen aláírt; a próbatelepítés az
   eltávolítót, a `Skipwise.exe`-t és a `python.exe`-t is megnézi.

A jóváhagyásra a munkafolyamat legfeljebb 4 órát vár lépésenként. Csak kiadáskor ír alá (új változatszám a `main`
ágon). Más build csak akkor, ha a `SIGNPATH_TEST_POLICY` változó be van állítva (a SignPath próbatanúsítványával,
jóváhagyás nélkül: a csővezeték kipróbálására). Ha a `SIGNPATH_API_TOKEN` titok vagy a `SIGNPATH_ORGANIZATION_ID`
változó hiányzik, a telepítő aláíratlan, mint eddig.

### Beállítás (egyszer)

1. **Jelentkezés:** <https://signpath.org/apply>. Projekt: Skipwise runner, repó: `https://github.com/kzkz22/skipwise-runner`,
   licenc MIT, a szabályzat: `docs/code-signing.md`. Írd meg, hogy a csomag a CPython-t és nyílt forrású
   Python-csomagokat is tartalmaz, és ezek aláíratlan bináris fájljait is alá kell írni (az Intelligens
   alkalmazásvezérlés miatt).
2. **Kétlépcsős belépés** a GitHubon (és később a SignPathnál) – a SignPath feltétele.
3. Elfogadás után a SignPath-ban (app.signpath.io):
   - Trusted Build Systems: a „GitHub.com” hozzáadása a szervezethez, és összekapcsolása a projekttel.
   - A SignPath GitHub App telepítése a `skipwise-runner` repóra (ha kérik).
   - A projekt slugja: `skipwise-runner` (ha más, a GitHubon a `SIGNPATH_PROJECT_SLUG` változóba írd).
   - Két artifact configuration: `package` és `installer`, a `packaging/signpath/` két XML-fájljának tartalmával.
   - Signing policy: `release-signing` (jóváhagyóként te), és ha van, `test-signing`.
   - Egy CI-felhasználó (vagy a saját fiókod) API-tokenje, „submitter” joggal mindkét policyhez.
4. A GitHubon (`skipwise-runner` → Settings → Secrets and variables → Actions):
   - Secret: `SIGNPATH_API_TOKEN` = az API-token.
   - Variables: `SIGNPATH_ORGANIZATION_ID` = a szervezet azonosítója (SignPath → Settings), szükség szerint
     `SIGNPATH_POLICY` (ha nem `release-signing`), `SIGNPATH_PROJECT_SLUG`, és a próbához `SIGNPATH_TEST_POLICY`
     = `test-signing`.
5. Próba: Actions → release → **Run workflow** a `main` ágon (a `SIGNPATH_TEST_POLICY`-val jóváhagyás nélkül végigmegy).
   Utána a következő kiadásnál két jóváhagyást kérsz a SignPath-ban (e-mailben érkezik a kérés).

## Kulcscsere

Új kulcspár, a titok cseréje a GitHubon, az új nyilvános kulcs a két helyre, és egy kiadás. A régi kulccsal aláírt kiadásokat az új futtató már nem fogadja el, a régi futtatók pedig az újat nem: azokat egyszer kézzel kell frissíteni.
