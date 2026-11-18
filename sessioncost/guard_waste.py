"""Stop token waste before it happens: an agent about to read a whole big file (Read tool, or cat / type /
Get-Content without head or tail) is turned back once, with a smaller way to get what it needs. Reading the same
file again goes through, so an agent that really needs all of it is never stuck.

Runs when the guard is on (for every chat, or this chat by /liveguard) or token tips are on. It is a token tip, not
a guard block: nothing is asked of the user and the guard's counts are not touched.
Left out: command output and web pages (their size is known only after they ran: the report covers them), and
Cursor's file-read hook (it says nothing about a line range, and it also runs for files the user attaches)."""
from __future__ import annotations

import os
from pathlib import Path

from .guard_checks import _BUILD_SUFFIXES, _LOCK_NAMES, _cmd_read_paths
from .guard_store import _read, _safe_id, _write, fix_auto_on, guard_on, home, state_path

BIG_BYTES = 40_000        # about 10k tokens: the size the big-results check calls huge (fixes/big_results.py)
LOCK_BYTES = 12_000       # about 3k tokens: a lockfile or build bundle (fixes/big_results.py LOCKFILE_TOKENS)


def waste_path(sid: str) -> Path:
    return home() / f"{_safe_id(sid)}.waste.json"


def _on(sid: str) -> bool:
    cfg = _read(home() / "config.json", {})
    return guard_on(cfg) or fix_auto_on(cfg) or state_path(sid).exists()


def _big(raw: str, cwd: str) -> tuple[str, int] | None:
    """(full path, size in bytes) of a file too big to read whole, else None (a missing file is not big)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    p = Path(os.path.expanduser(raw))
    p = p if p.is_absolute() or not cwd else Path(cwd) / p
    try:
        size = p.stat().st_size if p.is_file() else None
    except OSError:
        size = None
    if size is None:
        return None
    t = str(p).replace("\\", "/").lower()
    lock = t.rsplit("/", 1)[-1] in _LOCK_NAMES or t.endswith(_BUILD_SUFFIXES) or "/dist/" in t
    return (str(p), size) if size >= (LOCK_BYTES if lock else BIG_BYTES) else None


def _whole_reads(a, cwd: str) -> list[tuple[str, int]]:
    if a.kind == "read" and not getattr(a, "ranged", False):
        found = [_big(raw, cwd) for raw in (a.target or "").splitlines()]
    elif a.kind == "command":
        found = [_big(raw, cwd) for raw in _cmd_read_paths(a.target or "")[1]]
    else:
        return []
    return [f for f in found if f]


def stop(inp: dict) -> dict | None:
    """The pre-step answer that turns back a whole read of a big file the first time in this chat, else None."""
    from .guard_decide import step_of
    sid, cwd = str(inp.get("session_id") or ""), str(inp.get("cwd") or "")
    if inp.get("_agent") == "cursor" and inp.get("tool_name") == "Read":
        return None
    a = step_of(str(inp.get("tool_name") or ""), inp.get("tool_input") or {}, str(inp.get("tool_use_id") or ""))
    if a.kind not in ("read", "command") or not _on(sid):
        return None
    big = _whole_reads(a, cwd)
    if big:                                   # the project expects this read: `big_read <file>` in .sessioncost-allow
        from . import risks
        big = [] if risks._allowed(a, "big_read", risks.read_allow(cwd), cwd) else big
    seen = _read(waste_path(sid), {}).get("files") or []
    new = [(p, n) for p, n in big if os.path.normcase(p) not in seen]
    if not new:
        return None
    _write(waste_path(sid), {"files": (seen + [os.path.normcase(p) for p, _ in new])[-200:]})
    names = ", ".join(f"`{os.path.basename(p)}` (~{max(1, round(n / 4 / 1000))}k tokens)" for p, n in new)
    reason = (f"SessionCost token tip: this reads all of {names}, and every later message sends it again. "
              "Search it first, or read only the lines you need (a line range, or head / tail). "
              "If you really need the whole file, read it again: it will go through.")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}
