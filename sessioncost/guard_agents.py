"""Other agents' hooks (Codex, Cursor, Antigravity, Copilot) turned into the Claude Code shape the handlers read."""
from __future__ import annotations

import hashlib
import json
import re

from .guard_inventory import mcp_servers

# ---- other agents' hooks, turned into the Claude Code shape the handlers read, and the answers turned back ----
# Codex (learn.chatgpt.com/docs/hooks): the same events and fields; apply_patch carries a patch; it cannot "ask" (an
# ask fails and the step runs), so an ask becomes a block that tells the agent to ask you in the chat: your yes in the
# next message allows that same step once. Cursor (cursor.com/docs/hooks): its own event names and fields; a
# permission hook must always answer (an empty answer blocks the action), so it answers allow when the guard is off.
# Antigravity (antigravity.google/docs/hooks): its own field names (conversationId, workspacePaths, toolCall.name and
# .args) and tool names; PreToolUse answers {"decision": allow | deny | force_ask}; force_ask, not ask, so its own
# "Always allow" memory cannot skip a question the guard asks; PostToolUse carries no tool result, so a result's
# injection signs are not seen there; no user-message event is documented, so untrusted content counts until the chat
# ends (as in Cursor) unless PreInvocation carries the message.
AGY_TOOLS = {"run_command": ("Bash", lambda a: {"command": a.get("CommandLine") or ""}),
             "view_file": ("Read", lambda a: {"file_path": a.get("AbsolutePath") or a.get("Path") or "",
                                              **({"StartLine": a["StartLine"]} if a.get("StartLine") else {}),
                                              **({"EndLine": a["EndLine"]} if a.get("EndLine") else {})}),
             "read_file": ("Read", lambda a: {"file_path": a.get("AbsolutePath") or a.get("Path") or "",
                                              **({"StartLine": a["StartLine"]} if a.get("StartLine") else {}),
                                              **({"EndLine": a["EndLine"]} if a.get("EndLine") else {})}),
             "write_to_file": ("Write", lambda a: {"file_path": a.get("TargetFile") or a.get("Path") or "",
                                                   "content": a.get("CodeContent") or a.get("Content") or ""}),
             "replace_file_content": ("Edit", lambda a: {"file_path": a.get("TargetFile") or "", "new_string": json.dumps(a)}),
             "multi_replace_file_content": ("Edit", lambda a: {"file_path": a.get("TargetFile") or "", "new_string": json.dumps(a)}),
             "edit_file": ("Edit", lambda a: {"file_path": a.get("Path") or a.get("TargetFile") or "", "new_string": json.dumps(a)}),
             "read_url_content": ("WebFetch", lambda a: {"url": a.get("Url") or ""}),
             "search_web": ("WebSearch", lambda a: {"query": a.get("query") or a.get("Query") or ""}),
             "grep_search": ("Grep", lambda a: {"pattern": a.get("Query") or a.get("Pattern") or "", "path": a.get("SearchPath") or ""}),
             "find_by_name": ("Glob", lambda a: {"pattern": a.get("Pattern") or "", "path": a.get("SearchDirectory") or ""}),
             "list_dir": ("Glob", lambda a: {"pattern": "*", "path": a.get("DirectoryPath") or ""})}
AGY_EVENTS = {"session-start", "pre", "post", "invocation", "stop"}
AFFIRM = re.compile(r"^\s*(?:y|yes|yeah|ok|okay|sure|go ahead|go on|allow(?: it)?|do it|proceed|approved?)\b", re.I)
_ALLOW_SAID = re.compile(r"\b(?:allow|permit)\s+(?:it|this|that|them|these)\b|\balways\s+allow\b|"
                         r"\b(?:don'?t|do\s+not|stop)\s+ask(?:ing)?\b.{0,20}\bagain\b", re.I)
_ALLOW_NOT = re.compile(r"\b(?:don'?t|do\s+not|never|not|cannot|can'?t|won'?t)\s+(?:\w+\s+){0,2}(?:allow|permit)\b|\?", re.I)


def says_allow(text: str) -> bool:
    """The user's own short message says, in plain words, to allow the step the guard just asked about: "allow it",
    "always allow this", "don't ask again". A question, a "do not allow" or a long message is not that."""
    return len(text.split()) <= 15 and bool(_ALLOW_SAID.search(text)) and not _ALLOW_NOT.search(text)


# The guard's switches in plain words (owner, 2026-11-11: nothing has to be typed). The phrase must be the whole
# message, with "please" at most: "why did the guard turn off?" or "turn the guard off and run the tests" switch nothing.
_G = r"(?:the\s+)?(?:live\s*)?guard"
_RULES = r"(?:all\s+)?(?:my\s+|the\s+|this\s+project'?s\s+)?allow(?:ed)?\s+(?:steps|rules|list)"
_SAID_SWITCH = [(arg, re.compile(rf"^\s*(?:please\s+)?(?:{words})[\s.!]*(?:please[\s.!]*)?$", re.I)) for arg, words in (
    ("off", rf"(?:turn|switch|shut)\s+{_G}\s+off|(?:turn|switch|shut)\s+off\s+{_G}|{_G}\s+off|stop\s+{_G}"),
    ("strict", rf"{_G}\s+strict|(?:turn|switch|make|set)\s+{_G}\s+(?:to\s+)?strict|(?:turn|switch)\s+on\s+(?:the\s+)?strict\s+(?:live\s*)?guard"),
    ("on", rf"(?:turn|switch)\s+{_G}\s+on|(?:turn|switch)\s+on\s+{_G}|{_G}\s+on|start\s+{_G}"),
    ("allow-clear", rf"(?:clear|remove|delete|forget)\s+{_RULES}"),
    ("allow-list-rules", rf"(?:show|list)\s+(?:me\s+)?{_RULES}"))]


def says_switch(text: str) -> str | None:
    """The guard switch the user's own whole message asks for in plain words: on, strict, off, allow-clear or
    allow-list-rules; None when it is anything else."""
    return next((arg for arg, rx in _SAID_SWITCH if rx.match(text)), None)


CURSOR_GATES ={"beforeShellExecution", "beforeMCPExecution", "beforeReadFile"}


def _codex_in(inp: dict) -> dict:
    t, ti = str(inp.get("tool_name") or ""), inp.get("tool_input") or {}
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if isinstance(cmd, str) and "*** Begin Patch" in cmd:            # apply_patch: an edit to the files it names
        files = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", cmd, re.M)
        added = re.findall(r"^\*\*\* Add File: (.+?)\s*$", cmd, re.M)
        if len(files) == 1 and added:           # one new file: a write, so deleting it later in this chat goes ahead
            return {**inp, "tool_name": "Write", "tool_input": {"file_path": added[0], "content": cmd}}
        return {**inp, "tool_name": "Edit", "tool_input": {"file_path": "\n".join(files), "new_string": cmd}}
    if t == "exec_command" or (t != "Bash" and isinstance(ti, dict) and "cmd" in ti and not t.startswith("mcp__")):
        return {**inp, "tool_name": "Bash", "tool_input": {"command": str(ti.get("cmd") or ti.get("command") or "")}}
    return inp


def _cursor_in(inp: dict) -> tuple[str, dict]:
    ev = str(inp.get("hook_event_name") or "")
    roots = inp.get("workspace_roots") or [""]
    base = {"session_id": inp.get("conversation_id") or inp.get("session_id") or "cursor",
            "cwd": inp.get("cwd") or (roots[0] if isinstance(roots, list) and roots else ""), "_agent": "cursor"}
    tid = lambda x: f"{inp.get('generation_id', '')}:{hashlib.sha1(json.dumps(x, sort_keys=True).encode()).hexdigest()[:12]}"
    if ev in ("beforeShellExecution", "afterShellExecution"):
        ti = {"command": str(inp.get("command") or "")}
        return ("pre" if ev.startswith("before") else "post"), {**base, "tool_name": "Bash", "tool_input": ti, "tool_use_id": tid(ti),
                                                                 "tool_response": inp.get("output") or inp.get("stdout") or ""}
    if ev in ("beforeMCPExecution", "afterMCPExecution"):
        name = f"mcp__{inp.get('mcp_server_name') or 'server'}__{inp.get('tool_name') or 'tool'}"
        ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {"input": inp.get("tool_input")}
        resp = next((inp[k] for k in ("result_json", "result", "tool_output", "output") if k in inp), "")
        return ("pre" if ev.startswith("before") else "post"), {**base, "tool_name": name, "tool_input": ti, "tool_use_id": tid(ti),
                                                                 "tool_response": resp}
    if ev == "beforeReadFile":
        ti = {"file_path": str(inp.get("file_path") or "")}
        return "read", {**base, "tool_name": "Read", "tool_input": ti, "tool_use_id": tid(ti), "tool_response": inp.get("content") or ""}
    return {"sessionStart": "session-start", "stop": "stop", "sessionEnd": "end"}.get(ev, ""), base


def _agy_in(event: str, inp: dict) -> tuple[str, dict]:
    roots = inp.get("workspacePaths") or [""]
    base = {"session_id": inp.get("conversationId") or "antigravity",
            "cwd": roots[0] if isinstance(roots, list) and roots else "", "_agent": "antigravity"}
    if event == "invocation":                 # the user's message, when this build sends it: the turn's checkpoint
        msg = next((inp[k] for k in ("userMessage", "currentUserMessage", "prompt", "userPrompt") if isinstance(inp.get(k), str)), "")
        return ("prompt" if msg else ""), {**base, "prompt": msg}
    if event not in ("pre", "post"):
        return event, base
    call = inp.get("toolCall") if isinstance(inp.get("toolCall"), dict) else {}
    name, args = str(call.get("name") or ""), call.get("args") if isinstance(call.get("args"), dict) else {}
    tool, ti = (AGY_TOOLS[name][0], AGY_TOOLS[name][1](args)) if name in AGY_TOOLS else (name, args)
    m = re.match(r"mcp_([^_]+)_(.+)$", name)
    if m and name not in AGY_TOOLS:          # an MCP tool (Antigravity's naming is not documented: server_tool)
        tool = f"mcp__{m.group(1)}__{m.group(2)}"
    return event, {**base, "tool_name": tool, "tool_input": ti, "tool_use_id": f"{base['session_id']}:{inp.get('stepIdx', '')}",
                   "tool_response": inp.get("error") or ""}


# Copilot CLI (docs.github.com/en/copilot/reference/hooks-configuration and .../copilot-cli-reference/cli-hooks-reference):
# camelCase events with sessionId, cwd, toolName and toolArgs (a JSON string, parsed here); preToolUse answers
# {permissionDecision, permissionDecisionReason}, and an empty answer leaves the step to Copilot's own permissions;
# sessionStart reads only additionalContext; userPromptSubmitted and sessionEnd answers are dropped; a hook that times
# out lets the step run (fail open), one that errors is a deny. No tool call id: a step is known by its name and
# arguments. MCP tools are named <server>-<tool>.
COPILOT_TOOLS = {"bash": ("Bash", lambda a: {"command": a.get("command") or ""}),
                 "powershell": ("Bash", lambda a: {"command": a.get("command") or ""}),
                 "write_bash": ("Bash", lambda a: {"command": a.get("input") or ""}),        # typed into a running shell
                 "write_powershell": ("Bash", lambda a: {"command": a.get("input") or ""}),
                 "view": ("Read", lambda a: {"file_path": a.get("path") or "",
                                             **({"view_range": a["view_range"]} if a.get("view_range") else {})}),
                 "create": ("Write", lambda a: {"file_path": a.get("path") or "", "content": a.get("file_text") or ""}),
                 "edit": ("Edit", lambda a: {"file_path": a.get("path") or "", "new_string": a.get("new_str") or json.dumps(a)}),
                 "str_replace_editor": ("Edit", lambda a: {"file_path": a.get("path") or "", "new_string": json.dumps(a)}),
                 "web_fetch": ("WebFetch", lambda a: {"url": a.get("url") or ""}),
                 "web_search": ("WebSearch", lambda a: {"query": a.get("query") or ""}),
                 "glob": ("Glob", lambda a: {"pattern": a.get("pattern") or "", "path": _first(a.get("path") or a.get("paths"))}),
                 "grep": ("Grep", lambda a: {"pattern": a.get("pattern") or "", "path": _first(a.get("path") or a.get("paths"))}),
                 "rg": ("Grep", lambda a: {"pattern": a.get("pattern") or "", "path": _first(a.get("path") or a.get("paths"))})}
COPILOT_EVENTS = {"sessionStart": "session-start", "userPromptSubmitted": "prompt", "preToolUse": "pre",
                  "postToolUse": "post", "agentStop": "stop", "sessionEnd": "end"}


def _first(x) -> str:
    return str(x[0] if isinstance(x, list) and x else x or "")


def _copilot_in(event: str, inp: dict) -> dict:
    base = {"session_id": inp.get("sessionId") or "copilot", "cwd": str(inp.get("cwd") or ""), "_agent": "copilot"}
    if event == "prompt":
        return {**base, "prompt": str(inp.get("prompt") or "")}
    if event not in ("pre", "post"):
        return base
    name, args = str(inp.get("toolName") or ""), inp.get("toolArgs")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass                              # apply_patch: the patch itself
    tid = f"{base['session_id']}:{hashlib.sha1(json.dumps([name, args], sort_keys=True).encode()).hexdigest()[:12]}"
    res = inp.get("toolResult") if isinstance(inp.get("toolResult"), dict) else {}
    out = {**base, "tool_use_id": tid, "tool_response": res.get("textResultForLlm") or ""}
    if isinstance(args, str) and "*** Begin Patch" in args:
        return {**_codex_in({"tool_name": "apply_patch", "tool_input": {"command": args}}), **out}
    args = args if isinstance(args, dict) else {"input": args}
    if name in COPILOT_TOOLS:
        return {**out, "tool_name": COPILOT_TOOLS[name][0], "tool_input": COPILOT_TOOLS[name][1](args)}
    if "-" in name:                           # an MCP tool: the longest server name it starts with, else up to the last -
        try:
            known = sorted([*mcp_servers(base["cwd"], "copilot"), "github-mcp-server"], key=len, reverse=True)
        except Exception:
            known = ["github-mcp-server"]
        srv = next((s for s in known if name.startswith(s + "-")), name.rsplit("-", 1)[0])
        return {**out, "tool_name": f"mcp__{srv}__{name[len(srv) + 1:]}", "tool_input": args}
    return {**out, "tool_name": name, "tool_input": args}
