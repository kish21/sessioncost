"""Finding Claude Code, Codex, Antigravity and Cursor sessions on this machine.

Codex keeps every thread under $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl (CODEX_HOME defaults to
~/.codex); the project is the `cwd` in each file's first row. Helper threads (they carry a parent thread id) are
read with their main session, never listed on their own.

Antigravity keeps one SQLite file per conversation under ~/.gemini/antigravity-ide/conversations (or
$ANTIGRAVITY_HOME/conversations); the project is the workspace stored inside it.

Cursor keeps every chat in one SQLite file (see readers/cursor.py for where); a chat stands in for a session file
as a `CursorChat`. Helper chats are read with the chat that started them, never listed on their own.

Claude Code keeps one folder per project under ~/.claude/projects (or $CLAUDE_CONFIG_DIR/projects). The folder name
is the project path with every character other than a letter or digit replaced by '-', e.g. c:\\Users\\x\\my_proj
-> c--Users-x-my-proj. Matching is case-insensitive (Windows drive letters come in both cases).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path


def claude_home() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base).expanduser() if base else Path.home() / ".claude"


def codex_home() -> Path:
    base = os.environ.get("CODEX_HOME")
    return Path(base).expanduser() if base else Path.home() / ".codex"


def session_id(path: Path) -> str:
    """The id a user types: a Claude Code file is named by it; a Codex file ends with it."""
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", path.stem)
    return m.group(1) if m and path.stem.startswith("rollout-") else path.stem   # (a CursorChat's stem is its id)


def short_id(path: Path) -> str:
    return session_id(path)[:8]


def project_key(path: str | Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).expanduser().resolve()))


def project_dir(project: str | Path | None = None, home: Path | None = None) -> Path | None:
    root = (home or claude_home()) / "projects"
    if not root.is_dir():
        return None
    want = project_key(project or os.getcwd()).lower()
    for d in root.iterdir():
        if d.is_dir() and d.name.lower() == want:
            return d
    return None


def last_stamp(path: Path, tail_bytes: int = 262144, types=("user", "assistant")) -> datetime | None:
    """The timestamp of the newest row that has one. Reading only the end of the file keeps this fast.
    (Newest by file time can pick an old chat that was merely reopened; its last row stays old.)"""
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(max(0, size - tail_bytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        if '"timestamp"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if types and row.get("type") not in types:
            continue
        try:
            return datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
    return None


def _same_dir(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).expanduser().resolve())) ==             os.path.normcase(str(Path(b).expanduser().resolve()))
    except OSError:
        return False


def codex_sessions(project: str | Path | None = None, home: Path | None = None) -> list[Path]:
    """Main Codex threads whose working folder is the project."""
    root = (home or codex_home()) / "sessions"
    if not root.is_dir():
        return []
    want = project or os.getcwd()
    out = []
    for p in root.glob("*/*/*/rollout-*.jsonl"):
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                row = json.loads(f.readline())
        except (OSError, ValueError):
            continue
        meta = row.get("payload") if isinstance(row, dict) and row.get("type") == "session_meta" else None
        if isinstance(meta, dict) and not meta.get("parent_thread_id") and meta.get("cwd") and                 _same_dir(meta["cwd"], want):
            out.append(p)
    return out


def antigravity_home() -> Path:
    base = os.environ.get("ANTIGRAVITY_HOME")
    return Path(base).expanduser() if base else Path.home() / ".gemini" / "antigravity-ide"


def antigravity_sessions(project: str | Path | None = None,
                         home: Path | None = None) -> list[tuple[datetime | None, Path]]:
    """Antigravity conversations whose workspace is the project, with the time of their last step."""
    from .readers import _pb
    from .readers.antigravity import _ts, connect, workspace
    root = (home or antigravity_home()) / "conversations"
    if not root.is_dir():
        return []
    want = project or os.getcwd()
    out = []
    for p in root.glob("*.db"):
        try:
            con = connect(p)
            try:
                ws = workspace(con)
                if not ws or not _same_dir(ws, want):
                    continue
                row = con.execute("select metadata from steps order by idx desc limit 1").fetchone()
            finally:
                con.close()
        except Exception:   # a locked or half-written file must not break the list
            continue
        out.append((_ts(row[0], 1) if row else None, p))
    return out


def cursor_sessions(project: str | Path | None = None, db: Path | None = None) -> list[tuple[datetime | None, object]]:
    """Cursor chats (not helper chats) whose workspace is the project, with their last update time."""
    from .readers.cursor import CursorChat, chats, connect, helper_ids, state_db, workspace
    db = db or state_db()
    if not Path(db).is_file():
        return []
    want = project or os.getcwd()
    try:
        con = connect(db)
        try:
            found = chats(con)
        finally:
            con.close()
    except Exception:   # Cursor may hold the file; the other agents must still be listed
        return []
    helpers = helper_ids(found)
    out = []
    for d in found:
        ws = workspace(d)
        if d["composerId"] in helpers or not ws or not _same_dir(ws, want) or                 not (d.get("fullConversationHeadersOnly") or d.get("conversation")):
            continue
        ms = d.get("lastUpdatedAt") or d.get("createdAt")
        when = datetime.fromtimestamp(ms / 1000).astimezone() if isinstance(ms, (int, float)) else None
        out.append((when, CursorChat(Path(db), str(d["composerId"]))))
    return out


def sessions(project: str | Path | None = None, home: Path | None = None, codex: Path | None = None,
             antigravity: Path | None = None, cursor: Path | None = None) -> list[tuple[datetime, Path]]:
    """Main sessions of a project (Claude Code, Codex, Antigravity, Cursor), newest first."""
    d = project_dir(project, home)
    found = [(last_stamp(p), p) for p in d.glob("*.jsonl")] if d is not None else []
    found += [(last_stamp(p, types=None), p) for p in codex_sessions(project, codex)]
    found += antigravity_sessions(project, antigravity)
    found += cursor_sessions(project, cursor)
    out = [(t, p) for t, p in found if t is not None]
    out.sort(key=lambda x: x[0], reverse=True)
    return out
