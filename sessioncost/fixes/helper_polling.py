"""Check helper-polling: the main agent only waiting on a helper."""
from __future__ import annotations


from ..model import Fix, Session
from .common import (_where, sent_usd)

POLL_TOOLS = {"schedule"}                               # a timer to look again (Antigravity)
POLL_ACTIONS = {("manage_subagents", "list"), ("manage_subagents", "status")}


def _polls(a) -> bool:
    return a.tool in POLL_TOOLS or any(a.tool == t and f"({v})" in a.label for t, v in POLL_ACTIONS)


def helper_polling(s: Session, P) -> Fix | None:
    """Main-agent calls that only waited for a helper: a timer, or a look at the list of running helpers. A helper
    sends its answer back by itself, so each of these calls re-sent the whole conversation for nothing."""
    if not s.helpers:
        return None
    usd, tokens, items, turns = 0.0, 0, [], set()
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    helper_turns = {c.turn for h in s.helpers for c in h.calls}
    for c in s.calls:
        if not c.actions or c.turn not in helper_turns or not all(_polls(a) for a in c.actions):
            continue
        usd += sent_usd(c, P.rates(c))
        tokens += c.usage.context
        turns.add(c.turn)
        per_turn[c.turn] = per_turn.get(c.turn, 0) + c.usage.context
        hit_calls.append((c.agent, c.n))
        items.append(f"call {c.n} (turn {c.turn}): {'; '.join(a.label for a in c.actions)}, re-sending "
                     f"{c.usage.context:,} tokens")
    if not items:
        return None
    return Fix(
        id="helper-polling", title="Let helpers report back instead of checking on them",
        why=(f"{len(items)} call{'s' if len(items) > 1 else ''} of the main agent only waited for a helper: a timer, "
             "or a look at which helpers were still running. A helper sends its answer back by itself, and each of "
             "these calls re-sent the whole conversation to wait."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="When a helper is working, wait for its message. Do not set timers or check on it in between.")
