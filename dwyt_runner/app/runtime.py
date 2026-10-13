"""The desktop app's moving parts: the worker thread, pairing, the test run (round 7c)."""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

from .. import activity, config, gpuswap, trial, worker
from ..protocol import Central, CentralError, Outdated, Unauthorized

log = logging.getLogger("dwyt-runner")


class Runtime:
    def __init__(self, start_worker: bool = True) -> None:
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.error = ""
        self.lock = threading.Lock()
        self.pairing: dict[str, Any] = {}
        self.trial: dict[str, Any] = {"running": False, "stage": "", "error": ""}
        self.closing = False
        self.machine: dict[str, Any] = {"m": None, "said": [], "at": 0.0, "scanning": False, "local_models": []}
        self.refresh_machine()  # the machine page then opens at once
        if start_worker:
            self.start_worker()

    # -- the worker --
    def start_worker(self) -> None:
        cfg = config.load()
        if self.closing or not cfg.paired or (self.thread and self.thread.is_alive()):
            return
        self.stop = threading.Event()
        self.error = ""
        self.thread = threading.Thread(target=self._work, args=(cfg, self.stop), daemon=True, name="worker")
        self.thread.start()

    def _work(self, cfg: config.Config, stop: threading.Event) -> None:
        try:
            worker.loop(cfg, stop)
        except Unauthorized:
            self.error = "A dwyt nem fogadja el a párosítást (visszavonták?). Párosítsd újra."
        except Outdated as exc:
            self.error = f"{exc} Frissítsd a futtatót."
        except Exception as exc:  # noqa: BLE001 - the window must say it, not die with it
            log.exception("worker stopped")
            self.error = f"A futtató leállt: {exc}"

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def settings_changed(self) -> None:
        """New settings take effect before the next job (never in the middle of one)."""
        worker.RELOAD.set()
        self.start_worker()

    def shutdown(self) -> None:
        self.closing = True
        self.stop.set()

    # -- pairing --
    def start_pairing(self, server: str, name: str) -> None:
        server = server.strip().rstrip("/")
        if not server.startswith(("http://", "https://")):
            server = "https://" + server
        central = Central(server)
        start = central.pair_start()
        with self.lock:
            self.pairing = {"server": server, "name": name.strip() or socket.gethostname(), "code": start["code"],
                            "expires": time.time() + int(start.get("expires_in", 600)), "state": "waiting",
                            "error": ""}
        threading.Thread(target=self._poll_pairing, args=(central, start["poll_id"]), daemon=True).start()

    def _poll_pairing(self, central: Central, poll_id: str) -> None:
        while time.time() < self.pairing.get("expires", 0):
            time.sleep(3)
            try:
                done = central.pair_poll(poll_id)
            except CentralError as exc:
                with self.lock:
                    self.pairing.update(state="error", error=str(exc))
                return
            if done:
                cfg = config.load()
                cfg.server, cfg.token, cfg.runner_id = self.pairing["server"], done["token"], done["runner_id"]
                cfg.name = self.pairing["name"] or done.get("name") or socket.gethostname()
                config.save(cfg)
                self.start_worker()
                with self.lock:
                    self.pairing.update(state="done", code="")
                return
        with self.lock:
            self.pairing.update(state="expired", code="")

    # -- the machine check, kept for the machine page --
    MACHINE_FRESH_S = 300

    def refresh_machine(self) -> bool:
        if self.machine["scanning"]:
            return False
        self.machine["scanning"] = True

        def scan() -> None:
            from .. import localllm, machine
            try:
                cfg = config.load()
                m = machine.scan(cfg)
                said = machine.changes(machine.load_snapshot(), m)
                machine.save_snapshot(m)
                models: list[str] = []
                server = localllm.server(cfg)
                if server and server.running():
                    try:
                        models = [x.name for x in server.models()]
                    except Exception:  # noqa: BLE001
                        models = []
                self.machine.update(m=m, said=said, at=time.time(), local_models=models)
            except Exception:  # noqa: BLE001
                log.exception("machine check")
            finally:
                self.machine["scanning"] = False
        threading.Thread(target=scan, daemon=True, name="machine-scan").start()
        return True

    def machine_view(self) -> dict[str, Any]:
        """The last scan at once; a fresh one starts in the background when it is old."""
        if self.machine["m"] is not None and time.time() - self.machine["at"] > self.MACHINE_FRESH_S:
            self.refresh_machine()
        return dict(self.machine)

    # -- the test run --
    def start_trial(self, llm_model: str = "") -> bool:
        if self.trial["running"]:
            return False
        self.trial = {"running": True, "stage": "indul", "error": ""}

        def run() -> None:
            try:
                trial.run(config.load(), "large-v3", lambda s: self.trial.update(
                    stage=activity.STAGE_LABELS.get(s, s)), llm_model)
            except Exception as exc:  # noqa: BLE001
                log.exception("test run")
                self.trial["error"] = str(exc)
            finally:
                self.trial["running"] = False
        threading.Thread(target=run, daemon=True, name="trial").start()
        return True

    # -- what the Status tab shows --
    def status(self) -> dict[str, Any]:
        cfg = config.load()
        cur = activity.current()
        if cur:
            cur["kind_label"] = activity.KIND_LABELS.get(cur.get("kind"), cur.get("kind") or "")
            cur["stage_label"] = activity.STAGE_LABELS.get(cur.get("stage"), cur.get("stage") or "")
            p = cur.get("progress") or {}
            cur["part"] = f"{p.get('part')}/{p.get('parts')}. rész" if p.get("parts") else ""
            cur["minutes"] = int((time.time() - cur.get("started", time.time())) // 60)
        recent = [dict(e, kind_label=activity.KIND_LABELS.get(e.get("kind"), e.get("kind") or ""))
                  for e in reversed(activity.history()[-8:])]
        swap = dict(gpuswap.STATE)
        with self.lock:
            pairing = dict(self.pairing)
        return {"paired": cfg.paired, "server": cfg.server, "name": cfg.name, "running": self.running(),
                "error": self.error, "current": cur, "recent": recent, "today": activity.today(),
                "swap": swap, "pairing": pairing}
