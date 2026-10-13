"""Start the desktop app: the worker in the background, the local page, a window (pywebview) or, without
it, the default browser, and the tray icon.

One app per user: the running one writes `window.json` (port + a random secret) next to the config; a
second start asks it to show its window and exits, so a shortcut click or a start at login never runs two
workers. Closing the window only hides it (plan 15b: background running, also where there is no tray);
the tray icon or a new start brings it back, "Kilépés" in the tray or in the window quits.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .. import config

log = logging.getLogger("dwyt-runner")
INSTANCE_HEADER = "X-Dwyt-Instance"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def instance_file() -> Path:
    return config.home() / "window.json"


def wake_running() -> bool:
    """True when another app of this user is running and was asked to show its window."""
    try:
        info = json.loads(instance_file().read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/ablak", method="POST", data=b"",
                                     headers={INSTANCE_HEADER: str(info["secret"]),
                                              "Host": f"127.0.0.1:{int(info['port'])}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a system proxy
        with opener.open(req, timeout=3) as r:
            return r.status == 204
    except Exception:  # noqa: BLE001 - no file, stale file, nobody listening: we are the first
        return False


def write_instance(port: int, secret: str) -> None:
    f = instance_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"port": port, "secret": secret, "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def remove_instance(secret: str) -> None:
    try:
        f = instance_file()
        if json.loads(f.read_text(encoding="utf-8")).get("secret") == secret:
            f.unlink()
    except Exception:  # noqa: BLE001
        pass


def serve(start_worker: bool = True, port: int = 0):
    """(uvicorn server, App state, Runtime) with the server running in a thread."""
    import uvicorn

    from .runtime import Runtime
    from .server import create
    port = port or free_port()
    runtime = Runtime(start_worker=start_worker)
    api, state = create(runtime, f"127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="window-server").start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    return server, state, runtime


def run(browser: bool = False, hidden: bool = False) -> int:
    if wake_running():
        print("A futtató már fut; előhoztam az ablakát.")
        return 0
    keep_login_start_current()
    server, state, runtime = serve()
    port = int(state.host.rsplit(":", 1)[1])
    write_instance(port, state.instance_secret)
    quitting = threading.Event()
    webview = None
    if not browser:
        try:
            import webview  # pywebview: WebView2 on Windows, WebKitGTK / Qt on Linux
        except ImportError:
            log.info("pywebview is not installed: opening the browser instead")
    window = None

    def show() -> None:
        if window is not None:
            window.show()
            try:
                window.restore()
            except Exception:  # noqa: BLE001 - not every backend has it
                pass
        else:
            webbrowser.open(state.new_start_url())

    def quit_app() -> None:
        quitting.set()
        if window is not None:
            window.destroy()

    from .tray import Tray
    tray = Tray(runtime.status, show, quit_app)
    state.on_show = show
    state.on_quit = quit_app
    try:
        if webview is not None:
            window = webview.create_window("dwyt futtató", state.start_url, width=1120, height=860,
                                           min_size=(420, 520), hidden=hidden)

            def closing() -> bool:
                if quitting.is_set():
                    return True
                window.hide()  # keeps working; the tray icon or a new start brings the window back
                return False

            window.events.closing += closing
            webview.start(func=lambda: tray.start() or (hidden and show()))  # no tray: never start unseen
        else:
            has_tray = tray.start()
            if not hidden or not has_tray:
                webbrowser.open(state.start_url)
            print(f"A futtató ablaka a böngészőben: {state.start_url}\n"
                  + "Kilépés: az ablak Kilépés gombjával" + (", a tálcaikon menüjéből" if has_tray else "")
                  + " vagy Ctrl+C")
            while not quitting.wait(1):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        tray.stop()
        runtime.shutdown()
        server.should_exit = True
        remove_instance(state.instance_secret)
    return 0


def keep_login_start_current() -> None:
    """After an update or a move the program may live elsewhere: point the login start at this one."""
    from .. import autostart
    try:
        if autostart.enabled():
            autostart.enable()
    except Exception:  # noqa: BLE001 - never a reason not to start
        log.debug("login start refresh", exc_info=True)


def entry() -> None:
    """`dwyt-runner-app`: the app without a console window (Windows: pythonw), logging to app.log."""
    import argparse
    import logging.handlers
    import sys
    ap = argparse.ArgumentParser(prog="dwyt-runner-app")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--hidden", action="store_true")
    args = ap.parse_args()
    if sys.stdout is None:  # pythonw: no console; some libraries (uvicorn) ask sys.stdout.isatty()
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(home / "app.log", maxBytes=1_000_000, backupCount=2,
                                                   encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(run(args.browser, args.hidden))


if __name__ == "__main__":
    entry()
