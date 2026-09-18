"""Guarded bridge between the running app (ui.py / main.py) and the Brain.

Every function here degrades gracefully to a safe no-op when the Brain cannot
be built (missing deps, locked sqlite file, model import failure) — importing
this module can never crash the assistant. Lazy: nothing is loaded until the
first call, and heavy optional packages (sentence-transformers) are skipped via
JARVIS_NO_EMBEDDINGS or an import failure.
"""

from __future__ import annotations

import json
import threading
import time

# NB: `json` is required — feed_wake_audio() read the config with json.loads and
# the missing import was swallowed by its blanket except, so the wake feed
# always reported "disabled" instead of raising.

_brain = None
_main_lock = threading.Lock()
_boot_try: float | None = None


def get_brain():
    """Singleton Brain (thread-safe, lazy). Returns None if it fails once."""
    global _brain, _boot_try
    if _brain is not None:
        return _brain
    with _main_lock:
        if _brain is not None:
            return _brain
        # Don't hammer a failing boot on every call-site; retry once a minute max.
        now = time.time()
        if _boot_try is not None and _boot_try is not None and (now - _boot_try) < 60:
            return None
        _boot_try = now
        try:
            from brain import get_brain as _get_brain

            _brain = _get_brain()
        except Exception as exc:
            print(f"[Brain] ⚠️ brain unavailable: {exc}")
            _brain = None
        return _brain


def current_features() -> dict:
    """Live feature flags from config/api_keys.json (never raises).

    Skill availability is derived from these, so revoking microphone or camera
    access in Settings disables the matching skill immediately instead of the
    model cheerfully offering it."""
    try:
        from pathlib import Path
        cfg = _read_json(Path(__file__).resolve().parents[1]
                         / "config" / "api_keys.json")
        feats = cfg.get("features", {})
        return feats if isinstance(feats, dict) else {}
    except Exception:
        return {}


def _read_json(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_skill_block_cache: tuple[float, str] | None = None


def skill_block() -> str:
    """The skill map injected into the system instruction.

    Cached for a minute: it only changes when the user toggles permissions, and
    a session rebuild should not re-parse the config for every connect."""
    global _skill_block_cache
    now = time.time()
    if _skill_block_cache is not None and (now - _skill_block_cache[0]) < 60.0:
        return _skill_block_cache[1]
    text = ""
    try:
        from brain import skills as _skills
        text = _skills.prompt_block(features=current_features())
    except Exception:
        text = ""
    _skill_block_cache = (now, text)
    return text


def task_block() -> str:
    """Live multi-step task state (empty string when nothing is in flight)."""
    try:
        from brain.tasks import get_task_tracker
        return get_task_tracker().context_block()
    except Exception:
        return ""


def handle_skill_query(args: dict | None = None) -> str:
    """Backend for the skill_query tool (what can you do / which skill is this)."""
    try:
        from brain import skills as _skills
        return _skills.handle_query(args)
    except Exception as exc:
        return f"Skill lookup unavailable: {exc}"


def handle_task_command(args: dict | None = None) -> str:
    """Backend for the task_plan tool (multi-step task memory)."""
    try:
        from brain.tasks import handle_task_command as _handle
        return _handle(args)
    except Exception as exc:
        return f"Task memory unavailable: {exc}"


def augment_system_prompt(parts: list) -> list:
    """Append the Brain's context block (personality + honesty hint), the live
    skill map and any in-flight task state to a list of prompt parts.

    Returns the same list; every step is individually guarded so a broken
    subsystem degrades to a slightly smaller prompt, never to a failed connect."""
    try:
        b = get_brain()
        if b is not None:
            block = b.build_context_block()
            if block:
                parts.append(block)
    except Exception:
        pass
    try:
        sblock = skill_block()
        if sblock:
            parts.append(sblock)
    except Exception:
        pass
    try:
        tblock = task_block()
        if tblock:
            parts.append(tblock)
    except Exception:
        pass
    return parts


def observe_utterance(text: str) -> None:
    """Learn from a user's finished utterance (personality + activity).

    Also routes the text through the skill registry and remembers the result on
    the Brain, so the selected skills are deterministic, inspectable and shared
    with the skill_query tool instead of living only inside the model. The
    utterance is appended to the conversation ledger too, which is what makes
    "this / that / the other one" resolvable on the next turn.
    """
    if not text:
        return
    try:
        b = get_brain()
        if b is None:
            return
        try:
            b.observe(text)
        except Exception:
            pass
        try:
            b.note_turn("user", text)
        except Exception:
            pass
        try:
            b.route_skills(text, features=current_features(), limit=4)
        except Exception:
            pass
        # Feedback commands ("remember...", "forget...") handled via tools;
        # the learning engine is a no-op for ordinary chat so no side effect.
    except Exception:
        pass


def record_reply(text: str) -> None:
    """Append JARVIS's own finished reply to the conversation ledger.

    The reply matters: it names the file/website/object that was just handled,
    and it is what "the same thing again" refers back to.
    """
    if not text:
        return
    try:
        b = get_brain()
        if b is None:
            return
        b.note_turn("assistant", text)
    except Exception:
        pass


def resolve_reference(text: str) -> dict:
    """Resolve deictic references against the conversation ledger.

    Returns `{"references": [...], "notes": [...], "summary": str}`; empty
    fields mean nothing concrete was recognised (the caller must not guess).
    """
    try:
        b = get_brain()
        if b is None:
            return {"references": [], "notes": [], "summary": ""}
        return b.resolve(text)
    except Exception:
        return {"references": [], "notes": [], "summary": ""}


def note_entity(kind: str, label: str, **kw) -> None:
    """Record a file/url/object/app the assistant is now working with."""
    try:
        b = get_brain()
        if b is not None:
            b.note_entity(kind, label, **kw)
    except Exception:
        pass


def note_objects(objects) -> None:
    """Record a fresh detection scan (labels + left/right position)."""
    try:
        b = get_brain()
        if b is not None:
            b.note_objects(objects)
    except Exception:
        pass


def plan_request(text: str) -> dict:
    """Deterministic skill plan for a request (advisory, offline)."""
    try:
        b = get_brain()
        if b is not None:
            return b.plan(text, features=current_features())
    except Exception:
        pass
    return {"skills": [], "chain": [], "steps": []}


def learn_tool_outcome(name: str, ok: bool, detail: str = "") -> None:
    """Feed tool success/failure into the learning engine (preferred tools)."""
    try:
        b = get_brain()
        if b is None:
            return
        method = str(getattr(b, "_last_routed_method", "")) or name
        request = detail or name
        ms = 0.0
        if ok:
            b.learning.record_success(request=request, intent=f"tool_{name}",
                                      tool=method, ms=ms, steps=None)
        else:
            b.learning.record_failure(request=request, intent=f"tool_{name}",
                                      tool=method, ms=ms, error=request, fallback=None)
    except Exception:
        pass


def _wake_config() -> dict:
    """Live wake configuration, read from the file Settings writes.

    Settings stores the master switch at ``features.wake_word`` and the phrases
    at ``wake_words``. The old lookup only checked a top-level ``wake_enabled``
    key that Settings never wrote, so the detector was never built: the "Wake
    word" switch, the extra wake words and the sensitivity all did nothing.
    """
    from pathlib import Path
    cfg = _read_json(Path(__file__).resolve().parents[1] / "config" / "api_keys.json")
    feats = cfg.get("features") if isinstance(cfg.get("features"), dict) else {}
    return {
        "wake_words": cfg.get("wake_words") or [],
        "wake_sensitivity": cfg.get("wake_sensitivity", 0.5),
        # Only a real keyword model counts as a wake word. The energy-based
        # speech fallback is opt-in, because it cannot tell "Jarvis" from any
        # loud sentence and would report a wake on ordinary speech.
        "wake_fallback": str(cfg.get("wake_fallback", "off") or "off"),
        "features": feats,
    }


def wake_enabled() -> bool:
    """Is the local wake detector allowed to run right now?

    Honours the Settings switches: the wake word must be on, the detector is a
    background listener by definition, and push-to-talk turns hands-free
    listening off by design.
    """
    try:
        cfg = _wake_config()
        feats = cfg.get("features") or {}
        if "wake_word" not in feats:
            # Legacy config with no Settings-written flag.
            return bool(_read_json(Path(__file__).resolve().parents[1]
                                   / "config" / "api_keys.json").get("wake_enabled", False))
        if not bool(feats.get("wake_word")):
            return False
        if not bool(feats.get("background_listening", True)):
            return False
        if bool(feats.get("push_to_talk", False)) and not bool(feats.get("hands_free", True)):
            return False
        return True
    except Exception:
        return False


def feed_wake_audio(samples, *, force: bool | None = None) -> bool:
    """Non-invasive wake-word feed. Takes optional pre-computed PCM bytes; when
    `force` is True the detector pumps even if app-level mute states its owner
    normally gates on. Returns True when a wake word fired (barge-in request).
    No-op (False) when wake is disabled/unavailable."""
    try:
        if force is None and not wake_enabled():
            return False
        from core.wake import get_wake_manager

        mgr = get_wake_manager()
        try:
            # Install the live reader BEFORE ensure() builds the detector, so
            # the detector is created with the configured words/sensitivity.
            mgr.set_config_provider(_wake_config)
        except Exception:
            pass
        det = mgr.ensure()
        if det is None:
            return False
        if not det.active():
            mgr.start()
        return bool(det.feed(samples))
    except Exception:
        return False


def reconfigure_wake(*, words: list[str] | None = None,
                     sensitivity: float | None = None) -> bool:
    """Apply new wake words/sensitivity from Settings without a restart.
    Safe to call any time: no-op when wake is disabled or the detector is
    still being built. Returns True when a live detector was updated."""
    try:
        from core.wake import get_wake_manager
        mgr = get_wake_manager()
        try:
            mgr.set_config_provider(_wake_config)
        except Exception:
            pass
        mgr.reconfigure(words=words, sensitivity=sensitivity)
        return mgr._detector is not None
    except Exception:
        return False


def set_wake_barge_in(fn) -> None:
    """Hook an interrupt function onto the shared wake detector so a detected
    wake word stops JARVIS mid-speech (spec §23)."""
    try:
        from core.wake import get_wake_manager

        mgr = get_wake_manager()
        det = mgr.ensure()
        if det is not None:
            det.set_interrupt(fn)
    except Exception:
        pass


__all__ = [
    "get_brain",
    "wake_enabled",
    "augment_system_prompt",
    "observe_utterance",
    "record_reply",
    "resolve_reference",
    "note_entity",
    "note_objects",
    "plan_request",
    "learn_tool_outcome",
    "feed_wake_audio",
    "set_wake_barge_in",
    "reconfigure_wake",
    "current_features",
    "skill_block",
    "task_block",
    "handle_skill_query",
    "handle_task_command",
]