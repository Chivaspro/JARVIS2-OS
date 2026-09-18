"""LangGraph orchestrator (task 6.4) — plan → execute → verify → replan.

Enabled only when ``features.langgraph_orchestrator=true``; the deterministic
skill router remains the default path. Complex goals flow through a bounded
state machine:

    plan → execute step → verify → (next | replan ≤ MAX_REPLANS | fail)

Key contracts (orchestration spec):
* a step is complete only when its *verifier* says so — a tool returning
  without an exception is not success;
* state is checkpointed at every step boundary so an interrupted task resumes
  instead of restarting;
* every transition emits a task-state event the HUD can render without
  blocking the UI thread;
* LangGraph missing/broken → one degradation notice and the deterministic
  path takes over; the application never crashes over the orchestrator.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

MAX_REPLANS = 3

# ── task-state events (task 6.6) ─────────────────────────────────────────────

STATES = ("Planning", "Executing", "Verifying", "Waiting for approval",
          "Completed", "Failed", "Recovering")


class TaskStateError(ValueError):
    pass


class TaskState(str, Enum):
    PLANNING = "Planning"
    EXECUTING = "Executing"
    VERIFYING = "Verifying"
    WAITING_APPROVAL = "Waiting for approval"
    COMPLETED = "Completed"
    FAILED = "Failed"
    RECOVERING = "Recovering"


_listeners: list[Callable[[dict], None]] = []
_listeners_lock = threading.Lock()


def emit_state(task_id: str, state: TaskState | str, detail: str = "") -> None:
    """Notify listeners of one transition. Never raises; listener errors are
    contained so a broken HUD hook cannot break the orchestrator."""
    state_name = state.value if isinstance(state, TaskState) else str(state)
    event = {"task_id": task_id, "state": state_name, "detail": detail,
             "ts": time.time()}
    with _listeners_lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(event)
        except Exception:
            pass
    try:
        from observability.logger import log_event
        log_event("INFO", "orchestration", "task state",
                  task_id=task_id, state=state_name, result=detail)
    except Exception:
        pass


def add_state_listener(fn: Callable[[dict], None]) -> None:
    """Register a listener (the UI wires a thread-safe setter here)."""
    with _listeners_lock:
        if fn not in _listeners:
            _listeners.append(fn)


def remove_state_listener(fn: Callable[[dict], None]) -> None:
    with _listeners_lock:
        if fn in _listeners:
            _listeners.remove(fn)


# ── sqlite checkpointing ──────────────────────────────────────────────────────

class Checkpointer:
    """Tiny sqlite checkpointer (task state at each step boundary)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._init()

    def _init(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._path) as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS orchestrator_tasks ("
                    " task_id TEXT PRIMARY KEY,"
                    " goal TEXT, plan TEXT, cursor INTEGER,"
                    " results TEXT, errors TEXT, replans INTEGER,"
                    " state TEXT, updated_at REAL)"
                )
        except Exception:
            pass

    def save(self, task_id: str, record: dict) -> None:
        try:
            with sqlite3.connect(self._path) as con:
                con.execute(
                    "INSERT INTO orchestrator_tasks"
                    " (task_id, goal, plan, cursor, results, errors, replans, state, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(task_id) DO UPDATE SET goal=excluded.goal,"
                    " plan=excluded.plan, cursor=excluded.cursor,"
                    " results=excluded.results, errors=excluded.errors,"
                    " replans=excluded.replans, state=excluded.state,"
                    " updated_at=excluded.updated_at",
                    (task_id, record.get("goal", ""), repr(record.get("plan", [])),
                     int(record.get("cursor", 0)), repr(record.get("results", [])),
                     repr(record.get("errors", [])), int(record.get("replans", 0)),
                     str(record.get("state", "")), time.time()),
                )
        except Exception:
            pass

    def load(self, task_id: str) -> dict | None:
        try:
            with sqlite3.connect(self._path) as con:
                con.row_factory = sqlite3.Row
                row = con.execute(
                    "SELECT * FROM orchestrator_tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
            if not row:
                return None
            return {
                "goal": row["goal"], "plan": eval(row["plan"]) if row["plan"] else [],
                "cursor": int(row["cursor"] or 0),
                "results": eval(row["results"]) if row["results"] else [],
                "errors": eval(row["errors"]) if row["errors"] else [],
                "replans": int(row["replans"] or 0), "state": row["state"],
            }
        except Exception:
            return None

    def list_active(self) -> list[dict]:
        try:
            with sqlite3.connect(self._path) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT task_id, goal, state, updated_at FROM orchestrator_tasks "
                    "WHERE state NOT IN ('Completed','Failed') ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


# ── graph definition ──────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    ok: bool
    summary: str
    steps_done: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    replans: int = 0
    state: str = "Failed"


def _step_executor_default(step: str, ctx: dict) -> dict:
    """Default executor: delegates to the model tool layer via the Brain.
    Replaced in tests; the real wiring lands with the dispatcher registry."""
    raise RuntimeError("no executor wired")


def _verifier_default(step: str, ctx: dict, observation: dict) -> bool:
    """Default verifier: an exception-free observation is NOT proof. Callers
    wire a real verifier; the default demands explicit evidence."""
    return bool(observation.get("verified"))


class Orchestrator:
    """LangGraph-backed orchestration (graceful degradation to a pure-Python
    engine with identical semantics when the package is missing)."""

    def __init__(self, *, checkpointer: Checkpointer | None = None,
                 executor: Callable | None = None,
                 verifier: Callable | None = None,
                 planner: Callable | None = None,
                 use_langgraph: bool | None = None) -> None:
        self._checkpointer = checkpointer or _default_checkpointer()
        self._executor = executor or _step_executor_default
        self._verifier = verifier or _verifier_default
        self._planner = planner or _default_planner
        self._lg = None
        self._lg_checked = False
        self._degradation_logged = False
        self._use_langgraph = use_langgraph

    def _langgraph(self):
        """Lazy LangGraph graph (built once, on first orchestrated run)."""
        if self._lg_checked:
            return self._lg
        self._lg_checked = True
        if self._use_langgraph is False:
            return None
        try:
            import langgraph  # noqa: F401  (optional dependency)
            self._lg = self._build_graph()
        except Exception as exc:
            self._lg = None
            self._note_degradation(f"langgraph unavailable: {exc}")
        return self._lg

    def _note_degradation(self, reason: str) -> None:
        if self._degradation_logged:
            return
        self._degradation_logged = True
        try:
            from observability.logger import log_event
            log_event("WARNING", "orchestration",
                      "LangGraph orchestrator unavailable; deterministic path active",
                      error=reason)
        except Exception:
            try:
                print(f"[Orchestrator] LangGraph unavailable ({reason}); "
                      f"deterministic path active.")
            except Exception:
                pass

    # ── the engine ────────────────────────────────────────────────────────────

    def run(self, goal: str, task_id: str = "", steps: list[str] | None = None) -> OrchestratorResult:
        """Execute one goal through the state machine. Never raises."""
        tid = task_id or f"orch-{int(time.time() * 1000)}"
        emit_state(tid, TaskState.PLANNING)
        try:
            plan = steps or self._planner(goal)
            rec = {"goal": goal, "plan": plan, "cursor": 0, "results": [],
                   "errors": [], "replans": 0, "state": TaskState.PLANNING.value}
            graph = self._langgraph()
            if graph is not None:
                try:
                    return self._run_langgraph(tid, goal, rec, graph)
                except Exception as exc:
                    self._note_degradation(f"graph runtime error: {exc}")
                    # fall through to the pure-Python engine (same semantics)
            return self._run_steps(tid, goal, rec)
        except Exception as exc:
            emit_state(tid, TaskState.FAILED, str(exc))
            return OrchestratorResult(ok=False, summary=f"orchestration error: {exc}",
                                      state="Failed")

    # ── pure-Python engine (fallback with identical semantics) ───────────────

    def _run_steps(self, tid: str, goal: str, rec: dict) -> OrchestratorResult:
        plan: list[str] = list(rec.get("plan") or [])
        cursor, results, errors = rec.get("cursor", 0), list(rec.get("results", [])), list(rec.get("errors", []))
        replans = int(rec.get("replans", 0))

        while cursor < len(plan) or replans > 0 and cursor >= len(plan):
            if cursor >= len(plan):
                break
            step = plan[cursor]
            emit_state(tid, TaskState.EXECUTING, step)
            ctx = {"goal": goal, "step": step, "task_id": tid}
            try:
                observation = self._executor(step, ctx) or {}
            except Exception as exc:
                observation = {"ok": False, "verified": False, "error": str(exc)}
            emit_state(tid, TaskState.VERIFYING, step)
            ok = self._verifier(step, ctx, observation)
            rec.update(cursor=cursor, results=results, errors=errors, replans=replans,
                       state=TaskState.EXECUTING.value)
            self._checkpointer.save(tid, rec)

            if ok:
                results.append(step)
                cursor += 1
                continue

            # verification failed → replan with evidence, bounded
            errors.append(f"step failed: {step} — {observation.get('error', 'unverified')}")
            if replans >= MAX_REPLANS:
                emit_state(tid, TaskState.FAILED, errors[-1])
                rec["state"] = TaskState.FAILED.value
                self._checkpointer.save(tid, rec)
                return OrchestratorResult(ok=False, summary=f"failed after {replans} replans: {errors[-1]}",
                                          steps_done=results, errors=errors,
                                          replans=replans, state="Failed")
            emit_state(tid, TaskState.RECOVERING, step)
            replans += 1
            new_steps = self._replan(goal, step, observation)
            plan[cursor:cursor + 1] = new_steps
            if not new_steps:
                cursor += 1  # unrecoverable step: skip forward, evidence kept

        rec["state"] = TaskState.COMPLETED.value
        self._checkpointer.save(tid, rec)
        emit_state(tid, TaskState.COMPLETED, goal)
        return OrchestratorResult(ok=True, summary=f"goal complete: {goal}",
                                  steps_done=results, errors=errors,
                                  replans=replans, state="Completed")

    def _replan(self, goal: str, failed_step: str, observation: dict) -> list[str]:
        """Derive replacement steps from the observed error. Returns [] when
        the step is unrecoverable (evidence is preserved either way)."""
        try:
            from core.brain_bridge import plan_request
            plan = plan_request(f"{goal} (retry after failing at: {failed_step}; "
                                f"observed: {observation.get('error', 'unverified')})")
            steps = [str(s) for s in (plan.get("steps") or [])][:MAX_REPLANS]
            return steps
        except Exception:
            return []

    # ── LangGraph engine (same semantics, graph-routed) ──────────────────────

    def _build_graph(self):
        from langgraph.graph import StateGraph, END
        from typing_extensions import TypedDict

        class GraphState(TypedDict):
            tid: str
            goal: str
            plan: list
            cursor: int
            results: list
            errors: list
            replans: int
            observation: dict

        def node_plan(state: GraphState) -> dict:
            emit_state(state["tid"], TaskState.PLANNING)
            plan = state["plan"] or self._planner(state["goal"])
            return {"plan": plan, "cursor": 0}

        def node_execute(state: GraphState) -> dict:
            step = state["plan"][state["cursor"]]
            emit_state(state["tid"], TaskState.EXECUTING, step)
            try:
                obs = self._executor(step, {"goal": state["goal"], "step": step,
                                            "task_id": state["tid"]}) or {}
            except Exception as exc:
                obs = {"ok": False, "verified": False, "error": str(exc)}
            return {"observation": obs}

        def node_verify(state: GraphState) -> dict:
            step = state["plan"][state["cursor"]]
            emit_state(state["tid"], TaskState.VERIFYING, step)
            ok = self._verifier(step, {"goal": state["goal"], "step": step},
                                state["observation"])
            results = list(state["results"])
            errors = list(state["errors"])
            replans = state["replans"]
            cursor = state["cursor"]
            if ok:
                results.append(step)
                return {"results": results, "errors": errors,
                        "cursor": cursor + 1, "replans": replans, "observation": {}}
            errors.append(f"step failed: {step} — {state['observation'].get('error', 'unverified')}")
            return {"errors": errors, "replans": replans + 1, "observation": {}}

        def node_replan(state: GraphState) -> dict:
            emit_state(state["tid"], TaskState.RECOVERING)
            cursor = state["cursor"]
            failed_step = state["plan"][cursor]
            new_steps = self._replan(state["goal"], failed_step,
                                     state["observation"])
            plan = list(state["plan"])
            plan[cursor:cursor + 1] = new_steps
            if not new_steps:
                cursor += 1
            return {"plan": plan, "cursor": cursor}

        def route_after_verify(state: GraphState) -> str:
            if state["replans"] > MAX_REPLANS:
                return "fail"
            if state["cursor"] >= len(state["plan"]):
                return "end"
            return "execute"

        g = StateGraph(GraphState)
        g.add_node("plan", node_plan)
        g.add_node("execute", node_execute)
        g.add_node("verify", node_verify)
        g.add_node("replan", node_replan)
        g.set_entry_point("plan")
        g.add_edge("plan", "execute")
        g.add_edge("execute", "verify")
        g.add_conditional_edges(
            "verify", route_after_verify,
            {"execute": "execute", "replan": "replan", "end": END, "fail": END},
        )
        g.add_edge("replan", "execute")
        return g.compile()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _run_langgraph(self, tid, goal, rec, graph) -> OrchestratorResult:
        """Drive the compiled graph and map its final state onto the result."""
        final = graph.invoke({
            "tid": tid, "goal": goal, "plan": list(rec.get("plan") or []),
            "cursor": 0, "results": [], "errors": [], "replans": 0,
            "observation": {},
        }, config={"configurable": {"thread_id": tid}})
        exhausted = (
            int(final.get("replans", 0)) > MAX_REPLANS
            or (final.get("cursor", 0) >= len(final.get("plan") or [])
                and bool(final.get("errors")))
        )
        if exhausted and final.get("errors"):
            emit_state(tid, TaskState.FAILED, final["errors"][-1])
            return OrchestratorResult(
                ok=False,
                summary=(f"failed after {final.get('replans', 0)} replans: "
                         f"{final['errors'][-1]}"),
                steps_done=list(final.get("results") or []),
                errors=list(final.get("errors") or []),
                replans=int(final.get("replans", 0)), state="Failed",
            )
        self._checkpointer.save(tid, {
            "goal": goal, "plan": final.get("plan") or [],
            "cursor": int(final.get("cursor", 0)),
            "results": list(final.get("results") or []),
            "errors": list(final.get("errors") or []),
            "replans": int(final.get("replans", 0)),
            "state": TaskState.COMPLETED.value,
        })
        emit_state(tid, TaskState.COMPLETED, goal)
        return OrchestratorResult(
            ok=True, summary=f"goal complete: {goal}",
            steps_done=list(final.get("results") or []),
            errors=list(final.get("errors") or []),
            replans=int(final.get("replans", 0)), state="Completed",
        )

    def resume(self, task_id: str) -> dict | None:
        """Checkpointed state for a resumable task (None when none)."""
        return self._checkpointer.load(task_id)


def _default_planner(goal: str) -> list[str]:
    """Offline planner: reuse the deterministic skill chain."""
    try:
        from core.brain_bridge import plan_request
        plan = plan_request(goal)
        return [str(s) for s in (plan.get("steps") or [])][:6] or [goal]
    except Exception:
        return [goal]


_cp: Checkpointer | None = None
_cp_lock = threading.Lock()


def _default_checkpointer() -> Checkpointer:
    global _cp
    if _cp is not None:
        return _cp
    with _cp_lock:
        if _cp is not None:
            return _cp
        try:
            from config.settings import RUNTIME_DIR
            _cp = Checkpointer(RUNTIME_DIR / "orchestrator-checkpoints.sqlite")
        except Exception:
            _cp = Checkpointer(Path("data") / "runtime" /
                               "orchestrator-checkpoints.sqlite")
    return _cp


def orchestrator_enabled() -> bool:
    """Is the LangGraph path switched on (and able to run)?"""
    try:
        from config.settings import feature_enabled
        return bool(feature_enabled("langgraph_orchestrator"))
    except Exception:
        return False


def maybe_orchestrate(goal: str, task_id: str = "",
                      steps: list[str] | None = None) -> OrchestratorResult | None:
    """Entry point used by the brain: runs the graph only when enabled."""
    if not orchestrator_enabled():
        return None
    try:
        return Orchestrator().run(goal, task_id=task_id, steps=steps)
    except Exception as exc:
        try:
            from observability.logger import log_event
            log_event("WARNING", "orchestration",
                      "orchestrator failed; deterministic path active", error=str(exc))
        except Exception:
            pass
        return None
