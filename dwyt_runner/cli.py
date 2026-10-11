"""dwyt-runner <command>

  pair SERVER [--name NAME]  pair with a dwyt central service (prints a code to approve there)
  run                        ask for work and do it, until stopped (Ctrl+C)
  config show                the settings (secrets masked)
  config set KEY VALUE       e.g. assemblyai_key, whisper_url, whisper_models, llm_url, llm_key,
                             llm_public_model, llm_private_model, allowed_domains (comma-separated, * = any)
  check                      tools, settings and the connection to the central service
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import sys
import threading
import time

from . import __version__, config, tools, work, worker
from .protocol import Central, CentralError, JobGone, Outdated, Unauthorized


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def pair(server: str, name: str | None) -> int:
    cfg = config.load()
    central = Central(server)
    try:
        start = central.pair_start()
    except (CentralError, Outdated) as exc:
        _out(f"Hiba: {exc}")
        return 1
    _out(f"\nPárosítókód: {start['code']}\n")
    _out(f"Nyisd meg a {server.rstrip('/')}/futtatok oldalt, és írd be ezt a kódot"
         f" ({start['expires_in'] // 60} percig érvényes). Várakozás a jóváhagyásra…")
    deadline = time.monotonic() + start["expires_in"]
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            done = central.pair_poll(start["poll_id"])
        except CentralError as exc:
            _out(f"Hiba: {exc}")
            return 1
        if done:
            cfg.server, cfg.token, cfg.runner_id = server.rstrip("/"), done["token"], done["runner_id"]
            cfg.name = name or done.get("name") or socket.gethostname()
            config.save(cfg)
            _out(f"Párosítva: {done.get('name')}. Beállítások: {config.path()}")
            _out("Indítás: dwyt-runner run")
            return 0
    _out("A kód lejárt; indítsd újra a párosítást.")
    return 1


def run() -> int:
    cfg = config.load()
    if not cfg.paired:
        _out("Előbb párosítsd: dwyt-runner pair https://KÖZPONT-CÍME")
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    _out(f"dwyt-runner {__version__}: munkát kérek innen: {cfg.server} (leállítás: Ctrl+C)")
    try:
        worker.loop(cfg, stop)
    except Unauthorized as exc:
        _out(f"A központ nem fogadja el a tokent ({exc}). Párosítsd újra: dwyt-runner pair {cfg.server}")
        return 2
    except Outdated as exc:
        _out(f"{exc} Frissítés: uv tool upgrade dwyt-runner")
        return 3
    return 0


def check() -> int:
    cfg = config.load()
    ok = True
    _out(f"dwyt-runner {__version__}, beállítások: {config.path()}")
    _out(f"eszközök: {json.dumps(tools.versions())}")
    _out(f"ffmpeg: {tools.ffmpeg()}")
    _out(f"deno: {tools.deno() or 'NINCS (a YouTube-letöltéshez kellhet)'}")
    caps = work.capabilities(cfg)
    _out(f"képességek: átírás {', '.join(caps['transcribe_modes'])}; összefoglaló: "
         f"{', '.join(caps['llm_roles']) or 'nincs beállítva (llm_url, llm_public_model)'}")
    if cfg.whisper_url:
        _out(f"helyi Whisper: {cfg.whisper_url} ({', '.join(cfg.whisper_models)}); nem hívom meg, mert "
             "a GPU-t elvenné az LLM-től")
    _out(f"engedélyezett tartományok: {', '.join(cfg.allowed_domains)}")
    if not cfg.paired:
        _out("párosítás: még nincs (dwyt-runner pair SERVER)")
        return 1
    try:
        central = Central(cfg.server, cfg.token)
        central.heartbeat(0)
    except Unauthorized:
        ok = False
        _out("központ: a token érvénytelen vagy visszavonták")
    except Outdated as exc:
        ok = False
        _out(f"központ: {exc}")
    except CentralError as exc:
        ok = False
        _out(f"központ: {exc}")
    except JobGone:  # job 0 does not exist: the token was accepted
        _out(f"központ: rendben ({cfg.server})")
    else:
        _out(f"központ: rendben ({cfg.server})")
    return 0 if ok else 1


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="dwyt-runner")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair")
    p.add_argument("server")
    p.add_argument("--name")
    sub.add_parser("run")
    sub.add_parser("check")
    c = sub.add_parser("config")
    csub = c.add_subparsers(dest="action", required=True)
    csub.add_parser("show")
    s = csub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "pair":
        sys.exit(pair(args.server, args.name))
    if args.cmd == "run":
        sys.exit(run())
    if args.cmd == "check":
        sys.exit(check())
    cfg = config.load()
    if args.action == "show":
        _out(json.dumps(config.masked(cfg), indent=2, ensure_ascii=False))
        return
    try:
        config.set_value(cfg, args.key, args.value)
    except (KeyError, ValueError):
        _out(f"Ismeretlen vagy hibás beállítás: {args.key}")
        sys.exit(1)
    config.save(cfg)
    _out("Mentve.")


if __name__ == "__main__":
    main()
