"""Start the runner's app at login (plan 15b, round 7c), without admin rights.

The system's own place is the only record, so what the window shows is always what will happen:
Windows: HKCU\\...\\CurrentVersion\\Run (value "dwyt-runner"); Linux: ~/.config/autostart/dwyt-runner.desktop
(XDG, read by GNOME, KDE, Xfce, Cinnamon...); macOS: ~/Library/LaunchAgents/hu.dwyt.runner.plist.
It starts `app --hidden`: only the tray icon (the window opens where there is no tray). On Windows the
console-less `dwyt-runner-app` is used, so no black window flashes up at every login.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "dwyt-runner"
LABEL = "hu.dwyt.runner"


def command(platform: str = sys.platform) -> list[str]:
    """The command line the login starts."""
    image = os.environ.get("APPIMAGE")
    if image and Path(image).is_file():  # the AppImage itself: its AppRun starts the app
        return [image, "--hidden"]
    here = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path()
    if platform == "win32":
        for d in (here, Path(sys.executable).parent, Path(sys.executable).parent / "Scripts"):
            exe = d / "dwyt-runner-app.exe"
            if exe.is_file():
                return [str(exe), "--hidden"]
        found = shutil.which("dwyt-runner-app")
        if found:
            return [found, "--hidden"]
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return [str(pyw if pyw.is_file() else Path(sys.executable)), "-m", "dwyt_runner.app.main", "--hidden"]
    for d in (here, Path(sys.executable).parent):
        exe = d / "dwyt-runner"
        if exe.is_file():
            return [str(exe), "app", "--hidden"]
    found = shutil.which("dwyt-runner")
    if found:
        return [found, "app", "--hidden"]
    return [sys.executable, "-m", "dwyt_runner.app.main", "--hidden"]


def _quote_win(parts: list[str]) -> str:
    return " ".join(f'"{p}"' if (" " in p or not p) else p for p in parts)


def _desktop_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / f"{NAME}.desktop"


def _plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _desktop_exec(parts: list[str]) -> str:
    # Desktop Entry spec: quote arguments with spaces; escape ", `, $ and \ inside quotes
    def q(p: str) -> str:
        if not any(c in p for c in ' \t"`$\\'):
            return p
        return '"' + p.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'
    return " ".join(q(p) for p in parts)


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def enabled(platform: str = sys.platform) -> bool:
    if platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, NAME)
            return True
        except OSError:
            return False
    if platform == "darwin":
        return _plist().is_file()
    f = _desktop_file()
    if not f.is_file():
        return False
    text = f.read_text(encoding="utf-8", errors="replace")
    return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text


def enable(platform: str = sys.platform) -> None:
    parts = command(platform)
    if platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _quote_win(parts))
        return
    if platform == "darwin":
        f = _plist()
        args = "".join(f"<string>{_xml(p)}</string>" for p in parts)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                     '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                     f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string>'
                     f'<key>ProgramArguments</key><array>{args}</array>'
                     '<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
        return
    f = _desktop_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("[Desktop Entry]\nType=Application\nName=Skipwise futtató\n"
                 "Comment=A Skipwise futtatója a tálcán\n"
                 f"Exec={_desktop_exec(parts)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
                 encoding="utf-8")


def disable(platform: str = sys.platform) -> None:
    if platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, NAME)
        except FileNotFoundError:
            pass
        return
    f = _plist() if platform == "darwin" else _desktop_file()
    f.unlink(missing_ok=True)


def set_enabled(on: bool, platform: str = sys.platform) -> None:
    (enable if on else disable)(platform)
