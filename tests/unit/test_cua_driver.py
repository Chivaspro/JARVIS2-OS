"""Cua Driver adapter contract.

These tests never require the driver to be installed. They pin the two rules the
adapter exists to enforce:

1. Support is decided from observable driver state (binary + daemon + tool list),
   never from a platform allow-list — the bug that made Windows fall through to
   the local backend.
2. The tool surface is discovered, never invented. Every tool name the adapter
   maps to must be a name Cua actually documents, and every argument is filtered
   against the tool's declared schema.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from computer import cua_backend as cb  # noqa: E402
from computer.computer_use import ComputerActionError, ComputerUse  # noqa: E402


# The complete tool list reported by cua-driver 0.28.2 (`list-tools`), captured
# on a real Windows install. If the adapter ever grows a mapping to a name that
# is not in here, that is an invented API and this set is where it shows up.
# The live driver is also queried when one is reachable, so a renamed tool on a
# future release fails loudly instead of silently drifting.
DOCUMENTED_DRIVER_TOOLS = {
    "bring_to_front", "browser_click", "browser_dialog", "browser_download",
    "browser_navigate", "browser_pointer", "browser_prepare",
    "browser_set_input_files", "browser_type", "check_for_update",
    "check_permissions", "click", "clipboard_read", "clipboard_write",
    "debug_window_info", "double_click", "drag", "end_session",
    "escalate_session", "get_accessibility_tree", "get_agent_cursor_state",
    "get_browser_state", "get_config", "get_cursor_position",
    "get_desktop_state", "get_recording_state", "get_screen_size",
    "get_session", "get_session_state", "get_window_state", "health_report",
    "hotkey", "install_ffmpeg", "invoke_menu", "kill_app", "launch_app",
    "list_apps", "list_sessions", "list_windows", "move_cursor", "page",
    "press_key", "replay_trajectory", "right_click", "scroll",
    "set_agent_cursor_enabled", "set_agent_cursor_motion",
    "set_agent_cursor_theme", "set_config", "set_value", "set_window_frame",
    "start_recording", "start_session", "stop_recording", "type_text",
    "verify_state", "zoom",
}

REACHABLE = {
    "host": "Windows", "host_supported": True,
    "binary_path": "C:/cua-driver.exe", "binary_version": "cua-driver 0.28.2",
    "daemon_running": True,
    "tools": sorted(DOCUMENTED_DRIVER_TOOLS),
    "reason": "", "install_hint": "irm https://cua.ai/driver/install.ps1 | iex",
}


@pytest.fixture(autouse=True)
def _fresh():
    cb.reset_probe_cache()
    yield
    cb.reset_probe_cache()


def _fake_probe(monkeypatch, probe):
    monkeypatch.setattr(cb, "_probe", lambda: dict(probe))
    cb.reset_probe_cache()


def _fake_run(monkeypatch, handler):
    monkeypatch.setattr(cb, "_run", handler)


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


# ── the mapping may only name real tools ─────────────────────────────────────

def test_every_mapped_tool_is_a_documented_driver_tool():
    invented = sorted(set(cb._ACTION_TOOLS.values()) - DOCUMENTED_DRIVER_TOOLS)
    assert not invented, f"adapter maps to tools Cua does not document: {invented}"


def test_every_mapped_tool_exists_on_the_driver_that_is_installed():
    """The strongest available check: ask the driver, do not trust a snapshot.

    Runs only when a driver is actually reachable, and skips otherwise rather
    than pretending. A renamed tool on a future release fails here.
    """
    if not cb.detect():
        pytest.skip("no cua-driver daemon is reachable on this machine")
    real = set(cb.list_tools())
    assert real, "a reachable driver reported no tools at all"
    invented = sorted(set(cb._ACTION_TOOLS.values()) - real)
    assert not invented, f"adapter maps to tools this driver lacks: {invented}"


def test_the_adapter_never_maps_a_tool_to_itself_by_accident():
    """A wrong mapping is as bad as an invented one: `move` used to map to
    `click`, so asking Jarvis to move the pointer would have clicked."""
    assert cb._ACTION_TOOLS["move"] == "move_cursor"
    assert cb._ACTION_TOOLS["move"] != cb._ACTION_TOOLS["click"]
    # The declare-an-action-in-the-tool-name case must stay covered, because
    # open_app's declaration carries no `action` parameter at all.
    assert cb._TOOL_DEFAULT_ACTION["open_app"] in cb._ACTION_TOOLS


def test_the_obsolete_host_allowlist_is_gone():
    assert not hasattr(cb, "_CUA_SUPPORTED_HOSTS")


# ── parsing the driver's own answers ─────────────────────────────────────────

@pytest.mark.parametrize("payload,expected", [
    ('["list_apps","click"]', ["click", "list_apps"]),
    ('{"tools":[{"name":"click"},{"name":"type_text"}]}', ["click", "type_text"]),
    ('{"mcp_tools":{"click":"Click it","list_apps":"List apps"}}',
     ["click", "list_apps"]),
    ("click — Click against a target pid\nlist_apps — List apps",
     ["click", "list_apps"]),
])
def test_tool_list_parsing_handles_every_plausible_shape(payload, expected):
    assert cb._parse_tool_list(payload) == expected


def test_tool_list_parsing_reports_nothing_rather_than_guessing():
    assert cb._parse_tool_list("") == []
    assert cb._parse_tool_list("All systems nominal.\nNo tools here.") == []


def test_lenient_json_finds_a_payload_behind_a_human_preamble():
    assert cb._parse_json_lenient('cua-driver 0.28.2\n{"tools": []}\ntrailing') == \
        {"tools": []}
    assert cb._parse_json_lenient("not json at all") is None


# ── calls are schema-filtered, and never invent a tool ───────────────────────

def test_a_tool_the_driver_does_not_report_is_never_called(monkeypatch):
    _fake_probe(monkeypatch, {**REACHABLE, "tools": ["list_apps"]})
    called = []
    _fake_run(monkeypatch, lambda *a, **kw: called.append(a) or _Proc())
    with pytest.raises(cb.UnsupportedOnThisDriver) as err:
        cb.call_tool("click", {"x": 1, "y": 2})
    assert "list_apps" in str(err.value)          # says what IS available
    assert called == []                            # and never shelled out


def test_arguments_are_filtered_to_the_tool_schema(monkeypatch):
    _fake_probe(monkeypatch, REACHABLE)
    seen = {}

    def handler(binary, args, timeout):
        if args[:1] == ["dump-docs"]:
            return _Proc(json.dumps({"tools": [{
                "name": "click",
                "inputSchema": {"type": "object",
                                "properties": {"x": {}, "y": {}, "button": {}},
                                "required": ["x", "y"]}}]}))
        seen["args"] = args
        return _Proc("✅ clicked\n")

    _fake_run(monkeypatch, handler)
    cb.call_tool("click", {"x": 5, "y": 6, "element_token": "nope", "text": "ignored"})
    payload = json.loads(seen["args"][2])
    assert payload == {"x": 5, "y": 6}


def test_a_missing_required_argument_fails_loudly(monkeypatch):
    _fake_probe(monkeypatch, REACHABLE)
    _fake_run(monkeypatch, lambda binary, args, timeout: _Proc(json.dumps({
        "tools": [{"name": "click",
                   "inputSchema": {"type": "object",
                                   "properties": {"x": {}, "y": {}},
                                   "required": ["x", "y"]}}]})))
    with pytest.raises(RuntimeError) as err:
        cb.call_tool("click", {"x": 1})
    assert "requires" in str(err.value) and "y" in str(err.value)


def test_the_drivers_own_error_message_is_surfaced(monkeypatch):
    _fake_probe(monkeypatch, REACHABLE)

    def handler(binary, args, timeout):
        if args[:1] == ["dump-docs"]:
            return _Proc("{}")
        return _Proc("", "window_id_not_found: no such window 99", 3)

    _fake_run(monkeypatch, handler)
    with pytest.raises(RuntimeError) as err:
        cb.call_tool("click", {"x": 1, "y": 1})
    assert "window_id_not_found" in str(err.value)


def test_a_screenshot_is_written_to_a_file_not_embedded(monkeypatch):
    _fake_probe(monkeypatch, REACHABLE)
    seen = {}

    def handler(binary, args, timeout):
        if args[:1] in (["dump-docs"], ["describe"]):
            return _Proc("{}")
        seen["args"] = args
        return _Proc("✅ captured")

    _fake_run(monkeypatch, handler)
    out = cb.CuaBackend().execute("computer_control", {"action": "screenshot"})
    assert seen["args"][1] == "get_desktop_state"
    # The documented tool argument (which also switches the structured response
    # to a path) and the CLI flag are both used, so the PNG never comes back as
    # base64 in stdout.
    payload = json.loads(seen["args"][2])
    assert payload["screenshot_out_file"].endswith(".png")
    assert "--screenshot-out-file" in seen["args"]
    assert "screenshot=" in out


# ── actions that have no driver equivalent ──────────────────────────────────

def test_an_unmapped_action_names_what_the_driver_offers(monkeypatch):
    _fake_probe(monkeypatch, REACHABLE)
    with pytest.raises(cb.UnsupportedOnThisDriver) as err:
        cb.CuaBackend().execute("computer_control", {"action": "teleport"})
    message = str(err.value)
    assert "teleport" in message and "click" in message


def test_an_unusable_driver_refuses_rather_than_pretending(monkeypatch):
    _fake_probe(monkeypatch, {**REACHABLE, "daemon_running": False,
                              "tools": [], "reason": "daemon not running"})
    with pytest.raises(RuntimeError) as err:
        cb.CuaBackend().execute("computer_control", {"action": "click"})
    assert "daemon not running" in str(err.value)


# ── failure diagnostics + logging ───────────────────────────────────────────

@pytest.mark.parametrize("exc,code", [
    (cb.UnsupportedOnThisDriver("no such tool"), "unsupported_on_driver"),
    (RuntimeError("daemon not running"), "driver_unreachable"),
    (RuntimeError("click requires x, y"), "bad_arguments"),
    (TimeoutError("timed out"), "timeout"),
    (ValueError("something else"), "driver_error"),
])
def test_failures_are_classified_with_a_next_action(exc, code):
    from computer.computer_use import _classify, _next_action
    assert _classify(exc) == code
    assert _next_action(code)


def test_a_failed_action_carries_an_actionable_diagnostic(monkeypatch):
    import computer.computer_use as cu_mod

    cu = ComputerUse()
    cu._backend = cb.CuaBackend()
    # Forced-Cua mode: no delegation to the local controller. This test is about
    # the diagnostic a *failure* carries, and without this it would delegate a
    # real click to the local backend and click on the user's desktop.
    cu._mode = "on"
    _fake_probe(monkeypatch, {**REACHABLE, "tools": ["list_apps"]})
    with pytest.raises(ComputerActionError) as err:
        cu.execute("computer_control",
                   {"action": "click", "target": "Compose"})
    detail = err.value.detail
    for field in ("Target: Compose", "Backend: cua", "Reason:",
                  "Possible next action:"):
        assert field in detail
    assert err.value.diagnostic["reason"] == "unsupported_on_driver"
    # The concise line is what the user hears; the detail is for the logs.
    assert len(str(err.value)) < len(detail)


def test_every_action_is_logged_with_backend_and_duration(monkeypatch):
    import computer.computer_use as cu_mod
    from observability import logger as logger_mod

    records = []
    monkeypatch.setattr(logger_mod, "log_event",
                        lambda **kw: records.append(kw))
    cu = ComputerUse()
    cu._backend = _FakeBackend()
    monkeypatch.setattr(cu_mod, "_log", lambda: logger_mod.get_logger("computer"))

    cu.execute("computer_control", {"action": "screenshot", "task_id": "t1"},
               task_id="t1")
    events = [r for r in records if r.get("event") == "action"]
    assert events, "an action must emit a log record"
    record = events[-1]
    assert record["component"] == "computer"
    assert record["tool"] == "computer_control"
    assert record["state"] == "screenshot"
    assert record["duration_ms"] is not None
    assert record["result"]["backend"] == "fake"


def test_a_failed_action_is_logged_as_an_error(monkeypatch):
    from observability import logger as logger_mod

    records = []
    monkeypatch.setattr(logger_mod, "log_event", lambda **kw: records.append(kw))
    cu = ComputerUse()
    cu._backend = _FailingBackend()
    with pytest.raises(ComputerActionError):
        cu.execute("computer_control", {"action": "click", "target": "Compose"})
    errors = [r for r in records if r.get("event") == "action_failed"]
    assert errors and errors[-1]["result"]["reason"] == "driver_error"
    assert errors[-1]["error"]


class _FakeBackend:
    name = "fake"

    def available(self):
        return True

    def execute(self, tool, parameters, response=None, player=None,
                session_memory=None):
        return "fake:ok"


class _FailingBackend(_FakeBackend):
    def execute(self, *a, **kw):
        raise ValueError("the driver said no")


# ── the diagnostics report ──────────────────────────────────────────────────

def test_the_diagnostics_report_answers_every_required_question(monkeypatch):
    from computer.computer_use import computer_report

    monkeypatch.setattr(cb, "_probe", lambda: dict(REACHABLE))
    cb.reset_probe_cache()
    report = computer_report()
    for label in ("COMPUTER CONTROL", "OS:", "Cua SDK", "Cua Driver:",
                  "Driver path:", "Driver daemon:", "Driver reachable:",
                  "Driver tools:", "Backend:", "Why:", "Playwright:",
                  "Local fallback:", "Browser process:",
                  "Existing browser session:", "Browser profile:",
                  "DevTools endpoint:"):
        assert label in report, f"{label} missing from the diagnostics report"


def test_the_report_offers_a_remedy_when_the_driver_is_missing(monkeypatch):
    from computer.computer_use import computer_report

    monkeypatch.setattr(cb, "_probe", lambda: {
        **REACHABLE, "binary_path": None, "binary_version": None,
        "daemon_running": False, "tools": [],
        "reason": "cua-driver binary not found"})
    cb.reset_probe_cache()
    report = computer_report()
    assert "not installed" in report
    assert "install.ps1" in report or "install.sh" in report


def test_chrome_facts_never_start_or_authenticate_anything():
    from computer.computer_use import _chrome_facts

    facts = _chrome_facts()
    assert set(facts) >= {"process", "windows", "existing_session",
                          "profile_dir", "attach_note", "cdp_reachable"}
    assert isinstance(facts["windows"], list)
    # The report must explain the one thing that decides whether the user's
    # signed-in session is reachable, not imply it happens automatically.
    if facts["process"] and not facts["cdp_reachable"]:
        assert "--remote-debugging-port" in facts["attach_note"]


def test_chrome_facts_come_from_the_one_owner_for_browser_resolution():
    """Diagnostics and the browser controller must not disagree."""
    from computer import computer_use
    from computer import existing_browser
    import inspect

    source = inspect.getsource(computer_use._chrome_facts)
    assert "existing_browser" in source
    assert "psutil" not in source   # no second, divergent process scan

    info = existing_browser.resolve("chrome")
    assert set(info) >= {"how", "running", "cdp_reachable", "note"}
    assert info["how"] in (existing_browser.ATTACH, existing_browser.LAUNCH,
                           existing_browser.SECOND_PROFILE)


# ── the browser route comes from the one shared resolver ─────────────────────
# The adapter must not answer "which browser can we reach" itself: that second,
# divergent answer is how the browser the user had open stopped being the one
# Jarvis drove.

def _route(monkeypatch, how, pids=(4242,)):
    """Pin the shared resolver's answer, so no test reads this machine."""
    from computer import existing_browser

    monkeypatch.setattr(existing_browser, "resolve", lambda *a, **k: {
        "browser": "chrome",
        "running": how in (existing_browser.ATTACH, existing_browser.SECOND_PROFILE),
        "pids": list(pids),
        "profile_dir": "C:/profile",
        "cdp_port": existing_browser.DEFAULT_DEBUG_PORT,
        "cdp_reachable": how == existing_browser.ATTACH,
        "cdp_url": "http://127.0.0.1:9222" if how == existing_browser.ATTACH else "",
        "cdp_browser": "Chrome/140" if how == existing_browser.ATTACH else "",
        "how": how,
        "note": f"route={how}",
    })


def _capture_prepare(monkeypatch, refusal=None, window_id=9090):
    """Record every `browser_prepare` exactly as the driver would receive it."""
    _fake_probe(monkeypatch, REACHABLE)
    calls = []

    def handler(binary, args, timeout):
        if args[:1] in (["dump-docs"], ["describe"]):
            return _Proc("{}")
        tool = args[1] if len(args) > 1 else ""
        if tool == "browser_prepare":
            calls.append(json.loads(args[2]) if len(args) > 2 else {})
            if refusal:
                return _Proc(json.dumps(
                    {"status": "refused",
                     "refusal": {"code": refusal[0], "message": refusal[1]}}))
            return _Proc('{"target_id": "t-1", "tab_id": "tab-1"}')
        if tool == "list_windows":
            return _Proc(json.dumps(
                {"windows": [{"title": "New Tab - Google Chrome",
                              "window_id": window_id}]}))
        return _Proc("ok")

    _fake_run(monkeypatch, handler)
    return calls


@pytest.mark.parametrize("how,expected", [
    ("attach", "existing_profile"),          # running, attachable
    ("second_profile", "existing_profile"),  # running, not attachable: still theirs
    ("launch", "isolated_named"),            # nothing of theirs is running
])
def test_the_browser_route_comes_from_the_shared_resolver(monkeypatch, how, expected):
    _route(monkeypatch, how)
    calls = _capture_prepare(monkeypatch)
    cb.CuaBackend().execute("browser_control",
                            {"action": "go_to", "url": "https://example.com"})
    assert calls, "browser_prepare was never called"
    prepare = calls[0]
    if expected == "existing_profile":
        assert prepare["strategy"]["kind"] == "existing_profile"
        assert "profile" not in prepare
    else:
        assert prepare["profile"]["mode"] == "isolated_named"
        assert "strategy" not in prepare


def test_the_attach_request_carries_both_identifiers_the_schema_requires(monkeypatch):
    """Neither identifier alone is acceptable to the driver.

    Verified against the installed driver: the window-only shape is rejected as
    "Missing required integer field: pid", the pid-only shape as a demand for "an
    exact window_id approval anchor". Both must therefore be present.
    """
    _route(monkeypatch, "second_profile", pids=(4242,))
    calls = _capture_prepare(monkeypatch, window_id=9090)
    cb.CuaBackend().execute("browser_control",
                            {"action": "go_to", "url": "https://example.com"})
    prepare = calls[0]
    assert prepare["strategy"] == {"kind": "existing_profile"}
    assert prepare["pid"] == 4242
    assert prepare["window_id"] == 9090


def test_a_running_browser_is_never_replaced_by_a_second_profile(monkeypatch):
    """The reported symptom: a signed-out profile driven in their session's place."""
    _route(monkeypatch, "second_profile")
    calls = _capture_prepare(monkeypatch, refusal=(
        "browser_consent_required",
        "existing-profile attachment in standard mode requires --grant "
        "existing-profile or an embedding authorization host"))

    with pytest.raises(cb.DriverRefused) as caught:
        cb.CuaBackend().execute("browser_control",
                                {"action": "go_to", "url": "https://example.com"})

    assert caught.value.code == "browser_consent_required"
    assert len(calls) == 1, "a second context must not be prepared as a substitute"
    assert calls[0]["strategy"]["kind"] == "existing_profile"


def test_the_adapter_does_not_answer_the_route_question_itself():
    """Mirrors the diagnostics test: one owner, checked in the source."""
    import inspect

    session_src = inspect.getsource(cb.CuaBackend._browser_session)
    assert "existing_browser" in session_src
    assert "existing_browser.resolve" in inspect.getsource(
        cb.CuaBackend._existing_session)


def test_nothing_starts_the_daemon_with_a_grant():
    """The grant widens machine access, so it is reported, never obtained."""
    import inspect

    assert "--grant" not in inspect.getsource(cb)
