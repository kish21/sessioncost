"""Check 2 (1.1): a big tool result sat in the conversation for 10+ calls after the last step that used it."""
from __future__ import annotations

from ..model import Call, Fix, Session
from .big_results import big_result_action_ids
from .common import Ledger, short_target, src, tok, turns_where

MIN_RESULT_TOKENS = 1_000
MIN_STALE_CALLS = 10
MIN_STALE_CARRY_TOKENS = 20_000


def _compaction_after(calls: list[Call], start: int) -> int:
    """Index of the last call before a compaction (context drop > 60%), or len(calls) - 1."""
    for j in range(start + 1, len(calls)):
        prev_ctx = calls[j - 1].usage.context
        if prev_ctx > 20_000 and calls[j].usage.context < prev_ctx * 0.4:
            return j - 1
    return len(calls) - 1


def stale_context(s: Session, pr) -> Fix | None:
    calls = s.calls
    if len(calls) <= MIN_STALE_CALLS:
        return None
    big_ids = big_result_action_ids(calls)
    items: list[str] = []
    led = Ledger()
    hit_calls: list[tuple[str, int]] = []
    seen_targets: set[str] = set()

    for i, c in enumerate(calls):
        end_idx = _compaction_after(calls, i)
        if end_idx - i < MIN_STALE_CALLS:
            continue
        for a in c.actions:
            if id(a) in big_ids:
                continue
            t = tok(a.result_chars)
            if t < MIN_RESULT_TOKENS:
                continue
            key = a.target or a.label
            if key in seen_targets:
                continue
            last_touch = i
            if a.target:
                for j in range(i + 1, end_idx + 1):
                    if any(x.target == a.target for x in calls[j].actions):
                        last_touch = j
            stale_slice = calls[last_touch + 1 : end_idx + 1]
            if len(stale_slice) < MIN_STALE_CALLS:
                continue
            carried_tok = t * len(stale_slice)
            if carried_tok < MIN_STALE_CARRY_TOKENS:
                continue
            seen_targets.add(key)
            led.carry(src(c, a), t, stale_slice, pr, first_new=False)
            if ("main", c.n) not in hit_calls:
                hit_calls.append(("main", c.n))
            name = short_target(a.target or a.label, s.cwd)
            items.append(f"`{name}` (call #{c.n}, ~{t:,} tokens) — carried across {len(stale_slice)} later calls "
                         f"(~{carried_tok:,} cached tokens) without being touched again")

    total_tok, total_usd, per_turn = led.tokens, led.usd, led.per_turn()
    if total_tok < MIN_STALE_CARRY_TOKENS:
        return None
    turns = sorted(per_turn)
    n = len(items)
    return Fix(
        id="stale-context",
        title=f"{n} large tool result{'s' if n != 1 else ''} stayed in the chat long after you moved on",
        why=(f"Every tool result stays in the conversation and is re-sent on every call until you compact or start "
             f"a new chat. {n} large result{'s' if n != 1 else ''} sat untouched for 10+ calls, adding "
             f"~{total_tok:,} cached tokens (run /compact once a noisy step is done)."),
        saving_usd=total_usd, saving_tokens=total_tok, claims=led.claims,
        where=turns_where(turns), turns=turns, items=items[:6], per_turn=per_turn, calls=hit_calls,
        how="Write long command or log output to a file and read only the lines you need, so the full output does not stay in the chat.",
        paste=True,
    )
