"""Antigravity conversations: ~/.gemini/antigravity-ide/conversations/<conversation id>.db, one SQLite file each.

What the file holds (checked on the 2026 builds; protobuf without a published schema, field numbers below):
- `steps`, in order. Each row has a type, a status and a `metadata` blob: 1 = created (seconds, nanos), 8 = done,
  4 = the tool call (1 id, 2 name, 3 arguments as JSON with Antigravity's own `toolSummary`), 9 = model usage.
  Type 14 = something you typed (payload 19.2 = the text). Type 15 = one model reply, with its usage in metadata 9:
  1 model number, 2 input not from the cache, 3 output (= 9 thinking + 10 reply), 4 written to the cache,
  5 read from the cache, 11 response id; payload 20.1 = the reply, 20.3 = its thinking summary.
  Tool steps (view_file, run_command, ...) follow the reply that asked for them.
  The browser helper (type 85) keeps its own steps nested in its payload (6.2), usage included.
- `gen_metadata`: the same usage once more, with the model name (1.19) for each model number (1.4.1); used only to
  name models. Per-call totals from `steps` equal those of `gen_metadata` (checked on 71 conversations).
- `trajectory_metadata_blob`: 1.1 = the workspace folder (a file:// URI).
The system prompt and tool definitions are not stored, so the setup is unknown. Antigravity does not bill per token.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..model import Action, Call, Event, Helper, Session, Turn, Usage
from . import _pb
from .claude import _turn_at, short
from .codex import _command, _name

USER, REPLY, BROWSER = 14, 15, 85


def antigravity_home() -> Path:
    base = os.environ.get("ANTIGRAVITY_HOME")
    return Path(base).expanduser() if base else Path.home() / ".gemini" / "antigravity-ide"


def _ts(meta: bytes | None, field: int) -> datetime | None:
    sec = _pb.num(meta, field, 1)
    if not sec:
        return None
    return datetime.fromtimestamp(sec + (_pb.num(meta, field, 2) or 0) / 1e9, tz=timezone.utc)


def _usage(u: bytes) -> Usage:
    think, reply = _pb.num(u, 9), _pb.num(u, 10)
    return Usage(fresh_input=_pb.num(u, 2) or 0, cache_write_5m=_pb.num(u, 4) or 0, cache_read=_pb.num(u, 5) or 0,
                 output=_pb.num(u, 3) or 0,
                 thinking=(think or 0) if think is not None or reply is not None else None)


def _rest(payload: bytes | None) -> bytes:
    """A step's own content: the payload without its type, status and copy of the metadata."""
    f = _pb.fields(payload)
    return b"".join(v for k, vs in sorted(f.items()) if k not in (1, 4, 5) for v in vs if isinstance(v, bytes))


def describe(name: str, a: dict) -> tuple[str, str, str, int]:
    """(kind, plain-words label, target, written chars) for one Antigravity tool call."""
    summary = short(a.get("toolSummary") or "", 70)
    if name == "view_file":
        f = str(a.get("AbsolutePath") or "")
        lines = f" (lines {a['StartLine']}-{a['EndLine']})" if a.get("StartLine") and a.get("EndLine") else ""
        return "read", f"read `{_name(f)}`{lines}", f, 0
    if name == "list_dir":
        f = str(a.get("DirectoryPath") or "")
        return "search", f"listed the folder `{_name(f)}`", f, 0
    if name == "grep_search":
        q = str(a.get("Query") or "")
        return "search", f"searched for `{short(q, 50)}`", q, 0
    if name == "run_command":
        kind, label, target = _command(name, str(a.get("CommandLine") or ""))
        return kind, label, target, 0
    if name == "write_to_file":
        f = str(a.get("TargetFile") or "")
        return "write", f"wrote `{_name(f)}`", f, len(str(a.get("CodeContent") or ""))
    if name in ("replace_file_content", "multi_replace_file_content"):
        f = str(a.get("TargetFile") or "")
        chunks = a.get("ReplacementChunks") if isinstance(a.get("ReplacementChunks"), list) else []
        written = len(str(a.get("ReplacementContent") or "")) + sum(
            len(str(c.get("ReplacementContent") or "")) for c in chunks if isinstance(c, dict))
        return "edit", f"edited `{_name(f)}`", f, written
    if name == "manage_task":
        return "command", "checked on a running command" if a.get("Action") == "status" else \
            f"managed a running command ({a.get('Action') or '?'})", str(a.get("TaskId") or ""), 0
    if "DurationSeconds" in a:
        return "command", f"waited up to {a.get('DurationSeconds')} s for a running command", "", 0
    if name == "search_web":
        return "web", f"searched the web: {short(a.get('query'), 60)}", str(a.get("query") or ""), 0
    if name == "read_url_content":
        return "web", f"opened a web page: {short(a.get('Url'), 60)}", str(a.get("Url") or ""), 0
    if name == "browser_subagent":
        return "helper", f"started a browser helper: {short(a.get('TaskName') or a.get('Task'), 60)}", "", 0
    if name == "ask_question":
        n = len(a.get("questions") or []) or 1
        return "question", f"asked you {n} question{'s' if n > 1 else ''}", "", 0
    if name == "generate_image":
        return "other", "generated an image", "", 0
    return "other", summary or f"used {name}", "", 0


class _Thread:
    """Calls and tool steps of one thread (the conversation, or one browser helper run)."""

    def __init__(self, agent: str, models: dict[int, str]):
        self.agent = agent
        self.models = models
        self.order: list[Call] = []
        self.last_ts: datetime | None = None

    def step(self, typ: int, meta: bytes | None, payload: bytes | None, error: bool) -> Call | None:
        """Read one step. Returns the call when the step is a model reply with usage."""
        created, done = _ts(meta, 1), _ts(meta, 8)
        u = _pb.get(meta, 9)
        if typ == REPLY and isinstance(u, bytes):
            n = len(self.order) + 1
            enum = _pb.num(u, 1)
            c = Call(n=n, msg_id=_pb.text(u, 11) or f"{self.agent}#{n}", agent=self.agent,
                     model=self.models.get(enum or -1) or (f"antigravity-model-{enum}" if enum else ""),
                     usage=_usage(u), ts_first=created, ts_last=done or created)
            reply = _pb.get(payload, 20)
            txt = _pb.text(reply, 1)
            c.text_chars = len(txt)
            c.first_text = short(txt, 120) if txt.strip() else ""
            c.thinking_chars = len(_pb.text(reply, 3))
            self.order.append(c)
            self.last_ts = done or created or self.last_ts
            return c
        call = _pb.get(meta, 4)
        if isinstance(call, bytes) and self.order:
            name = _pb.text(call, 2) or _pb.text(call, 9) or "?"
            raw = _pb.text(call, 3)
            try:
                args = json.loads(raw) if raw.strip().startswith("{") else {}
            except ValueError:
                args = {}
            kind, label, target, written = describe(name, args if isinstance(args, dict) else {})
            rest = _rest(payload)
            self.order[-1].actions.append(Action(
                id=_pb.text(call, 1), tool=name, kind=kind, label=label, target=target, args_chars=len(raw),
                written_chars=written, result_chars=max(0, len(rest) - len(raw)),
                result_hash=hashlib.sha1(rest).hexdigest()[:16], is_error=error, ts=created, result_ts=done))
            self.last_ts = done or created or self.last_ts
        return None


def _attachments(payload: bytes | None) -> str:
    """Words for a message sent without text (a voice note or an image)."""
    kinds = [_pb.text(m, 1) for m in _pb.all_(payload, 19, 9)]
    if any(k.startswith("audio") for k in kinds):
        return "(a voice message)"
    if kinds:
        return "(an image)" if len(kinds) == 1 else f"({len(kinds)} attachments)"
    return "(no text)"


def _models(con: sqlite3.Connection) -> dict[int, str]:
    out: dict[int, str] = {}
    try:
        for (data,) in con.execute("select data from gen_metadata"):
            enum, name = _pb.num(data, 1, 4, 1) or _pb.num(data, 1, 3), _pb.text(data, 1, 19)
            if enum and name and enum not in out:
                out[enum] = name
    except sqlite3.Error:
        pass
    return out


def workspace(con: sqlite3.Connection) -> str:
    try:
        row = con.execute("select data from trajectory_metadata_blob").fetchone()
    except sqlite3.Error:
        return ""
    uri = _pb.text(row[0], 1, 1) if row else ""
    if not uri.startswith("file:"):
        return uri
    p = unquote(urlparse(uri).path)
    return p[1:] if len(p) > 2 and p[0] == "/" and p[2] == ":" else p


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


class AntigravityReader:
    name = "antigravity"

    def can_read(self, path: Path) -> bool:
        if path.suffix != ".db":
            return False
        try:
            con = connect(path)
            try:
                names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
            finally:
                con.close()
        except sqlite3.Error:
            return False
        return {"steps", "gen_metadata"} <= names

    def read(self, path: Path) -> Session:
        con = connect(path)
        try:
            models = _models(con)
            rows = con.execute("select idx, step_type, status, metadata, error_details, step_payload from steps "
                               "order by idx").fetchall()
            cwd = workspace(con)
        finally:
            con.close()
        s = Session(agent=self.name, session_id=path.stem, source=str(path), cwd=cwd, billed_per_token=False)
        th = _Thread("main", models)
        turns: list[Turn] = []

        def current() -> Turn:
            if not turns:
                turns.append(Turn(n=0, kind="start", text="(before your first message)"))
            return turns[-1]

        for idx, typ, status, meta, err, payload in rows:
            if typ == USER:
                txt = _pb.text(payload, 19, 2).strip() or _attachments(payload)
                when = _ts(meta, 1)
                waited = (when - th.last_ts).total_seconds() if when and th.last_ts and when > th.last_ts else 0.0
                t = Turn(n=len([x for x in turns if x.n > 0]) + 1, kind="typed", text=short(txt, 300) or "(empty)",
                         ts=when, waited_s=waited if turns else 0.0)
                t.events.append(Event("typed", "you typed", short(txt, 300), len(txt), when))
                turns.append(t)
                continue
            c = th.step(typ, meta, payload, bool(err))
            if c is not None:
                c.turn = current().n
                current().calls.append(c)
            if typ == BROWSER:
                h = self._browser(payload, meta, models, len(s.helpers) + 1)
                if h is not None:
                    s.helpers.append(h)
        s.turns = [t for t in turns if t.calls or t.n > 0]
        s.title = next((t.text for t in s.turns if t.n > 0), "(no message from you)")
        stamps = [x for c in th.order for x in (c.ts_first, c.ts_last) if x] + [t.ts for t in s.turns if t.ts]
        s.started, s.ended = (min(stamps), max(stamps)) if stamps else (None, None)
        counts: dict[str, int] = {}
        for c in th.order:
            counts[c.model] = counts.get(c.model, 0) + 1
        s.model = max(counts, key=counts.get) if counts else ""
        for h in s.helpers:
            h.turn = _turn_at(s.turns, h.calls[0].ts_first if h.calls else None)
            for c in h.calls:
                c.turn = _turn_at(s.turns, c.ts_first) if c.ts_first else h.turn   # a helper can run across turns
        s.notes.append("Antigravity does not store its system prompt or tool definitions, so the fixed setup is "
                       "not measured.")
        return s

    def _browser(self, payload: bytes | None, meta: bytes | None, models: dict[int, str], k: int) -> Helper | None:
        th = _Thread(f"helper:browser{k}", models)
        for st in _pb.all_(payload, 6, 2):
            th.step(_pb.num(st, 1) or 0, _pb.get(st, 5), st, False)
        if not th.order:
            return None
        call = _pb.get(meta, 4)
        try:
            args = json.loads(_pb.text(call, 3) or "{}")
        except ValueError:
            args = {}
        desc = (args.get("TaskName") or args.get("Task") or "browser helper") if isinstance(args, dict) else ""
        return Helper(id=f"browser{k}", description=short(desc, 90), agent_type="browser helper", calls=th.order)
