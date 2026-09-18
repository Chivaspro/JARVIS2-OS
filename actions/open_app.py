"""Delegation shim — implementation moved to computer/windows.py.

Kept so existing callers (the tool dispatcher's lazy imports, plugins) keep
working unchanged. The tool name and parameter contract are identical.
"""

from computer.windows import open_app  # noqa: F401

__all__ = ["open_app"]
