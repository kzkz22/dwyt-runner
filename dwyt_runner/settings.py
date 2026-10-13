"""The runner's settings described once (plan 15b, decision 18.3): field, kind, allowed values, plain
Hungarian label and explanation. The desktop window (7c) and the dwyt Settings page (7d) draw from this,
and `config set` checks values against it. Every field of config.Config is here exactly once (a test
makes sure); `remote: False` fields are never set from the dwyt website (the pairing, local paths of the
machine's own programs), secrets are write-only everywhere.
"""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields
from typing import Any

from .config import Config

GROUPS = [
    ("connection", "Kapcsolat a dwyt-vel"),
    ("cloud", "Felhős átírás"),
    ("local", "Helyi átírás ezen a gépen"),
    ("llm", "Összefoglaló, fordítás, kérdések"),
    ("sources", "Források és saját felvételek"),
    ("advanced", "Haladó"),
]
TRUE = ("1", "true", "yes", "on", "igen", "be")
FALSE = ("0", "false", "no", "off", "nem", "ki", "")


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str              # text | secret | bool | int | float | choice | list | map | path | url
    group: str
    label: str
    help: str
    choices: tuple[tuple[str, str], ...] = ()   # (value, label)
    min: float | None = None
    max: float | None = None
    unit: str = ""
    advanced: bool = False
    show_if: tuple[tuple[str, Any], ...] = ()   # shown only when these settings have these values
    remote: bool = True    # may be set from the dwyt website (7d)
    readonly: bool = False  # set by pairing, not by hand


S = Setting
SETTINGS: tuple[Setting, ...] = (
    # -- connection --
    S("server", "url", "connection", "A dwyt címe", "Ahová a futtató munkáért fordul. A párosítás tölti ki.",
      remote=False, readonly=True),
    S("token", "secret", "connection", "Párosítási token", "A párosításkor kapott belépő; a dwyt Futtatók oldalán "
      "visszavonható.", remote=False, readonly=True),
    S("runner_id", "int", "connection", "Futtató azonosító", "A párosítás tölti ki.", remote=False, readonly=True),
    S("name", "text", "connection", "A futtató neve", "Így látszik a dwyt Futtatók oldalán, például „otthoni gép”."),
    # -- cloud transcription --
    S("assemblyai_key", "secret", "cloud", "AssemblyAI-kulcs", "A saját AssemblyAI-fiókod kulcsa. Felhős átírás "
      "csak akkor készül, ha valaki kifejezetten azt kéri; a költség a te fiókodat terheli. Javasolt egy külön kulcs "
      "keretkorláttal."),
    S("assemblyai_url", "url", "cloud", "AssemblyAI címe", "Csak ha az EU-s végpontot használod "
      "(https://api.eu.assemblyai.com).", advanced=True),
    S("cloud_delete_after", "bool", "cloud", "Törlés az AssemblyAI-nál", "Az átírás után a hangot és a szöveget "
      "törli az AssemblyAI-nál."),
    # -- local transcription --
    S("whisper_builtin", "bool", "local", "Helyi átírás ezen a gépen", "A whisper.cpp és a beszélők szétválasztása "
      "ezen a gépen fut, a modelleket egyszer tölti le (kb. 3,5 GB). Videókártyával gyors, nélküle lassú."),
    S("whisper_models", "list", "local", "Választható Whisper-modellek", "large-v3: pontosabb; large-v3-turbo: "
      "gyorsabb, kicsit pontatlanabb. Az első az alapértelmezett. Külön GPU-gépnél az ott telepített nevek."),
    S("diarize_threshold", "float", "local", "Beszélők szétválasztása", "Ha a beküldéskor nincs megadva a beszélők "
      "száma, ebből becsli: nagyobb érték kevesebb beszélőt ad. A 0,8 egy 8 beszélős interjún mérve a legjobb.",
      min=0.3, max=1.5, advanced=True, show_if=(("whisper_builtin", True),)),
    S("polite_mode", "bool", "local", "Udvarias mód", "Ha valaki épp használja a helyi nyelvi modellt, az átírás "
      "előtt megvárja, hogy egy percig ne használja (legfeljebb a megadott ideig), és csak utána üríti ki a "
      "videókártyáról.", show_if=(("whisper_builtin", True),)),
    S("polite_wait_min", "int", "local", "Udvarias mód: leghosszabb várakozás", "Ennyi perc után akkor is kiüríti "
      "a modellt, ha még használják.", min=0, max=120, unit="perc", show_if=(("polite_mode", True),)),
    S("whisper_cli", "path", "local", "A whisper.cpp helye", "Üresen a futtató maga intézi (Windowson letölti). "
      "Megadható a whisper-cli program vagy egy kicsomagolt kiadás mappája.", advanced=True, remote=False),
    S("whisper_threads", "int", "local", "Processzorszálak", "0: a whisper.cpp dönt.", min=0, max=256,
      advanced=True),
    S("whisper_url", "url", "local", "Külön GPU-gép (AI-router)", "Ha a Whisper egy másik gépen fut (dwyt-airouter "
      "és dwyt-gpu), annak címe; ilyenkor ez a gép nem ír át maga.", advanced=True),
    S("whisper_timeout_min", "int", "local", "Egy átírás leghosszabb ideje", "Hosszú felvételnél a hossz négyszerese "
      "számít, ha az több.", min=5, max=1440, unit="perc", advanced=True),
    # -- language models --
    S("local_llm", "choice", "llm", "Helyi modellszerver", "Az összefoglalók ezen a gépen készülnek, a választott "
      "programmal. A futtató elindítja, letölti és betölti a modelleket.",
      choices=(("", "nincs"), ("ollama", "Ollama"), ("lmstudio", "LM Studio"))),
    S("local_llm_url", "url", "llm", "A helyi modellszerver címe", "Üresen a szokásos cím ezen a gépen.",
      advanced=True, show_if=(("local_llm", "ollama"), ("local_llm", "lmstudio"))),
    S("lmstudio_token", "secret", "llm", "LM Studio API-token", "Csak ha az LM Studióban bekapcsoltad az "
      "API-tokent.", show_if=(("local_llm", "lmstudio"),)),
    S("llm_url", "url", "llm", "Felhős vagy saját LLM-szerver címe", "Például https://openrouter.ai/api/v1, vagy a "
      "saját LiteLLM-ed. Ha meg van adva, ez az elsődleges a helyi modellszerverrel szemben."),
    S("llm_key", "secret", "llm", "Az LLM-szerver kulcsa", "Javasolt egy külön kulcs keretkorláttal."),
    S("llm_public_model", "text", "llm", "Modell a nyilvános videókhoz", "Ezzel készül a YouTube-videók "
      "összefoglalója."),
    S("llm_private_model", "text", "llm", "Modell a saját felvételekhez", "A saját felvételek szövege csak ehhez a "
      "modellhez kerül; helyi modell javasolt."),
    S("llm_translate_model", "text", "llm", "Modell a fordításhoz", "Üresen a nyilvános videók modellje.",
      advanced=True),
    S("llm_ask_model", "text", "llm", "Modell a kérdésekhez", "Üresen a nyilvános videók modellje; egy gondolkodó "
      "modell itt jobb válaszokat ad.", advanced=True),
    S("llm_models", "list", "llm", "További választható modellek (nyilvános)", "Ezek közül is választhatsz az elem "
      "oldalán.", advanced=True),
    S("llm_private_models", "list", "llm", "További választható modellek (saját felvételek)", "Csak amit ide "
      "felveszel, ahhoz kerülhet saját felvétel szövege.", advanced=True),
    S("llm_model_notes", "map", "llm", "Modellek leírása", "Ha az LLM-szerver nem mondja meg, mi fut egy név "
      "mögött: név=leírás.", advanced=True),
    # -- sources --
    S("allowed_domains", "list", "sources", "Engedélyezett oldalak", "Honnan tölthet le a futtató (* = bárhonnan)."),
    S("cookies_file", "path", "sources", "Sütifájl", "Csak ha egy oldal bejelentkezést kér (Netscape-formátumú "
      "cookies.txt).", advanced=True, remote=False),
    S("watch_dirs", "list", "sources", "Figyelt mappák", "Az ide kerülő hang- és videófájlokat a futtató magától "
      "átírja; a fájlok nem hagyják el a gépet."),
    S("watch_mode", "choice", "sources", "Figyelt mappák: átírás módja", "Helyben vagy felhőben.",
      choices=(("local", "helyben"), ("cloud", "felhőben"))),
    S("watch_model", "text", "sources", "Figyelt mappák: modell", "Üresen az alapértelmezett.", advanced=True),
    S("watch_language", "text", "sources", "Figyelt mappák: nyelv", "Például hu; üresen felismeri.",
      advanced=True),
    S("watch_summarize", "bool", "sources", "Figyelt mappák: összefoglaló", "Az átírás után összefoglaló is "
      "készül."),
    S("max_file_mb", "int", "sources", "Legnagyobb fájl", "", min=1, max=100000, unit="MB", advanced=True),
    S("max_duration_min", "int", "sources", "Leghosszabb felvétel", "", min=1, max=1440, unit="perc",
      advanced=True),
    # -- advanced --
    S("work_dir", "path", "advanced", "Munkamappa", "Ide kerülnek az ideiglenes fájlok; üresen a rendszer "
      "ideiglenes mappája.", advanced=True, remote=False),
    S("secret_store", "choice", "advanced", "A kulcsok helye", "Alapból a rendszer kulcstárolója, ha van.",
      choices=(("auto", "automatikus"), ("keyring", "a rendszer kulcstárolója"), ("file", "a beállítási fájl")),
      advanced=True, remote=False),
)
BY_KEY = {s.key: s for s in SETTINGS}


def defaults() -> dict[str, Any]:
    out = {}
    for f in fields(Config):
        out[f.name] = f.default if f.default is not MISSING else f.default_factory()  # type: ignore[misc]
    return out


def describe() -> dict[str, Any]:
    """Everything a settings screen needs, as plain data (JSON)."""
    base = defaults()
    out = {"groups": []}
    for gid, title in GROUPS:
        items = []
        for s in SETTINGS:
            if s.group != gid:
                continue
            d = asdict(s)
            d["choices"] = [{"value": v, "label": label} for v, label in s.choices]
            d["show_if"] = [{"key": k, "value": v} for k, v in s.show_if]
            d["default"] = "" if s.kind == "secret" else base[s.key]
            items.append(d)
        out["groups"].append({"id": gid, "title": title, "settings": items})
    return out


def parse(key: str, raw: str) -> Any:
    """A typed value from text (the command line, a form), checked; ValueError says why in Hungarian."""
    s = BY_KEY.get(key)
    if s is None or s.readonly:
        raise ValueError(f"Ismeretlen vagy nem kézzel állítható beállítás: {key}")
    text = (raw or "").strip()
    if s.kind == "bool":
        low = text.lower()
        if low in TRUE:
            return True
        if low in FALSE:
            return False
        raise ValueError(f"{s.label}: igen vagy nem")
    if s.kind in ("int", "float"):
        try:
            value: Any = int(text) if s.kind == "int" else float(text.replace(",", "."))
        except ValueError:
            raise ValueError(f"{s.label}: számot várok") from None
        if (s.min is not None and value < s.min) or (s.max is not None and value > s.max):
            raise ValueError(f"{s.label}: {s.min:g} és {s.max:g} között")
        return value
    if s.kind == "choice":
        allowed = [v for v, _ in s.choices]
        if text not in allowed:
            raise ValueError(f"{s.label}: ezek egyike: {', '.join(v or '(üres)' for v in allowed)}")
        return text
    if s.kind == "list":
        items = [v.strip() for v in text.split(",") if v.strip()]
        if s.choices:
            bad = [v for v in items if v not in dict(s.choices)]
            if bad:
                raise ValueError(f"{s.label}: ismeretlen érték: {', '.join(bad)}")
        return items
    if s.kind == "map":
        pairs = (part.split("=", 1) for part in text.split(";") if "=" in part)
        return {k.strip(): v.strip() for k, v in pairs if k.strip() and v.strip()}
    if s.kind == "url" and text and not text.lower().startswith(("http://", "https://")):
        raise ValueError(f"{s.label}: http:// vagy https:// kezdetű cím")
    return text


def summary() -> str:
    """The settings as text, group by group (dwyt-runner config describe)."""
    lines = []
    for g in describe()["groups"]:
        lines += [g["title"], ""]
        for s in g["settings"]:
            if s["readonly"]:
                continue
            extra = []
            if s["choices"]:
                extra.append(" | ".join(c["value"] or "(üres)" for c in s["choices"]))
            if s["unit"]:
                extra.append(s["unit"])
            lines.append(f"  {s['key']}{' (' + '; '.join(extra) + ')' if extra else ''}")
            lines.append(f"      {s['label']}." + (f" {s['help']}" if s["help"] else ""))
        lines.append("")
    return "\n".join(lines)
