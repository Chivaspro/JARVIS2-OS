"""Delegation shim — implementation moved to computer/browser_control.py.

Kept so existing callers (the tool dispatcher, actions/flight_finder, plugins)
keep working unchanged. The tool name and parameter contract are identical.
"""

from computer.browser_control import browser_control  # noqa: F401

__all__ = ["browser_control"]
