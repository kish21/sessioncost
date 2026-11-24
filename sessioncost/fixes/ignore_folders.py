"""Check ignore-folders (owner, 2026-11-24): the agent read or searched folders of installed or generated files
(node_modules, dist, .venv, ...). Every agent gets a firm rules line; Claude Code also gets `Read(...)` deny rules
(its permissions docs: a Read deny rule blocks its file tools for that path; it does not stop a shell command such as
`grep -r`, hence the rule line too; a .claudeignore has no effect). Copilot Business/Enterprise admins can also set
content exclusion (GitHub docs). Cursor's, Codex's and Antigravity's ignore files are not named: not checked."""
from __future__ import annotations

import re

from ..model import Fix, Session
from .common import Ledger, short_target, src, tok, turns_where

FOLDERS = ("node_modules", "dist", "build", ".next", "coverage", ".venv", "venv", "__pycache__", ".pytest_cache",
           ".mypy_cache", ".tox")
_IN = re.compile(r"(?:^|[\s\\/'\"=])(" + "|".join(map(re.escape, FOLDERS)) + r")(?:[\\/]|$|[\s'\"])")
_READ_CMD = re.compile(r"^\s*(cat|head|tail|less|more|grep|rg|find|ls|sed|awk|type|dir|Get-Content|Select-String|"
                       r"Get-ChildItem)\b", re.I)
MIN_EACH = 200
MIN_TOTAL = 2_000
CUT = 0.8                    # an estimate: most of what came back from such a folder was not needed


def _folder(a) -> str | None:
    if a.kind not in ("read", "search") and not (a.kind == "command" and _READ_CMD.search(a.target or "")):
        return None
    m = _IN.search(a.target or "")
    return m.group(1) if m else None


def rule_line(folders: list[str]) -> str:
    names = ", ".join(folders)
    return (f"Never read or search {names}: they hold installed or generated files. Search with paths or globs that "
            "leave them out.")


def ignore_folders(s: Session, pr, home=None) -> Fix | None:
    led, items, turns, hit, seen = Ledger(), [], set(), [], []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        for i, c in enumerate(calls):
            for a in c.actions:
                f = _folder(a)
                n = tok(a.result_chars)
                if not f or n < MIN_EACH or i + 1 >= len(calls):
                    continue
                led.carry(src(c, a), int(n * CUT), calls[i + 1:], pr)
                turns.add(c.turn)
                hit.append((c.agent, c.n))
                seen += [f] if f not in seen else []
                items.append(f"{a.label or short_target(a.target, s.cwd)} (~{n:,} tokens, inside {f})")
    if led.tokens < MIN_TOTAL:
        return None
    from .. import workspace
    from ..helper_agent import rules_file
    ws = workspace.of(s, home)
    line = rule_line(seen)
    have = ws.find(r"\b(" + "|".join(map(re.escape, seen)) + r")\b.*\b(never|don't|do not|avoid|skip|ignore)\b|"
                   r"\b(never|don't|do not|avoid|skip|ignore)\b.*\b(" + "|".join(map(re.escape, seen)) + r")\b")
    steps = []
    if have:
        steps.append(f"Your rule {have[0].quote()} did not stop these reads. Replace it with: \"{line}\"")
    else:
        steps.append(f"Add to {rules_file(s.agent)}: \"{line}\"")
    if s.agent == "claude-code":
        deny = ", ".join(f'"Read(./{f}/**)"' for f in seen)
        steps.append(f"In .claude/settings.json add {deny} to \"permissions\" → \"deny\" (this blocks Claude Code's "
                     "file tools there; the rule line covers shell searches).")
    if s.agent.startswith("copilot"):
        steps.append("On Copilot Business or Enterprise, an admin can also exclude these paths (content exclusion).")
    numbered = " ".join(f"{k}. {x}" for k, x in enumerate(steps, 1))
    return Fix(
        id="ignore-folders",
        title=f"Keep the agent out of {', '.join(seen)} ({len(items)} read{'s' if len(items) > 1 else ''})",
        why=(f"The agent read or searched {', '.join(seen)}: installed or generated files it rarely needs. What came "
             f"back stayed in the chat and was sent again with every later message (an estimate: about "
             f"{round(CUT * 100)}% of it was not needed)."),
        saving_usd=led.usd, saving_tokens=led.tokens, claims=led.claims, where=turns_where(sorted(turns)),
        turns=sorted(turns), items=items[:6], per_turn=led.per_turn(), calls=hit,
        how="Best practice: the agent never reads installed or generated folders. " + numbered,
        ask=f"Keep yourself out of {', '.join(seen)}: {numbered} Propose the exact changes and wait for my yes.",
    )
