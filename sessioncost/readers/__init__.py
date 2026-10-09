"""Readers: one per coding agent, each turning that agent's log into a `Session` (see model.py).

To add an agent: write a module with a class that has `name`,
`can_read(path) -> bool` and `read(path) -> Session`, and add it to READERS. Nothing else changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..model import Session
from . import antigravity, claude, codex, cursor


class Reader(Protocol):
    name: str

    def can_read(self, path: Path) -> bool: ...

    def read(self, path: Path) -> Session: ...


READERS: list[Reader] = [claude.ClaudeCodeReader(), codex.CodexReader(), antigravity.AntigravityReader()]


class NotSupported(Exception):
    pass


def read(path) -> Session:
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
