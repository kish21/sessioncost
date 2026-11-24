"""Check helper-model (owner yes 2026-11-24): helpers that only read, searched, ran checks or reviewed, on the
most expensive model. Names where that model is set: the helper's own agent file and its `model:` line, or (a
built-in helper with no file, such as Claude Code's general-purpose, which takes the chat's model) the file format
that gives it a cheaper one. Every agent with helpers in its log (Claude Code, Codex, Cursor, Copilot, Antigravity);
the saving is the price of the helper's own calls on the cheaper model, nothing guessed."""
from __future__ import annotations

import re
from pathlib import Path

from .. import prices as pricemod
from ..model import Call, Fix, Helper, Session
from .common import turns_where
from .model_choice import _cheaper_peer

MIN_SAVING_USD = 0.20
READ_WORK = {"read", "search", "command", "web", "skill", "plan", "other"}   # no edit / write: review and read work
from ..workspace import AGENT_DIRS, HOME_DIRS   # one list of where helper files live

_REVIEW = re.compile(r"\b(review|audit|security|check the (change|diff|pr))", re.I)
_MODEL = re.compile(r"^\s*model\s*[:=]\s*[\"']?([^\"'\s#]+)", re.I)


def read_only(h: Helper) -> bool:
    kinds = {a.kind for c in h.calls for a in c.actions}
    return bool(kinds) and kinds <= READ_WORK


def agent_file(agent: str, kind: str, project: str, home: Path | None = None) -> tuple[str, int, str] | None:
    """(path, line of `model:` or 0, model named or "") of a named helper's own file, or None (built-in, no file)."""
    if not kind or kind in ("helper", "general-purpose"):
        return None
    home = home or Path.home()
    places = [Path(project) / d / f"{kind}{ext}" for d, ext in AGENT_DIRS.get(agent, [])] if project else []
    if agent in HOME_DIRS:
        places.append(home / HOME_DIRS[agent] / f"{kind}{AGENT_DIRS[agent][0][1]}")
    for p in places:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines[:40], 1):
            if m := _MODEL.match(line):
                return str(p), i, m.group(1)
        return str(p), 0, ""
    return None


def peer_saving(calls: list[Call], pr) -> tuple[float, str]:
    """What these calls would have cost on the cheaper model of the same family, and that model."""
    diff, peer_name = 0.0, ""
    for c in calls:
        peer = _cheaper_peer(c.model)
        if not peer:
            continue
        rates, _, _ = pricemod.rates(peer, pr.prices)
        rates = pricemod.for_prompt(rates, c.usage.context)
        diff += max(0.0, pr.total(c) - sum(pricemod.cost(c.usage, rates).values()))
        peer_name = peer_name or peer
    return diff, peer_name


def where_set(s: Session, kind: str, peer: str, home: Path | None = None) -> str:
    """One sentence: where this helper's model is set and what to change."""
    found = agent_file(s.agent, kind, s.cwd, home)
    if found and found[1]:
        return f"`{found[0]}` line {found[1]} sets `model: {found[2]}`: change it to a cheaper one ({peer} or similar)."
    if found:
        return f"`{found[0]}` has no `model:` line, so the helper takes this chat's model: add a cheaper one."
    if s.agent == "claude-code":
        return (f"“{kind or 'helper'}” is a built-in helper with no file of its own: it takes this chat's model. "
                "Have the step that starts it name a cheaper model, or start a helper of your own with a cheaper "
                "model in its file (`.claude/agents/<name>.md`, `model: sonnet`).")
    from ..helper_agent import FILE
    return ("It has no file of its own here, so it takes this chat's model: give it one with a cheaper model "
            f"({FILE.get(s.agent, 'your agent’s helper file')}).")


def helper_model(s: Session, pr, home: Path | None = None) -> Fix | None:
    groups: dict[str, list[Helper]] = {}
    for h in s.helpers:
        if h.calls and read_only(h) and any(_cheaper_peer(c.model) for c in h.calls):
            groups.setdefault(h.agent_type or "helper", []).append(h)
    hits = []
    for kind, hs in groups.items():
        calls = [c for h in hs for c in h.calls]
        diff, peer = peer_saving(calls, pr)
        if diff > 0:
            hits.append((diff, kind, hs, calls, peer))
    total = sum(x[0] for x in hits)
    if not hits or total < MIN_SAVING_USD:
        return None
    hits.sort(key=lambda x: -x[0])
    items, asks, turns, calls_all = [], [], set(), []
    for diff, kind, hs, calls, peer in hits:
        model = calls[0].model
        tokens = sum(c.usage.context + c.usage.output for c in calls)
        what = "; ".join(sorted({h.description for h in hs if h.description}))[:120]
        place = where_set(s, kind, peer, home)
        items.append(f"{len(hs)} “{kind}” helper{'s' if len(hs) > 1 else ''} on {model} ({tokens:,} tokens, "
                     f"saved ${diff:.2f} on {peer}): {what}. {place}")
        asks.append(f"“{kind}” ({what}): {place}")
        turns |= {h.turn for h in hs}
        calls_all += [(c.agent, c.n) for c in calls]
    n = sum(len(x[2]) for x in hits)
    # a built-in helper doing reviews: a reviewer helper of the user's own, on a cheaper model, findings only
    from .. import workspace
    from ..helper_agent import make_stick, prompt
    ws = workspace.of(s, home)
    built_in = [h for x in hits if not agent_file(s.agent, x[1], s.cwd, home) for h in x[2]]
    reviews = [h for h in built_in if _REVIEW.search(h.description or "")]
    extra = ""
    if reviews:
        have = ws.helper_file("reviewer")
        extra = " " + (make_stick(s.agent, ws.show(have), len(reviews), ws, "reviewer") if have
                       else prompt(s.agent, "reviewer", ws))
        items.append(f"{len(reviews)} review{'s' if len(reviews) > 1 else ''} ran on a built-in helper: a reviewer "
                     "helper of your own sets a cheaper model once and sends back only the findings.")
    return Fix(
        id="helper-model",
        title=f"Run read and review helpers on a cheaper model ({n} ran on {hits[0][3][0].model})",
        why=(f"{n} helper{'s' if n > 1 else ''} in this chat only read, searched, ran checks or reviewed, on the most "
             "expensive model. That work rarely needs it; a cheaper model of the same family costs a fraction."),
        saving_usd=total, saving_tokens=0, where=turns_where(turns), turns=sorted(turns), items=items[:6],
        per_turn={t: 0 for t in turns}, calls=calls_all,
        how=("Change the model where the helper is set up (below). A new chat picks it up. Trade-off: for a "
             "security or design review, keep the top model if its findings matter more than the cost."),
        ask=("Some helpers in this project run on the most expensive model for read-only or review work. Make them "
             "use a cheaper model: " + " ".join(asks) + extra + " Propose the exact change for each and wait for my yes "
             "before editing any file."),
    )
