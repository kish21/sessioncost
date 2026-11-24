"""Check 8 (1.1): report-only check when a top-tier model is used for many small mechanical steps."""
from __future__ import annotations

from pathlib import Path

from .. import prices as pricemod
from ..model import Call, Fix, Session
from .common import turns_where

MIN_MECHANICAL_CALLS = 5
MIN_SAVING_USD = 0.20
MAX_MECHANICAL_OUTPUT = 400
MAX_MECHANICAL_THINKING = 200


def _cheaper_peer(model: str) -> str | None:
    m = (model or "").lower()
    if any(k in m for k in ("opus-5", "fable", "mythos")):
        return "claude-sonnet-5-5"
    if "opus-4" in m or "opus" in m:
        return "claude-sonnet-4-6"
    if "gpt-6-astra" in m:
        return "gpt-6-sol"
    return None


def _is_mechanical(c: Call) -> bool:
    if not c.actions:
        return False
    kinds = {a.kind for a in c.actions}
    if not kinds.issubset({"read", "search", "command"}):
        return False
    if c.usage.output > MAX_MECHANICAL_OUTPUT:
        return False
    if (c.usage.thinking or 0) > MAX_MECHANICAL_THINKING:
        return False
    return True


def model_choice(s: Session, pr, home: Path | None = None) -> Fix | None:
    calls = s.calls
    if len(calls) < MIN_MECHANICAL_CALLS:
        return None
    hit_calls: list[tuple[str, int]] = []
    turns_set: set[int] = set()
    items: list[str] = []
    total_diff = 0.0
    src_model = ""
    dst_model = ""

    from .skill_cost import skill_cost
    sc = skill_cost(s, pr, home)                           # one-step calls it counted: not counted here again
    claimed = {cl.call for cl in (sc.claims if sc else []) if cl.source[:1] == ("skill-single",)}
    for c in calls:
        peer = _cheaper_peer(c.model)
        if not peer or not _is_mechanical(c) or (c.agent, c.n) in claimed:
            continue
        cur_usd = pr.total(c)
        peer_rates, _, _ = pricemod.rates(peer, pr.prices)
        peer_rates = pricemod.for_prompt(peer_rates, c.usage.context)
        peer_usd = sum(pricemod.cost(c.usage, peer_rates).values())
        diff = max(0.0, cur_usd - peer_usd)
        if diff <= 0:
            continue
        src_model = src_model or c.model
        dst_model = dst_model or peer
        total_diff += diff
        turns_set.add(c.turn)
        hit_calls.append(("main", c.n))
        acts = ", ".join(a.label for a in c.actions[:2])
        items.append(f"call #{c.n} (turn {c.turn}) — {acts} (saved ${diff:.2f} on {peer})")

    if len(hit_calls) < MIN_MECHANICAL_CALLS or total_diff < MIN_SAVING_USD:
        return None
    turns = sorted(turns_set)
    n = len(hit_calls)
    from .. import workspace
    from ..helper_agent import make_stick, prompt
    ws = workspace.of(s, home)
    have = ws.helper_file("reader")
    if have:                    # set up before, not used: firm wording from the user's own files, not a second helper
        title = f"Use the reader helper you already have ({n} simple steps ran on {src_model})"
        why = (f"{n} steps in this chat only read files, searched or ran a quick command, on the most expensive "
               f"model, though this project has a reader helper on a cheaper model. The agent picks a helper from its "
               "description and your rules: soft or missing words there let it do the reads itself.")
        ask = make_stick(s.agent, ws.show(have), n, ws)
        from .common import before_change_note, changed_after_start
        why += before_change_note([ws.show(Path(p)) for p in changed_after_start(s, [have, *ws.instruction_files])])
    else:
        title = f"Hand simple steps to a cheaper helper ({n} ran on {src_model})"
        why = (f"{n} steps in this chat only read files, searched or ran a quick command, on the most expensive "
               f"model. A helper on a cheaper model ({dst_model} or similar) does that work for much less, and its "
               "long outputs stay out of the main chat.")
        ask = prompt(s.agent, "reader", ws)
    how = ("Switch the model only when you start a new chat: `/model` in the middle of a chat sends the whole chat "
           "again, uncached.")
    default = ws.setting("model")
    if default and (_cheaper_peer(str(default[1])) or str(default[1]).lower() in ("opus", "fable")):
        how = (f"{default[0].quote()} makes the most expensive model the default for every chat. Best practice: a "
               "cheaper default (`\"model\": \"sonnet\"`), and the top model chosen with /model at the start of a "
               "design or debugging chat. " + how)
    return Fix(
        id="model-choice",
        title=title, why=why,
        saving_usd=total_diff, saving_tokens=0,
        where=turns_where(turns), turns=turns, items=items[:6],
        per_turn={t: 0 for t in turns}, calls=hit_calls,
        how=how, paste=False, ask=ask,
    )
