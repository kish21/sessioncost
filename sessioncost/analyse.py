"""From a Session to the full report model: exact cost per call, the drill-down parts (which always add up to their
parent), the turn rows, where the cost went, the fixed setup, timing and the fixes.

Token sizes that the log does not give directly (how much of a call's input was the fixed setup, how much of its
output was a file vs a message) are estimated as characters / 4 and then scaled so the parts add up exactly to the
token counts the log does give. Those parts are marked `estimated`.
"""
from __future__ import annotations

import os

from datetime import datetime

from . import fixes as fixmod, rules
from .fixes.overlap import combine, shared
from . import __version__, prices as pricemod
from .model import Call, Session
from .readers._util import clean_cmd
from .risks import ALLOW_FILE, NEVER, SEVERITY, WATCH, read_allow
from . import guard as guardmod
from .analyse_calls import (CATEGORIES, CLASS_LABELS, SAID, Pricer, _hide_keys, call_label, category,  # noqa: F401
                            checked_day, guard_summary, hhmm, iso, money_label, secs, sent_parts, split_exact, tok,
                            written_parts)
from .analyse_lists import _base, _folder, agents, cursor_range, plain, top_files


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
            "rules_file": {"claude-code": "CLAUDE.md", "codex": "AGENTS.md", "cursor": ".cursor/rules/sessioncost.mdc",
                           "antigravity": "GEMINI.md", "copilot": ".github/copilot-instructions.md",
                           "copilot-vscode": ".github/copilot-instructions.md"}.get(
                               s.agent, "the agent's instruction file"),
            # which recommended rules are already in the instruction files (read here, never sent)
            "rules_check": rules.check(s.agent, s.cwd),
        },
        "money": {"label": long_label, "short": short_label, "as_of": as_of, "day": checked_day(as_of), "exact": exact,
                  "own": own, "stale": pricemod.stale_note(prices),
                  "range": cursor_range(s, P) if not s.tokens_exact else None,
                  # a usage export was used but did not cover every turn (a later turn, or an older export)
                  "export_partial": s.agent == "cursor" and not s.tokens_exact and
                                    any(n.startswith("Tokens from Cursor's usage export") for n in s.notes),
                  "subscription": subscription, "unchecked_models": sorted(P.unchecked), "why": why,
                  "tokens_exact": s.tokens_exact, "write_split_known": split_known, "counts_note": s.counts_note,
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
    # all fixes together, each token counted once (fixes/overlap.py)
    ctx = {(c.agent, c.n): (c.usage.context, c.turn) for c in s.all_calls}
    both = combine(s.fixes, ctx)
    pairs = shared(s.fixes, ctx, both["overlaps"])
    out["saving"] = {k: v for k, v in both.items() if k != "overlaps"}
    out["saving"]["per_turn"] = {str(k): v for k, v in both["per_turn"].items()}
    call_ix = {(d["agent"], d["n"]): i for i, d in enumerate(calls)}
    out["fixes"] = [{"id": f.id, "title": f.title, "why": f.why, "saving_usd": f.saving_usd,
                     "saving_tokens": f.saving_tokens, "where": f.where, "turns": f.turns, "items": f.items,
                     "source": f.source, "per_turn": {str(k): v for k, v in f.per_turn.items()},
                     "calls": [call_ix[k] for k in f.calls if k in call_ix], "how": f.how, "paste": f.paste,
                     "targets": list(f.targets), "per_target": dict(f.per_target),
                     "overlaps": both["overlaps"].get(f.id, []), "shared": pairs.get(f.id, {})}
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
