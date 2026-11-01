"""From a Session to the full report model: exact cost per call, the drill-down parts (which always add up to their
parent), the turn rows, where the cost went, the fixed setup, timing and the fixes.

Token sizes that the log does not give directly (how much of a call's input was the fixed setup, how much of its
output was a file vs a message) are estimated as characters / 4 and then scaled so the parts add up exactly to the
token counts the log does give. Those parts are marked `estimated`.
"""
from __future__ import annotations

import os
import re

from datetime import datetime

from . import fixes as fixmod, rules
from . import __version__, prices as pricemod
from .model import Call, Session, Usage
from .readers._util import clean_cmd
from .risks import _SECRET_VALUE, ALLOW_FILE, NEVER, SEVERITY, WATCH, read_allow
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
    intact, entries = guardmod.verify_log(p)
    steps = {}
    for e in entries:
        if e.get("event") == "pre" and e.get("decision") in ("blocked", "asked"):
            steps[e.get("id")] = {"decision": e["decision"], "marks": e.get("marks", []), "what": _hide_keys(e.get("what", ""))}
        elif e.get("event") == "allowed" and e.get("id") in steps:
            steps[e["id"]]["allowed"] = True
    inv = next((e for e in reversed(entries) if e.get("event") == "inventory"), None)
    return {"intact": intact, "lines": len(entries), "steps": steps,
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


def analyse(s: Session, prices: dict, subscription: bool = False, engines: list | None = None) -> dict:
    P = Pricer(prices)
    subscription = subscription or not s.billed_per_token
    main_calls = s.calls
    # Step 0, the fixed setup: the first call has no history yet, so everything it sent except what you said is the
    # agent's own setup (instructions, tools, skills, project rules). Measured this way it includes the parts the log
    # does not spell out (Claude Code's built-in instructions), and it works for agents that log no setup at all.
    setup_logged = tok(s.setup.chars) if s.setup.known else 0
    setup_tokens = setup_logged
    first = main_calls[0] if main_calls else None
    if first is not None:
        t0 = next((t for t in s.turns if first in t.calls), None)
        said = sum(tok(e.chars) for e in (t0.events if t0 else [])
                   if e.kind in SAID and (e.ts is None or first.ts_first is None or e.ts <= first.ts_first))
        setup_tokens = max(first.usage.context - said, 0)     # measured; the text estimate only splits it
    first_id = main_calls[0].msg_id if main_calls else None
    # the agent checking its own commands before they run (Codex): its own line, not "helpers"
    reviewer_calls = {id(c) for h in s.helpers if h.agent_type == "reviewer" for c in h.calls}
    last_id = main_calls[-1].msg_id if main_calls else None

    # ---- calls (main first, then helpers), each with its parts
    calls: list[dict] = []
    by_obj: dict[int, dict] = {}
    prev_of: dict[str, Call] = {}                 # the previous call of each thread (main, or one helper)
    for c in s.all_calls:
        r = P.rates(c)
        cc = pricemod.cost(c.usage, r)
        total = sum(cc.values())
        pv = prev_of.get(c.agent)
        prev_setup = min(setup_tokens, pv.usage.context) if pv is not None and c.agent == "main" else 0
        sp = sent_parts(c, r, setup_tokens, None if pv is None else pv.usage.context - prev_setup + pv.usage.output,
                        cache_known=s.tokens_exact)      # estimated tokens (Cursor, VS Code Copilot Chat): cache unknown
        prev_of[c.agent] = c
        sp_by = {"setup": sum(x["tokens"] for x in sp if x["what"].startswith("fixed setup")),
                 "history": sum(x["tokens"] for x in sp if x["what"].startswith("earlier conversation")),
                 "new": sum(x["tokens"] for x in sp if x["what"].startswith("new input"))}
        wp = written_parts(c, r)
        d = {
            "agent": c.agent, "n": c.n, "turn": c.turn, "model": c.model,
            "ts": iso(c.ts_first), "end": iso(c.ts_last), "time": hhmm(c.ts_first),
            "category": "reviewer" if id(c) in reviewer_calls else category(c, c.msg_id == first_id and c.agent == "main",
                                                                         c.msg_id == last_id and c.agent == "main"),
            "label": _hide_keys(call_label(c)),
            "actions": [{"id": a.id, "label": _hide_keys(a.label), "kind": a.kind, "result_tokens": tok(a.result_chars), "tool": a.tool,
                         "secs": round(secs(a.ts, a.result_ts), 1) if a.ts and a.result_ts else None,
                         "error": a.is_error, "risks": [r for r in WATCH if r in a.risks],
                         **({"allowed": a.allowed} if a.allowed else {}),
                         **({"after": list(a.chain_from)} if a.chain_from else {}),
                         # the whole command or path (a reviewer reads all of it), key-shaped values hidden
                         **({"where": _hide_keys(clean_cmd(a.target.replace("\n", " · "), s.cwd, 300))}
                            if a.target else {})} for a in c.actions],
            "usage": {"input": c.usage.fresh_input, "cache_write_5m": c.usage.cache_write_5m,
                      "cache_write_1h": c.usage.cache_write_1h, "cache_read": c.usage.cache_read,
                      "output": c.usage.output, "thinking": c.usage.thinking},
            "context": c.usage.context,
            "cost_by_class": cc,
            "usd": total,
            "sent": {"tokens": c.usage.context, "usd": sum(p["usd"] for p in sp), "parts": sp, **sp_by},
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
                items += [{"what": _hide_keys(f"result of: {a.label}"), "tokens": tok(a.result_chars)}
                          for a in prev.actions if a.result_chars]
            for e in t.events:
                if i == 0:
                    keep = e.ts is None or c.ts_first is None or e.ts <= c.ts_first
                else:
                    keep = bool(e.ts and prev is not None and prev.ts_last and c.ts_first
                                and prev.ts_last < e.ts <= c.ts_first)
                if keep:
                    said = e.kind in SAID
                    items.append({"what": e.label + (f": “{e.text}”" if said else ""),
                                  "tokens": tok(e.chars)})
            by_obj[id(c)]["new_items"] = [x for x in items if x["tokens"]]
            prev = c

    # what the cache re-reads would have cost at the normal input price (the cache is why a session is cheap)
    cache_full_usd = sum(c.usage.cache_read * P.rates(c)["input"] for c in s.all_calls) / 1e6
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
            "sent_by": {k: sum(d["sent"][k] for d in tc) for k in ("setup", "history", "new")},
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
    parts = [{"what": p.what, "tokens": tok(p.chars)} for p in s.setup.parts]
    if setup_logged > setup_tokens and setup_logged:    # the text estimate ran over the measured size: scale it to fit
        parts = [dict(x, tokens=round(x["tokens"] * setup_tokens / setup_logged)) for x in parts]
    elif setup_tokens > setup_logged + 50:             # a few tokens of rounding is not a part worth a line
        parts.append({"what": "the agent's built-in instructions and other start-up text (not spelled out in the log)",
                      "tokens": setup_tokens - setup_logged})
    setup = {"known": s.setup.known, "measured": first is not None, "tokens": setup_tokens, "logged": setup_logged,
             "parts": parts,
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
    # only Claude prices cache writes kept 5 minutes and 1 hour differently; elsewhere the split does not matter
    split_known = s.agent != "claude-code" or all(c.usage.write_split_known for c in s.all_calls)
    exact = not P.unchecked and s.tokens_exact and split_known
    long_label, short_label = money_label(exact, as_of, subscription, s.tokens_exact)
    if not exact and long_label.startswith("at the API"):
        long_label, short_label = ("estimate: this log does not say which cache writes were kept 5 minutes or 1 hour "
                                   "(they cost 1.25x and 2x the input price), so all are priced at 5 minutes "
                                   "(prices checked %s)" % checked_day(as_of), "estimate")
    own = prices.get("own") or []
    if own:
        # your own prices: the dollars are not the list prices, so the label must not say they are
        mine = "your own prices (%s; the list prices checked %s for any model they leave out)" % (
            ", ".join(own), checked_day(as_of))
        if exact and not subscription:
            long_label, short_label = "at " + mine, "at your own prices"
        else:
            long_label += "; with " + mine
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
            "version": __version__, "title": plain(s.title), "agent": s.agent, "agent_label": {"claude-code": "Claude Code", "codex": "Codex", "antigravity": "Antigravity",
                                                       "cursor": "Cursor", "copilot": "Copilot CLI",
                                                       "copilot-vscode": "Copilot Chat (VS Code)"}.get(s.agent, s.agent),
            "agent_version": s.agent_version, "model": s.model,
            "models": sorted({c.model for c in s.all_calls if c.model}), "session_id": s.session_id,
            "project": _folder(s.cwd), "started": iso(s.started), "ended": iso(s.ended),
            # when this report was built, and whether auto mode rebuilds it after each answer (the page says so)
            "made_at": iso(datetime.now().astimezone()), "auto": bool(guardmod._read(guardmod.home() / "config.json", {}).get("auto")),
            "date": s.started.astimezone().strftime("%d %b %Y") if s.started else "",
            # the file each agent reads at the start of every chat (Cursor: AGENTS.md in the project root; a plain
            # file in .cursor/rules is ignored, those need Cursor's .mdc format)
            "rules_file": {"claude-code": "CLAUDE.md", "codex": "AGENTS.md", "cursor": "AGENTS.md",
                           "antigravity": "GEMINI.md", "copilot": ".github/copilot-instructions.md",
                           "copilot-vscode": ".github/copilot-instructions.md"}.get(
                               s.agent, "the agent's instruction file"),
            # which recommended rules are already in the instruction files (read here, never sent)
            "rules_check": rules.check(s.agent, s.cwd),
        },
        "money": {"label": long_label, "short": short_label, "as_of": as_of, "day": checked_day(as_of), "exact": exact,
                  "own": own,
                  "range": cursor_range(s, P) if not s.tokens_exact else None,
                  # a usage export was used but did not cover every turn (a later turn, or an older export)
                  "export_partial": s.agent == "cursor" and not s.tokens_exact and
                                    any(n.startswith("Tokens from Cursor's usage export") for n in s.notes),
                  "subscription": subscription, "unchecked_models": sorted(P.unchecked), "why": why,
                  "tokens_exact": s.tokens_exact, "write_split_known": split_known,
                  "own_cost_usd": s.own_cost_usd,
                  # the price per million of each kind of token, for each model of the session (the report explains them)
                  "rates": {m: {k: P.rates(next(c for c in s.all_calls if c.model == m)).get(k, 0.0)
                                for k in pricemod.CLASSES} for m in sorted({c.model for c in s.all_calls if c.model})}},
        "totals": {
            "usd": total_usd, "main_usd": main_usd, "helpers_usd": helper_usd,
            "calls": len(main_calls), "helper_calls": len(s.helper_calls),
            "turns": len([t for t in s.turns if t.n > 0]),
            "working_min": round(working, 1), "waiting_min": round(waiting / 60, 1),
            "context_max": max((d["context"] for d in calls if d["agent"] == "main"), default=0),
            "sent": sum(d["sent"]["tokens"] for d in calls),
            "sent_by": {k: sum(d["sent"][k] for d in calls) for k in ("setup", "history", "new")},
            "output": sum(d["usage"]["output"] for d in calls), "thinking": thinking,
            "cache_read_full_usd": cache_full_usd,
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
        "chapters": chapters(s, turn_rows, P),
        "top_files": top_files(s),
    }

    # ---- fixes: built-in detectors + any engine plugged in (see fixes.py)
    engines = engines if engines is not None else fixmod.load_engines()
    out["fix_engines"] = [getattr(e, "name", "built-in") for e in engines]
    s.fixes = fixmod.find(s, P, engines)
    call_ix = {(d["agent"], d["n"]): i for i, d in enumerate(calls)}
    out["fixes"] = [{"id": f.id, "title": f.title, "why": f.why, "saving_usd": f.saving_usd,
                     "saving_tokens": f.saving_tokens, "where": f.where, "turns": f.turns, "items": f.items,
                     "source": f.source, "per_turn": {str(k): v for k, v in f.per_turn.items()},
                     "calls": [call_ix[k] for k in f.calls if k in call_ix], "how": f.how, "paste": f.paste}
                    for f in s.fixes]
    for f in s.fixes:
        for tr in turn_rows:
            if tr["n"] in f.turns:
                tr["fixes"].append(f.id)
    # risky steps (risks.py): not a cost, shown above the fixes, whether or not the fix engine is used
    tried = [(c, a) for c in s.all_calls for a in c.actions if "skips_commit_checks" in a.risks]
    out["agents"] = agents(s, by_obj, total_usd)
    # the watch list (risks.py): how many steps carry each mark, main chat and helpers
    out["watch"] = {w: sum(1 for c in s.all_calls for a in c.actions if w in a.risks) for w in WATCH}
    out["watch_meta"] = {"severity": SEVERITY, "allow_file": ALLOW_FILE, "allow_rules": len(read_allow(s.cwd)),
                         "allowed": sum(len(a.allowed) for c in s.all_calls for a in c.actions), "never": sorted(NEVER)}
    out["guard"] = guard_summary(s.session_id)
    out["warnings"] = [{"id": "hook-bypass", "title": "Your agent tried to switch off the commit checks",
                        "times": [hhmm(a.ts or c.ts_first) for c, a in tried],
                        "refused": sum(1 for _, a in tried if a.is_error or {"blocked", "rejected"} & set(a.result_flags)),
                        "how": "Never switch off the commit checks (no SKIP=, no --no-verify). If a check fails, fix "
                               "the cause or tell me which check and why."}] if tried else []
    untested = fixmod.untested_edits(s)
    if untested:
        out["warnings"].append(untested)
    # the private fix engine's input: built only when that engine is plugged in, and absent from public installs
    if os.environ.get("SESSIONCOST_ENGINE"):
        try:
            from .private import summary as summarymod
            out["summary"] = summarymod.build(s, out)
        except ImportError:
            pass
    return out


DID = [("read", "read", "reads"), ("write", "file written", "files written"), ("edit", "edit", "edits"),
       ("search", "search", "searches"), ("web", "web lookup", "web lookups"), ("command", "command", "commands"),
       ("helper", "step with a helper", "steps with helpers"), ("question", "question to you", "questions to you")]


def agents(s: Session, by_obj: dict, total_usd: float) -> list[dict]:
    """Who did the work: the main agent, then each helper, with why it ran, what it did, what it cost and which
    fixes point at its calls. A helper that failed to start is listed too, with no calls."""
    fixes_of: dict[str, list[str]] = {}
    for f in s.fixes:
        for agent in dict.fromkeys(a for a, _ in f.calls):
            fixes_of.setdefault(agent, []).append(f.id)

    def row(name: str, why: str, kind: str, calls: list[Call], agent: str) -> dict:
        kinds: dict[str, int] = {}
        for a in (a for c in calls for a in c.actions):
            kinds[a.kind] = kinds.get(a.kind, 0) + 1
        did = [f"{kinds[k]} {one if kinds[k] == 1 else many}"
               for k, one, many in sorted((d for d in DID if kinds.get(d[0])), key=lambda d: -kinds[d[0]])]
        usd = sum(by_obj[id(c)]["usd"] for c in calls)
        return {"name": name, "why": why, "kind": kind, "calls": len(calls),
                "sent": sum(c.usage.context for c in calls), "output": sum(c.usage.output for c in calls),
                "usd": usd, "share": usd / total_usd if total_usd else 0.0,
                "turns": sorted({c.turn for c in calls}), "did": did[:4], "fixes": fixes_of.get(agent, [])}

    out = [row("Main agent", "your chat with it", "main", s.calls, "main")]
    for h in s.helpers:
        agent = h.calls[0].agent if h.calls else f"helper:{h.id}"
        woke = sorted({c.turn for c in h.calls} - {h.turn})
        why = (f"started by the helper “{h.parent}” in turn {h.turn}" if h.parent else f"started in turn {h.turn}") + (
            f", woken again in turn{'s' if len(woke) > 1 else ''} {', '.join(map(str, woke))}" if woke else "")
        out.append(dict(row(h.description or h.id, why, "reviewer" if h.agent_type == "reviewer" else "helper",
                            h.calls, agent), parent=h.parent))
    names = {h.description for h in s.helpers}
    for c in s.calls:                    # asked for, but never ran: no calls of its own to show
        for a in c.actions:
            if a.kind == "helper" and a.is_error and a.label.startswith("started a helper") and a.target not in names:
                names.add(a.target)
                out.append(dict(row(a.target or "a helper", f"asked for in turn {c.turn}; it did not start",
                                    "failed", [], ""), turns=[c.turn]))
    return out


REUSE_GAP_S = 300       # the low end of a Cursor range: a call this soon after the last one re-read its conversation


def cursor_range(s: Session, P: Pricer) -> dict | None:
    """Cursor records no cache reads, so its cost can only be a range: low if each call that came within 5 minutes of
    the one before re-read that call's conversation from the cache, high if nothing was re-read (the estimate as it
    stands). The usage export gives the real number."""
    low = 0.0
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        prev = None
        for c in calls:
            u, reuse = c.usage, 0
            if prev is not None and c.ts_first and (prev.ts_last or prev.ts_first) and \
                    (c.ts_first - (prev.ts_last or prev.ts_first)).total_seconds() <= REUSE_GAP_S:
                reuse = min(prev.usage.context, u.context)
            alt = Usage(fresh_input=u.context - reuse, cache_read=reuse, output=u.output, thinking=u.thinking) \
                if reuse else u
            low += sum(pricemod.cost(alt, P.rates(c)).values())
            prev = c
    high = sum(P.total(c) for c in s.all_calls)
    return {"low": low, "high": high} if low < high else None


CHAPTER_MIN_TURNS = 6    # a shorter chat is one task
CHAPTER_MAX = 6
CHAPTER_SIDE = 4         # each side of a split keeps at least this many turns
CHAPTER_SMALL = 0.12     # a stage under this share of the tokens is not split further
# how sure each sign of a new task is (fixes.task_starts); an hour away adds 0.5
CHAPTER_STRENGTH = (("min break", 3.0), ("after a git commit", 2.5), ("a new command", 2.0), ("a new skill", 1.5))


def _chapter_strength(why: str, waited_s: float) -> float:
    s = next((v for k, v in CHAPTER_STRENGTH if k in why), 1.0)
    return s + (0.5 if waited_s >= 3600 else 0.0)


def _chapter_name(ts) -> str:
    """Its /command, else its first real message (short replies like "Go" are skipped)."""
    first = next((t for t in ts if t.n > 0), None)
    if first is not None and first.kind == "command" and first.text.strip():
        return first.text.split()[0]
    for t in ts:
        if t.n > 0 and t.kind == "typed":
            words = plain(t.text).split()
            if len(words) >= 4:
                text = " ".join(words)
                return text if len(text) <= 56 else text[:55].rstrip() + "…"
    return ""


def _base(path: str) -> str:
    return re.split(r"[\\/]", path.strip().rstrip("\\/"))[-1] if path else ""


def chapters(s: Session, turn_rows: list[dict], P: Pricer) -> list[dict]:
    """The chat in 2-6 stages of useful size, split where a new task likely began (fixes.task_starts: a long break, a
    commit, a /command, a new skill). Again and again the biggest stage is split at a strong sign near its middle, with
    at least CHAPTER_SIDE turns on each side (taking the earliest commits instead let one stage hold 97% of a chat).
    Each is named from its /command or first real message, and its main file."""
    if len([t for t in s.turns if t.n > 0]) < CHAPTER_MIN_TURNS:
        return []
    rows = {r["n"]: r for r in turn_rows}
    ns = sorted(rows)
    weight = {n: rows[n]["sent"] + rows[n]["helper_sent"] for n in ns}
    total = sum(weight.values()) or 1
    signs = {t.n: (why, _chapter_strength(why, t.waited_s)) for t, why in fixmod.task_starts(s)}
    bounds: list[int] = []

    def spans():
        edges = [ns[0]] + sorted(bounds) + [ns[-1] + 1]
        return [(edges[i], edges[i + 1] - 1) for i in range(len(edges) - 1)]

    while len(bounds) < CHAPTER_MAX - 1:
        best = None
        for lo, hi in spans():
            inside = [n for n in ns if lo <= n <= hi]
            w = sum(weight[n] for n in inside)
            if w < total * CHAPTER_SMALL:
                continue
            for n, (why, strength) in signs.items():
                if n in bounds or not lo < n <= hi:
                    continue
                left = [m for m in inside if m < n]
                if len(left) < CHAPTER_SIDE or len(inside) - len(left) < CHAPTER_SIDE:
                    continue
                share = sum(weight[m] for m in left) / (w or 1)
                score = strength - 4 * abs(share - 0.5) + 2 * w / total    # a strong sign, near the middle, of a big stage
                if best is None or score > best[0]:
                    best = (score, n)
        if best is None:
            break
        bounds.append(best[1])
    if not bounds:
        return []
    helper_calls = s.helper_calls
    out = []
    for lo, hi in spans():
        ts = [t for t in s.turns if lo <= t.n <= hi]
        rs = [rows[t.n] for t in ts if t.n in rows]
        hc = [c for c in helper_calls if lo <= c.turn <= hi]
        files: dict[str, int] = {}
        for t in ts:
            for c in t.calls:
                for a in c.actions:
                    if a.kind in ("write", "edit") and a.target:
                        f = _base(a.target.splitlines()[0])
                        files[f] = files.get(f, 0) + 1
        main_file = max(files, key=files.get) if files else ""
        title = _chapter_name(ts) or (f"work on {main_file}" if main_file else f"turns {lo}–{hi}")
        helpers = []
        for h in s.helpers:
            mine = [c for c in h.calls if lo <= c.turn <= hi]
            if mine:
                helpers.append({"name": h.description or h.id, "calls": len(mine),
                                "sent_tokens": sum(c.usage.context for c in mine),
                                "usd": sum(P.total(c) for c in mine),
                                "first_turn": min(c.turn for c in mine), "started": lo <= h.turn <= hi,
                                "parent": h.parent})
        out.append({"title": title, "main_file": main_file, "start_turn": lo or min(1, hi), "end_turn": hi,
                    "helpers": helpers, "why": signs[lo][0] if lo in signs else "",
                    "calls": sum(len(r["calls"]) for r in rs) + len(hc),
                    "sent_tokens": sum(r["sent"] + r["helper_sent"] for r in rs),
                    "output_tokens": sum(r["written"] for r in rs) + sum(c.usage.output for c in hc),
                    "usd": sum(r["usd"] + r["helper_usd"] for r in rs)})
    return out


TOP_FILES = 5


def top_files(s: Session) -> list[dict]:
    """The files that weighed most: what each read brought in, and what each write or edit put out, times the later
    calls of that thread that carried it. A compacted chat drops some of it, so this is the most each one cost."""
    by: dict[str, dict] = {}
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        for i, c in enumerate(calls):
            later = len(calls) - i - 1
            for a in c.actions:
                if a.kind not in ("read", "write", "edit") or not a.target:
                    continue
                f = a.target.splitlines()[0]
                t = tok(a.result_chars if a.kind == "read" else a.written_chars)
                d = by.setdefault(f, {"path": f, "tokens": 0, "reads": 0, "writes": 0})
                d["tokens"] += t * later
                d["reads" if a.kind == "read" else "writes"] += 1
    rows = sorted((d for d in by.values() if d["tokens"] > 0), key=lambda d: -d["tokens"])[:TOP_FILES]
    names = [_base(d["path"]) for d in rows]
    for d, n in zip(rows, names):        # the same name in two folders: add the folder
        parts = re.split(r"[\\/]", d.pop("path").strip())
        d["name"] = "/".join(parts[-2:]) if names.count(n) > 1 and len(parts) > 1 else n
    return rows


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
