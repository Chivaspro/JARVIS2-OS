"""Local automation backend — the default controller on Windows.

Wraps the proven implementations that now live in this package
(``computer_control``, ``browser_control``, ``windows``, ``desktop_ops``).
This is a *routing* layer: the automation code itself is the same code that
has been running in production, so timing-sensitive behavior is preserved
(change: modernize-jarvis-architecture, task 4.2/4.3).
"""

from __future__ import annotations

from typing import Any


class BackendUnavailable(RuntimeError):
    """Raised by a backend that cannot serve this host right now."""


# Actions that need a working browser stack rather than just this host. Owned
# here because this is also where availability is answered, so there is exactly
# one definition of "this action needs a browser" for the backend, the facade's
# preflight and the classifier to share.
BROWSER_DEPENDENT_ACTIONS = {
    "go_to", "navigate", "browse", "new_tab", "browser_click",
    "browser_type", "smart_click", "fill", "get_url", "scroll_page",
}


def needs_browser(tool: str, action: str = "") -> bool:
    """Whether this request depends on the browser stack being usable."""
    return (str(tool or "") == "browser_control"
            or str(action or "").strip().lower() in BROWSER_DEPENDENT_ACTIONS)


class LocalBackend:
    """The consolidated local controller (pyautogui/pywinauto/Playwright)."""

    name = "local"

    def available(self, tool: str = "", action: str = "") -> bool:
        """Whether this backend can serve the request *right now*.

        The local controller is always the fallback for general GUI work, so it
        answers ``True`` there. Browser-dependent work is the exception: it needs
        Playwright *and* a usable browser, and answering an unconditional ``True``
        is what let an unusable browser stack look healthy right up to the moment
        an action failed.

        The answer comes from the same diagnostics check that `run diagnostics`
        and the failure path use, so there is one source of truth for "is the
        browser stack usable" instead of a second one that can drift.
        """
        if not needs_browser(tool, action):
            return True
        try:
            from observability.diagnostics import _check_browser
            return _check_browser().state == "ONLINE"
        except Exception:
            # A diagnostics hiccup must never be what declares the browser
            # unusable — the action's own result is more informative.
            return True

    def execute(
        self,
        tool: str,
        parameters: dict | None,
        response=None,
        player=None,
        session_memory=None,
    ) -> str:
        params = parameters or {}
        if tool == "computer_control":
            from computer.computer_control import computer_control
            return computer_control(parameters=params, response=response,
                                    player=player, session_memory=session_memory)
        if tool == "browser_control":
            from computer.browser_control import browser_control
            return browser_control(parameters=params, response=response,
                                   player=player, session_memory=session_memory)
        if tool == "open_app":
            from computer.windows import open_app
            return open_app(parameters=params, response=response,
                            player=player, session_memory=session_memory)
        if tool == "desktop_control":
            from computer.desktop_ops import desktop_control
            return desktop_control(parameters=params, response=response,
                                   player=player, session_memory=session_memory)
        if tool == "computer_settings":
            # Window/system settings stay in actions/ for now (task 4.2 scope
            # covers the four moved modules); routed here so callers have one
            # entry point regardless.
            from actions.computer_settings import computer_settings
            return computer_settings(parameters=params, response=response,
                                     player=player, session_memory=session_memory)
        raise BackendUnavailable(f"local backend does not implement '{tool}'")


def status_detail() -> dict[str, Any]:
    """Cheap capability snapshot for diagnostics (no GUI calls)."""
    out: dict[str, Any] = {"name": LocalBackend.name, "available": True}
    for mod, label in (("pyautogui", "mouse_keyboard"),
                       ("pywinauto", "windows_apps"),
                       ("mss", "fast_screen_capture")):
        try:
            __import__(mod)
            out[label] = True
        except Exception:
            out[label] = False
    try:
        import importlib.util as _u
        out["playwright"] = _u.find_spec("playwright") is not None
    except Exception:
        out["playwright"] = False
    return out
