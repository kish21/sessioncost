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

MAX_FIXES = 6
LONG_CHAT = 100_000      # a conversation this long, carried into a turn after a break, is worth a new chat
LONG_BREAK_S = 30 * 60   # a break this long: the cache has usually expired, and the task has often changed
SUMMARY = 15_000         # what a new chat would carry instead: a short summary of where you are
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


def server_of(tool: str) -> str:
    """mcp__claude-in-chrome__computer -> claude-in-chrome. Built-in tools (Read, Bash, Agent...) have none."""
    return tool.split("__")[1] if tool.startswith("mcp__") and tool.count("__") >= 2 else ""


def server_name(server: str) -> str:
    """What the user sees in their settings: claude_ai_Claude_Docs -> claude.ai Claude Docs (a claude.ai connector)."""
    if server.lower().startswith("claude_ai_") or server.lower().startswith("claude.ai"):
        return "claude.ai " + re.sub(r"^claude[._]ai[_ ]", "", server, flags=re.I).replace("_", " ")
    if server.startswith("plugin_"):
        return server[len("plugin_"):].replace("_", " ") + " (a plugin)"
    return server


def turn_off_steps(agent: str, servers: list[str]) -> str:
    """Where each agent turns off a connected server, as numbered steps (one per line). The agent cannot do this
    from a chat, so the user does it."""
    names = [server_name(x) for x in servers]
    connectors = [n for n in names if n.startswith("claude.ai ")]
    plugins = [n for n in names if n.endswith("(a plugin)")]
    others = [n for n in names if n not in connectors and n not in plugins]
    if agent == "claude-code":
        steps = []
        if others:
            steps.append("In Claude Code, type /mcp, choose each of these and turn it off: " + ", ".join(others) + ".")
        if connectors:
            steps.append("Connectors from your claude.ai account (" + ", ".join(connectors) + "): turn them off at "
                         "claude.ai, Settings, Connectors, or in /mcp.")
        if plugins:
            steps.append("Plugins (" + ", ".join(plugins) + "): type /plugin, open Installed, and disable them.")
        steps.append("Start a new chat: the change applies from then on.")
    elif agent == "codex":
        steps = ["Open ~/.codex/config.toml and remove the [mcp_servers.<name>] sections of: " + ", ".join(names) + ".",
                 "Start a new Codex chat."]
    elif agent == "cursor":
        steps = ["Open Cursor Settings, Tools & MCP, and turn off: " + ", ".join(names) + ".", "Start a new chat."]
    else:
        steps = ["In the agent's MCP settings, turn off: " + ", ".join(names) + ".", "Start a new chat."]
    return "\n".join(f"{i}. {x}" for i, x in enumerate(steps, 1))


def unused_tools(s: Session, P) -> Fix | None:
    """Connected tools (MCP servers, claude.ai connectors, plugins) sent with every call and never used, and the ones
    announced later in the conversation, which rode along on every call after they arrived. Only what the user can
    turn off is counted: the agent's own built-in tools (Read, Bash, Agent...) come with it and cannot be."""
    if not s.calls:
        return None
    used = {a.tool for a in s.actions()}
    used_servers = {_loose(server_of(t)) for t in used if server_of(t)}
    idle_servers: list[str] = []

    def idle_server(sv: str) -> bool:
        return bool(sv) and _loose(sv) not in used_servers and \
            not any(_loose(sv) in u or u in _loose(sv) for u in used_servers if u)

    unused = [t for t in s.setup.tools if t.what not in used and idle_server(server_of(t.what))]
    for t in unused:
        if server_of(t.what) not in idle_servers:
            idle_servers.append(server_of(t.what))
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
    late_items = []
    for ad in s.added:
        if ad.kind == "tools":
            idle = [n for n in ad.tools if n not in used and idle_server(server_of(n))]
            share = tok(ad.chars * len(idle) / len(ad.tools)) if ad.tools else 0
            svs = sorted({server_of(n) for n in idle})
            what = f"{len(idle)} tool{'s' if len(idle) != 1 else ''} announced later ({', '.join(server_name(x) for x in svs)})"
        elif ad.kind == "instructions":
            svs = [x for x in ad.servers if idle_server(x)]
            idle = svs
            share = tok(ad.chars * len(idle) / len(ad.servers)) if ad.servers else 0
            what = "instructions for " + ", ".join(server_name(x) for x in svs)
        else:
            continue
        carry = s.calls[ad.after_call:]
        if not share or not idle or not carry:
            continue
        for sv in svs:
            if sv not in idle_servers and _loose(sv) not in {_loose(x) for x in idle_servers}:
                idle_servers.append(sv)
        r0 = P.rates(carry[0])
        usd += share * pricemod.new_input_rate(carry[0].usage, r0) / 1e6
        usd += sum(share * P.rates(c)["cache_read"] for c in carry[1:]) / 1e6
        saving += share * len(carry)
        late_items.append(f"{what}, ~{share:,} tokens from call {ad.after_call + 1} on ({len(carry)} calls)")
    if not saving or not idle_servers:
        return None
    by_server: dict[str, list] = {}
    for t in unused:
        by_server.setdefault(server_name(server_of(t.what)), []).append(t)
    items = [f"{sv}: {len(ts)} tool{'s' if len(ts) != 1 else ''}, ~{sum(tok(t.chars) for t in ts):,} tokens on every call"
             for sv, ts in sorted(by_server.items(), key=lambda x: -sum(t.chars for t in x[1]))] + late_items
    names = ", ".join(server_name(x) for x in idle_servers)
    return Fix(how=turn_off_steps(s.agent, idle_servers), paste=False,   # a setting the user changes, not a rule
        id="unused-tools", title="Turn off connected tools this project does not use",
        why=(f"Never used in this session: {names}. Their tool lists and instructions went out with every call "
             f"after they were connected. If this project does not need them, turning them off saves this much on a "
             "session like this one. (The agent's own built-in tools come with it and are not counted.)"),
        saving_usd=usd, saving_tokens=saving, where="every call", items=items[:8])


# Claude Code's own tools that a setting can switch off (code.claude.com/docs: artifacts, settings-reference,
# 2026-10-16). Removing one takes its definition out of every call; a deny rule on the bare name does the same.
BUILTIN_SWITCHES = {
    "Artifact": ('"enableArtifact": false', "CLAUDE_CODE_DISABLE_ARTIFACT=1"),
    "Workflow": ('"disableWorkflows": true', "CLAUDE_CODE_DISABLE_WORKFLOWS=1"),
}
BUILTIN_MIN = 1000       # tokens of definition worth a setting


def unused_builtins(s: Session, P) -> Fix | None:
    """Claude Code's big built-in tools that can be switched off, sent with every call and never used."""
    if s.agent != "claude-code" or not s.calls:
        return None
    used = {a.tool for a in s.actions()}
    idle = [t for t in s.setup.tools if t.what in BUILTIN_SWITCHES and t.what not in used and tok(t.chars) >= BUILTIN_MIN]
    if not idle:
        return None
    tokens = sum(tok(t.chars) for t in idle)
    usd = 0.0
    for c in s.calls:
        r, u = P.rates(c), c.usage
        from_cache = min(tokens, u.cache_read)
        as_new = min(tokens - from_cache, u.new_input)
        usd += (from_cache * r["cache_read"] + as_new * pricemod.new_input_rate(u, r)) / 1e6
    names = ", ".join(t.what for t in idle)
    settings = ", ".join(BUILTIN_SWITCHES[t.what][0] for t in idle)
    steps = [f"Open ~/.claude/settings.json (for every project) or .claude/settings.json in this project, and add {settings}.",
             "Start a new chat: the change applies from then on.",
             "Type /context to see Step 0 get smaller. Turn it back on when you want the tool again."]
    return Fix(how="\n".join(f"{i}. {x}" for i, x in enumerate(steps, 1)), paste=False,
        id="unused-builtin", title="Switch off Claude Code tools you never use",
        why=(f"Claude Code's own {names} tool{'s' if len(idle) > 1 else ''} went out with every call (~{tokens:,} "
             "tokens each time) and this session never used it. Unlike most built-in tools, a setting removes "
             f"{'them' if len(idle) > 1 else 'it'} from the prompt. Only do it if you do not use "
             f"{'them' if len(idle) > 1 else 'it'} in other chats either."),
        saving_usd=usd, saving_tokens=tokens * len(s.calls), where="every call",
        items=[f"{t.what}: ~{tok(t.chars):,} tokens on every call ({BUILTIN_SWITCHES[t.what][0]}, or "
               f"{BUILTIN_SWITCHES[t.what][1]})" for t in idle])


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
                                 + ("" if c.agent == "main" else f" of helper {c.agent.split(':')[-1]}"))
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


def new_chat_steps(agent: str) -> str:
    """How to start over with a short summary, in each agent (numbered steps, one per line)."""
    first = {
        "claude-code": "Type /compact: Claude Code shrinks the chat to a short summary and carries on. Or type /clear "
                       "to start a fresh chat.",
        "codex": "Type /compact to shrink the chat to a summary, or /new to start a fresh chat.",
        "cursor": "Start a new chat (the + at the top of the chat panel).",
    }.get(agent, "Start a new chat.")
    return "\n".join(f"{i}. {x}" for i, x in enumerate([
        "Do it when a task is done, or when you come back after a break of half an hour or more.",
        first,
        "If you start fresh, tell the agent in two or three lines where you are and what is next.",
    ], 1))


def long_chat(s: Session, P) -> Fix | None:
    """A long conversation carried on after a long break: every later call re-sent all of it. A new chat (or a
    compacted one) at the break would have carried a short summary instead. The saving is measured call by call:
    what each later call re-sent of the old conversation, minus the summary that would replace it."""
    calls = s.calls
    if len(calls) < 2:
        return None
    setup = calls[0].usage.context                      # the first call carries little more than the setup
    splits = [t for t in s.turns if t.n > 1 and t.calls and t.waited_s >= LONG_BREAK_S
              and t.calls[0].usage.context - setup >= LONG_CHAT]
    if not splits:
        return None
    usd, tokens, per_turn, items, hit = 0.0, 0, {}, [], []
    order = {id(c): i for i, c in enumerate(calls)}
    for k, t in enumerate(splits):
        start = order[id(t.calls[0])]
        end = order[id(splits[k + 1].calls[0])] if k + 1 < len(splits) else len(calls)
        base = t.calls[0].usage.context - setup         # the old conversation at the break
        cut = base - SUMMARY
        if cut <= 0:
            continue
        seg = 0
        for i in range(start, end):
            c = calls[i]
            r = P.rates(c)
            take = min(cut, max(0, c.usage.context - setup))
            # the first call after a long break wrote it all again at full price; later calls re-read it cheaply
            rate = pricemod.new_input_rate(c.usage, r) if i == start and c.usage.cache_read < base / 2 else r["cache_read"]
            usd += take * rate / 1e6
            tokens += take
            seg += take
            per_turn[c.turn] = per_turn.get(c.turn, 0) + take
        hit.append((t.calls[0].agent, t.calls[0].n))
        items.append(f"before turn {t.n}: a {round(t.waited_s / 60)} min break with ~{base:,} tokens of conversation; "
                     f"a new chat would have cut ~{seg:,} tokens from the {end - start} calls after it")
    if not tokens:
        return None
    turns = [t.n for t in splits]
    return Fix(
        id="long-chat", title="Start a new chat after a long break",
        why=(f"This chat went on after {len(turns)} long break{'s' if len(turns) > 1 else ''} with "
             f"{max(t.calls[0].usage.context - setup for t in splits) // 1000}k tokens of conversation, and every "
             "later call re-sent all of it, including old work that had nothing to do with the next task. A new "
             "chat, or a compacted one, would have carried a short summary instead. (A fresh chat may re-read a "
             "file or two to catch up, so this is the most it could save.)"),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=turns, items=items,
        per_turn=per_turn, calls=hit, how=new_chat_steps(s.agent), paste=False)


class BasicFixes:
    name = "built-in"

    def find(self, session: Session, pricer) -> list[Fix]:
        return [f for f in (unused_tools(session, pricer), unused_builtins(session, pricer),
                            repeated_reads(session, pricer), confirm_turns(session, pricer),
                            long_chat(session, pricer)) if f]


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
