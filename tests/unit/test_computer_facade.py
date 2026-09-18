"""Computer facade contract tests (tasks 4.1, 4.4, 4.5).

Proves: backend selection honors the config override and Cua detection, the
facade serializes execution and routes tools to the active backend, the
actions/ shims delegate with identical signatures, and verification reports
failure instead of silent success.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from computer import computer_use as cu_mod  # noqa: E402
from computer.computer_use import ComputerUse, reset_computer_use  # noqa: E402
from computer.verification import verify_action  # noqa: E402


class FakeBackend:
    name = "fake"
    calls: list[tuple] = []

    def available(self):
        return True

    def execute(self, tool, parameters, response=None, player=None,
                session_memory=None):
        FakeBackend.calls.append((tool, dict(parameters or {})))
        return f"fake:{tool}"


@pytest.fixture(autouse=True)
def _clean():
    reset_computer_use()
    FakeBackend.calls = []
    yield
    reset_computer_use()


def test_facade_routes_to_active_backend():
    cu = ComputerUse()
    cu._backend = FakeBackend()
    out = cu.execute("computer_control", {"action": "screenshot"})
    assert out == "fake:computer_control"
    assert FakeBackend.calls == [("computer_control", {"action": "screenshot"})]
    assert cu.last_result()["backend"] == "fake"


def test_facade_rejects_unknown_tools():
    cu = ComputerUse()
    cu._backend = FakeBackend()
    import computer.local_backend as lb
    with pytest.raises(lb.BackendUnavailable):
        cu.execute("not_a_tool", {})


def test_facade_serializes_concurrent_calls():
    cu = ComputerUse()
    cu._backend = FakeBackend()
    import threading
    results = []
    def worker(i):
        results.append(cu.execute("computer_control", {"action": f"a{i}"}))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 8 and all(r == "fake:computer_control" for r in results)
    assert len(FakeBackend.calls) == 8


def test_backend_selection_off_forces_local():
    import config.settings as settings
    original = settings.get_feature

    def fake_flag(name, default=None):
        return "off" if name == "cua_backend" else original(name, default)

    settings.get_feature = fake_flag
    try:
        cu = ComputerUse()
        assert cu.backend_name == "local"
    finally:
        settings.get_feature = original


def test_backend_selection_on_forces_cua():
    import config.settings as settings
    original = settings.get_feature

    def fake_flag(name, default=None):
        return "on" if name == "cua_backend" else original(name, default)

    settings.get_feature = fake_flag
    try:
        cu = ComputerUse()
        assert cu.backend_name == "cua"
    finally:
        settings.get_feature = original


def test_windows_is_a_supported_cua_host():
    """Cua Driver drives macOS, Windows and Linux.

    This replaces an earlier test that asserted the opposite. The old check was
    an obsolete ``{"Darwin"}`` allow-list: it made every Windows install report
    Cua unavailable and silently fall through to the local backend, whatever the
    driver situation. Support is now decided from driver state, never the host.
    """
    from computer import cua_backend
    assert not hasattr(cua_backend, "_CUA_SUPPORTED_HOSTS"), \
        "the host allow-list must stay deleted"
    st = cua_backend.status_detail()
    assert st["host"] == platform.system()
    assert st["host_supported"] is True


def test_cua_availability_comes_from_the_driver_not_the_platform():
    from computer import cua_backend
    cua_backend.reset_probe_cache()
    st = cua_backend.status_detail()
    if st["available"]:
        # A real driver is installed and reachable on this machine.
        assert st["binary_present"] and st["daemon_running"] and st["tool_count"]
    else:
        # Unavailable must be *explained*, never just false.
        assert st["reason"], "an unavailable driver must report why"
        missing = [not st["binary_present"], not st["daemon_running"],
                   st["tool_count"] == 0]
        assert any(missing)


def test_detection_requires_a_reachable_driver():
    """Installing the binary alone must not select the Cua backend."""
    from computer import cua_backend

    for probe, expected in (
        ({"host": "Windows", "host_supported": True, "binary_path": None,
          "daemon_running": False, "tools": [], "reason": "binary not found"}, False),
        ({"host": "Windows", "host_supported": True, "binary_path": "C:/cua-driver.exe",
          "daemon_running": False, "tools": [], "reason": "daemon not running"}, False),
        ({"host": "Windows", "host_supported": True, "binary_path": "C:/cua-driver.exe",
          "daemon_running": True, "tools": [], "reason": "no tools listed"}, False),
        ({"host": "Windows", "host_supported": True, "binary_path": "C:/cua-driver.exe",
          "daemon_running": True, "tools": ["list_apps", "click"], "reason": ""}, True),
    ):
        cua_backend._cache["at"] = 0.0
        cua_backend._cache["data"] = None
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(cua_backend, "_probe", lambda probe=probe: dict(probe))
        try:
            assert cua_backend.detect() is expected, probe
        finally:
            monkeypatch.undo()
            cua_backend.reset_probe_cache()


def test_backend_selection_reports_why_it_fell_back():
    """A fallback must be deliberate and explained, never silent."""
    import computer.computer_use as cu_mod

    original_detect, original_diag = cu_mod.cua_detect, cu_mod.cua_diagnose
    cu_mod.cua_detect = lambda: False
    cu_mod.cua_diagnose = lambda force=False: {
        "host": "Windows", "host_supported": True, "binary_path": None,
        "daemon_running": False, "tools": [], "reason": "binary not found",
    }
    try:
        cu = ComputerUse()
        assert cu.backend_name == "local"
        assert "Cua unavailable" in cu.selection_reason
        assert "binary not found" in cu.selection_reason
    finally:
        cu_mod.cua_detect, cu_mod.cua_diagnose = original_detect, original_diag


def test_backend_selection_picks_cua_when_the_driver_is_reachable():
    import computer.computer_use as cu_mod

    original_detect, original_diag = cu_mod.cua_detect, cu_mod.cua_diagnose
    cu_mod.cua_detect = lambda: True
    cu_mod.cua_diagnose = lambda force=False: {
        "host": "Windows", "host_supported": True,
        "binary_path": "C:/cua-driver.exe", "binary_version": "cua-driver 0.28.2",
        "daemon_running": True, "tools": ["list_apps", "click", "type_text"],
        "reason": "",
    }
    try:
        cu = ComputerUse()
        assert cu.backend_name == "cua"
        assert "0.28.2" in cu.selection_reason
        assert "3 tools" in cu.selection_reason
    finally:
        cu_mod.cua_detect, cu_mod.cua_diagnose = original_detect, original_diag


def test_actions_shims_delegate_with_identical_signatures():
    import inspect
    import actions.computer_control as a_cc
    import computer.computer_control as c_cc
    assert a_cc.computer_control is c_cc.computer_control
    sig = inspect.signature(a_cc.computer_control)
    assert list(sig.parameters) == ["parameters", "response", "player", "session_memory"]

    import actions.browser_control as a_bc
    import computer.browser_control as c_bc
    assert a_bc.browser_control is c_bc.browser_control

    import actions.open_app as a_oa
    import computer.windows as c_oa
    assert a_oa.open_app is c_oa.open_app

    import actions.desktop as a_dc
    import computer.desktop_ops as c_dc
    assert a_dc.desktop_control is c_dc.desktop_control


def test_local_backend_routes_all_five_tools(monkeypatch):
    from computer.local_backend import LocalBackend
    lb = LocalBackend()
    seen = []
    import computer.computer_control as c_cc
    import computer.browser_control as c_bc
    import computer.windows as c_oa
    import computer.desktop_ops as c_dc
    import actions.computer_settings as a_cs
    monkeypatch.setattr(c_cc, "computer_control",
                        lambda **kw: seen.append("cc") or "ok")
    monkeypatch.setattr(c_bc, "browser_control",
                        lambda **kw: seen.append("bc") or "ok")
    monkeypatch.setattr(c_oa, "open_app",
                        lambda **kw: seen.append("oa") or "ok")
    monkeypatch.setattr(c_dc, "desktop_control",
                        lambda **kw: seen.append("dc") or "ok")
    monkeypatch.setattr(a_cs, "computer_settings",
                        lambda **kw: seen.append("cs") or "ok")
    for tool in ("computer_control", "browser_control", "open_app",
                 "desktop_control", "computer_settings"):
        assert lb.execute(tool, {}) == "ok"
    assert seen == ["cc", "bc", "oa", "dc", "cs"]


def test_verification_reports_failure_not_silent_success():
    # open_app verification against a window title that will not exist.
    r = verify_action("open_app", {"app_name": "zz-no-such-app-zz"}, "Opened.")
    assert r["verified"] is False and "no matching window" in r["reason"]


def test_verification_no_check_defined_is_honest():
    """An action nobody checked must not be reported as verified.

    This used to return ``verified: True`` with the reason "no verification
    defined", which made an unchecked action indistinguishable from a proven one
    at every call site — including the log.
    """
    r = verify_action("computer_control", {"action": "move", "x": 1, "y": 1}, "ok")
    assert r["verified"] is False
    assert r["state"] == "unverified"
    assert "no verification defined" in r["reason"]


def test_a_checked_action_reports_its_state_not_an_untested_pass():
    """The three states are distinct and ``verified`` is only true for one."""
    from computer.verification import state_of

    ok = verify_action("computer_control", {"action": "paste", "text": "zz-none-zz"},
                       "ok")
    assert ok["state"] in ("verified", "failed") and ok["state"] != "unverified"
    assert state_of({"verified": True}) == "verified"
    assert state_of({"verified": False}) == "failed"
    assert state_of({"verified": False, "state": "unverified"}) == "unverified"
    assert state_of(None) == "failed"


def test_the_facade_verifies_every_action_it_reports_as_done(monkeypatch):
    """A backend returning without raising must not be reported as success."""
    monkeypatch.setattr(
        cu_mod.ComputerUse, "_verify",
        staticmethod(lambda tool, params, result, backend:
                     {"verified": False, "reason": "window never appeared",
                      "evidence": None}))
    cu = ComputerUse()
    cu._backend = FakeBackend()
    out = cu.execute("open_app", {"app_name": "notepad"})
    assert "unverified" in out and "never appeared" in out
    assert cu.last_result()["verification"]["verified"] is False


def test_a_verified_action_is_left_exactly_as_the_tool_returned_it(monkeypatch):
    monkeypatch.setattr(
        cu_mod.ComputerUse, "_verify",
        staticmethod(lambda tool, params, result, backend:
                     {"verified": True, "reason": "window found", "evidence": None}))
    cu = ComputerUse()
    cu._backend = FakeBackend()
    out = cu.execute("open_app", {"app_name": "notepad"})
    assert out == "fake:open_app"
    assert cu.last_result()["verification"]["verified"] is True


def test_verification_failure_does_not_break_the_action():
    """A verifier that blows up must degrade to 'unverified', never raise."""
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if name.endswith("verification"):
            raise RuntimeError("verifier exploded")
        return real_import(name, *a, **kw)

    builtins.__import__ = _boom
    try:
        r = ComputerUse._verify("open_app", {}, "ok", "local")
    finally:
        builtins.__import__ = real_import
    assert r["verified"] is False and "unavailable" in r["reason"]


def test_a_window_title_must_actually_belong_to_the_application():
    """'notepad' matching 'Notepad++' counted a wrong window as proof.

    Cost of the loose substring test: the open_app step verified against an
    unrelated editor, and the next step typed into whatever was focused.
    """
    from computer.verification import _title_matches_app

    assert _title_matches_app("Untitled - Notepad", "notepad")
    assert _title_matches_app("Untitled - Notepad", "Notepad")
    assert _title_matches_app("prompt.txt - Notepad++ [Administrator]", "notepad") is False
    assert _title_matches_app("NotepadPlus", "notepad") is False
    assert _title_matches_app("J.A.R.V.I.S", "notepad") is False
    # A longer, real folder path is still a match for the bare app name.
    assert _title_matches_app(r"C:\work\notes.txt - Notepad", "notepad")


def test_verifying_a_navigation_does_not_open_a_browser():
    """Verification observes the navigation; it must not start a second browser."""
    from computer.browser_control import _registry
    _registry.note_native_url("https://example.com/page")
    r = verify_action("browser_control", {"action": "go_to", "url": "example.com"},
                      "Opened.", timeout_s=0.3)
    assert r["verified"] is True and r["reason"] == "navigation recorded"
    assert _registry.has() is False, "verification started a browser session"


def test_diagnostics_reports_active_backend():
    from core.diagnostics import check
    c = check("computer_backend")
    assert c.name == "computer_backend"
    assert "backend=" in c.detail
