"""Which results the guard judges by their words only, and what it remembers after one (owner 2026-11-19).

- Web pages, network command output (curl, gh...) and the agent's own app tools (update_plan, Claude Docs, Artifact,
  scheduled tasks): no longer untrusted just because of where they came from. Each result is scanned; signs of an
  injection are shown with the reason and risky steps are blocked; a clean result changes nothing. Strict mode keeps
  them untrusted by source, as before. MCP servers of other people stay untrusted by source.
- The user's own project files (read, searched, or shown by git): only hidden characters and a real attack phrase
  ("ignore previous instructions", "do not tell the user") count, and not when the phrase is quoted as an example
  (a test, a handover or a detector names it). Ordinary instructions in one's own notes stay silent.
- Any result from outside (the web, an MCP server) is remembered until the user's next message: changing the agent's
  memory or rules after it asks first (memory poisoning, guard_owasp.py), even when the result looked clean.

The scan misses clever attacks (owner accepted this on 2026-11-19); strict mode is the stronger setting.
"""
from __future__ import annotations

import re

# the agent's own tools: plans, docs, artifacts, schedules (each agent names them its own way)
_OWN_APP = re.compile(r"^(?:update_plan|TodoWrite|todo_write|manage_task|schedule|Artifact\w*|Cron(?:Create|Delete|List)|ScheduleWakeup|"
                      r"EnterPlanMode|ExitPlanMode|Task(?:Create|Update|List|Get|Output|Stop)?)$|"
                      r"^mcp__(?:claude_ai_Claude_Docs|scheduled-tasks|claude-vscode|ccd_turn|ccd_session)__", re.I)

# the phrases the owner named as real attacks ("ignore previous instructions", "do not tell the user") and their
# rewordings (textflags.OVERRIDE); the other injection signs (textflags) are ordinary in one's notes
from .textflags import OVERRIDE, TAKEOVER

_REAL_ATTACK = re.compile(TAKEOVER, re.I)    # in own files, "never tell the user about X" is prompt writing (textflags.HIDE)
_QUOTED = re.compile(r"[\"'`“‘«]\s*$")            # the phrase opens a quotation: it is named, not said
_GIT_LOOK = re.compile(r"^\s*git\s+(?:-C\s+\S+\s+)?(?:diff|show|log|blame|grep)\b", re.I)


def own_app_tool(a) -> bool:
    return bool(_OWN_APP.search(a.tool or ""))


def scan_only(a) -> bool:
    """A result judged by its words only (not untrusted by source): a web page, network command output, or one of
    the agent's own app tools."""
    return a.kind == "web" or own_app_tool(a) or (a.kind == "command" and not (a.tool or "").startswith("mcp__"))


def own_scripts(a, cwd: str) -> list[str] | None:
    """The script files a command runs, when it runs one and reaches no network (None otherwise): a project's own
    tool printing its next steps (owner 2026-11-19: 13 blocks after `python .agents/…/status.py next`)."""
    from .guard_checks import _RUN_FILE
    from .risk_patterns import _NET_CMD
    t = a.target or ""
    if "network" in (a.risks or []) or _NET_CMD.search(t):
        return None
    ran = [m.group(1) for m in _RUN_FILE.finditer(t)]
    if not ran:
        return None
    import os
    return [p if os.path.isabs(os.path.expanduser(p)) else os.path.join(cwd, p) for p in ran]


def in_project(p: str, cwd: str) -> bool:
    """The path, resolved from the project folder, is inside it: `..\\other\\notes.md` and the temp folder are not
    (risk_shell._outside lets both pass, which is right for writes, not for what counts as the user's own text)."""
    import os
    from .risk_shell import _inside
    if not p or not cwd:
        return False
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(p.strip().strip("'\""))))
    return _inside(full, cwd)


def own_files(a, cwd: str) -> bool:
    """The result shows the user's own project files: a read, a search or a git look inside the project."""
    from .guard_checks import _cmd_all_read_paths
    from .risk_patterns import _VENDOR
    if a.kind == "read":
        paths = [p for p in (a.target or "").splitlines() if p.strip()]
    elif a.kind == "search":
        paths = [p for p in (getattr(a, "paths", "") or "").splitlines() if p.strip()] or [cwd]
    elif a.kind == "command" and _GIT_LOOK.search(a.target or ""):
        return bool(cwd)
    elif a.kind == "command" and (ran := own_scripts(a, cwd)) is not None:
        paths = ran                           # the output of the project's own script (it prints notes for the agent)
    elif a.kind == "command":
        paths = _cmd_all_read_paths(a.target or "")
    else:
        return False
    return bool(paths) and bool(cwd) and all(in_project(p, cwd) and not _VENDOR.search(p) for p in paths)


_SAVED_PAGE = re.compile(r"\.(?:html?|mhtml?|eml)$|(?:^|[\\/])downloads?[\\/]", re.I)   # outside content kept in the project
_ANY_ATTACK = re.compile(OVERRIDE, re.I)


def real_attack(text: str, paths: list[str] | tuple = ()) -> bool:
    """A real attack phrase that is said, not quoted as an example. In the user's own files only a takeover counts
    ("ignore your instructions"); in a web page or download kept in the project, hiding from the user counts too."""
    from .textflags import SCAN
    t = (text or "")[:SCAN]
    rx = _ANY_ATTACK if any(_SAVED_PAGE.search(p) for p in paths) else _REAL_ATTACK
    return any(not _QUOTED.search(t[max(0, m.start() - 3):m.start()]) for m in rx.finditer(t))


def outside_result(a) -> bool:
    """The result came from outside this computer (the web, an MCP server, a network command)."""
    from .risk_patterns import _SEND_ONLY
    from .guard_post import own_tool_list
    return "network" in (a.risks or []) and (a.tool or "").lower() not in _SEND_ONLY and not own_tool_list(a) \
        and not (own_app_tool(a) and not (a.tool or "").startswith("mcp__"))
