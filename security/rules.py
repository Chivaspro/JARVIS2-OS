"""Risk-tier rule table — deterministic classification (security spec).

The tier is derived from the action type and parameters by this rule table,
never by model judgment alone. Unlisted actions classify as LOW (read-only
behavior is the safe default) unless their parameters carry a bulk flag.
"""

from __future__ import annotations

import enum
from typing import Any


class RiskTier(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Tool-name → tier for the whole-call classification.
_TOOL_TIERS: dict[str, RiskTier] = {
    # LOW: observation and lookup
    "screen_process": RiskTier.LOW,
    "close_camera": RiskTier.LOW,
    "web_search": RiskTier.LOW,
    "weather_report": RiskTier.LOW,
    "system_status": RiskTier.LOW,
    "recall_memory": RiskTier.LOW,
    "skill_query": RiskTier.LOW,
    "task_plan": RiskTier.LOW,
    "diagnostics": RiskTier.LOW,
    "performance_scan": RiskTier.LOW,
    "jarvis_window": RiskTier.LOW,
    "brain_summary": RiskTier.LOW,
    # MEDIUM: reversible state changes
    "open_app": RiskTier.MEDIUM,
    "file_controller": RiskTier.MEDIUM,
    "code_helper": RiskTier.MEDIUM,
    "dev_agent": RiskTier.MEDIUM,
    "reminder": RiskTier.MEDIUM,
    "desktop_control": RiskTier.MEDIUM,
    "computer_control": RiskTier.MEDIUM,
    "browser_control": RiskTier.MEDIUM,
    "computer_settings": RiskTier.MEDIUM,
    "youtube_video": RiskTier.MEDIUM,
    "play_music": RiskTier.LOW,
    # HIGH: outward-facing or destructive
    "send_message": RiskTier.HIGH,
    "coding_agent": RiskTier.HIGH,
}

# Within computer_settings / computer_control, the action decides the tier.
_SETTINGS_MEDIUM = {
    "volume_up", "volume_down", "volume_set", "mute", "brightness_up",
    "brightness_down", "sleep_display", "pause_video", "close_app",
    "close_window", "full_screen", "minimize", "maximize", "snap_left",
    "snap_right", "switch_window", "show_desktop", "task_manager",
    "focus_search", "refresh_page", "close_tab", "new_tab", "next_tab",
    "prev_tab", "go_back", "go_forward", "zoom_in", "zoom_out", "zoom_reset",
    "find_on_page", "scroll_up", "scroll_down", "scroll_top", "scroll_bottom",
    "page_up", "page_down", "copy", "paste", "cut", "undo", "redo",
    "select_all", "save", "enter", "escape", "press_key", "type_text",
    "screenshot", "lock_screen", "open_settings", "file_explorer", "open_run",
    "dark_mode", "restart", "shutdown", "toggle_wifi",
}

_FILE_MEDIUM = {
    "create_file", "create_folder", "move", "copy", "rename", "write",
    "organize_desktop",
}

_DELETE_ACTIONS = {"delete", "bulk_delete", "remove"}

_SEND_PLATFORMS = {"whatsapp", "telegram", "email", "gmail", "outlook", "sms", "imessage"}


def classify(action: str, params: dict | None = None) -> RiskTier:
    """Deterministic tier for one action. Never raises."""
    try:
        return _classify(action or "", dict(params or {}))
    except Exception:
        return RiskTier.LOW


def _classify(action: str, p: dict) -> RiskTier:
    a = str(action or "").strip().lower()

    # Sub-action refinement for the multi-action tools.
    sub = str(p.get("action", "") or "").strip().lower()

    if a in ("file_controller", "desktop_control"):
        if sub in _DELETE_ACTIONS:
            count = _param_int(p.get("count")) or _param_int(p.get("confirm_count"))
            return RiskTier.HIGH if (count is None or count > 5) else RiskTier.MEDIUM
        if sub in _FILE_MEDIUM:
            return RiskTier.MEDIUM
        return RiskTier.LOW  # list/read/find/info/stats

    if a in ("computer_settings", "computer_control"):
        if sub in ("shutdown", "restart", "toggle_wifi"):
            return RiskTier.HIGH
        return RiskTier.MEDIUM if sub in _SETTINGS_MEDIUM else RiskTier.LOW

    if a == "send_message":
        platform = str(p.get("platform", "") or "").strip().lower()
        if platform in _SEND_PLATFORMS or not platform:
            return RiskTier.HIGH
        return RiskTier.MEDIUM

    if a in ("code_helper", "dev_agent"):
        if sub in ("run", "build", "install", "execute", "terminal"):
            return RiskTier.MEDIUM
        return RiskTier.MEDIUM

    tier = _TOOL_TIERS.get(a)
    if tier is not None:
        return tier

    # Unknown actions with dangerous verbs in the name stay cautious.
    if any(v in a for v in ("delete", "format", "wipe", "purchase", "pay")):
        return RiskTier.HIGH
    if any(v in a for v in ("install", "uninstall", "write", "set_", "toggle")):
        return RiskTier.MEDIUM
    return RiskTier.LOW


def _param_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
