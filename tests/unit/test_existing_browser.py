"""Existing-browser resolution and dispatcher routing.

Two behaviours the Cua debugging pass depends on:

* ``computer.existing_browser`` decides how Jarvis can reach the browser the
  user already has open, and never reports a locked, signed-out profile as if it
  were the user's session.
* the tool dispatcher sends every GUI tool through the ``ComputerUse`` facade,
  so a backend other than the local one can actually become primary.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from computer import existing_browser as eb  # noqa: E402


# ── existing_browser.resolve: the three states ───────────────────────────────

def _patch(monkeypatch, *, pids, cdp):
    monkeypatch.setattr(eb, "running_pids", lambda browser="chrome": list(pids))
    monkeypatch.setattr(eb, "cdp_version", lambda port=eb.DEFAULT_DEBUG_PORT, timeout=0.6: dict(cdp))
    monkeypatch.setattr(eb, "profile_dir", lambda browser="chrome": "/tmp/profile")


def test_not_running_means_launch_the_real_profile(monkeypatch):
    _patch(monkeypatch, pids=[], cdp={})
    info = eb.resolve("chrome")
    assert info["how"] == eb.LAUNCH
    assert info["running"] is False
    assert info["cdp_reachable"] is False


def test_running_and_reachable_means_attach(monkeypatch):
    _patch(monkeypatch, pids=[11, 22], cdp={"Browser": "Chrome/141.0.0.0"})
    info = eb.resolve("chrome")
    assert info["how"] == eb.ATTACH
    assert info["cdp_reachable"] is True
    assert info["cdp_url"] == f"http://127.0.0.1:{eb.DEFAULT_DEBUG_PORT}"
    assert "Chrome/141" in info["cdp_browser"]


def test_running_but_unreachable_means_second_profile_and_says_why(monkeypatch):
    _patch(monkeypatch, pids=[11], cdp={})
    info = eb.resolve("chrome")
    assert info["how"] == eb.SECOND_PROFILE
    # The reason must name the remedy, not just the symptom.
    assert "--remote-debugging-port" in info["note"]
    assert "signed-in" in info["note"] or "sign-in" in info["note"]


def test_an_unreachable_running_browser_is_not_reported_as_an_existing_session(monkeypatch):
    """The regression this module exists for: a locked profile is not a session."""
    monkeypatch.setattr(eb, "resolve", lambda browser="chrome", port=eb.DEFAULT_DEBUG_PORT: {
        "browser": browser, "running": True, "pids": [1], "profile_dir": "/p",
        "cdp_port": 9222, "cdp_reachable": False, "cdp_url": "",
        "cdp_browser": "", "how": eb.SECOND_PROFILE, "note": "locked",
    })
    monkeypatch.setitem(sys.modules, "pygetwindow", None)  # optional dependency
    facts = eb.existing_session()
    assert facts["process"] is True
    assert facts["existing_session"] is False


def test_a_reachable_browser_is_reported_as_an_existing_session(monkeypatch):
    monkeypatch.setattr(eb, "resolve", lambda browser="chrome", port=eb.DEFAULT_DEBUG_PORT: {
        "browser": browser, "running": True, "pids": [1], "profile_dir": "/p",
        "cdp_port": 9222, "cdp_reachable": True, "cdp_url": "http://127.0.0.1:9222",
        "cdp_browser": "Chrome/141", "how": eb.ATTACH, "note": "attached",
    })
    monkeypatch.setitem(sys.modules, "pygetwindow", None)
    facts = eb.existing_session()
    assert facts["existing_session"] is True
    assert facts["browser"] == "chrome"


def test_the_probe_only_ever_touches_loopback(monkeypatch):
    """Attaching must never reach out to a network address."""
    seen: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"Browser": "Chrome/141"}'

    def _fake_urlopen(url, timeout=None):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(eb.urllib.request, "urlopen", _fake_urlopen)
    eb.cdp_version()
    assert seen and all(u.startswith("http://127.0.0.1:") for u in seen)


def test_take_profile_note_is_consumed_once():
    from computer.browser_control import _BrowserSession
    session = _BrowserSession("chrome")
    assert session.take_profile_note() == ""
    session._profile_note = "a signed-out second profile"
    assert session.take_profile_note() == "a signed-out second profile"
    assert session.take_profile_note() == ""


class _FakeContext:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _close(session):
    import asyncio
    asyncio.run(session._async_close())


def test_closing_a_launched_session_closes_the_context():
    from computer.browser_control import _BrowserSession
    session = _BrowserSession("chrome")
    context, pw = _FakeContext(), _FakePlaywright()
    session._context, session._pw = context, pw
    _close(session)
    assert context.closed is True
    assert pw.stopped is True


def test_closing_an_attached_session_leaves_the_users_browser_alone():
    """'Close the browser' must not shut down the Chrome the user is using."""
    from computer.browser_control import _BrowserSession
    session = _BrowserSession("chrome")
    context, pw = _FakeContext(), _FakePlaywright()
    session._context, session._pw, session._attached = context, pw, True
    _close(session)
    assert context.closed is False, "the user's own browser was closed"
    assert pw.stopped is True, "Playwright itself should still be released"


# ── dispatcher routing ───────────────────────────────────────────────────────

def test_every_gui_tool_goes_through_the_facade():
    """main.py must not call the local automation functions directly.

    Without this the backend selection is dead code and only ever the local
    controller can run, no matter what is installed.
    """
    source = pathlib.Path("main.py").read_text(encoding="utf-8")
    for call in (
        "open_app(parameters=",
        "browser_control(parameters=",
        "desktop_control(parameters=",
        "computer_control(parameters=",
        "computer_settings(parameters=",
    ):
        assert call not in source, f"dispatcher still calls {call} directly"
    assert source.count("await _computer(name, args)") == 5
    assert "async def _computer(name_, args_):" in source


def test_the_dispatcher_helper_runs_the_facade_in_an_executor_and_reports_failures():
    source = pathlib.Path("main.py").read_text(encoding="utf-8")
    helper = source.split("async def _computer(name_, args_):", 1)[1].split("loop   ", 1)[0]
    assert "get_computer_use().execute(" in helper
    assert "run_in_executor" in helper
    assert "ComputerActionError" in helper
    assert "exc.detail" in helper and "exc.summary" in helper


def test_the_facade_still_reports_the_reason_for_the_selected_backend():
    """Diagnostics depend on the reason surviving selection."""
    from computer.computer_use import ComputerUse
    backend, reason, mode = ComputerUse._select_backend()
    assert mode in ("auto", "on", "off")
    assert reason
    # A local selection must always be explained.
    if getattr(backend, "name", "") == "local" and mode == "auto":
        assert "unavailable" in reason.lower() or "off" in reason.lower()
