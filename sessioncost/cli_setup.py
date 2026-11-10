"""sessioncost setup: the /sessioncost and /liveguard skills put into every agent found on this computer, and
how each agent is found."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .names import GUARD_CMD, OLD_GUARD_CMDS



SKILL = Path(__file__).parent / "skill" / "SKILL.md"
LIVE_SKILL = Path(__file__).parent / "skill" / f"{GUARD_CMD}.md"
SKILL_HOMES = [   # (agent, its folder, where its personal skills live inside it, its name for `guard setup`)
    ("Claude Code", ".claude", "skills", "claude"),
    ("Codex", ".codex", "skills", "codex"),
    ("Antigravity", ".gemini", "config/skills", "antigravity"),
    ("Cursor", ".cursor", "skills", "cursor"),
    ("Copilot", ".copilot", "skills", "copilot"),     # VS Code's Copilot Chat and Copilot CLI both read ~/.copilot/skills
]


def agent_installed(folder: str, home: Path) -> bool:
    """The agent is on this computer. Copilot Chat in VS Code makes no ~/.copilot folder of its own, so its extension
    counts too (owner 2026-11-02: a laptop with only VS Code and Copilot got nothing from setup)."""
    if (home / folder).is_dir():
        return True
    return folder == ".copilot" and any(any((home / ext / "extensions").glob("github.copilot*"))
                                        for ext in (".vscode", ".vscode-insiders"))


def claude_plugin_installed(home: Path) -> bool:
    """True when Claude Code has the SessionCost plugin for every folder, which brings its own /sessioncost. A plugin
    installed for one folder only (scope "local" or "project": owner 2026-11-02, from Manage Plugins) or switched off
    in ~/.claude/settings.json leaves every other chat without it."""
    try:
        plugins = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    try:
        enabled = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8")).get("enabledPlugins") or {}
    except (OSError, ValueError, AttributeError):
        enabled = {}
    return any(k.startswith("sessioncost@") and enabled.get(k) is not False
               and any((e or {}).get("scope", "user") == "user" for e in (v if isinstance(v, list) else [v]))
               for k, v in (plugins.get("plugins") or {}).items())


def plugin_installed(folder: str, home: Path) -> bool:
    """True when this agent has the SessionCost plugin, which brings its own /sessioncost (2026-10-16: Cursor and Codex
    showed two /sessioncost, the plugin's and a copy setup had written)."""
    if folder == ".claude":
        return claude_plugin_installed(home)
    plugins = home / folder / "plugins"
    if folder == ".codex":
        return (plugins / "cache").is_dir() and any((plugins / "cache").glob("*/sessioncost"))
    if folder == ".cursor":
        return plugins.is_dir() and any(p.is_dir() for p in plugins.glob("**/sessioncost"))
    return False


def setup(home: Path | None = None) -> list[tuple[str, Path | None]]:
    """Add the /sessioncost and /liveguard skills to every agent installed for this user (/liveguard runs that
    agent's `guard setup`). Returns (agent, the /sessioncost skill file written), with None for an agent whose
    SessionCost plugin already provides both."""
    home = home or Path.home()
    # The skills name this exact Python, so they run even when no 'python' or 'sessioncost' is on the PATH.
    exe = Path(sys.executable).as_posix()
    run = f'"{exe}" -m sessioncost' if " " in exe else f"{exe} -m sessioncost"
    fill = lambda f: f.read_text(encoding="utf-8").replace("{{RUN}}", run).replace("{{VERSION}}", __version__)
    text, live = fill(SKILL), fill(LIVE_SKILL)
    if " " in exe:
        text = text.replace("   If that fails because", "   In PowerShell, put `& ` in front of the quoted path. If that fails because")
        live = live.replace("     Add ` strict`", "     In PowerShell, put `& ` in front of the quoted path. Add ` strict`")
    done = []
    for agent, folder, sub, guard_name in SKILL_HOMES:
        if not agent_installed(folder, home):
            continue
        skills = {"sessioncost": text}
        if guard_name:
            skills[GUARD_CMD] = live.replace("{{AGENT}}", agent).replace("{{SETUP}}", guard_name)
        # an earlier name of the guard command (/livesession until 2026-11-09): the copy an earlier setup wrote goes
        for was in OLD_GUARD_CMDS:
            old = home / folder / sub / was / "SKILL.md"
            if old.is_file() and f"name: {was}" in old.read_text(encoding="utf-8", errors="replace"):
                old.unlink()
                if not any(old.parent.iterdir()):
                    old.parent.rmdir()
        if plugin_installed(folder, home):
            # Two /sessioncost in one agent confuse people: remove copies an earlier setup wrote, add none.
            for name in skills:
                dest = home / folder / sub / name / "SKILL.md"
                if dest.is_file() and f"name: {name}" in dest.read_text(encoding="utf-8", errors="replace"):
                    dest.unlink()
                    if not any(dest.parent.iterdir()):
                        dest.parent.rmdir()
            done.append((agent, None))
            continue
        for name, body in skills.items():
            dest = home / folder / sub / name / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
        done.append((agent, home / folder / sub / "sessioncost" / "SKILL.md"))
    return done


def cmd_setup() -> int:
    done = setup()
    if not done:
        print("No Claude Code, Codex, Antigravity, Cursor or GitHub Copilot found for this user; nothing to set up. "
              "Install your agent, then run setup again.")
        return 1
    for agent, dest in done:
        if not dest:
            print(f"{agent:12} /sessioncost comes from the SessionCost plugin; nothing added")
        else:
            print(f"{agent:12} /sessioncost and /{GUARD_CMD} added ({dest.parent.parent})")
    # The guard goes into every agent now, not at its first /liveguard: the switch is one for all agents, so turning
    # it on in one guards them all (owner 2026-11-02). Off, the hooks only look at the switch and let each step pass.
    from . import guard
    names = {a: g for a, _, _, g in SKILL_HOMES}
    # an agent's plugin brings the commands; only Claude Code's brings the guard's hooks too
    ready = [(a, names[a]) for a, dest in done if dest or a != "Claude Code"]
    for agent, name in ready:
        guard.setup(name)
        print(f"{agent:12} live guard ready (off until you type /{GUARD_CMD} in any agent)"
              + ("; Codex asks you once to trust new hooks: type /hooks there" if name == "codex" else ""))
    for name in guard.refresh():            # hooks an older version wrote get this version's commands
        if name not in {n for _, n in ready}:
            print(f"{name.capitalize():12} live guard hooks brought up to date")
    print(f"\nRestart the agent if it is open, then type /sessioncost in a chat. /{GUARD_CMD} turns the live guard on.")
    return 0
