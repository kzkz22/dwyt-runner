"""The desktop window's local web page (plan 15b, round 7c): Állapot, Működés, Gépem, Kulcsok.

Only for this machine's user: it listens on 127.0.0.1, answers only to the Host it was started with
(no DNS rebinding), and lets in only the window that opened it: the start URL carries a one-time code that
becomes an HttpOnly, SameSite=Strict session cookie. Forms carry a CSRF token. No inline script or
style (strict CSP), like the central's pages. Keys can be written and tried here, never read back.
"""
from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__, autostart, config, keystore, machine, modes, settings, trial
from .runtime import Runtime

HERE = Path(__file__).parent
COOKIE = "dwyt_runner_session"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
SECRET_LABELS = {"assemblyai_key": "AssemblyAI-kulcs (felhős átírás)", "llm_key": "LLM-kulcs (felhős összefoglaló)",
                 "lmstudio_token": "LM Studio API-token"}


class App:
    def __init__(self, runtime: Runtime, host: str) -> None:
        self.runtime = runtime
        self.host = host                       # "127.0.0.1:PORT": the only Host header answered
        self.launch_code = secrets.token_urlsafe(24)
        self.instance_secret = secrets.token_urlsafe(24)  # a second start of the app shows this window
        self.on_show = lambda: None
        self.on_quit = lambda: None
        self.sessions: dict[str, str] = {}     # session id -> CSRF token
        self.env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
        self.env.globals.update(version=__version__, lamp_text=machine.LAMP_TEXT)

    @property
    def start_url(self) -> str:
        return f"http://{self.host}/belepes?t={self.launch_code}"

    def new_start_url(self) -> str:
        """A fresh one-time entry (the browser mode's tray "open" opens a new tab with it)."""
        self.launch_code = secrets.token_urlsafe(24)
        return self.start_url

    async def form(self, request: Request):
        """The posted form, after checking its CSRF token against the session's."""
        form = await request.form()
        expected = self.sessions.get(request.cookies.get(COOKIE, ""), "")
        if not expected or not secrets.compare_digest(str(form.get("csrf", "")), expected):
            raise HTTPException(403, "Lejárt űrlap; töltsd újra az oldalt.")
        return form

    def render(self, request: Request, name: str, **ctx: Any) -> HTMLResponse:
        sid = request.cookies.get(COOKIE, "")
        ctx.update(csrf=self.sessions.get(sid, ""), tab=name.split(".")[0], cfg=config.load())
        return HTMLResponse(self.env.get_template(name).render(**ctx))


def create(runtime: Runtime, host: str) -> tuple[FastAPI, App]:
    state = App(runtime, host)
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    api.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @api.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") != state.host:
            return Response("Ismeretlen cím.", status_code=403)
        path = request.url.path
        if not (path in ("/belepes", "/ablak") or path.startswith("/static/")):
            sid = request.cookies.get(COOKIE, "")
            if sid not in state.sessions:
                return Response("Ezt az oldalt a dwyt futtató ablaka nyitja meg.", status_code=403)
        response = await call_next(request)
        response.headers.update({"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
        return response

    @api.get("/belepes")
    def enter(t: str = ""):
        if not state.launch_code or not secrets.compare_digest(t, state.launch_code):
            return Response("Ez a belépő már nem érvényes; nyisd meg az ablakot újra.", status_code=403)
        state.launch_code = ""  # one time only
        sid = secrets.token_urlsafe(24)
        state.sessions[sid] = secrets.token_urlsafe(24)
        r = RedirectResponse("/allapot", status_code=303)
        r.set_cookie(COOKIE, sid, httponly=True, samesite="strict")
        return r

    @api.post("/ablak")
    def show_window(request: Request):
        """Another start of the app on this machine: show this window instead of a second app."""
        given = request.headers.get("x-dwyt-instance", "")
        if not secrets.compare_digest(given, state.instance_secret):
            return Response(status_code=403)
        state.on_show()
        return Response(status_code=204)

    @api.get("/")
    def root():
        return RedirectResponse("/allapot", status_code=303)

    @api.post("/kilepes")
    async def quit_app(request: Request):
        await state.form(request)
        threading.Timer(0.5, state.on_quit).start()  # the goodbye page gets out first
        return HTMLResponse("<!doctype html><html lang=\"hu\"><head><meta charset=\"utf-8\"><title>dwyt futtató</title>"
                            "<link rel=\"stylesheet\" href=\"/static/style.css\"></head><body><main>"
                            "<h1>A futtató leállt</h1><p class=\"muted\">Nem vesz fel munkát, amíg újra nem indítod."
                            "</p></main></body></html>")

    # -- Állapot --
    @api.get("/allapot")
    def status_page(request: Request):
        return state.render(request, "allapot.html", s=runtime.status())

    @api.get("/api/allapot")
    def status_json():
        return JSONResponse(runtime.status())

    @api.post("/parositas")
    async def pair(request: Request):
        form = await state.form(request)
        try:
            runtime.start_pairing(str(form.get("server", "")), str(form.get("name", "")))
        except Exception as exc:  # noqa: BLE001 - shown on the page
            runtime.pairing = {"state": "error", "error": str(exc)}
        return RedirectResponse("/allapot", status_code=303)

    # -- Működés --
    @api.get("/mukodes")
    def mode_page(request: Request, hiany: str = ""):
        cfg = config.load()
        return state.render(request, "mukodes.html", modes=modes.MODES, mode=modes.current(cfg),
                            flows=modes.preview(cfg), missing=[m for m in hiany.split("|") if m],
                            groups=settings.describe()["groups"], values=config.masked(cfg),
                            login_start=autostart.enabled())

    @api.post("/mukodes")
    async def mode_save(request: Request):
        form = await state.form(request)
        cfg = config.load()
        missing: list[str] = []
        mode = str(form.get("mode", ""))
        if mode in ("felho", "vegyes", "sajat"):
            missing = modes.apply(cfg, mode)
        cfg.polite_mode = form.get("polite_mode") == "on"
        config.save(cfg)
        runtime.settings_changed()
        return RedirectResponse("/mukodes" + ("?hiany=" + "|".join(missing) if missing else ""), status_code=303)

    @api.post("/mukodes/inditas")
    async def login_start_save(request: Request):
        form = await state.form(request)
        try:
            autostart.set_enabled(form.get("on") == "on")
        except OSError as exc:
            return RedirectResponse("/mukodes?hiany=" + f"Az indítást nem sikerült beállítani: {exc}", status_code=303)
        return RedirectResponse("/mukodes", status_code=303)

    @api.post("/mukodes/reszletek")
    async def details_save(request: Request):
        form = await state.form(request)
        cfg = config.load()
        errors = []
        for s in settings.SETTINGS:
            if s.readonly or s.kind == "secret":
                continue
            if s.kind == "bool":
                raw = "igen" if form.get(s.key) == "on" else "nem"
            elif s.key in form:
                raw = str(form.get(s.key))
                if s.kind == "map":
                    raw = raw.replace("\n", ";")
            else:
                continue
            try:
                setattr(cfg, s.key, settings.parse(s.key, raw))
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            return RedirectResponse("/mukodes?hiany=" + "|".join(errors), status_code=303)
        config.save(cfg)
        runtime.settings_changed()
        return RedirectResponse("/mukodes", status_code=303)

    # -- Gépem --
    @api.get("/gepem")
    def machine_page(request: Request):
        view = runtime.machine_view()
        m = view["m"]
        if m is None:  # the very first scan is still running
            return state.render(request, "gepem_wait.html")
        last = trial.load()
        card = m.best_gpu.name if m.best_gpu else ""
        last_when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last.at)) if last else ""
        scanned = time.strftime("%H:%M", time.localtime(view["at"]))
        return state.render(request, "gepem.html", m=m, a=machine.assess(m), said=view["said"], last=last,
                            last_when=last_when, same_card=last is not None and last.gpu == card,
                            trial_state=runtime.trial, local_models=view["local_models"], scanned=scanned,
                            scanning=view["scanning"])

    @api.post("/gepem/ujra")
    async def rescan(request: Request):
        await state.form(request)
        runtime.refresh_machine()
        return RedirectResponse("/gepem", status_code=303)

    @api.get("/api/gep")
    def machine_json():
        view = runtime.machine_view()
        return JSONResponse({"scanning": view["scanning"], "at": view["at"]})

    @api.post("/gepem/proba")
    async def start_trial(request: Request):
        form = await state.form(request)
        runtime.start_trial(str(form.get("llm", "")))
        return RedirectResponse("/gepem", status_code=303)

    @api.get("/api/proba")
    def trial_json():
        return JSONResponse(runtime.trial)

    # -- Kulcsok --
    @api.get("/kulcsok")
    def keys_page(request: Request, proba: str = ""):
        cfg = config.load()
        rows = [{"key": k, "label": label, "set": bool(getattr(cfg, k))} for k, label in SECRET_LABELS.items()]
        checks = {}
        if proba:
            checks = {"assemblyai_key": keystore.check_assemblyai(cfg), "llm_key": keystore.check_llm(cfg)}
        return state.render(request, "kulcsok.html", rows=rows, store=keystore.name(), checks=checks)

    @api.post("/kulcsok")
    async def keys_save(request: Request):
        form = await state.form(request)
        key = str(form.get("key", ""))
        if key not in SECRET_LABELS:
            return Response("Ismeretlen kulcs.", status_code=400)
        cfg = config.load()
        setattr(cfg, key, str(form.get("value", "")).strip())
        config.save(cfg)
        runtime.settings_changed()
        return RedirectResponse("/kulcsok?proba=1", status_code=303)

    return api, state
