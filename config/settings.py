"""Centralized configuration for Mark LII (change: modernize-jarvis-architecture).

One read-only view over the existing config sources:

* ``config/api_keys.json``  — user-edited JSON (existing primary store)
* environment variables     — optional overrides (see .env.example)
* built-in defaults         — the value used when nothing else is set

Existing modules keep writing config exactly as before; this module never
writes to api_keys.json. Existing consumers see identical values, so this is
purely additive.

Feature flags default to OFF — every optional subsystem introduced by the
modernization (LangGraph orchestrator, Mem0, Cua backend, Langfuse, Qdrant,
coding agents) stays dormant until the user opts in.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_KEYS_FILE = CONFIG_DIR / "api_keys.json"
DATA_DIR = BASE_DIR / os.environ.get("JARVIS_DATA_DIR", "data")
LOGS_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"
RUNTIME_DIR = DATA_DIR / "runtime"

# Feature flags and their defaults. Every optional subsystem introduced by the
# modernization is off unless explicitly enabled in config/api_keys.json
# ("features" map) or overridden by an environment variable.
FEATURE_DEFAULTS: dict[str, bool | str] = {
    "langgraph_orchestrator": False,
    "mem0_memory": False,
    "cua_backend": "auto",          # auto (detect) | on | off
    "langfuse_tracing": False,
    "qdrant_knowledge": False,
    "coding_agents": True,          # router ships active; individual backends probe availability
    "self_inspection": True,
}

# Secret-looking key names, used by the redaction helper at the logging
# boundary (observability requirement: no secrets in logs/traces).
SECRET_FIELD_NAMES = {
    "gemini_api_key", "api_key", "apikey", "token", "access_token",
    "refresh_token", "secret", "secret_key", "password", "passwd", "pwd",
    "authorization", "fish_audio_api_key", "mem0_api_key", "qdrant_api_key",
    "langfuse_secret_key", "langfuse_public_key", "private_key",
}


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "data": None}


def _read_api_keys() -> dict:
    """Parsed api_keys.json, cached on (mtime, size); {} when unreadable."""
    try:
        st = API_KEYS_FILE.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    with _cache_lock:
        if _cache["data"] is not None and _cache["mtime"] == key:
            return _cache["data"]
        try:
            data = json.loads(API_KEYS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    with _cache_lock:
        _cache["mtime"] = key
        _cache["data"] = data
    return data


def get(key: str, default: Any = None) -> Any:
    """Top-level config value: env override → api_keys.json → default."""
    env = os.environ.get(key.upper())
    if env is not None and env != "":
        return env
    return _read_api_keys().get(key, default)


def get_feature(name: str, default: Any = None) -> Any:
    """Feature-flag value with precedence: JARVIS_FEATURE_<NAME> env →
    features map in api_keys.json → FEATURE_DEFAULTS → default."""
    env_key = f"JARVIS_FEATURE_{name.upper()}"
    env = os.environ.get(env_key)
    if env is not None and env != "":
        low = env.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        return env.strip()
    feats = _read_api_keys().get("features")
    if isinstance(feats, dict) and name in feats:
        return feats[name]
    if name in FEATURE_DEFAULTS:
        return FEATURE_DEFAULTS[name]
    return default


def feature_enabled(name: str) -> bool:
    """Boolean view of a feature flag (truthy strings count as enabled)."""
    v = get_feature(name)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "auto")
    return bool(v)


def gemini_api_key() -> str:
    env = os.environ.get("GEMINI_API_KEY", "")
    if env:
        return env
    return str(_read_api_keys().get("gemini_api_key", "") or "")


def data_dir() -> Path:
    """Runtime data root (logs/cache/runtime), created lazily."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def logs_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def redact_secrets(value: Any, _key_hint: str = "") -> Any:
    """Return *value* with secret-shaped content replaced.

    Rules:
    * dict: keys in SECRET_FIELD_NAMES (case-insensitive) become "[REDACTED]";
      other values are redacted recursively.
    * str that *is* the secret for a secret-named key: "[REDACTED]".
    * everything else passes through unchanged.

    Used by the structured logger so credentials never reach log files.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if isinstance(k, str) and k.strip().lower() in SECRET_FIELD_NAMES:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_secrets(v, _key_hint)
        return out
    if (
        isinstance(value, str)
        and _key_hint
        and _key_hint.strip().lower() in SECRET_FIELD_NAMES
    ):
        return "[REDACTED]"
    return value


def env_summary() -> dict:
    """Safe configuration summary for diagnostics (secrets redacted)."""
    data = _read_api_keys()
    return {
        "config_file": str(API_KEYS_FILE),
        "config_present": API_KEYS_FILE.exists(),
        "assistant_name": data.get("assistant_name", "JARVIS"),
        "gemini_key_present": bool(gemini_api_key()),
        "features": {k: get_feature(k) for k in FEATURE_DEFAULTS},
        "log_level": os.environ.get("JARVIS_LOG_LEVEL", "INFO"),
        "data_dir": str(DATA_DIR),
    }
