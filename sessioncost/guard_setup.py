"""Turning the guard on and off, and putting its hooks into Claude Code, Codex, Cursor, Antigravity and Copilot."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .guard_agents import COPILOT_EVENTS, CURSOR_GATES
from .guard_auto import reports_on
from .guard_store import _read, _write, home, project_allow_path
from .names import GUARD_CMD

# ---- turning it on and off ----
SETTINGS = {"fail-closed": ("fail_closed", "If the guard itself breaks, every step is blocked until it is fixed.",
                            "If the guard itself breaks, never-list steps are still blocked and the rest pass."),
            "allow-list": ("allow_list", "Every step that changes something (a command that is not look-only, an edit, a "
                           "write, the web, an MCP tool) asks you unless the project's .sessioncost-allow lists it "
                           "(a line like `step  pytest *`); reads and searches pass. What you allow is remembered for "
                           "that exact step in this chat.",
                           "Only risky steps ask you.")}
# never allowed by a saved project rule: keys stay out of the chat whatever the rule says
NO_PROJECT_ALLOW = {"secrets", "key_in_output"}
def command(arg: str, cwd: str, strict: bool = False, rule: tuple[str, str] | None = None) -> str:
    cfg = home() / "config.json"
    keep = {k: v for k, v in _read(cfg, {}).items() if k in ("auto", "fix_auto", "long_chat", "budget_usd")}   # reports, fix auto, budgets: your choice stays
    if arg == "allow" and rule:
        from .risks import NEVER, SEVERITY
        mark, pat = rule[0].strip(), rule[1].strip()
        if (mark not in SEVERITY and mark != "step") or mark in NEVER or mark in NO_PROJECT_ALLOW or not pat:
            return (f"SessionCost guard: cannot allow '{mark}' for a project (never-list marks, secrets and unknown "
                    "marks cannot be added).")
        p = project_allow_path(cwd)
        data = _read(p, {"project": cwd, "rules": []})
        rules = [r for r in (data.get("rules") or []) if isinstance(r, list) and len(r) == 2]
        if [mark, pat] not in rules:
            rules.append([mark, pat])
        _write(p, {"project": cwd, "rules": rules})
        return f"SessionCost guard: allowed for this project: {mark}  {pat} (saved in {p})."
    if arg == "allow-list-rules":
        from .risks import read_allow
        rules = read_allow(cwd)
        if not rules:
            return "SessionCost guard: no project allow rules set here."
        return "SessionCost guard allow rules for this project:\n" + "\n".join(f"  {m}  {pt}" for m, pt in rules)
    if arg == "allow-clear":
        p = project_allow_path(cwd)
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
        return "SessionCost guard: cleared saved allow rules for this project."
    if arg in ("on", "start"):                # one switch: every chat, open ones from their next step, until off
        have = _read(cfg, {})
        strict = strict or bool(have.get("on") and have.get("strict"))     # turning it on again never lowers strict
        _write(cfg, {**have, "on": True, "strict": strict})
        return (f"SessionCost guard: on{' (strict)' if strict else ''} for every chat, this one included, until you type "
                f"/{GUARD_CMD} off (or run `sessioncost guard off`). Every agent `sessioncost setup` gave the guard.")
    if arg == "off":
        _write(cfg, {**keep, "on": False})
        for p in home().glob("*.state.json"):
            try:
                p.unlink()
            except OSError:
                pass
        return f"SessionCost guard: off for every chat. /{GUARD_CMD} turns it on again. The logs stay in " + str(home())
    if arg.rsplit("-", 1)[0] in SETTINGS and arg.endswith(("-on", "-off")):
        name, on = arg.rsplit("-", 1)[0], arg.endswith("-on")
        _write(cfg, {**_read(cfg, {}), SETTINGS[name][0]: on})
        return f"SessionCost guard: {name} {'on' if on else 'off'}. {SETTINGS[name][1 if on else 2]}"
    if arg in ("auto-on", "auto-off"):        # auto: the guard on for every chat, and a report after each answer
        on = arg == "auto-on"
        have = _read(cfg, {})
        _write(cfg, {**have, "on": True if on else have.get("on", False), "auto": on, "strict": strict or have.get("strict", False)})
        return ("SessionCost auto: on. Every new chat is guarded, and its report is built after each answer in "
                f"{Path.home() / '.sessioncost' / 'reports'} (nothing to type). `sessioncost guard auto off` stops the reports."
                if on else "SessionCost auto: off. No more reports after each answer; the guard stays as it was "
                "(`sessioncost guard off` turns it off).")
    if arg in ("reports-on", "reports-off"):  # `sessioncost auto on|off`: only the reports, the guard as it was (#14)
        on = arg == "reports-on"
        _write(cfg, {**_read(cfg, {}), "auto": on})
        return (f"SessionCost auto: on. Each chat's report is rebuilt after each answer in {Path.home() / '.sessioncost' / 'reports'} "
                "(at most once a minute; press F5 on the open report). `sessioncost auto off` stops it." if on else
                "SessionCost auto: off. Reports are built only when you run /sessioncost. `sessioncost auto on` starts it again.")
    c = _read(cfg, {})
    live = len(list(home().glob("*.state.json"))) if home().exists() else 0
    extra = "".join(f", {n}" for n, (k, *_) in SETTINGS.items() if c.get(k)) + (", reports after each answer" if reports_on() else "")
    return f"SessionCost guard: {'on for every chat' if c.get('on') else 'off'}{extra}; {live} chat(s) guarded now. Logs: {home()}"



# ---- putting the guard into Claude Code, Codex, Cursor, Antigravity and Copilot CLI ----
# Claude Code: the plugin brings the hooks; without it (npm or Python install) `guard setup claude` writes them into
# ~/.claude/settings.json, so every way of installing gives both /sessioncost and /liveguard.
def _path_arg(p: str) -> str:
    """A path for a hook command, quoted only when it must be: Antigravity hands the command to cmd.exe with every `"`
    escaped as `\\"`, which cmd cannot read. A Windows path with spaces gets its short (8.3) form when there is one."""
    if not re.search(r'[\s"&()^|<>]', p):
        return p
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetShortPathNameW(p, buf, len(buf)) and not re.search(r'[\s"&()^|<>]', buf.value):
                return buf.value
        except (OSError, AttributeError):
            pass
    return f'"{p}"'


def _launch(event: str, agent: str) -> str:
    root = Path(__file__).resolve().parent.parent
    g = root / "guard.py"
    if agent == "claude":       # Claude Code runs hooks in bash, also on Windows: forward slashes, always quoted
        run = f'"{g.as_posix()}"' if g.is_file() else "-m sessioncost.guard"
        exe = Path(sys.executable)
        # python.exe flashes a console window on Windows at every hook; pythonw.exe opens none and still reads and
        # answers through the pipes (Antigravity gets no answer from it, so only Claude Code uses it)
        quiet = exe.with_name("pythonw.exe")
        if os.name == "nt" and exe.name.lower() == "python.exe" and quiet.is_file():
            exe = quiet
        return f'"{exe.as_posix()}" {run} hook {event} claude'
    run = _path_arg(str(g)) if g.is_file() else "-m sessioncost.guard"
    return f'{_path_arg(sys.executable)} {run} hook {event} {agent}'


def _backup(p: Path) -> None:
    """Keep the user's file as it was before the first setup: a later setup (/liveguard runs it again) keeps that copy."""
    old = p.with_name(p.name + ".before-sessioncost")
    if p.exists() and not old.exists():
        old.write_bytes(p.read_bytes())


def setup(agent: str) -> str:
    """Add the guard's hooks to the agent's hooks file, keeping every other hook (a backup is kept)."""
    if agent == "claude":
        from .cli import claude_plugin_installed
        p = Path.home() / ".claude" / "settings.json"
        plugin = claude_plugin_installed(Path.home())     # its hooks already run: ours too would check each step twice
        ours = {} if plugin else {
            ev: [{"hooks": [{"type": "command", "command": _launch(e, "claude"), "timeout": 10}]}]
            for ev, e in (("SessionStart", "session-start"), ("UserPromptSubmit", "prompt"), ("PreToolUse", "pre"),
                          ("PostToolUse", "post"), ("PostToolUseFailure", "fail"), ("Stop", "stop"), ("SessionEnd", "end"))}
        if plugin:
            if not p.exists() or "sessioncost" not in p.read_text(encoding="utf-8", errors="replace").lower():
                return "SessionCost guard: Claude Code has the SessionCost plugin, which brings the guard; nothing added."
            ours = {ev: [] for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                                      "PostToolUseFailure", "Stop", "SessionEnd")}
    elif agent == "codex":
        p = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"
        ours = {ev: [{**({"matcher": ".*"} if ev in ("PreToolUse", "PostToolUse") else {}),
                      "hooks": [{"type": "command", "command": _launch(e, "codex"), "timeout": 10}]}]
                for ev, e in (("SessionStart", "session-start"), ("UserPromptSubmit", "prompt"), ("PreToolUse", "pre"),
                              ("PostToolUse", "post"), ("Stop", "stop"))}
    elif agent == "cursor":
        p = Path.home() / ".cursor" / "hooks.json"
        ours = {ev: [{"command": _launch("gate" if ev in CURSOR_GATES else "note", "cursor"), "timeout": 10}]
                for ev in ("sessionStart", "beforeShellExecution", "afterShellExecution", "beforeMCPExecution",
                           "afterMCPExecution", "beforeReadFile", "stop", "sessionEnd")}
    elif agent == "antigravity":              # one named hook of its own; every other named hook is kept
        p = Path.home() / ".gemini" / "config" / "hooks.json"
        run = lambda e: [{"type": "command", "command": _launch(e, "antigravity"), "timeout": 10}]
        have = _read(p, {}) if p.exists() else {}
        _backup(p)
        have["sessioncost-guard"] = {"enabled": True, "SessionStart": run("session-start"), "PreInvocation": run("invocation"),
                                     "PreToolUse": [{"matcher": "*", "hooks": run("pre")}],
                                     "PostToolUse": [{"matcher": "*", "hooks": run("post")}], "Stop": run("stop")}
        _write(p, have)
        return (f"SessionCost guard: added to {p} (your other hooks kept; the old file is next to it). Restart Antigravity. "
                "The guard is on when `sessioncost guard on` is set; check it under Customizations > Hooks.")
    elif agent == "copilot":                  # a file of its own in ~/.copilot/hooks: every other hook file is left alone
        from .readers.copilot import copilot_home
        p = copilot_home() / "hooks" / "sessioncost-guard.json"
        run = lambda e: {"type": "command", "bash": _launch(e, "copilot"), "powershell": "& " + _launch(e, "copilot"),
                         "timeoutSec": 15 if e == "session-start" else 10}
        p.parent.mkdir(parents=True, exist_ok=True)
        _write(p, {"version": 1, "hooks": {ev: [run(e)] for ev, e in COPILOT_EVENTS.items()}})
        return (f"SessionCost guard: added as {p} (your other hooks are untouched; delete that file to take it out). "
                "Restart Copilot CLI. The guard is on when `sessioncost guard on` is set. A check that takes longer "
                "than 10 s lets the step run (Copilot's rule), so a very slow computer weakens it.")
    else:
        return "SessionCost guard: setup is for claude, codex, cursor, antigravity or copilot."
    have = _read(p, {}) if p.exists() else {}
    _backup(p)
    hooks = have.get("hooks") if isinstance(have.get("hooks"), dict) else {}
    mine = lambda entry: "sessioncost" in json.dumps(entry).lower() and " hook " in json.dumps(entry)
    for ev, entries in ours.items():
        hooks[ev] = [x for x in (hooks.get(ev) or []) if not mine(x)] + entries
        if not hooks[ev]:
            del hooks[ev]
    out = {**have, "hooks": hooks, **({"version": have.get("version", 1)} if agent == "cursor" else {})}
    _write(p, out)
    if agent == "claude":
        if not ours or not any(ours.values()):
            return (f"SessionCost guard: Claude Code has the SessionCost plugin, which brings the guard; the copy in {p} "
                    "was taken out so steps are not checked twice.")
        return (f"SessionCost guard: added to {p} (your other hooks and settings kept; the old file is next to it). "
                "Restart Claude Code: the guard checks steps from the next chat on.")
    note = (" Codex asks you to review new hooks: run /hooks in Codex once and trust them." if agent == "codex" else
            " Restart Cursor. The guard is on when `sessioncost guard on` is set (Cursor's hooks cannot read "
            f"/{GUARD_CMD}: switch it in a terminal).")
    return f"SessionCost guard: added to {p} (your other hooks kept; the old file is next to it).{note}"


def _hooks_file(agent: str) -> Path:
    if agent == "claude":
        return Path.home() / ".claude" / "settings.json"
    if agent == "codex":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"
    if agent == "cursor":
        return Path.home() / ".cursor" / "hooks.json"
    if agent == "antigravity":
        return Path.home() / ".gemini" / "config" / "hooks.json"
    from .readers.copilot import copilot_home
    return copilot_home() / "hooks" / "sessioncost-guard.json"


def refresh() -> list[str]:
    """Write the guard's hooks again for each agent that already has them, so an update (and `sessioncost setup`)
    replaces hook commands an older version wrote. Returns the agents refreshed."""
    done = []
    for agent in ("claude", "codex", "cursor", "antigravity", "copilot"):
        try:
            text = _hooks_file(agent).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "sessioncost" in text.lower() and " hook " in text:
            setup(agent)
            done.append(agent)
    return done
