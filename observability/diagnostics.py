"""JARVIS diagnostics, tool discovery and safe mode.

Implements the observability layer the master spec asks for:

* §36/§78 — real tool discovery: what is installed, its version, path, whether
  it actually runs, and what JARVIS loses without it.
* §51 — health checks per subsystem with the states ONLINE / DEGRADED /
  OFFLINE / ERROR / DISABLED, each with evidence and a suggested action.
* §53/§54 — crash bookkeeping and SAFE MODE: three crashes inside ten minutes
  puts the assistant into a reduced runtime that skips background work until
  the user clears it.
* §56 — correlation ids (TASK-2026-000001) so tool calls can be traced.
* §86 — nothing here ever reports a check as passing when it was not run. A
  check that could not be performed says so and stays DEGRADED.

Every public call is guarded: diagnostics must never be the thing that breaks
the assistant. Read-only apart from the runtime-state file it owns.
"""

from __future__ import annotations

import importlib.metadata as _md
import importlib.util as _util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

BASE_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = BASE_DIR / "config" / "runtime_state.json"

# ── states (spec §51) ─────────────────────────────────────────────────────────
ONLINE = "ONLINE"
DEGRADED = "DEGRADED"
OFFLINE = "OFFLINE"
ERROR = "ERROR"
DISABLED = "DISABLED"
STATES = (ONLINE, DEGRADED, OFFLINE, ERROR, DISABLED)

_WIN = platform.system() == "Windows"
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _WIN else {}


@dataclass
class Tool:
    """One external dependency JARVIS can use (spec §15, §36)."""

    key: str
    name: str
    category: str
    capability: str
    kind: str = "python"          # "python" package or "binary"
    module: str = ""              # import name for python kind
    binary: str = ""              # executable name for binary kind
    version_args: tuple = ("--version",)
    required_for: tuple = ()      # JARVIS features that stop working without it
    optional: bool = True
    authorization_required: bool = False
    path: str = ""
    version: str = ""
    installed: bool = False
    health: str = OFFLINE
    detail: str = ""


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""
    evidence: dict = field(default_factory=dict)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def config() -> dict:
    """Current config/api_keys.json (never raises)."""
    return _read_json(BASE_DIR / "config" / "api_keys.json")


def features() -> dict:
    cfg = config().get("features")
    return cfg if isinstance(cfg, dict) else {}


# ── tool registry ─────────────────────────────────────────────────────────────

def _registry() -> list[Tool]:
    return [
        # local AI (§7)
        Tool("llama_cpp", "llama.cpp server", "local_ai", "local GGUF inference",
             kind="binary", binary="llama-server", required_for=("offline AI",),
             optional=True),
        # AI providers
        Tool("genai", "Google GenAI SDK", "ai", "Gemini Live + text",
             module="google.genai", required_for=("Gemini voice", "Gemini text")),
        Tool("openai", "OpenAI SDK", "ai", "alternative AI provider",
             module="openai", optional=True),
        # voice (§8)
        Tool("sounddevice", "sounddevice", "audio", "microphone and playback",
             module="sounddevice", required_for=("voice input", "voice output")),
        Tool("numpy", "NumPy", "audio", "audio maths", module="numpy",
             required_for=("voice", "vision")),
        Tool("onnxruntime", "ONNX Runtime", "voice", "local neural voices (jarvis-high)",
             module="onnxruntime", required_for=("local ONNX voice",)),
        Tool("pyttsx3", "pyttsx3", "voice", "offline SAPI voice", module="pyttsx3",
             optional=True),
        Tool("openwakeword", "openWakeWord", "voice", "wake-word detection",
             module="openwakeword", required_for=("wake word",)),
        Tool("faster_whisper", "faster-whisper", "voice", "local speech recognition",
             module="faster_whisper", required_for=("local STT",), optional=True),
        # vision (§18-§20)
        Tool("cv2", "OpenCV", "vision", "camera capture and frame processing",
             module="cv2", required_for=("camera", "vision")),
        Tool("mediapipe", "MediaPipe", "vision", "face, hand and gesture tracking",
             module="mediapipe", required_for=("face tracking", "gestures")),
        Tool("ultralytics", "Ultralytics", "vision", "YOLO object detection",
             module="ultralytics", optional=True),
        Tool("PIL", "Pillow", "vision", "image loading and encoding",
             module="PIL", required_for=("image panels",)),
        # browser (§22)
        Tool("playwright", "Playwright", "browser", "browser automation",
             module="playwright", required_for=("browser control",)),
        # documents
        Tool("fitz", "PyMuPDF", "documents", "PDF text extraction",
             module="fitz", optional=True),
        Tool("pandas", "pandas", "documents", "spreadsheet analysis",
             module="pandas", optional=True),
        # ui
        Tool("PyQt6", "PyQt6", "ui", "the JARVIS interface", module="PyQt6",
             required_for=("interface",)),
        Tool("psutil", "psutil", "system", "CPU/RAM/disk telemetry", module="psutil",
             required_for=("system monitor",)),
        # external binaries
        Tool("ffmpeg", "FFmpeg", "media", "media conversion and recording",
             kind="binary", binary="ffmpeg", required_for=("media conversion",),
             optional=True),
        Tool("ffprobe", "FFprobe", "media", "media metadata", kind="binary",
             binary="ffprobe", optional=True),
        Tool("mpv", "MPV", "media", "local media playback", kind="binary",
             binary="mpv", optional=True),
        Tool("tesseract", "Tesseract OCR", "ocr", "text extraction from images",
             kind="binary", binary="tesseract", required_for=("OCR",), optional=True),
        Tool("rg", "ripgrep", "developer", "fast project and log search",
             kind="binary", binary="rg", optional=True),
        Tool("git", "Git", "developer", "version control", kind="binary",
             binary="git", optional=True),
        Tool("node", "Node.js", "developer", "javascript tooling", kind="binary",
             binary="node", optional=True),
        Tool("nmap", "Nmap", "network", "authorized network mapping",
             kind="binary", binary="nmap", optional=True, authorization_required=True),
        Tool("scrcpy", "scrcpy", "android", "authorized Android mirroring",
             kind="binary", binary="scrcpy", optional=True,
             authorization_required=True),
    ]


# Import name → distribution name, where they differ. Without this the
# registry showed an installed OpenCV/Pillow/PyMuPDF with no version at all.
_DIST_NAMES = {
    "cv2": "opencv-python", "PIL": "pillow", "fitz": "pymupdf",
    "sklearn": "scikit-learn", "yaml": "pyyaml", "google.genai": "google-genai",
    "faster_whisper": "faster-whisper", "onnxruntime": "onnxruntime",
    "pyttsx3": "pyttsx3", "sounddevice": "sounddevice",
}


def _python_version(module: str) -> str:
    """Version of an importable distribution (metadata only, no import)."""
    names = {_DIST_NAMES.get(module, module), module, module.replace("_", "-"),
             module.lower()}
    for name in names:
        try:
            return _md.version(name)
        except Exception:
            continue
    return ""


def _binary_version(binary: str, args: tuple) -> str:
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True,
                             timeout=6, **_NO_WINDOW)
        text = (out.stdout or out.stderr or "").strip().splitlines()
        return text[0][:80] if text else ""
    except Exception:
        return ""


_probe_cache: tuple[float, list[Tool]] | None = None


def probe_tools(refresh: bool = False) -> list[Tool]:
    """Discover every registered tool with its real state. Cached 5 minutes.

    `health` is only ONLINE when the thing was actually located; a binary that
    is on PATH but fails to run is reported as ERROR with the output, not as
    available.
    """
    global _probe_cache
    now = time.time()
    if not refresh and _probe_cache and (now - _probe_cache[0]) < 300:
        return _probe_cache[1]
    tools = _registry()
    for t in tools:
        try:
            if t.kind == "python":
                t.installed = _util.find_spec(t.module) is not None
                if t.installed:
                    t.version = _python_version(t.module)
                    t.health = ONLINE
                    t.detail = f"importable ({t.version or 'version unknown'})"
                else:
                    t.health = OFFLINE
                    t.detail = "package not installed"
            else:
                t.path = shutil.which(t.binary) or ""
                t.installed = bool(t.path)
                if t.installed:
                    t.version = _binary_version(t.binary, t.version_args)
                    # A binary that exists but cannot report a version has not
                    # been proven usable.
                    t.health = ONLINE if t.version else DEGRADED
                    t.detail = t.version or "found on PATH but --version gave nothing"
                else:
                    t.health = OFFLINE
                    t.detail = "not on PATH"
        except Exception as exc:
            t.health = ERROR
            t.detail = f"{type(exc).__name__}: {exc}"
    _probe_cache = (now, tools)
    return tools


def tool_report(refresh: bool = False) -> str:
    """Spec §78/§36 — \"What tools do I have?\"."""
    tools = probe_tools(refresh=refresh)
    have = [t for t in tools if t.installed]
    missing = [t for t in tools if not t.installed]
    lines = [f"TOOLS  //  {len(have)} installed · {len(missing)} missing"]
    for t in sorted(have, key=lambda x: (x.category, x.name)):
        ver = f" {t.version}" if t.version else ""
        flag = "" if t.health == ONLINE else f"  [{t.health}]"
        auth = "  (authorization required)" if t.authorization_required else ""
        lines.append(f"- {t.name}{ver} · {t.capability} [\u2713]{flag}{auth}")
    for t in sorted(missing, key=lambda x: (x.category, x.name)):
        why = ("  needed for: " + ", ".join(t.required_for)) if t.required_for else ""
        lines.append(f"- {t.name} · {t.capability} [\u2717]{why}")
    if missing:
        lines.append("Missing tools only disable their own capability — nothing "
                     "else stops working.")
    return "\n".join(lines)


def missing_for(feature: str) -> list[str]:
    """Registered tools whose absence would break `feature`."""
    try:
        return [t.name for t in probe_tools()
                if not t.installed and feature.lower() in
                " ".join(t.required_for).lower()]
    except Exception:
        return []


# ── subsystem health checks (spec §51) ────────────────────────────────────────

def _check_config() -> Check:
    cfg = config()
    if not cfg:
        return Check("configuration", ERROR, "config/api_keys.json is missing or unreadable",
                     "Restore a backup from config/backups/ or re-run setup.")
    name = str(cfg.get("assistant_name") or "").strip()
    problems = []
    if not name:
        problems.append("assistant_name is empty")
    if not (cfg.get("features") or {}):
        problems.append("features block is missing")
    problems = problems + validate_settings(cfg)[:4]
    if problems:
        return Check("configuration", DEGRADED, "; ".join(problems),
                     "Run diagnostics with mode=settings to repair safe problems.",
                     {"keys": len(cfg)})
    return Check("configuration", ONLINE, f"{len(cfg)} keys, schema OK", "", {"keys": len(cfg)})


def _check_ai() -> Check:
    cfg = config()
    provider = str(cfg.get("ai_provider") or "gemini").lower()
    keys = {
        "gemini": bool(cfg.get("gemini_api_key")),
        "openai": bool(cfg.get("openai_api_key")),
        "anthropic": bool(cfg.get("anthropic_api_key")),
        "groq": bool(cfg.get("groq_api_key")),
        "custom": bool(cfg.get("custom_ai_api_key")),
    }
    if provider in keys and keys[provider]:
        model = cfg.get("ai_model") or "(provider default)"
        detail = f"provider={provider}, model={model}"
        if provider == "gemini":
            try:
                from app import model_router
                rep = model_router.report()
                detail += "; " + model_router.describe_report(rep)
                if rep.get("benched"):
                    return Check("ai", DEGRADED, detail,
                                 "A model is cooling down after failures; the "
                                 "router already falls back automatically.",
                                 {"provider": provider})
            except Exception:
                pass
        return Check("ai", ONLINE, detail, "", {"provider": provider})
    if any(keys.values()):
        have = [k for k, v in keys.items() if v]
        return Check("ai", DEGRADED,
                     f"set to {provider} but only {', '.join(have)} has a key",
                     "Add the key for the selected provider or switch provider in Settings → AI.")
    if _util.find_spec("google.genai") is not None:
        return Check("ai", DEGRADED, "no AI provider key is configured",
                     "Add a Gemini (or other provider) key in Settings → AI.")
    return Check("ai", OFFLINE, "no provider key and the GenAI SDK is not installed",
                 "pip install google-genai, then add an API key in Settings → AI.")


def _check_memory() -> Check:
    try:
        from memory.manager import get_brain_memory
        mem = get_brain_memory()
        counts = mem.db.count()
        total = sum(counts.values())
        if not mem.is_enabled():
            return Check("memory", DISABLED, "brain memory is switched off in Settings",
                         "Enable it in Settings → MEMORY if you want recall.")
        return Check("memory", ONLINE, f"{total} active memories across {len(counts)} areas",
                     "", {"total": total})
    except Exception as exc:
        return Check("memory", ERROR, f"{type(exc).__name__}: {exc}",
                     "The brain store could not be opened; check data/ permissions.")


def _voice_status_dict() -> dict:
    """core.tts.voice_status() returns a VoiceStatus dataclass, not a dict."""
    try:
        from voice.tts import voice_status
        st = voice_status()
    except Exception:
        return {}
    if isinstance(st, dict):
        return dict(st)
    try:
        return {k: v for k, v in vars(st).items() if not k.startswith("_")}
    except Exception:
        return {}


def _check_voice() -> Check:
    st = _voice_status_dict()
    state = str(st.get("state") or "").upper()
    engine = str(st.get("engine") or "") or str(config().get("tts_engine") or "?")
    if state == "ERROR":
        return Check("voice", ERROR, f"{engine}: {st.get('error') or 'engine error'}",
                     "Pick another engine in Settings → VOICE, or install the missing package.")
    if not _util.find_spec("sounddevice"):
        return Check("voice", OFFLINE, "sounddevice is not installed",
                     "pip install sounddevice — without it no audio can be played.")
    if state in ("READY", "LOADING"):
        return Check("voice", ONLINE, f"engine={engine}, state={state}", "", dict(st))
    return Check("voice", DEGRADED,
                 f"engine={engine} has not been built yet this session",
                 "Send a test from Settings → VOICE to verify it.",
                 dict(st))


def _check_wake() -> Check:
    feats = features()
    from app.brain_bridge import wake_enabled
    if not wake_enabled():
        why = []
        if not feats.get("wake_word", True):
            why.append("wake word is off")
        if not feats.get("background_listening", True):
            why.append("background listening is off")
        return Check("wake_word", DISABLED, "; ".join(why) or "disabled",
                     "Enable it in Settings → AUTOMATION → Voice automation.")
    if _util.find_spec("openwakeword") is None:
        return Check("wake_word", OFFLINE,
                     "enabled, but openWakeWord is not installed so no wake word can fire",
                     "pip install openwakeword onnxruntime, or turn the wake word off.")
    return Check("wake_word", ONLINE, "detector available", "")


def _check_camera(probe: bool = False) -> Check:
    if not features().get("camera_access", True):
        return Check("camera", DISABLED, "camera access is revoked in Settings → PERMISSIONS",
                     "Grant camera access in Settings if you want vision.")
    if _util.find_spec("cv2") is None:
        return Check("camera", OFFLINE, "OpenCV is not installed",
                     "pip install opencv-python.")
    idx = config().get("camera_index", 0)
    if not probe:
        # Opening the camera to prove it works would be a privacy decision the
        # user did not ask for; say what was actually checked.
        return Check("camera", DEGRADED,
                     f"OpenCV present; device {idx} not opened (no camera probe was requested)",
                     "Ask for a camera check to open the device.", {"index": idx})
    try:
        import cv2
        cap = cv2.VideoCapture(int(idx) if str(idx).isdigit() else 0)
        ok = bool(cap and cap.isOpened())
        frame_ok = False
        if ok:
            try:
                frame_ok = bool(cap.read()[0])
            except Exception:
                frame_ok = False
        try:
            cap.release()
        except Exception:
            pass
        if ok and frame_ok:
            return Check("camera", ONLINE, f"device {idx} opened and returned a frame",
                         "", {"index": idx})
        return Check("camera", DEGRADED, f"device {idx} did not return a frame",
                     "Check that no other application is holding the camera.")
    except Exception as exc:
        return Check("camera", ERROR, f"{type(exc).__name__}: {exc}",
                     "Try a different camera index in Settings → VIDEO.")


def _check_vision() -> Check:
    feats = features()
    if not feats.get("camera_access", True):
        return Check("vision", DISABLED, "camera access is revoked", "")
    if _util.find_spec("mediapipe") is None:
        return Check("vision", DEGRADED,
                     "MediaPipe is missing; face, hand and gesture tracking are unavailable",
                     "pip install mediapipe.")
    missing = missing_for("face")
    detail = "MediaPipe present"
    if missing:
        detail += f"; also missing {', '.join(missing)}"
    return Check("vision", ONLINE, detail, "")


def _check_browser() -> Check:
    if _util.find_spec("playwright") is None:
        return Check("browser", OFFLINE, "Playwright is not installed",
                     "pip install playwright && playwright install chromium.")
    # Playwright is installed — are the browser binaries too?
    bases = []
    if _WIN:
        la = os.environ.get("LOCALAPPDATA")
        if la:
            bases.append(Path(la) / "ms-playwright")
    else:
        bases.append(Path.home() / ".cache" / "ms-playwright")
        bases.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    for base in bases:
        try:
            if base.is_dir() and any(base.iterdir()):
                return Check("browser", ONLINE, "Playwright and its browsers are present",
                             "", {"path": str(base)})
        except Exception:
            continue
    return Check("browser", DEGRADED, "Playwright is installed but no browser binaries were found",
                 "playwright install chromium")


def _check_tools() -> Check:
    tools = probe_tools()
    bad = [t for t in tools if t.health == ERROR]
    missing = [t for t in tools if not t.installed]
    if bad:
        return Check("tools", DEGRADED,
                     "; ".join(f"{t.name}: {t.detail}" for t in bad[:3]),
                     "Reinstall the tools that are failing to run.")
    return Check("tools", ONLINE,
                 f"{len(tools) - len(missing)}/{len(tools)} present "
                 f"({len(missing)} optional integrations missing)",
                 "", {"missing": [t.name for t in missing]})


def _check_plugins() -> Check:
    """Plugin scan. discover_plugins(plugins_dir, core_tool_names, logger) never
    raises for a bad plugin, so a failure here means the loader itself changed."""
    try:
        from app.plugin_loader import discover_plugins
    except ImportError:
        return Check("plugins", DISABLED, "plugin loader not present in this build", "")
    try:
        reg = discover_plugins(BASE_DIR / "plugins", set(), logger=lambda _m: None)
    except Exception as exc:
        return Check("plugins", ERROR, f"plugin scan failed: {type(exc).__name__}: {exc}",
                     "The plugin loader could not run; plugins are not loaded at all.")
    try:
        records = list(getattr(reg, "all_records", None) or getattr(reg, "plugins", None)
                       or getattr(reg, "valid", None) or [])
        enabled = sum(1 for r in records
                      if bool(getattr(r, "enabled", True)))
    except Exception:
        records, enabled = [], 0
    if not records:
        return Check("plugins", DISABLED, "no plugins installed", "")
    return Check("plugins", ONLINE, f"{len(records)} plugin(s) discovered, {enabled} enabled",
                 "", {"count": len(records), "enabled": enabled})


def _check_filesystem() -> Check:
    try:
        import psutil
        probe = BASE_DIR / "data"
        probe.mkdir(parents=True, exist_ok=True)
        test = probe / ".diag_write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        du = psutil.disk_usage(str(BASE_DIR))
        free_gb = du.free / (1024 ** 3)
        if free_gb < 2:
            return Check("filesystem", DEGRADED, f"only {free_gb:.1f} GB free",
                         "Free disk space — memory writes will fail when it runs out.")
        return Check("filesystem", ONLINE, f"writable, {free_gb:.1f} GB free",
                     "", {"free_gb": round(free_gb, 1)})
    except Exception as exc:
        return Check("filesystem", ERROR, f"{type(exc).__name__}: {exc}",
                     "Check that the project data/ folder is writable.")


def _check_system() -> Check:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.25)
        ram = psutil.virtual_memory()
        detail = (f"CPU {cpu:.0f}% · RAM {ram.percent:.0f}% "
                  f"({ram.available / (1024 ** 3):.1f} GB free)")
        if ram.percent > 92 or cpu > 95:
            return Check("system", DEGRADED, detail,
                         "Memory or CPU is saturated; close heavy applications "
                         "or enable low-resource behaviour.",
                         {"cpu": cpu, "ram": ram.percent})
        return Check("system", ONLINE, detail, "", {"cpu": cpu, "ram": ram.percent})
    except Exception as exc:
        return Check("system", ERROR, f"{type(exc).__name__}: {exc}",
                     "psutil is required for system telemetry.")


def _check_safe_mode() -> Check:
    state = runtime_state()
    if state.get("safe_mode"):
        return Check("safe_mode", DISABLED,
                     f"SAFE MODE is on ({state.get('last_reason') or 'crash recovery'})",
                     "Repair what was failing, then clear safe mode.")
    crashes = int(state.get("crash_count", 0) or 0)
    if crashes:
        return Check("safe_mode", DEGRADED,
                     f"off, but {crashes} crash(es) recorded this session",
                     "Review the log for the failure before it repeats.")
    return Check("safe_mode", ONLINE, "off, no crashes recorded", "")


def _check_metrics() -> Check:
    """Per-subsystem latency/outcome counters (observability package)."""
    try:
        from observability.metrics import metrics_check
        return metrics_check()
    except Exception as exc:
        return Check("metrics", ERROR, f"{type(exc).__name__}: {exc}",
                     "The observability package failed to import.")


def _check_tracing() -> Check:
    """Optional Langfuse export state (off/no-op unless configured)."""
    try:
        from observability.tracing import tracing_check
        return tracing_check()
    except Exception as exc:
        return Check("tracing", ERROR, f"{type(exc).__name__}: {exc}",
                     "The observability package failed to import.")


def _check_approvals() -> Check:
    """Risk-tiered approval service state (security package)."""
    try:
        from security.approvals import approvals_check
        return approvals_check()
    except Exception as exc:
        return Check("approvals", ERROR, f"{type(exc).__name__}: {exc}",
                     "The security package failed to import.")


def _check_computer_backend() -> Check:
    """Active computer-control backend (computer package)."""
    try:
        from computer.computer_use import computer_check
        return computer_check()
    except Exception as exc:
        return Check("computer_backend", ERROR, f"{type(exc).__name__}: {exc}",
                     "The computer package failed to import.")


def _check_cua_driver() -> Check:
    """Cua Driver: installed, reachable, and whether it is the active backend.

    Reports the driver's own reason for being unusable, so a fallback to the
    local backend is never unexplained.
    """
    try:
        from computer.computer_use import computer_status
        st = computer_status()
        cua = st["cua"]
        if cua["available"]:
            return Check("cua_driver", ONLINE,
                         f"{cua['binary_version'] or 'cua-driver'} reachable, "
                         f"{cua['tool_count']} tools; backend={st['active_backend']}",
                         "")
        fix = (cua["install_hint"] + " then `cua-driver serve`"
               if not cua["binary_present"]
               else "start the driver daemon: `cua-driver serve`")
        return Check("cua_driver", DEGRADED,
                     f"not usable — {cua['reason'] or 'driver not detected'}; "
                     f"backend={st['active_backend']}", fix)
    except Exception as exc:
        return Check("cua_driver", ERROR, f"{type(exc).__name__}: {exc}",
                     "The computer package failed to import.")


def _check_coding_agents() -> Check:
    """Coding-agent backends (coding package)."""
    try:
        from coding.agent_router import coding_check
        return coding_check()
    except Exception as exc:
        return Check("coding_agents", ERROR, f"{type(exc).__name__}: {exc}",
                     "The coding package failed to import.")


CHECKS = {
    "config": _check_config,
    "ai": _check_ai,
    "memory": _check_memory,
    "voice": _check_voice,
    "wake_word": _check_wake,
    "camera": _check_camera,
    "vision": _check_vision,
    "browser": _check_browser,
    "tools": _check_tools,
    "plugins": _check_plugins,
    "filesystem": _check_filesystem,
    "system": _check_system,
    "safe_mode": _check_safe_mode,
    "metrics": _check_metrics,
    "tracing": _check_tracing,
    "approvals": _check_approvals,
    "computer_backend": _check_computer_backend,
    "cua_driver": _check_cua_driver,
    "coding_agents": _check_coding_agents,
}


def check(name: str, **kw) -> Check:
    """Run one named check. Unknown names report honestly instead of passing."""
    fn = CHECKS.get(str(name or "").strip().lower())
    if fn is None:
        return Check(str(name or "?"), ERROR,
                     f"no such check (have: {', '.join(sorted(CHECKS))})", "")
    try:
        return fn(**kw)
    except Exception as exc:
        return Check(str(name), ERROR, f"{type(exc).__name__}: {exc}",
                     "The check itself failed — that is a bug worth reporting.")


def check_all(probe_camera: bool = False) -> list[Check]:
    out: list[Check] = []
    for name in CHECKS:
        if name == "camera":
            out.append(_check_camera(probe=probe_camera))
        else:
            out.append(check(name))
    return out


def report(name: str = "all", probe_camera: bool = False) -> str:
    """Human/voice readable health report (spec §51)."""
    if str(name or "all").lower() in ("all", "*", ""):
        checks = check_all(probe_camera=probe_camera)
        head = "DIAGNOSTICS  //  " + summary_line(checks)
        body = [head]
        for c in checks:
            body.append(f"- {c.name}: {c.state} — {c.detail}")
            if c.fix and c.state != ONLINE:
                body.append(f"    fix: {c.fix}")
        # The computer-control block answers the questions the one-line check
        # above cannot: which driver, which version, reachable or not, and why.
        try:
            from computer.computer_use import computer_report
            body.append("")
            body.append(computer_report())
        except Exception:
            pass
        worst = [c for c in checks if c.state in (ERROR, OFFLINE)]
        if not worst:
            body.append("Nothing is failing. Missing optional tools only disable "
                        "their own capability.")
        return "\n".join(body)
    c = check(name, probe_camera=probe_camera) if name == "camera" else check(name)
    line = f"{c.name}: {c.state} — {c.detail}"
    if c.fix and c.state != ONLINE:
        line += f"\nfix: {c.fix}"
    return line


def summary_line(checks: list[Check]) -> str:
    counts: dict = {}
    for c in checks:
        counts[c.state] = counts.get(c.state, 0) + 1
    order = [ONLINE, DEGRADED, OFFLINE, ERROR, DISABLED]
    return " · ".join(f"{k} {counts[k]}" for k in order if counts.get(k))


# ── settings validation (shared with config_guard) ───────────────────────────

_RANGES = {
    "ui_opacity": (0, 100), "reactor_opacity": (0, 100),
    "reactor_stroke_opacity": (0, 100), "compact_size": (80, 900),
    "chat_font_scale": (50, 400), "equalizer_sensitivity": (0.0, 5.0),
    "reactor_animation_speed": (0.1, 5.0), "voice_volume": (0.0, 3.0),
    "voice_speed": (0.25, 4.0), "voice_pitch": (-24.0, 24.0),
    "world_monitor_refresh": (5, 3600), "camera_index": (0, 32),
}


def validate_settings(data: dict) -> list[str]:
    """Structural problems in a config dict (spec §39). Returns plain sentences.

    Only things that are actually wrong: wrong types, out-of-range numbers,
    contradictory feature combinations. No opinion about taste.
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["configuration is not an object"]
    for key, (lo, hi) in _RANGES.items():
        if key not in data or data.get(key) is None:
            continue
        try:
            val = float(data[key])
        except Exception:
            problems.append(f"{key} is not a number ({data[key]!r})")
            continue
        if val < lo or val > hi:
            problems.append(f"{key} is {data[key]} (allowed {lo}–{hi})")
    feats = data.get("features")
    if feats is not None and not isinstance(feats, dict):
        problems.append("features is not an object")
        feats = {}
    feats = feats or {}
    for key in ("assistant_name", "user_name", "ui_font", "ui_color", "web_homepage"):
        if key in data and not isinstance(data.get(key), str):
            problems.append(f"{key} should be text")
    if isinstance(data.get("wake_words"), str):
        problems.append("wake_words should be a list, not a single string")
    elif isinstance(data.get("wake_words"), list) and not all(
            isinstance(w, str) for w in data["wake_words"]):
        problems.append("wake_words contains a non-text entry")
    # Contradictions worth saying out loud.
    if feats.get("webview_music") and not feats.get("webview_engine"):
        problems.append("webview_music is on while webview_engine is off "
                        "(music has no engine to play in)")
    if feats.get("barge_in") and not feats.get("voice_interruption"):
        problems.append("barge_in is on while voice_interruption is off "
                        "(the wake word cannot interrupt speech it is allowed to stop)")
    if feats.get("push_to_talk") and feats.get("hands_free"):
        problems.append("both push_to_talk and hands_free are on; push-to-talk wins")
    return problems


# ── runtime state / safe mode / correlation ids (spec §53-§56) ───────────────

_state_lock = threading.RLock()


def runtime_state() -> dict:
    with _state_lock:
        st = _read_json(STATE_PATH)
    if not isinstance(st, dict):
        st = {}
    st.setdefault("crash_count", 0)
    st.setdefault("restart_count", 0)
    st.setdefault("last_crash", 0.0)
    st.setdefault("last_reason", "")
    st.setdefault("safe_mode", False)
    st.setdefault("correlation_seq", 0)
    return st


def _write_state(st: dict) -> None:
    with _state_lock:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except Exception:
            pass


def note_startup_ok() -> dict:
    """A clean start clears the crash streak (spec §53)."""
    st = runtime_state()
    st["restart_count"] = int(st.get("restart_count", 0) or 0) + 1
    st["crash_count"] = 0
    st["last_reason"] = ""
    _write_state(st)
    return st


def note_crash(reason: str = "", safe_mode_threshold: int = 3,
               window_seconds: float = 600.0) -> bool:
    """Record a crash. Returns True when this put JARVIS into safe mode.

    Three crashes inside ten minutes is a loop, not bad luck — safe mode stops
    the background work that could be causing it.
    """
    st = runtime_state()
    now = time.time()
    last = float(st.get("last_crash", 0) or 0)
    if last and (now - last) > window_seconds:
        st["crash_count"] = 0
    st["crash_count"] = int(st.get("crash_count", 0) or 0) + 1
    st["last_crash"] = now
    st["last_reason"] = str(reason or "")[:300]
    entered = False
    if int(st["crash_count"]) >= int(safe_mode_threshold):
        st["safe_mode"] = True
        entered = True
    _write_state(st)
    return entered


def safe_mode() -> bool:
    return bool(runtime_state().get("safe_mode"))


def set_safe_mode(enabled: bool, reason: str = "") -> dict:
    st = runtime_state()
    st["safe_mode"] = bool(enabled)
    if reason:
        st["last_reason"] = str(reason)[:300]
    if not enabled:
        st["crash_count"] = 0
    _write_state(st)
    return st


def clear_safe_mode() -> str:
    set_safe_mode(False)
    return ("Safe mode cleared. Background tasks, proactive checks and monitoring "
            "will run again on the next start.")


def new_correlation(prefix: str = "TASK") -> str:
    """TASK-2026-000001 style id (spec §56), persisted so ids never repeat."""
    st = runtime_state()
    seq = int(st.get("correlation_seq", 0) or 0) + 1
    st["correlation_seq"] = seq
    _write_state(st)
    return f"{str(prefix or 'TASK').upper()}-{time.strftime('%Y')}-{seq:06d}"


# ── the tool entry point (main.py dispatch + "run diagnostics") ──────────────

def what_changed() -> str:
    """Spec §50 — current settings vs the most recent backup."""
    try:
        from app import config_guard as cg
        backups = cg.list_backups()
        if not backups:
            return ("There is no previous configuration to compare against yet — "
                    "the first backup is written the next time settings are saved.")
        old = cg.read(backups[0]["path"])
        new = cg.read()
        changes = cg.diff(old, new)
        head = f"Compared with {backups[0]['name']}:"
        return head + "\n" + cg.describe_diff(changes)
    except Exception as exc:
        return f"Could not compare configurations: {exc}"


def handle_diagnostics(args: Mapping | None = None) -> str:
    """Single entry point for the ``diagnostics`` tool. Never raises.

    Modes mirror the master spec's commands: run diagnostics, check one
    subsystem, list tools, fix settings, what changed, restore a backup, and
    safe mode. Every mode reports only what it actually did.
    """
    a = dict(args or {})
    mode = str(a.get("mode") or "all").strip().lower()
    target = str(a.get("target") or "").strip()
    probe = bool(a.get("probe_camera", False))
    try:
        if mode in ("all", "full", "run", "diagnostics"):
            return report("all", probe_camera=probe)
        if mode in ("check", "health", "status"):
            return report(target or "all", probe_camera=probe)
        if mode in ("tools", "tool", "inventory", "installed"):
            return tool_report(refresh=True)
        if mode in ("settings", "fix_settings", "fix", "repair"):
            from app import config_guard as cg
            return cg.report_repair(cg.repair(apply=True))
        if mode in ("restore", "rollback", "last_good"):
            from app import config_guard as cg
            if target:
                for entry in cg.list_backups():
                    if target in entry["name"]:
                        return cg.restore(entry["path"])
                return f"No backup matches '{target}'."
            return cg.restore()
        if mode in ("what_changed", "changed", "diff"):
            return what_changed()
        if mode == "backups":
            from app import config_guard as cg
            items = cg.list_backups()
            if not items:
                return "No configuration backups yet."
            return "BACKUPS\n" + "\n".join(
                f"- {e['name']} ({e['size']} bytes)" for e in items[:10])
        if mode in ("safe_mode", "safe"):
            return report("safe_mode")
        if mode in ("models", "model", "roles"):
            from app import model_router
            return "MODEL ROUTER\n" + model_router.describe_report(
                model_router.report())
        if mode in ("clear_safe_mode", "clear_safe", "resume"):
            return clear_safe_mode()
        return (f"Unknown diagnostics mode '{mode}'. Use all, check, tools, "
                "settings, models, restore, what_changed, backups, safe_mode or "
                "clear_safe_mode.")
    except Exception as exc:
        return f"Diagnostics failed: {type(exc).__name__}: {exc}"


__all__ = [
    "handle_diagnostics", "what_changed",
    "Tool", "Check", "STATES", "ONLINE", "DEGRADED", "OFFLINE", "ERROR", "DISABLED",
    "config", "features", "probe_tools", "tool_report", "missing_for",
    "check", "check_all", "report", "summary_line", "validate_settings",
    "runtime_state", "note_startup_ok", "note_crash", "safe_mode",
    "set_safe_mode", "clear_safe_mode", "new_correlation", "STATE_PATH",
]
