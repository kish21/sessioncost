"""Optional per-project git hooks (pre-commit & pre-push) as a second guard layer (#35).

Runs regardless of which agent or person invokes git:
- pre-commit: blocks staging secrets files (.env, keys, credentials) or real API/private keys in staged diffs.
- pre-push: blocks non-fast-forward pushes that would rewrite shared history (force push).

Never overwrites existing hooks (husky, pre-commit, project hooks): existing hooks are moved to
`<name>.before-sessioncost` and chained after our check; `uninstall` removes only ours and restores them.
"""
from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

MARKER = "# sessioncost-git-hook"
HOOKS = ("pre-commit", "pre-push")
ZERO_OIDS = {"0" * 40, "0" * 64}
_OID_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _git_run(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd or ".", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        stdin=subprocess.DEVNULL,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )


def hooks_dir(cwd: str) -> Path | None:
    """Return the active git hooks directory for the repo containing cwd, or None outside a git repo."""
    try:
        top = _git_run(cwd, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or not top.stdout.strip():
            return None
        root = Path(top.stdout.strip()).resolve()
        custom = _git_run(cwd, "config", "--get", "core.hooksPath")
        if custom.returncode == 0 and custom.stdout.strip():
            hp = Path(custom.stdout.strip())
            resolved = (hp if hp.is_absolute() else (root / hp)).resolve()
            # Refuse core.hooksPath escaping outside the repository root.
            if resolved != root and root not in resolved.parents:
                return None
            return resolved
        gp = _git_run(cwd, "rev-parse", "--git-path", "hooks")
        if gp.returncode == 0 and gp.stdout.strip():
            p = Path(gp.stdout.strip())
            return p if p.is_absolute() else (Path(cwd or ".") / p).resolve()
        return root / ".git" / "hooks"
    except (OSError, subprocess.SubprocessError):
        return None


def _python_posix() -> str:
    return Path(sys.executable).as_posix()


def _guard_script_posix() -> str:
    from .guard_setup import _guard_script
    return _guard_script().as_posix()


def _shim(name: str) -> str:
    py = shlex.quote(_python_posix())
    script = shlex.quote(_guard_script_posix())
    prev = f"{name}.before-sessioncost"
    if name == "pre-push":
        return (
            "#!/bin/sh\n"
            f"{MARKER}\n"
            "_sc_stdin=$(cat)\n"
            f'if [ -n "$_sc_stdin" ]; then printf "%s\\n" "$_sc_stdin" | {py} -S {script} git-check pre-push "$@"; '
            f'else {py} -S {script} git-check pre-push "$@" </dev/null; fi\n'
            "_sc_rc=$?\n"
            'if [ "$_sc_rc" -ne 0 ]; then exit "$_sc_rc"; fi\n'
            f'_sc_prev="$(dirname "$0")/{prev}"\n'
            'if [ -f "$_sc_prev" ]; then\n'
            '  if [ -n "$_sc_stdin" ]; then\n'
            '    if [ -x "$_sc_prev" ]; then printf "%s\\n" "$_sc_stdin" | "$_sc_prev" "$@"; else printf "%s\\n" "$_sc_stdin" | sh "$_sc_prev" "$@"; fi\n'
            '  else\n'
            '    if [ -x "$_sc_prev" ]; then "$_sc_prev" "$@" </dev/null; else sh "$_sc_prev" "$@" </dev/null; fi\n'
            '  fi\n'
            "  exit $?\n"
            "fi\n"
            "exit 0\n"
        )
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        f'{py} -S {script} git-check {name} "$@"\n'
        "_sc_rc=$?\n"
        'if [ "$_sc_rc" -ne 0 ]; then exit "$_sc_rc"; fi\n'
        f'_sc_prev="$(dirname "$0")/{prev}"\n'
        'if [ -f "$_sc_prev" ]; then\n'
        '  if [ -x "$_sc_prev" ]; then "$_sc_prev" "$@"; else sh "$_sc_prev" "$@"; fi\n'
        "  exit $?\n"
        "fi\n"
        "exit 0\n"
    )


def _is_ours(path: Path) -> bool:
    try:
        return path.is_file() and MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _make_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def install(cwd: str) -> str:
    """Install SessionCost pre-commit and pre-push git hooks in this repo, chaining to existing hooks."""
    hdir = hooks_dir(cwd)
    if hdir is None:
        return "SessionCost git guard: not inside a git repository."
    hdir.mkdir(parents=True, exist_ok=True)
    chained: list[str] = []
    for name in HOOKS:
        hook = hdir / name
        prev = hdir / f"{name}.before-sessioncost"
        if hook.exists() and not _is_ours(hook):
            if not prev.exists():
                hook.replace(prev)
            elif hook.is_symlink():
                hook.unlink()
            chained.append(name)
        elif hook.is_symlink():
            hook.unlink()
        hook.write_bytes(_shim(name).encode("utf-8"))
        _make_executable(hook)
    extra = f" Chained existing hook(s): {', '.join(chained)}." if chained else ""
    return (
        f"SessionCost git guard: installed pre-commit and pre-push hooks in {hdir}.{extra} "
        "Run `sessioncost guard git off` to remove only SessionCost's hooks."
    )


def uninstall(cwd: str) -> str:
    """Remove only SessionCost's git hooks and restore any chained original hooks."""
    hdir = hooks_dir(cwd)
    if hdir is None:
        return "SessionCost git guard: not inside a git repository."
    removed: list[str] = []
    restored: list[str] = []
    for name in HOOKS:
        hook = hdir / name
        prev = hdir / f"{name}.before-sessioncost"
        if not _is_ours(hook):
            continue
        if prev.exists():
            prev.replace(hook)
            restored.append(name)
        else:
            hook.unlink()
        removed.append(name)
    if not removed:
        return f"SessionCost git guard: no SessionCost git hooks installed in {hdir}."
    extra = f" Restored previous hook(s): {', '.join(restored)}." if restored else ""
    return f"SessionCost git guard: removed {', '.join(removed)} from {hdir}.{extra}"


def status(cwd: str) -> str:
    """Report whether SessionCost's git hooks are installed in this repo."""
    hdir = hooks_dir(cwd)
    if hdir is None:
        return "SessionCost git guard: not inside a git repository."
    active = [name for name in HOOKS if _is_ours(hdir / name)]
    if not active:
        return f"SessionCost git guard: off in {hdir} (`sessioncost guard git on` to enable)."
    return f"SessionCost git guard: on ({', '.join(active)}) in {hdir}."


def check_pre_commit(cwd: str) -> tuple[int, str]:
    """Block commits that stage secrets files or real API/private keys."""
    from .risk_patterns import _SECRET, real_key
    try:
        r = _git_run(cwd, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    except (OSError, subprocess.SubprocessError):
        return 1, "SessionCost git pre-commit blocked: git diff failed or timed out while checking staged files."
    if r.returncode != 0:
        return 0, ""
    staged = [f for f in r.stdout.split("\x00") if f]
    if not staged:
        return 0, ""
    secret_files = sorted({f for f in staged if _SECRET.search("/" + f.replace("\\", "/"))})
    if secret_files:
        return (
            1,
            "SessionCost git pre-commit blocked: staged secrets file(s): "
            f"{', '.join(secret_files[:5])}. Unstage with `git restore --staged <file>` and add to .gitignore.",
        )
    try:
        diff = _git_run(cwd, "diff", "--cached", "--no-ext-diff", "--unified=0", "--no-color")
    except (OSError, subprocess.SubprocessError):
        return 1, "SessionCost git pre-commit blocked: git diff failed or timed out while scanning staged changes."
    if diff.returncode == 0 and diff.stdout:
        added_lines = [
            ln[1:] for ln in diff.stdout.splitlines() if ln.startswith("+") and not ln.startswith("+++")
        ]
        if real_key("\n".join(added_lines)):
            return (
                1,
                "SessionCost git pre-commit blocked: staged changes contain a real API key, token, or private key. "
                "Remove the key before committing.",
            )
    return 0, ""


def check_pre_push(cwd: str, stdin_text: str) -> tuple[int, str]:
    """Block non-fast-forward pushes that would rewrite shared remote history."""
    for line in (stdin_text or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        local_ref, local_oid, remote_ref, remote_oid = parts[:4]
        if not (_OID_RE.fullmatch(local_oid) and _OID_RE.fullmatch(remote_oid)):
            continue
        if local_oid in ZERO_OIDS or remote_oid in ZERO_OIDS or local_oid == remote_oid:
            continue
        try:
            has_remote = _git_run(cwd, "cat-file", "-e", remote_oid)
            if has_remote.returncode != 0:
                continue
            anc = _git_run(cwd, "merge-base", "--is-ancestor", remote_oid, local_oid)
        except (OSError, subprocess.SubprocessError):
            continue
        if anc.returncode == 1:
            return (
                1,
                f"SessionCost git pre-push blocked: pushing {local_ref} to {remote_ref} would rewrite shared history "
                "(non-fast-forward / force push).",
            )
    return 0, ""
