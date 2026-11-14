"""All fixes together: each token counted once.

Each fix lists, call by call, the tokens it would have kept out (its claims, model.Claim). Two fixes can point at the
same text: a big file read is both a "big result" and, read again, a "repeated read"; an old tool result is also part
of the old conversation a new chat would drop. Adding the fixes up would count that text twice. The rules, per call:

1. A call that would not have happened at all (kind "whole": a yes-only stop, a retry, a timer) covers every other
   claim on that call: the call counts once, at its own size.
2. Claims on the same text (the same source) count once: the largest.
3. The old conversation a new chat would drop (kind "history") holds the old tool results too: the call counts the
   larger of the two, never both.
4. "price" claims (the cache broke) make tokens cheaper without cutting any: money only.
5. Never more than the call sent.

Fixes without claims: Step 0 fixes (same text on every call, nothing else points at it) count on their own line; a
cheaper model (tokens 0) counts only on the share of tokens the other fixes leave; any other fix (a plugged-in engine)
is added as it is.
"""
from __future__ import annotations

from collections import defaultdict

from ..model import Claim, Fix


def _pairs(out: dict[str, set], ids) -> None:
    ids = set(ids)
    for a in ids:
        out[a] |= ids - {a}


def combine(fixes: list[Fix], ctx: dict[tuple[str, int], tuple[int, int]]) -> dict:
    """ctx: (thread, call number) -> (tokens the call sent, its turn). Returns tokens, usd, setup_tokens,
    per_turn {turn: tokens} (Step 0 not included) and overlaps {fix id: [fix ids it shares tokens with]}."""
    by_call: dict[tuple, list[tuple[str, Claim]]] = defaultdict(list)
    overlaps: dict[str, set] = defaultdict(set)
    tokens, usd, setup, per_turn = 0, 0.0, 0, defaultdict(int)
    price_only: list[Fix] = []
    for f in fixes:
        if f.claims:
            for c in f.claims:
                by_call[c.call].append((f.id, c))
        elif not f.saving_tokens:
            price_only.append(f)
        elif not f.turns and not f.per_turn:
            setup += f.saving_tokens
            usd += f.saving_usd
        else:
            tokens += f.saving_tokens
            usd += f.saving_usd
            for t, v in f.per_turn.items():
                per_turn[int(t)] += v

    for call, cl in by_call.items():
        whole = [x for x in cl if x[1].kind == "whole"]
        price_usd = 0.0
        if whole:
            _, w = max(whole, key=lambda x: (x[1].tokens, x[1].usd))
            tok, money = w.tokens, w.usd
            if len({i for i, _ in cl}) > 1:
                _pairs(overlaps, [i for i, _ in cl])
        else:
            best: dict[tuple, tuple[str, Claim]] = {}
            on_source: dict[tuple, set] = defaultdict(set)
            for fid, c in cl:
                if c.kind != "carry":
                    continue
                on_source[c.source].add(fid)
                if c.source not in best or (c.tokens, c.usd) > (best[c.source][1].tokens, best[c.source][1].usd):
                    best[c.source] = (fid, c)
            for ids in on_source.values():
                if len(ids) > 1:
                    _pairs(overlaps, ids)
            tok = sum(c.tokens for _, c in best.values())
            money = sum(c.usd for _, c in best.values())
            hist = [x for x in cl if x[1].kind == "history"]
            if hist:
                hid, h = max(hist, key=lambda x: (x[1].tokens, x[1].usd))
                if best:
                    _pairs(overlaps, {hid} | {i for i, _ in best.values()})
                if h.tokens > tok:
                    tok, money = h.tokens, h.usd
            prices: dict[tuple, float] = {}
            for _, c in cl:
                if c.kind == "price":
                    prices[c.source] = max(prices.get(c.source, 0.0), c.usd)
            price_usd = sum(prices.values())
        sent = ctx.get(call, (None, None))[0]
        if sent is not None and tok > sent:
            money *= sent / tok
            tok = sent
        tokens += tok
        usd += money + price_usd
        if tok:
            per_turn[cl[0][1].turn] += tok

    tokens += setup
    if price_only:
        total = sum(v[0] for v in ctx.values())
        kept = max(0.0, 1 - tokens / total) if total else 1.0
        usd += kept * sum(f.saving_usd for f in price_only)
    return {"tokens": tokens, "usd": usd, "setup_tokens": setup, "per_turn": dict(per_turn),
            "overlaps": {k: sorted(v) for k, v in overlaps.items() if v}}


def shared(fixes: list[Fix], ctx: dict, overlaps: dict[str, list[str]]) -> dict[str, dict[str, int]]:
    """For each pair of fixes that overlap: the tokens they share (both alone, minus both together). The `fix`
    plan applies only some fixes and has no claims: it takes its total as the sum, minus what each pair shares."""
    by_id = {f.id: f for f in fixes}
    out: dict[str, dict[str, int]] = {}
    for a, others in overlaps.items():
        for b in others:
            if a < b and a in by_id and b in by_id:
                fa, fb = by_id[a], by_id[b]
                both = sum(combine([f], ctx)["tokens"] for f in (fa, fb)) - combine([fa, fb], ctx)["tokens"]
                out.setdefault(a, {})[b] = out.setdefault(b, {})[a] = max(0, both)
    return out
