"""Risky steps, found on your computer: not a cost, but worth knowing.

skips_commit_checks  the agent tried to switch off the commit checks (pre-commit, husky): SKIP=... with a commit,
                     git commit --no-verify / -n, git push --no-verify, HUSKY=0, HUSKY_SKIP_HOOKS=1,
                     PRE_COMMIT_ALLOW_NO_CONFIG. Marked whether the command ran or was refused: the attempt is the
                     finding. Seen 2026-10-16: Codex set $env:SKIP to every check, then git commit.

Only the yes/no mark leaves this module; the command text is never kept.
"""
from __future__ import annotations

import re

from .model import Session

_COMMIT = re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*commit\b", re.I)
_SKIP_SET = re.compile(r"(?:^|[\s;&|(])(?:\$env:SKIP\s*=|(?:export\s+|set\s+)?SKIP\s*=)", re.I)
# the options before any quoted message: a message that mentions "-n" or "--no-verify" is not the option
_NO_VERIFY = re.compile(r"""\bgit\s+(?:-c\s+\S+\s+)*(?:commit|push)\b[^\n;&|"']*?\s--no-verify\b""", re.I)
_COMMIT_N = re.compile(r"""\bgit\s+(?:-c\s+\S+\s+)*commit\b[^\n;&|"']*?\s-(?=[a-z]*n)[a-z]+\b""", re.I)   # -n, -an
_HOOKS_OFF = re.compile(r"\b(?:HUSKY\s*=\s*['\"]?0|HUSKY_SKIP_HOOKS\s*=\s*['\"]?1|PRE_COMMIT_ALLOW_NO_CONFIG\s*=)", re.I)
LOOK_AHEAD = 3           # a SKIP set on its own counts when one of the next few commands commits


def _direct(cmd: str) -> bool:
    if _NO_VERIFY.search(cmd) or _COMMIT_N.search(cmd):
        return True
    if (_HOOKS_OFF.search(cmd) or _SKIP_SET.search(cmd)) and _COMMIT.search(cmd):
        return True
    return False


def mark_commit_check_skips(s: Session) -> None:
    """Add 'skips_commit_checks' to the risk marks of every command that tried to switch the commit checks off."""
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        cmds = [a for c in calls for a in c.actions if a.kind == "command" and a.target]
        for i, a in enumerate(cmds):
            hit = _direct(a.target)
            if not hit and (_SKIP_SET.search(a.target) or _HOOKS_OFF.search(a.target)):
                hit = any(_COMMIT.search(b.target) for b in cmds[i + 1:i + 1 + LOOK_AHEAD])
            if hit and "skips_commit_checks" not in a.risks:
                a.risks.append("skips_commit_checks")
