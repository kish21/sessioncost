"""One Cursor tool call read as a step: its parameters, what kind of step it is, its target and label."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .claude import short
from .codex import _command, _name



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
    if base == "write":
        return "write", f"wrote `{_name(f)}`", f
    if base in ("edit_file", "search_replace", "apply_patch"):
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
