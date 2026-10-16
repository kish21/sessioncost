"""Readers: one per coding agent, each turning that agent's log into a `Session` (see model.py).

To add an agent: write a module with a class that has `name`,
`can_read(path) -> bool` and `read(path) -> Session`, and add it to READERS. Nothing else changes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .. import risks
from ..model import Session
from . import antigravity, claude, codex, cursor


class Reader(Protocol):
    name: str

    def can_read(self, path: Path) -> bool: ...

    def read(self, path: Path) -> Session: ...


READERS: list[Reader] = [claude.ClaudeCodeReader(), codex.CodexReader(), antigravity.AntigravityReader()]


class NotSupported(Exception):
    pass


# A command that only prints files is a read: agents often read through the shell (Codex always, Claude Code on
# Windows with Get-Content), and the checks for re-reads and one-read-per-call would miss it as a "command".
# Every part of the command must be one of these, joined by ; && or new lines; anything else keeps it a command.
_PATHS = r"((?:'[^']+'|\"[^\"]+\"|[^\s;&|,'\"]+)(?:\s*,\s*(?:'[^']+'|\"[^\"]+\"|[^\s;&|,'\"]+))*)"
_TRIM = r"(?:\s*\|\s*select-object(?:\s+-(?:first|last|skip)\s+\d+)+)?"
_PRINT = [re.compile(x, re.I) for x in (
    r"(?:get-content|gc|cat|type)\s+(?:-path\s+)?" + _PATHS + r"(?:\s+-(?:totalcount|head|tail|first|last|raw)(?:\s+\d+)?)*" + _TRIM,
    r"(?:head|tail)\s+(?:-n\s*\d+|-\d+)\s+" + _PATHS,
    r"sed\s+-n\s+['\"]?\d+(?:,\d+)?p['\"]?\s+" + _PATHS,
    r"nl\s+-ba\s+" + _PATHS + r"(?:\s*\|\s*sed\s+-n\s+['\"]?\d+(?:,\d+)?p['\"]?)?",
)]


def _printed_files(cmd: str) -> list[str]:
    """The files a command only prints, or [] when it does anything else."""
    parts = [x.strip() for x in re.split(r"\s*(?:;|&&|\n)\s*", cmd.strip()) if x.strip()]
    files: list[str] = []
    for part in parts:
        m = next((m for p in _PRINT if (m := p.fullmatch(part))), None)
        if not m:
            return []
        files += [f.strip().strip("'\"") for f in m.group(1).split(",")]
    return files


def _command_reads(s: Session) -> None:
    for c in s.all_calls:
        for a in c.actions:
            if a.kind == "command" and a.target and (files := _printed_files(a.target)):
                names = [re.split(r"[\\/]", f)[-1] for f in files]
                a.kind, a.target = "read", "\n".join(files)
                a.label = f"read `{names[0]}`" if len(names) == 1 else \
                    f"read {len(names)} files: " + ", ".join(f"`{n}`" for n in names[:3]) + (", …" if len(names) > 3 else "")


def read(path) -> Session:
    s = _read(path)
    _command_reads(s)
    risks.mark_commit_check_skips(s)
    return s


def _read(path) -> Session:
    if isinstance(path, cursor.CursorChat):     # Cursor keeps every chat in one database
        return cursor.CursorReader().read(path)
    p = Path(path).expanduser()
    if not p.is_file():
        raise NotSupported(f"no such file: {p}")
    for r in READERS:
        if r.can_read(p):
            return r.read(p)
    raise NotSupported(f"{p.name}: not a session log this version can read (Claude Code and Codex .jsonl, "
                       "Antigravity .db; Cursor chats are opened by id)")
