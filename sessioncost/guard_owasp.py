"""Five guards from the OWASP Top 10 for LLM Applications 2025 (owner 2026-11-19), each a pause with its reason:

  url_leak       data leak through a web address: a value from the project's .env, a long block of encoded data or
                 several lines of text put into a web address the agent opens (LLM02: Sensitive Information Disclosure)
  memory_poison  the agent's memory or rules changed after content from outside (the web, an MCP server) came in this
                 turn, even when it looked clean, and the new text carries something that acts (a command that
                 downloads, runs or installs, a switched-off check, words that steer the agent): what is planted there
                 steers every later chat (LLM01: Prompt Injection). A plain note stays silent.
  prompt_leak    the agent's rules or instruction files sent out: a rule file uploaded, or PROMPT_LINES or more of its
                 lines in what is sent (one quoted rule in a pull request is not a leak) (LLM07: System Prompt Leakage)
  runaway        the same command, web or MCP step run RUNAWAY times in one turn with no file changed in between
                 (LLM10: Unbounded Consumption); reads, searches, the agent's task lists and status polling
                 (gh run view, gh pr checks, git status) are not counted
A replay of 597 saved chats (tools/guard_replay.py, 2026-11-19) set these limits: looser ones paused on normal work.
  fresh_release  an install of a package version released in the last few days (LLM03: Supply Chain; guard_fresh.py)

Checked on this computer; only fresh_release looks anything up online (the package's name, at its registry).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, unquote

RUNAWAY, PROMPT_LINES = 8, 3
_URL = re.compile(r"https?://[^\s'\"<>`)\]]+", re.I)
_BLOB = re.compile(r"[A-Za-z0-9+/=_%.-]{120,}")
_LINES = re.compile(r"%0[AD]", re.I)
_LOCAL = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(?:[:/]|$)", re.I)
_RULE_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".github/copilot-instructions.md",
               ".claude/CLAUDE.md", "CLAUDE.local.md")
_RULE_DIRS = (".cursor/rules", ".github/instructions")
_RULE_NAME = re.compile(r"(?:^|[\s'\"=@<:/\\])(?:CLAUDE(?:\.local)?|AGENTS|GEMINI|MEMORY|SKILL)\.md\b|\.cursorrules\b|"
                        r"copilot-instructions\.md|\.cursor[\\/]rules|\.github[\\/]instructions", re.I)
# a rule file uploaded: curl -F f=@CLAUDE.md, gh gist create AGENTS.md, scp, cat CLAUDE.md | curl ...
_UPLOAD = re.compile(r"(?:@|\b(?:gist\s+create|scp|--upload-file|--body-file|--data-binary)\s+|\s-T\s+)[^\s|;&'\"]*"
                     r"(?:CLAUDE(?:\.local)?|AGENTS|GEMINI|MEMORY)\.md\b|\b(?:cat|type|Get-Content|gc)\s+[^|;&\n]*"
                     r"(?:CLAUDE|AGENTS|GEMINI|MEMORY)\.md[^|;&\n]*\|\s*(?:curl|wget|nc|ncat|Invoke-WebRequest|iwr|irm)\b", re.I)
_ACTS = re.compile(r"\b(?:curl|wget|Invoke-WebRequest|iwr|irm|Invoke-Expression|iex|eval|npx|uvx|ssh|scp|nc|ncat|"
                   r"base64\s+-d|(?:ba)?sh\s+-c|powershell(?:\.exe)?\s+-e\w*|pip3?\s+install|npm\s+(?:i|install)|"
                   r"git\s+push)\b", re.I)
# waiting for CI or a status: polling looks the same each time and is not a loop (replay 2026-11-19)
_POLL = re.compile(r"^\s*(?:gh\s+(?:run\s+(?:view|watch|list)|pr\s+(?:checks|view|status))|git\s+status|sleep)\b[^;&|]*$", re.I)
_SEND_MORE = re.compile(r"\b(?:nc|ncat|netcat|telnet)\b|\bgh\s+(?:gist|issue|pr|release)\s+(?:create|edit|comment|upload)\b|"
                        r"\bcurl\b[^\n|;&]*\s(?:-d|--data\S*|-F|--form|-T|--upload-file)\s", re.I)


def _read(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() and p.stat().st_size < limit else ""
    except OSError:
        return ""


def _env_values(cwd: str) -> list[str]:
    """Values from the project's .env files that would be a leak in a web address (long enough, not an address)."""
    vals = []
    for p in sorted(Path(cwd).glob(".env*"))[:6] if cwd else []:
        for ln in _read(p).splitlines():
            m = re.match(r"\s*(?:export\s+)?[A-Za-z_]\w*\s*=\s*(.*)$", ln)
            v = m.group(1).strip().strip("'\"") if m else ""
            if len(v) >= 12 and " " not in v and "://" not in v and not re.fullmatch(r"[\w.-]+\.[a-z]{2,}(?::\d+)?", v, re.I):
                vals.append(v)
    return vals


def _rule_lines(cwd: str) -> list[tuple[str, str]]:
    """(file, line) for the distinctive lines of the project's rule files and the guard's own note to the agent."""
    from .guard import CONTEXT
    out = [("the guard's note to the agent", CONTEXT[:80])]
    root = Path(cwd) if cwd else None
    files = [root / f for f in _RULE_FILES] + [p for d in _RULE_DIRS for p in sorted((root / d).glob("*"))[:20]] if root else []
    for p in files:
        for ln in _read(p).splitlines():
            s = " ".join(ln.split()).lstrip("-*#> ")
            if len(s) >= 40:
                out.append((p.name, s[:120]))
    return out[:400]


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def _outgoing(a) -> str:
    """The text this step sends out: a web address or search, an MCP tool's arguments, a command that sends data."""
    from .risks import _sends_out
    t = a.target or ""
    if a.kind == "web":
        return unquote(t)
    if (a.tool or "").startswith("mcp__"):
        try:
            return "\n".join(_strings(json.loads(t)))
        except ValueError:
            return t
    if a.kind == "command" and (_sends_out(a) or _SEND_MORE.search(t)):
        return t
    return ""


def url_leak(a, cwd: str, net: bool = True) -> str:
    """Why this step's web address leaks data ('' when it does not). `net`: the step uses the network."""
    if a.kind == "web" and not re.match(r"https?://", a.target or ""):
        urls, search = [], a.target or ""                     # a web search: its words go to the search site
    elif a.kind == "web" or (a.kind == "command" and net) or (a.tool or "").startswith("mcp__"):
        urls, search = [u for u in _URL.findall(a.target or "") if not _LOCAL.match(u)], ""
    else:
        return ""
    vals = _env_values(cwd)
    for text in urls + ([search] if search else []):
        if any(v in text or quote(v, safe="") in text for v in vals):
            return "a value from the project's .env"
    for u in urls:
        rest = u.split("?", 1)[1] if "?" in u else u.split("/", 3)[-1] if u.count("/") >= 3 else ""
        if len(_LINES.findall(u)) >= 3:
            return "several lines of text"
        if any(_BLOB.fullmatch(p) for p in re.split(r"[&=/?#;]", rest)):
            return "a long block of encoded data"
    return ""


def prompt_leak(a, cwd: str) -> str:
    """The rule file this step sends out ('' when none)."""
    out = _outgoing(a)
    if not out:
        return ""
    if a.kind == "command" and (m := _UPLOAD.search(out)):
        return _RULE_NAME.search(m.group(0)).group(0).strip(" '\"=@<:/\\") if _RULE_NAME.search(m.group(0)) else "a rules file"
    flat = " ".join(out.split())
    found = [name for name, line in _rule_lines(cwd) if line in flat]
    return found[0] if len(found) >= PROMPT_LINES else ""


def _acts(a) -> bool:
    """The text this step adds to the agent's memory or rules carries something that acts (see memory_poison)."""
    from .guard_quiet import _OFF, _texts
    from .textflags import injection_signs
    if a.kind == "command":
        new = a.target or ""
    else:
        old, new = _texts(a)
        before = set(old.splitlines())
        new = "\n".join(ln for ln in new.splitlines() if ln not in before)
    return bool(_ACTS.search(new) or _OFF.search(new) or injection_signs(new))


def _own_app(a) -> bool:
    from .guard_post import own_tool_list
    from .guard_scan import own_app_tool
    return own_app_tool(a) or own_tool_list(a)


def _waits(a) -> bool:
    """Waiting on a command that is still running (Codex sends it empty input; Antigravity asks its status): the
    same check again and again is how waiting looks, not a loop (owner 2026-11-19: 20 needless loop warnings)."""
    t = (a.target or "").strip()
    return t in ("", "(checked on a running command)") or (a.label or "").startswith("checked on a running command") \
        or (a.tool or "") in ("write_stdin", "command_status")


def note_step(st: dict, a) -> int:
    """Count this step for the runaway check: how often this exact step ran this turn since a file last changed."""
    from .guard_checks import _step_fp
    if a.kind in ("edit", "write"):
        st["reps"] = {}
        return 0
    if a.kind not in ("command", "web") and not (a.tool or "").startswith("mcp__") or _own_app(a) or \
            (a.kind == "command" and (_POLL.match(a.target or "") or _waits(a))):
        return 0
    reps = st.get("reps") or {}
    fp = _step_fp(a)
    reps[fp] = reps.pop(fp, 0) + 1
    st["reps"] = dict(list(reps.items())[-200:])
    return reps[fp]


def marks(st: dict, a, cwd: str, have: list[str]) -> list[str]:
    """The marks of the five guards for this step (decide() adds them; the user's allow rules are checked there)."""
    from .guard_checks import _step_fp
    out = []
    if (why := url_leak(a, cwd, "network" in have)):
        a.leak = why
        out.append("url_leak")
    if (name := prompt_leak(a, cwd)):
        a.leaked = name
        out.append("prompt_leak")
    if "guardrails" in have and st.get("outside") and _acts(a):
        a.outside_from = st["outside"].get("what", "")
        out.append("memory_poison")
    if (st.get("reps") or {}).get(_step_fp(a), 0) >= RUNAWAY:
        a.reps = st["reps"][_step_fp(a)]
        out.append("runaway")
    if "installs" in have:
        from .guard_fresh import check_on, fresh
        on, days = check_on()
        if on:
            a.fresh, a.unchecked = fresh(a.target or "", days)
            if a.fresh:
                out.append("fresh_release")
    return out


def extra(open_: list[str], a) -> str:
    """What the pause line adds for these marks: the exact thing found."""
    bits = []
    if "url_leak" in open_:
        bits.append(f"{getattr(a, 'leak', 'data')} in the web address")
    if "prompt_leak" in open_:
        bits.append(f"{getattr(a, 'leaked', 'a rules file')} in what it sends")
    if "memory_poison" in open_:
        bits.append(f"after {getattr(a, 'outside_from', 'outside content') or 'outside content'} came in")
    if "runaway" in open_:
        bits.append(f"{getattr(a, 'reps', RUNAWAY)} times now, with no file changed in between")
    if "fresh_release" in open_:
        bits.append(f"{', '.join(getattr(a, 'fresh', [])[:3])}: a hijacked package often ships a poisoned new version; "
                    "pin an older version or wait a few days")
    return "".join(f" ({b})" for b in bits)
