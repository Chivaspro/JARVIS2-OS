"""Memory backend interface (task 6.1) — long-term memory behind one contract.

The default backend is the existing local store (``BrainMemory``); Mem0 is an
optional backend enabled by ``features.mem0_memory`` + an API key. Backends
are swappable without touching callers: everything goes through
``get_memory_backend()``.

Scope of the interface is deliberately small — only *long-term* facts and
recall. Session context, task state and ephemeral turns never reach a backend
(memory-policy spec; see memory/policy.py for the promotion rules).
"""

from __future__ import annotations

import threading
from typing import Any, Protocol


class MemoryBackend(Protocol):
    """Backend-agnostic long-term memory contract."""

    name: str

    def write(self, content: str, *, category: str = "",
              project_id: str = "", explicit: bool = False) -> bool:
        """Persist one durable fact. Returns True when stored."""
        ...

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Recall relevant facts (most relevant first)."""
        ...

    def available(self) -> bool:
        """False when the backend cannot serve right now (caller falls back)."""
        ...


class LocalMemoryBackend:
    """The existing sqlite + long_term.json store, unchanged."""

    name = "local"

    def __init__(self, memory=None) -> None:
        self._memory = memory

    def _brain_memory(self):
        if self._memory is None:
            from memory.manager import get_brain_memory
            self._memory = get_brain_memory()
        return self._memory

    def write(self, content: str, *, category: str = "",
              project_id: str = "", explicit: bool = False) -> bool:
        try:
            mid = self._brain_memory().remember(
                content, category=category or None,
                project_id=project_id, explicit=explicit,
            )
            return mid is not None
        except Exception:
            return False

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        try:
            mem = self._brain_memory().retrieve(query, limit=limit)
            return list(mem or [])
        except Exception:
            return []

    def available(self) -> bool:
        try:
            return self._brain_memory().is_enabled()
        except Exception:
            return False


_backend: MemoryBackend | None = None
_lock = threading.Lock()


def _mem0_configured() -> bool:
    try:
        from config.settings import get_feature
        if not bool(get_feature("mem0_memory")):
            return False
        import os
        return bool(os.environ.get("MEM0_API_KEY")
                    or _api_key_from_config())
    except Exception:
        return False


def _api_key_from_config() -> str:
    try:
        from config.settings import get
        return str(get("mem0_api_key", "") or "")
    except Exception:
        return ""


def get_memory_backend() -> MemoryBackend:
    """Process-wide backend: Mem0 when enabled+configured, local otherwise."""
    global _backend
    if _backend is not None:
        return _backend
    with _lock:
        if _backend is not None:
            return _backend
        if _mem0_configured():
            try:
                from memory.mem0_client import Mem0Backend
                candidate = Mem0Backend(
                    api_key=_api_key_from_config() or None,
                    fallback=LocalMemoryBackend(),
                )
                if candidate.available():
                    _backend = candidate
                    return _backend
            except Exception:
                pass  # fall through to local
        _backend = LocalMemoryBackend()
    return _backend


def reset_memory_backend() -> None:
    """Test/teardown hook."""
    global _backend
    with _lock:
        _backend = None
