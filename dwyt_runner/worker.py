"""The main loop: ask the central service for work, run it, report back.

A heartbeat thread renews the job's lease while it runs and notices a cancellation. Results
are delivered with retries (the central service accepts a repeated delivery once).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import activity, modelinfo, tools, watch, work
from .config import Config
from .errors import WorkError
from .protocol import Central, CentralError, JobGone

log = logging.getLogger("dwyt-runner")
HEARTBEAT_S = 60
DELIVERY_DELAYS = (2, 5, 15, 30, 60, 120, 300, 600)
WATCH_INTERVAL_S = 60
RELOAD = threading.Event()  # the window saved new settings: read them before the next claim (never mid-job)


class Job:
    def __init__(self, central: Central, job: dict[str, Any]) -> None:
        self.central = central
        self.id = job["job_id"]
        self.data = job
        self.stage_name = "starting"
        self.progress_data: dict[str, Any] | None = None
        self.cancel = threading.Event()
        self.gone = threading.Event()
        self.done = threading.Event()
        self._last_beat = 0.0
        self.interval = min(HEARTBEAT_S, max(5, int(job.get("lease_s", 900)) / 3))

    def beat(self) -> None:
        try:
            answer = self.central.heartbeat(self.id, self.stage_name, self.progress_data)
            self._last_beat = time.monotonic()
            if answer.get("cancel"):
                self.cancel.set()
        except JobGone:
            self.gone.set()
            self.cancel.set()
        except CentralError as exc:
            log.warning("heartbeat failed: %s", exc)

    def stage(self, name: str) -> None:
        self.stage_name = name
        activity.stage(name)
        log.info("job %s: %s", self.id, name)
        if time.monotonic() - self._last_beat > 5:
            self.beat()

    def progress(self, data: dict[str, Any]) -> None:
        """Parts done of a long LLM job (shown on the item page); sent with the heartbeats."""
        self.progress_data = data
        activity.progress(data)
        if time.monotonic() - self._last_beat > 5:
            self.beat()

    def heartbeats(self) -> None:
        while not self.done.wait(self.interval):
            self.beat()


def deliver(central: Central, call, *args, stop: threading.Event | None = None) -> bool:
    for delay in (*DELIVERY_DELAYS, None):
        try:
            call(*args)
            return True
        except JobGone as exc:
            log.warning("the job is no longer ours: %s", exc)
            return False
        except CentralError as exc:
            if delay is None:
                log.error("could not report to the central service: %s", exc)
                return False
            log.warning("could not report (%s); retrying in %s s", exc, delay)
            if stop is not None and stop.wait(delay):
                return False
            if stop is None:
                time.sleep(delay)
    return False


def run_job(central: Central, cfg: Config, job_data: dict[str, Any], stop: threading.Event | None = None) -> str:
    job = Job(central, job_data)
    log.info("job %s: %s", job.id, job_data.get("kind"))
    activity.start(job_data, cfg)
    outcome, error = "crashed", ""
    beats = threading.Thread(target=job.heartbeats, daemon=True)
    beats.start()
    try:
        result = work.run(job_data, cfg, job.stage, job.cancel.is_set, central, progress=job.progress)
        if job.gone.is_set():
            outcome = "gone"
            return "gone"
        if job.cancel.is_set():
            raise WorkError("megszakítva")
        job.stage("write")
        deliver(central, central.result, job.id, result, stop=stop)
        outcome = "done"
        return "done"
    except WorkError as exc:
        log.warning("job %s failed: %s", job.id, exc)
        outcome, error = "failed", str(exc)
        if not job.gone.is_set():
            deliver(central, central.fail, job.id, str(exc), exc.retry, stop=stop)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - report every crash instead of dying
        log.exception("job %s crashed", job.id)
        if not job.gone.is_set():
            deliver(central, central.fail, job.id, f"A futtató hibára futott: {type(exc).__name__}: {exc}", "later",
                    stop=stop)
        return "failed"
    finally:
        job.done.set()
        activity.finish(outcome, error)


def sync_settings(cfg: Config, central: Central, take: bool = False) -> Config:
    """Report the settings (round 7d) and, when changes wait, apply them. Never stops the work: a failure is
    logged and tried again at the next report; a finished update leaves the loop (remote.RestartNow)."""
    from . import remote
    try:
        if remote.report(cfg, central) or take:
            if remote.enabled(cfg):
                return remote.handle(cfg, central)
    except remote.RestartNow:
        raise
    except CentralError as exc:
        log.warning("settings report: %s", exc)
    except Exception:  # noqa: BLE001 - e.g. the key file cannot be written
        log.exception("settings report failed")
    return cfg


def loop(cfg: Config, stop: threading.Event, central: Central | None = None, once: bool = False) -> None:
    """Ask for work until stopped. Unauthorized/Outdated propagate: the user must act."""
    central = central or Central(cfg.server, cfg.token)
    RELOAD.clear()  # cfg is the current settings; only changes from now on count
    caps = work.capabilities(cfg)
    versions = tools.versions()
    log.info("capabilities: %s", caps)
    wait = 2
    last_scan = 0.0
    last_caps = time.monotonic()
    from . import remote
    cfg = sync_settings(cfg, central)
    last_report = time.monotonic()
    while not stop.is_set():
        if RELOAD.is_set():
            RELOAD.clear()
            from . import config
            cfg = config.load()
            caps = work.capabilities(cfg)
            last_caps = time.monotonic()
            log.info("settings reloaded; capabilities: %s", caps)
            cfg = sync_settings(cfg, central)  # a change made in the window: dwyt shows it (and logs it)
            last_report = time.monotonic()
        if time.monotonic() - last_report >= remote.REPORT_EVERY_S:
            last_report = time.monotonic()
            cfg = sync_settings(cfg, central)
        if time.monotonic() - last_caps >= modelinfo.REFRESH_S:  # what the model names mean may change
            last_caps = time.monotonic()
            caps = work.capabilities(cfg)
        if cfg.watch_dirs and time.monotonic() - last_scan >= WATCH_INTERVAL_S:
            last_scan = time.monotonic()
            try:
                watch.scan(cfg, central)
            except (OSError, CentralError) as exc:
                log.warning("watched folders: %s", exc)
        from . import ytdlp_update
        if ytdlp_update.due(cfg.ytdlp_auto_update):  # idle here: a newer yt-dlp is installed between jobs
            ytdlp_update.run_if_due(cfg.ytdlp_auto_update)
            versions = tools.versions()
        from . import updater
        if updater.due(cfg):  # the runner itself: signed releases only (7c)
            updater.run_if_due(cfg)
        try:
            job = central.claim(caps, versions)
            wait = 2
        except CentralError as exc:
            log.warning("%s; retrying in %s s", exc, wait)
            stop.wait(wait)
            wait = min(wait * 2, 60)
            continue
        if job is not None and stop.is_set():  # stopped while asking (quit, reset): hand it back at once
            try:
                central.fail(job["job_id"], "A futtató leállt, mielőtt elkezdte; később újra kiadható.", "later")
            except CentralError as exc:
                log.warning("job %s not handed back: %s", job.get("job_id"), exc)
            return
        if job is None and getattr(central, "settings_pending", False):  # the owner changed something on the dwyt page
            cfg = sync_settings(cfg, central, take=True)
            caps = work.capabilities(cfg)
            last_report = last_caps = time.monotonic()
        if job is not None:
            run_job(central, cfg, job, stop)
            if once:
                return
        elif once:
            return
