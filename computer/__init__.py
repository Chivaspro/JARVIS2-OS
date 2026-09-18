"""Mark LII computer-control layer (change: modernize-jarvis-architecture).

One facade over the real machine: screen observation, mouse, keyboard,
window/application management, desktop operations and browser automation.

Layout:
* ``computer_control.py`` — general GUI automation (moved from actions/)
* ``browser_control.py``  — browser automation (moved from actions/)
* ``windows.py``          — application launching (moved from actions/open_app.py)
* ``desktop_ops.py``      — desktop/taskbar operations (moved from actions/desktop.py)
* ``computer_use.py``     — backend-selection facade (Cua adapter + local default)
* ``local_backend.py``    — backend wrapper delegating to the moved modules
* ``verification.py``     — post-action verification (task 4.5)

The tools keep their entry points in ``actions/`` (delegation shims), so the
Gemini declarations, the permission gate and the deferred-import loader work
unchanged. Exactly one controller drives the desktop at a time.
"""

from computer.computer_use import (
    ComputerUse,
    get_computer_use,
    active_backend,
    computer_control_action,
    computer_status,
    computer_check,
)
from computer.verification import verify_action

__all__ = [
    "ComputerUse", "get_computer_use", "active_backend",
    "computer_control_action", "computer_status", "computer_check",
    "verify_action",
]
