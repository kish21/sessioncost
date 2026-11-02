"""Finding Claude Code, Codex, Copilot CLI, Antigravity and Cursor sessions on this machine.

Copilot CLI keeps one folder per session under $COPILOT_HOME/session-state (COPILOT_HOME defaults to ~/.copilot):
<id>/events.jsonl, and workspace.yaml whose `cwd:` is the project. Copilot Chat in VS Code keeps one file per chat in
<VS Code user folder>/workspaceStorage/<hash>/chatSessions; workspace.json in <hash> names the project folder.

Codex keeps every thread under $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl (CODEX_HOME defaults to
~/.codex), and moves an archived one to $CODEX_HOME/archived_sessions; both are searched, at any depth, so a new
folder layout does not hide them. The project is the `cwd` (or one of the `runtime_workspace_roots`) in the
file's `session_meta` row. Helper threads (a parent thread id, or a `subagent` source) are read with their main
session, never listed on their own.

Antigravity keeps one SQLite file per conversation under ~/.gemini/antigravity/conversations (older builds:
~/.gemini/antigravity-ide/conversations; or $ANTIGRAVITY_HOME/conversations); the project is the workspace stored
inside it.

Cursor keeps every chat in one SQLite file (see readers/cursor.py for where); a chat stands in for a session file
as a `CursorChat`. Helper chats are read with the chat that started them, never listed on their own. Which folder
a chat belongs to comes from any of three places, because Cursor has moved it before: the chat's own
`workspaceIdentifier` (older builds), its transcript under ~/.cursor/projects/<folder key>/agent-transcripts
(newer builds, which leave `workspaceIdentifier` out), or the per-folder store in workspaceStorage that lists the
chats open in that folder.

A session whose folder cannot be found at all is kept apart (`unlinked`); `sessioncost last` falls back to the
newest of those, with a note, rather than finding nothing.

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
    """The id a user types: a Claude Code file is named by it; a Codex file ends with it; a Copilot CLI session is
    the folder its events.jsonl sits in."""
    if path.name == "events.jsonl":
        return path.parent.name
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


def codex_meta(path: Path, rows: int = 5) -> dict:
    """The `session_meta` payload of a Codex file (the first row today; a few rows are read in case that changes)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for _ in range(rows):
                line = f.readline()
                if not line:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
                    return row["payload"]
    except OSError:
        pass
    return {}


def codex_is_helper(meta: dict) -> bool:
    src = meta.get("source")
    return bool(meta.get("parent_thread_id")) or (isinstance(src, dict) and "subagent" in src)


def codex_folders(meta: dict) -> list[str]:
    roots = meta.get("runtime_workspace_roots")
    return [x for x in [meta.get("cwd")] + (roots if isinstance(roots, list) else []) if isinstance(x, str) and x]


def codex_files(home: Path | None = None) -> list[Path]:
    """Every Codex thread file, active and archived."""
    root = home or codex_home()
    out: list[Path] = []
    for sub in ("sessions", "archived_sessions"):
        if (root / sub).is_dir():
            out += (root / sub).rglob("rollout-*.jsonl")
    return out


def codex_sessions(project: str | Path | None = None, home: Path | None = None,
                   unlinked: list | None = None) -> list[Path]:
    """Main Codex threads whose working folder is the project. Threads that name no folder go to `unlinked`."""
    want = project or os.getcwd()
    out = []
    for p in codex_files(home):
        meta = codex_meta(p)
        if not meta or codex_is_helper(meta):
            continue
        folders = codex_folders(meta)
        if any(_same_dir(f, want) for f in folders):
            out.append(p)
        elif not folders and unlinked is not None:
            unlinked.append(p)
    return out


def copilot_sessions(project: str | Path | None = None, home: Path | None = None,
                     unlinked: list | None = None) -> list[Path]:
    """Copilot CLI sessions whose folder (workspace.yaml `cwd`) is the project. Sessions that name none go to
    `unlinked`."""
    from .readers.copilot import copilot_home, is_copilot, workspace_cwd
    root = (home or copilot_home()) / "session-state"
    want = project or os.getcwd()
    out = []
    for f in sorted(root.glob("*/events.jsonl")) if root.is_dir() else []:
        if not is_copilot(f):
            continue
        cwd = workspace_cwd(f.parent)
        if cwd and _same_dir(cwd, want):
            out.append(f)
        elif not cwd and unlinked is not None:
            unlinked.append(f)
    return out


def vscode_chats(project: str | Path | None = None, homes: list[Path] | None = None) -> list[tuple[datetime, Path]]:
    """VS Code Copilot Chat sessions whose workspace folder is the project, with the time each file last changed (the
    edits carry no timestamp of their own). Chats with no message in them are left out."""
    from .readers.vscode_chat import folder_of, replay, vscode_homes
    want = project or os.getcwd()
    out = []
    for home in homes if homes is not None else vscode_homes():
        for d in sorted((home / "workspaceStorage").glob("*/chatSessions")) if (home / "workspaceStorage").is_dir() else []:
            files = sorted(d.glob("*.json")) + sorted(d.glob("*.jsonl"))
            if not files or not _same_dir(folder_of(files[0]) or "\0", want):
                continue
            for f in files:
                try:
                    if not any(isinstance(q, dict) and (q.get("message") or {}).get("text")
                               for q in replay(f).get("requests") or []):
                        continue
                except (OSError, ValueError, AttributeError):
                    continue
                out.append((datetime.fromtimestamp(f.stat().st_mtime).astimezone(), f))
    return out


def antigravity_home() -> Path:
    from .readers.antigravity import antigravity_home as home
    return home()


def antigravity_sessions(project: str | Path | None = None,
                         home: Path | None = None) -> list[tuple[datetime | None, Path]]:
    """Antigravity conversations whose workspace is the project, with the time of their last step. Both the current
    folder and the older one are read; a conversation in both counts once (the newer copy). Helper conversations
    (nothing typed by you) are read with the conversation that started them, never listed on their own."""
    from .readers.antigravity import _ts, antigravity_homes, connect, is_helper, workspace
    want = project or os.getcwd()
    best: dict[str, tuple[datetime | None, Path]] = {}
    for base in [home] if home else antigravity_homes():
        root = base / "conversations"
        if not root.is_dir():
            continue
        for p in root.glob("*.db"):
            try:
                con = connect(p)
                try:
                    ws = workspace(con)
                    if not ws or not _same_dir(ws, want) or is_helper(con):
                        continue
                    row = con.execute("select metadata from steps order by idx desc limit 1").fetchone()
                finally:
                    con.close()
            except Exception:   # a locked or half-written file must not break the list
                continue
            ts = _ts(row[0], 1) if row else None
            old = best.get(p.stem)
            if old is None or (ts and (old[0] is None or ts > old[0])):
                best[p.stem] = (ts, p)
    return list(best.values())


def cursor_home() -> Path:
    return Path.home() / ".cursor"


def loose_key(path: str | Path) -> str:
    """Cursor's folder key: every run of characters other than letters and digits becomes one '-'
    (c:\\Users\\x\\my_proj -> c-Users-x-my-proj). Compared without case."""
    try:
        path = Path(path).expanduser().resolve()
    except OSError:
        pass
    return re.sub(r"[^A-Za-z0-9]+", "-", str(path)).strip("-").lower()


def _file_uri_path(uri: str) -> str:
    """file:///c%3A/Users/x -> c:/Users/x"""
    from urllib.parse import unquote, urlparse
    u = urlparse(uri)
    if u.scheme != "file":
        return ""
    p = unquote(u.path)
    return p[1:] if re.match(r"^/[A-Za-z]:", p) else p


def cursor_links(db: Path, home: Path | None = None) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Where Cursor says each chat belongs, outside the chat itself: ({chat id: folder keys} from the transcript
    folders, {chat id: folders} from the per-folder stores next to the shared database)."""
    keys: dict[str, set[str]] = {}
    root = (home or cursor_home()) / "projects"
    if root.is_dir():
        for t in root.glob("*/agent-transcripts/*"):
            keys.setdefault(t.name.split(".")[0], set()).add(t.parent.parent.name.lower())
    folders: dict[str, set[str]] = {}
    store = Path(db).parent.parent / "workspaceStorage"
    if store.is_dir():
        import sqlite3
        for d in store.iterdir():
            try:
                folder = _file_uri_path(json.loads((d / "workspace.json").read_text(encoding="utf-8")).get("folder") or "")
                if not folder or not (d / "state.vscdb").is_file():
                    continue
                con = sqlite3.connect(f"file:{(d / 'state.vscdb').as_posix()}?mode=ro", uri=True)
                try:
                    row = con.execute("select value from ItemTable where key = 'composer.composerData'").fetchone()
                finally:
                    con.close()
                v = json.loads(row[0]) if row and row[0] else {}
            except Exception:   # a store Cursor holds open or wrote half of must not break the lookup
                continue
            ids = set(v.get("selectedComposerIds") or []) | set(v.get("lastFocusedComposerIds") or [])
            ids |= {c.get("composerId") for c in v.get("allComposers") or [] if isinstance(c, dict)}
            for cid in ids:
                if isinstance(cid, str):
                    folders.setdefault(cid, set()).add(folder)
    return keys, folders


def cursor_sessions(project: str | Path | None = None, db: Path | None = None, home: Path | None = None,
                    unlinked: list | None = None) -> list[tuple[datetime | None, object]]:
    """Cursor chats (not helper chats) whose folder is the project, with their last update time. Chats whose
    folder cannot be found anywhere go to `unlinked`."""
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
    keys, folders = cursor_links(Path(db), home)
    want_key = loose_key(want)
    out = []
    for d in found:
        cid = str(d["composerId"])
        if cid in helpers or not (d.get("fullConversationHeadersOnly") or d.get("conversation")):
            continue
        ms = d.get("lastUpdatedAt") or d.get("createdAt")
        when = datetime.fromtimestamp(ms / 1000).astimezone() if isinstance(ms, (int, float)) else None
        chat = (when, CursorChat(Path(db), cid))
        ws = workspace(d)
        if ws:
            linked = _same_dir(ws, want)
        elif cid in keys or cid in folders:
            linked = want_key in keys.get(cid, ()) or any(_same_dir(f, want) for f in folders.get(cid, ()))
        else:
            if unlinked is not None:
                unlinked.append(chat)
            continue
        if linked:
            out.append(chat)
    return out


def sessions(project: str | Path | None = None, home: Path | None = None, codex: Path | None = None,
             antigravity: Path | None = None, cursor: Path | None = None, cursor_dir: Path | None = None,
             unlinked: list | None = None, copilot: Path | None = None,
             vscode: list[Path] | None = None) -> list[tuple[datetime, Path]]:
    """Main sessions of a project (Claude Code, Codex, Copilot CLI, VS Code Copilot Chat, Antigravity, Cursor), newest
    first. Sessions that name no folder at all are added to `unlinked` (newest first), when a list is given."""
    d = project_dir(project, home)
    found = [(last_stamp(p), p) for p in d.glob("*.jsonl")] if d is not None else []
    codex_loose: list[Path] = []
    found += [(last_stamp(p, types=None), p) for p in codex_sessions(project, codex, codex_loose)]
    found += [(last_stamp(p, types=None), p) for p in copilot_sessions(project, copilot, codex_loose)]
    found += vscode_chats(project, vscode)
    loose = [(last_stamp(p, types=None), p) for p in codex_loose]
    found += antigravity_sessions(project, antigravity)
    found += cursor_sessions(project, cursor, cursor_dir, loose)
    out = [(t, p) for t, p in found if t is not None]
    out.sort(key=lambda x: x[0], reverse=True)
    if unlinked is not None:
        unlinked += sorted([(t, p) for t, p in loose if t is not None], key=lambda x: x[0], reverse=True)
    return out
