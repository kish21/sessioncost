"""Check large-files (owner, 2026-11-24): a long source file read whole again and again (it changed in between, so
repeated-reads does not catch it). Whole = the read brought back most of the file as it is on disk now (Claude Code's
log does not mark a part read). Lock and build files are big-results' job, notes files notes-file's."""
from __future__ import annotations

from pathlib import Path

from ..model import Fix, Session
from .big_results import _is_lock_or_build
from .common import Ledger, short_target, src, tok, turns_where
from .notes_file import is_notes

MIN_TOKENS = 3_000           # a read this big (about 300+ lines of code) counts as a large file
SPLIT_LINES = 500            # a file longer than this is worth splitting by job
CUT = 0.7                    # an estimate: reading the needed lines keeps about 30% of a whole re-read
WHOLE = 0.8                  # the read brought back at least this share of the file


def large_files(s: Session, pr, home=None) -> Fix | None:
    from .. import workspace
    from ..helper_agent import rules_file
    ws = workspace.of(s, home)
    calls = s.calls
    led, items, turns, hit = Ledger(), [], set(), []
    count: dict[str, int] = {}
    size: dict[str, tuple[int, int]] = {}
    for i, c in enumerate(calls):
        for a in c.actions:
            t = a.target or ""
            if a.kind != "read" or not t or tok(a.result_chars) < MIN_TOKENS or "(lines " in a.label \
                    or _is_lock_or_build(t) or is_notes(t):
                continue
            p = Path(t) if Path(t).is_absolute() or not s.cwd else Path(s.cwd) / t
            if t not in size:
                txt = ws.text(p)
                size[t] = (txt.count("\n") + 1, len(txt)) if txt else (0, 0)
            lines, chars = size[t]
            if chars and a.result_chars < WHOLE * chars:
                continue                          # a part of the file
            count[t] = count.get(t, 0) + 1
            if count[t] < 2 or i + 1 >= len(calls):
                continue
            n = tok(a.result_chars)
            led.carry(src(c, a), int(n * CUT), calls[i + 1:], pr)
            turns.add(c.turn)
            hit.append((c.agent, c.n))
            items.append(f"`{short_target(t, s.cwd)}` read whole again (read {count[t]}, ~{n:,} tokens"
                         + (f", {lines:,} lines" if lines else "") + ")")
    if not items:
        return None
    rule = ("Read files over 300 lines in parts: search for the place first, then read only those lines; after an "
            "edit, re-read only the lines you changed.")
    have = ws.find(r"\bread\b.*\b(parts?|ranges?|only the lines|line ranges?|sections?)\b")
    big = sorted({short_target(t, s.cwd) for t in count if count[t] >= 2 and size[t][0] > SPLIT_LINES})
    steps = [f"Your rule {have[0].quote()} did not stop these whole re-reads. Make it firm: \"{rule}\"" if have
             else f"Add to {rules_file(s.agent)}: \"{rule}\""]
    if big:
        steps.append(f"Split {', '.join(big)} by job (each over {SPLIT_LINES} lines), so a task reads one small file.")
    numbered = " ".join(f"{k}. {x}" for k, x in enumerate(steps, 1))
    return Fix(
        id="large-files",
        title=f"Read long files in parts ({len(items)} whole re-read{'s' if len(items) > 1 else ''})",
        why=("Long files were read whole more than once. Each copy stays in the chat and is sent again with every "
             f"later message (an estimate: reading only the needed lines keeps about {round((1 - CUT) * 100)}%)."),
        saving_usd=led.usd, saving_tokens=led.tokens, claims=led.claims, where=turns_where(sorted(turns)),
        turns=sorted(turns), items=items[:6], per_turn=led.per_turn(), calls=hit,
        how="Best practice: search first, then read only the lines you need. " + numbered,
        ask=f"Long files are read whole again and again here. {numbered} Propose the exact changes and wait for my yes.",
    )
