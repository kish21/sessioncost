"""A fresh chat after the handover (1.2.4): a link that opens a new chat with "continue from handover" filled in.

Only for the apps where the owner clicked the link and it opened a new chat with the text (2026-11-21): Claude Code
in VS Code, Claude Code in a terminal, Claude Desktop, and Cursor. Codex's link opens the app only, and Antigravity
and Copilot have none: there the user starts the new chat, as before. Which Claude Code it is comes from what Claude
Code tells the programs it starts (CLAUDE_CODE_ENTRYPOINT, and the extension's folder for VS Code).

Off by default, the agent shows the link with the handover. `sessioncost fix fresh-chat on` (opt-in) has the agent
open it by itself once the handover is written (`sessioncost fix open-chat`)."""
from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import quote

SAY = "continue from handover"


def link(agent: str = "", project: str = "") -> str:
    """The link that opens a fresh chat in this agent's app, or "" when there is none."""
    a, q = (agent or "").lower(), quote(SAY)
    if a.startswith("cursor"):
        return f"cursor://anysphere.cursor-deeplink/prompt?text={q}"
    if not (a.startswith("claude") or (not a and os.environ.get("CLAUDECODE"))):
        return ""
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").lower()
    where = os.environ.get("CLAUDE_CODE_EXECPATH", "").replace("\\", "/").lower()
    if entry == "claude-vscode":              # VS Code only: in Cursor or another editor this link opens VS Code
        return f"vscode://anthropic.claude-code/open?prompt={q}" if "/.vscode/extensions/" in where else ""
    if "desktop" in entry:
        return f"claude://code/new?q={q}" + (f"&folder={quote(project)}" if project else "")
    if entry == "cli":
        return f"claude-cli://open?q={q}" + (f"&cwd={quote(project)}" if project else "")
    return ""


def _config():
    from .guard_store import _read, home
    p = home() / "config.json"
    data = _read(p, {})
    return p, data if isinstance(data, dict) else {}


def auto_on() -> bool:
    return bool(_config()[1].get("fresh_chat"))


def handover_words(agent: str, project: str) -> str:
    """Added to the handover instruction: open the fresh chat (opt-in), or give the user its link."""
    url = link(agent, project)
    if not url:
        return ""
    if auto_on():
        from .fixer import _q, run_cmd
        return (f" Once the handover is written, run `{run_cmd()} fix open-chat --agent {agent} --project "
                f"{_q(project)}`: it opens a fresh chat with \"{SAY}\" filled in.")
    return f" Give them this link too, on its own line: it opens a fresh chat with \"{SAY}\" filled in: {url}"


def _open(url: str) -> bool:
    try:
        if os.name == "nt":
            os.startfile(url)                 # noqa: S606 - our own link, built above
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def main(argv: list[str]) -> int:
    """`fix fresh-chat [on|off]` and `fix open-chat [--agent A] [--project P]`."""
    if argv[:1] == ["fresh-chat"]:
        if argv[1:2] in (["on"], ["off"]):
            from .guard_store import _write
            p, data = _config()
            _write(p, {**data, "fresh_chat": argv[1] == "on"})
        state = "on: after the handover, your agent opens a fresh chat by itself" if auto_on() else \
            "off: after the handover, your agent gives you a link that opens a fresh chat"
        print(f"SessionCost fix: fresh chat after the handover is {state} (Claude Code and Cursor; other agents: "
              f'start a new chat and say "{SAY}"). To change it, say "open the fresh chat for me" or "stop opening '
              'the fresh chat" (or type `sessioncost fix fresh-chat on` / `off`).')
        return 0
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv[:-1] else ""
    project = argv[argv.index("--project") + 1] if "--project" in argv[:-1] else os.getcwd()
    url = link(agent, project)
    if url and _open(url):
        print(f'SessionCost: opened a fresh chat with "{SAY}" filled in. Send it there.')
        return 0
    print(f'SessionCost: this app has no link for a fresh chat. Start a new chat and say "{SAY}".')
    return 1
