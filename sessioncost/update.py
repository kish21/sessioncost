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
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PUBLIC_ZIP = "https://github.com/kish21/sessioncost/archive/refs/heads/main.zip"   # what the installers use
NPX_HOME = "/.sessioncost/npm/"    # where `npx sessioncost` keeps its own Python environment (npm/bin/sessioncost.js)
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
        if NPX_HOME in Path(exe).as_posix().lower():          # npx's own environment: npx installs the new one
            steps.append(Step("Update SessionCost (run with npx): run `npx sessioncost@latest` once"))
        elif "/uv/tools/sessioncost/" in Path(exe).as_posix().lower() and which("uv"):
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


SKILL_VERSION = re.compile(r"\(SessionCost ([^\s)]+)\)")   # the skill's description names its version
PROBE = ("import os, sys, sessioncost as s; print(sys.executable); print(s.__version__); "
         "print(os.path.dirname(s.__file__))")       # the version it reports itself, as `sessioncost --version` does


def _json_version(f: Path) -> str | None:
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def _probe(argv: list[str]) -> list[str] | None:
    """Another program's output lines, or None when it fails or takes too long (Store Python starts in ~2 s). Run from
    the temp folder: a project folder holding sessioncost itself would be found first."""
    import tempfile
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15, cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError):
        return None
    return [x.strip() for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else None


def copies(home: Path | None = None, exe: str | None = None, here: Path | None = None, which=shutil.which,
           probe=_probe) -> list[tuple[str, str, str]]:
    """Every copy of SessionCost found on this computer: (what, version, where). The first is the one running."""
    from . import __version__
    from .cli import SKILL_HOMES
    home = home or Path.home()
    exe = exe or sys.executable
    here = here or Path(__file__).resolve().parent
    found = [("This copy (running now)", __version__, str(here))]
    same = lambda a, b: Path(a).resolve() == Path(b).resolve()
    python = which("python")                       # the terminal's own Python, when it is not this one
    if python and (out := probe([python, "-c", PROBE])) and len(out) == 3 and not same(out[2], here):
        found.append(("Terminal (python)", out[1], out[0]))
    cmd = which("sessioncost")                     # a sessioncost command on the PATH from another Python
    if cmd and not same(Path(cmd).parent, Path(exe).parent) and not same(Path(cmd).parent, Path(exe).parent / "Scripts"):
        if (out := probe([cmd, "--version"])) and out[0].startswith("sessioncost "):
            # inside `npx sessioncost`, npm puts its own launcher first on the PATH: not a copy you can type later
            npx = "_npx" in Path(cmd).parts
            found.append(("npx launcher (temporary)" if npx else "Terminal (sessioncost)", out[0].split()[-1], cmd))
    for key in _plugin_keys(home):
        try:
            entries = json.loads((home / ".claude" / "plugins" / "installed_plugins.json")
                                 .read_text(encoding="utf-8"))["plugins"][key]
        except (OSError, ValueError, KeyError):
            entries = []
        for e in entries or [{}]:
            found.append(("Claude Code plugin", e.get("version") or "?", e.get("installPath") or key))
    for d in sorted((home / ".codex" / "plugins" / "cache").glob("*/sessioncost/*")):
        if d.is_dir():
            found.append(("Codex plugin", _json_version(d / ".codex-plugin" / "plugin.json") or d.name, str(d)))
    cursor = home / ".cursor" / "plugins"
    for f in sorted(cursor.glob("**/sessioncost/**/.cursor-plugin/plugin.json")) if cursor.is_dir() else []:
        if (v := _json_version(f)) and not any(c[0] == "Cursor plugin" and c[1] == v for c in found):
            found.append(("Cursor plugin", v, str(f.parent.parent)))
    for agent, folder, sub, _ in SKILL_HOMES:      # the /sessioncost skill `setup` wrote into each agent
        f = home / folder / sub / "sessioncost" / "SKILL.md"
        try:
            m = SKILL_VERSION.search(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        found.append((f"{agent} /sessioncost", m.group(1) if m else "?", str(f)))
    # a plugin's folder holding the running copy is that copy, not another one
    return found[:1] + [c for c in found[1:] if not (same(c[2], here) or same(c[2], here.parent))]


def kind(here: Path) -> str:
    """What the running copy is, in the user's words."""
    p = here.as_posix().lower()
    for folder, name in ((".claude", "Claude Code plugin"), (".codex", "Codex plugin"), (".cursor", "Cursor plugin")):
        if f"/{folder}/plugins/" in p:
            return name
    return "npx" if NPX_HOME in p else "Python package"


def show_versions(every: bool = False) -> int:
    """This copy only, by default: in a chat, the other agents' copies are noise (owner 2026-10-31). --all lists
    every copy."""
    here = Path(__file__).resolve().parent
    found = copies(here=here)
    if not every:
        others = found[1:]
        print(f"SessionCost {found[0][1]} ({kind(here)}): {found[0][2]}")
        if others:
            differ = sum(v != found[0][1] for _, v, _ in others)
            print(f"{len(others)} other {'copy' if len(others) == 1 else 'copies'} on this computer"
                  f"{f', {differ} on another version' if differ else ''}. All of them: sessioncost version --all")
        return 0
    print("SessionCost on this computer:")
    width = max(len(w) for w, _, _ in found)
    for what, v, where in found:
        print(f"  {what:{width}}  {v:14}  {where}")
    other = [w for w, v, _ in found[1:] if v != found[0][1]]
    if other:
        print(f"\nNot the same version as this copy: {', '.join(other)}. `sessioncost update` updates them "
              "(a plugin can also be updated in its agent).")
    return 0


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
