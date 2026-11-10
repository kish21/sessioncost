"""Reading Claude Code's log text: the tags the harness adds, what the user said, times, usage, and each tool
call as a step."""
from __future__ import annotations

import json
import re
from datetime import datetime

from ..model import Added, Event, Usage
from ._util import clean_cmd


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
# Attachments Claude Code sends to the model after the first call, in plain words. Others are its own records
# (the prompt snapshot, the environment snapshot, tool records, hook bookkeeping) and are not counted.
SENT_ATTACHMENTS = {
    "skill_listing": "the skill list (sent again)",
    "agent_listing_delta": "the list of helper agents",
    "instructions": "instruction files (CLAUDE.md and the like)",
    "session_context": "session context (git status and the like)",
    "invoked_skills": "instructions of a skill it used",
    "nested_memory": "an instruction file from a sub-folder",
    "file": "a file you attached",
    "selected_lines_in_ide": "your editor selection",
    "edited_text_file": "a note that a file changed",
    "hook_additional_context": "hook output (added by your hooks)",
    "hook_success": "hook output (added by your hooks)",
    "hook_system_message": "hook output (added by your hooks)",
    "task_reminder": "the task list (a reminder)",
    "read_truncation_notice": "a note that a read was cut short",
    "total_tokens_reminder": "a note of how many tokens are left in its budget",
    "budget_usd": "a budget reminder",
    "date": "today's date",
    "silent_turn_reminder": "a reminder (added by Claude Code)",
    "batching_reminder_sent": "a reminder (added by Claude Code)",
    "plan_mode": "a plan-mode reminder",
    "model": "a note about the model",
}


def _server(tool: str) -> str:
    """mcp__claude-in-chrome__computer -> claude-in-chrome"""
    return tool.split("__")[1] if tool.startswith("mcp__") and tool.count("__") >= 2 else ""


def _added(a: dict, after_call: int) -> Added | None:
    k = a.get("type")
    if k == "deferred_tools_delta":
        names = [str(x) for x in a.get("addedNames") or []]
        if not names:
            return None
        lines = a.get("addedLines")
        chars = len("\n".join(map(str, lines))) if isinstance(lines, list) and lines else len(json.dumps(names))
        servers = sorted({_server(x) for x in names} - {""})
        what = ", ".join(servers) if servers else "loaded on demand"
        return Added("tools", f"{len(names)} more tool{'s' if len(names) != 1 else ''} ({what})", chars,
                     after_call, names, servers)
    if k == "mcp_instructions_delta":
        blocks = [str(x) for x in a.get("addedBlocks") or []]
        names = [str(x) for x in a.get("addedNames") or []]
        if not blocks:
            return None
        return Added("instructions", "instructions for connected services: " + (", ".join(names) or "MCP servers"),
                     sum(len(x) for x in blocks), after_call, servers=names)
    if k in SENT_ATTACHMENTS:
        if k == "skill_listing":
            chars = len(str(a.get("content") or ""))
        elif k == "instructions":
            chars = sum(len(str(f.get("content") or "")) for f in a.get("files") or [] if isinstance(f, dict))
        else:
            chars = len(json.dumps({x: v for x, v in a.items() if x != "type"}, ensure_ascii=False))
        label = SENT_ATTACHMENTS[k]
        if k in ("edited_text_file", "file", "nested_memory") and (a.get("filename") or a.get("path")):
            label += ": " + re.split(r"[\\/]", str(a.get("filename") or a.get("path")))[-1]
        return Added("other", label, chars, after_call) if chars else None
    return None


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
    return clean_cmd(cmd)


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
    if tool.startswith("mcp__"):          # the arguments, so the watch list can check them (a key in a body, a path)
        return "other", f"used {tool.split('__')[-1]} ({tool.split('__')[1]})", json.dumps(inp, ensure_ascii=False)[:4000]
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
