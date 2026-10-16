"""`sessioncost update`: bring every copy of SessionCost on this computer up to date, the right way for each.

SessionCost can be installed several ways, and each updates differently (checked 2026-10-16):
  - the install line (uv tool):       update the package inside its own environment from the public repo (not a
                                      reinstall, which would have to replace the Python that is running);
  - pip:                              pip install -U sessioncost;
  - the Claude Code plugin:           claude plugin marketplace update, then claude plugin update (Claude Code does
                                      not update plugins from our marketplace by itself unless you turn that on);
  - the Codex plugin:                 codex plugin marketplace upgrade;
  - the Cursor plugin:                no command: Cursor's marketplace has a Refresh button.
Then `setup` runs again, so the /sessioncost skill in each agent names the new version.

Nothing is read or sent from your sessions; the commands only fetch SessionCost itself.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PUBLIC_ZIP = "https://github.com/kish21/sessioncost/archive/refs/heads/main.zip"   # what the installers use
WINDOWS = sys.platform == "win32"


@dataclass
class Step:
    what: str                      # plain words
    argv: list[str] | None = None  # the command; None = something the user does by hand (shown, not run)
    manual_after: bool = False     # Windows cannot replace a running .exe: shown for the user to run afterwards


def _plugin_keys(home: Path) -> list[str]:
    try:
        plugins = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [k for k in (plugins.get("plugins") or {}) if k.startswith("sessioncost@")]


def plan(home: Path | None = None, exe: str | None = None, here: Path | None = None, which=shutil.which,
         windows: bool = WINDOWS, launcher: str | None = None) -> list[Step]:
    """The steps for this computer. `exe` is the Python running SessionCost, `here` this package's folder,
    `launcher` the program the user started (sys.argv[0])."""
    home = home or Path.home()
    exe = exe or sys.executable
    here_s = (here or Path(__file__).resolve().parent).as_posix().lower()
    started = Path(launcher if launcher is not None else sys.argv[0]).name.lower()
    steps: list[Step] = []
    in_plugin = "/plugins/cache/" in here_s                    # this run is a plugin's own bundled copy
    if not in_plugin:
        if "/uv/tools/sessioncost/" in Path(exe).as_posix().lower() and which("uv"):
            steps.append(Step("Update SessionCost (installed with the install line)",
                              [which("uv"), "pip", "install", "--quiet", "--python", exe, "--reinstall-package",
                               "sessioncost", PUBLIC_ZIP]))
        else:
            steps.append(Step("Update SessionCost (installed with pip)",
                              [exe, "-m", "pip", "install", "--quiet", "-U", "sessioncost"],
                              manual_after=windows and started == "sessioncost.exe"))   # pip would replace it
    for key in _plugin_keys(home):
        market = key.split("@", 1)[1]
        claude = which("claude")              # the full path: on Windows it is a .cmd launcher
        if claude:
            steps.append(Step("Claude Code plugin: fetch the newest version",
                              [claude, "plugin", "marketplace", "update", market]))
            steps.append(Step("Claude Code plugin: install it", [claude, "plugin", "update", key]))
        else:
            steps.append(Step("Claude Code plugin: in Claude Code, type /plugin, open Installed, choose sessioncost, "
                              "then Update now"))
        steps.append(Step("Claude Code plugin: to get every new version by itself, type /plugin, open Marketplaces, "
                          f"choose {market}, then Enable auto-update"))
    codex_cache = home / ".codex" / "plugins" / "cache"
    if codex_cache.is_dir() and any(codex_cache.glob("*/sessioncost")):
        codex = which("codex")
        steps.append(Step("Codex plugin: fetch the newest version", [codex, "plugin", "marketplace", "upgrade"])
                     if codex else Step("Codex plugin: run `codex plugin marketplace upgrade`"))
    cursor_plugins = home / ".cursor" / "plugins"
    if cursor_plugins.is_dir() and any(cursor_plugins.glob("**/sessioncost")):
        steps.append(Step("Cursor plugin: in Cursor, open Customize, find the marketplace with SessionCost, then Refresh"))
    if not in_plugin:
        steps.append(Step("Refresh /sessioncost in each agent", [exe, "-m", "sessioncost", "setup"]))
    return steps


def run(dry_run: bool = False) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)   # our step names before each command's own output
    except (AttributeError, ValueError):
        pass
    steps = plan()
    failed, later = 0, []          # later: commands the user runs once this SessionCost has closed
    for n, st in enumerate(steps, 1):
        print(f"{n}. {st.what}")
        if st.argv is None or dry_run:
            if st.argv:
                print("   " + " ".join(st.argv))
            continue
        if st.manual_after:
            later.append(st.argv)
            continue
        try:
            ok = subprocess.run(st.argv).returncode == 0
        except OSError as e:
            ok = False
            print(f"   could not run it: {e}")
        if not ok:
            failed += 1
            print("   that step did not work; the rest still ran")
    if dry_run:
        print("\n(dry run: nothing was changed)")
        return 0
    for argv in later:
        print("\nSessionCost cannot replace itself while it runs. Close it, then run:\n   " + " ".join(argv))
    print("\nRestart your agent so it picks up the new version.")
    return 1 if failed else 0
