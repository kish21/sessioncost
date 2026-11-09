"""The report model's building blocks: what each call cost (Pricer), its kind, the parts of what it sent and
wrote, and how the money line is labelled."""
from __future__ import annotations


from datetime import datetime

from . import prices as pricemod
from .model import Call
from .risks import _SECRET_VALUE
from . import guard as guardmod



def _hide_keys(text: str) -> str:
    """A step's words as the report shows them: any API key or token in a command (sk-..., ghp_..., AKIA...) hidden."""
    return _SECRET_VALUE.sub("[secret hidden]", text or "")


def guard_summary(session_id: str) -> dict | None:
    """What the live guard did in this chat (guard.py's log), or None when it was not on: each step it blocked or
    asked about (by tool call id), what you allowed, and whether the log's hash chain is intact."""
    p = guardmod.log_path(session_id)
    if not session_id or not p.is_file():
        return None
    v = guardmod.verify_status(p)
    intact, entries = v["status"] in ("intact", "empty"), v["entries"]
    steps = {}
    for e in entries:
        if e.get("event") == "pre" and e.get("decision") in ("blocked", "asked"):
            steps[e.get("id")] = {"decision": e["decision"], "marks": e.get("marks", []), "what": _hide_keys(e.get("what", ""))}
        elif e.get("event") == "allowed" and e.get("id") in steps:
            steps[e["id"]]["allowed"] = True
    inv = next((e for e in reversed(entries) if e.get("event") == "inventory"), None)
    return {"intact": intact, "status": v["status"], "lines": len(entries), "steps": steps,
            "injections": sum(1 for e in entries if e.get("event") == "injection" and e.get("at") != "start"),
            "loaded": [w for e in entries if e.get("event") == "injection" and e.get("at") == "start" for w in e.get("where") or []],
            "keys_seen": sum(1 for e in entries if e.get("event") == "key_seen"),
            "strict": any(e.get("strict") for e in entries if e.get("event") == "start"),
            "inventory": {k: inv.get(k) for k in ("items", "first", "new", "changed", "removed") if k in inv} if inv else None,
            "blocked": sum(1 for x in steps.values() if x["decision"] == "blocked"),
            "asked": sum(1 for x in steps.values() if x["decision"] == "asked"),
            "allowed": sum(1 for x in steps.values() if x.get("allowed")),
            "untrusted": sum(1 for e in entries if e.get("event") in ("untrusted", "injection"))}   # an injection came in too

SAID = ("typed", "command", "queued", "answer")    # what you said, as opposed to what the agent added

CATEGORIES = [
    ("start", "Start (first call)"),
    ("questions", "Asking you questions"),
    ("talk", "Talking to you"),
    ("reading", "Reading and searching"),
    ("commands", "Running commands"),
    ("writing", "Writing files"),
    ("research", "Research and helpers"),
    ("reviewer", "The agent checking its own commands"),   # Codex's safety reviewer (codex-auto-review)
    ("other", "Other steps"),
    ("closing", "Closing message"),
]
CLASS_LABELS = [
    ("input", "New input, not cached"),
    ("cache_write_5m", "New input, cached for 5 minutes"),
    ("cache_write_1h", "New input, cached for 1 hour"),
    ("cache_read", "Re-read from cache"),
    ("output", "Written by the model"),
]


def tok(chars: int) -> int:
    return max(0, int(chars) // 4)


def split_exact(estimates: list[tuple[str, int]], total: int) -> list[tuple[str, int]]:
    """Scale estimated sizes so they add up exactly to `total` (largest-remainder rounding)."""
    s = sum(v for _, v in estimates)
    if total <= 0:
        return [(k, 0) for k, _ in estimates]
    if s <= 0:
        return [(k, 0) for k, _ in estimates[:-1]] + [(estimates[-1][0], total)] if estimates else []
    raw = [(k, v * total / s) for k, v in estimates]
    out = [[k, int(v)] for k, v in raw]
    left = total - sum(v for _, v in out)
    order = sorted(range(len(raw)), key=lambda i: -(raw[i][1] - int(raw[i][1])))
    for i in order[:left]:
        out[i][1] += 1
    return [(k, v) for k, v in out]


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def hhmm(d: datetime | None) -> str:
    return d.astimezone().strftime("%H:%M") if d else ""


def secs(a: datetime | None, b: datetime | None) -> float:
    return max(0.0, (b - a).total_seconds()) if a and b else 0.0


class Pricer:
    def __init__(self, prices: dict):
        self.prices = prices
        self.unchecked: set[str] = set()

    def rates(self, c: Call) -> dict:
        r, checked, _ = pricemod.rates(c.model, self.prices)
        if not checked:
            self.unchecked.add(c.model or "unknown model")
        return pricemod.for_prompt(r, c.usage.context)      # a long prompt may cost more (Haiku 5.5, GPT over 272K)

    def cost(self, c: Call) -> dict:
        return pricemod.cost(c.usage, self.rates(c))

    def total(self, c: Call) -> float:
        return sum(self.cost(c).values())


def category(c: Call, first_main: bool, last_main: bool) -> str:
    kinds = {a.kind for a in c.actions}
    if c.agent != "main":
        return "research"
    if first_main:
        return "start"
    if "question" in kinds:
        return "questions"
    if kinds & {"write", "edit"}:
        return "writing"
    if "command" in kinds:
        return "commands"
    if kinds & {"read", "search"}:
        return "reading"
    if kinds & {"helper", "web"}:
        return "research"
    if kinds:
        return "other"
    return "closing" if last_main else "talk"


def call_label(c: Call) -> str:
    if c.actions:
        labels = [a.label for a in c.actions]
        if len(labels) > 4:
            return "; ".join(labels[:3]) + f"; and {len(labels) - 3} more"
        return "; ".join(labels)
    if c.first_text:
        return f"wrote to you: “{c.first_text}”"
    return "thought, wrote nothing visible"


def sent_parts(c: Call, r: dict, setup_tokens: int, carried_max: int | None, cache_known: bool = True) -> list[dict]:
    """What was sent, by what it is: fixed setup (step 0) + earlier conversation (step 1) + new (step 2) = the call's
    context, exactly. Earlier conversation is what the previous call of this thread sent and wrote, minus the setup,
    whether it came from the cache or not (after a pause the cache expires and it is sent again at full price).
    The cache only sets the price: cache reads are counted against the setup first, then the conversation."""
    u = c.usage
    cr_rate, new_rate = r["cache_read"], pricemod.new_input_rate(u, r)
    setup = min(setup_tokens, u.context) if c.agent == "main" else 0
    if carried_max is None:                       # first call of its thread: nothing earlier to carry
        carried_max = 0 if c.agent == "main" else u.cache_read
    carried = max(0, min(u.context - setup, carried_max))
    new = u.context - setup - carried
    cr_left = u.cache_read
    s_cr = min(setup, cr_left); cr_left -= s_cr
    h_cr = min(carried, cr_left); cr_left -= h_cr
    n_cr = min(new, cr_left)
    price = lambda tokens, cr: (cr * cr_rate + (tokens - cr) * new_rate) / 1e6
    parts = []
    if setup_tokens and c.agent == "main":
        parts.append({"what": "fixed setup (instructions, tools, skills: step 0)", "tokens": setup,
                      "usd": price(setup, s_cr), "estimated": False})
    # the newest piece (the last reply and tool results) is always written to the cache on this call, not read:
    # only call it an expired cache when most of the earlier conversation missed it, and the chat was not compacted
    # (a context under 40% of the last call is a compaction: its new summary always misses the cache)
    compacted = carried_max > 20000 and u.context < carried_max * 0.4
    # (a log that records no cache reads, Cursor without its usage export, cannot show an expired cache at all)
    expired = cache_known and carried > 2000 and h_cr < carried / 2 and not compacted
    parts.append({"what": "earlier conversation" + (" (sent again at full price: the cache had expired)" if expired else ""),
                  "tokens": carried, "usd": price(carried, h_cr), "estimated": False})
    parts.append({"what": "new input (sent for the first time)", "tokens": new, "usd": price(new, n_cr),
                  "estimated": False})
    return parts


def written_parts(c: Call, r: dict) -> list[dict]:
    """What was written: messages + files + other tool calls + hidden thinking = the call's output, exactly."""
    u = c.usage
    files = sum(a.written_chars for a in c.actions)
    tools = sum(max(0, a.args_chars - a.written_chars) for a in c.actions)
    est = [("messages to you", tok(c.text_chars)), ("files written", tok(files)),
           ("tool calls (commands, questions, searches)", tok(tools))]
    est = [(k, v) for k, v in est if v]
    if u.thinking is not None:
        think = min(u.thinking, u.output)
        exact_think = True
    else:
        think = max(0, u.output - sum(v for _, v in est))
        exact_think = False
    visible = u.output - think
    if visible and not est:
        est = [("other output (formatting)", 1)]
    parts = [{"what": k, "tokens": v, "usd": v * r["output"] / 1e6, "estimated": True}
             for k, v in split_exact(est, visible)] if visible else []
    parts.append({"what": "hidden thinking", "tokens": think, "usd": think * r["output"] / 1e6,
                  "estimated": not exact_think})
    return parts


def checked_day(as_of: str) -> str:
    """'2026-10-29' -> '29 Oct 2026': the day the price list was checked, said so it is not read as the report's date."""
    try:
        d = datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        return as_of
    return f"{d.day} {d.strftime('%b %Y')}"


def money_label(exact: bool, as_of: str, subscription: bool, tokens_exact: bool = True) -> tuple[str, str]:
    """(long label, short label)"""
    day = checked_day(as_of)
    if not tokens_exact:
        return ("estimate: this log keeps no token counts, so tokens are estimated from the size of the text, at "
                "API prices checked %s%s" % (day, "; your plan is not billed per token" if subscription else ""),
                "estimate")
    if subscription:
        return ("API-equivalent: what these tokens would cost at API prices (checked %s). Your plan is not billed per "
                "token." % day, "API-equivalent")
    if exact:
        # the token counts are exact; the dollars are those tokens at the list prices, so the label names the prices
        return ("at the API price list, checked %s" % day, "at API prices checked %s" % day)
    return ("estimate: at least one model has no checked price (prices checked %s)" % day, "estimate")
