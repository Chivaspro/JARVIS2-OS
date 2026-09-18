"""Cua Driver backend — the PRIMARY GUI controller on Windows, macOS and Linux.

Integration surface: the **Cua Driver CLI**. Cua's own documentation states that
``cua-driver call <tool> <json>`` runs the same code path as its MCP tools, and
that the CLI is the integration surface for shell-oriented agents and automation.
That is the right boundary for Jarvis: a long-running desktop app cannot bundle a
native SDK, and the CLI ships with the installer.

Two rules this module follows, both of which the previous adapter broke:

1. **The driver is never assumed, and never assumed absent.** Support is decided
   at runtime from three observable facts — the binary exists, the daemon answers
   ``cua-driver status``, and the driver reports a tool list. Cua Driver drives
   macOS, Windows *and* Linux; the earlier ``{"Darwin"}`` host allow-list was
   obsolete and made every Windows install fall through to the local backend.
   Any of the three facts missing becomes a *reported reason*, never a silent
   fallback.
2. **The tool surface is discovered, never hardcoded.** The registry is versioned
   and platform-specific (Cua publishes separate macOS, Windows and Linux tool
   references), so every tool name is checked against the driver's own manifest
   before it is called, and every argument is filtered against the tool's
   declared schema. A driver that renames a tool yields an explicit
   unsupported-operation diagnostic instead of a fabricated call.

Discovery prefers the machine-readable sources Cua documents for consumers
(``cua-driver dump-docs --type mcp`` and ``cua-driver manifest``) and falls back
to ``list-tools`` / ``describe <tool>``.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from computer import existing_browser

# ── probe tuning ─────────────────────────────────────────────────────────────
BINARY_NAME = "cua-driver"
PROBE_TTL_S = 30.0        # a probe result stays fresh this long; re-probed after
PROBE_TIMEOUT_S = 6.0     # version / status / list-tools must answer fast
CALL_TIMEOUT_S = 90.0     # a window-tree walk can legitimately take ~20s

# The one URL a user needs when the driver is missing. Sourced from the official
# installation guide (cua.ai/docs/how-to-guides/driver/install).
INSTALL_HINT = {
    "Windows": "irm https://cua.ai/driver/install.ps1 | iex",
    "Darwin": "/bin/bash -c \"$(curl -fsSL https://cua.ai/driver/install.sh)\"",
    "Linux": "/bin/bash -c \"$(curl -fsSL https://cua.ai/driver/install.sh)\"",
}

_probe_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "data": None}
_schema_cache: dict[str, Any] = {}


class UnsupportedOnThisDriver(RuntimeError):
    """The installed driver exposes no tool for this operation."""


class AttachTargetUnavailable(RuntimeError):
    """The browser the user has open could not be anchored to for an attach.

    Distinct from a refusal: the driver was never asked, because an
    existing-profile request is gated on *two* identifiers — the process id and an
    exact window anchor — and one of them could not be resolved. Naming this
    condition keeps it separable from the missing-grant case, which is the user's
    to act on, and from the permission problem it is not.
    """


class SessionEnded(RuntimeError):
    """The driver refused a call because its session is no longer alive.

    This is a *recoverable* condition and the driver says so itself: it refuses
    the call and names the tool that revives it. Distinguishing it from a real
    failure is what stops a dead session from disabling the backend for the rest
    of the process (change: fix-computer-control-driver-diagnostics).
    """

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(message)
        self.tool = tool
        self.raw = message


# The driver's own wording for a session that has ended, plus the revive hint.
# Matching the driver's words — not a guess at what a failure "usually" looks
# like — is the whole point: this signal is what the previous adapter lacked.
_SESSION_ENDED_RE = re.compile(
    r"(?:session\b[^.\n]*\b(?:has\s+ended|ended|expired|no\s+longer\s+alive)\b)"
    r"|(?:start_session[^.\n]*\brevive\b)",
    re.IGNORECASE,
)


def _is_session_ended(text: str) -> bool:
    """Does this driver output say the session has ended?"""
    return bool(text) and _SESSION_ENDED_RE.search(text) is not None


class DriverRefused(RuntimeError):
    """The driver refused a call, stating *why* in a structured envelope.

    The driver answers some calls with ``{"status": "refused", "refusal":
    {"code": ..., "message": ...}}`` — for example
    ``browser_route_unavailable`` when it has no Chromium it may launch. That
    code *is* the diagnosis, so it must never be flattened into a generic
    failure the way an unrecognised exception is.
    """

    def __init__(self, code: str, message: str, tool: str = "") -> None:
        super().__init__((f"{code}: {message}" if code and message else
                          code or message or "the driver refused the call"))
        self.code = code or "driver_refused"
        self.message = message or ""
        self.tool = tool


def refusal_of(payload: Any) -> tuple[str, str] | None:
    """``(code, message)`` from a structured refusal envelope, else ``None``.

    Both shapes the driver uses are accepted: a ``refusal`` object, and a flat
    ``status: refused`` with a top-level code.
    """
    if not isinstance(payload, dict):
        return None
    refusal = payload.get("refusal")
    if isinstance(refusal, dict):
        return (str(refusal.get("code") or "driver_refused"),
                str(refusal.get("message") or payload.get("message") or ""))
    if str(payload.get("status") or "").strip().lower() == "refused":
        return (str(payload.get("code") or "driver_refused"),
                str(payload.get("message") or ""))
    return None


# ── process helpers ──────────────────────────────────────────────────────────

def _no_window_kwargs() -> dict:
    """Keep probe subprocesses from flashing a console over the HUD."""
    if platform.system() == "Windows":
        try:
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        except Exception:                                  # pragma: no cover
            return {}
    return {}


def _run(binary: str, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run the driver binary. Never raises for a non-zero exit; the caller reads
    ``returncode`` and decides. ``stdin`` is closed so a command that would read
    a piped payload can never block the HUD thread."""
    return subprocess.run(
        [binary, *args],
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        **_no_window_kwargs(),
    )


def _install_roots() -> list[Path]:
    """Directories the driver's own installers use.

    Data, not guesses. The Windows installer reported
    ``%LOCALAPPDATA%\\Programs\\Cua\\cua-driver\\bin`` — and did **not** put that
    directory on PATH, so a perfectly good install looks missing to anything
    that only runs ``which``. That is the same class of mistake as the old
    ``{"Darwin"}`` allow-list, and it was found by installing the driver.
    """
    home = Path.home()
    roots = [home / ".local" / "bin", home / ".cua-driver" / "bin",
             home / ".cua-driver"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        roots += [base / "Programs", base / "cua-driver", base]
    return roots


def _search_dirs(roots: list[Path], depth: int = 3, limit: int = 120) -> list[Path]:
    """The roots plus a bounded descent, so a versioned layout still matches.

    Nothing here is recursive-forever: a handful of directories, twenty entries
    each, three levels — cheap enough to run on every probe.
    """
    out: list[Path] = []
    queue: list[tuple[Path, int]] = [(r, 0) for r in roots]
    while queue and len(out) < limit:
        directory, level = queue.pop(0)
        try:
            if not directory.is_dir():
                continue
        except Exception:
            continue
        out.append(directory)
        if level >= depth:
            continue
        try:
            for child in sorted(directory.iterdir())[:20]:
                try:
                    if child.is_dir():
                        queue.append((child, level + 1))
                except Exception:
                    continue
        except Exception:
            continue
    return out


def _candidate_binaries() -> list[str]:
    """Where the driver can legitimately live. PATH is checked first but is not
    required — the installers do not always add themselves to it."""
    names = [BINARY_NAME] + ([f"{BINARY_NAME}.exe"]
                             if platform.system() == "Windows" else [])
    found: list[str] = []
    which = shutil.which(BINARY_NAME)
    if which:
        found.append(which)
    for directory in _search_dirs(_install_roots()):
        for name in names:
            candidate = directory / name
            try:
                if candidate.is_file():
                    found.append(str(candidate))
            except Exception:
                continue
    # De-duplicate, preserving order.
    seen: set[str] = set()
    unique = []
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


# ── facts about the installed driver ─────────────────────────────────────────

def _parse_json_lenient(text: str) -> Any:
    """Parse JSON, tolerating a human-readable preamble around it.

    The driver's documented machine-readable paths (``dump-docs --type mcp -p``,
    ``manifest -p``) emit clean JSON; the human-facing commands may not, so a
    brace-matching scan is used as a second attempt before giving up.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def _parse_tool_list(text: str) -> list[str]:
    """Tool names from whatever shape ``list-tools`` prints.

    Accepts a JSON payload of names, a JSON payload of tool objects, or plain
    ``name — description`` lines. Unknown shapes yield an empty list, which the
    caller reports as "driver answered but listed no tools" rather than a guess.
    """
    payload = _parse_json_lenient(text)
    if isinstance(payload, list):
        names = []
        for item in payload:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "tool", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
        if names:
            return sorted({n for n in names if _TOOL_NAME_RE.match(n)})
    if isinstance(payload, dict):
        for key in ("tools", "mcp_tools", "items"):
            value = payload.get(key)
            # A tool registry may be a list of entries or a name->description map.
            if isinstance(value, (list, dict)) and value:
                return _parse_tool_list(json.dumps(value))
        if payload and all(isinstance(v, (dict, str)) for v in payload.values()):
            names = [k for k in payload if _TOOL_NAME_RE.match(k)]
            if names:
                return sorted(names)
    names = []
    for line in (text or "").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        token = token.strip("-*:,")
        if _TOOL_NAME_RE.match(token):
            names.append(token)
    return sorted(set(names))


def _dump_docs_mcp(binary: str) -> dict:
    """The driver's own machine-readable MCP documentation, if it offers it."""
    try:
        proc = _run(binary, ["dump-docs", "--type", "mcp", "-p"], PROBE_TIMEOUT_S)
        if proc.returncode == 0:
            payload = _parse_json_lenient(proc.stdout or "")
            if isinstance(payload, dict) and payload:
                return payload
    except Exception:
        pass
    return {}


def _tools_from_docs(docs: dict) -> list[str]:
    """Tool names out of the docs payload, whatever nesting it uses."""
    found: list[str] = []
    stack = [docs]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("tools", "mcp_tools") and isinstance(value, (list, dict)):
                    found += _parse_tool_list(json.dumps(value))
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack += node
    return sorted(set(found))


def _probe() -> dict:
    """Collect every fact about the driver required to decide availability.

    Cost is bounded: PATH/bin lookups, then at most four short subprocess calls
    (version, status, dump-docs, list-tools), each with its own timeout.
    """
    system = platform.system()
    data: dict[str, Any] = {
        "host": system,
        "host_supported": system in ("Windows", "Darwin", "Linux"),
        "binary_path": None,
        "binary_version": None,
        "daemon_running": False,
        "tools": [],
        "sdk_present": False,
        "reason": "",
        "install_hint": INSTALL_HINT.get(system, INSTALL_HINT["Linux"]),
        "checked_at": time.time(),
    }
    try:
        import importlib.util as _util
        data["sdk_present"] = _util.find_spec("cua_driver") is not None
    except Exception:
        data["sdk_present"] = False

    candidates = _candidate_binaries()
    if not candidates:
        data["reason"] = ("cua-driver binary not found on PATH or in the driver's "
                          "documented install locations")
        return data
    binary = candidates[0]
    data["binary_path"] = binary
    data["binary_candidates"] = candidates

    try:
        proc = _run(binary, ["--version"], PROBE_TIMEOUT_S)
        data["binary_version"] = (proc.stdout or proc.stderr or "").strip()[:120] or None
    except Exception as exc:
        data["reason"] = f"cua-driver --version failed: {exc}"
        return data

    try:
        proc = _run(binary, ["status"], PROBE_TIMEOUT_S)
        text = f"{proc.stdout or ''}{proc.stderr or ''}".lower()
        # `status` reports whether a daemon is running; a clean exit plus a
        # not-running word is the only honest way to read it.
        running = proc.returncode == 0 and "not running" not in text
        data["daemon_running"] = bool(running)
        data["status_text"] = (proc.stdout or proc.stderr or "").strip()[:200]
    except Exception as exc:
        data["reason"] = f"cua-driver status failed: {exc}"
        return data

    if not data["daemon_running"]:
        data["reason"] = ("cua-driver is installed but no driver daemon is "
                          "running (`cua-driver call` requires one)")
        return data

    docs = _dump_docs_mcp(binary)
    tools = _tools_from_docs(docs) if docs else []
    if not tools:
        try:
            proc = _run(binary, ["list-tools"], PROBE_TIMEOUT_S)
            if proc.returncode == 0:
                tools = _parse_tool_list(proc.stdout or "")
        except Exception:
            tools = []
    data["tools"] = tools
    data["tool_count"] = len(tools)
    if not tools:
        data["reason"] = ("driver daemon is running but it listed no tools — "
                          "refusing to route GUI actions to it")
        return data

    data["reason"] = ""
    return data


def diagnose(force: bool = False) -> dict:
    """Cached driver facts. Safe to call on every startup and every diagnostics
    run; re-probes after ``PROBE_TTL_S`` so installing the driver mid-session is
    picked up without a restart."""
    with _probe_lock:
        fresh = (time.time() - float(_cache.get("at") or 0.0)) < PROBE_TTL_S
        if not force and fresh and _cache.get("data") is not None:
            return dict(_cache["data"])
        data = _probe()
        _cache["at"] = time.time()
        _cache["data"] = data
        return dict(data)


def reset_probe_cache() -> None:
    """Test/teardown hook — forget everything probed so far."""
    with _probe_lock:
        _cache["at"] = 0.0
        _cache["data"] = None
        _schema_cache.clear()


def detect() -> bool:
    """True only when a driver that can actually be driven was found.

    That means: supported host, binary present, daemon answering, and a tool list
    reported. Anything less is reported by ``diagnose()["reason"]`` — the old
    implementation answered from a platform allow-list instead of from reality.
    """
    d = diagnose()
    return bool(d.get("host_supported") and d.get("binary_path")
                and d.get("daemon_running") and d.get("tools"))


def unavailable_reason() -> str:
    """Why the Cua backend is not usable right now ("" when it is)."""
    d = diagnose()
    if not d.get("host_supported"):
        return f"host {d.get('host')} is not one Cua Driver supports"
    return str(d.get("reason") or "")


# ── calling tools ────────────────────────────────────────────────────────────

def list_tools() -> list[str]:
    return list(diagnose().get("tools") or [])


def schema_for(tool: str) -> dict:
    """The tool's declared JSON input schema, or ``{}`` when unavailable."""
    if tool in _schema_cache:
        return _schema_cache[tool]
    schema: dict = {}
    d = diagnose()
    binary = d.get("binary_path")
    if not binary:
        _schema_cache[tool] = schema
        return schema
    docs = _dump_docs_mcp(binary)
    stack = [docs]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("name") == tool and isinstance(node.get("inputSchema"), dict):
                schema = node["inputSchema"]
                break
            if node.get("name") == tool and isinstance(node.get("input_schema"), dict):
                schema = node["input_schema"]
                break
            stack += [v for v in node.values() if isinstance(v, (dict, list))]
        elif isinstance(node, list):
            stack += node
    if not schema:
        try:
            proc = _run(binary, ["describe", tool], PROBE_TIMEOUT_S)
            payload = _parse_json_lenient(proc.stdout or "")
            if isinstance(payload, dict):
                for key in ("inputSchema", "input_schema", "schema", "parameters"):
                    if isinstance(payload.get(key), dict):
                        schema = payload[key]
                        break
                if not schema and isinstance(payload.get("properties"), dict):
                    schema = payload
        except Exception:
            schema = {}
    _schema_cache[tool] = schema
    return schema


def call_tool(tool: str, args: dict | None = None,
              timeout: float = CALL_TIMEOUT_S,
              screenshot_out: str | None = None) -> str:
    """Invoke one driver tool. Returns its stdout (the ``✅`` envelope).

    The tool must be in the driver's own reported list, and every argument is
    filtered to that tool's declared schema, so this can neither invent a tool nor
    invent a parameter. Raises ``UnsupportedOnThisDriver`` for a tool the installed
    driver does not have, and ``RuntimeError`` with the driver's own message for a
    call the driver rejects.
    """
    d = diagnose()
    available = d.get("tools") or []
    if tool not in available:
        raise UnsupportedOnThisDriver(
            f"the installed Cua Driver has no '{tool}' tool "
            f"(it reports: {', '.join(available) or 'nothing'})"
        )
    binary = d.get("binary_path")
    if not binary:
        raise RuntimeError("cua-driver binary disappeared between probe and call")

    payload = dict(args or {})
    schema = schema_for(tool)
    if schema:
        declared = schema.get("properties")
        if isinstance(declared, dict):
            unknown = [k for k in payload if k not in declared]
            for key in unknown:
                payload.pop(key, None)
        required = schema.get("required") or []
        missing = [k for k in required if k not in payload]
        if missing:
            raise RuntimeError(
                f"{tool} requires {', '.join(missing)}; "
                f"got {', '.join(sorted(payload)) or 'nothing'}"
            )

    argv = ["call", tool, json.dumps(payload)]
    if screenshot_out:
        argv += ["--screenshot-out-file", str(screenshot_out)]
    proc = _run(binary, argv, timeout)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # Checked *before* the exit-code branch, so a call the driver rejected over a
    # dead session is never mistaken for a broken tool, a bad argument, or a
    # generic driver error. A rejected call was never executed, which is also
    # what makes retrying it safe.
    if tool != "start_session" and _is_session_ended(f"{err}\n{out}"):
        raise SessionEnded(tool, (err or out).strip())
    # A refusal can arrive with exit code 0, because the driver answered — it just
    # said no. Checked here (behind a cheap substring probe, so a large
    # accessibility tree is not re-parsed) so the driver's own code reaches the
    # caller instead of being lost as "answered without target_id/tab_id".
    if '"refus' in out:
        refusal = refusal_of(_parse_json_lenient(out))
        if refusal:
            raise DriverRefused(refusal[0], refusal[1], tool)
    if proc.returncode != 0:
        raise RuntimeError(f"{tool} failed (exit {proc.returncode}): "
                           f"{(err or out)[:400]}")
    return out or err


# ── session lifecycle ────────────────────────────────────────────────────────
# The driver attaches calls to a named lifecycle session, and rejects any call
# that names one which has ended — explicitly telling the caller to revive it.
# The adapter therefore owns the session: it is opened lazily before the first
# action and revived when the driver says it is gone. Nothing did this before,
# so one ended session disabled every browser action until the app restarted.

_session_lock = threading.Lock()
_session_live: dict[str, bool] = {}


def ensure_session(label: str, force: bool = False) -> bool:
    """Open (or return) the driver session for this transport.

    Returns ``True`` when a session is believed live. A driver that reports no
    session tool at all is treated as live too — there is no session to manage,
    and the action's own result will carry any real problem.
    """
    with _session_lock:
        if _session_live.get(label) and not force:
            return True
    try:
        call_tool("start_session", {"session": label})
    except UnsupportedOnThisDriver:
        with _session_lock:
            _session_live[label] = True
        return True
    except Exception:
        # Do not mask the action's own failure with a session error: report that
        # no session could be opened and let the call surface its reason.
        with _session_lock:
            _session_live[label] = False
        return False
    with _session_lock:
        _session_live[label] = True
    return True


def revive_session(label: str) -> bool:
    """Force the session back to life (the driver's ``start_session``)."""
    return ensure_session(label, force=True)


def session_is_live(label: str) -> bool:
    """Whether the adapter currently believes this session is alive."""
    with _session_lock:
        return bool(_session_live.get(label))


def forget_session(label: str) -> None:
    """Drop cached session knowledge (used by tests)."""
    with _session_lock:
        _session_live.pop(label, None)


# ── the backend ──────────────────────────────────────────────────────────────

# Jarvis action → driver tool. Every name here was checked against the driver's
# own 57-tool manifest (0.28.2) before being written down; actions with no
# documented equivalent deliberately have no entry, so they raise.
_ACTION_TOOLS = {
    # observation
    "screenshot": "get_desktop_state",
    "screen": "get_desktop_state",
    "screen_size": "get_screen_size",
    "cursor_position": "get_cursor_position",
    "list_apps": "list_apps",
    "list_windows": "list_windows",
    "ui_tree": "get_accessibility_tree",
    "window_state": "get_window_state",
    "inspect": "get_window_state",
    "inspect_window": "get_window_state",
    # mouse
    "click": "click",
    "left_click": "click",
    "double_click": "double_click",
    "right_click": "right_click",
    "move": "move_cursor",
    "move_cursor": "move_cursor",
    "drag": "drag",
    "scroll": "scroll",
    # keyboard and text
    "type": "type_text",
    "type_text": "type_text",
    "key": "press_key",
    "press_key": "press_key",
    "hotkey": "hotkey",
    "set_value": "set_value",
    "invoke_menu": "invoke_menu",
    "clipboard_read": "clipboard_read",
    "clipboard_write": "clipboard_write",
    # applications and windows
    "launch": "launch_app",
    "open_app": "launch_app",
    "focus": "bring_to_front",
    "bring_to_front": "bring_to_front",
    "close": "kill_app",
    "kill": "kill_app",
    # session lifecycle (inspectable without performing a GUI action)
    "sessions": "list_sessions",
    "session_status": "get_session",
    # verification
    "verify": "verify_state",
    # browser (require a prepared target; see _prepare_browser)
    "prepare_browser": "browser_prepare",
    "browse": "browser_navigate",
    "go_to": "browser_navigate",
    "navigate": "browser_navigate",
    "new_tab": "browser_navigate",
    "browser_click": "browser_click",
    "browser_type": "browser_type",
    "browser_state": "get_browser_state",
    "page": "page",
    "browser_dialog": "browser_dialog",
    "browser_download": "browser_download",
}

# Tools whose semantics are carried by the *tool* name, not by a parameter. The
# Tool declarations for open_app/… expose no `action` at all, so without this a
# perfectly valid open_app call would look like an unmapped action.
_TOOL_DEFAULT_ACTION = {"open_app": "open_app", "computer_control": "screenshot"}

# Browser tools that address a prepared target and therefore need target_id and
# tab_id injected from the session bootstrap below.
_BROWSER_TOOLS = {
    "browser_navigate", "browser_click", "browser_type", "get_browser_state",
    "page", "browser_dialog", "browser_download", "browser_pointer",
    "browser_set_input_files",
}

# Jarvis parameter → driver parameter, for the actions whose argument names
# differ from ours. Anything absent passes through unchanged, and anything the
# tool's own schema does not declare is dropped before the call.
_PARAM_ALIASES = {"text": "text", "x": "x", "y": "y", "key": "key",
                  "keys": "keys", "app_name": "name", "app": "name",
                  "pid": "pid", "window_id": "window_id",
                  "element_index": "element_index", "button": "button",
                  "path": "screenshot_out_file", "url": "url",
                  "selector": "ref", "ref": "ref", "tab_id": "tab_id",
                  "target_id": "target_id", "profile": "profile",
                  "query": "query", "name": "name"}


class CuaBackend:
    """Adapter implementing the facade's contract through the Cua Driver CLI."""

    name = "cua"

    def __init__(self) -> None:
        self._session_label = "jarvis"
        # Browser work is two-phase: `browser_prepare` hands back the target and
        # tab ids that every later browser_* tool addresses. That pair is a
        # session, so it is owned here rather than re-derived on every action.
        self._browser_target = ""
        self._browser_tab = ""

    def available(self) -> bool:
        return detect()

    def reason(self) -> str:
        return unavailable_reason() or "cua-driver is not reachable"

    def _driver_args(self, params: dict) -> dict:
        args: dict[str, Any] = {}
        for key, value in params.items():
            if key in ("action", "tool"):
                continue
            mapped = _PARAM_ALIASES.get(key, key)
            if value is not None and value != "":
                args[mapped] = value
        args.setdefault("session", self._session_label)
        return args

    @staticmethod
    def _find_ids(payload: Any) -> tuple[str, str]:
        """The first (target_id, tab_id) pair anywhere in a driver response.

        The reply shape is not part of a tool's input schema, so the ids are
        located structurally rather than by a guessed key path.
        """
        target, tab = "", ""
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("target_id", "targetId") and not target \
                            and isinstance(value, (str, int)):
                        target = str(value)
                    elif key in ("tab_id", "tabId") and not tab \
                            and isinstance(value, (str, int)):
                        tab = str(value)
                    elif isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                stack += node
        return target, tab

    def _browser_session(self, params: dict) -> tuple[str, str]:
        """(target_id, tab_id) for a prepared browser, preparing one on demand.

        The route comes from :mod:`computer.existing_browser` — the single owner of
        "can Jarvis drive the browser the user already has open" — so this adapter
        and the local browser path cannot disagree about the same machine.

        Attach first, always. When a browser of the user's is running, an isolated
        context is *not* prepared as a substitute: that is a different, signed-out
        browser, and presenting it as their session is exactly how a page needing a
        sign-in becomes a mystery failure. An isolated context is the last resort,
        for when nothing of theirs is running to be driven.
        """
        if self._browser_target and self._browser_tab:
            return self._browser_target, self._browser_tab

        profile = str(params.get("profile") or "").strip().lower()
        # A caller naming a non-default profile is asking for that context rather
        # than for the user's own browser. Anything else — including no profile at
        # all — is a request to drive the session the user already has.
        wants_existing = profile in ("", "default", "auto", "existing",
                                     "existing_profile")
        info = self._existing_session(params) if wants_existing else None

        if info is not None and info.get("how") in (existing_browser.ATTACH,
                                                   existing_browser.SECOND_PROFILE):
            args = self._attach_args(params, info)
        else:
            args = {"session": self._session_label,
                    "profile": {"mode": "isolated_named",
                                "name": str(params.get("profile_name") or "jarvis")},
                    "allow_launch": bool(params.get("allow_launch", True))}

        out = call_tool("browser_prepare", args)
        target, tab = self._find_ids(_parse_json_lenient(out or ""))
        if not (target and tab):
            raise RuntimeError(
                "browser_prepare answered without target_id/tab_id: "
                + (out or "")[:300]
            )
        self._browser_target, self._browser_tab = target, tab
        return target, tab

    @staticmethod
    def _existing_session(params: dict) -> dict[str, Any] | None:
        """The shared resolver's finding for the browser in question."""
        browser = str(params.get("browser") or params.get("browser_name")
                      or params.get("engine") or "chrome").strip().lower()
        try:
            return existing_browser.resolve(browser)
        except Exception:
            return None

    def _attach_args(self, params: dict, info: dict) -> dict[str, Any]:
        """An existing-profile request carrying *every* identifier its schema needs.

        The driver gates ``strategy.kind=existing_profile`` on two identifiers at
        once — the process id and an exact ``window_id`` approval anchor. Asking with
        only one is rejected before any consent check runs: the window-only shape as
        "Missing required integer field: pid", the pid-only shape as a demand for
        "an exact window_id approval anchor". Both are resolved here, so an
        incomplete request cannot be issued at all.
        """
        pids = info.get("pids") or []
        pid = params.get("pid") or (pids[0] if pids else None)
        window_id = params.get("window_id")
        if window_id is None:
            window_id = self._browser_window_id()
        if pid is None or window_id is None:
            raise AttachTargetUnavailable(
                f"{info.get('browser') or 'the browser'} is running, but no "
                f"{'window' if pid is not None else 'process'} could be anchored "
                f"to, so an existing-profile attach cannot be requested"
            )
        return {"session": self._session_label,
                "strategy": {"kind": "existing_profile"},
                "pid": int(pid),
                "window_id": int(window_id),
                "allow_launch": bool(params.get("allow_launch", True))}

    def _browser_window_id(self) -> int | None:
        """Native window id of an open browser — the driver's approval anchor.

        Supplies the *anchor* only. Which route to take — attach to the browser the
        user has open, or prepare an isolated context — is decided by the shared
        resolver in :mod:`computer.existing_browser`, so this scan cannot become a
        second, divergent answer to the same question.
        """
        out = call_tool("list_windows", {"session": self._session_label})
        stack = [_parse_json_lenient(out or "")]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                title = str(node.get("title") or node.get("name") or "").lower()
                wid = node.get("window_id", node.get("id"))
                if title and isinstance(wid, int) and any(
                        b in title for b in ("chrome", "edge", "brave", "chromium")):
                    return wid
                stack += [v for v in node.values() if isinstance(v, (dict, list))]
            elif isinstance(node, list):
                stack += node
        return None

    def execute(
        self,
        tool: str,
        parameters: dict | None,
        response=None,
        player=None,
        session_memory=None,
    ) -> str:
        """Route one Jarvis GUI action to the driver.

        Deliberately narrow: an action with no documented driver equivalent
        raises ``UnsupportedOnThisDriver`` listing what the driver *does* offer,
        instead of silently doing something else (or nothing).
        """
        if not self.available():
            raise RuntimeError(
                "Cua driver is not usable right now: " + (self.reason() or "unknown")
            )
        params = dict(parameters or {})
        # Some tools carry their meaning in the tool name: the open_app
        # declaration has no `action` parameter at all.
        action = (str(params.get("action", "")).strip().lower()
                  or _TOOL_DEFAULT_ACTION.get(tool, ""))
        driver_tool = _ACTION_TOOLS.get(action)
        if driver_tool is None:
            raise UnsupportedOnThisDriver(
                f"no Cua Driver tool is mapped for {tool}/{action or '*'}; "
                f"driver offers: {', '.join(sorted(list_tools()))}"
            )
        # A screenshot is only useful written to a file rather than embedded as
        # base64 in stdout. Both documented mechanisms are used: the tool's own
        # `screenshot_out_file` argument (which also switches the structured
        # response to a path) and the CLI's `--screenshot-out-file` for any tool
        # that returns an image block without such an argument.
        screenshot_out = None
        if driver_tool in ("get_desktop_state", "get_window_state"):
            screenshot_out = self._shot_path()

        def _attempt() -> str:
            args = self._driver_args(params)
            if driver_tool in _BROWSER_TOOLS:
                target, tab = self._browser_session(params)
                # Preparation is done. These arguments describe `browser_prepare`,
                # not the addressed action, and the driver rejects any argument its
                # own schema does not declare.
                for owned in ("profile", "profile_name", "allow_launch",
                              "pid", "window_id"):
                    args.pop(owned, None)
                args.setdefault("target_id", target)
                args.setdefault("tab_id", tab)
            if screenshot_out:
                args.setdefault("screenshot_out_file", screenshot_out)
            return call_tool(driver_tool, args, screenshot_out=screenshot_out)

        # The session is opened lazily before the first action of the process.
        ensure_session(self._session_label)
        try:
            out = _attempt()
        except SessionEnded:
            # The driver *refused* the call, so nothing was executed and a single
            # retry cannot double an action. Everything derived from the dead
            # session is dropped: a browser target prepared by a session that has
            # ended is not addressable by the new one, so it must be re-prepared
            # rather than reused.
            self._browser_target = self._browser_tab = ""
            if not revive_session(self._session_label):
                raise
            out = _attempt()
        detail = out[:600] if out else "done"
        if screenshot_out:
            detail += f" | screenshot={screenshot_out}"
        return f"[cua] {driver_tool}: {detail}"

    @staticmethod
    def _shot_path() -> str:
        """Where a driver screenshot should land. Kept inside the workspace."""
        try:
            base = Path(__file__).resolve().parent.parent / "runtime" / "cua"
            base.mkdir(parents=True, exist_ok=True)
            return str(base / f"shot-{int(time.time() * 1000)}.png")
        except Exception:
            return str(Path.cwd() / f"cua-shot-{int(time.time() * 1000)}.png")


def status_detail() -> dict[str, Any]:
    """Driver facts in the shape the facade's diagnostics consume."""
    d = diagnose()
    return {
        "name": CuaBackend.name,
        "available": bool(d.get("host_supported") and d.get("binary_path")
                          and d.get("daemon_running") and d.get("tools")),
        "host": d.get("host"),
        "host_supported": bool(d.get("host_supported")),
        "binary_present": bool(d.get("binary_path")),
        "binary_path": d.get("binary_path"),
        "binary_version": d.get("binary_version"),
        "daemon_running": bool(d.get("daemon_running")),
        "sdk_present": bool(d.get("sdk_present")),
        "tool_count": len(d.get("tools") or []),
        "tools": list(d.get("tools") or []),
        "reason": d.get("reason") or "",
        "install_hint": d.get("install_hint") or "",
    }
