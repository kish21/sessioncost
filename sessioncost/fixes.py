"""Fixes: each one names what to change and a saving measured from this session's own calls.

The open tool ships three simple detectors (BasicFixes). Every saving is the cost of the tokens or calls involved
in THIS session, priced like the rest of the report - nothing is projected or guessed.

PLUG-IN POINT: a separate fix engine can add detectors without changing this repo. Set the environment variable
SESSIONCOST_ENGINE to an importable module name; that module must expose `Engine()`, an object with a `name` and
`find(session, pricer) -> list[Fix]` (the FixEngine interface below). It runs after the built-in detectors and its
fixes are ranked together with theirs.
"""
from __future__ import annotations

import importlib
import os
import re
from typing import Protocol

from . import prices as pricemod
from .model import Call, Fix, Session

MAX_FIXES = 3
CONFIRM = {"y", "yes", "yes please", "yep", "yeah", "ok", "okay", "k", "sure", "go", "go ahead", "go on", "do it",
           "proceed", "continue", "save", "save it", "confirm", "confirmed", "approve", "approved", "lgtm",
           "sounds good", "looks good", "ship it", "fine"}


def tok(chars: int) -> int:
    return max(0, int(chars) // 4)


def is_confirm(text: str) -> bool:
    t = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    return bool(t) and len(t) <= 25 and t in CONFIRM


ASK_RE = re.compile(r"\b(shall i|should i|do you want|would you like|want me to|may i|can i|ok to|okay to|is that ok|"
                    r"sound good|good to go|ready (?:for me )?to|before i (?:go|continue|proceed))\b", re.I)


def asked(text: str) -> bool:
    """Did the agent's message end by asking the user something? A finished report ("Done. All tests pass.") did
    not, so an "ok" after it is the user saying thanks, not a stop the agent made."""
    t = re.sub(r"[\s*_`>)\]]+$", "", text or "")
    tail = re.split(r"(?<=[.!?])\s+", t)[-2:]
    return t.endswith("?") or any("?" in x for x in tail) or bool(ASK_RE.search(" ".join(tail)))


def sent_usd(c: Call, r: dict) -> float:
    cc = pricemod.cost(c.usage, r)
    return cc["input"] + cc["cache_write_5m"] + cc["cache_write_1h"] + cc["cache_read"]


class FixEngine(Protocol):
    name: str

    def find(self, session: Session, pricer) -> list[Fix]: ...


def _loose(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def unused_tools(s: Session, P) -> Fix | None:
    """Tool definitions sent with every call that the session never used, and tools (with their MCP server
    instructions) announced later in the conversation and never used, which rode along on every later call."""
    if not s.calls:
        return None
    used = {a.tool for a in s.actions()}
    used_servers = {_loose(t.split("__")[1]) for t in used if t.startswith("mcp__") and t.count("__") >= 2}
    unused = [t for t in s.setup.tools if t.what not in used]
    tokens = sum(tok(t.chars) for t in unused)
    usd = 0.0
    for c in s.calls if tokens else []:
        # The setup is the front of the prompt, so it comes from the cache first; only what the call really
        # wrote as new input can be priced as new input. The saving can never exceed what the call paid to send.
        r, u = P.rates(c), c.usage
        from_cache = min(tokens, u.cache_read)
        as_new = min(tokens - from_cache, u.new_input)
        usd += (from_cache * r["cache_read"] + as_new * pricemod.new_input_rate(u, r)) / 1e6
    saving = tokens * len(s.calls)
    # announced later: written once when it arrived, then re-read from the cache by every later call
    late, late_items = [], []
    for ad in s.added:
        if ad.kind == "tools":
            idle = [n for n in ad.tools if n not in used]
            share = tok(ad.chars * len(idle) / len(ad.tools)) if ad.tools else 0
            what = f"{len(idle)} tool{'s' if len(idle) != 1 else ''} announced later ({', '.join(ad.servers) or 'loaded on demand'})"
        elif ad.kind == "instructions":
            idle = [x for x in ad.servers if _loose(x) not in used_servers and
                    not any(_loose(x) in u or u in _loose(x) for u in used_servers)]
            share = tok(ad.chars * len(idle) / len(ad.servers)) if ad.servers else 0
            what = "instructions for " + ", ".join(idle)
        else:
            continue
        carry = s.calls[ad.after_call:]
        if not share or not idle or not carry:
            continue
        r0 = P.rates(carry[0])
        usd += share * pricemod.new_input_rate(carry[0].usage, r0) / 1e6
        usd += sum(share * P.rates(c)["cache_read"] for c in carry[1:]) / 1e6
        saving += share * len(carry)
        late.append(idle)
        late_items.append(f"{what}, ~{share:,} tokens from call {ad.after_call + 1} on ({len(carry)} calls)")
    if not saving:
        return None
    top = sorted(unused, key=lambda t: -t.chars)
    servers = sorted({sv for ad in s.added for sv in ad.servers if ad.kind == "instructions"
                      and _loose(sv) not in used_servers})
    how = {"claude-code": "Disconnect the MCP servers and plugins this project does not use (in Claude Code: /mcp "
                          "and /plugin).",
           "codex": "Remove the MCP servers this project does not use from ~/.codex/config.toml.",
           "cursor": "Turn off the MCP servers this project does not use (Cursor Settings, Tools & MCP).",
           }.get(s.agent, "Turn off the tools, MCP servers and plugins this project does not use.")
    if servers:
        how += " Never used here: " + ", ".join(servers) + "."
    why = []
    if tokens:
        why.append(f"{len(unused)} of {len(s.setup.tools)} tool definitions (~{tokens:,} tokens) went out with every "
                   f"one of the {len(s.calls)} calls and were never used.")
    if late_items:
        why.append(f"{'More' if tokens else 'Tools'} and service instructions announced later in the chat were never "
                   "used either, and rode along on every call after they arrived.")
    why.append("If this project does not need them (MCP servers and plugins are the usual ones), turning them off "
               "saves this much on a session like this one.")
    return Fix(how=how,                       # every call: shown on Step 0, not flagged call by call
        id="unused-tools", title="Turn off tools this session never used",
        why=" ".join(why), saving_usd=usd, saving_tokens=saving, where="every call",
        items=late_items + [f"{t.what} ~{tok(t.chars):,} tokens" for t in top[:8]] + (
            [f"and {len(top) - 8} more"] if len(top) > 8 else []))


def repeated_reads(s: Session, P) -> Fix | None:
    """The same file read again with exactly the same content, in the same thread."""
    threads = [s.calls] + [h.calls for h in s.helpers]
    usd, tokens, items, turns = 0.0, 0, [], set()
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
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
                    items.append(f"{a.label} again (~{t:,} tokens, unchanged), call {c.n}"
                                 + ("" if c.agent == "main" else f" of helper {c.agent.split(':')[-1]}"))
                else:
                    seen[key] = a.result_hash + str(a.label)
    if not items:
        return None
    return Fix(
        id="repeated-reads", title="Don't re-read a file that has not changed",
        why=(f"{len(items)} read{'s' if len(items) > 1 else ''} returned exactly the same text as an earlier read in "
             "the same chat. The copy was paid for when it came in and again on every later call that carried it."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="Do not read a file again if it has not changed since you last read it: use what you already read.")


def confirm_turns(s: Session, P) -> Fix | None:
    """A stop where the user only said yes / ok / save, and the next call just acted on it."""
    usd, hits, turns, tokens = 0.0, [], set(), 0
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    before = None                       # the last main call before this turn: what the agent said last
    for t in s.turns:
        prev, before = before, (t.calls[-1] if t.calls else before)
        if t.kind in ("typed", "queued") and t.calls and is_confirm(t.text) and \
                (prev is None or asked(prev.last_text or prev.first_text)):
            c = t.calls[0]
            usd += sent_usd(c, P.rates(c))
            tokens += c.usage.context
            per_turn[t.n] = per_turn.get(t.n, 0) + c.usage.context
            hit_calls.append((c.agent, c.n))
            hits.append(f"turn {t.n}: you typed “{t.text}”; the next call re-sent "
                        f"{c.usage.context:,} tokens to receive it")
            turns.add(t.n)
        for e in t.events:
            if e.kind != "answer" or not e.ts:
                continue
            answers = [x for x in e.text.split("; ") if x.strip()]
            if answers and all(is_confirm(x) for x in answers):
                nxt = next((c for c in t.calls if c.ts_first and c.ts_first >= e.ts), None)
                if nxt is None:
                    continue
                usd += sent_usd(nxt, P.rates(nxt))
                tokens += nxt.usage.context
                per_turn[t.n] = per_turn.get(t.n, 0) + nxt.usage.context
                hit_calls.append((nxt.agent, nxt.n))
                hits.append(f"turn {t.n}: you picked “{e.text}” on a question card; the next call re-sent "
                            f"{nxt.usage.context:,} tokens to receive it")
                turns.add(t.n)
    if not hits:
        return None
    return Fix(
        id="confirm-turn", title="Skip the stop that only asks you to confirm",
        why=("The agent stopped and waited for a yes. Receiving it meant re-sending the whole conversation once "
             "more. Let it go ahead and ask “change anything?” afterwards instead."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=hits,
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="When the next step is clear, go ahead without asking me to confirm. Tell me what you did afterwards.")


def _where(turns) -> str:
    ts = sorted(turns)
    return ("turn " if len(ts) == 1 else "turns ") + ", ".join(str(t) for t in ts) if ts else ""


class BasicFixes:
    name = "built-in"

    def find(self, session: Session, pricer) -> list[Fix]:
        return [f for f in (unused_tools(session, pricer), repeated_reads(session, pricer),
                            confirm_turns(session, pricer)) if f]


def load_engines() -> list:
    engines: list = [BasicFixes()]
    # ---- PLUG-IN POINT (see the module docstring). Only loaded when the user names it explicitly.
    mod = os.environ.get("SESSIONCOST_ENGINE", "").strip()
    if mod:
        try:
            engines.append(importlib.import_module(mod).Engine())
        except Exception as e:  # an engine problem must never break the report
            print(f"sessioncost: fix engine {mod!r} not loaded: {e}")
    return engines


def find(session: Session, pricer, engines: list | None = None) -> list[Fix]:
    out: list[Fix] = []
    for eng in engines if engines is not None else load_engines():
        for f in eng.find(session, pricer) or []:
            if f.source == "built-in" and getattr(eng, "name", "built-in") != "built-in":
                f.source = eng.name
            out.append(f)
    out = [f for f in out if f.saving_usd > 0]
    out.sort(key=lambda f: (-f.saving_tokens, -f.saving_usd))   # tokens cut first; money follows
    return out[:MAX_FIXES]
