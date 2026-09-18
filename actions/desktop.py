"""Delegation shim — implementation moved to computer/desktop_ops.py.

Kept so existing callers (the tool dispatcher's lazy imports, plugins) keep
working unchanged. The tool name and parameter contract are identical.
"""

from computer.desktop_ops import desktop_control  # noqa: F401

__all__ = ["desktop_control"]
