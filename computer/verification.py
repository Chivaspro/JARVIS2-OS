"""Post-action verification (task 4.5) — actions are verified, not assumed.

A tool returning without an exception is not proof an action achieved its
intent. For the mutating facade actions that admit a cheap, deterministic
check, ``verify_action`` inspects observable state and reports a three-state
outcome — ``verified`` / ``failed`` / ``unverified`` — alongside the backend's own
result. ``verified`` is true only when a check actually ran and passed; an action
for which no check exists is ``unverified``, never ``verified``.

* ``open_app``        — the expected application appears among open windows
                        (best-effort: window enumeration is optional).
* ``browser_control`` go_to — the active browser URL contains the target.
* ``computer_control`` typing actions — the text is on the clipboard after a
  copy/paste pair (only when the caller passes ``expect_clipboard``).

Verification failures do not raise: the returned dict carries the evidence so
the orchestrator/verifier can replan, and the tool layer can report failure
instead of silent success. Never fatal — an error during verification yields
``state="failed"`` rather than an exception. Use :func:`state_of` rather than the
raw ``verified`` flag when reporting to a user, so "unchecked" and "checked and
failed" are never confused.
"""

from __future__ import annotations

import re
import time
from typing import Any


def _title_matches_app(title: str, app: str) -> bool:
    """Does this window title belong to this application?

    A plain substring test is not good enough, and the cost was real: "notepad"
    matches "Notepad++", so an unrelated editor's window counted as proof that
    Notepad had opened and the next step typed into whatever was focused. The
    boundary rejects a product-name continuation (``Notepad++``) while still
    accepting "Untitled - Notepad".
    """
    if not title or not app:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(app)}(?![A-Za-z0-9+])",
                     title, re.IGNORECASE) is not None


def _window_titles() -> list[str]:
    """Best-effort list of visible window titles ([] when unavailable)."""
    titles: list[str] = []
    try:
        import pygetwindow as gw
        titles = [t for t in (gw.getAllTitles() or []) if t]
    except Exception:
        try:
            import pywinauto
            from pywinauto import Desktop
            titles = [w.window_text() for w in Desktop(backend="uia").windows()]
        except Exception:
            return []
    return titles


def verify_action(
    tool: str,
    parameters: dict | None,
    result: str,
    backend: str = "",
    timeout_s: float = 2.5,
) -> dict[str, Any]:
    """Verify one executed action. Returns
    ``{verified: bool, reason: str, evidence: ...}``; never raises."""
    p = dict(parameters or {})
    action = str(p.get("action", "")).strip().lower()
    try:
        deadline = time.monotonic() + max(0.0, timeout_s)

        if tool == "open_app":
            app = str(p.get("app_name", "")).strip().lower()
            if not app:
                return {"verified": False, "state": "failed",
                        "reason": "no app_name to verify", "evidence": None}
            while time.monotonic() < deadline:
                titles = _window_titles()
                hit = next((t for t in titles if _title_matches_app(t, app)), "")
                if hit:
                    return {"verified": True, "state": "verified",
                            "reason": "window found",
                            "evidence": {"match": app, "title": hit[:120]}}
                time.sleep(0.25)
            return {"verified": False, "state": "failed",
                    "reason": "no matching window appeared",
                    "evidence": {"expected": app}}

        if tool == "browser_control" and action == "go_to":
            target = str(p.get("url", "")).strip()
            if not target:
                # Nothing was asked for, so nothing was checked. That is not a
                # pass — it is the case that must never be reported as one.
                return {"verified": False, "state": "unverified",
                        "reason": "no navigation target to verify", "evidence": None}
            host = target.lower().replace("https://", "").replace("http://", "")
            host = host.split("/")[0]
            # Only read the URL the native navigation already recorded. Asking
            # the automation session for its URL would *start* one, opening a
            # second browser window just to check what the first one did.
            while time.monotonic() < deadline:
                seen = ""
                try:
                    from computer.browser_control import _registry
                    seen = _registry.native_url()
                except Exception:
                    seen = ""
                if seen and host and host in seen.lower():
                    return {"verified": True, "state": "verified",
                            "reason": "navigation recorded",
                            "evidence": {"url": seen[:200]}}
                time.sleep(0.2)
            return {"verified": False, "state": "failed",
                    "reason": "navigation not confirmed",
                    "evidence": {"expected": host}}

        if tool == "computer_control" and action == "paste":
            try:
                import pyperclip
                text = str(p.get("text", "") or "")
                if text and text in (pyperclip.paste() or ""):
                    return {"verified": True, "state": "verified",
                            "reason": "clipboard carries the text", "evidence": None}
                return {"verified": False, "state": "failed",
                        "reason": "clipboard does not carry the text", "evidence": None}
            except Exception as exc:
                return {"verified": False, "state": "failed",
                        "reason": f"clipboard unavailable: {exc}", "evidence": None}

        # No deterministic check for this action. Reported as *unverified*: the
        # boolean stays False so nothing downstream can read this as a pass, and
        # the state says which kind of non-pass it is. Claiming `verified: True`
        # here was a false pass — the same class of defect that once let an
        # open_app check match the wrong window and type into it.
        return {"verified": False, "state": "unverified",
                "reason": "no verification defined for this action",
                "evidence": None}

    except Exception as exc:
        return {"verified": False, "state": "failed",
                "reason": f"verification error: {exc}", "evidence": None}


def state_of(verification: dict | None) -> str:
    """The three-state outcome of a verification result.

    Tolerates callers that supply only the older ``verified`` boolean, so the
    richer state never becomes a reason for a caller to break.
    """
    v = verification or {}
    state = str(v.get("state") or "").strip().lower()
    if state in ("verified", "failed", "unverified"):
        return state
    return "verified" if v.get("verified") else "failed"
