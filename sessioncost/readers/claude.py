"""Claude Code session logs: ~/.claude/projects/<project>/<session>.jsonl (+ <session>/subagents/*.jsonl).

What the log holds (checked on CLI 2.1.2xx logs):
- one `assistant` row per content block of a model reply; all rows of one reply share `message.id` and carry the
  same `usage`, so a call is counted once, by id;
- `usage`: input_tokens, cache_creation_input_tokens (newer CLIs split it into `cache_creation.ephemeral_5m/1h`),
  cache_read_input_tokens, output_tokens (`output_tokens_details.thinking_tokens` on newer CLIs);
- `user` rows: what the user typed, slash commands, text the harness injected (IDE selection, browser
  instructions, reminders, task notifications) and tool results;
- `attachment` rows: what was sent at the start (system prompt + tool definitions snapshot, skill list, ...);
- `cost-state`: Claude Code's own running cost total.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from ..model import Action, Call, Event, Helper, Session, Setup, SetupPart, Turn, Usage

# Text the harness puts into a `user` row. Never shown as something the user typed.
TAGS = {
    "browser_instruction": "browser tool instructions (added by the harness)",
    "ide_selection": "your editor selection (added by the IDE)",
    "ide_opened_file": "the file open in your editor (added by the IDE)",
    "ide_diagnostics": "editor diagnostics (added by the IDE)",
    "system-reminder": "a reminder (added by the harness)",
    "task-notification": "a background task finished (notification)",
    "local-command-stdout": "output of a local command",
    "local-command-stderr": "output of a local command",
    "local-command-caveat": "a note about local commands (added by the harness)",
    "user-prompt-submit-hook": "hook output (added by your hooks)",
    "command-message": "", "command-name": "", "command-args": "",
}
TAG_RE = re.compile(r"<(%s)\b[^>]*>.*?</\1>" % "|".join(map(re.escape, TAGS)), re.S)
OPEN_TAG_RE = re.compile(r"^\s*<(%s)\b" % "|".join(map(re.escape, TAGS)))
PREFIXES = {
    "Another Claude session sent a message": "a message from another agent",
    "<agent-message": "a message from another agent",
    "[Request interrupted": "you stopped the agent",
    "Caveat: The messages below": "a note about local commands (added by the harness)",
    "This session is being continued from a previous conversation": "summary of the earlier conversation",
}
SETUP_ATTACHMENTS = {
    "skill_listing": "skill list",
    "agent_listing_delta": "helper (agent) list",
    "deferred_tools_delta": "names of tools loaded on demand",
    "mcp_instructions_delta": "MCP server instructions",
    "instructions": "instruction files (CLAUDE.md and the like)",
    "session_context": "session context (git status and the like)",
}
KINDS = {
    "Read": "read", "NotebookRead": "read",
    "Grep": "search", "Glob": "search", "LS": "search", "ToolSearch": "search",
    "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "Write": "write",
    "Bash": "command", "PowerShell": "command", "BashOutput": "command", "KillShell": "command",
    "Monitor": "command", "TaskStop": "command",
    "WebSearch": "web", "WebFetch": "web",
    "Agent": "helper", "Task": "helper", "SendMessage": "helper",
    "AskUserQuestion": "question", "ExitPlanMode": "question",
    "Skill": "skill", "TodoWrite": "plan", "TaskCreate": "plan", "TaskUpdate": "plan",
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def short(s: str, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _name(p) -> str:
    return str(p or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(str(x.get("text", "")) for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "")


def _usage(u: dict) -> Usage:
    cw = int(u.get("cache_creation_input_tokens") or 0)
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w1h, w5m = cc.get("ephemeral_1h_input_tokens"), cc.get("ephemeral_5m_input_tokens")
    split = True
    if w1h is None and w5m is None:
        w5m, w1h = cw, 0           # older logs do not split cache writes: the API default is 5 minutes
        split = not cw
    else:
        w1h, w5m = int(w1h or 0), int(w5m or 0)
        w5m += max(0, cw - w1h - w5m)
    det = u.get("output_tokens_details")
    thinking = int(det["thinking_tokens"]) if isinstance(det, dict) and det.get("thinking_tokens") is not None \
        else None
    return Usage(fresh_input=int(u.get("input_tokens") or 0), cache_write_5m=w5m, cache_write_1h=w1h,
                 cache_read=int(u.get("cache_read_input_tokens") or 0), output=int(u.get("output_tokens") or 0),
                 thinking=thinking, write_split_known=split)


def _merge_usage(a: Usage, b: Usage) -> None:
    """Rows of one reply repeat the same usage; keep the largest of each field (a streamed row may be partial)."""
    for f in ("fresh_input", "cache_write_5m", "cache_write_1h", "cache_read", "output"):
        setattr(a, f, max(getattr(a, f), getattr(b, f)))
    if b.thinking is not None:
        a.thinking = max(a.thinking or 0, b.thinking)
    a.write_split_known = a.write_split_known and b.write_split_known


def _command_text(cmd: str) -> str:
    cmd = re.sub(r'^\s*cd\s+("[^"]*"|\'[^\']*\'|\S+)\s*(&&|;)\s*', "", cmd.strip())
    return short(cmd.splitlines()[0] if cmd else "", 80)


def describe(tool: str, inp: dict) -> tuple[str, str, str]:
    """(kind, plain-words label, target) for one tool use."""
    kind = KINDS.get(tool, "other")
    f = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
    if kind == "read":
        return kind, f"read `{_name(f)}`", str(f)
    if tool == "Write":
        return kind, f"wrote `{_name(f)}`", str(f)
    if kind == "edit":
        return kind, f"edited `{_name(f)}`", str(f)
    if kind == "search":
        q = inp.get("pattern") or inp.get("query") or f
        return kind, f"searched for `{short(q, 50)}`", str(q)
    if kind == "command":
        c = str(inp.get("command") or inp.get("description") or "")
        return kind, f"ran `{_command_text(c)}`" if c else f"used {tool}", c
    if tool == "WebSearch":
        return kind, f"searched the web: {short(inp.get('query'), 60)}", str(inp.get("query") or "")
    if tool == "WebFetch":
        return kind, f"opened a web page: {short(inp.get('url'), 60)}", str(inp.get("url") or "")
    if kind == "helper":
        d = inp.get("description") or inp.get("subagent_type") or ""
        return kind, f"started a helper: {short(d, 60)}", str(d)
    if kind == "question":
        n = len(inp.get("questions") or []) or 1
        return kind, (f"asked you {n} question{'s' if n > 1 else ''}" if tool == "AskUserQuestion"
                      else "showed you a plan to approve"), ""
    if kind == "skill":
        return kind, f"loaded the skill `{inp.get('skill', '')}`", str(inp.get("skill") or "")
    if kind == "plan":
        return kind, "updated its to-do list", ""
    if tool.startswith("mcp__"):
        return "other", f"used {tool.split('__')[-1]} ({tool.split('__')[1]})", ""
    return kind, f"used {tool}", ""


def _written(tool: str, inp: dict) -> int:
    if tool == "Write":
        return len(str(inp.get("content") or ""))
    if tool == "Edit":
        return len(str(inp.get("new_string") or ""))
    if tool == "MultiEdit":
        return sum(len(str(e.get("new_string") or "")) for e in inp.get("edits") or [] if isinstance(e, dict))
    if tool == "NotebookEdit":
        return len(str(inp.get("new_source") or ""))
    return 0


def classify_text(text: str, is_meta: bool = False) -> tuple[str | None, list[Event]]:
    """Split one user text block into what the user typed (or a /command) and harness-injected events.
    Returns (typed text or '/command args' or None, events)."""
    events: list[Event] = []
    if is_meta:
        label = "skill instructions (loaded by a command or skill)" if text.lstrip().startswith(("#", "Base directory")) \
            else "text added by the harness"
        return None, [Event("instructions", label, short(text, 80), len(text))]
    if "<command-name>" in text:
        name = re.search(r"<command-name>\s*([^<]*?)\s*</command-name>", text)
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        cmd = (name.group(1) if name else "a command").strip()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        typed = (cmd + " " + short(args.group(1), 140)).strip() if args and args.group(1).strip() else cmd
        return "\x00cmd" + typed, events
    for m in TAG_RE.finditer(text):
        label = TAGS[m.group(1)]
        if label:
            events.append(Event("injected", label, short(m.group(0), 80), len(m.group(0))))
    rest = TAG_RE.sub("", text).strip()
    if OPEN_TAG_RE.match(rest):          # an unclosed harness tag: the whole block is injected
        tag = OPEN_TAG_RE.match(rest).group(1)
        events.append(Event("injected", TAGS[tag] or "text added by the harness", short(rest, 80), len(rest)))
        return None, events
    for p, label in PREFIXES.items():
        if rest.startswith(p):
            events.append(Event("injected", label, short(rest, 80), len(rest)))
            return None, events
    return (rest or None), events


def _parse_answers(text: str) -> str:
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"', text)
    if pairs:
        return "; ".join(a for _, a in pairs)
    return short(text, 200)


class _Thread:
    """Calls and actions of one thread (the main chat or one helper)."""

    def __init__(self, agent: str):
        self.agent = agent
        self.calls: dict[str, Call] = {}
        self.order: list[Call] = []
        self.uses: dict[str, Action] = {}

    def assistant(self, r: dict, turn: int) -> Call | None:
        m = r.get("message") or {}
        mid = m.get("id")
        if not mid:
            return None
        u = _usage(m.get("usage") or {})
        rts = ts(r.get("timestamp"))
        c = self.calls.get(mid)
        if c is None:
            if m.get("model") == "<synthetic>" and not u.context and not u.output:
                return None   # an error message the CLI wrote itself, not a model call
            c = Call(n=len(self.order) + 1, msg_id=mid, agent=self.agent, model=m.get("model") or "", usage=u,
                     ts_first=rts, ts_last=rts, turn=turn)
            self.calls[mid] = c
            self.order.append(c)
        else:
            _merge_usage(c.usage, u)
            c.ts_last = rts or c.ts_last
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                txt = str(b.get("text") or "")
                c.text_chars += len(txt)
                if txt.strip() and not c.first_text:
                    c.first_text = short(txt, 120)
            elif t in ("thinking", "redacted_thinking"):
                c.thinking_chars += len(str(b.get("thinking") or ""))
            elif t == "tool_use":
                name, inp = str(b.get("name") or "?"), b.get("input") or {}
                if not isinstance(inp, dict):
                    inp = {}
                kind, label, target = describe(name, inp)
                a = Action(id=str(b.get("id") or ""), tool=name, kind=kind, label=label, target=target,
                           args_chars=len(json.dumps(inp, ensure_ascii=False)), written_chars=_written(name, inp),
                           ts=rts)
                c.actions.append(a)
                self.uses[a.id] = a
        return c

    def result(self, b: dict, rts) -> Action | None:
        a = self.uses.get(b.get("tool_use_id"))
        if a is None:
            return None
        txt = _result_text(b)
        a.result_chars = len(txt)
        a.result_hash = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:16]
        a.result_ts = rts
        a.is_error = bool(b.get("is_error"))
        if a.kind == "question":
            a.answer_text = _parse_answers(txt)
        return a


def _rows(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    return out


class ClaudeCodeReader:
    name = "claude-code"

    def can_read(self, path: Path) -> bool:
        if path.suffix != ".jsonl":
            return False
        with path.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 400:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("type") in ("assistant", "user") and \
                        isinstance(row.get("message"), dict):
                    return True
        return False

    def read(self, path: Path) -> Session:
        rows = _rows(path)
        s = Session(agent=self.name, session_id=path.stem, source=str(path))
        main = _Thread("main")
        side = _Thread("sidechain")
        turns: list[Turn] = []
        last_agent_ts = None
        snapshot = None
        setup_extra: dict[str, int] = {}

        def current() -> Turn:
            if not turns:
                turns.append(Turn(n=0, kind="start", text="(before your first message)"))
            return turns[-1]

        def new_turn(kind: str, text: str, rts) -> Turn:
            waited = (rts - last_agent_ts).total_seconds() if rts and last_agent_ts and rts > last_agent_ts else 0.0
            t = Turn(n=len([x for x in turns if x.n > 0]) + 1, kind=kind, text=short(text, 300), ts=rts,
                     waited_s=waited if turns else 0.0)
            turns.append(t)
            return t

        for r in rows:
            t = r.get("type")
            rts = ts(r.get("timestamp"))
            if not s.cwd and r.get("cwd"):
                s.cwd = str(r.get("cwd"))
            if not s.agent_version and r.get("version"):
                s.agent_version = str(r.get("version"))
            if r.get("sessionId") and s.session_id == path.stem and r.get("sessionId") != path.stem:
                s.session_id = str(r["sessionId"])
            if t == "cost-state" and r.get("totalCostUSD") is not None:
                s.own_cost_usd = float(r["totalCostUSD"])
                continue
            if t == "attachment":
                a = r.get("attachment") or {}
                k = a.get("type")
                if k == "prompt_snapshot":
                    if snapshot is None or a.get("tools"):
                        snapshot = a
                elif k in SETUP_ATTACHMENTS and k not in setup_extra:
                    if k == "instructions":
                        setup_extra[k] = sum(len(str(f.get("content") or "")) for f in a.get("files") or []
                                             if isinstance(f, dict)) or len(json.dumps(a, ensure_ascii=False))
                    elif k == "skill_listing":
                        setup_extra[k] = len(str(a.get("content") or ""))
                    else:
                        setup_extra[k] = len(json.dumps(a, ensure_ascii=False))
                elif k == "queued_command":
                    q = str(a.get("prompt") or "")
                    typed, evs = classify_text(q)
                    if typed:
                        turn = new_turn("queued", typed.replace("\x00cmd", ""), rts)
                        turn.events.append(Event("queued", "you typed (while the agent worked)", short(typed, 200),
                                                 len(q), rts))
                        turn.events += evs
                    elif evs:
                        current().events += evs
                continue
            m = r.get("message")
            if not isinstance(m, dict):
                continue
            if r.get("isSidechain"):            # older CLIs logged helpers inline
                if t == "assistant":
                    side.assistant(r, current().n)
                elif t == "user" and isinstance(m.get("content"), list):
                    for b in m["content"]:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            side.result(b, rts)
                continue
            if t == "assistant":
                c = main.assistant(r, current().n)
                if c is not None and c not in current().calls:
                    current().calls.append(c)
                last_agent_ts = max(last_agent_ts, rts) if last_agent_ts and rts else (rts or last_agent_ts)
                continue
            if t != "user":
                continue
            content = m.get("content")
            if r.get("isCompactSummary"):
                txt = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                current().events.append(Event("injected", "summary of the earlier conversation", "", len(txt), rts))
                continue
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else \
                [b for b in content or [] if isinstance(b, dict)]
            typed_parts: list[str] = []
            command = None
            origin = r.get("origin") if isinstance(r.get("origin"), dict) else {}
            machine = bool(origin.get("kind")) and origin.get("kind") != "human"
            events: list[Event] = []
            for b in blocks:
                if b.get("type") == "tool_result":
                    a = main.result(b, rts)
                    if a is not None and a.kind == "question":
                        events.append(Event("answer", "you answered a question card", a.answer_text,
                                            a.result_chars, rts))
                    last_agent_ts = max(last_agent_ts, rts) if last_agent_ts and rts else (rts or last_agent_ts)
                elif b.get("type") == "text":
                    text = str(b.get("text") or "")
                    typed, evs = classify_text(text, bool(r.get("isMeta")))
                    events += evs
                    if typed and machine and not typed.startswith("\x00cmd"):
                        events.append(Event("injected", f"text added by the harness ({origin.get('kind')})",
                                            short(typed, 80), len(typed)))
                        continue
                    if typed and typed.startswith("\x00cmd"):
                        command = typed[4:]
                    elif typed:
                        typed_parts.append(typed)
            if command:
                turn = new_turn("command", command + (" " + " ".join(typed_parts) if typed_parts else ""), rts)
                turn.events.append(Event("command", "you ran a command", command, len(command), rts))
            elif typed_parts:
                text = "\n".join(typed_parts)
                turn = new_turn("typed", text, rts)
                turn.events.append(Event("typed", "you typed", short(text, 300), len(text), rts))
            else:
                turn = current()
            for e in events:
                e.ts = e.ts or rts
            turn.events += events

        s.turns = [t for t in turns if t.calls or t.n > 0]
        s.title = next((t.text for t in s.turns if t.n > 0), "(no message from you)")
        stamps = [x for c in main.order for x in (c.ts_first, c.ts_last) if x] + [t.ts for t in s.turns if t.ts]
        s.started, s.ended = (min(stamps), max(stamps)) if stamps else (None, None)
        models = {}
        for c in main.order:
            models[c.model] = models.get(c.model, 0) + 1
        s.model = max(models, key=models.get) if models else ""
        s.setup = _setup(snapshot, setup_extra)
        if side.order:
            s.helpers.append(Helper(id="sidechain", description="helpers logged inside the main log",
                                    agent_type="helper", calls=side.order))
        s.helpers += _helpers(path)
        for h in s.helpers:
            h.turn = _turn_at(s.turns, h.calls[0].ts_first if h.calls else None)
            for c in h.calls:
                c.turn = _turn_at(s.turns, c.ts_first) if c.ts_first else h.turn   # a helper can run across turns
        if any(c.usage.thinking is None for c in main.order):
            s.notes.append("This log does not separate hidden thinking from other output; it is estimated.")
        if main.order and all(not (c.usage.cache_write_1h or c.usage.cache_write_5m) for c in main.order):
            s.notes.append("No cache writes in this log.")
        return s


def _setup(snapshot: dict | None, extra: dict[str, int]) -> Setup:
    st = Setup()
    if snapshot:
        sp = snapshot.get("systemPrompt")
        sp_text = "\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in sp) \
            if isinstance(sp, list) else str(sp or "")
        sp_text = sp_text.replace("__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__", "")
        st.parts.append(SetupPart("system prompt", len(sp_text)))
        tools = [t for t in snapshot.get("tools") or [] if isinstance(t, dict)]
        if tools:
            st.tools = sorted((SetupPart(str(t.get("name") or "?"), len(json.dumps(t, ensure_ascii=False)))
                               for t in tools), key=lambda p: -p.chars)
            st.parts.append(SetupPart(f"tool definitions ({len(tools)})", sum(p.chars for p in st.tools)))
        st.known = True
    for k, label in SETUP_ATTACHMENTS.items():
        if extra.get(k):
            st.parts.append(SetupPart(label, extra[k]))
    return st


def _helpers(path: Path) -> list[Helper]:
    d = path.with_suffix("") / "subagents"
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.jsonl")):
        meta = {}
        mf = f.with_suffix(".meta.json")
        if mf.is_file():
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except ValueError:
                meta = {}
        hid = f.stem.replace("agent-", "")
        th = _Thread("helper:" + hid[:12])
        brief = ""
        for r in _rows(f):
            m = r.get("message")
            if not isinstance(m, dict):
                continue
            if r.get("type") == "assistant":
                th.assistant(r, 0)
            elif r.get("type") == "user":
                c = m.get("content")
                if isinstance(c, str) and not brief:
                    brief = c
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            th.result(b, ts(r.get("timestamp")))
                        elif isinstance(b, dict) and b.get("type") == "text" and not brief:
                            brief = str(b.get("text") or "")
        if th.order:
            out.append(Helper(id=hid, description=short(meta.get("description") or brief, 90),
                              agent_type=str(meta.get("agentType") or "helper"), calls=th.order))
    return out


def _turn_at(turns: list[Turn], when) -> int:
    if when is None:
        return turns[-1].n if turns else 0
    n = turns[0].n if turns else 0
    for t in turns:
        if t.ts and t.ts <= when:
            n = t.n
    return n
