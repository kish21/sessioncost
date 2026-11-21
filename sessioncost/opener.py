"""Opening the report without holding up the command that made it.

Python's webbrowser can wait for the browser, and the browser it starts keeps the command's output open: an agent
waits for that output, so a report took minutes to come back in a Codex chat on a Mac (2026-11-18). Here the
system's own opener starts on its own, with no link to the command's input or output, and the command ends at once.
Inside Codex's sandbox nothing is opened: the sandbox may stop it, and the agent shows the path to click."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

SANDBOX_VARS = ("CODEX_SANDBOX",)      # set by Codex for the commands it runs in its sandbox


def in_sandbox() -> bool:
    return any(os.environ.get(v) for v in SANDBOX_VARS)


def _command(path: Path) -> list[str] | None:
    if sys.platform == "darwin":
        return ["open", str(path)]
    if sys.platform.startswith("linux"):
        return ["xdg-open", str(path)]
    return None


def open_report(path: Path):
    """Start the browser on the report and return at once (the opener's process, or None)."""
    sys.stdout.flush()                 # the path is out before anything can wait
    if in_sandbox():
        return None
    path = Path(path).resolve()
    cmd = _command(path)
    try:
        if cmd:
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        if hasattr(os, "startfile"):   # Windows: hands the file to its default program and returns
            os.startfile(str(path))    # noqa: S606 (our own report file)
            return None
    except OSError:
        return None                    # no opener here: the agent shows the path to click
    threading.Thread(target=webbrowser.open, args=(path.as_uri(),), daemon=True).start()
    return None
