"""GitHub Copilot CLI session logs: $COPILOT_HOME/session-state/<session id>/events.jsonl (COPILOT_HOME defaults to
~/.copilot), one folder per session, with workspace.yaml (its `cwd:` is the project folder) next to it.

What the log holds (checked on Copilot CLI logs of October 2026; field names only):
- `session.start`: the folder (`context.cwd`), Copilot's version, the chosen model;
- `user.message`: what you typed (`content`; `delivery: steering` = typed while the agent worked). A message whose
  `source` is `agent-<id>` is the agent's own prompt to a helper, not yours;
- `session.usage_record` after each model call: `usage.inputTokens` INCLUDES the cache re-read and the cache write
  (`cacheReadTokens`, `cacheWriteTokens`; the cache keeps 30 minutes, `cacheTtlSeconds` 1800); `outputTokens`
  INCLUDES `reasoningTokens`; `model`, `duration` (ms), `apiCallId`, and `toolTokenCount` (the tool definitions);
- `assistant.message`: the reply text and its `toolRequests` (`toolCallId`, `name`, `arguments`), joined to its
  usage record by `apiCallId`; `tool.execution_start` / `tool.execution_complete` by `toolCallId`;
- helpers: `subagent.started` (its `agentId`, name, description, and the `toolCallId` of the `task` that started
  it); every event of a helper carries its `agentId`;
- `session.shutdown`: the session's own token totals per model (they equal the sum of the usage records).
Copilot is billed per premium request on a plan, not per token, so its money is API-equivalent.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from ..model import Action, Call, Event, Helper, Session, Setup, SetupPart, Turn, Usage, said_kind
from ..textflags import fingerprint, text_flags
from .claude import _rows, _turn_at, short, ts
from .codex import _command, _name, _patch

SHELL_TOOLS = {"powershell", "bash", "shell", "zsh", "cmd"}


def copilot_home() -> Path:
    base = os.environ.get("COPILOT_HOME")
    return Path(base).expanduser() if base else Path.home() / ".copilot"


def workspace_cwd(folder: Path) -> str:
    """The `cwd:` line of a session's workspace.yaml ('' when there is none)."""
    try:
        for line in (folder / "workspace.yaml").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("cwd:"):
                return line[4:].strip().strip("'\"")
    except OSError:
        pass
    return ""


def is_copilot(path: Path) -> bool:
    if path.name != "events.jsonl":
        return False
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            first = json.loads(f.readline() or "{}")
    except (OSError, ValueError):
        return False
    return isinstance(first, dict) and first.get("type") == "session.start"


def _text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(str(x.get("text") or "") if isinstance(x, dict) else str(x) for x in v)
    if isinstance(v, dict):
        return str(v.get("content") or v.get("text") or "")
    return ""


def describe(name: str, args) -> tuple[str, str, str, str, int]:
    """(tool, kind, plain-words label, target, written chars) for one tool request."""
    a = args if isinstance(args, dict) else {}
    if name == "view":
        f = str(a.get("path") or "")
        rng = a.get("view_range")
        return name, "read", f"read `{_name(f)}`" + (f" (lines {rng[0]}–{rng[-1]})" if isinstance(rng, list) and rng else ""), f, 0
    if name in ("rg", "grep"):
        q = str(a.get("pattern") or "")
        return name, "search", f"searched for `{short(q, 60)}`", q, 0
    if name == "glob":
        q = str(a.get("pattern") or "")
        return name, "search", f"looked for files `{short(q, 60)}`", q, 0
    if name in SHELL_TOOLS:
        return (name,) + _command(name, str(a.get("command") or "")) + (0,)
    if name == "apply_patch":
        return _patch(name, args if isinstance(args, str) else str(a.get("input") or a.get("patch") or ""))
    if name in ("create", "write"):
        f = str(a.get("path") or "")
        return name, "write", f"wrote `{_name(f)}`", f, len(str(a.get("file_text") or a.get("content") or ""))
    if name in ("edit", "str_replace", "str_replace_editor"):
        f = str(a.get("path") or "")
        return name, "edit", f"edited `{_name(f)}`", f, len(str(a.get("new_str") or a.get("new_string") or ""))
    if name in ("web_fetch", "openBrowserPage"):
        u = str(a.get("url") or "")
        return name, "web", f"opened {short(u, 70)}", u, 0
    if name in ("readPage", "web_search"):
        return name, "web", "read a web page" if name == "readPage" else f"searched the web: {short(str(a.get('query') or ''), 60)}", "", 0
    if name == "task":
        return name, "helper", f"started a helper: {short(str(a.get('description') or a.get('name') or ''), 60)}".rstrip(": "), "", 0
    if name in ("read_agent", "write_agent", "list_agents"):
        return name, "helper", {"read_agent": "waited for a helper", "write_agent": "sent a helper a message"}.get(
            name, "listed its helpers"), "", 0
    if name == "ask_user":
        return name, "question", "asked you a question", "", 0
    if name == "tool_search_tool":
        return name, "search", "looked up tools to load", "", 0
    if name == "task_complete":
        return name, "other", "said the task was done", "", 0
    if "-mcp-server-" in name or "__" in name:          # an MCP tool: github-mcp-server-search_issues
        return name, "other", f"used {name.split('-mcp-server-')[-1].split('__')[-1]}", "", 0
    return name, "other", f"used {name}", "", 0


def _usage(u: dict) -> Usage:
    inp, read, write = (int(u.get(k) or 0) for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"))
    r = u.get("reasoningTokens")
    return Usage(fresh_input=max(0, inp - read - write), cache_write_5m=write, cache_read=read,
                 output=int(u.get("outputTokens") or 0), thinking=int(r) if r is not None else None)


class CopilotReader:
    name = "copilot"

    def can_read(self, path: Path) -> bool:
        return is_copilot(path)

    def read(self, path: Path) -> Session:
        rows = _rows(path)
        s = Session(agent=self.name, session_id=path.parent.name, source=str(path),
                    cwd=workspace_cwd(path.parent), billed_per_token=False)
        setup = Setup()
        turns: list[Turn] = []
        by_api: dict[str, Call] = {}           # apiCallId -> the call (text, tools, usage)
        counted: list[Call] = []               # calls with a usage record, in the order they ended
        uses: dict[str, Action] = {}           # toolCallId -> the tool step
        owner: dict[str, str] = {}             # toolCallId -> the agent that asked for it
        helpers: dict[str, dict] = {}          # agentId -> what subagent.started said
        last_agent_ts = None
        tools_known = False
        premium = 0.0

        def call_for(api: str, agent: str, rts) -> Call:
            c = by_api.get(api)
            if c is None:
                c = by_api[api] = Call(n=0, msg_id=api, agent=agent, model="", usage=Usage(), ts_first=rts, ts_last=rts)
            return c

        for r in rows:
            t, d = r.get("type"), r.get("data") or {}
            if not isinstance(d, dict):
                continue
            rts = ts(r.get("timestamp"))
            agent = r.get("agentId") or "main"
            if t == "session.start":
                ctx = d.get("context") or {}
                s.cwd = s.cwd or str(ctx.get("cwd") or "")
                s.agent_version = str(d.get("copilotVersion") or d.get("version") or "")
            elif t == "system.message" and agent == "main" and not any(x.n > 0 for x in turns):
                txt = _text(d.get("content"))
                if txt:
                    setup.parts.append(SetupPart("system prompt", len(txt)))
                    setup.known = True
            elif t == "user.message":
                if agent != "main" or str(d.get("source") or "").startswith("agent-"):
                    continue                    # the agent's prompt to a helper, not yours
                text = _text(d.get("content")).strip()
                if not text:
                    continue
                waited = (rts - last_agent_ts).total_seconds() if rts and last_agent_ts and rts > last_agent_ts else 0.0
                kind = "queued" if d.get("delivery") == "steering" else said_kind(text)
                n = len([x for x in turns if x.n > 0]) + 1
                turn = Turn(n=n, kind=kind, text=short(text, 300), ts=rts, waited_s=waited if turns else 0.0)
                said = {"queued": "you typed (while the agent worked)", "command": "you ran a command"}.get(kind, "you typed")
                turn.events.append(Event(kind, said, short(text, 300), len(text), rts))
                turns.append(turn)
            elif t == "assistant.message":
                c = call_for(str(d.get("apiCallId") or d.get("messageId") or r.get("id")), agent, rts)
                txt = _text(d.get("content"))
                c.text_chars += len(txt)
                if txt.strip() and not c.first_text:
                    c.first_text = short(txt, 120)
                if txt.strip():
                    c.last_text = txt.strip()[-300:]
                c.thinking_chars += len(str(d.get("reasoningText") or ""))
                for tr in d.get("toolRequests") or []:
                    if not isinstance(tr, dict) or not tr.get("toolCallId") or tr["toolCallId"] in uses:
                        continue
                    raw = tr.get("arguments")
                    tool, kind, label, target, written = describe(str(tr.get("name") or "?"), raw)
                    a = Action(id=str(tr["toolCallId"]), tool=tool, kind=kind, label=label, target=target,
                               args_chars=len(raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)),
                               written_chars=written, ts=rts)
                    c.actions.append(a)
                    uses[a.id], owner[a.id] = a, agent
                last_agent_ts = rts or last_agent_ts
            elif t == "tool.execution_complete":
                a = uses.get(str(d.get("toolCallId") or ""))
                if a is None:
                    continue
                res = d.get("result") or {}
                txt = _text(res.get("content") if isinstance(res, dict) else res) or _text((d.get("error") or {}).get("message"))
                a.result_chars, a.result_hash, a.result_flags, a.result_ts = len(txt), fingerprint(txt), text_flags(txt), rts
                code = (d.get("shellExecution") or {}).get("exitCode")
                a.is_error = d.get("success") is False or bool(code)
                last_agent_ts = rts or last_agent_ts
            elif t == "session.usage_record":
                u = d.get("usage") or {}
                if not isinstance(u, dict):
                    continue
                c = call_for(str(u.get("apiCallId") or r.get("id")), agent, rts)
                if c in counted:
                    continue                    # the same call recorded twice
                c.usage, c.model = _usage(u), str(u.get("model") or c.model)
                c.ts_last = rts or c.ts_last
                if rts and u.get("duration"):
                    c.ts_first = rts - timedelta(milliseconds=float(u["duration"]))
                counted.append(c)
                premium += float(u.get("cost") or 0)
                if agent == "main" and not tools_known and u.get("toolTokenCount"):
                    # the tool definitions go with every call; the log gives their size in tokens, not their text
                    setup.parts.append(SetupPart(f"tool definitions ({u.get('availableToolCount') or '?'} tools)",
                                                 int(u["toolTokenCount"]) * 4))
                    setup.known = tools_known = True
                last_agent_ts = rts or last_agent_ts
            elif t == "subagent.started" and r.get("agentId"):
                helpers[r["agentId"]] = {"name": d.get("agentDisplayName") or d.get("agentName") or "helper",
                                         "desc": d.get("agentDescription") or "", "type": d.get("agentType") or "helper",
                                         "by": str(d.get("toolCallId") or "")}
        # a reply that never got a usage record (cut off) has no tokens to price, so it is not a call
        main = sorted([c for c in counted if c.agent == "main"], key=lambda c: c.ts_last or c.ts_first)
        if any(c.ts_first and (not turns or c.ts_first < turns[0].ts) for c in main) or not turns:
            turns.insert(0, Turn(n=0, kind="start", text="(before your first message)"))
        for i, c in enumerate(main, 1):
            c.n, c.msg_id = i, f"main#{i}"
            c.turn = _turn_at(turns, c.ts_first)
            next(t for t in turns if t.n == c.turn).calls.append(c)
        s.turns = [t for t in turns if t.calls or t.n > 0]
        for aid in dict.fromkeys(c.agent for c in counted if c.agent != "main"):
            calls = sorted([c for c in counted if c.agent == aid], key=lambda c: c.ts_last or c.ts_first)
            info = helpers.get(aid, {})
            for i, c in enumerate(calls, 1):
                c.n, c.agent, c.msg_id = i, "helper:" + aid[:12], f"{aid}#{i}"
                c.turn = _turn_at(s.turns, c.ts_first)
            parent = owner.get(info.get("by", ""), "main")
            desc = f"{info.get('name', 'helper')}: {info['desc']}" if info.get("desc") else info.get("name", "helper")
            s.helpers.append(Helper(id=aid, description=short(desc, 90), agent_type="helper", calls=calls,
                                    turn=_turn_at(s.turns, calls[0].ts_first) if calls else 0,
                                    parent="" if parent == "main" else parent))
        s.title = next((t.text for t in s.turns if t.n > 0), "(no message from you)")
        stamps = [x for c in counted for x in (c.ts_first, c.ts_last) if x] + [t.ts for t in s.turns if t.ts]
        s.started, s.ended = (min(stamps), max(stamps)) if stamps else (None, None)
        models: dict[str, int] = {}
        for c in main:
            models[c.model] = models.get(c.model, 0) + 1
        s.model = max(models, key=models.get) if models else ""
        s.setup = setup
        if premium:
            s.notes.append(f"Copilot counted {premium:g} premium request{'s' if premium != 1 else ''} for this session; "
                           "your plan bills those, not tokens, so the money here is what the tokens would cost on the API.")
        s.notes.append("Copilot logs the size of its tool definitions, not their text, so Step 0 shows them as one block.")
        return s
