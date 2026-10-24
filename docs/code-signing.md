# Code signing policy / Kódaláírási szabályzat

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

## What is signed

The Windows installer of the Skipwise runner (`skipwise-runner-setup-*.exe`) and every executable file it installs
that has no valid signature of its own: the bundled Python interpreter (`python.exe`, `pythonw.exe`, `Skipwise.exe`),
the native modules of the bundled open-source Python packages (`.pyd`, `.dll`) and the uninstaller. Files that already
carry a valid signature of their publisher (for example Microsoft's runtime libraries) keep it.

Every signed file comes from an automated build of this repository's source code on GitHub Actions
(`.github/workflows/release.yml`, `packaging/build.py`); nothing is signed by hand or from a developer machine.
The bundled components and their licenses: the runner itself (MIT), CPython (PSF License), and the open-source
packages listed in `pyproject.toml` with their dependencies (MIT, BSD, Apache-2.0, LGPL, MPL).

## Team roles

- Committers and reviewers: [kzkz22](https://github.com/kzkz22)
- Approvers: [kzkz22](https://github.com/kzkz22)

Every release signing request is approved by hand in SignPath. All team members use multi-factor authentication for
GitHub and SignPath.

## Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the user
or the person installing or operating it. In detail: it connects only to the Skipwise server the user pairs it with,
to the services the user configures (for example AssemblyAI or a language-model server, with the user's own keys),
to the video sites of the jobs the user submits, and, for updates the user can switch off, to GitHub (runner releases,
whisper.cpp) and PyPI (yt-dlp). The privacy policy of the Skipwise service:
[skipwise.app/adatvedelem](https://skipwise.app/adatvedelem).

---

## Magyarul

A Windows-telepítőt és minden általa telepített, saját aláírás nélküli futtatható fájlt (a beépített Python, a
`Skipwise.exe`, a nyílt forrású Python-csomagok natív moduljai, az eltávolító) a SignPath Foundation tanúsítványával
írjuk alá, ingyenesen, a SignPath.io szolgáltatásán keresztül. Csak a GitHub Actions automatikus buildjének
eredménye kerül aláírásra, kézi jóváhagyással. A futtató csak oda küld adatot, ahová a felhasználó kéri: a párosított
Skipwise-szerverre, a beállított szolgáltatásokhoz (AssemblyAI, nyelvi modell), a beküldött videók oldalaira, és a
kikapcsolható frissítéshez a GitHubra és a PyPI-ra.
