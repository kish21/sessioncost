"""From a Session to the full report model: exact cost per call, the drill-down parts (which always add up to their
parent), the turn rows, where the cost went, the fixed setup, timing and the fixes.

Token sizes that the log does not give directly (how much of a call's input was the fixed setup, how much of its
output was a file vs a message) are estimated as characters / 4 and then scaled so the parts add up exactly to the
token counts the log does give. Those parts are marked `estimated`.
"""
from __future__ import annotations

import re

from datetime import datetime

from . import fixes as fixmod
from . import prices as pricemod
from .model import Call, Session

CATEGORIES = [
    ("start", "Start (first call)"),
    ("questions", "Asking you questions"),
    ("talk", "Talking to you"),
    ("reading", "Reading and searching"),
    ("commands", "Running commands"),
    ("writing", "Writing files"),
    ("research", "Research and helpers"),
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
        return r

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


def sent_parts(c: Call, r: dict, setup_tokens: int) -> list[dict]:
    """What was sent: fixed setup + carried history + new input = the call's context, exactly."""
    u = c.usage
    cr_rate, new_rate = r["cache_read"], pricemod.new_input_rate(u, r)
    setup = min(setup_tokens, u.context) if c.agent == "main" else 0
    s_cr = min(setup, u.cache_read)
    s_new = setup - s_cr
    carried = u.cache_read - s_cr
    new = u.new_input - s_new
    parts = []
    if setup_tokens and c.agent == "main":
        parts.append({"what": "fixed setup (system prompt, tool definitions, lists)", "tokens": setup,
                      "usd": (s_cr * cr_rate + s_new * new_rate) / 1e6, "estimated": True})
    parts.append({"what": "carried history (re-read from cache)", "tokens": carried, "usd": carried * cr_rate / 1e6,
                  "estimated": bool(setup_tokens and c.agent == "main")})
    parts.append({"what": "new input (read for the first time)", "tokens": new, "usd": new * new_rate / 1e6,
                  "estimated": bool(s_new)})
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


def money_label(exact: bool, as_of: str, subscription: bool, tokens_exact: bool = True) -> tuple[str, str]:
    """(long label, short label)"""
    if not tokens_exact:
        return ("estimate: this log keeps no token counts, so tokens are estimated from the size of the text, at "
                "API prices as of %s%s" % (as_of, "; your plan is not billed per token" if subscription else ""),
                "estimate")
    if subscription:
        return ("API-equivalent: what these tokens would cost at API prices (as of %s). Your plan is not billed per "
                "token." % as_of, "API-equivalent")
    if exact:
        return ("exact, at the API price list as of %s" % as_of, "exact (API prices %s)" % as_of)
    return ("estimate: at least one model has no checked price (prices as of %s)" % as_of, "estimate")


def analyse(s: Session, prices: dict, subscription: bool = False, engines: list | None = None) -> dict:
    P = Pricer(prices)
    subscription = subscription or not s.billed_per_token
    setup_tokens = tok(s.setup.chars) if s.setup.known else 0
    main_calls = s.calls
    first_id = main_calls[0].msg_id if main_calls else None
    last_id = main_calls[-1].msg_id if main_calls else None

    # ---- calls (main first, then helpers), each with its parts
    calls: list[dict] = []
    by_obj: dict[int, dict] = {}
    for c in s.all_calls:
        r = P.rates(c)
        cc = pricemod.cost(c.usage, r)
        total = sum(cc.values())
        sp = sent_parts(c, r, setup_tokens)
        wp = written_parts(c, r)
        d = {
            "agent": c.agent, "n": c.n, "turn": c.turn, "model": c.model,
            "ts": iso(c.ts_first), "end": iso(c.ts_last), "time": hhmm(c.ts_first),
            "category": category(c, c.msg_id == first_id and c.agent == "main",
                                 c.msg_id == last_id and c.agent == "main"),
            "label": call_label(c),
            "actions": [{"label": a.label, "kind": a.kind, "result_tokens": tok(a.result_chars), "tool": a.tool,
                         "secs": round(secs(a.ts, a.result_ts), 1) if a.ts and a.result_ts else None,
                         "error": a.is_error} for a in c.actions],
            "usage": {"input": c.usage.fresh_input, "cache_write_5m": c.usage.cache_write_5m,
                      "cache_write_1h": c.usage.cache_write_1h, "cache_read": c.usage.cache_read,
                      "output": c.usage.output, "thinking": c.usage.thinking},
            "context": c.usage.context,
            "cost_by_class": cc,
            "usd": total,
            "sent": {"tokens": c.usage.context, "usd": sum(p["usd"] for p in sp), "parts": sp},
            "written": {"tokens": c.usage.output, "usd": sum(p["usd"] for p in wp), "parts": wp},
            "mix": {"written": cc["output"], "new": cc["input"] + cc["cache_write_5m"] + cc["cache_write_1h"],
                    "reread": cc["cache_read"]},
            "new_items": [],
        }
        calls.append(d)
        by_obj[id(c)] = d

    # what came in before each main call (informational: names the sources of "new input")
    prev = None
    for t in s.turns:
        for i, c in enumerate(t.calls):
            items = []
            if prev is not None:
                items += [{"what": f"result of: {a.label}", "tokens": tok(a.result_chars)}
                          for a in prev.actions if a.result_chars]
            for e in t.events:
                if i == 0:
                    keep = e.ts is None or c.ts_first is None or e.ts <= c.ts_first
                else:
                    keep = bool(e.ts and prev is not None and prev.ts_last and c.ts_first
                                and prev.ts_last < e.ts <= c.ts_first)
                if keep:
                    said = e.kind in ("typed", "command", "queued", "answer")
                    items.append({"what": e.label + (f": “{e.text}”" if said else ""),
                                  "tokens": tok(e.chars)})
            by_obj[id(c)]["new_items"] = [x for x in items if x["tokens"]]
            prev = c

    total_usd = sum(d["usd"] for d in calls)
    main_usd = sum(by_obj[id(c)]["usd"] for c in main_calls)
    helper_usd = total_usd - main_usd

    # ---- timing
    question_wait = 0.0
    turn_rows = []
    for t in s.turns:
        q = sum(secs(a.ts, a.result_ts) for c in t.calls for a in c.actions if a.kind == "question")
        question_wait += q
        start = t.ts or (t.calls[0].ts_first if t.calls else None)
        ends = [x for c in t.calls for x in [c.ts_last] + [a.result_ts for a in c.actions] if x]
        end = max(ends) if ends else start
        minutes = max(0.0, secs(start, end) - q) / 60
        tc = [by_obj[id(c)] for c in t.calls]
        turn_rows.append({
            "n": t.n, "kind": t.kind, "text": plain(t.text), "time": hhmm(start), "ts": iso(start),
            "waited_min": round(t.waited_s / 60, 1), "question_wait_min": round(q / 60, 1),
            "minutes": round(minutes, 1),
            "usd": sum(d["usd"] for d in tc),
            "helper_usd": sum(by_obj[id(c)]["usd"] for c in s.helper_calls if c.turn == t.n),
            "sent": sum(d["sent"]["tokens"] for d in tc), "written": sum(d["written"]["tokens"] for d in tc),
            "helper_sent": sum(by_obj[id(c)]["sent"]["tokens"] for c in s.helper_calls if c.turn == t.n),
            "calls": [calls.index(d) for d in tc],
            "mix": {k: sum(d["mix"][k] for d in tc) for k in ("written", "new", "reread")},
            "events": [{"kind": e.kind, "label": e.label, "text": e.text, "tokens": tok(e.chars)} for e in t.events],
            "fixes": [],
        })
    waiting = sum(t.waited_s for t in s.turns) + question_wait
    working = sum(t["minutes"] for t in turn_rows)

    # ---- where the cost went
    by_step = {k: {"key": k, "label": lbl, "usd": 0.0, "calls": 0} for k, lbl in CATEGORIES}
    for d in calls:
        by_step[d["category"]]["usd"] += d["usd"]
        by_step[d["category"]]["calls"] += 1
    by_class = []
    for k, lbl in CLASS_LABELS:
        tokens = sum(d["usage"][k] for d in calls)
        by_class.append({"key": k, "label": lbl, "tokens": tokens, "usd": sum(d["cost_by_class"][k] for d in calls)})
    thinking_known = all(d["usage"]["thinking"] is not None for d in calls)
    thinking = sum(p["tokens"] for d in calls for p in d["written"]["parts"] if p["what"] == "hidden thinking")

    # ---- setup
    used = {a.tool for a in s.actions()}
    setup = {"known": s.setup.known, "tokens": setup_tokens,
             "parts": [{"what": p.what, "tokens": tok(p.chars)} for p in s.setup.parts],
             "tools": [{"name": p.what, "tokens": tok(p.chars),
                        "used": p.what in used} for p in s.setup.tools]}
    setup["unused"] = [t["name"] for t in setup["tools"] if not t["used"]]
    setup["unused_tokens"] = sum(t["tokens"] for t in setup["tools"] if not t["used"])

    # ---- replay order: every call by time, with the running total
    order = sorted(range(len(calls)), key=lambda i: (calls[i]["ts"] or "", i))
    running = 0.0
    replay = []
    for i in order:
        running += calls[i]["usd"]
        replay.append({"call": i, "cum_usd": running})

    as_of = str(prices.get("as_of") or "unknown date")
    split_known = all(c.usage.write_split_known for c in s.all_calls)
    exact = not P.unchecked and s.tokens_exact and split_known
    long_label, short_label = money_label(exact, as_of, subscription, s.tokens_exact)
    if not exact and long_label.startswith("exact"):
        long_label, short_label = ("estimate: this log does not say which cache writes were kept 5 minutes or 1 hour "
                                   "(they cost 1.25x and 2x the input price), so all are priced at 5 minutes "
                                   "(prices as of %s)" % as_of, "estimate")
    reasons = []      # why the total is not exact, biggest first
    if not s.tokens_exact:
        reasons.append("This log keeps no token counts, so tokens are estimated from the size of the text.")
    if P.unchecked:
        reasons.append("No confirmed price for " + ", ".join(sorted(P.unchecked)) +
                       " (a third-party or default rate was used).")
    if not split_known:
        reasons.append("The log does not split cache writes into 5-minute and 1-hour, so all are priced at the "
                       "5-minute rate.")
    why = " ".join(reasons) or "Every model has a confirmed price and every token class is in the log."
    if subscription:
        why += " Your plan is not billed per token: read the figures as API-equivalent."


    out = {
        "tool": "sessioncost",
        "meta": {
            "title": plain(s.title), "agent": s.agent, "agent_label": {"claude-code": "Claude Code", "codex": "Codex", "antigravity": "Antigravity",
                                                       "cursor": "Cursor"}.get(s.agent, s.agent),
            "agent_version": s.agent_version, "model": s.model,
            "models": sorted({c.model for c in s.all_calls if c.model}), "session_id": s.session_id,
            "project": _folder(s.cwd), "started": iso(s.started), "ended": iso(s.ended),
            "date": s.started.astimezone().strftime("%d %b %Y") if s.started else "",
        },
        "money": {"label": long_label, "short": short_label, "as_of": as_of, "exact": exact,
                  "subscription": subscription, "unchecked_models": sorted(P.unchecked), "why": why,
                  "tokens_exact": s.tokens_exact, "write_split_known": split_known,
                  "own_cost_usd": s.own_cost_usd},
        "totals": {
            "usd": total_usd, "main_usd": main_usd, "helpers_usd": helper_usd,
            "calls": len(main_calls), "helper_calls": len(s.helper_calls),
            "turns": len([t for t in s.turns if t.n > 0]),
            "working_min": round(working, 1), "waiting_min": round(waiting / 60, 1),
            "context_max": max((d["context"] for d in calls if d["agent"] == "main"), default=0),
            "sent": sum(d["sent"]["tokens"] for d in calls),
            "output": sum(d["usage"]["output"] for d in calls), "thinking": thinking,
            "thinking_known": thinking_known,
            "by_class": by_class,
            "by_step": [v for v in by_step.values() if v["calls"]],
        },
        "setup": setup,
        "turns": turn_rows,
        "calls": calls,
        "replay": replay,
        "helpers": [{"id": h.id, "description": h.description, "type": h.agent_type, "turn": h.turn,
                     "calls": len(h.calls), "usd": sum(by_obj[id(c)]["usd"] for c in h.calls),
                     "tokens": sum(c.usage.context + c.usage.output for c in h.calls)} for h in s.helpers],
        "notes": list(s.notes),
    }

    # ---- fixes: built-in detectors + any engine plugged in (see fixes.py)
    engines = engines if engines is not None else fixmod.load_engines()
    out["fix_engines"] = [getattr(e, "name", "built-in") for e in engines]
    s.fixes = fixmod.find(s, P, engines)
    out["fixes"] = [{"id": f.id, "title": f.title, "why": f.why, "saving_usd": f.saving_usd,
                     "saving_tokens": f.saving_tokens, "where": f.where, "turns": f.turns, "items": f.items,
                     "source": f.source} for f in s.fixes]
    for f in s.fixes:
        for tr in turn_rows:
            if tr["n"] in f.turns:
                tr["fixes"].append(f.id)
    return out


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
PATH_RE = re.compile(r"(?<![\w:/\\])(?:[A-Za-z]:[\\/]+|~[\\/]+|[\\/]+)(?:[^\s\\/\"'`()\[\]]+[\\/]+)+([^\s\\/\"'`()\[\]]*)")


def plain(text: str) -> str:
    """What a person reads: markdown links become their text, file paths become their last name
    ("[$architect](C:/Users/me/SKILL.md)" -> "$architect"). Also keeps folder names out of shared screenshots."""
    text = LINK_RE.sub(lambda m: m.group(1), text or "")
    text = re.sub(r"@\[([^\]]+)\]", lambda m: "@" + re.split(r"[\\/]", m.group(1).rstrip("\\/"))[-1], text)
    return PATH_RE.sub(lambda m: m.group(1) or "folder", text).strip()


def _folder(cwd: str) -> str:
    return cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if cwd else ""
