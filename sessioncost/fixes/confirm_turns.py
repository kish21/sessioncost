"""Check confirm-turn: the agent stopping to ask, and the user only saying yes."""
from __future__ import annotations

import re

from ..model import Fix, Session
from .common import Ledger, _where, asked, is_confirm, lettered, sent_usd

SKILL_STOPS = 2          # this many a) / b) stops in a chat that ran a skill: the skill's own questions cause them
SKILL_DIR = re.compile(r"[\\/]skills[\\/]([^\\/]+)[\\/]", re.I)


def skill_in_use(s: Session) -> str:
    """The skill the chat ran: a /command you typed ("/idea"), else the folder of a SKILL.md the agent read."""
    cmd = next((t.text.split()[0] for t in s.turns if t.kind == "command" and t.text.strip()), "")
    if cmd:
        return cmd
    return next((m.group(1) for a in s.actions(include_helpers=False) if a.kind == "read"
                 for m in [SKILL_DIR.search(a.target or "")] if m), "")


def confirm_turns(s: Session, P) -> Fix | None:
    """A stop where the user only said yes / ok / save, and the next call just acted on it."""
    led, hits, turns, choices = Ledger(), [], set(), 0
    hit_calls: list[tuple[str, int]] = []
    before = None                       # the last main call before this turn: what the agent said last
    for t in s.turns:
        prev, before = before, (t.calls[-1] if t.calls else before)
        said = (prev.last_text or prev.first_text) if prev is not None else ""
        if "sessioncost" in said.lower():
            continue                    # SessionCost's own "Shall I apply these?": a stop it requires, not waste
        if t.kind in ("typed", "queued") and t.calls and is_confirm(t.text, lettered(said)) and \
                (prev is None or asked(said)):
            c = t.calls[0]
            led.add(("call", c.agent, c.n), c, c.usage.context, sent_usd(c, P.rates(c)), kind="whole", turn=t.n)
            hit_calls.append((c.agent, c.n))
            hits.append(f"turn {t.n}: you typed “{t.text}”; the next call re-sent "
                        f"{c.usage.context:,} tokens to receive it")
            turns.add(t.n)
            choices += lettered(said)
        for e in t.events:
            if e.kind != "answer" or not e.ts:
                continue
            answers = [x for x in e.text.split("; ") if x.strip()]
            if answers and all(is_confirm(x) for x in answers):
                nxt = next((c for c in t.calls if c.ts_first and c.ts_first >= e.ts), None)
                if nxt is None:
                    continue
                led.add(("call", nxt.agent, nxt.n), nxt, nxt.usage.context, sent_usd(nxt, P.rates(nxt)),
                        kind="whole", turn=t.n)
                hit_calls.append((nxt.agent, nxt.n))
                hits.append(f"turn {t.n}: you picked “{e.text}” on a question card; the next call re-sent "
                            f"{nxt.usage.context:,} tokens to receive it")
                turns.add(t.n)
    if not hits:
        return None
    skill = skill_in_use(s)
    from_skill = (f" {choices} of these stops answered the a) / b) choices of the {skill} skill: asking for one "
                  "approval per stage in the skill itself removes them in every chat that uses it."
                  if skill and choices >= SKILL_STOPS else "")
    return Fix(
        id="confirm-turn", title="Skip the stop that only asks you to confirm",
        why=("The agent stopped and waited for a yes. Receiving it meant re-sending the whole conversation once "
             "more. Let it go ahead and ask “change anything?” afterwards instead." + from_skill),
        saving_usd=led.usd, saving_tokens=led.tokens, where=_where(turns), turns=sorted(turns), items=hits,
        per_turn=led.per_turn(), calls=hit_calls, paste=True, claims=led.claims,
        how="When the next step is clear, go ahead without asking me to confirm. Tell me what you did afterwards.")
