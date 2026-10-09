"""Cursor chats: one SQLite file for all of them, <Cursor user dir>/globalStorage/state.vscdb, table cursorDiskKV
(Windows: %APPDATA%/Cursor/User; macOS: ~/Library/Application Support/Cursor/User; Linux: ~/.config/Cursor/User;
or set CURSOR_STATE_DB to the file).

What it holds (checked on the 2026 builds):
- `composerData:<chat id>`: the chat: name, workspace folder, model, the ordered list of its messages
  (`fullConversationHeadersOnly`), helper chats (`subagentComposerIds`), the setup split with Cursor's own token
  estimates (`promptTokenBreakdown`: system prompt, tool definitions, rules, skills, MCP) and the context size of the
  last call (`contextTokensUsed`);
- `bubbleId:<chat id>:<message id>`: one message. type 1 = you, type 2 = the agent: text, thinking, or one tool call
  (`toolFormerData`: name, params, result, `modelCallId` shared by every tool asked for in one model reply).

Cursor does not keep token counts on this machine (every `tokenCount` is 0), so tokens are ESTIMATED: each call is
rebuilt from the messages, its size from the text it carried (characters / 4), scaled so the last call matches
the context size Cursor recorded. Exact numbers come from Cursor's usage export (a CSV from the dashboard), which
`apply_usage_csv` spreads over the turns it covers.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..model import Action, Call, Event, Helper, Session, Setup, SetupPart, Turn, Usage
from .claude import short, ts
from .codex import _command, _name

USER, AGENT = 1, 2
SETUP_IDS = {"system_prompt", "tools", "rules", "skills", "mcp", "subagents"}
CACHE_GAP_S = 300          # a call within 5 minutes of the previous one is assumed to re-read it from the cache


def state_db() -> Path:
    if os.environ.get("CURSOR_STATE_DB"):
        return Path(os.environ["CURSOR_STATE_DB"]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"


@dataclass(frozen=True)
class CursorChat:
    """One Cursor chat inside the shared database; stands in for a session file."""
    db: Path
    id: str

    @property
    def stem(self) -> str:
        return self.id

    @property
    def name(self) -> str:
        return f"cursor-{self.id}"

    def __str__(self) -> str:
        return f"{self.db}#{self.id}"


def connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)


def _value(con: sqlite3.Connection, key: str) -> dict:
    row = con.execute("select value from cursorDiskKV where key = ?", (key,)).fetchone()
    try:
        v = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def chats(con: sqlite3.Connection) -> list[dict]:
    out = []
    for (v,) in con.execute("select value from cursorDiskKV where key like 'composerData:%'"):
        try:
            d = json.loads(v) if v else None
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("composerId"):
            out.append(d)
    return out


def helper_ids(all_chats: list[dict]) -> set[str]:
    return {x for d in all_chats for k in ("subagentComposerIds", "subComposerIds") for x in d.get(k) or []}


def workspace(d: dict) -> str:
    uri = (d.get("workspaceIdentifier") or {}).get("uri") or {}
    return str(uri.get("fsPath") or "") if isinstance(uri, dict) else ""


def _ms(x) -> datetime | None:
    return datetime.fromtimestamp(x / 1000, tz=timezone.utc) if isinstance(x, (int, float)) and x > 0 else None


def _params(t: dict) -> dict:
    for k in ("params", "rawArgs"):
        try:
            v = json.loads(t.get(k) or "{}")
        except ValueError:
            continue
        if isinstance(v, dict) and v:
            return v
    return {}


def describe(name: str, p: dict) -> tuple[str, str, str]:
    """(kind, plain-words label, target) for one Cursor tool call."""
    f = str(p.get("targetFile") or p.get("relativeWorkspacePath") or p.get("path") or p.get("filePath") or "")
    base = re.sub(r"_v\d+$", "", name)
    if base in ("read_file",):
        return "read", f"read `{_name(f)}`", f
    if base in ("edit_file", "search_replace", "apply_patch", "write"):
        return "edit", f"edited `{_name(f)}`", f
    if base == "delete_file":
        return "edit", f"deleted `{_name(f)}`", f
    if base == "run_terminal_command" or base == "run_terminal_cmd":
        return _command(name, str(p.get("command") or ""))
    if base in ("ripgrep_raw_search", "grep_search", "codebase_search", "grep"):
        q = str(p.get("pattern") or p.get("query") or "")
        return "search", f"searched for `{short(q, 50)}`", q
    if base in ("glob_file_search", "list_dir", "file_search"):
        q = str(p.get("globPattern") or p.get("query") or p.get("targetDirectory") or "")
        return "search", f"looked for files `{short(_name(q) or q, 50)}`", q
    if base == "web_search":
        q = str(p.get("searchTerm") or p.get("query") or "")
        return "web", f"searched the web: {short(q, 60)}", q
    if base == "web_fetch":
        u = str(p.get("url") or "")
        return "web", f"opened a web page: {short(u, 60)}", u
    if base == "task":
        d = str(p.get("description") or "")
        return "helper", f"started a helper: {short(d, 60)}", d
    if base == "ask_question":
        return "question", "asked you a question", ""
    if base in ("todo_write", "update_current_step"):
        return "plan", "updated its plan", ""
    if base == "await":
        return "command", "waited for a running command", ""
    if base == "get_mcp_tools":
        return "search", "looked up tools to load", ""
    if name.startswith("mcp"):
        return "other", f"used {name.rsplit('-', 1)[-1]}", ""
    return "other", f"used {name}", ""


def _reply_id(t: dict) -> str:
    """The model reply a tool call came from: ids look like 'call-<request>-<n>' + newline + 'fc_<reply>_<index>'."""
    mid = str(t.get("modelCallId") or t.get("toolCallId") or "")
    return mid.splitlines()[-1].rsplit("_", 1)[0] if mid else ""


class _Builder:
    """Rebuilds model calls from Cursor messages, with sizes in characters (turned into tokens afterwards)."""

    def __init__(self, agent: str, model: str):
        self.agent = agent
        self.model = model
        self.calls: list[Call] = []
        self.sizes: list[dict] = []          # per call: chars that came in before it, chars it wrote
        self.history = 0                     # characters of conversation so far
        self.cur: Call | None = None
        self.cur_group = None
        self.last_was_tool = False

    def _new(self, when) -> Call:
        n = len(self.calls) + 1
        c = Call(n=n, msg_id=f"{self.agent}#{n}", agent=self.agent, model=self.model, usage=Usage(),
                 ts_first=when, ts_last=when)
        self.calls.append(c)
        self.sizes.append({"in": self.history, "out": 0})
        self.cur, self.last_was_tool = c, False
        return c

    def user(self, text: str) -> None:
        self.history += len(text)
        self.cur, self.cur_group, self.last_was_tool = None, None, False

    def agent_msg(self, b: dict, when) -> Call:
        t = b.get("toolFormerData") if isinstance(b.get("toolFormerData"), dict) else None
        group = _reply_id(t) if t else None
        # A reply is its thinking and text, then the tools it asked for (all with one reply id). Anything after
        # those tools, or a tool from another reply, is the next model call.
        if self.cur is None or (self.last_was_tool and (t is None or group != self.cur_group)) or \
                (t is not None and self.cur_group is not None and group and group != self.cur_group):
            self._new(when)
            self.cur_group = None
        c = self.cur
        c.ts_last = when or c.ts_last
        think = b.get("thinking")
        if isinstance(think, str):
            try:
                think = json.loads(think)
            except ValueError:
                think = {"text": think}
        think_text = str(think.get("text") or "") if isinstance(think, dict) else ""
        text = str(b.get("text") or "")
        c.thinking_chars += len(think_text)
        c.text_chars += len(text)
        if text.strip() and not c.first_text:
            c.first_text = short(text, 120)
        out = len(text) + len(think_text)
        if t is not None:
            name = str(t.get("name") or "?")
            p = _params(t)
            kind, label, target = describe(name, p)
            raw = str(t.get("params") or t.get("rawArgs") or "")
            result = str(t.get("result") or "")
            written = sum(len(str(p.get(k) or "")) for k in ("streamingContent", "contents", "code_edit", "newString"))
            c.actions.append(Action(id=str(t.get("toolCallId") or ""), tool=name, kind=kind, label=label,
                                    target=target, args_chars=len(raw), written_chars=written,
                                    result_chars=len(result), is_error=t.get("status") == "error", ts=when))
            out += len(raw)
            self.history += len(result)
            self.cur_group = group or self.cur_group
            self.last_was_tool = True
        self.sizes[-1]["out"] += out
        self.history += out
        return c


def _estimate(calls: list[Call], sizes: list[dict], setup_tokens: int, last_context: int | None,
              summary_tokens: int = 0, recent_tokens: int = 0) -> tuple[float, int | None]:
    """Fill each call's usage from character sizes. Returns (scale used for the conversation, the call where Cursor
    summarised the history, if it did).

    No summary: the conversation part is scaled up so the last call matches the context size Cursor recorded (file
    reads keep only a short note here, so text alone undercounts). With a summary: Cursor recorded how big the summary
    is and how much came after it, so the summary goes where the remaining messages add up to that recent part."""
    if not calls:
        return 1.0, None
    conv = [s["in"] / 4 for s in sizes]
    scale, cut = 1.0, None
    if summary_tokens and recent_tokens and last_context and setup_tokens + conv[-1] > last_context:
        cut = next((i for i, cv in enumerate(conv) if conv[-1] - cv <= recent_tokens), len(conv) - 1)
        cut = cut if cut > 0 else None
    elif last_context and conv[-1] > 0 and last_context > setup_tokens:
        scale = max(1.0, (last_context - setup_tokens) / conv[-1])
    prev_ctx, prev_ts = 0, None
    for i, (c, s, cv) in enumerate(zip(calls, sizes, conv)):
        part = summary_tokens + (cv - conv[cut]) if cut is not None and i >= cut else cv * scale
        ctx = int(setup_tokens + part)
        gap = (c.ts_first - prev_ts).total_seconds() if c.ts_first and prev_ts else None
        cached = min(prev_ctx, ctx) if prev_ctx and gap is not None and gap <= CACHE_GAP_S and i != cut else 0
        out = max(1, s["out"] // 4)
        think = c.thinking_chars // 4
        c.usage = Usage(fresh_input=ctx - cached, cache_read=cached, output=out, thinking=min(think, out))
        prev_ctx, prev_ts = ctx, c.ts_last or c.ts_first
    return scale, cut


class CursorReader:
    name = "cursor"

    def can_read(self, path) -> bool:
        return isinstance(path, CursorChat)

    def read(self, chat: CursorChat) -> Session:
        con = connect(chat.db)
        try:
            d = _value(con, f"composerData:{chat.id}")
            if not d:
                raise ValueError(f"no Cursor chat {chat.id} in {chat.db}")
            s, b = self._chat(con, d, "main")
            for hid in d.get("subagentComposerIds") or []:
                hd = _value(con, f"composerData:{hid}")
                if hd:
                    hs, hb = self._chat(con, hd, "helper:" + hid[:12])
                    if hb.calls:
                        s.helpers.append(Helper(id=hid, description=short(hd.get("name") or "helper", 90),
                                                agent_type="helper", calls=hb.calls))
        finally:
            con.close()
        from .claude import _turn_at
        for h in s.helpers:
            h.turn = _turn_at(s.turns, h.calls[0].ts_first if h.calls else None)
            for c in h.calls:
                c.turn = _turn_at(s.turns, c.ts_first) if c.ts_first else h.turn   # a helper can run across turns
        return s

    def _chat(self, con: sqlite3.Connection, d: dict, agent: str) -> tuple[Session, _Builder]:
        cid = str(d["composerId"])
        model = str((d.get("modelConfig") or {}).get("modelName") or "")
        s = Session(agent=self.name, session_id=cid, source=f"{state_db()}#{cid}", cwd=workspace(d),
                    title=str(d.get("name") or ""), tokens_exact=False)
        bubbles = {}
        for k, v in con.execute("select key, value from cursorDiskKV where key like ?", (f"bubbleId:{cid}:%",)):
            try:
                bubbles[k.rsplit(":", 1)[-1]] = json.loads(v)
            except (ValueError, TypeError):
                continue
        order = [h.get("bubbleId") for h in d.get("fullConversationHeadersOnly") or [] if isinstance(h, dict)]
        msgs = [bubbles[i] for i in order if i in bubbles] or [x for x in d.get("conversation") or []
                                                                if isinstance(x, dict)]
        builder = _Builder(agent, model)
        turns: list[Turn] = []
        last_agent = None
        for b in msgs:
            when = ts(b.get("createdAt")) or _ms(b.get("startedAtMs"))
            if b.get("type") == USER:
                text = str(b.get("text") or "").strip()
                info = b.get("modelInfo") if isinstance(b.get("modelInfo"), dict) else {}
                if info.get("modelName"):
                    builder.model = str(info["modelName"])
                builder.user(text)
                if b.get("isSimulatedMsg"):        # written by Cursor itself, e.g. "a background task finished"
                    if not turns:
                        turns.append(Turn(n=0, kind="start", text="(before your first message)"))
                    turns[-1].events.append(Event("injected", "a notification from Cursor (not typed by you)",
                                                  short(text, 80), len(text), when))
                    continue
                waited = (when - last_agent).total_seconds() if when and last_agent and when > last_agent else 0.0
                t = Turn(n=len([x for x in turns if x.n > 0]) + 1, kind="typed", text=short(text, 300) or "(no text)", ts=when,
                         waited_s=waited if turns else 0.0)
                t.events.append(Event("typed", "you typed", short(text, 300), len(text), when))
                turns.append(t)
            elif b.get("type") == AGENT:
                c = builder.agent_msg(b, when)
                if not turns:
                    turns.append(Turn(n=0, kind="start", text="(before your first message)"))
                if c not in turns[-1].calls:
                    c.turn = turns[-1].n
                    turns[-1].calls.append(c)
                last_agent = when or last_agent
        s.setup = _setup(d)
        setup_tokens = sum(int(c.get("estimatedTokens") or 0) for c in _categories(d) if c.get("id") in SETUP_IDS)
        last_ctx = d.get("contextTokensUsed")
        cats = {c.get("id"): int(c.get("estimatedTokens") or 0) for c in _categories(d)}
        scale, cut = _estimate(builder.calls, builder.sizes, setup_tokens,
                               last_ctx if isinstance(last_ctx, int) else None,
                               cats.get("summarized_conversation", 0), cats.get("conversation", 0))
        if cut is not None:
            c = builder.calls[cut]
            t = next((t for t in turns if c in t.calls), None)
            if t is not None:
                t.events.append(Event("injected", "Cursor summarised the earlier conversation (place estimated)", "",
                                      cats.get("summarized_conversation", 0) * 4, c.ts_first))
        s.turns = [t for t in turns if t.calls or t.n > 0]
        s.title = s.title or next((t.text for t in s.turns if t.n > 0), "(no message from you)")
        stamps = [x for c in builder.calls for x in (c.ts_first, c.ts_last) if x] + [t.ts for t in s.turns if t.ts]
        s.started, s.ended = (min(stamps), max(stamps)) if stamps else (None, None)
        s.model = model or (builder.calls[-1].model if builder.calls else "")
        s.notes.append("Cursor does not keep token counts on this computer, so every token figure here is an "
                       "estimate from the size of the text"
                       + (f" (scaled ×{scale:.1f} to match the context size Cursor recorded)" if scale > 1.05 else "")
                       + ". Add Cursor's usage export (--cursor-usage <file.csv>) for exact numbers.")
        return s, builder


def _categories(d: dict) -> list[dict]:
    b = d.get("promptTokenBreakdown") if isinstance(d.get("promptTokenBreakdown"), dict) else {}
    return [c for c in b.get("categories") or [] if isinstance(c, dict)]


def _setup(d: dict) -> Setup:
    st = Setup()
    for c in _categories(d):
        if c.get("id") in SETUP_IDS and c.get("estimatedTokens"):
            st.parts.append(SetupPart(str(c.get("label") or c.get("id")).lower(), int(c["estimatedTokens"]) * 4))
    st.known = bool(st.parts)
    return st


# ---- Cursor's usage export (dashboard > usage > export CSV): exact tokens per request ---------------------------

COLUMNS = {
    "date": ("date", "timestamp", "time", "created at"),
    "model": ("model",),
    "input_write": ("input (w/ cache write)", "input with cache write", "cache write", "input_with_cache_write"),
    "input_plain": ("input (w/o cache write)", "input without cache write", "input", "input tokens",
                    "input_without_cache_write"),
    "cache_read": ("cache read", "cache read tokens", "cache_read"),
    "output": ("output tokens", "output", "output_tokens"),
    "total": ("total tokens", "total", "total_tokens"),
    "cost": ("cost", "cost ($)", "cost (usd)", "usd"),
}


def _num(x) -> float | None:
    x = str(x or "").replace(",", "").replace("$", "").strip()
    try:
        return float(x)
    except ValueError:
        return None


def _when(x: str) -> datetime | None:
    x = str(x or "").strip().strip('"')
    w = ts(x)
    if w is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%b %d, %Y, %I:%M:%S %p",
                    "%b %d, %Y, %I:%M %p", "%Y-%m-%d %H:%M"):
            try:
                w = datetime.strptime(x, fmt)
                break
            except ValueError:
                continue
    if w is not None and w.tzinfo is None:
        w = w.astimezone()             # no zone in the file: it is the user's local time
    return w


def read_usage_csv(path: str | Path) -> list[dict]:
    """Rows of Cursor's usage export as {when, model, write, plain, cache_read, output, cost}."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    col = {}
    for k, names in COLUMNS.items():
        for n in names:
            if n in head:
                col[k] = head.index(n)
                break
    if "date" not in col or not ({"input_plain", "output", "total"} & col.keys()):
        raise ValueError(f"{Path(path).name}: not a Cursor usage export (need a date column and token columns; "
                         f"found {', '.join(rows[0])})")
    out = []
    for r in rows[1:]:
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else ""
        when = _when(get("date"))
        if when is None:
            continue
        write, plain = int(_num(get("input_write")) or 0), int(_num(get("input_plain")) or 0)
        read, output = int(_num(get("cache_read")) or 0), int(_num(get("output")) or 0)
        if not (write or plain or read or output):
            total = int(_num(get("total")) or 0)
            if not total:
                continue
            plain = total
        out.append({"when": when, "model": get("model").strip(), "write": write, "plain": plain,
                    "cache_read": read, "output": output, "cost": _num(get("cost"))})
    return out


def apply_usage_csv(s: Session, rows: list[dict], slack_s: float = 60.0) -> int:
    """Replace estimated tokens with Cursor's exact ones, turn by turn: each export row (one request) belongs to the
    turn whose time span holds it; the turn's exact tokens are spread over its calls in proportion to the estimates.
    Returns the number of export rows used. Rows of other chats running at the same time cannot be told apart."""
    used = 0
    cost = 0.0
    have_cost = False
    spans = []
    for i, t in enumerate(s.turns):
        if not t.calls:
            continue
        start = t.ts or t.calls[0].ts_first
        end = max((c.ts_last or c.ts_first for c in t.calls if c.ts_last or c.ts_first), default=start)
        if start and end:
            spans.append((t, start, end))
    owner: dict[int, list[dict]] = {}
    for r in rows:      # each request belongs to one turn: the one it falls in, else the nearest one before it
        near = [k for k, (_, start, end) in enumerate(spans)
                if start - _td(slack_s) <= r["when"] <= end + _td(slack_s)]
        if near:
            inside = [k for k in near if spans[k][1] <= r["when"]]
            owner.setdefault(inside[-1] if inside else near[0], []).append(r)
    for k, (t, start, end) in enumerate(spans):
        mine = owner.get(k, [])
        if not mine:
            continue
        used += len(mine)
        tot = {k: sum(r[k] for r in mine) for k in ("write", "plain", "cache_read", "output")}
        for r in mine:
            if r["cost"] is not None:
                cost += r["cost"]
                have_cost = True
        if mine[0]["model"]:
            for c in t.calls:
                c.model = mine[0]["model"]
        _spread(t.calls, tot)
    if used:
        s.tokens_exact = len(owner) == len(spans)      # every turn got its exact numbers
        s.notes = [n for n in s.notes if not n.startswith("Cursor does not keep token counts")]
        s.notes.append(f"Tokens from Cursor's usage export ({used} requests), spread over each turn's calls in "
                       "proportion to their size; the turn totals are exact.")
        if have_cost:
            s.own_cost_usd = cost
    return used


def _td(sec: float):
    from datetime import timedelta
    return timedelta(seconds=sec)


def _spread(calls: list[Call], tot: dict) -> None:
    def share(values: list[int], total: int) -> list[int]:
        w = sum(values) or len(values)
        parts = [int(total * (v if sum(values) else 1) / w) for v in values]
        if parts:
            parts[-1] += total - sum(parts)
        return parts

    ctx = [c.usage.context for c in calls]
    outs = [c.usage.output for c in calls]
    writes, plains, reads = share(ctx, tot["write"]), share(ctx, tot["plain"]), share(ctx, tot["cache_read"])
    for c, w, p, r, o in zip(calls, writes, plains, reads, share(outs, tot["output"])):
        think = min(c.usage.thinking or 0, o)
        c.usage = Usage(fresh_input=p, cache_write_5m=w, cache_read=r, output=o, thinking=think)
