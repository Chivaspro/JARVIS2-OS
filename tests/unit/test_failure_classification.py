"""Failure classification, Cua session recovery, preflight (change:
fix-computer-control-driver-diagnostics).

These pin the behaviour the change exists to provide:

* a failure names its real cause and a remedy a person can act on, and the
  catch-all label is only ever the last resort;
* a dead Cua driver session recovers instead of disabling the backend for the
  life of the process — the actual cause of "browser automation is blocked due
  to a driver error";
* a preflight answer comes from the same checks the diagnostics page reports,
  so the two cannot describe the same situation differently.

No driver has to be installed: the fake below speaks the CLI's argv shape. Where
a real driver is reachable, one test asks it instead of a snapshot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from computer import cua_backend as cb  # noqa: E402
from computer.computer_use import (  # noqa: E402
    ComputerActionError,
    ComputerUse,
    _classify,
    _next_action,
    browser_binaries_remedy,
)

REACHABLE = {
    "host": "Windows", "host_supported": True,
    "binary_path": "C:/cua-driver.exe", "binary_version": "cua-driver 0.28.2",
    "daemon_running": True, "tools": sorted(cb._ACTION_TOOLS.values()) + [
        "start_session", "list_sessions", "get_session"],
    "reason": "", "install_hint": "irm https://cua.ai/driver/install.ps1 | iex",
}


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture(autouse=True)
def _fresh():
    cb.reset_probe_cache()
    cb.forget_session("jarvis")
    yield
    cb.reset_probe_cache()
    cb.forget_session("jarvis")


def _driver(monkeypatch, probe=None, session_ends_once=False):
    """A fake driver that records every tool it is asked for.

    ``session_ends_once`` reproduces the real rejection captured from the driver:
    the first call that is not ``start_session`` is refused because the session
    has ended, exactly as it was on this machine.
    """
    monkeypatch.setattr(cb, "_probe", lambda: dict(probe or REACHABLE))
    cb.reset_probe_cache()
    seen: list[str] = []
    state = {"ended": bool(session_ends_once)}

    def handler(binary, args, timeout):
        if args[:1] in (["dump-docs"], ["describe"]):
            return _Proc("{}")
        tool = args[1] if len(args) > 1 else ""
        seen.append(tool)
        if state["ended"] and tool != "start_session":
            state["ended"] = False
            return _Proc(
                "", f"session 'jarvis' has ended; tool call '{tool}' was rejected. "
                    f"Call start_session with this id to revive it before issuing "
                    f"further actions", 1)
        if tool == "browser_prepare":
            return _Proc('{"target_id": "t-1", "tab_id": "tab-1"}')
        return _Proc("✅ ok")

    monkeypatch.setattr(cb, "_run", handler)
    return seen


def _no_existing_session(monkeypatch, browser="chrome"):
    """Answer "which browser can we reach" without reading this machine.

    The shared resolver enumerates real processes and probes the DevTools port, so a
    test that drives the browser path would otherwise take a different route
    depending on whether the person running the suite has a browser open. This
    pins the "nothing of the user's is running" answer, which is the isolated
    route.
    """
    from computer import existing_browser

    monkeypatch.setattr(existing_browser, "resolve", lambda *a, **k: {
        "browser": browser,
        "running": False,
        "pids": [],
        "profile_dir": None,
        "cdp_port": existing_browser.DEFAULT_DEBUG_PORT,
        "cdp_reachable": False,
        "cdp_url": "",
        "cdp_browser": "",
        "how": existing_browser.LAUNCH,
        "note": "not running (test)",
    })


# ── every specific cause gets its own code ───────────────────────────────────

@pytest.mark.parametrize("exc,expected", [
    (cb.SessionEnded("browser_prepare", "session 'jarvis' has ended"),
     "cua_session_ended"),
    (cb.UnsupportedOnThisDriver("no such tool"), "unsupported_on_driver"),
    (RuntimeError("Executable doesn't exist at C:/x/chrome.exe"),
     "browser_binaries_missing"),
    (RuntimeError("ProcessSingleton: the profile is already in use"),
     "profile_locked"),
    (RuntimeError("existing_profile access was not granted to this daemon"),
     "existing_profile_not_granted"),
    (TimeoutError("timed out"), "timeout"),
    (RuntimeError("daemon not running"), "driver_unreachable"),
    (RuntimeError("click requires x, y"), "bad_arguments"),
    (RuntimeError("Unknown action: 'list_windows'"), "unsupported_on_backend"),
    # The same signal when it arrives as text rather than as SessionEnded: a
    # driver message wrapped by another layer must still classify specifically.
    (RuntimeError("browser_prepare failed (exit 1): session 'jarvis' has ended; "
                  "tool call 'browser_prepare' was rejected. Call start_session "
                  "with this id to revive it before issuing further actions"),
     "cua_session_ended"),
])
def test_each_known_cause_is_classified_specifically(exc, expected):
    assert _classify(exc) == expected
    assert _next_action(expected), "a specific code must carry a remedy"


def test_the_generic_code_is_only_the_last_resort_and_points_at_the_log():
    """The bug: one label for every cause the classifier did not recognise."""
    code = _classify(ValueError("something nothing else explains"))
    assert code == "driver_error"
    remedy = _next_action(code)
    assert "jarvis-" in remedy and ".jsonl" in remedy


def test_no_known_cause_falls_through_to_the_generic_code():
    causes = [
        cb.SessionEnded("x", "session 'jarvis' has ended"),
        RuntimeError("Executable doesn't exist at C:/x/chrome.exe"),
        RuntimeError("ProcessSingleton: the profile is already in use"),
        cb.UnsupportedOnThisDriver("no tool"),
    ]
    codes = [_classify(e) for e in causes]
    assert "driver_error" not in codes
    assert codes == ["cua_session_ended", "browser_binaries_missing",
                     "profile_locked", "unsupported_on_driver"]


def test_a_structured_driver_refusal_keeps_the_drivers_own_code():
    """Found live: the driver refused ``browser_prepare`` with
    ``{"status": "refused", "refusal": {"code": "browser_route_unavailable"}}``
    and the adapter reported it as a generic driver error."""
    exc = cb.DriverRefused("browser_route_unavailable",
                           "no vendor-signed protected Chromium executable is available")
    assert _classify(exc) == "browser_route_unavailable"
    remedy = _next_action("browser_route_unavailable")
    assert remedy and "remote-debugging-port" in remedy
    # A refusal code from a newer driver still reports the driver's own words,
    # never "cause not recognised".
    unknown = cb.DriverRefused("some_future_code", "the driver said no")
    assert _classify(unknown) == "some_future_code"
    assert "some_future_code" in _next_action("some_future_code", exc=unknown)


def test_both_refusal_envelope_shapes_are_recognised():
    nested = {"status": "refused",
              "refusal": {"code": "browser_route_unavailable", "message": "no chromium"}}
    flat = {"status": "refused", "code": "nope", "message": "flat shape"}
    assert cb.refusal_of(nested) == ("browser_route_unavailable", "no chromium")
    assert cb.refusal_of(flat) == ("nope", "flat shape")
    assert cb.refusal_of({"status": "ok"}) is None
    assert cb.refusal_of(None) is None


def test_a_refusal_envelope_from_the_driver_becomes_a_typed_error(monkeypatch):
    monkeypatch.setattr(cb, "_probe", lambda: dict(REACHABLE))
    cb.reset_probe_cache()

    def handler(binary, args, timeout):
        if args[:1] in (["dump-docs"], ["describe"]):
            return _Proc("{}")
        return _Proc(json.dumps({"status": "refused",
                                 "refusal": {"code": "browser_route_unavailable",
                                             "message": "no chromium"}}))

    monkeypatch.setattr(cb, "_run", handler)
    with pytest.raises(cb.DriverRefused) as err:
        cb.call_tool("browser_prepare", {"session": "jarvis"})
    assert err.value.code == "browser_route_unavailable"


def test_the_missing_browser_remedy_is_per_engine():
    """Chrome and Edge on Windows run through a Playwright *channel* against the
    browser already installed, so telling their owner to download browsers would
    be wrong and would not fix anything."""
    assert "playwright install" not in browser_binaries_remedy("chrome")
    assert "playwright install" not in browser_binaries_remedy("edge")
    assert "playwright install firefox" in browser_binaries_remedy("firefox")


def test_the_remedy_uses_the_engine_the_request_actually_asked_for():
    remedial = _next_action("browser_binaries_missing", {"browser": "firefox"})
    assert "playwright install firefox" in remedial


@pytest.mark.parametrize("probe,expected", [
    ({"host_supported": False, "host": "Plan9"}, "host_unsupported"),
    ({"host_supported": True, "binary_path": None}, "cua_binary_absent"),
    ({"host_supported": True, "binary_path": "C:/cua-driver.exe",
      "daemon_running": False}, "cua_daemon_unreachable"),
    ({"host_supported": True, "binary_path": "C:/cua-driver.exe",
      "daemon_running": True, "tools": ["list_apps"]}, "cua_degraded_manifest"),
])
def test_cua_failures_are_classified_from_driver_state(monkeypatch, probe, expected):
    """Not from the exception's wording — from the driver's own probe."""
    import computer.computer_use as cu
    monkeypatch.setattr(cu, "cua_diagnose", lambda *a, **k: dict(probe))
    unclassifiable = RuntimeError("the call did not complete")
    assert _classify(unclassifiable, {}, "cua") == expected
    # The same exception on a healthy driver is honestly unclassified.
    monkeypatch.setattr(cu, "cua_diagnose", lambda *a, **k: dict(REACHABLE))
    assert _classify(unclassifiable, {}, "cua") == "driver_error"


# ── the session lifecycle: the actual fix ───────────────────────────────────

def test_the_first_action_opens_the_driver_session(monkeypatch):
    seen = _driver(monkeypatch)
    cb.CuaBackend().execute("computer_control", {"action": "screenshot"})
    assert "start_session" in seen


def test_an_ended_session_is_revived_and_the_action_retried(monkeypatch):
    """Without this, one ended session rejects every later action forever."""
    seen = _driver(monkeypatch, session_ends_once=True)
    out = cb.CuaBackend().execute("computer_control", {"action": "screenshot"})
    assert "captured" in out or "get_desktop_state" in out
    # Once to open it lazily, once to revive it.
    assert seen.count("start_session") == 2
    assert seen.count("get_desktop_state") == 2


def test_a_revived_session_throws_away_the_stale_browser_target(monkeypatch):
    """A target prepared by a session that has ended is not addressable by the
    new one, so it has to be re-prepared rather than reused."""
    _driver(monkeypatch, session_ends_once=True)
    _no_existing_session(monkeypatch)
    backend = cb.CuaBackend()
    backend._browser_target, backend._browser_tab = "stale-target", "stale-tab"
    backend.execute("browser_control", {"action": "go_to",
                                        "url": "https://example.com"})
    assert backend._browser_target == "t-1"
    assert backend._browser_tab == "tab-1"


def test_the_drivers_own_rejection_is_recognised_verbatim():
    """Matched against the exact text this machine's driver produced."""
    real = ("session 'jarvis' has ended; tool call 'browser_prepare' was rejected. "
            "Call start_session with this id to revive it before issuing further "
            "actions")
    assert cb._is_session_ended(real)
    assert not cb._is_session_ended("browser_prepare ok, target_id=abc")
    assert not cb._is_session_ended("")


def test_a_session_that_cannot_be_revived_reports_its_own_cause(monkeypatch):
    monkeypatch.setattr(cb, "_probe", lambda: dict(REACHABLE))
    cb.reset_probe_cache()

    def handler(binary, args, timeout):
        if args[:1] in (["dump-docs"], ["describe"]):
            return _Proc("{}")
        return _Proc("", "session 'jarvis' has ended; start_session to revive it", 1)

    monkeypatch.setattr(cb, "_run", handler)
    with pytest.raises(cb.SessionEnded):
        cb.CuaBackend().execute("computer_control", {"action": "screenshot"})


def test_the_live_driver_accepts_a_session(monkeypatch):
    """Asks the real driver when one is installed, rather than a snapshot."""
    if not cb.detect():
        pytest.skip("no cua-driver daemon is reachable on this machine")
    assert cb.ensure_session("jarvis-verify") is True


# ── preflight and real availability ─────────────────────────────────────────

class _BrowserFinding:
    """A diagnostics Check stand-in for the browser stack."""

    def __init__(self, state, detail, fix):
        self.state, self.detail, self.fix = state, detail, fix


def _fake_browser_check(monkeypatch, state, detail="no browser binaries", fix="x"):
    import observability.diagnostics as d
    monkeypatch.setattr(d, "_check_browser",
                        lambda: _BrowserFinding(state, detail, fix))


def test_preflight_names_the_cause_diagnostics_reports(monkeypatch):
    from computer.local_backend import LocalBackend
    _fake_browser_check(
        monkeypatch, "DEGRADED",
        detail="Playwright is installed but no browser binaries were found",
        fix="playwright install chromium")
    cu = ComputerUse()
    cu._backend = LocalBackend()
    result = cu.preflight("browser_control", "go_to")
    assert result["ok"] is False
    assert result["reason"] == "browser_binaries_missing"
    assert "no browser binaries" in result["detail"]
    assert result["next_action"]
    assert result["source"].startswith("diagnostics")


def test_preflight_passes_when_the_browser_stack_is_healthy(monkeypatch):
    from computer.local_backend import LocalBackend
    _fake_browser_check(monkeypatch, "ONLINE", detail="Playwright and its browsers are present")
    cu = ComputerUse()
    cu._backend = LocalBackend()
    assert cu.preflight("browser_control", "go_to")["ok"] is True


def test_preflight_is_side_effect_free(monkeypatch):
    """No GUI call, no browser launch, no driver call — it only reads state."""
    from computer.local_backend import LocalBackend
    called = []
    monkeypatch.setattr(cb, "_run",
                        lambda *a, **kw: called.append(a) or _Proc("{}"))
    cu = ComputerUse()
    cu._backend = LocalBackend()
    called.clear()
    cu.preflight("browser_control", "go_to")
    assert called == [], "preflight must not touch the driver or the browser"


def test_local_availability_is_real_for_browser_work(monkeypatch):
    """It used to answer an unconditional True, so an unusable browser stack
    looked healthy right up to the moment an action failed."""
    from computer.local_backend import LocalBackend
    lb = LocalBackend()
    assert lb.available() is True
    assert lb.available("computer_control", "screenshot") is True
    _fake_browser_check(monkeypatch, "DEGRADED")
    assert lb.available("browser_control", "go_to") is False
    _fake_browser_check(monkeypatch, "ONLINE")
    assert lb.available("browser_control", "go_to") is True


def test_a_diagnostics_hiccup_does_not_declare_the_browser_unusable(monkeypatch):
    from computer.local_backend import LocalBackend
    import observability.diagnostics as d

    def _boom():
        raise RuntimeError("diagnostics exploded")

    monkeypatch.setattr(d, "_check_browser", _boom)
    assert LocalBackend().available("browser_control", "go_to") is True


# ── actions the primary backend cannot serve ───────────────────────────────

def _primary(name="cua"):
    """A stand-in for the selected backend."""
    class _Backend:
        def __init__(self, failure=None):
            self.name = name
            self.failure = failure

        def available(self):
            return True

        def execute(self, *a, **kw):
            raise self.failure

    return _Backend


def test_an_action_the_primary_backend_cannot_serve_is_delegated(monkeypatch):
    """Choosing Cua as primary must not remove capability the local backend
    already had — list_browsers and desktop stats were failing outright."""
    import computer.computer_use as cu_mod
    from computer.local_backend import LocalBackend

    class _Local(LocalBackend):
        def execute(self, tool, parameters=None, **kw):
            return f"local:{tool}"

    monkeypatch.setattr(cu_mod, "LocalBackend", _Local)
    cu = ComputerUse()
    cu._backend = _primary()(cb.UnsupportedOnThisDriver("no mapping for this action"))
    cu._mode = "auto"
    out = cu.execute("desktop_control", {"action": "stats"})
    assert out == "local:desktop_control"
    assert cu.last_result()["backend"] == "local"
    assert cu.last_result()["delegated_from"] == "cua"
    assert "no mapping" in cu.last_result()["delegated_why"]


def test_a_forced_cua_setting_does_not_delegate(monkeypatch):
    """`cua_backend=on` asks for Cua only, so it must fail loudly instead."""
    import computer.computer_use as cu_mod
    from computer.local_backend import LocalBackend

    class _Local(LocalBackend):
        def execute(self, *a, **kw):
            return "should not run"

    monkeypatch.setattr(cu_mod, "LocalBackend", _Local)
    cu = ComputerUse()
    cu._backend = _primary()(cb.UnsupportedOnThisDriver("no mapping"))
    cu._mode = "on"
    with pytest.raises(ComputerActionError) as caught:
        cu.execute("desktop_control", {"action": "stats"})
    assert caught.value.diagnostic["reason"] == "unsupported_on_driver"


def test_a_real_failure_is_never_silently_delegated(monkeypatch):
    """Delegation is for an action the backend has no tool for — not a licence to
    quietly retry a genuine failure somewhere else."""
    import computer.computer_use as cu_mod
    from computer.local_backend import LocalBackend

    local_calls = []

    class _Local(LocalBackend):
        def execute(self, *a, **kw):
            local_calls.append(1)
            return "should not run"

    monkeypatch.setattr(cu_mod, "LocalBackend", _Local)
    cu = ComputerUse()
    cu._backend = _primary()(RuntimeError("the window is gone"))
    cu._mode = "auto"
    with pytest.raises(ComputerActionError):
        cu.execute("computer_control", {"action": "click"})
    assert local_calls == []


# ── failure detail reaches the caller ───────────────────────────────────────

def test_an_already_written_explanation_reaches_the_diagnostic():
    """The browser layer builds a rich reason for why a session got the profile
    it did; flattening it to `type: message[:300]` is what lost it."""
    from computer.local_backend import LocalBackend

    class _Failing(LocalBackend):
        def execute(self, *a, **kw):
            err = RuntimeError("Could not launch chrome: boom")
            err.profile_note = ("chrome is already running, so a second, signed-out "
                               "profile was used")
            raise err

    cu = ComputerUse()
    cu._backend = _Failing()
    with pytest.raises(ComputerActionError) as caught:
        cu.execute("browser_control", {"action": "go_to", "url": "https://x.test"})
    assert "Why this session" in caught.value.detail
    assert "signed-out" in caught.value.detail
    assert caught.value.diagnostic["profile_note"]
    # The concise line stays concise: the detail is for the logs.
    assert len(str(caught.value)) < len(caught.value.detail)


def test_a_failing_action_is_logged_with_its_specific_reason(monkeypatch):
    from computer.local_backend import LocalBackend
    from observability import logger as logger_mod

    records = []
    monkeypatch.setattr(logger_mod, "log_event",
                        lambda **kw: records.append(kw))
    import computer.computer_use as cu_mod
    monkeypatch.setattr(cu_mod, "_log", lambda: logger_mod.get_logger("computer"))

    class _Ended(LocalBackend):
        def execute(self, *a, **kw):
            raise cb.SessionEnded("browser_prepare", "session 'jarvis' has ended")

    cu = ComputerUse()
    cu._backend = _Ended()
    with pytest.raises(ComputerActionError):
        cu.execute("browser_control", {"action": "go_to", "url": "https://x.test"})
    errors = [r for r in records if r.get("event") == "action_failed"]
    assert errors and errors[-1]["result"]["reason"] == "cua_session_ended"


# ── the browser-attach causes are separable ──────────────────────────────────

def test_an_un_anchorable_browser_is_not_reported_as_a_permission_problem():
    """A running browser with nothing to anchor to is not a missing grant.

    Found by driving the adapter against the live driver: one code,
    ``browser_consent_required``, answers both "your request was incomplete" and
    "the daemon holds no grant". The remedies differ in kind, so they must not
    share a classification.
    """
    exc = cb.AttachTargetUnavailable(
        "chrome is running, but no window could be anchored to")
    assert _classify(exc) == "attach_anchor_missing"
    remedy = _next_action("attach_anchor_missing")
    assert remedy
    assert "--remote-debugging-port" in remedy


def test_the_missing_grant_remedy_names_the_runnable_command():
    """The user has to run it themselves, so the remedy must be runnable."""
    remedy = _next_action("browser_consent_required")
    assert "cua-driver serve --grant existing-profile" in remedy
    # And the driver's own code reaches that remedy rather than a generic line.
    refused = cb.DriverRefused(
        "browser_consent_required",
        "existing-profile attachment in standard mode requires --grant "
        "existing-profile or an embedding authorization host")
    assert _classify(refused) == "browser_consent_required"
    assert "cua-driver serve --grant existing-profile" in _next_action(
        _classify(refused), {}, refused)
