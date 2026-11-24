"""Check notes-file (owner, 2026-11-24): a handover or notes file (NEXT-SESSION.md, HANDOVER.md, NOTES.md, ...) that
keeps growing was read whole. Only the newest note is usually needed; the rest stayed in the chat and went out again
with every later message. The fix is the user's agent's: keep the newest note on top and older ones in an archive,
or read only the top section."""
from __future__ import annotations

import re
from pathlib import Path

from ..model import Fix, Session
from .common import Ledger, short_target, src, tok, turns_where

_NOTES = re.compile(r"(?:^|[\\/])(NEXT[-_ ]?SESSION|HAND[-_ ]?(?:OVER|OFF)|NOTES|PROGRESS|STATUS|WORKLOG|DEVLOG|"
                    r"SESSION[-_ ]?NOTES|TODO)[^\\/]*\.(md|txt)$", re.I)
MIN_TOKENS = 3_000           # a notes read this big counts
KEEP = 1_500                 # what the newest note takes (an estimate)


def is_notes(target: str) -> bool:
    return bool(_NOTES.search(target or ""))


def notes_file(s: Session, pr, home=None) -> Fix | None:
    calls = s.calls
    led, items, turns, hit, files = Ledger(), [], set(), [], []
    for i, c in enumerate(calls):
        for a in c.actions:
            n = tok(a.result_chars)
            if a.kind != "read" or not is_notes(a.target) or n < MIN_TOKENS or "(lines " in a.label \
                    or i + 1 >= len(calls):
                continue
            led.carry(src(c, a), n - KEEP, calls[i + 1:], pr)
            turns.add(c.turn)
            hit.append((c.agent, c.n))
            name = short_target(a.target, s.cwd)
            files += [a.target] if a.target not in files else []
            items.append(f"`{name}` read whole (~{n:,} tokens), call #{c.n}")
    if not items:
        return None
    from .. import workspace
    ws = workspace.of(s, home)
    shown = []
    for t in files:
        p = Path(t) if Path(t).is_absolute() or not s.cwd else Path(s.cwd) / t
        txt = ws.text(p)
        shown.append(f"`{ws.show(p)}`" + (f" ({txt.count(chr(10)) + 1:,} lines now)" if txt else ""))
    names = ", ".join(shown)
    return Fix(
        id="notes-file",
        title=f"Keep {', '.join(short_target(t, s.cwd) for t in files)} short ({len(items)} whole "
              f"read{'s' if len(items) > 1 else ''})",
        why=("A handover or notes file that keeps growing was read whole, though only the newest note is usually "
             "needed. The rest stayed in the chat and was sent again with every later message (an estimate: the "
             f"newest note takes about {KEEP:,} tokens)."),
        saving_usd=led.usd, saving_tokens=led.tokens, claims=led.claims, where=turns_where(sorted(turns)),
        turns=sorted(turns), items=items[:6], per_turn=led.per_turn(), calls=hit,
        how=("Best practice: keep only the newest note at the top and move older ones to an archive file nobody "
             f"reads by default. Now: {names}."),
        ask=(f"Keep {names} short: leave only the newest note at the top, move the older notes into a separate "
             "archive file next to it, and from now on read only the newest note when you continue. Show me what "
             "you will move and wait for my yes before editing."),
    )
