"""Check repeated-writes: the same file written whole again."""
from __future__ import annotations


from ..model import Fix, Session
from .common import Ledger, _where, helper_name, src, tok

WRITE_MIN = 800          # characters (~200 tokens): a smaller rewrite is not worth an edit instead


def repeated_writes(s: Session, P) -> Fix | None:
    """The same file written whole again in the same thread. Each rewrite after the first was paid as output, then
    carried by every later call of that thread. An edit would have written only the lines that changed, so this is
    the most it could save."""
    led, items, turns = Ledger(), [], set()
    hit_calls: list[tuple[str, int]] = []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        seen: dict[str, int] = {}
        for i, c in enumerate(calls):
            for a in c.actions:
                if a.kind != "write" or not a.target or a.written_chars < WRITE_MIN:
                    continue
                seen[a.target] = seen.get(a.target, 0) + 1
                if seen[a.target] < 2:
                    continue
                t = tok(a.written_chars)
                led.add(src(c, a), c, 0, t * P.rates(c)["output"] / 1e6, kind="price")    # written: output, not sent
                led.carry(src(c, a), t, calls[i + 1:], P, first_new=False)
                turns.add(c.turn)
                hit_calls.append((c.agent, c.n))
                items.append(f"{a.label} again, time {seen[a.target]} (~{t:,} tokens), call {c.n}"
                             + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
    if not items:
        return None
    return Fix(
        id="repeated-writes", title="Edit an existing file instead of rewriting all of it",
        why=(f"{len(items)} write{'s' if len(items) > 1 else ''} rewrote a whole file this chat had already written. "
             "The new copy was paid for as output, and again on every later call that carried it. An edit writes "
             "only the lines that change, so this is the most it could save."),
        saving_usd=led.usd, saving_tokens=led.tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=led.per_turn(), calls=hit_calls, paste=True, claims=led.claims,
        how="Write a new file once. When changing an existing file, edit only the lines that changed instead of "
            "writing the whole file again.")
