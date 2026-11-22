"""`sessioncost uninstall`: take SessionCost out of this computer in one go, with a backup of everything it moves.

It removes the guard's hooks from all five agents (every other hook is kept), the /sessioncost and /liveguard skills
that `sessioncost setup` wrote, and ~/.sessioncost (reports, guard logs, settings). Nothing is deleted: each file or
folder goes into ~/sessioncost-backup-<date>, so the user can put any of it back. What only the user can remove (an
agent's plugin, the editor extension, the program itself) is listed at the end. It asks first, in the terminal; an
agent cannot answer that question, and the guard blocks an agent from running it (it switches the guard off)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from .cli_setup import SKILL_HOMES
from .names import GUARD_CMD, OLD_GUARD_CMDS


def _hook_files(home: Path) -> list[tuple[str, Path]]:
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    copilot = Path(os.environ.get("COPILOT_HOME") or home / ".copilot")
    return [("Claude Code", home / ".claude" / "settings.json"), ("Codex", codex / "hooks.json"),
            ("Cursor", home / ".cursor" / "hooks.json"), ("Antigravity", home / ".gemini" / "config" / "hooks.json"),
            ("Copilot", copilot / "hooks" / "sessioncost-guard.json")]


def _ours(entry) -> bool:
    text = json.dumps(entry).lower()
    return "sessioncost" in text and " hook " in text


def _without_ours(data: dict) -> dict | None:
    """The hooks file without SessionCost's entries, or None when it has none."""
    found = False
    if isinstance(data.get("sessioncost-guard"), dict):            # Antigravity: one named hook of its own
        data = {k: v for k, v in data.items() if k != "sessioncost-guard"}
        found = True
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        kept = {}
        for ev, entries in hooks.items():
            left = [x for x in entries if not _ours(x)] if isinstance(entries, list) else entries
            found = found or left != entries
            if left:
                kept[ev] = left
        data = {**data, "hooks": kept}
    return data if found else None


def plan(home: Path) -> list[tuple[str, Path, dict | None]]:
    """What would be taken out: (label, path, the file's new content, or None to move the whole file or folder)."""
    out = []
    for agent, p in _hook_files(home):
        if not p.is_file():
            continue
        if p.name == "sessioncost-guard.json":
            out.append((f"{agent}: the guard's hooks file", p, None))
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        new = _without_ours(data) if isinstance(data, dict) else None
        if new is not None:
            out.append((f"{agent}: the guard's hooks (your other hooks stay)", p, new))
    for agent, folder, sub, _ in SKILL_HOMES:
        for name in ("sessioncost", GUARD_CMD, *OLD_GUARD_CMDS):
            skill = home / folder / sub / name / "SKILL.md"
            try:
                if f"name: {name}" in skill.read_text(encoding="utf-8", errors="replace"):
                    out.append((f"{agent}: the /{name} skill", skill.parent, None))
            except OSError:
                pass
    data = home / ".sessioncost"
    if data.is_dir():
        out.append(("SessionCost's own folder: reports, guard logs, settings", data, None))
    return out


def run(home: Path, items: list[tuple[str, Path, dict | None]]) -> tuple[Path, list[str]]:
    """Take each item out, keeping a copy under ~/sessioncost-backup-<date>. Returns the backup folder and failures."""
    backup = home / time.strftime("sessioncost-backup-%Y-%m-%d-%H%M%S")
    failed = []
    for label, p, new in items:
        keep = backup / p.relative_to(home)
        try:
            keep.parent.mkdir(parents=True, exist_ok=True)
            if new is None:
                shutil.move(str(p), str(keep))
            else:
                shutil.copy2(p, keep)
                p.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            failed.append(f"{label}: {p} ({e.strerror or e})")
    return backup, failed


LEFT_FOR_YOU = """Only you can remove these, where you have them:
  Claude Code plugin:  type /plugin in Claude Code, open SessionCost, choose Uninstall
  Codex, Cursor plugin: the agent's plugin list (Codex: Plugins; Cursor: Settings > Plugins), remove SessionCost
  Editor extension:     Extensions panel in VS Code, Cursor or Antigravity: SessionCost > Uninstall
  The program:          uv tool uninstall sessioncost   (or: pip uninstall sessioncost, npm uninstall -g sessioncost)
Restart each agent afterwards."""


def main(argv: list[str]) -> int:
    home = Path.home()
    yes = "--yes" in argv or "-y" in argv
    items = plan(home)
    if not items:
        print("SessionCost: nothing of SessionCost's own found in your agents or your home folder.\n\n" + LEFT_FOR_YOU)
        return 0
    print("SessionCost will take out:")
    for label, p, _ in items:
        print(f"  {label}\n      {p}")
    print("A copy of each goes into a backup folder in your home folder first; nothing is deleted.")
    if not yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("Nothing changed. Run `sessioncost uninstall` in a terminal and answer y.")
            return 1
        if input("Take SessionCost out now? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing changed.")
            return 1
    backup, failed = run(home, items)
    print(f"Done. Backup: {backup}")
    for f in failed:
        print(f"  Not taken out (close the agent or editor using it, then run this again): {f}")
    print("\n" + LEFT_FOR_YOU)
    return 1 if failed else 0
