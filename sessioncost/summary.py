"""The summary SessionCost would send for deeper fixes: numbers, tool names and yes/no flags, never content.

What it never holds: your messages, the agent's replies, code, file contents, command text, file or folder names,
the project name, the session id, or clock times. Files and commands appear only as short codes that are random for
each summary ("f3", "c7"), so the same file read twice shows as the same code without saying which file it is.
Times are seconds from the start of the session.

Nothing is sent yet: the report shows this summary so you can see exactly what would be shared.
"""
from __future__ import annotations

import re

from . import __version__

FORMAT = "sessioncost-summary/1"

# Program names that say what kind of step a command was, and nothing about your project. Anything else is "other".
PROGRAMS = {
    "git", "gh", "npm", "npx", "pnpm", "yarn", "node", "bun", "deno", "python", "python3", "py", "pip", "pip3", "uv",
    "uvx", "pytest", "go", "cargo", "rustc", "java", "mvn", "gradle", "dotnet", "make", "cmake", "docker", "kubectl",
    "ls", "dir", "cat", "head", "tail", "type", "grep", "rg", "find", "sed", "awk", "echo", "cd", "pwd", "mkdir", "rm",
    "cp", "mv", "touch", "chmod", "curl", "wget", "sleep", "timeout", "start-sleep", "netstat", "lsof", "ps", "kill",
    "tasklist", "taskkill", "get-childitem", "get-content", "set-content", "select-string", "powershell", "pwsh",
    "bash", "sh", "cmd", "wc", "sort", "diff", "tar", "zip", "unzip", "which", "where", "ssh", "scp", "jq",
}
GIT_SUB = {"status", "diff", "log", "add", "commit", "push", "pull", "fetch", "checkout", "switch", "branch", "merge",
           "rebase", "stash", "show", "reset", "tag", "clone", "init", "remote", "rev-parse", "ls-files", "restore"}
SOURCE_EXT = r"\.(py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|cs|cpp|c|h|rb|php|swift|html|css|scss|vue|svelte|json|ya?ml|toml|md)\b"


def _flags(cmd: str) -> list[str]:
    """Yes/no facts about a command's shape. The command text itself is not kept."""
    whole = cmd.strip()
    c = whole.splitlines()[0] if whole else ""          # the shape is the first line; a script body is not a chain
    low = c.lower()
    f = []
    if re.search(r"(^|\s)(--help|-h|/\?)(\s|$)|\bhelp\b", low):
        f.append("help")
    if re.match(r"(cd|set-location|pushd)\s", low):
        f.append("cd_prefix")
    if "&&" in c or re.search(r";\s*\S", c):
        f.append("chained")
    if re.search(r"(^|\s)&\s*$|start-process|nohup|run_in_background", low):
        f.append("background")
    if re.match(r"(sleep|start-sleep|timeout)\b", low):
        f.append("wait")
    wl = whole.lower()
    if re.search(r"(sed\s+-i|perl\s+-pi|set-content|out-file|python3?\s+-c|>\s*\S+" + SOURCE_EXT + r"|<<)", wl) \
            and re.search(r"(write_text|>\s*\S+|sed\s+-i|set-content|out-file|\.write\()", wl) and re.search(SOURCE_EXT, wl):
        f.append("rewrites_file")
    return f


def _program(cmd: str) -> tuple[str, str]:
    words = re.findall(r"[^\s;&|]+", cmd.strip())
    i = 0
    while i < len(words) and (words[i].lower() in ("cd", "set-location", "pushd") or "=" in words[i]):
        i += 2 if words[i].lower() in ("cd", "set-location", "pushd") else 1          # skip "cd dir &&" and VAR=x
        while i < len(words) and words[i] in ("&&", ";"):
            i += 1
    if i >= len(words):
        return "other", ""
    prog = re.split(r"[\\/]", words[i].strip("\"'"))[-1].lower().removesuffix(".exe")
    if prog not in PROGRAMS:
        return "other", ""
    sub = ""
    if prog == "git" and i + 1 < len(words) and words[i + 1].lower() in GIT_SUB:
        sub = words[i + 1].lower()
    return prog, sub


SETUP_KINDS = [("system prompt", "system prompt"), ("base instructions", "system prompt"), ("tool definition", "tool definitions"),
               ("skill", "skill list"), ("helper", "helper list"), ("agent", "helper list"), ("loaded on demand", "deferred tool names"),
               ("mcp", "MCP server instructions"), ("instruction", "instruction files"), ("rule", "instruction files"),
               ("context", "session context"), ("built-in", "built-in instructions (not in the log)")]


def _setup_kind(what: str) -> str:
    """Setup parts can be named after your own files (Cursor, Codex): keep only a generic kind."""
    w = what.lower()
    if w.startswith("the agent's built-in"):
        return "built-in instructions (not in the log)"
    return next((kind for key, kind in SETUP_KINDS if key in w), "other")


def _tool_name(tool: str) -> str:
    """The agent's own tool names (Read, Bash, ...) are public. MCP tool names can name private servers: keep only
    that it was an MCP tool."""
    return "mcp" if tool.lower().startswith("mcp") else tool


def build(s, a: dict) -> dict:
    """The summary for one session: s is the Session, a the analysis made from it."""
    codes: dict[tuple[str, str], str] = {}

    def code(kind: str, target: str) -> str:
        key = (kind, target.strip())
        if key not in codes:
            codes[key] = ("f" if kind == "file" else "c" if kind == "command" else "q") + str(len(codes) + 1)
        return codes[key]

    t0 = s.started
    secs = lambda d: round((d - t0).total_seconds(), 1) if d and t0 else None
    seen_result: dict[str, str] = {}                  # target code -> result fingerprint last seen
    by_id = {(c["agent"], c["n"]): c for c in a["calls"]}

    def step(x) -> dict:
        kind = {"read": "file", "edit": "file", "write": "file", "command": "command"}.get(x.kind, "query")
        st = {"tool": _tool_name(x.tool), "kind": x.kind,
              "in": round(x.args_chars / 4), "out": round(x.result_chars / 4), "wrote": round(x.written_chars / 4),
              "error": x.is_error,
              "secs": round((x.result_ts - x.ts).total_seconds(), 1) if x.ts and x.result_ts else None}
        if x.target:
            st["target"] = code(kind, x.target)
            if x.result_hash:
                st["same_result_as_last_time"] = seen_result.get(st["target"]) == x.result_hash
                seen_result[st["target"]] = x.result_hash
        if x.kind == "command" and x.target:
            st["program"], sub = _program(x.target)
            if sub:
                st["git"] = sub
            st["flags"] = _flags(x.target)
        return st

    def call(c) -> dict:
        d = by_id.get((c.agent, c.n), {})
        sent = d.get("sent", {})
        return {"thread": "main" if c.agent == "main" else "helper", "turn": c.turn, "t": secs(c.ts_first),
                "model": c.model,
                "sent": {k: sent.get(k, 0) for k in ("setup", "history", "new")},
                "cache_read": c.usage.cache_read, "cache_write": c.usage.cache_write, "written": c.usage.output,
                "thinking": c.usage.thinking, "steps": [step(x) for x in c.actions]}

    tools = a["setup"]["tools"]
    return {
        "format": FORMAT,
        "made_by": f"sessioncost {__version__}",
        "agent": s.agent, "agent_version": s.agent_version, "models": a["meta"]["models"],
        "session": {"turns": a["totals"]["turns"], "calls": a["totals"]["calls"],
                    "helper_calls": a["totals"]["helper_calls"], "working_min": a["totals"]["working_min"],
                    "waiting_min": a["totals"]["waiting_min"], "tokens_exact": a["money"]["tokens_exact"]},
        "setup": {"tokens": a["setup"]["tokens"],
                  "parts": [{"what": _setup_kind(p["what"]), "tokens": p["tokens"]} for p in a["setup"]["parts"]],
                  "tools_defined": len(tools), "tools_used": sum(1 for x in tools if x["used"]),
                  "unused_tool_tokens": a["setup"]["unused_tokens"],
                  "mcp_tools_defined": sum(1 for x in tools if x["name"].lower().startswith("mcp"))},
        "turns": [{"n": t["n"], "kind": t["kind"], "said_tokens": sum(e["tokens"] for e in t["events"]
                                                                      if e["kind"] in ("typed", "command", "queued", "answer")),
                   "added_by_agent_tokens": sum(e["tokens"] for e in t["events"]
                                                if e["kind"] not in ("typed", "command", "queued", "answer")),
                   "waited_s": round(t["waited_min"] * 60), "calls": len(t["calls"]),
                   "confirm_only": "confirm-turn" in t["fixes"]} for t in a["turns"]],
        "calls": [call(c) for c in s.calls] + [call(c) for h in s.helpers for c in h.calls],
        "helpers": [{"calls": h["calls"], "turn": h["turn"], "tokens": h["tokens"]} for h in a["helpers"]],
        "basic_fixes": [{"id": f["id"], "tokens": f["saving_tokens"]} for f in a["fixes"]],
    }
