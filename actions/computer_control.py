"""Delegation shim — implementation moved to computer/computer_control.py.

Kept so existing callers (the tool dispatcher's lazy imports, plugins) keep
working unchanged. The tool name and parameter contract are identical.
"""

from computer.computer_control import computer_control  # noqa: F401

__all__ = ["computer_control"]
