"""Check error-loop: failed steps, and retries straight after a failure."""
from __future__ import annotations

import re

from .. import prices as pricemod
from ..model import Fix, Session
from .common import (_where, helper_name, sent_usd, tok)

ERRORS_MIN = 3           # this many failed steps in a chat, or two in a row in one turn, is a pattern


def error_loop(s: Session, P) -> Fix | None:
    """Failed tool steps, and above all retries straight after a failure. Each error message rode on every later
    call of its thread; a retry right after a failure also re-sent the whole conversation to try again. Stopping to
    find the cause after the second failure would have saved the retries (the most it could save)."""
    usd, tokens, items, turns, fails, longest, same = 0.0, 0, [], set(), 0, 0, 0
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        run, turn = 0, None
        failed: set[tuple[str, str]] = set()      # what failed since the last change to a file: the same again is blind
        for i, c in enumerate(calls):
            if c.turn != turn:
                run, turn = 0, c.turn
            for a in c.actions:
                if a.kind in ("write", "edit"):
                    failed.clear()
                if not a.is_error:
                    run = 0
                    continue
                key = (a.tool, re.sub(r"\s+", " ", a.target or a.label).strip())
                again = key in failed
                failed.add(key)
                same += again
                fails += 1
                run += 1
                longest = max(longest, run)
                t = tok(a.result_chars)
                cost = 0.0
                if t and i + 1 < len(calls):
                    nxt = calls[i + 1]
                    cost += t * pricemod.new_input_rate(nxt.usage, P.rates(nxt))
                    cost += sum(t * P.rates(k)["cache_read"] for k in calls[i + 2:])
                retry = run > 1
                if retry and a is c.actions[0]:       # this call only happened to try again after a failure
                    cost += sent_usd(c, P.rates(c)) * 1e6
                    t += c.usage.context
                usd += cost / 1e6
                tokens += t
                turns.add(c.turn)
                per_turn[c.turn] = per_turn.get(c.turn, 0) + t
                if (c.agent, c.n) not in hit_calls:
                    hit_calls.append((c.agent, c.n))
                note = ", the same as before and unchanged" if again else ", again right after a failure" if retry else ""
                items.append(f"{a.label} failed{note}, call {c.n}"
                             + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
    if fails < ERRORS_MIN and longest < 2:
        return None
    return Fix(
        id="error-loop", title="Stop and find the cause after a step fails twice",
        why=(f"{fails} step{'s' if fails > 1 else ''} failed in this chat"
             + (f", up to {longest} in a row" if longest > 1 else "")
             + (f"; {same} of them ran exactly what had already failed, with no file changed in between" if same else "")
             + ". Each error message was carried by every later call, and each retry right after a failure re-sent "
             "the whole conversation to try again. Finding the cause first is usually cheaper than trying again."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="When a command or tool fails twice in a row, stop and find the cause before trying again. Do not retry "
            "the same thing with small changes.")
