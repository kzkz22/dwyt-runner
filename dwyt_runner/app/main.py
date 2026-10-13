"""Start the desktop window: the worker in the background, the local page, a window (pywebview) or,
without it, the default browser."""
from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

log = logging.getLogger("dwyt-runner")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


def run(browser: bool = False) -> int:
    server, state, runtime = serve()
    try:
        webview = None
        if not browser:
            try:
                import webview  # pywebview: WebView2 on Windows, WebKitGTK / Qt on Linux
            except ImportError:
                log.info("pywebview is not installed: opening the browser instead")
        if webview is not None:
            webview.create_window("dwyt futtató", state.start_url, width=1120, height=860, min_size=(420, 520))
            webview.start()
        else:
            webbrowser.open(state.start_url)
            print(f"A futtató ablaka a böngészőben: {state.start_url}\nLeállítás: Ctrl+C")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.shutdown()
        server.should_exit = True
    return 0
