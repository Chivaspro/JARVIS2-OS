"""Permission gate — moved verbatim from main.py (task 3.1/3.2).

Twenty Settings switches used to be saved but never enforced until
main.py::_permission_gate made each tool (and each action of multi-purpose
tools) checked against its own switch before any work happens, with refusals
that name the switch. This module is that gate, unmodified in behavior, so
the security service and future subsystems share one implementation.
"""

from __future__ import annotations

from typing import Callable

_MOUSE_ACTIONS = {"type", "smart_type", "click", "left_click", "double_click",
                 "right_click", "move", "drag", "hotkey", "press", "scroll",
                 "copy", "paste", "clear_field", "screen_click"}
_WINDOW_ACTIONS = {"focus_window", "open_app", "close_app"}
_SCREEN_ACTIONS = {"screenshot", "screen_find"}
_TERMINAL_ACTIONS = {"run", "build", "install", "execute", "terminal"}

_PERMISSION_LABEL = {
    "computer_control": "Computer control",
    "mouse_keyboard_control": "Mouse and keyboard control",
    "window_control": "Window control",
    "screen_understanding": "Screen capture and visual understanding",
    "desktop_awareness": "Desktop awareness",
    "desktop_automation": "Desktop automation",
    "terminal_control": "Terminal and command execution",
    "file_control": "File control",
    "camera_access": "Camera access",
    "mic_access": "Microphone access",
    "web_task": "Web task automation",
}


def label_for(feature: str) -> str:
    """Human label for a permission switch (falls back to the key)."""
    return _PERMISSION_LABEL.get(feature, feature)


def permission_refusal(
    enabled: Callable[[str], bool],
    name: str,
    args: dict | None,
) -> str | None:
    """Refusal sentence when a revoked Settings permission covers this tool.

    `enabled` is ui.feature_enabled (name -> bool). Returns None when the call
    may proceed. Never raises: a broken feature lookup must not block tools.
    """
    def off(feat: str) -> bool:
        try:
            return not bool(enabled(feat))
        except Exception:
            return False

    def refusal(feat: str) -> str:
        return (f"{_PERMISSION_LABEL.get(feat, feat)} is switched off in Settings, "
                f"so I have not done that.")

    act = str((args or {}).get("action", "") or "").strip().lower()

    if name == "computer_control":
        if off("computer_control"):
            return refusal("computer_control")
        if act in _MOUSE_ACTIONS and off("mouse_keyboard_control"):
            return refusal("mouse_keyboard_control")
        if act in _WINDOW_ACTIONS and off("window_control"):
            return refusal("window_control")
        if act in _SCREEN_ACTIONS and off("screen_understanding"):
            return refusal("screen_understanding")
        return None

    if name == "desktop_control":
        if off("computer_control"):
            return refusal("computer_control")
        if off("desktop_automation"):
            return refusal("desktop_automation")
        return None

    if name == "system_status":
        return refusal("desktop_awareness") if off("desktop_awareness") else None

    if name in ("file_controller", "file_processor"):
        return refusal("file_control") if off("file_control") else None

    if name in ("code_helper", "dev_agent"):
        # Only the actions that actually execute something are gated; writing
        # and explaining code is not terminal access.
        if act in _TERMINAL_ACTIONS and off("terminal_control"):
            return refusal("terminal_control")
        return None

    if name == "screen_process":
        return refusal("screen_understanding") if off("screen_understanding") else None

    if name in ("vision_look", "recognize_faces", "what_am_i_holding",
                "detect_objects", "detect_gesture"):
        return refusal("camera_access") if off("camera_access") else None

    return None
