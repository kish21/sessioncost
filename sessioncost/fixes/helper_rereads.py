"""Check 7 (1.1): helper agents re-reading files the main agent already read."""
from __future__ import annotations

from ..model import Fix, Session
from .common import Ledger, short_target, src, tok, turns_where

MIN_FILE_TOKENS = 200
MIN_TOTAL_SAVED_TOKENS = 500


def helper_rereads(s: Session, pr) -> Fix | None:
    if not s.helpers or not s.calls:
        return None
    main_reads: dict[str, list[tuple[int, object, str]]] = {}
    for c in s.calls:
        for a in c.actions:
            if a.kind == "read" and a.target and tok(a.result_chars) >= MIN_FILE_TOKENS:
                main_reads.setdefault(a.target, []).append((c.turn, c.ts_first, a.result_hash))

    if not main_reads:
        return None

    items: list[str] = []
    led = Ledger()
    hit_calls: list[tuple[str, int]] = []

    for h in s.helpers:
        seen_in_helper: set[str] = set()
        for j, hc in enumerate(h.calls):
            hturn = hc.turn or h.turn or 1
            for a in hc.actions:
                if a.kind != "read" or not a.target or a.target in seen_in_helper:
                    continue
                t = tok(a.result_chars)
                if t < MIN_FILE_TOKENS or a.target not in main_reads:
                    continue
                earlier = [
                    (mt, mts, mhash) for (mt, mts, mhash) in main_reads[a.target]
                    if mt <= hturn and not (mts and hc.ts_first and mts > hc.ts_first)
                ]
                if not earlier:
                    continue
                if a.result_hash and any(mhash for _, _, mhash in earlier):
                    if not any(mhash == a.result_hash for _, _, mhash in earlier):
                        continue
                seen_in_helper.add(a.target)
                tail = h.calls[j + 1 :]
                if not tail:
                    continue                     # the helper ended before the result was sent back
                led.carry(src(hc, a), t, tail, pr, turn=hturn)
                tag = hc.agent if hc.agent.startswith("helper:") else f"helper:{h.id}"
                if (tag, hc.n) not in hit_calls:
                    hit_calls.append((tag, hc.n))
                name = short_target(a.target, s.cwd)
                hname = h.description or h.agent_type or h.id[:8]
                items.append(f"`{name}` (~{t:,} tokens) — re-read by helper `{hname}` in turn {hturn}")

    total_tok, total_usd, per_turn = led.tokens, led.usd, led.per_turn()
    if total_tok < MIN_TOTAL_SAVED_TOKENS:
        return None
    turns = sorted(per_turn)
    n = len(items)
    return Fix(
        id="helper-rereads",
        title=f"Helpers re-read {n} file{'s' if n != 1 else ''} the main agent had already read (~{total_tok:,} tokens)",
        why=(f"Helper agents start with a blank context and cannot see what the main agent already read unless you "
             f"include it in their prompt. Passing the key facts or lines in the helper prompt avoids ~{total_tok:,} "
             f"duplicate tokens."),
        saving_usd=total_usd, saving_tokens=total_tok, claims=led.claims,
        where=turns_where(turns), turns=turns, items=items[:6], per_turn=per_turn, calls=hit_calls,
        how="When starting a helper agent, pass the relevant file excerpts or facts in its task prompt so it does not re-read files you already read.",
        paste=True,
    )
