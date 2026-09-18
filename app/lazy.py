"""core/lazy.py — lazy heavy-module proxy.

Importing pyautogui (~5 s), google.genai (~4-13 s) or playwright at module
import time delays JARVIS becoming ready. These proxies take the place of the
real module at import time and perform the actual import on the first
attribute access, so every existing ``pyautogui.foo(...)`` / ``pyperclip.x``
call site keeps working unchanged while the cost moves to the first *use*
(inside a tool worker), not the boot.

    from app.lazy import lazy_module

    def _setup_pg(m):          # runs once, right after the real import
        m.FAILSAFE = True
        m.PAUSE    = 0.05

    pyautogui = lazy_module("pyautogui", _setup_pg)
"""
from __future__ import annotations


class lazy_module:
    """Stand-in for a module imported on first attribute access.

    ``bool(proxy)`` forces the import (cached) and reports whether it
    succeeded — this preserves the old ``if _PYAUTOGUI:`` gating checks.
    """

    __slots__ = ("_name", "_setup", "_real", "_failed")

    def __init__(self, name: str, setup=None):
        self._name   = name
        self._setup  = setup
        self._real   = None
        self._failed = False

    def _load(self):
        if self._real is not None:
            return self._real
        if self._failed:
            raise ImportError(f"No module named {self._name!r}")
        try:
            real = __import__(self._name)
        except Exception:
            self._failed = True
            raise
        if self._setup is not None:
            try:
                self._setup(real)
            except Exception:
                pass
        self._real = real
        return real

    def __getattr__(self, item):
        return getattr(self._load(), item)

    def __bool__(self):
        try:
            self._load()
            return True
        except Exception:
            return False