"""Memory promotion policy (task 6.2) — what deserves long-term storage.

Scopes (memory-policy spec):
* temporary  — this breath of the conversation; never stored
* session    — this conversation only; the session ledger already keeps it
* long_term  — durable user/project facts; promotable
* project    — durable facts tied to a project id; promotable with project_id
* task       — in-flight work; lives in task state, never long-term memory

The classifier is deterministic and offline: regex/rule based, no model call.
``promote()`` is the single gate between a candidate fact and the memory
backend; ephemeral chatter never passes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from memory.backend import get_memory_backend, MemoryBackend


class Scope(str, Enum):
    TEMPORARY = "temporary"
    SESSION = "session"
    LONG_TERM = "long_term"
    PROJECT = "project"
    TASK = "task"


@dataclass
class Candidate:
    content: str
    scope: Scope = Scope.TEMPORARY
    category: str = ""
    project_id: str = ""
    explicit: bool = False   # the user said "remember this"
    reason: str = ""


# Durable-fact markers. Anything matching these is long-term material.
_DURABLE_PATTERNS = (
    re.compile(r"\bmy (sister|brother|mother|father|wife|husband|daughter|son|"
               r"dog|cat|boss|colleague|friend)('?s?| is|)'s? name is\b", re.I),
    re.compile(r"\bi (prefer|like|love|hate|always|never|usually)\b", re.I),
    re.compile(r"\bmy (birthday|anniversary) is\b", re.I),
    re.compile(r"\bi (work|live|study)\b", re.I),
    re.compile(r"\bremember (this|that)\b", re.I),
    re.compile(r"\bdon'?t forget\b", re.I),
    re.compile(r"\bi use\b", re.I),
    re.compile(r"\bmy (name|email|phone|address) is\b", re.I),
    re.compile(r"\bthe (project|api|password policy|convention) uses\b", re.I),
    re.compile(r"\bi'?m (allergic|vegan|vegetarian)\b", re.I),
)

# Ephemeral markers — commands and transient state, never durable facts.
_EPHEMERAL_PATTERNS = (
    re.compile(r"^(open|close|launch|start|stop|kill|run|play|pause|skip)\b", re.I),
    re.compile(r"^(what|who|when|where|why|how|is|are|do|does|did|can|could|"
               r"will|would|should)\b", re.I),
    re.compile(r"^(now|later|today|tomorrow|then|next)\b", re.I),
    re.compile(r"\b(on the screen|right now|at the moment|currently)\b", re.I),
)

# Low-value chatter (mirrors BrainMemory's own rule).
_CHATTER = re.compile(
    r"^(ok|okay|thanks|thank you|yes|no|good|great|nice|perfect|understood|"
    r"yeah|sure|alright|fine|done|got it|hey|hi|hello|jarvis|cool|awesome)"
    r"[\s\.!]*$", re.I,
)


def classify_scope(content: str, *, explicit: bool = False,
                   project_id: str = "") -> Candidate:
    """One candidate fact classified into its scope."""
    text = (content or "").strip()
    if not text:
        return Candidate(content=text, reason="empty")
    if _CHATTER.match(text) and not explicit:
        return Candidate(text, reason="chatter")
    if any(p.search(text) for p in _EPHEMERAL_PATTERNS) and not explicit:
        return Candidate(text, scope=Scope.SESSION, reason="ephemeral command/question")
    if explicit or any(p.search(text) for p in _DURABLE_PATTERNS):
        scope = Scope.PROJECT if project_id else Scope.LONG_TERM
        return Candidate(text, scope=scope, explicit=explicit,
                         project_id=project_id, reason="durable fact")
    # Unclassified statements stay session-scoped: conservative by default —
    # the local store's own value classifier still gets a say at write time.
    return Candidate(text, scope=Scope.SESSION, reason="unclassified statement")


def promote(content: str, *, explicit: bool = False,
            project_id: str = "", backend: MemoryBackend | None = None) -> dict:
    """Gate one candidate through the policy; store when durable.

    Returns {stored: bool, scope: str, reason: str, backend: str}. Never
    raises: a broken backend or policy bug degrades to a no-op, not an error
    in the conversation path.
    """
    try:
        cand = classify_scope(content, explicit=explicit, project_id=project_id)
        if cand.scope not in (Scope.LONG_TERM, Scope.PROJECT):
            return {"stored": False, "scope": cand.scope.value,
                    "reason": cand.reason or "not durable"}
        be = backend or get_memory_backend()
        ok = be.write(content, category=cand.category,
                      project_id=cand.project_id, explicit=cand.explicit)
        return {"stored": bool(ok), "scope": cand.scope.value,
                "reason": cand.reason, "backend": getattr(be, "name", "local")}
    except Exception as exc:
        return {"stored": False, "scope": "unknown", "reason": f"policy error: {exc}"}


def recall(query: str, limit: int = 8, backend: MemoryBackend | None = None) -> list[dict]:
    """Backend-agnostic recall (falls back to an empty list, never raises)."""
    try:
        be = backend or get_memory_backend()
        return be.search(query, limit=limit)
    except Exception:
        return []
