"""The tray icon (plan 15b, decision 18.7): the runner's state at a glance, the window on a click, and the
way to quit. Closing the window only hides it while the icon is there.

Colours: green = waiting for work, blue = working, orange = the GPU is being handed over between the
language model and Whisper (do not use the local models now), red = stopped with an error, grey = not
paired. Where no tray exists (some Linux desktops) the app runs on without it; a new start shows the window.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("dwyt-runner")

COLOURS = {"green": (34, 160, 90), "blue": (37, 99, 235), "orange": (234, 128, 20), "red": (210, 45, 45),
           "grey": (130, 130, 140)}
POLL_S = 2.0


def state_of(s: dict[str, Any]) -> tuple[str, str]:
    """(colour, tooltip) from Runtime.status()."""
    if not s.get("paired"):
        return "grey", "dwyt futtató – nincs párosítva"
    if s.get("error"):
        return "red", ("dwyt futtató – leállt: " + s["error"])[:120]
    if not s.get("running"):
        return "grey", "dwyt futtató – nem fut"
    if (s.get("swap") or {}).get("phase") in ("waiting", "swapping", "whisper"):
        return "orange", "dwyt futtató – modellcsere: most ne használd a helyi modelleket"
    cur = s.get("current") or {}
    if cur:
        label = cur.get("kind_label") or "munka"
        stage = cur.get("stage_label") or ""
        return "blue", f"dwyt futtató – {label}" + (f": {stage}" if stage else "")
    return "green", "dwyt futtató – vár a következő munkára"


def image(colour: str, size: int = 64):
    """A round icon in the given colour with a white 'd' (Pillow; drawn, so no image files to ship)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 3, size - 3), fill=COLOURS[colour] + (255,))
    w = max(3, size // 10)  # a simple 'd': a ring and a stem
    r0, r1 = int(size * 0.26), int(size * 0.62)
    c1 = int(size * 0.44)
    d.ellipse((r0, c1 - (r1 - r0) // 2 + size // 10, r1, c1 + (r1 - r0) // 2 + size // 10),
              outline=(255, 255, 255, 255), width=w)
    d.rectangle((r1 - w, int(size * 0.2), r1, int(size * 0.8) - w // 2), fill=(255, 255, 255, 255))
    return img


class Tray:
    """pystray icon kept in step with the runtime by a small polling thread."""

    def __init__(self, status: Callable[[], dict[str, Any]], show: Callable[[], None],
                 quit: Callable[[], None]) -> None:
        self.status, self.show, self.quit = status, show, quit
        self.icon = None
        self.colour = ""
        self.stopped = threading.Event()

    def start(self) -> bool:
        """True when a tray icon is up; False where there is none."""
        try:
            import pystray
        except Exception as exc:  # noqa: BLE001 - not installed, or no backend on this desktop
            log.info("no tray icon: %s", exc)
            return False
        colour, tip = state_of(self.status())
        menu = pystray.Menu(
            pystray.MenuItem("Ablak megnyitása", lambda *_: self.show(), default=True),
            pystray.MenuItem(lambda _item: self._tip(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Kilépés", lambda *_: self.quit()),
        )
        try:
            self.icon = pystray.Icon("dwyt-runner", image(colour), tip, menu)
            self.icon.run_detached()
        except Exception as exc:  # noqa: BLE001
            log.info("no tray icon: %s", exc)
            self.icon = None
            return False
        self.colour, self.tooltip = colour, tip
        threading.Thread(target=self._follow, daemon=True, name="tray").start()
        return True

    def _tip(self) -> str:
        return getattr(self, "tooltip", "").removeprefix("dwyt futtató – ").capitalize()

    def _follow(self) -> None:
        while not self.stopped.wait(POLL_S):
            try:
                self.update(self.status())
            except Exception:  # noqa: BLE001 - the icon must never take the app down
                log.debug("tray update", exc_info=True)

    def update(self, s: dict[str, Any]) -> None:
        colour, tip = state_of(s)
        if self.icon is None:
            return
        if colour != self.colour:
            if colour == "red":
                self.notify(tip.removeprefix("dwyt futtató – "))
            self.icon.icon = image(colour)
            self.colour = colour
        if tip != getattr(self, "tooltip", ""):
            self.icon.title = tip
            self.tooltip = tip
            self.icon.update_menu()

    def notify(self, text: str) -> None:
        try:
            if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", False):
                self.icon.notify(text, "dwyt futtató")
        except Exception:  # noqa: BLE001
            log.debug("tray notification", exc_info=True)

    def stop(self) -> None:
        self.stopped.set()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
