from __future__ import annotations
import ctypes
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

def msg(text: str, title: str = "J.A.R.V.I.S") -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass

def main() -> None:
    # The v10 failure was caused by the Python interpreter used to launch JARVIS
    # not having PyQt6 installed. Install the project's declared dependencies
    # once, silently, then start the GUI with the same interpreter.
    try:
        __import__("PyQt6")
    except Exception:
        req = BASE / "requirements.txt"
        try:
            if not req.exists():
                raise FileNotFoundError(req)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req)],
                cwd=str(BASE),
                check=True,
                creationflags=flags,
            )
            __import__("PyQt6")
        except Exception as exc:
            log = BASE / "jarvis_dependency_error.log"
            try:
                import traceback
                log.write_text(traceback.format_exc(), encoding="utf-8")
            except Exception:
                pass
            msg(
                "J.A.R.V.I.S could not start because PyQt6/dependencies are not installed.\n\n"
                f"Python: {sys.executable}\n"
                f"Error: {exc}\n\n"
                "Run install_dependencies.bat once, then launch JARVIS again.\n\n"
                f"Dependency log: {log}",
                "J.A.R.V.I.S — Startup Error",
            )
            return

    try:
        from main import main as run_main
        run_main()
    except Exception as exc:
        log = BASE / "jarvis_startup_error.log"
        try:
            import traceback
            log.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        msg(
            "J.A.R.V.I.S failed to start.\n\n"
            f"{exc}\n\n"
            f"A detailed log was written to:\n{log}",
            "J.A.R.V.I.S — Startup Error",
        )

if __name__ == "__main__":
    main()
