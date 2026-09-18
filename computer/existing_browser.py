"""Reaching the browser the user already has open.

One owner for the question the Cua debugging prompt asks first (spec §10/§12):
*can Jarvis drive the browser session the user is already signed in to?*

Why this is not a detail. ``launch_persistent_context`` cannot attach to a
running Chromium — the profile is locked and Chrome ≥136 refuses the directory
to automation. A second launch therefore lands on a **different, signed-out
profile**, which is exactly how "Compose button not found" happens on Gmail:
the page the model is told about is not the page the automation is driving. The
old code fell back to ``~/.jarvis_profiles/<browser>`` silently, so the user saw
a browser open and then a mystery failure.

The resolution order implemented here mirrors what the user asked for::

    browser already running?
      ├── yes, remote-debugging endpoint reachable → ATTACH (their tabs, their logins)
      ├── yes, no endpoint                        → SECOND_PROFILE, reported, never silent
      └── no                                      → LAUNCH (their real profile)

Read-only: process enumeration plus one loopback HTTP probe. Nothing is launched,
nothing is killed, no profile is modified.
"""

from __future__ import annotations

import json
import os
import platform
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Chrome/Edge expose the DevTools endpoint on 9222 by default when started with
# `--remote-debugging-port`. This is the documented, standard port.
DEFAULT_DEBUG_PORT = 9222

# Process names per browser, lowercased. Windows/macOS/Linux spellings.
_PROCESS_NAMES = {
    "chrome": ("chrome.exe", "chrome", "google chrome", "chromium",
               "chromium-browser"),
    "edge": ("msedge.exe", "msedge", "microsoft edge"),
    "brave": ("brave.exe", "brave", "brave browser"),
    "vivaldi": ("vivaldi.exe", "vivaldi", "vivaldi-stable"),
    "opera": ("opera.exe", "opera"),
    "operagx": ("opera.exe", "opera"),
    "firefox": ("firefox.exe", "firefox"),
    "safari": ("safari",),
}

# How the caller can reach the browser it was asked about.
ATTACH = "attach"                  # drive the browser they already have open
LAUNCH = "launch"                  # nothing running; open their real profile
SECOND_PROFILE = "second_profile"  # running, unreachable: a second profile


def profile_dir(browser: str = "chrome") -> str | None:
    """The browser's real user-data directory, or ``None`` if it is not there."""
    home = Path.home()
    local = os.environ.get("LOCALAPPDATA") or ""
    roam = os.environ.get("APPDATA") or ""
    system = platform.system()

    if system == "Windows":
        table = {
            "chrome": [Path(local) / "Google" / "Chrome" / "User Data"],
            "edge": [Path(local) / "Microsoft" / "Edge" / "User Data"],
            "brave": [Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "vivaldi": [Path(local) / "Vivaldi" / "User Data"],
            "opera": [Path(roam) / "Opera Software" / "Opera Stable"],
            "operagx": [Path(roam) / "Opera Software" / "Opera GX Stable"],
            "firefox": [Path(roam) / "Mozilla" / "Firefox" / "Profiles"],
        }
    elif system == "Darwin":
        lib = home / "Library" / "Application Support"
        table = {
            "chrome": [lib / "Google" / "Chrome"],
            "edge": [lib / "Microsoft Edge"],
            "brave": [lib / "BraveSoftware" / "Brave-Browser"],
            "vivaldi": [lib / "Vivaldi"],
            "opera": [lib / "com.operasoftware.Opera"],
            "operagx": [lib / "com.operasoftware.OperaGX"],
            "firefox": [lib / "Firefox"],
        }
    else:
        cfg = home / ".config"
        table = {
            "chrome": [cfg / "google-chrome", cfg / "chromium"],
            "edge": [cfg / "microsoft-edge"],
            "brave": [cfg / "BraveSoftware" / "Brave-Browser"],
            "vivaldi": [cfg / "vivaldi"],
            "opera": [cfg / "opera"],
            "operagx": [cfg / "opera-gx"],
            "firefox": [home / ".mozilla" / "firefox"],
        }
    for candidate in table.get(browser, []):
        try:
            if str(candidate) and candidate.is_dir():
                return str(candidate)
        except Exception:
            continue
    return None


def running_pids(browser: str = "chrome") -> list[int]:
    """PIDs of the running processes for this browser (read-only)."""
    wanted = {n.lower() for n in _PROCESS_NAMES.get(browser, (browser,))}
    out: list[int] = []
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                name = str(proc.info.get("name") or "").lower()
            except Exception:
                continue
            if name in wanted:
                out.append(proc.pid)
    except Exception:
        return []
    return sorted(out)


def is_running(browser: str = "chrome") -> bool:
    return bool(running_pids(browser))


def _get_json(url: str, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return payload if isinstance(payload, dict) else {}
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    except Exception:
        return {}


def cdp_version(port: int = DEFAULT_DEBUG_PORT, timeout: float = 0.6) -> dict:
    """What is answering on the DevTools port (empty dict when nothing is)."""
    return _get_json(f"http://127.0.0.1:{int(port)}/json/version", timeout)


def debug_endpoint(port: int = DEFAULT_DEBUG_PORT, timeout: float = 0.6) -> str | None:
    """The DevTools HTTP endpoint, if something is listening on it.

    Loopback only, one short GET, no browser involvement. A reachable endpoint
    means an already-running browser can be attached to over CDP — the only way
    to drive the session the user is signed in to.
    """
    url = f"http://127.0.0.1:{int(port)}"
    return f"{url}/json/version" if cdp_version(port, timeout) else None


def resolve(browser: str = "chrome", port: int = DEFAULT_DEBUG_PORT) -> dict[str, Any]:
    """How to reach this browser, and why (see the module docstring)."""
    browser = (browser or "chrome").lower().strip()
    pids = running_pids(browser)
    running = bool(pids)
    cdp = cdp_version(port) if running else {}

    if running and cdp:
        how = ATTACH
        note = (f"attached to your running {browser} over the DevTools endpoint on "
                f"port {port}")
    elif running:
        how = SECOND_PROFILE
        note = (
            f"{browser} is already running, and a Chromium profile can be opened by "
            f"only one process at a time, so a second window cannot use your "
            f"signed-in session. Start {browser} with "
            f"`--remote-debugging-port={port}` to let Jarvis drive the browser you "
            f"already have open; otherwise it opens a separate profile and pages "
            f"that need a sign-in will not be logged in."
        )
    else:
        how = LAUNCH
        note = f"{browser} is not running; opening your real profile"

    return {
        "browser": browser,
        "running": running,
        "pids": pids,
        "profile_dir": profile_dir(browser),
        "cdp_port": int(port),
        "cdp_reachable": bool(cdp),
        "cdp_url": f"http://127.0.0.1:{int(port)}" if cdp else "",
        "cdp_browser": str(cdp.get("Browser") or ""),
        "how": how,
        "note": note,
    }


def existing_session() -> dict[str, Any]:
    """The 'is my logged-in browser usable?' facts, for diagnostics and the model.

    Preferred over ad-hoc process checks so the diagnostics report and the
    browser controller cannot disagree about the same machine.
    """
    out: dict[str, Any] = {
        "browser": "", "process": False, "windows": [],
        "existing_session": False, "profile_dir": None,
        "cdp_reachable": False, "cdp_port": DEFAULT_DEBUG_PORT, "attach_note": "",
    }
    for candidate in ("chrome", "edge", "brave", "vivaldi", "opera", "firefox"):
        info = resolve(candidate)
        if not info["running"]:
            continue
        # A browser counts as an existing session only when it is both running
        # *and* attachable. Reporting otherwise would repeat the mistake this
        # module exists to fix.
        out.update({
            "browser": candidate,
            "process": True,
            "profile_dir": info["profile_dir"],
            "cdp_reachable": info["cdp_reachable"],
            "cdp_port": info["cdp_port"],
            "existing_session": bool(info["cdp_reachable"]),
            "attach_note": info["note"],
        })
        break
    try:
        import pygetwindow as gw
        out["windows"] = [t for t in (gw.getAllTitles() or []) if t][:5]
    except Exception:
        pass
    return out


__all__ = [
    "DEFAULT_DEBUG_PORT", "ATTACH", "LAUNCH", "SECOND_PROFILE",
    "profile_dir", "running_pids", "is_running", "debug_endpoint",
    "cdp_version", "resolve", "existing_session",
]
