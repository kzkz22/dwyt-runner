"""dwyt-runner <command>

  pair SERVER [--name NAME]  pair with a dwyt central service (prints a code to approve there)
  run                        ask for work and do it, until stopped (Ctrl+C)
  app [--browser] [--hidden] the same with its window (Állapot, Működés, Gépem, Kulcsok) and tray icon;
                             a second start shows the running one's window; extra [desktop]
  autostart on|off|status    start the app (tray only) at login; no admin rights
  reset [--models] [--yes]   back to the start: login start off, settings and keys deleted (models kept)
  config show                the settings (secrets masked)
  config describe [--json]   every setting with its meaning (the window and the dwyt Settings page draw from it)
  config set KEY VALUE       e.g. assemblyai_key, whisper_url, whisper_models, llm_url, llm_key,
                             llm_public_model, llm_private_model, llm_ask_model, allowed_domains (comma-separated,
                             * = any),
                             watch_dirs (comma-separated folders), watch_mode, watch_language
  check                      tools, settings and the connection to the central service
  machine [--json]           what this machine can do: video card, memory, free space, local model server
  keys                       where the keys are kept, and whether they work (one test request each)
  trial [--model M] [--llm NAME]  test run: a 33 s sample transcribed and a short summary, timed; recommends a mode
  whisper prepare [--model M] download whisper.cpp, its model and the speaker models (built-in local Whisper)
  whisper try FILE [--model M] [--language hu] [--speakers N]
                             transcribe a file on this machine and print it (nothing goes to the central)
  llm status|start|pull NAME|load NAME|unload [NAME]
                             the local model server (local_llm = ollama | lmstudio)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time

from . import __version__, config, localllm, machine, tools, work, worker
from .errors import WorkError
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
        for line in machine.watch_at_start(cfg):
            _out(line)
    except Exception as exc:  # noqa: BLE001 - the quiet hardware check must never stop the work
        logging.debug("gépvizsgálat: %s", exc)
    try:
        worker.loop(cfg, stop)
    except Unauthorized as exc:
        _out(f"A központ nem fogadja el a tokent ({exc}). Párosítsd újra: dwyt-runner pair {cfg.server}")
        return 2
    except Outdated as exc:
        _out(f"{exc} Frissítés: uv tool upgrade dwyt-runner")
        return 3
    return 0


def app_running() -> bool:
    """The window app of this user answers on the port it wrote down."""
    try:
        info = json.loads((config.home() / "window.json").read_text(encoding="utf-8"))
        return machine.port_open(f"http://127.0.0.1:{int(info['port'])}")
    except (OSError, ValueError, KeyError, TypeError):
        return False


def reset_cmd(models: bool, yes: bool) -> int:
    from . import machine, reset
    if app_running():
        _out("A futtató ablaka fut: az alaphelyzetet ott kérd (Működés lap), vagy előbb lépj ki belőle.")
        return 1
    if not yes:
        what = "a beállítások, a kulcsok" + (" és a letöltött modellek" if models else "")
        answer = input(f"Törlődnek {what}; a modellek helye: {machine.models_dir()}. Mehet? (igen/nem) ")
        if answer.strip().lower() not in ("igen", "i", "yes", "y"):
            _out("Nem töröltem semmit.")
            return 1
    server = config.load().server
    for line in reset.run(models):
        _out(line)
    if server:
        _out(f"A dwyt-ben ({server}/futtatok) a régi futtatót vond vissza.")
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
    if cfg.watch_dirs:
        from . import watch
        _out(f"figyelt mappák: {', '.join(cfg.watch_dirs)} ({watch.options(cfg)})")
    local = localllm.server(cfg)
    if local:
        state = "fut" if local.running() else ("telepítve, nem fut (a futtató elindítja)" if local.installed()
                                               else "NINCS TELEPÍTVE")
        _out(f"helyi modellszerver: {local.title} ({local.url}): {state}")
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


def show_machine(as_json: bool) -> int:
    cfg = config.load()
    m = machine.scan(cfg)
    said = machine.changes(machine.load_snapshot(), m)
    machine.save_snapshot(m)
    a = machine.assess(m)
    if as_json:
        from dataclasses import asdict
        _out(json.dumps({"machine": asdict(m), "assessment": asdict(a), "changes": said}, ensure_ascii=False,
                        indent=2))
        return 0
    for line in said:
        _out(line + "\n")
    from . import trial
    last = trial.load()
    card = m.best_gpu.name if m.best_gpu else ""
    same_card = last is not None and last.gpu == card
    _out(machine.report(m, a, last.mode_reason if same_card else ""))
    if last:
        old = "" if same_card else f" · még a régi kártyával ({last.gpu or 'videókártya nélkül'})"
        _out("\nUtolsó p" + trial.report(last).split("\n", 1)[0][1:] + old)
        for x in last.lamps:
            _out(f"  {x.label}: {x.value}" + (f" · {x.note}" if x.note else ""))
    else:
        _out("\nPróbafuttatás még nem volt: dwyt-runner trial")
    return 0


def show_keys() -> int:
    from . import keystore
    cfg = config.load()
    _out(f"A kulcsok helye: {keystore.name()}")
    ok = True
    for label, present, check in (("AssemblyAI (felhős átírás)", cfg.assemblyai_key, keystore.check_assemblyai),
                                  ("LLM (összefoglaló)", cfg.llm_key or cfg.llm_url or cfg.local_llm,
                                   keystore.check_llm)):
        if not present:
            _out(f"  {label}: nincs beállítva")
            continue
        good, text = check(cfg)
        ok = ok and good
        _out(f"  {label}: {text}")
    _out(f"  Párosítás a központtal: {'megvan' if cfg.token else 'nincs'}")
    return 0 if ok else 1


def run_trial(model: str, llm_model: str = "") -> int:
    from . import trial
    last = [""]

    def stage(name: str) -> None:
        if name != last[0]:
            last[0] = name
            _out(f"  {name} …")
    _out("Próbafuttatás (az első alkalommal a modellek letöltése hosszabb) …")
    r = trial.run(config.load(), model, stage, llm_model)
    _out("\n" + trial.report(r))
    return 0 if not r.errors else 1


def local_llm(action: str, name: str | None) -> int:
    cfg = config.load()
    s = localllm.server(cfg)
    if s is None:
        _out("Nincs beállítva helyi modellszerver: dwyt-runner config set local_llm ollama (vagy lmstudio)")
        return 1
    try:
        if action == "status":
            if not s.running():
                _out(f"{s.title}: nem fut ({'telepítve' if s.installed() else 'nincs telepítve'}); "
                     "indítás: dwyt-runner llm start")
                return 1
            version = s.version()
            _out(f"{s.title}{' ' + version if version else ''}: fut ({s.url}); az összefoglalókhoz: {s.openai_url}")
            for m in s.models():
                size = f"{m.size_gb:.1f} GB".replace(".", ",") if m.size_gb else ""
                state = "betöltve" + (f", {m.vram_gb:.1f} GB a videókártyán".replace(".", ",") if m.vram_gb else "") \
                    if m.loaded else ""
                _out(f"  {m.name}  {size}  {state}".rstrip())
            return 0
        if action == "start":
            if s.ensure_running():
                _out(f"{s.title}: fut ({s.url})")
                return 0
            _out(f"{s.title} nincs telepítve ezen a gépen.")
            return 1
        if not s.ensure_running():
            _out(f"{s.title} nincs telepítve ezen a gépen.")
            return 1
        if action == "pull":
            last = [-1]

            def show(frac: float | None, status: str) -> None:
                if frac is None:
                    return
                pct = int(frac * 100)
                if pct >= last[0] + 5 or pct == 100:
                    last[0] = pct
                    _out(f"  {pct}%")
            _out(f"Letöltés: {name}")
            s.pull(name, show)
            _out("Kész.")
        elif action == "load":
            s.load(name)
            _out(f"Betöltve: {name}")
        elif action == "unload":
            done = [name] if name else s.unload_all()
            if name:
                s.unload(name)
            _out("Kiürítve: " + (", ".join(done) or "nem volt betöltött modell"))
    except localllm.LocalLlmError as exc:
        _out(f"Hiba: {exc}")
        return 1
    return 0


def whisper_cmd(action: str, model: str, path: str | None, language: str | None, speakers: int | None) -> int:
    from . import formatting, local, media, whispercpp
    cfg = config.load()
    last = [""]

    def stage(name: str) -> None:
        if name != last[0]:
            last[0] = name
            _out(f"  {name} …")
    try:
        if action == "prepare":
            _out(f"whisper.cpp: {whispercpp.whisper_cli(cfg, stage)}")
            _out(f"modell: {whispercpp.whisper_model(model, stage)}")
            _out(f"beszédfelismerés: {whispercpp.vad_model(stage)}")
            seg, emb = whispercpp.speaker_models(stage)
            _out(f"beszélők: {seg.name}, {emb.name}")
            _out("Kész. Bekapcsolás: dwyt-runner config set whisper_builtin igen")
            return 0
        cfg.whisper_builtin, cfg.whisper_url = True, ""
        if model not in cfg.whisper_models:
            cfg.whisper_models = [*cfg.whisper_models, model]

        class File(media.Fetch):
            def info(self):
                return {"title": os.path.basename(path), "duration": media.duration(path)}

            def get(self, workdir):
                return path
        started = time.monotonic()
        out = local.run(File(), {"model": model, "language": language, "diarize": True, "num_speakers": speakers},
                        cfg, stage)
        t = out["transcript"]
        _out(f"\n{t['model']} · {t['language']} · {len(t['speakers'])} beszélő · "
             f"{time.monotonic() - started:.0f} mp\n")
        for w in t["warnings"]:
            _out(f"Figyelem: {w}")
        for turn in formatting.turns(t["segments"]):
            _out(f"[{formatting.hms(turn['start'])}] {turn['speaker'] or ''}: {turn['text']}\n")
        return 0
    except WorkError as exc:
        _out(f"Hiba: {exc}")
        return 1


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
    ap = sub.add_parser("app")
    ap.add_argument("--browser", action="store_true", help="a böngészőben, saját ablak helyett")
    ap.add_argument("--hidden", action="store_true", help="csak a tálcaikon indul, az ablak nem nyílik meg")
    sub.add_parser("check")
    rp = sub.add_parser("reset")
    rp.add_argument("--models", action="store_true", help="a letöltött modelleket is")
    rp.add_argument("--yes", action="store_true", help="kérdés nélkül")
    asp = sub.add_parser("autostart")
    asp.add_argument("action", choices=["on", "off", "status"])
    mp = sub.add_parser("machine")
    mp.add_argument("--json", action="store_true")
    sub.add_parser("keys")
    tp = sub.add_parser("trial")
    tp.add_argument("--model", default="large-v3")
    tp.add_argument("--llm", default="", help="a helyi modellszerver ezen modelljével (alapból llm_private_model)")
    wp = sub.add_parser("whisper")
    wp.add_argument("action", choices=["prepare", "try"])
    wp.add_argument("file", nargs="?")
    wp.add_argument("--model", default="large-v3")
    wp.add_argument("--language")
    wp.add_argument("--speakers", type=int)
    lp = sub.add_parser("llm")
    lp.add_argument("action", choices=["status", "start", "pull", "load", "unload"])
    lp.add_argument("name", nargs="?")
    c = sub.add_parser("config")
    csub = c.add_subparsers(dest="action", required=True)
    csub.add_parser("show")
    dp = csub.add_parser("describe")
    dp.add_argument("--json", action="store_true")
    s = csub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not args.verbose:  # every HTTP request is not news
        logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "pair":
        sys.exit(pair(args.server, args.name))
    if args.cmd == "run":
        sys.exit(run())
    if args.cmd == "app":
        try:
            from .app import main as app_main
        except ImportError:
            _out("Az ablakhoz telepítsd a futtatót a desktop kiegészítővel: "
                 "uv tool install --force --python 3.13 \"dwyt-runner[desktop,local] @ "
                 "https://github.com/kzkz22/dwyt-runner/archive/refs/heads/main.zip\"")
            sys.exit(1)
        sys.exit(app_main.run(args.browser, args.hidden))
    if args.cmd == "reset":
        sys.exit(reset_cmd(args.models, args.yes))
    if args.cmd == "autostart":
        from . import autostart
        if args.action != "status":
            autostart.set_enabled(args.action == "on")
        on = autostart.enabled()
        _out(("Bejelentkezéskor elindul: " + " ".join(autostart.command())) if on
             else "Bejelentkezéskor nem indul el.")
        sys.exit(0)
    if args.cmd == "check":
        sys.exit(check())
    if args.cmd == "machine":
        sys.exit(show_machine(args.json))
    if args.cmd == "keys":
        sys.exit(show_keys())
    if args.cmd == "trial":
        sys.exit(run_trial(args.model, args.llm))
    if args.cmd == "whisper":
        if args.action == "try" and not args.file:
            parser.error("whisper try: add meg a fájlt")
        sys.exit(whisper_cmd(args.action, args.model, args.file, args.language, args.speakers))
    if args.cmd == "llm":
        if args.action in ("pull", "load") and not args.name:
            parser.error(f"llm {args.action}: add meg a modell nevét")
        sys.exit(local_llm(args.action, args.name))
    cfg = config.load()
    if args.action == "show":
        _out(json.dumps(config.masked(cfg), indent=2, ensure_ascii=False))
        return
    if args.action == "describe":
        from . import settings
        _out(json.dumps(settings.describe(), ensure_ascii=False, indent=2) if args.json else settings.summary())
        return
    try:
        config.set_value(cfg, args.key, args.value)
    except ValueError as exc:
        _out(f"Nem mentettem: {exc}")
        sys.exit(1)
    config.save(cfg)
    _out("Mentve.")
    if args.key in ("assemblyai_key", "llm_key") and args.value.strip():  # a test request right away
        from . import keystore
        good, text = (keystore.check_assemblyai if args.key == "assemblyai_key" else keystore.check_llm)(cfg)
        _out(f"Próba: {text}" + ("" if good else " (a kulcs mentve, de nézd meg)"))


if __name__ == "__main__":
    main()
