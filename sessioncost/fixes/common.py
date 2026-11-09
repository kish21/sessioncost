"""Shared by the checks: tokens from characters, yes-turns and questions, what a call's input cost, turn lists."""
from __future__ import annotations

import re

from .. import prices as pricemod
from ..model import Call, Session

CONFIRM = {"y", "yes", "yes please", "yep", "yeah", "ok", "okay", "k", "sure", "go", "go ahead", "go on", "do it",
           "proceed", "continue", "save", "save it", "confirm", "confirmed", "approve", "approved", "lgtm",
           "sounds good", "looks good", "ship it", "fine"}


def tok(chars: int) -> int:
    return max(0, int(chars) // 4)


def helper_name(s: Session, agent: str) -> str:
    """A helper as the report names it: its task ("Vision Co-Founder Reviewer"), else its id."""
    h = next((h for h in s.helpers if h.calls and h.calls[0].agent == agent), None)
    return f"“{h.description}”" if h and h.description else agent.split(":", 1)[-1]


def is_confirm(text: str, options: bool = False) -> bool:
    """A reply that only says go ahead. With `options` (the agent listed a) b) c) choices), a bare letter too."""
    t = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    return bool(t) and len(t) <= 25 and (t in CONFIRM or (options and t in ("a", "b", "c")))


ASK_RE = re.compile(r"\b(shall i|should i|do you want|would you like|want me to|may i|can i|ok to|okay to|is that ok|"
                    r"sound good|good to go|ready (?:for me )?to|before i (?:go|continue|proceed))\b", re.I)
OPTIONS_RE = re.compile(r"(?:^|\n)\s*[*_]*[a-c][).][*_]*\s+\S")
OPTIONS_TAIL = 600       # lettered choices count when they are in the end of the message


def lettered(text: str) -> bool:
    """Did the agent's message end with lettered choices (a) ... b) ...), the way many skills ask?"""
    return bool(OPTIONS_RE.search((text or "")[-OPTIONS_TAIL:]))


def asked(text: str) -> bool:
    """Did the agent's message end by asking the user something? A finished report ("Done. All tests pass.") did
    not, so an "ok" after it is the user saying thanks, not a stop the agent made."""
    t = re.sub(r"[\s*_`>)\]]+$", "", text or "")
    tail = re.split(r"(?<=[.!?])\s+", t)[-2:]
    return t.endswith("?") or any("?" in x for x in tail) or bool(ASK_RE.search(" ".join(tail))) or lettered(text)


def sent_usd(c: Call, r: dict) -> float:
    cc = pricemod.cost(c.usage, r)
    return cc["input"] + cc["cache_write_5m"] + cc["cache_write_1h"] + cc["cache_read"]



def _where(turns) -> str:
    ts = sorted(turns)
    return ("turn " if len(ts) == 1 else "turns ") + ", ".join(str(t) for t in ts) if ts else ""


turns_where = _where


def setup_tokens(s: Session) -> int:
    if not s.setup.known or not s.calls:
        return 0
    return min(tok(s.setup.chars), min(c.usage.context for c in s.calls))


def short_target(target: str, cwd: str = "") -> str:
    t = (target or "").replace("\\", "/").strip()
    c = (cwd or "").replace("\\", "/").rstrip("/")
    if c and t.lower().startswith(c.lower() + "/"):
        t = t[len(c) + 1 :]
    if "/" in t and len(t) > 48:
        t = t.rsplit("/", 1)[-1]
    return t[:60]


def first_and_cached(tokens: int, calls: list[Call], pr) -> float:
    if not calls or tokens <= 0:
        return 0.0
    r0 = pr.rates(calls[0])
    usd = tokens * pricemod.new_input_rate(calls[0].usage, r0) / 1e6
    usd += sum(tokens * pr.rates(c)["cache_read"] / 1e6 for c in calls[1:])
    return usd
