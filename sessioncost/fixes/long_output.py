"""Check 6 (1.1): long model replies or heavy hidden thinking (3k+ output tokens per call)."""
from __future__ import annotations

from ..model import Call, Fix, Session
from .common import Ledger, tok, turns_where

AVG_OUTPUT_THRESHOLD = 3_000
CALL_OUTPUT_THRESHOLD = 3_000
TARGET_CONCISE_OUTPUT = 1_500
MIN_EXCESS_OUTPUT = 3_000


def _non_file_output(c: Call) -> int:
    written = sum(a.written_chars for a in c.actions)
    return max(0, c.usage.output - tok(written))


def long_output(s: Session, pr) -> Fix | None:
    calls = s.calls
    if len(calls) < 2:
        return None
    non_file = [_non_file_output(c) for c in calls]
    avg_out = sum(non_file) // len(calls)
    long_Indices = [i for i, v in enumerate(non_file) if v >= CALL_OUTPUT_THRESHOLD]
    if avg_out < AVG_OUTPUT_THRESHOLD and len(long_Indices) < 2:
        return None

    items: list[str] = []
    led = Ledger()
    hit_calls: list[tuple[str, int]] = []
    excess_total = 0
    think_total = 0
    out_total = 0

    for i in long_Indices:
        c = calls[i]
        nf = non_file[i]
        excess = max(0, nf - TARGET_CONCISE_OUTPUT)
        if excess <= 0:
            continue
        think = c.usage.thinking if c.usage.thinking is not None else max(0, nf - tok(c.text_chars))
        think_total += min(think, c.usage.output)
        out_total += c.usage.output
        reply = ("reply", c.agent, c.n)
        led.add(reply, c, 0, excess * pr.rates(c)["output"] / 1e6, kind="price")     # written: output, not sent
        led.carry(reply, excess, calls[i + 1 :], pr, first_new=False)
        excess_total += excess
        hit_calls.append(("main", c.n))
        think_note = f", ~{think:,} hidden thinking" if think >= 1_000 else ""
        items.append(f"call #{c.n} (turn {c.turn}) — {c.usage.output:,} output tokens ({nf:,} outside file edits{think_note})")

    if excess_total < MIN_EXCESS_OUTPUT:
        return None
    turns = sorted({c.turn for c in calls if ("main", c.n) in hit_calls})
    hit_avg = out_total // len(hit_calls)          # the long replies only, not the whole chat
    think_pct = int(round(100 * think_total / out_total)) if out_total else 0
    think_sentence = (f" Hidden thinking accounted for ~{think_pct}% of those output tokens "
                      f"(lower `/effort` or thinking budget on routine steps)." if think_pct >= 35 else "")
    return Fix(
        id="long-output",
        title=f"{len(hit_calls)} long model repl{'ies' if len(hit_calls) != 1 else 'y'} averaged ~{hit_avg:,} output tokens",
        why=(f"Output tokens cost 3–5x more than input tokens and then sit in the conversation for every later call."
             f"{think_sentence}"),
        saving_usd=led.usd, saving_tokens=led.tokens, claims=led.claims,
        where=turns_where(turns), turns=turns, items=items[:6], per_turn=led.per_turn(), calls=hit_calls,
        how="Keep replies concise: give the short answer first and detail only when asked.",
        paste=True,
    )