"""ComputerUse facade — the single entry point for real-machine control.

Routing:

    Jarvis tool  →  ComputerUse  →  backend selection
                                       ├── CuaBackend (PRIMARY, when reachable)
                                       └── LocalBackend (deliberate fallback)

Selection happens once per process and is *reported*, never silent: the chosen
backend, the mode that chose it and the driver's own reason for being unusable
are all logged and surfaced through diagnostics. Nothing here decides support
from a platform allow-list — the Cua backend answers for itself from observable
driver state (see ``computer.cua_backend``).

Every action is logged with its backend, target, duration and result, through the
redacting observability logger, and every failure carries an actionable
diagnostic (see :class:`ComputerActionError`).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from computer.local_backend import (
    LocalBackend,
    BackendUnavailable,
    BROWSER_DEPENDENT_ACTIONS,
)
from computer.cua_backend import (
    AttachTargetUnavailable,
    CuaBackend,
    DriverRefused,
    SessionEnded,
    UnsupportedOnThisDriver,
    detect as cua_detect,
    diagnose as cua_diagnose,
    _is_session_ended as session_ended_in_text,
)

_lock = threading.Lock()
_instance: "ComputerUse | None" = None

_ROUTED_TOOLS = {
    "computer_control", "browser_control", "open_app",
    "desktop_control", "computer_settings",
}


def _log() -> Any:
    """The redacting structured logger, or a no-op stand-in.

    Observability must never be the thing that breaks GUI control.
    """
    try:
        from observability import get_logger
        return get_logger("computer")
    except Exception:
        class _Null:
            def event(self, *a, **kw) -> None:
                pass

            def info(self, *a, **kw) -> None:
                pass

            def warning(self, *a, **kw) -> None:
                pass

            def error(self, *a, **kw) -> None:
                pass
        return _Null()


class ComputerActionError(RuntimeError):
    """A GUI action failed, with the diagnosis attached.

    ``str(exc)`` is the concise user-facing line; :attr:`detail` is the
    multi-line internal diagnostic (backend, target, reason, next action) that
    belongs in logs and diagnostics rather than in speech.
    """

    def __init__(self, summary: str, diagnostic: dict, detail: str) -> None:
        super().__init__(summary)
        self.summary = summary
        self.diagnostic = diagnostic
        self.detail = detail


# ── failure classification ────────────────────────────────────────────────────
# Structured facts first, unambiguous text second, the last resort last. The order
# *is* the fix: the previous version pattern-matched unknown text and answered with
# one catch-all label for every cause it did not recognise, which is how a dead Cua
# session, a truncated tool manifest and a missing browser binary all reached the
# user as the same meaningless "driver error".

_PLAYWRIGHT_BROWSERS_MISSING = (
    "executable doesn't exist",
    "executable does not exist",
    "please run the following command to download new browsers",
    "looks like playwright was just installed",
    "browser binaries",
    "browser type launch: executable",
)

_PROFILE_CONFLICT = (
    "singletonlock",
    "processsingleton",
    "user data directory is already in use",
    "profile is already in use",
    "opening in existing browser session",
    "cannot create a file when that file already exists",
)

# A Cua daemon whose tool manifest is truncated cannot serve mapped actions, and
# the log shows exactly that happening (a daemon reporting only `list_apps`).
_CUA_DEGRADED_TOOLS = 20

_BROWSER_KEY_ALIASES = {
    "google chrome": "chrome", "chromium": "chrome", "chrome": "chrome",
    "microsoft edge": "edge", "msedge": "edge", "edge": "edge",
    "mozilla": "firefox", "firefox": "firefox",
    "brave": "brave", "brave browser": "brave",
    "opera": "opera", "operagx": "operagx",
    "vivaldi": "vivaldi", "safari": "safari",
}

def _browser_engine(params: dict) -> str:
    """Which engine a browser action needs, as ``_BROWSER_SPECS`` names it."""
    raw = str(params.get("browser") or params.get("engine")
              or params.get("browser_name") or "").strip().lower()
    return _BROWSER_KEY_ALIASES.get(raw, raw or "chrome")


def browser_binaries_remedy(engine: str) -> str:
    """What to actually run when Playwright cannot find a browser.

    Computed per engine from the same table the browser layer uses
    (``computer.browser_control._BROWSER_SPECS``). An engine served through a
    Playwright *channel* against the browser the user already has (Chrome and
    Edge on Windows) is not fixed by downloading browser binaries, so telling
    its owner to run ``playwright install`` would be both wrong and useless.
    """
    spec: dict = {}
    fallback_engine = "chromium"
    try:
        from computer.browser_control import _BROWSER_SPECS, _OS
        spec = ((_BROWSER_SPECS.get(_OS) or {}).get(engine)) or {}
        fallback_engine = spec.get("engine") or fallback_engine
    except Exception:
        pass
    if spec.get("channel"):
        return (f"the installed {engine} is launched through Playwright's "
                f"'{spec['channel']}' channel, so no browser download is needed — "
                f"check that {engine} is actually installed")
    return f"run `playwright install {fallback_engine}`"


def _looks_like_browser_binaries_missing(text: str) -> bool:
    return any(p in text for p in _PLAYWRIGHT_BROWSERS_MISSING)


def _looks_like_profile_conflict(text: str) -> bool:
    return any(p in text for p in _PROFILE_CONFLICT)


def _looks_like_grant_refusal(text: str) -> bool:
    """A refusal to reach the user's *existing* browser for want of a grant."""
    if "existing-profile" in text or "existing_profile" in text:
        return True
    return ("grant" in text
            and any(w in text for w in ("profile", "browser", "daemon")))


def _cua_state_code() -> str:
    """Why the Cua driver cannot serve an action right now (``""`` when it can).

    Read from the driver's own probe rather than from an exception's wording, so a
    Cua failure is classified from driver state and not from string matching.
    """
    try:
        d = cua_diagnose()
    except Exception:
        return ""
    if not d.get("host_supported"):
        return "host_unsupported"
    if not d.get("binary_path"):
        return "cua_binary_absent"
    if not d.get("daemon_running"):
        return "cua_daemon_unreachable"
    if len(d.get("tools") or []) < _CUA_DEGRADED_TOOLS:
        return "cua_degraded_manifest"
    return ""


def _cua_state_detail() -> str:
    """The driver's own reason, or a plain statement when it cannot be read."""
    try:
        return str(cua_diagnose().get("reason") or "the driver is not usable right now")
    except Exception:
        return "the driver state could not be read"


def _classify(exc: BaseException, params: dict | None = None,
              backend: str = "") -> str:
    """A machine-readable reason code for a failure."""
    # 1. Typed backend errors: the backend already knows what happened.
    #
    # A structured refusal carries the driver's own code, which is a better
    # diagnosis than anything this function could infer — so it is used verbatim.
    if isinstance(exc, DriverRefused):
        return exc.code or "driver_refused"
    if isinstance(exc, SessionEnded):
        return "cua_session_ended"
    if isinstance(exc, AttachTargetUnavailable):
        return "attach_anchor_missing"
    if isinstance(exc, UnsupportedOnThisDriver):
        return "unsupported_on_driver"
    if isinstance(exc, BackendUnavailable):
        return "backend_unavailable"

    text = str(exc).lower()

    # 2. Unambiguous failure text, most specific first.
    #
    # The ended-session signal is checked in text as well as by type, using the
    # adapter's own matcher: the driver may deliver it wrapped in another
    # exception, and a session that has ended must still classify specifically
    # rather than fall through to the catch-all — that fall-through is the bug
    # this change exists to fix.
    if session_ended_in_text(str(exc)):
        return "cua_session_ended"
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    if _looks_like_browser_binaries_missing(text):
        return "browser_binaries_missing"
    if _looks_like_grant_refusal(text):
        return "existing_profile_not_granted"
    if _looks_like_profile_conflict(text):
        return "profile_locked"
    if "unknown action" in text or "unsupported action" in text:
        # The *selected backend* does not implement this action. Distinct from an
        # unclassified failure: the cause is known, and naming it points straight
        # at the backend rather than at a log file.
        return "unsupported_on_backend"
    if "not usable" in text or "daemon" in text or "not found" in text:
        return "driver_unreachable"
    if "no '" in text and "tool" in text:
        return "tool_missing"
    if "requir" in text or "missing" in text:
        return "bad_arguments"

    # 3. Environment facts from the selected backend.
    if backend == "cua":
        code = _cua_state_code()
        if code:
            return code

    # 4. Last resort — never the first thing checked.
    return "driver_error"


def _log_path_hint() -> str:
    """The log file that holds the raw exception behind an unclassified failure."""
    return f"data/logs/jarvis-{time.strftime('%Y%m%d')}.jsonl"


def _next_action(reason_code: str, params: dict | None = None,
                 exc: BaseException | None = None) -> str:
    """What a person should actually do about this failure.

    Never a generic sentence where a specific one is knowable: the last resort
    names the log file, so nobody has to guess where the answer is.
    """
    params = params or {}
    table = {
        "cua_session_ended": ("the driver session is revived automatically; if this "
                             "repeats, run `cua-driver status` and restart the daemon "
                             "with `cua-driver serve`"),
        "cua_daemon_unreachable": "start the driver daemon: `cua-driver serve`",
        "cua_binary_absent": ("install the Cua Driver: " + _cua_install_hint()
                              + ", then `cua-driver serve`"),
        "cua_degraded_manifest": ("the daemon answered with a truncated tool list — "
                                  "restart it (`cua-driver stop`, then `cua-driver serve`)"),
        "host_unsupported": "the Cua Driver does not support this host; the local backend serves it",
        "existing_profile_not_granted": ("the daemon was not started with existing-profile "
                                        "access — restart it with the grant "
                                        "(`cua-driver serve --grant existing-profile`); "
                                        "Jarvis will not grant it for you"),
        "browser_consent_required": (
            "the driver daemon holds no existing-profile grant, so it will not drive "
            "the browser you already have open — restart it with "
            "`cua-driver serve --grant existing-profile` (Jarvis will not grant this "
            "for you), or start your browser with `--remote-debugging-port=9222` so "
            "the local backend can attach to it over CDP"),
        "attach_anchor_missing": (
            "your browser is running but no window could be anchored to for an "
            "existing-profile attach — restore or open a browser window, or start it "
            "with `--remote-debugging-port=9222` so the local backend can attach over "
            "CDP"),
        "browser_binaries_missing": browser_binaries_remedy(_browser_engine(params)),
        "profile_locked": ("the browser you already have open holds the profile — close it "
                          "first, or start it with `--remote-debugging-port=9222` so "
                          "Jarvis can drive the browser you already have"),
        "browser_unavailable": "check the browser finding in `run diagnostics`",
        "unsupported_on_backend": ("the active backend does not implement this action — "
                                  "check which backend is selected with `run diagnostics`"),
        "browser_route_unavailable": (
            "the driver has no Chromium it may launch in an isolated profile — "
            "drive the browser you already have (start it with "
            "`--remote-debugging-port=9222`), or install a Chromium the driver "
            "accepts"),
        "unsupported_on_driver": ("inspect `cua-driver list-tools` and add a mapping "
                                  "in computer/cua_backend.py::_ACTION_TOOLS"),
        "driver_unreachable": "run `cua-driver status`, then `cua-driver serve`",
        "tool_missing": "the installed driver renamed a tool — re-check its tool list",
        "bad_arguments": "inspect the tool schema with `cua-driver describe <tool>`",
        "timeout": "retry once; if it repeats, capture a fresh window state",
        "backend_unavailable": "check which backend is selected in diagnostics",
    }
    code = str(reason_code or "")
    if code in table:
        return table[code]
    if isinstance(exc, DriverRefused):
        # The driver named the cause; repeating it beats claiming the cause is
        # unrecognised, which is only true of an exception nobody classified.
        detail = f" ({exc.message})" if exc.message else ""
        return (f"the driver refused this action: {exc.code}{detail} — the full "
                f"exchange is in {_log_path_hint()}")
    return (f"cause not recognised — read the raw exception in {_log_path_hint()} "
            f"and re-inspect the target with a fresh window state + screenshot")


def _cua_install_hint() -> str:
    """The platform install command, verbatim from the adapter."""
    try:
        from computer.cua_backend import INSTALL_HINT
        return INSTALL_HINT.get(__import__("platform").system(), INSTALL_HINT["Linux"])
    except Exception:
        return "see https://cua.ai/docs/how-to-guides/driver/install"


def _browser_finding() -> dict:
    """The diagnostics module's own browser finding (``{}`` when unavailable)."""
    try:
        from observability.diagnostics import _check_browser
        c = _check_browser()
        return {"state": c.state, "detail": c.detail, "fix": c.fix}
    except Exception:
        return {}


def _target_of(params: dict) -> str:
    """A human label for what the action was aimed at (never a secret)."""
    for key in ("target", "app_name", "app", "window", "url", "key", "selector"):
        value = params.get(key)
        if value:
            return str(value)[:80]
    if params.get("element_index") is not None:
        return f"element_index={params['element_index']}"
    if params.get("x") is not None and params.get("y") is not None:
        return f"({params['x']}, {params['y']})"
    return ""


class ComputerUse:
    """Facade over the active computer-control backend."""

    def __init__(self) -> None:
        self._serial = threading.Lock()   # one GUI controller at a time
        self._backend, self._reason, self._mode = self._select_backend()
        self._last_result: dict[str, Any] | None = None
        self._local: Any = None           # built on first need only
        self._log_selection()

    def _local_backend(self):
        """The local controller, for actions the selected backend cannot serve."""
        if self._local is None:
            self._local = LocalBackend()
        return self._local

    # ── backend selection ─────────────────────────────────────────────────────

    @classmethod
    def _select_backend(cls) -> tuple[Any, str, str]:
        """Resolve the backend, and say why — the reason is part of the result.

        ``auto`` prefers Cua and falls back deliberately, recording the driver's
        own explanation. ``on`` selects Cua even when it is unusable, so the
        failure is loud and attributable rather than a silent downgrade to a
        different controller. ``off`` selects local.
        """
        mode = "auto"
        try:
            from config.settings import get_feature
            mode = str(get_feature("cua_backend", "auto") or "auto").strip().lower()
        except Exception:
            mode = "auto"
        if mode not in ("auto", "on", "off"):
            mode = "auto"

        if mode == "off":
            return LocalBackend(), "cua_backend=off (local backend requested)", mode
        if mode == "on":
            return (CuaBackend(),
                    "cua_backend=on — Cua forced, no fallback (unusable until the "
                    "driver is reachable)", mode)
        if cua_detect():
            d = cua_diagnose()
            version = d.get("binary_version") or "detected"
            return (CuaBackend(),
                    f"Cua Driver usable ({version}, "
                    f"{len(d.get('tools') or [])} tools, daemon running)", mode)
        reason = ""
        try:
            d = cua_diagnose()
            if not d.get("host_supported"):
                reason = f"host {d.get('host')} not supported"
            else:
                reason = d.get("reason") or "driver not detected"
        except Exception as exc:
            reason = f"detection failed: {exc}"
        return LocalBackend(), f"Cua unavailable — {reason}", mode

    def _log_selection(self) -> None:
        try:
            _log().event(
                "INFO", event="backend_selected", tool="computer",
                state="ready" if self.backend_name == "cua" else "fallback",
                result={"backend": self.backend_name, "mode": self._mode},
                error="" if self.backend_name == "cua" else self._reason,
            )
        except Exception:
            pass

    @property
    def backend(self):
        return self._backend

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "name", "local")

    @property
    def selection_reason(self) -> str:
        """Why this backend is active — surfaced verbatim by diagnostics."""
        return self._reason

    @property
    def mode(self) -> str:
        return self._mode

    def available(self) -> bool:
        try:
            return bool(self._backend.available())
        except Exception:
            return False

    def preflight(self, tool: str, action: str = "") -> dict[str, Any]:
        """Can this action be attempted right now? Answered *before* trying it.

        Derived from the same checks ``run diagnostics`` reports, so the live
        failure path and the diagnostics page cannot describe the same situation
        differently — a second implementation of "is the browser stack usable" is
        exactly how those two drift apart. Side-effect free: no GUI call, no
        browser launch, no window touched.
        """
        tool = str(tool or "")
        action = str(action or "").strip().lower()
        browser_dependent = (tool == "browser_control"
                             or action in BROWSER_DEPENDENT_ACTIONS)
        if browser_dependent:
            finding = _browser_finding()
            state = str(finding.get("state") or "")
            if state in ("OFFLINE", "DEGRADED"):
                code = ("browser_binaries_missing" if state == "DEGRADED"
                        else "browser_unavailable")
                return {"ok": False, "reason": code,
                        "detail": str(finding.get("detail") or ""),
                        "evidence": finding,
                        "next_action": _next_action(code),
                        "source": "diagnostics:browser"}
        if self.backend_name == "cua":
            code = _cua_state_code()
            if code:
                return {"ok": False, "reason": code,
                        "detail": _cua_state_detail(),
                        "evidence": {},
                        "next_action": _next_action(code),
                        "source": "diagnostics:cua_driver"}
        return {"ok": True, "reason": "", "detail": f"{self.backend_name} can serve {tool}",
                "evidence": {}, "next_action": "",
                "source": f"backend:{self.backend_name}"}

    # ── execution ─────────────────────────────────────────────────────────────

    def execute(
        self,
        tool: str,
        parameters: dict | None,
        response=None,
        player=None,
        session_memory=None,
        task_id: str = "",
    ) -> str:
        """Run one GUI tool through the active backend, serialized and logged.

        Raises :class:`ComputerActionError` (or ``BackendUnavailable`` for a tool
        no backend implements) with an actionable diagnostic attached.
        """
        if tool not in _ROUTED_TOOLS:
            raise BackendUnavailable(f"unknown computer tool '{tool}'")
        params = dict(parameters or {})
        action = str(params.get("action", "")).strip().lower()
        target = _target_of(params)
        backend = self.backend_name
        started = time.perf_counter()
        # Actions the selected backend has no tool for are delegated to the local
        # controller rather than failing, so choosing Cua as primary cannot remove
        # capability the local backend already had. Delegation happens *inside* the
        # serialization lock, so it is still one controller at a time — never two
        # fighting over the machine. ``cua_backend=on`` (Cua forced, explicitly no
        # fallback) does not delegate: that setting asked for Cua only.
        attempts = [self._backend]
        if self._mode == "auto" and self.backend_name != LocalBackend.name:
            attempts.append(self._local_backend())
        delegating_note = ""
        last_exc: BaseException | None = None
        backend = self.backend_name
        with self._serial:
            result: Any = None
            for index, candidate in enumerate(attempts):
                backend = getattr(candidate, "name", self.backend_name)
                try:
                    result = candidate.execute(
                        tool, parameters,
                        response=response, player=player, session_memory=session_memory,
                    )
                    last_exc = None
                    if index:
                        delegating_note = (
                            f"{self.backend_name} has no mapping for {tool}/"
                            f"{action or '*'}; served by {backend}")
                        _log().event("WARNING", event="backend_delegated", tool=tool,
                                     state=action or "?", task_id=task_id,
                                     result={"from": self.backend_name, "to": backend,
                                             "target": target}, error="")
                    break
                except UnsupportedOnThisDriver as exc:
                    last_exc = exc
                    if index + 1 < len(attempts):
                        continue     # try the backend that can serve this action
                    break
                except Exception as exc:
                    last_exc = exc
                    break
            if last_exc is not None:
                exc = last_exc
                code = _classify(exc, params, backend)
                # An already-written explanation of *why* this session is not the
                # one the user expects (computer.browser_control builds one) has to
                # survive into the diagnostic instead of being flattened into
                # `type: message[:300]` — losing it is what made a rich, correct
                # explanation arrive as a generic driver error.
                note = str(getattr(exc, "profile_note", "") or "")
                diagnostic = {
                    "what": f"{backend} {tool}/{action or '*'} failed",
                    "target": target,
                    "backend": backend,
                    "reason": code,
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    "profile_note": note,
                    "next_action": _next_action(code, params, exc),
                }
                lines = [
                    f"{diagnostic['what']}.",
                    f"Target: {target or '(none)'}",
                    f"Backend: {backend}",
                    f"Reason: {code}",
                    f"Detail: {diagnostic['error']}",
                ]
                if note:
                    lines.append(f"Why this session: {note}")
                lines.append(f"Possible next action: {diagnostic['next_action']}")
                detail = "\n".join(lines)
                _log().event("ERROR", event="action_failed", tool=tool,
                             state=action or "?", task_id=task_id,
                             duration_ms=(time.perf_counter() - started) * 1000.0,
                             result=diagnostic, error=diagnostic["error"])
                raise ComputerActionError(
                    f"{backend} could not complete {action or tool} "
                    f"({code}).", diagnostic, detail) from exc
            duration_ms = (time.perf_counter() - started) * 1000.0
            # A backend returning without raising is not proof the action
            # happened. Verify where a cheap deterministic check exists, and
            # report "unverified" rather than inventing a pass (§8, §16).
            verification = self._verify(tool, params, result, backend)
            self._last_result = {
                "tool": tool,
                "parameters": params,
                "result": result,
                "backend": backend,
                "delegated_from": (self.backend_name if delegating_note else ""),
                "delegated_why": delegating_note,
                "duration_ms": round(duration_ms, 2),
                "verification": verification,
            }
            # The action record every GUI operation emits. Secret-shaped values
            # are redacted by the logger itself (observability.logger), so typed
            # text, tokens and clipboard content never reach the log sinks.
            # Three states, not two: `verification` is only true when a check
            # actually ran and passed. An action for which no check exists is
            # reported as unverified — never as verified (§8, §16).
            vstate = str(verification.get("state")
                         or ("verified" if verification.get("verified") else "failed"))
            _log().event("INFO", event="action", tool=tool,
                         state=action or "?", task_id=task_id,
                         duration_ms=duration_ms,
                         result={"backend": backend, "target": target,
                                 "ok": True,
                                 "delegated_from": (self.backend_name if delegating_note
                                                    else ""),
                                 "verification": bool(verification.get("verified")),
                                 "verification_state": vstate,
                                 "verified_how": verification["reason"]},
                         error="")
            if vstate == "failed":
                # Say so on the same line the model reads. A silent "Done." that
                # did not land is the failure mode this exists to prevent.
                result = (f"{result}\n[unverified] {verification['reason']}")
            return result

    @staticmethod
    def _verify(tool: str, params: dict, result: str, backend: str) -> dict:
        """Post-action verification, or an honest "no check" for this action."""
        try:
            from computer.verification import verify_action
            return verify_action(tool, params, result, backend=backend)
        except Exception as exc:
            return {"verified": False, "reason": f"verification unavailable: {exc}",
                    "evidence": None}

    def last_result(self) -> dict[str, Any] | None:
        return self._last_result


# ── process-wide singleton ────────────────────────────────────────────────────

def get_computer_use() -> ComputerUse:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ComputerUse()
    return _instance


def reset_computer_use() -> None:
    """Test/teardown hook."""
    global _instance
    with _lock:
        _instance = None


def active_backend() -> str:
    return get_computer_use().backend_name


# ── convenience wrappers used by actions/ shims and diagnostics ──────────────

def computer_control_action(
    parameters: dict | None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    return get_computer_use().execute(
        "computer_control", parameters,
        response=response, player=player, session_memory=session_memory,
    )


def _chrome_facts() -> dict[str, Any]:
    """Is the user's browser open, and can Jarvis drive that session?

    Delegates to ``computer.existing_browser`` so this report and the browser
    controller can never disagree about the same machine. "Existing session"
    means *attachable* — a running browser whose profile is locked would
    otherwise be reported as usable and then open as a signed-out window.
    """
    try:
        from computer.existing_browser import existing_session
        return existing_session()
    except Exception as exc:
        return {"browser": "", "process": False, "windows": [],
                "existing_session": False, "profile_dir": None,
                "cdp_reachable": False, "cdp_port": 0,
                "attach_note": f"browser detection unavailable: {exc}"}


def computer_status() -> dict[str, Any]:
    """Backend selection + every fact diagnostics needs, in one call."""
    import platform
    cu = get_computer_use()
    from computer.local_backend import status_detail as local_detail
    from computer.cua_backend import status_detail as cua_detail
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "active_backend": cu.backend_name,
        "mode": cu.mode,
        "reason": cu.selection_reason,
        "local": local_detail(),
        "cua": cua_detail(),
        "chrome": _chrome_facts(),
    }


def computer_report() -> str:
    """The computer-control section of ``run diagnostics``."""
    try:
        st = computer_status()
    except Exception as exc:
        return f"COMPUTER CONTROL\n  unavailable: {type(exc).__name__}: {exc}"
    cua = st["cua"]
    local = st["local"]
    chrome = st["chrome"]
    caps = sorted(k for k, v in local.items()
                  if k not in ("name", "available", "tools") and v)
    lines = [
        "COMPUTER CONTROL",
        f"  OS: {st['os']} {st['os_release']}",
        f"  Cua SDK (cua_driver): {'installed' if cua['sdk_present'] else 'not installed'}",
        f"  Cua Driver: {'installed' if cua['binary_present'] else 'not installed'}"
        + (f" — {cua['binary_version']}" if cua["binary_version"] else ""),
        f"  Driver path: {cua['binary_path'] or 'not found'}",
        f"  Driver daemon: {'running' if cua['daemon_running'] else 'not running'}",
        f"  Driver reachable: {'YES' if cua['available'] else 'NO'}",
        f"  Driver tools: {cua['tool_count'] or 0}"
        + (f" ({', '.join(cua['tools'][:12])}{'…' if cua['tool_count'] > 12 else ''})"
           if cua["tools"] else ""),
        f"  Backend: {st['active_backend'].upper()}  (mode: {st['mode']})",
        f"  Why: {st['reason']}",
        f"  Playwright: {'installed' if local.get('playwright') else 'not installed'}",
        f"  Local fallback: available ({', '.join(caps) or 'no capabilities detected'})",
        f"  Browser process: {'detected' if chrome['process'] else 'not detected'}"
        + (f" ({chrome['browser']})" if chrome.get("browser") else ""),
        f"  Existing browser session: "
        f"{'attachable' if chrome['existing_session'] else 'not attachable'}",
        f"  Browser profile: {chrome['profile_dir'] or 'not found'}",
        f"  DevTools endpoint: "
        + (f"reachable on port {chrome['cdp_port']}" if chrome.get("cdp_reachable")
           else f"not reachable (port {chrome.get('cdp_port') or '?'})"),
    ]
    if not cua["available"]:
        lines.append(f"  Remedy: {cua['install_hint']}" if not cua["binary_present"]
                     else "  Remedy: start the driver daemon: cua-driver serve")
    if chrome["attach_note"]:
        lines.append(f"  Note: {chrome['attach_note']}")
    return "\n".join(lines)


def computer_check() -> "Check":
    """Diagnostics Check reporting the active computer-control backend."""
    try:
        from core.diagnostics import Check, ONLINE, DEGRADED, ERROR
        st = computer_status()
        cua = st["cua"]
        name = st["active_backend"]
        detail = f"backend={name} (mode={st['mode']}) — {st['reason']}"
        if cua["available"]:
            detail += f"; cua-driver {cua['binary_version'] or '?'} reachable"
        state = ONLINE if name == "cua" or st["mode"] == "off" else DEGRADED
        fix = ""
        if name != "cua" and st["mode"] != "off":
            fix = (f"{cua['install_hint']} then `cua-driver serve`"
                   if not cua["binary_present"] else "start the driver daemon: "
                   "cua-driver serve")
        return Check("computer_backend", state, detail, fix)
    except Exception as exc:
        from core.diagnostics import Check, ERROR
        return Check("computer_backend", ERROR, f"{type(exc).__name__}: {exc}",
                     "The computer package failed to import.")
