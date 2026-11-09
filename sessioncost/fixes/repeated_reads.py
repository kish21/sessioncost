"""Check repeated-reads: the same file read again with the same content."""
from __future__ import annotations


from .. import prices as pricemod
from ..model import Fix, Session
from .common import (_where, helper_name, tok)

def repeated_reads(s: Session, P) -> Fix | None:
    """The same file read again with exactly the same content, in the same thread."""
    threads = [s.calls] + [h.calls for h in s.helpers]
    usd, tokens, items, turns = 0.0, 0, [], set()
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    guessed = False
    for calls in threads:
        seen: dict[str, str] = {}
        for i, c in enumerate(calls):
            for a in c.actions:
                if a.kind in ("write", "edit"):
                    for f in a.target.splitlines() or [""]:
                        seen.pop(f, None)
                        for k in [k for k in seen if f and f in k.splitlines()]:
                            seen.pop(k)
                    continue
                if a.kind != "read" or a.result_chars < 200 or not a.result_hash:
                    continue
                key = a.target
                if seen.get(key) == a.result_hash + str(a.label):
                    t = tok(a.result_chars)
                    if i + 1 < len(calls):
                        nxt = calls[i + 1]
                        cost = t * pricemod.new_input_rate(nxt.usage, P.rates(nxt))
                        cost += sum(t * P.rates(k)["cache_read"] for k in calls[i + 2:])
                        usd += cost / 1e6
                    tokens += t
                    turns.add(c.turn)
                    per_turn[c.turn] = per_turn.get(c.turn, 0) + t
                    hit_calls.append((c.agent, c.n))
                    guessed = guessed or a.result_guessed
                    items.append(f"{a.label} again (~{t:,} tokens, {'likely ' if a.result_guessed else ''}unchanged), call {c.n}"
                                 + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
                else:
                    seen[key] = a.result_hash + str(a.label)
    if not items:
        return None
    return Fix(
        id="repeated-reads", title="Don't re-read a file that has not changed",
        why=(f"{len(items)} read{'s' if len(items) > 1 else ''} returned exactly the same text as an earlier read in "
             "the same chat." + (" (Cursor keeps only a file's line count: a read of the same file, the same part and "
                                 "the same length with no edit in between counts as likely unchanged, and its size is "
                                 "estimated from the lines.)" if guessed else "")
             + " The copy was paid for when it came in and again on every later call that carried it."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="Do not read a file again if it has not changed since you last read it: use what you already read.")
