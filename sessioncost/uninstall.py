"""`sessioncost uninstall`: remove SessionCost and LiveGuard cleanly in one go.

Removes:
- LiveGuard hooks from all five agents (every other hook is kept)
- LiveGuard git hooks in the current repository (restoring any previous hooks)
- `/sessioncost` and `/liveguard` skills from all five agents
- `~/.sessioncost` (reports, guard logs, settings, helper environments)
- Agent plugins (Claude Code, Codex, Cursor) and editor extensions (VS Code, Cursor, Antigravity)
- The CLI package itself (`uv tool`, `pipx`, `pip`, or global `npm`)
- Old `~/sessioncost-backup-*` folders and `*.before-sessioncost` files (unless `--backup` is passed)

Pass `--backup` to move removed configs and data into `~/sessioncost-backup-<date>` instead of deleting them.
It asks first in a terminal; an agent cannot answer that question, and LiveGuard blocks agents from running it."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import guard_git
from .cli_setup import SKILL_HOMES
from .names import GUARD_CMD, OLD_GUARD_CMDS

WINDOWS = sys.platform == "win32"
EXTENSION_ID = "kish21.sessioncost"
NPX_HOME = "/.sessioncost/npm/"
EDITOR_HOME = "/.sessioncost/editor/"


@dataclass
class CmdStep:
    label: str
    argv: list[str] | None = None
    manual: str = ""
    self_locked: bool = False
    cleanup_dirs: list[Path] = field(default_factory=list)


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


_PLUGIN_KEY_RE = re.compile(r"^sessioncost@[A-Za-z0-9._-]+$")


def _claude_plugin_keys(home: Path) -> list[str]:
    try:
        plugins = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(plugins, dict):
        return []
    entries = plugins.get("plugins")
    if not isinstance(entries, dict):
        return []
    return [k for k in entries if isinstance(k, str) and _PLUGIN_KEY_RE.fullmatch(k)]


def _has_git_hooks(cwd: str) -> bool:
    try:
        hdir = guard_git.hooks_dir(cwd)
    except Exception:
        return False
    if not hdir or not hdir.is_dir():
        return False
    for name in guard_git.HOOKS:
        p = hdir / name
        try:
            if p.is_file() and guard_git.MARKER in p.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    return False


def _codex_plugin_dirs(home: Path) -> list[Path]:
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    found: list[Path] = []
    cache = codex / "plugins" / "cache"
    if cache.is_dir():
        found.extend(sorted(p for p in cache.glob("*/sessioncost") if p.is_dir()))
    tmp_mkt = codex / ".tmp" / "marketplaces" / "sessioncost"
    if tmp_mkt.is_dir():
        found.append(tmp_mkt)
    return found


def _cursor_plugin_dirs(home: Path) -> list[Path]:
    cursor = home / ".cursor" / "plugins"
    if not cursor.is_dir():
        return []
    found: list[Path] = []
    for p in sorted(cursor.glob("**/sessioncost")):
        if p.is_dir() and not any(prev in p.parents for prev in found):
            found.append(p)
    return found


def plan(home: Path, *, purge_backups: bool = True) -> list[tuple[str, Path, dict | None]]:
    """What filesystem items would be taken out: (label, path, new JSON dict or None to remove the whole path)."""
    out: list[tuple[str, Path, dict | None]] = []
    for agent, p in _hook_files(home):
        if p.is_file():
            if p.name == "sessioncost-guard.json":
                out.append((f"{agent}: LiveGuard hooks file", p, None))
            else:
                try:
                    data = json.loads(p.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    data = None
                new = _without_ours(data) if isinstance(data, dict) else None
                if new is not None:
                    out.append((f"{agent}: LiveGuard hooks (your other hooks stay)", p, new))
        if purge_backups:
            bak = p.with_name(p.name + ".before-sessioncost")
            if bak.is_file():
                out.append((f"{agent}: hook backup file", bak, None))
    for agent, folder, sub, _ in SKILL_HOMES:
        for name in ("sessioncost", GUARD_CMD, *OLD_GUARD_CMDS):
            skill = home / folder / sub / name / "SKILL.md"
            try:
                if f"name: {name}" in skill.read_text(encoding="utf-8", errors="replace"):
                    out.append((f"{agent}: /{name} skill", skill.parent, None))
            except OSError:
                pass
    for d in _codex_plugin_dirs(home):
        out.append(("Codex: SessionCost plugin files", d, None))
    for d in _cursor_plugin_dirs(home):
        out.append(("Cursor: SessionCost plugin files", d, None))
    data_dir = home / ".sessioncost"
    if data_dir.is_dir():
        out.append(("SessionCost's own folder: reports, guard logs, settings", data_dir, None))
    if purge_backups:
        for pattern in ("sessioncost-backup-*", "sessioncost-uninstall-backup-*"):
            for d in sorted(home.glob(pattern)):
                if d.is_dir():
                    out.append(("Previous SessionCost backup folder", d, None))
    return out


def _global_npm_pkg_exists(npm_path: str, windows: bool) -> bool:
    if windows:
        appdata = os.environ.get("APPDATA")
        if appdata and (Path(appdata) / "npm" / "node_modules" / "sessioncost").is_dir():
            return True
    p = Path(npm_path).resolve()
    return (p.parent.parent / "lib" / "node_modules" / "sessioncost").is_dir()


def plan_commands(home: Path, *, exe: str | None = None, here: Path | None = None, which=shutil.which,
                  windows: bool = WINDOWS, launcher: str | None = None) -> list[CmdStep]:
    """External uninstall steps for plugins, editor extensions, and CLI packages."""
    exe = exe or sys.executable
    here = here or Path(__file__).resolve().parent
    here_posix = here.as_posix().lower()
    exe_posix = Path(exe).as_posix().lower()
    started = Path(launcher if launcher is not None else sys.argv[0]).name.lower()
    steps: list[CmdStep] = []

    # 1. Claude Code plugin
    for key in _claude_plugin_keys(home):
        claude = which("claude")
        steps.append(CmdStep(
            label=f"Claude Code plugin ({key})",
            argv=[claude, "plugin", "uninstall", key] if claude else None,
            manual=f"claude plugin uninstall {key}  (or in Claude Code: /plugin -> Installed -> SessionCost -> Uninstall)",
        ))

    # 2. Editor extensions (VS Code, Cursor, Antigravity)
    seen_editors: set[str] = set()
    ext_roots = (
        ("VS Code", "code", home / ".vscode" / "extensions"),
        ("Cursor", "cursor", home / ".cursor" / "extensions"),
        ("Antigravity", "antigravity", home / ".antigravity" / "extensions"),
        ("Antigravity", "antigravity", home / ".gemini" / "antigravity" / "extensions"),
    )
    for editor_name, cli_name, root in ext_roots:
        if editor_name in seen_editors or not root.is_dir():
            continue
        matches = sorted(
            p for p in root.glob(f"{EXTENSION_ID}*")
            if p.is_dir() and (p.name == EXTENSION_ID or p.name.startswith(f"{EXTENSION_ID}-"))
        )
        if matches:
            seen_editors.add(editor_name)
            cli = which(cli_name)
            steps.append(CmdStep(
                label=f"{editor_name} extension ({EXTENSION_ID})",
                argv=[cli, "--uninstall-extension", EXTENSION_ID] if cli else None,
                manual=f"{cli_name} --uninstall-extension {EXTENSION_ID}  (or Extensions panel in {editor_name}: SessionCost -> Uninstall)",
                cleanup_dirs=matches,
            ))

    # 3. CLI package (uv tool, pipx, pip) — skip dev repo checkouts and bundled plugin/editor/npx copies
    in_dev_repo = (here.parent / ".git").exists()
    in_bundled = ("/plugins/cache/" in here_posix) or (NPX_HOME in exe_posix) or (EDITOR_HOME in exe_posix)
    uv = which("uv")
    uv_tool_dirs = [home / ".local" / "share" / "uv" / "tools" / "sessioncost"]
    if windows and os.environ.get("APPDATA"):
        uv_tool_dirs.append(Path(os.environ["APPDATA"]) / "uv" / "tools" / "sessioncost")
    has_uv_tool = "/uv/tools/sessioncost/" in exe_posix or any(d.is_dir() for d in uv_tool_dirs)
    if has_uv_tool:
        steps.append(CmdStep(
            label="Python tool (uv): sessioncost",
            argv=[uv, "tool", "uninstall", "sessioncost"] if uv else None,
            manual="uv tool uninstall sessioncost",
            self_locked=windows and "/uv/tools/sessioncost/" in exe_posix,
        ))
    elif "/pipx/venvs/sessioncost/" in exe_posix:
        pipx = which("pipx")
        steps.append(CmdStep(
            label="Python tool (pipx): sessioncost",
            argv=[pipx, "uninstall", "sessioncost"] if pipx else None,
            manual="pipx uninstall sessioncost",
            self_locked=windows,
        ))
    elif not in_dev_repo and not in_bundled:
        steps.append(CmdStep(
            label="Python package (pip): sessioncost",
            argv=[exe, "-m", "pip", "uninstall", "-y", "sessioncost"],
            manual=f'"{exe}" -m pip uninstall -y sessioncost',
            self_locked=windows and started == "sessioncost.exe",
        ))

    # 4. Global npm package
    npm = which("npm")
    if npm and _global_npm_pkg_exists(npm, windows):
        steps.append(CmdStep(
            label="Node package (npm global): sessioncost",
            argv=[npm, "uninstall", "-g", "sessioncost"],
            manual="npm uninstall -g sessioncost",
        ))

    return steps


def _is_link_or_junction(p: Path) -> bool:
    """True for symlinks and Windows NTFS directory junctions (which Path.is_symlink() misses)."""
    if p.is_symlink():
        return True
    if hasattr(os.path, "isjunction") and os.path.isjunction(p):
        return True
    if WINDOWS:
        try:
            st = os.lstat(p)
            return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        except OSError:
            return False
    return False


def _atomic_write_text(path: Path, text: str) -> None:
    if _is_link_or_junction(path):
        raise OSError(f"Refusing to overwrite symlink or junction: {path}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _backup_target(backup_dir: Path, home: Path, p: Path) -> Path:
    try:
        return backup_dir / p.relative_to(home)
    except ValueError:
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", p.name) or "item"
        return backup_dir / "external" / safe_name


def _force_writable(func, path, _exc) -> None:
    p = Path(path)
    if _is_link_or_junction(p):
        raise OSError(f"Refusing to chmod symlink or junction: {path}")
    mode = os.lstat(path).st_mode
    os.chmod(path, mode | stat.S_IWRITE | stat.S_IREAD)
    func(path)


def _delete_path(p: Path) -> None:
    if _is_link_or_junction(p):
        # Never follow or recurse into symlinks or Windows directory junctions.
        try:
            p.unlink()
        except (IsADirectoryError, PermissionError, OSError):
            p.rmdir()
        return
    if p.is_file():
        try:
            p.unlink()
        except PermissionError:
            mode = os.lstat(p).st_mode
            os.chmod(p, mode | stat.S_IWRITE | stat.S_IREAD)
            p.unlink()
    elif p.is_dir():
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=_force_writable)
        else:
            shutil.rmtree(p, onerror=_force_writable)


def run(home: Path, items: list[tuple[str, Path, dict | None]], *, backup: bool = False) -> tuple[Path | None, list[str]]:
    """Remove each item. When backup=True, moves copies into ~/sessioncost-backup-<date>; otherwise deletes cleanly."""
    backup_dir = home / time.strftime("sessioncost-backup-%Y-%m-%d-%H%M%S") if backup else None
    failed: list[str] = []
    for label, p, new in items:
        try:
            if new is not None:
                if _is_link_or_junction(p):
                    raise OSError(f"Refusing to edit symlink or junction: {p}")
                if backup_dir is not None:
                    keep = _backup_target(backup_dir, home, p)
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, keep)
                _atomic_write_text(p, json.dumps(new, indent=2) + "\n")
                continue

            if backup_dir is not None:
                keep = _backup_target(backup_dir, home, p)
                keep.parent.mkdir(parents=True, exist_ok=True)
                if _is_link_or_junction(p):
                    _delete_path(p)
                    continue
                try:
                    shutil.move(str(p), str(keep))
                except OSError as e:
                    if not p.is_dir() or _is_link_or_junction(p):
                        raise
                    keep.mkdir(parents=True, exist_ok=True)
                    child_failed = False
                    for child in sorted(p.iterdir()):
                        try:
                            if _is_link_or_junction(child):
                                _delete_path(child)
                            else:
                                shutil.move(str(child), str(keep / child.name))
                        except OSError as ce:
                            child_failed = True
                            failed.append(f"{label}: {child} ({ce.strerror or ce})")
                    if not child_failed:
                        try:
                            p.rmdir()
                        except OSError:
                            failed.append(f"{label}: {p} ({e.strerror or e})")
            else:
                try:
                    _delete_path(p)
                except OSError as e:
                    if not p.is_dir() or _is_link_or_junction(p):
                        raise
                    child_failed = False
                    for child in sorted(p.iterdir()):
                        try:
                            _delete_path(child)
                        except OSError as ce:
                            child_failed = True
                            failed.append(f"{label}: {child} ({ce.strerror or ce})")
                    if not child_failed:
                        try:
                            p.rmdir()
                        except OSError:
                            failed.append(f"{label}: {p} ({e.strerror or e})")
        except (OSError, ValueError) as e:
            failed.append(f"{label}: {p} ({getattr(e, 'strerror', None) or e})")
    return backup_dir, failed


def _run_cmd(argv: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def main(argv: list[str]) -> int:
    home = Path.home()
    cwd = os.getcwd()
    yes = "--yes" in argv or "-y" in argv
    keep_backup = "--backup" in argv
    items = plan(home, purge_backups=not keep_backup)
    has_git = _has_git_hooks(cwd)
    cmds = plan_commands(home)

    if not items and not has_git and not cmds:
        print("SessionCost: nothing of SessionCost or LiveGuard found on this computer.")
        return 0

    print("SessionCost will remove:")
    for label, p, _ in items:
        print(f"  {label}\n      {p}")
    if has_git:
        print(f"  LiveGuard git hooks in this repo\n      {cwd}")
    for st in cmds:
        print(f"  {st.label}")
    if keep_backup:
        print("A backup copy of removed files goes into ~/sessioncost-backup-<date> first.")
    else:
        print("This is a clean uninstall (no backup folder left behind). Pass --backup to keep a copy first.")

    if not yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("Nothing changed. Run `sessioncost uninstall` in a terminal and answer y.")
            return 1
        if input("Remove SessionCost now? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing changed.")
            return 1

    failed: list[str] = []
    manual_after: list[str] = []

    if has_git:
        try:
            print(guard_git.uninstall(cwd))
        except Exception as e:
            failed.append(f"LiveGuard git hooks ({cwd}): {e}")

    backup_dir, fs_failed = run(home, items, backup=keep_backup)
    failed.extend(fs_failed)

    for st in cmds:
        if st.self_locked or not st.argv:
            manual_after.append(f"  {st.label}: {st.manual}")
            continue
        ok, output = _run_cmd(st.argv)
        orphaned_ok = bool(st.cleanup_dirs) and "not installed" in output.lower()
        if ok or orphaned_ok:
            for d in st.cleanup_dirs:
                if d.exists():
                    try:
                        _delete_path(d)
                    except OSError as e:
                        failed.append(f"{st.label}: {d} ({e.strerror or e})")
        else:
            manual_after.append(f"  {st.label}: {st.manual}")

    if backup_dir is not None:
        print(f"Done. Backup: {backup_dir}")
    else:
        print("Done. SessionCost and LiveGuard have been removed.")
    for f in failed:
        print(f"  Not removed (close the agent or editor using it, then run this again): {f}")
    if manual_after:
        print("\nRun these remaining steps to finish (the running program or editor cannot uninstall itself):")
        for line in manual_after:
            print(line)
    print("\nRestart your agents and editor.")
    return 1 if failed else 0
