"""Build the desktop packages (round 7c, decision 18.20): the Windows installer and the Linux AppImage.

    python packaging/build.py windows   # on Windows, with Inno Setup (iscc) on PATH or in its usual place
    python packaging/build.py appimage  # on Linux; downloads appimagetool

Both carry their own Python (the standalone CPython uv manages) with the runner and its [desktop,local]
extras installed into it; Linux also gets Qt for the window (pywebview[qt], decision 18.19). whisper.cpp
is not inside: the runner downloads the official release on first use, SHA-256 checked (decision 18.18).
The package kind is written into the bundled Python (dwyt-runner-package.json), so the runner knows how
to update itself. Output: dist/dwyt-runner-setup-<version>.exe or dist/dwyt-runner-<version>-x86_64.AppImage.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "package"
DIST = ROOT / "dist"
PYTHON = "3.13"
APPIMAGETOOL = ("https://github.com/AppImage/appimagetool/releases/download/continuous/"
                "appimagetool-x86_64.AppImage")


def run(*cmd: str, **kw) -> str:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, **kw).stdout.strip()


def version() -> str:
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in pyproject.toml")


def wheel() -> Path:
    found = sorted(DIST.glob(f"dwyt_runner-{version()}-py3-none-any.whl"))
    if not found:
        run("uv", "build", "--wheel", "--out-dir", str(DIST), cwd=ROOT)
        found = sorted(DIST.glob(f"dwyt_runner-{version()}-py3-none-any.whl"))
    return found[-1]


def bundled_python(target: Path) -> Path:
    """Copy uv's standalone CPython into target; returns its python executable."""
    run("uv", "python", "install", PYTHON)
    exe = Path(run("uv", "python", "find", "--managed-python", PYTHON))
    home = exe.parent if os.name == "nt" else exe.parent.parent
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(home, target, symlinks=True)
    for marker in target.rglob("EXTERNALLY-MANAGED"):  # it is ours now: packages go into it
        marker.unlink()
    return target / ("python.exe" if os.name == "nt" else "bin/python3")


def install_runner(python: Path, kind: str, extra: list[str]) -> None:
    whl = wheel()
    run("uv", "pip", "install", "--python", str(python), f"dwyt-runner[desktop,local] @ {whl.as_uri()}", *extra)
    # console scripts hold the build machine's absolute path: the package starts with `python -m` instead
    scripts = python.parent / "Scripts" if os.name == "nt" else python.parent
    for f in scripts.glob("*"):
        if f.name.startswith(("dwyt-runner", "yt-dlp", "deno", "keyring", "normalizer", "httpx", "pywebview")):
            f.unlink()
    prefix = run(str(python), "-c", "import sys; print(sys.prefix)")
    (Path(prefix) / "dwyt-runner-package.json").write_text(json.dumps({"kind": kind, "version": version()}),
                                                           encoding="utf-8")
    print(run(str(python), "-m", "dwyt_runner.cli", "--version"))


ICON_SCRIPT = """
import sys
from pathlib import Path
from dwyt_runner.app.logo import write_icons
write_icons(Path(sys.argv[1]))
"""


def icons(python: Path, out: Path) -> None:
    """The dwyt logo as .png and .ico (Pillow is in the bundled Python with the desktop extra)."""
    run(str(python), "-c", ICON_SCRIPT, str(out))


def iscc() -> str:
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs")):
        p = Path(base) / "Inno Setup 6" / "ISCC.exe"
        if p.is_file():
            return str(p)
    raise SystemExit("Inno Setup (ISCC.exe) not found")


def windows() -> Path:
    src = BUILD / "windows"
    if src.exists():
        shutil.rmtree(src)
    python = bundled_python(src / "python")
    install_runner(python, "inno", [])
    icons(python, src)
    (src / "bin").mkdir()
    (src / "bin" / "dwyt-runner.cmd").write_text('@"%~dp0..\\python\\python.exe" -m dwyt_runner.cli %*\r\n',
                                                 encoding="ascii")
    DIST.mkdir(exist_ok=True)
    run(iscc(), f"/DAppVersion={version()}", f"/DSrc={src}", f"/O{DIST}",
        str(ROOT / "packaging" / "windows" / "dwyt-runner.iss"))
    out = DIST / f"dwyt-runner-setup-{version()}.exe"
    print("built", out)
    return out


APPRUN = """#!/bin/sh
# dwyt runner AppImage: no arguments (or --hidden / --browser / --after) = the app with its window,
# anything else = the command line (dwyt-runner run, check, update ...)
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/python/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYWEBVIEW_GUI="${PYWEBVIEW_GUI:-qt}"
case "${1:-}" in
  ""|--hidden|--browser|--after) exec "$HERE/python/bin/python3" -m dwyt_runner.app.main "$@" ;;
  *) exec "$HERE/python/bin/python3" -m dwyt_runner.cli "$@" ;;
esac
"""

# Qt 6.5+ needs libxcb-cursor for X11, which many desktops do not have (Ubuntu 22.04): carried along
BUNDLE_LIBS = ("libxcb-cursor.so.0",)


def bundle_libs(lib_dir: Path) -> None:
    lib_dir.mkdir(parents=True, exist_ok=True)
    listing = run("ldconfig", "-p")
    for name in BUNDLE_LIBS:
        path = next((line.split("=>")[-1].strip() for line in listing.splitlines()
                     if line.strip().startswith(name + " ") and "x86-64" in line), "")
        if not path:
            raise SystemExit(f"{name} is missing on the build machine (apt install libxcb-cursor0)")
        shutil.copy(Path(path).resolve(), lib_dir / name)


DESKTOP = """[Desktop Entry]
Type=Application
Name=dwyt futtató
Comment=Átírás és összefoglaló a dwyt-nek, ezen a gépen
Exec=dwyt-runner
Icon=dwyt-runner
Categories=AudioVideo;Utility;
Terminal=false
"""


def appimage() -> Path:
    appdir = BUILD / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    appdir.mkdir(parents=True)
    python = bundled_python(appdir / "python")
    install_runner(python, "appimage", ["pywebview[qt]>=5"])
    bundle_libs(appdir / "usr" / "lib")
    (appdir / "AppRun").write_text(APPRUN, encoding="utf-8")
    (appdir / "AppRun").chmod(0o755)
    (appdir / "dwyt-runner.desktop").write_text(DESKTOP, encoding="utf-8")
    icons(python, BUILD / "icons")
    shutil.copy(BUILD / "icons" / "dwyt-runner.png", appdir / "dwyt-runner.png")
    tool = BUILD / "appimagetool"
    if not tool.exists():
        urllib.request.urlretrieve(APPIMAGETOOL, tool)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    DIST.mkdir(exist_ok=True)
    out = DIST / f"dwyt-runner-{version()}-x86_64.AppImage"
    run(str(tool), "--appimage-extract-and-run", "--no-appstream", str(appdir), str(out),
        env={**os.environ, "ARCH": "x86_64"})
    print("built", out)
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "windows":
        windows()
    elif what == "appimage":
        appimage()
    elif what == "wheel":
        print(wheel())
    else:
        raise SystemExit(__doc__)
