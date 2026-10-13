"""What this machine can do (plan 15b, decision 18.8): video card, processor, memory, free space and
a local model server (Ollama, LM Studio), with plain Hungarian sentences and lamps (green / yellow /
red). It only recommends: the operating mode is always the user's choice.

The scan needs no admin rights and installs nothing: nvidia-smi for NVIDIA cards, the registry
(Windows) or /sys (Linux) for the others, two short HTTP calls on 127.0.0.1 for the model servers.
On every start the runner compares the scan with the last one (`machine.json` next to the settings)
and says so when the hardware changed, e.g. a new video card ("run a test?").

The estimates here come from measurements (dwyt-runner/tools/whisper_probe.py, 2026-10-13): whisper.cpp
large-v3 with speech detection ran 17x real time on an RTX 5080 and about 1x on a 16-thread laptop
processor. The test run (later in round 7b) replaces them with this machine's own numbers.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger("dwyt-runner")
GREEN, YELLOW, RED = "green", "yellow", "red"
LAMP_TEXT = {GREEN: "jó", YELLOW: "figyelem", RED: "kevés"}
VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel", "106b": "apple"}
OLLAMA_URL = "http://127.0.0.1:11434"
LMSTUDIO_URL = "http://127.0.0.1:1234"
# what the dwyt models need on disk: whisper.cpp large-v3 (3.1 GB) + turbo (1.6 GB) + VAD and speakers
WHISPER_DISK_GB = 5
LOCAL_LLM_DISK_GB = 10
# video memory: whisper.cpp large-v3 with speakers; a small (8B) and a large (30B+) local language model
WHISPER_VRAM_GB = 6
WHISPER_TURBO_VRAM_GB = 4
SMALL_LLM_VRAM_GB = 8
LARGE_LLM_VRAM_GB = 24
MOE_VRAM_GB, MOE_RAM_GB = 12, 30  # a 30B-A3B MoE model at 4 bits (~20 GB) split between the card and memory
INTEGRATED_BELOW_GB = 2  # less dedicated memory than this: a processor's built-in graphics
# Built-in graphics by name: the memory the firmware sets aside for them (often 4 GB, e.g. a Ryzen 8845HS's
# "AMD Radeon(TM) 780M Graphics") looks like a card's own memory but is taken from the system memory.
INTEGRATED_NAMES = re.compile(
    r"^AMD Radeon\(TM\) (\d{3}M )?Graphics$|^AMD Radeon Graphics$|Radeon Vega \d+ Graphics|"
    r"Intel\(R\) (UHD|HD|Iris)|^Intel\(R\) Arc\(TM\) (\d{3}V )?Graphics$|Intel\(R\) Graphics", re.IGNORECASE)


@dataclass
class Gpu:
    vendor: str              # nvidia | amd | intel | apple | other
    name: str
    vram_gb: float | None = None
    driver: str = ""

    @property
    def backend(self) -> str:
        """What whisper.cpp would use: CUDA on NVIDIA, Metal on Apple, Vulkan on the others."""
        return {"nvidia": "CUDA", "apple": "Metal"}.get(self.vendor, "Vulkan")

    @property
    def integrated(self) -> bool:
        if self.vendor in ("nvidia", "apple"):
            return False
        return bool(INTEGRATED_NAMES.search(self.name.strip())) or (
            self.vram_gb is not None and self.vram_gb < INTEGRATED_BELOW_GB)


@dataclass
class ModelServer:
    kind: str                # ollama | lmstudio
    installed: bool = False
    running: bool = False
    version: str = ""
    models: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return {"ollama": "Ollama", "lmstudio": "LM Studio"}[self.kind]


@dataclass
class Machine:
    system: str = ""
    cpu: str = ""
    threads: int = 0
    ram_gb: float | None = None
    gpus: list[Gpu] = field(default_factory=list)
    disk_free_gb: float | None = None
    models_gb: float = 0.0   # what the dwyt models already take
    servers: list[ModelServer] = field(default_factory=list)
    scanned_at: float = 0.0

    @property
    def best_gpu(self) -> Gpu | None:
        cards = [g for g in self.gpus if not g.integrated]
        return max(cards, key=lambda g: g.vram_gb or 0, default=None)

    @property
    def vram_gb(self) -> float:
        g = self.best_gpu
        return (g.vram_gb or 0.0) if g else 0.0


@dataclass
class Lamp:
    key: str
    label: str
    value: str
    level: str
    note: str = ""


@dataclass
class Assessment:
    rows: list[Lamp]          # what is in the machine
    abilities: list[Lamp]     # what it can do with it
    mode: str                 # felho | vegyes | sajat
    mode_reason: str


# ---- reading the hardware ----------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 10) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _gb(n_bytes: float | int | None) -> float | None:
    return round(n_bytes / 1024 ** 3, 1) if n_bytes else None


def nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":  # older drivers put it here, outside PATH
        p = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI"
        p = p / "nvidia-smi.exe"
        if p.exists():
            return str(p)
    return None


def parse_nvidia_smi(text: str) -> list[Gpu]:
    """`nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits`"""
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue
        try:
            vram = round(float(parts[1]) / 1024, 1)  # MiB
        except ValueError:
            vram = None
        out.append(Gpu("nvidia", parts[0], vram, parts[2]))
    return out


def windows_gpus() -> list[Gpu]:
    """Display adapters from the registry. Unlike WMI's AdapterRAM (capped at 4 GB) the driver's
    qwMemorySize tells the real dedicated memory."""
    import winreg
    out = []
    root = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        cls = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
    except OSError:
        return out
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(cls, i)
        except OSError:
            break
        i += 1
        if not sub.isdigit():
            continue
        try:
            key = winreg.OpenKey(cls, sub)
        except OSError:
            continue

        def value(name: str, key=key):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except OSError:
                return None
        name = value("DriverDesc")
        if not name or "Basic Display" in name or "Remote" in name:
            continue
        ids = str(value("MatchingDeviceId") or "").lower()
        m = re.search(r"ven_([0-9a-f]{4})", ids)
        vendor = VENDORS.get(m.group(1), "other") if m else "other"
        mem = value("HardwareInformation.qwMemorySize") or value("HardwareInformation.MemorySize")
        if isinstance(mem, bytes):
            mem = int.from_bytes(mem[:8], "little")
        out.append(Gpu(vendor, str(name), _gb(mem), str(value("DriverVersion") or "")))
    return out


def linux_gpus(sys_root: Path = Path("/sys/class/drm")) -> list[Gpu]:
    out, seen = [], set()
    for card in sorted(sys_root.glob("card[0-9]*")):
        dev = card / "device"
        if "-" in card.name or not (dev / "vendor").exists():
            continue
        real = os.path.realpath(dev)
        if real in seen:
            continue
        seen.add(real)
        vendor_id = (dev / "vendor").read_text().strip().lower().removeprefix("0x")
        vendor = VENDORS.get(vendor_id, "other")
        vram = None
        mem = dev / "mem_info_vram_total"  # amdgpu
        if mem.exists():
            try:
                vram = _gb(int(mem.read_text().strip()))
            except ValueError:
                vram = None
        name = ""
        slot = os.path.basename(real)
        if shutil.which("lspci"):
            m = re.search(r'"[^"]*" "([^"]*)" "([^"]*)"', _run(["lspci", "-mm", "-s", slot]))
            if m:
                name = f"{m.group(1)} {m.group(2)}"
        out.append(Gpu(vendor, name or {"amd": "AMD videókártya", "intel": "Intel videóvezérlő",
                                        "nvidia": "NVIDIA videókártya"}.get(vendor, "videókártya"), vram))
    return out


def scan_gpus() -> list[Gpu]:
    gpus: list[Gpu] = []
    smi = nvidia_smi()
    if smi:
        gpus += parse_nvidia_smi(_run([smi, "--query-gpu=name,memory.total,driver_version",
                                        "--format=csv,noheader,nounits"]))
    try:
        others = windows_gpus() if sys.platform == "win32" else linux_gpus() if sys.platform.startswith("linux") \
            else []
    except Exception:  # noqa: BLE001 - an odd registry or /sys must not stop the scan
        others = []
    if gpus:  # nvidia-smi is better for NVIDIA cards
        others = [g for g in others if g.vendor != "nvidia"]
    if sys.platform == "darwin" and platform.machine() == "arm64":
        others.append(Gpu("apple", "Apple Silicon (közös memória)", None))
    return gpus + others


def cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif sys.platform == "darwin":
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if name:
            return name
    return platform.processor() or platform.machine()


def ram_gb() -> float | None:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemoryStatus()
        st.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return _gb(st.ullTotalPhys)
        return None
    if sys.platform.startswith("linux"):
        try:
            m = re.search(r"MemTotal:\s+(\d+) kB", Path("/proc/meminfo").read_text())
            return _gb(int(m.group(1)) * 1024) if m else None
        except OSError:
            return None
    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "hw.memsize"]).strip()
        return _gb(int(out)) if out.isdigit() else None
    return None


def models_dir() -> Path:
    """Where the runner keeps its own models (whisper.cpp, speech detection, speakers)."""
    return config.home() / "models"


def folder_gb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return round(sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 ** 3, 1)


# ---- local model servers -----------------------------------------------------------------------

def port_open(url: str, timeout: float = 0.3) -> bool:
    """A quick look before an HTTP call: on Windows a refused connection to a closed local port
    takes about two seconds, and the machine page asks several of them."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    try:
        socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80), timeout=timeout).close()
        return True
    except OSError:
        return False


def _get_json(url: str, timeout: float = 1.5):
    import httpx
    if not port_open(url):
        return None
    try:
        r = httpx.get(url, timeout=timeout)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _installed(names: list[str], places: list[Path]) -> bool:
    return any(shutil.which(n) for n in names) or any(p.exists() for p in places)


def ollama() -> ModelServer:
    local = Path(os.environ.get("LOCALAPPDATA", "")) if sys.platform == "win32" else Path("/nonexistent")
    s = ModelServer("ollama", _installed(["ollama"], [local / "Programs" / "Ollama" / "ollama.exe"]))
    version = _get_json(f"{OLLAMA_URL}/api/version")
    if isinstance(version, dict):
        s.running = s.installed = True
        s.version = str(version.get("version") or "")
        tags = _get_json(f"{OLLAMA_URL}/api/tags") or {}
        s.models = [m.get("name", "") for m in tags.get("models", []) if isinstance(m, dict) and m.get("name")]
    return s


def lmstudio() -> ModelServer:
    home = Path.home()
    s = ModelServer("lmstudio", _installed(["lms"], [home / ".lmstudio" / "bin" / "lms",
                                                    home / ".lmstudio" / "bin" / "lms.exe"]))
    native = _get_json(f"{LMSTUDIO_URL}/api/v1/models")  # LM Studio 0.4+: downloaded models with their type
    if isinstance(native, dict) and isinstance(native.get("models"), list):
        s.running = s.installed = True
        s.models = [m["key"] for m in native["models"] if isinstance(m, dict) and m.get("key")
                    and m.get("type") in ("llm", None)]
        return s
    models = _get_json(f"{LMSTUDIO_URL}/v1/models")
    if isinstance(models, dict):
        s.running = s.installed = True
        s.models = [m.get("id", "") for m in models.get("data", []) if isinstance(m, dict) and m.get("id")]
        s.models = [m for m in s.models if "embed" not in m.lower()]
    return s


# ---- the scan, its judgement and what changed --------------------------------------------------

def scan(cfg: config.Config | None = None) -> Machine:
    cfg = cfg or config.load()
    where = Path(cfg.work_dir) if cfg.work_dir else config.home()
    probe = where
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = _gb(shutil.disk_usage(probe).free)
    except OSError:
        free = None
    from concurrent.futures import ThreadPoolExecutor
    started = time.monotonic()
    with ThreadPoolExecutor(4) as pool:  # the slow parts side by side: nvidia-smi, the two servers
        gpus, oll, lms = pool.submit(scan_gpus), pool.submit(ollama), pool.submit(lmstudio)
        m = Machine(system=f"{platform.system()} {platform.release()}", cpu=cpu_name(), threads=os.cpu_count() or 0,
                    ram_gb=ram_gb(), gpus=gpus.result(), disk_free_gb=free, models_gb=folder_gb(models_dir()),
                    servers=[oll.result(), lms.result()], scanned_at=time.time())
    log.debug("machine scan took %.1f s", time.monotonic() - started)
    return m


def _num(x: float | None) -> str:
    if x is None:
        return "?"
    return f"{x:.0f}" if x >= 10 or float(x).is_integer() else f"{x:.1f}".replace(".", ",")


def assess(m: Machine) -> Assessment:
    rows, abilities = [], []
    g = m.best_gpu
    vram = m.vram_gb

    # what is in the machine
    if g:
        level = GREEN if vram >= SMALL_LLM_VRAM_GB else YELLOW
        driver = f" · illesztőprogram {g.driver}" if g.driver and g.vendor == "nvidia" else ""
        note = "" if level == GREEN else "kevés saját memória: csak a kisebb beszédfelismerő modell fér el"
        if g.vendor != "nvidia" and g.vendor != "apple":
            note = (note + "; " if note else "") + "nem NVIDIA: Vulkannal működik, ez lassabb lehet a CUDA-nál"
        rows.append(Lamp("gpu", "Videókártya", f"{g.name} · {_num(g.vram_gb)} GB{driver}", level, note))
    else:
        built_in = next((x.name for x in m.gpus if x.integrated), "")
        rows.append(Lamp("gpu", "Videókártya", f"nincs külön videókártya{f' ({built_in})' if built_in else ''}",
                         YELLOW, "a helyi átírás a processzoron fut, lassan"))
    ram = m.ram_gb or 0
    # "16 GB" machines report a little less (memory kept by the firmware or the built-in graphics)
    level = GREEN if ram >= 15 and m.threads >= 8 else YELLOW if ram >= 7.5 else RED
    rows.append(Lamp("cpu", "Processzor, memória", f"{m.cpu} · {m.threads} szál · {_num(m.ram_gb)} GB", level,
                     "" if level != RED else "8 GB-nál kevesebb memória: a helyi munka nem ajánlott"))
    free = m.disk_free_gb
    if free is None:
        rows.append(Lamp("disk", "Szabad hely", "nem sikerült megnézni", YELLOW))
    else:
        level = GREEN if free >= WHISPER_DISK_GB + LOCAL_LLM_DISK_GB else YELLOW if free >= WHISPER_DISK_GB else RED
        have = f" · a dwyt modelljei {_num(m.models_gb)} GB" if m.models_gb else ""
        rows.append(Lamp("disk", "Szabad hely", f"{_num(free)} GB{have}", level,
                         f"kell: kb. {WHISPER_DISK_GB} GB, saját összefoglalóhoz +{LOCAL_LLM_DISK_GB} GB"))
    running = [s for s in m.servers if s.running]
    installed = [s for s in m.servers if s.installed and not s.running]
    if running:
        s = running[0]
        names = ", ".join(s.models[:3]) + (" …" if len(s.models) > 3 else "")
        value = f"{s.title}{' ' + s.version if s.version else ''} · fut · {len(s.models)} modell" + \
            (f" ({names})" if names else "")
        rows.append(Lamp("server", "Helyi modellszerver", value, GREEN if s.models else YELLOW,
                         "" if s.models else "még nincs letöltött modell"))
    elif installed:
        rows.append(Lamp("server", "Helyi modellszerver", f"{installed[0].title} telepítve, de nem fut", YELLOW,
                         "a futtató el tudja indítani"))
    else:
        rows.append(Lamp("server", "Helyi modellszerver", "nincs (Ollama és LM Studio nem található)", YELLOW,
                         "saját összefoglalóhoz a telepítő felajánlja az Ollamát"))

    # what it can do
    if g and vram >= WHISPER_VRAM_GB:
        abilities.append(Lamp("whisper", "Helyi átírás", "gyors", GREEN,
                              f"large-v3, beszélőkkel, a videókártyán ({g.backend})"))
    elif g and vram >= WHISPER_TURBO_VRAM_GB:
        abilities.append(Lamp("whisper", "Helyi átírás", "működik", YELLOW,
                              "a kisebb (turbo) modellel; a magyar szöveg kicsit pontatlanabb"))
    elif m.threads >= 8 and ram >= 7.5:
        abilities.append(Lamp("whisper", "Helyi átírás", "lassú", YELLOW,
                              "a processzoron: egy 1 órás videó nagyjából 1 óra vagy több"))
    else:
        abilities.append(Lamp("whisper", "Helyi átírás", "nem ajánlott", RED,
                              "ezen a gépen nagyon lassú lenne; az átírás felhőben ajánlott"))
    if vram >= LARGE_LLM_VRAM_GB:
        abilities.append(Lamp("llm_small", "Helyi összefoglaló", "jó", GREEN,
                              "egy nagy (30B körüli) modell is elfér; a magyar szöveg közelít a felhőshöz"))
    elif vram >= SMALL_LLM_VRAM_GB:
        abilities.append(Lamp("llm_small", "Helyi összefoglaló", "működik, de gyengébb", YELLOW,
                              "egy kisebb (8B körüli) modell fér el; a magyar összefoglalók érezhetően gyengébbek "
                              "lesznek, mint egy felhős modellé"))
    else:
        abilities.append(Lamp("llm_small", "Helyi összefoglaló", "nem fér el", RED,
                              f"legalább {SMALL_LLM_VRAM_GB} GB videómemória kellene"))
    if vram < LARGE_LLM_VRAM_GB:
        if vram >= MOE_VRAM_GB and ram >= MOE_RAM_GB:
            # a mixture-of-experts model (e.g. 30B-A3B) keeps its experts in system memory and stays usable
            abilities.append(Lamp("llm_large", "Nagy helyi modell (30B+)", "MoE-modellel működik", YELLOW,
                                  "egy MoE-modell (például Qwen3 30B-A3B) a rendszermemória segítségével elfut, "
                                  "lassabban; egy sűrű 30B-s modellhez legalább "
                                  f"{LARGE_LLM_VRAM_GB} GB videómemória kellene"))
        else:
            abilities.append(Lamp("llm_large", "Nagy helyi modell (30B+)", "nem fér el", RED,
                                  f"ehhez legalább {LARGE_LLM_VRAM_GB} GB videómemória kellene"))

    if vram >= LARGE_LLM_VRAM_GB:
        mode, reason = "sajat", ("a **Saját gép** módot ajánljuk: az átírás és az összefoglaló is itt készül, "
                                 "ingyen, és semmi nem megy a felhőbe")
    elif g and vram >= WHISPER_TURBO_VRAM_GB:
        mode, reason = "vegyes", ("a **Vegyes** módot ajánljuk: az átírás itt, ingyen készül, az összefoglaló a "
                                  "felhőben, jó minőségben")
    else:
        mode, reason = "felho", ("a **Felhő** módot ajánljuk: ezen a gépen a helyi átírás lassú lenne; a felhő "
                                 "fizetős, de gyors")
    return Assessment(rows, abilities, mode, "A géped alapján " + reason + ". Bármelyiket választhatod.")


def snapshot_path() -> Path:
    return config.home() / "machine.json"


def save_snapshot(m: Machine) -> None:
    p = snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(m), ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot() -> Machine | None:
    try:
        data = json.loads(snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        data["gpus"] = [Gpu(**g) for g in data.get("gpus", [])]
        data["servers"] = [ModelServer(**s) for s in data.get("servers", [])]
        return Machine(**data)
    except TypeError:
        return None


def changes(before: Machine | None, now: Machine) -> list[str]:
    """What changed since the last scan, in sentences for the user (empty on the first scan)."""
    if before is None:
        return []
    out = []
    old, new = before.best_gpu, now.best_gpu

    def card(gpu: Gpu) -> str:
        return f"{gpu.name}, {_num(gpu.vram_gb)} GB"
    if new and (not old or (old.name, old.vram_gb) != (new.name, new.vram_gb)):
        was = f" (eddig: {card(old)})" if old else ""
        out.append(f"Új videókártyát találtam: {card(new)}{was}. Futtassunk próbát? Lehet, hogy most más mód is jól "
                   "menne.")
    elif old and not new:
        out.append(f"Nem találom a videókártyát (eddig: {card(old)}). Ha kivetted, a helyi átírás a processzoron "
                   "lassú lesz; ha nem, nézd meg az illesztőprogramot.")
    if before.ram_gb and now.ram_gb and abs(before.ram_gb - now.ram_gb) >= 1:
        out.append(f"Változott a memória: {_num(now.ram_gb)} GB (eddig {_num(before.ram_gb)} GB).")
    return out


def watch_at_start(cfg: config.Config) -> list[str]:
    """The quiet check on start: scan, compare with the last scan, keep this one."""
    now = scan(cfg)
    said = changes(load_snapshot(), now)
    save_snapshot(now)
    return said


def report(m: Machine, a: Assessment, measured: str = "") -> str:
    """The scan as plain text (the command line; the window draws the same data)."""
    lines = ["Gépem", ""]
    width = max(len(r.label) for r in a.rows + a.abilities) + 2
    for r in a.rows:
        lines.append(f"  {r.label:<{width}}{r.value}  [{LAMP_TEXT[r.level]}]")
        if r.note:
            lines.append(f"  {'':<{width}}{r.note}")
    lines += ["", "Mire képes"]
    for r in a.abilities:
        lines.append(f"  {r.label:<{width}}{r.value}  [{LAMP_TEXT[r.level]}]")
        if r.note:
            lines.append(f"  {'':<{width}}{r.note}")
    if measured:  # a test run with this card says more than the estimate
        lines += ["", measured.replace("**", "")]
    else:
        lines += ["", a.mode_reason.replace("**", ""),
                  "Ez csak becslés; a próbafuttatás méri meg, mennyire gyors ez a gép."]
    return "\n".join(lines)
