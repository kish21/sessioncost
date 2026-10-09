"""Codex session logs (CLI, IDE extension, desktop app): $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl
(CODEX_HOME defaults to ~/.codex), one file per thread.

What the log holds (checked on Codex 0.125 to 0.160 logs):
- `session_meta` first: thread id, session id, cwd, version, the base instructions (system prompt); a helper thread
  (the command reviewer "guardian", or an agent it spawned) has its own file with `parent_thread_id` and the main
  thread's session id. A forked helper starts with a copy of its parent's history, up to
  `subagent_history_start_ordinal`; that copy is skipped so nothing is counted twice;
- `response_item` rows: reasoning, assistant messages, tool calls and their outputs, and injected developer/user
  context messages;
- `event_msg` `token_count` after each model call: `last_token_usage` = that call (input_tokens INCLUDES the cached
  part; output_tokens INCLUDES reasoning). A count whose running total did not move is a repeat and is skipped;
- `task_started` / `task_complete` around each turn, the typed message as `user_message` (older) or
  `item_completed` UserMessage (newer), `turn_context` with the model, `compacted` when the history was summarised.
Tool definitions are not in the log, so the setup holds the instructions only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path

from ..model import Action, Call, Event, Helper, Session, Setup, SetupPart, Turn, Usage
from .claude import _rows, _turn_at, short, ts

CALL_ITEMS = ("function_call", "custom_tool_call", "local_shell_call", "web_search_call", "tool_search_call")
OUTPUT_ITEMS = ("function_call_output", "custom_tool_call_output", "local_shell_call_output", "tool_search_output")
SHELL_TOOLS = {"shell", "shell_command", "exec_command", "local_shell", "container.exec"}
READ_RE = re.compile(r"^\s*(cat|type|head|tail|nl|less|more|Get-Content|gc|sed\s+-n)\b", re.I)
EXEC_RE = re.compile(r"tools\.(\w+)\(\{\s*\"?cmd\"?\s*:\s*\"((?:[^\"\\]|\\.)*)\"")
TOOL_RE = re.compile(r"tools\.(\w+)\(")
QUERY_RE = re.compile(r"\bq\"?\s*:\s*\"((?:[^\"\\]|\\.)*)\"")
TRIM_PIPE_RE = re.compile(r"\s*\|\s*(Select-Object|select|head|tail|Out-String|more)\b[^|;&]*$", re.I)
PATCH_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.M)
TAG_RE = re.compile(r"^\s*<([\w-]+)")
SETUP_LABELS = {
    "permissions instructions": "permission rules",
    "environment_context": "session context (folder, shell, date)",
    "user_instructions": "instruction files (AGENTS.md and the like)",
    "collaboration_mode": "collaboration mode instructions",
    "app-context": "app context",
}


def codex_home() -> Path:
    base = os.environ.get("CODEX_HOME")
    return Path(base).expanduser() if base else Path.home() / ".codex"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    return ""


def _usage(u: dict) -> Usage:
    inp, cached = int(u.get("input_tokens") or 0), int(u.get("cached_input_tokens") or 0)
    write = int(u.get("cache_write_input_tokens") or 0)
    r = u.get("reasoning_output_tokens")
    return Usage(fresh_input=max(0, inp - cached - write), cache_write_5m=write, cache_read=cached,
                 output=int(u.get("output_tokens") or 0), thinking=int(r) if r is not None else None)


def _name(p: str) -> str:
    return str(p or "").strip().strip("'\"").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _reads(cmd: str) -> list[str]:
    """The files a command only reads (`a; b` chains of reads included), or [] when it does anything else."""
    files = [_read_target(x) for x in re.split(r";|&&|\n", cmd) if x.strip()]
    return files if files and all(files) else []


def _read_target(cmd: str) -> str:
    """The file one simple read command reads, or '' when it is not one (a pipe into a pager or `Select-Object`
    is fine; any other pipe, chain or variable makes it a command)."""
    cmd = TRIM_PIPE_RE.sub("", cmd.strip())
    if not READ_RE.match(cmd) or re.search(r"[|;&>$]", cmd):
        return ""
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        parts = cmd.split()
    args = [p for p in parts[1:] if not p.startswith("-") and not p.strip("'\"").replace(",", "").rstrip("p").isdigit()]
    return args[-1].strip("'\"") if args else ""


def _command(tool: str, cmd: str) -> tuple[str, str, str]:
    files = _reads(cmd)
    if len(files) == 1:
        f = files[0]
        return "read", f"read `{_name(f)}`" + (f" ({m.group(0)})" if (m := re.search(r"\d+,\d+p", cmd)) else ""), f
    if files:
        return "read", _read_label(files), "\n".join(files)
    first = short(cmd.strip().splitlines()[0] if cmd.strip() else "", 80)
    return "command", f"ran `{first}`" if first else f"used {tool}", cmd


def _read_label(files: list[str]) -> str:
    return f"read {len(files)} files: " + ", ".join(f"`{_name(f)}`" for f in files[:4]) + ("…" if len(files) > 4 else "")


def _unquote(s: str) -> str:
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s


def _shell_cmd(args: dict) -> str:
    c = args.get("command") if args.get("command") is not None else args.get("cmd")
    if isinstance(c, list):
        c = c[-1] if len(c) >= 3 and str(c[-2]).lower() in ("-c", "-command", "/c", "-lc") else " ".join(map(str, c))
    return str(c or "")


def _patch(tool: str, patch: str) -> tuple[str, str, str, str, int]:
    files = PATCH_RE.findall(patch)
    written = sum(len(line) - 1 for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
    kind = "write" if files and all(op == "Add" for op, _ in files) else "edit"
    verb = "wrote" if kind == "write" else "edited"
    names = ", ".join(f"`{_name(f)}`" for _, f in files[:3]) + ("…" if len(files) > 3 else "")
    return tool, kind, f"{verb} {names}" if files else "edited files", "\n".join(f.strip() for _, f in files), written


def describe(p: dict) -> tuple[str, str, str, str, int]:
    """(tool, kind, plain-words label, target, written chars) for one call item."""
    t = p.get("type")
    if t == "web_search_call":
        a = p.get("action") or {}
        q = a.get("query") or "; ".join(a.get("queries") or []) or a.get("url") or ""
        return "web_search", "web", f"searched the web: {short(q, 60)}", str(q), 0
    if t == "tool_search_call":
        return "tool_search", "search", "looked up tools to load", "", 0
    if t == "local_shell_call":
        cmd = _shell_cmd((p.get("action") or {}))
        return ("local_shell",) + _command("local_shell", cmd) + (0,)
    name = str(p.get("name") or "?")
    raw = p.get("input") if t == "custom_tool_call" else p.get("arguments")
    try:
        args = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
    except ValueError:
        args = {}
    text = raw if isinstance(raw, str) else ""
    if name in SHELL_TOOLS:
        return (name,) + _command(name, _shell_cmd(args)) + (0,)
    if name == "exec":                      # code mode: one script that runs several tools
        cmds = [_unquote(c) for _, c in EXEC_RE.findall(text)]
        if not cmds:
            used = sorted(set(TOOL_RE.findall(text)))
            if "apply_patch" in used:
                return _patch(name, text.replace("\\n", "\n"))
            if used == ["write_stdin"]:
                return name, "command", "typed into a running command", "", 0
            if any(u.startswith("web") for u in used):
                q = QUERY_RE.search(text)
                return name, "web", (f"searched the web: {short(_unquote(q.group(1)), 60)}" if q
                                     else "opened a web page"), short(text, 200), 0
            return name, "command", ("ran a script using " + ", ".join(used[:3])) if used else "ran a script", \
                short(text, 200), 0
        if len(cmds) == 1:
            return (name,) + _command(name, cmds[0]) + (0,)
        reads = [_reads(c) for c in cmds]
        if all(reads):
            files = [f for r in reads for f in r]
            return name, "read", _read_label(files), "\n".join(files), 0
        return name, "command", f"ran {len(cmds)} commands: `{short(cmds[0].splitlines()[0], 60)}`, …", \
            "\n".join(cmds), 0
    if name == "apply_patch":
        return _patch(name, text if not args else str(args.get("input") or args.get("patch") or ""))
    if name == "js":
        return name, "command", "ran a script", short(text, 200), 0
    if name == "write_stdin":
        return name, "command", "typed into a running command", "", 0
    if name == "update_plan":
        return name, "plan", "updated its plan", "", 0
    if name == "request_user_input":
        return name, "question", "asked you a question", "", 0
    if name in ("spawn_agent", "send_input", "wait_agent", "wait", "close_agent", "resume_agent"):
        label = {"spawn_agent": "started a helper", "wait_agent": "waited for a helper", "wait": "waited for a helper",
                 "send_input": "sent a helper a message"}.get(name, f"used {name}")
        return name, "helper", label, "", 0
    if name == "view_image":
        f = str(args.get("path") or "")
        return name, "read", f"looked at the image `{_name(f)}`", f, 0
    if "__" in name or "." in name:
        short_name = re.split(r"__|\.", name)[-1]
        return name, "other", f"used {short_name}", "", 0
    return name, "other", f"used {name}", "", 0


class _Thread:
    """Calls of one Codex thread, read in order."""

    def __init__(self, agent: str):
        self.agent = agent
        self.order: list[Call] = []
        self.uses: dict[str, Action] = {}
        self.pending: Call | None = None
        self.model = ""
        self.total = None

    def _call(self, rts) -> Call:
        if self.pending is None:
            self.pending = Call(n=0, msg_id="", agent=self.agent, model=self.model, usage=Usage(),
                                ts_first=rts, ts_last=rts)
        self.pending.ts_last = rts or self.pending.ts_last
        return self.pending

    def item(self, p: dict, rts) -> None:
        t = p.get("type")
        if t == "reasoning":
            c = self._call(rts)
            c.thinking_chars += sum(len(str(x.get("text") or "")) for x in p.get("summary") or [] if isinstance(x, dict))
        elif t == "message" and p.get("role") == "assistant":
            c = self._call(rts)
            txt = _text(p.get("content"))
            c.text_chars += len(txt)
            if txt.strip() and not c.first_text:
                c.first_text = short(txt, 120)
        elif t in CALL_ITEMS:
            c = self._call(rts)
            tool, kind, label, target, written = describe(p)
            raw = p.get("input") if t == "custom_tool_call" else p.get("arguments") or p.get("action")
            a = Action(id=str(p.get("call_id") or p.get("id") or ""), tool=tool, kind=kind, label=label, target=target,
                       args_chars=len(raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)),
                       written_chars=written, ts=rts)
            c.actions.append(a)
            if a.id:
                self.uses[a.id] = a
        elif t in OUTPUT_ITEMS:
            a = self.uses.get(str(p.get("call_id") or ""))
            if a is None:
                return
            out = p.get("output")
            txt = _text(out) if not isinstance(out, dict) else str(out.get("content") or "")
            a.result_chars = len(txt)
            a.result_hash = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:16]
            a.result_ts = rts
            a.is_error = bool(re.search(r"(?:Exit code|exit_code\"?:)\s*[1-9]", txt[:400]))

    def tokens(self, info: dict | None, rts) -> Call | None:
        """A token count closes the call it follows; a repeated or empty count is not a call."""
        if not isinstance(info, dict) or not isinstance(info.get("last_token_usage"), dict):
            return None
        total = (info.get("total_token_usage") or {}).get("total_tokens")
        if total is not None and total == self.total:
            return None
        self.total = total
        c = self._call(rts)
        c.usage = _usage(info["last_token_usage"])
        c.model = c.model or self.model
        c.n = len(self.order) + 1
        c.msg_id = f"{self.agent}#{c.n}"
        self.order.append(c)
        self.pending = None
        return c


def _meta(path: Path) -> dict:
    with path.open(encoding="utf-8", errors="replace") as f:
        try:
            r = json.loads(f.readline())
        except ValueError:
            return {}
    return r.get("payload") or {} if isinstance(r, dict) and r.get("type") == "session_meta" else {}


def _label(text: str) -> str:
    m = TAG_RE.match(text)
    tag = m.group(1).replace("_", " ") if m else ""
    for k, v in SETUP_LABELS.items():
        if m and m.group(1).lower().startswith(k.split()[0].lower()):
            return v
    if text.lstrip().startswith("# AGENTS.md"):
        return "instruction files (AGENTS.md and the like)"
    return f"{tag} (added by Codex)" if tag else "text added by Codex"


class CodexReader:
    name = "codex"

    def can_read(self, path: Path) -> bool:
        return path.suffix == ".jsonl" and bool(_meta(path).get("id"))

    def read(self, path: Path) -> Session:
        meta = _meta(path)
        s = Session(agent=self.name, session_id=str(meta.get("id") or path.stem), source=str(path),
                    cwd=str(meta.get("cwd") or ""), agent_version=str(meta.get("cli_version") or ""))
        th = _Thread("main")
        turns, setup = self._read(path, th, s, top=True)
        s.turns = [t for t in turns if t.calls or t.n > 0]
        s.title = next((t.text for t in s.turns if t.n > 0), "(no message from you)")
        stamps = [x for c in th.order for x in (c.ts_first, c.ts_last) if x] + [t.ts for t in s.turns if t.ts]
        s.started, s.ended = (min(stamps), max(stamps)) if stamps else (None, None)
        models: dict[str, int] = {}
        for c in th.order:
            models[c.model] = models.get(c.model, 0) + 1
        s.model = max(models, key=models.get) if models else ""
        s.setup = setup
        if not meta.get("parent_thread_id"):
            s.helpers = self._helpers(path, s.session_id, s.session_id)
        for h in s.helpers:
            h.turn = _turn_at(s.turns, h.calls[0].ts_first if h.calls else None)
            for c in h.calls:
                c.turn = _turn_at(s.turns, c.ts_first) if c.ts_first else h.turn   # a helper can run across turns
        if any(c.usage.thinking is None for c in th.order):
            s.notes.append("This log does not separate hidden thinking from other output; it is estimated.")
        s.notes.append("Codex does not log its tool definitions, so the fixed setup counts its instructions only.")
        return s

    def _read(self, path: Path, th: _Thread, s: Session | None, top: bool) -> tuple[list[Turn], Setup]:
        rows = _rows(path)
        meta = _meta(path)
        skip_to = int(meta.get("subagent_history_start_ordinal") or 0)
        turns: list[Turn] = []
        setup = Setup()
        base = (meta.get("base_instructions") or {}).get("text") if isinstance(meta.get("base_instructions"), dict) \
            else meta.get("base_instructions")
        if base:
            setup.parts.append(SetupPart("system prompt (base instructions)", len(str(base))))
            setup.known = True
        last_agent_ts = None
        task_typed: list[str] = []

        def current() -> Turn:
            if not turns:
                turns.append(Turn(n=0, kind="start", text="(before your first message)"))
            return turns[-1]

        for i, r in enumerate(rows):
            if int(r.get("ordinal", i) or 0) < skip_to:
                continue
            t, p = r.get("type"), r.get("payload") or {}
            if not isinstance(p, dict):
                continue
            rts = ts(r.get("timestamp"))
            pt = p.get("type")
            if t == "turn_context" and p.get("model"):
                th.model = str(p["model"])
            elif t == "world_state" and top and not turns:
                skills = ((p.get("state") or {}).get("host_skills") or {}).get("body")
                if skills and not any(x.what == "skill list" for x in setup.parts):
                    setup.parts.append(SetupPart("skill list", len(str(skills))))
            elif t == "compacted":
                current().events.append(Event("injected", "summary of the earlier conversation", "",
                                              len(json.dumps(p, ensure_ascii=False)), rts))
            elif t == "response_item":
                if pt == "message" and p.get("role") in ("user", "developer"):
                    txt = _text(p.get("content"))
                    kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds") or []
                    typed = p.get("role") == "user" and ("user.text" in kinds or not (
                        kinds or txt.lstrip().startswith(("<", "# AGENTS.md"))))
                    if typed or not txt:
                        continue
                    if top and not any(x.n > 0 for x in turns):
                        setup.parts.append(SetupPart(_label(txt), len(txt)))
                        setup.known = True
                    else:
                        current().events.append(Event("injected", _label(txt), short(txt, 80), len(txt), rts))
                    continue
                th.item(p, rts)
                if pt in OUTPUT_ITEMS or pt in CALL_ITEMS or pt == "message":
                    last_agent_ts = rts or last_agent_ts
            elif t == "event_msg":
                if pt == "token_count":
                    c = th.tokens(p.get("info"), rts)
                    if c is not None:
                        c.turn = current().n
                        current().calls.append(c)
                        last_agent_ts = rts or last_agent_ts
                elif pt == "task_started":
                    task_typed = []
                elif pt == "turn_aborted":
                    current().events.append(Event("injected", "you stopped the agent", "", 0, rts))
                elif pt in ("user_message", "item_completed"):
                    if pt == "item_completed":
                        it = p.get("item") or {}
                        if it.get("type") != "UserMessage":
                            continue
                        text = _text(it.get("content"))
                    else:
                        text = str(p.get("message") or "")
                    text = text.strip()
                    if not text or text in task_typed:
                        continue
                    task_typed.append(text)
                    waited = (rts - last_agent_ts).total_seconds() if rts and last_agent_ts and rts > last_agent_ts \
                        else 0.0
                    n = len([x for x in turns if x.n > 0]) + 1
                    kind = "queued" if len(task_typed) > 1 else "typed"
                    turn = Turn(n=n, kind=kind, text=short(text, 300), ts=rts, waited_s=waited if turns else 0.0)
                    turn.events.append(Event(kind, "you typed" if kind == "typed" else "you typed (while the agent worked)",
                                             short(text, 300), len(text), rts))
                    turns.append(turn)
        # A reply left without a token count (cut off) has no usage to price, so it is not counted as a call.
        return turns, setup

    def _helpers(self, path: Path, session_id: str, root_id: str) -> list[Helper]:
        root = path
        for _ in range(4):                  # sessions/YYYY/MM/DD/file -> sessions
            root = root.parent
        out = []
        for f in sorted(root.glob("**/rollout-*.jsonl")) if root.name == "sessions" else []:
            if f == path:
                continue
            m = _meta(f)
            if m.get("session_id") != session_id or not m.get("parent_thread_id") or m.get("id") == root_id:
                continue
            src = (m.get("source") or {}).get("subagent") if isinstance(m.get("source"), dict) else None
            if isinstance(src, dict) and src.get("other") == "guardian":
                kind, desc = "reviewer", "Codex's reviewer checking commands before they run"
            elif isinstance(src, dict) and isinstance(src.get("thread_spawn"), dict):
                sp = src["thread_spawn"]
                kind = "helper"
                desc = f"helper {sp.get('agent_nickname') or ''} {sp.get('agent_path') or ''}".strip()
            else:
                kind, desc = "helper", "helper thread"
            hid = str(m.get("id") or f.stem)
            th = _Thread("helper:" + hid[-12:])
            self._read(f, th, None, top=False)
            if th.order:
                out.append(Helper(id=hid, description=short(desc, 90), agent_type=kind, calls=th.order))
        return out
