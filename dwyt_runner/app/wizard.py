"""The setup steps in the window (plan 15b, docs/minta-7 1–6): Üdvözlés, Gépvizsgálat, Működési mód,
Hozzávalók, Párosítás, Kész. They only set what the other tabs also set; `setup.json` remembers that the
steps were finished (or skipped) and which mode was chosen in them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .. import config, modes

STEPS = ["Üdvözlés", "Gépvizsgálat", "Működési mód", "Hozzávalók", "Párosítás", "Kész"]
CLOUD_LLM_URL = "https://openrouter.ai/api/v1"
CLOUD_MODEL = "qwen/qwen3.8-flash"  # a good, cheap Hungarian summary on OpenRouter (the owner's choice)
MODE_TEXT = {
    "felho": ("Minden a felhőben készül. Gyenge géphez, vagy ha a gép ritkán van bekapcsolva.",
              "fizetős, a te kulcsaiddal"),
    "vegyes": ("Átírás ezen a gépen, összefoglaló a felhőben. Jó magyar összefoglaló kevés pénzért.",
               "az összefoglaló fizetős (centes tétel)"),
    "sajat": ("Minden ezen a gépen készül. Ingyenes és a legprivátabb; erős gép kell.",
              "ingyenes · a magyar összefoglaló gyengébb"),
    "egyeni": ("Haladóknak: lépésenként választható, a Működés lap részletes beállításaiban.", ""),
}


def path():
    return config.home() / "setup.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(**changes: Any) -> dict[str, Any]:
    data = {**load(), **changes}
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def needed(cfg: config.Config) -> bool:
    """The window opens on the setup steps: not paired yet, and the steps were neither finished nor skipped."""
    return not cfg.paired and not load().get("done")


def chosen_mode(cfg: config.Config) -> str:
    return load().get("mode") or modes.current(cfg)


def needs_whisper(cfg: config.Config, mode: str) -> bool:
    return mode in ("vegyes", "sajat") and not cfg.whisper_url


def whisper_model(cfg: config.Config) -> str:
    return (cfg.whisper_models or ["large-v3"])[0]


def cloud_model_default(cfg: config.Config) -> str:
    """The model field's value: the set one when it is a cloud name ('vendor/model'), else the suggestion."""
    return cfg.llm_public_model if "/" in cfg.llm_public_model else CLOUD_MODEL


@dataclass
class Ingredient:
    key: str
    title: str
    need: str        # kell | ajánlott | nem kell
    ok: bool
    note: str = ""

    @property
    def lamp(self) -> str:
        if self.need == "nem kell":
            return "grey"
        return "green" if self.ok else ("yellow" if self.need == "ajánlott" else "orange")


def ingredients(cfg: config.Config, mode: str, prepare: dict[str, Any], whisper_ready: bool) -> list[Ingredient]:
    out = []
    if needs_whisper(cfg, mode):
        out.append(Ingredient("whisper", f"Beszédfelismerő (Whisper {whisper_model(cfg)})", "kell",
                              whisper_ready or prepare.get("ready") or prepare.get("running"),
                              "a letöltés a háttérben is folytatódik" if prepare.get("running") else ""))
    else:
        out.append(Ingredient("whisper", "Beszédfelismerő (Whisper)", "nem kell", False,
                              "a GPU-gép végzi" if cfg.whisper_url else "ebben a módban nem kell"))
    cloud_llm = mode in ("felho", "vegyes")
    out.append(Ingredient("llm", "Felhős összefoglaló: OpenRouter-kulcs", "kell" if cloud_llm else "nem kell",
                          bool(cfg.llm_url and cfg.llm_key and cfg.llm_public_model),
                          "kötelező ehhez a módhoz" if cloud_llm else "ebben a módban nem kell"))
    out.append(Ingredient("assemblyai", "Felhős átírás (AssemblyAI)", "ajánlott" if mode == "felho" else "nem kell",
                          bool(cfg.assemblyai_key),
                          "nélküle csak a YouTube-feliratok használhatók" if mode == "felho"
                          else "ebben a módban nem kell"))
    local = mode == "sajat"
    out.append(Ingredient("local", "Helyi összefoglaló (Ollama vagy LM Studio)", "kell" if local else "nem kell",
                          bool(cfg.local_llm and cfg.llm_private_model),
                          "" if local else "ebben a módban nem kell · később a Működés lapon bekapcsolható"))
    return out


def can_go_on(items: list[Ingredient]) -> bool:
    return all(i.ok for i in items if i.need == "kell")


def suggested_local_model(vram_gb: float, kind: str) -> str:
    """A first local model for the summary, by video memory (Ollama names; LM Studio takes model keys)."""
    if kind == "lmstudio":
        return "qwen/qwen3-30b-a3b" if vram_gb >= 20 else "qwen/qwen3-8b"
    return "qwen3:30b" if vram_gb >= 20 else "qwen3:8b"


def size_text(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB".replace(".", ",")
    return f"{n / (1 << 20):.0f} MB"


def prepare_view(p: dict[str, Any]) -> dict[str, Any]:
    """The download's line for the window: what, how far."""
    percent = int(p["got"] * 100 / p["total"]) if p.get("total") else None
    text = p.get("label") or ""
    if p.get("total"):
        text += f" · {percent}% ({size_text(p['got'])} / {size_text(p['total'])})"
    elif p.get("got"):
        text += f" · {size_text(p['got'])}"
    return {"running": p.get("running", False), "ready": p.get("ready", False), "error": p.get("error", ""),
            "percent": percent, "text": text}


def pull_view(p: dict[str, Any]) -> dict[str, Any]:
    fraction = p.get("fraction")
    percent = int(fraction * 100) if isinstance(fraction, (int, float)) else None
    text = f"{p.get('name', '')} · {p.get('status') or ''}" + (f" · {percent}%" if percent is not None else "")
    return {"running": p.get("running", False), "error": p.get("error", ""), "percent": percent, "text": text}
