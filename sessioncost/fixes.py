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
                                 + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
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


WRITE_MIN = 800          # characters (~200 tokens): a smaller rewrite is not worth an edit instead


def repeated_writes(s: Session, P) -> Fix | None:
    """The same file written whole again in the same thread. Each rewrite after the first was paid as output, then
    carried by every later call of that thread. An edit would have written only the lines that changed, so this is
    the most it could save."""
    usd, tokens, items, turns = 0.0, 0, [], set()
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        seen: dict[str, int] = {}
        for i, c in enumerate(calls):
            for a in c.actions:
                if a.kind != "write" or not a.target or a.written_chars < WRITE_MIN:
                    continue
                seen[a.target] = seen.get(a.target, 0) + 1
                if seen[a.target] < 2:
                    continue
                t = tok(a.written_chars)
                usd += (t * P.rates(c)["output"] + sum(t * P.rates(k)["cache_read"] for k in calls[i + 1:])) / 1e6
                tokens += t
                turns.add(c.turn)
                per_turn[c.turn] = per_turn.get(c.turn, 0) + t
                hit_calls.append((c.agent, c.n))
                items.append(f"{a.label} again, time {seen[a.target]} (~{t:,} tokens), call {c.n}"
                             + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
    if not items:
        return None
    return Fix(
        id="repeated-writes", title="Edit an existing file instead of rewriting all of it",
        why=(f"{len(items)} write{'s' if len(items) > 1 else ''} rewrote a whole file this chat had already written. "
             "The new copy was paid for as output, and again on every later call that carried it. An edit writes "
             "only the lines that change, so this is the most it could save."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="Write a new file once. When changing an existing file, edit only the lines that changed instead of "
            "writing the whole file again.")


ERRORS_MIN = 3           # this many failed steps in a chat, or two in a row in one turn, is a pattern


def error_loop(s: Session, P) -> Fix | None:
    """Failed tool steps, and above all retries straight after a failure. Each error message rode on every later
    call of its thread; a retry right after a failure also re-sent the whole conversation to try again. Stopping to
    find the cause after the second failure would have saved the retries (the most it could save)."""
    usd, tokens, items, turns, fails, longest, same = 0.0, 0, [], set(), 0, 0, 0
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        run, turn = 0, None
        failed: set[tuple[str, str]] = set()      # what failed since the last change to a file: the same again is blind
        for i, c in enumerate(calls):
            if c.turn != turn:
                run, turn = 0, c.turn
            for a in c.actions:
                if a.kind in ("write", "edit"):
                    failed.clear()
                if not a.is_error:
                    run = 0
                    continue
                key = (a.tool, re.sub(r"\s+", " ", a.target or a.label).strip())
                again = key in failed
                failed.add(key)
                same += again
                fails += 1
                run += 1
                longest = max(longest, run)
                t = tok(a.result_chars)
                cost = 0.0
                if t and i + 1 < len(calls):
                    nxt = calls[i + 1]
                    cost += t * pricemod.new_input_rate(nxt.usage, P.rates(nxt))
                    cost += sum(t * P.rates(k)["cache_read"] for k in calls[i + 2:])
                retry = run > 1
                if retry and a is c.actions[0]:       # this call only happened to try again after a failure
                    cost += sent_usd(c, P.rates(c)) * 1e6
                    t += c.usage.context
                usd += cost / 1e6
                tokens += t
                turns.add(c.turn)
                per_turn[c.turn] = per_turn.get(c.turn, 0) + t
                if (c.agent, c.n) not in hit_calls:
                    hit_calls.append((c.agent, c.n))
                note = ", the same as before and unchanged" if again else ", again right after a failure" if retry else ""
                items.append(f"{a.label} failed{note}, call {c.n}"
                             + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
    if fails < ERRORS_MIN and longest < 2:
        return None
    return Fix(
        id="error-loop", title="Stop and find the cause after a step fails twice",
        why=(f"{fails} step{'s' if fails > 1 else ''} failed in this chat"
             + (f", up to {longest} in a row" if longest > 1 else "")
             + (f"; {same} of them ran exactly what had already failed, with no file changed in between" if same else "")
             + ". Each error message was carried by every later call, and each retry right after a failure re-sent "
             "the whole conversation to try again. Finding the cause first is usually cheaper than trying again."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="When a command or tool fails twice in a row, stop and find the cause before trying again. Do not retry "
            "the same thing with small changes.")


TEST_CMD = re.compile(r"(?:^|[\s;&|(/\\])(?:pytest|py\.test|jest|vitest|mocha|rspec|phpunit|tox|nox|"
                      r"python3?\s+-m\s+(?:pytest|unittest)|go\s+test|cargo\s+test|dotnet\s+test|deno\s+test|bun\s+test|"
                      r"(?:npm|pnpm|yarn)\s+(?:run\s+)?test|make\s+(?:check|test)|(?:mvn|gradlew?)\s+(?:\S+\s+)*test)"
                      r"(?=$|[\s;&|)])", re.I)
CODE_EXT = re.compile(r"\.(py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|cs|cpp|cc|c|h|hpp|rb|php|swift|scala|vue|svelte|sql)$", re.I)


def is_test(a) -> bool:
    return a.kind == "command" and bool(TEST_CMD.search(a.target or ""))


def test_reruns(s: Session, P) -> Fix | None:
    """The same test command run again with nothing changed in between: no file written or edited, no other
    command. Its second result tells nothing new, and it was carried by every later call."""
    usd, tokens, items, turns = 0.0, 0, [], set()
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        ran: set[str] = set()                     # test commands run since the last change
        for i, c in enumerate(calls):
            for a in c.actions:
                if a.kind in ("write", "edit") or (a.kind == "command" and not is_test(a)):
                    ran.clear()
                    continue
                if not is_test(a):
                    continue
                key = re.sub(r"\s+", " ", a.target).strip()
                if key not in ran:
                    ran.add(key)
                    continue
                t = tok(a.result_chars)
                if t and i + 1 < len(calls):
                    nxt = calls[i + 1]
                    usd += (t * pricemod.new_input_rate(nxt.usage, P.rates(nxt))
                            + sum(t * P.rates(k)["cache_read"] for k in calls[i + 2:])) / 1e6
                tokens += t
                turns.add(c.turn)
                per_turn[c.turn] = per_turn.get(c.turn, 0) + t
                hit_calls.append((c.agent, c.n))
                items.append(f"{a.label} again with nothing changed, call {c.n}"
                             + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
    if not items:
        return None
    return Fix(
        id="test-rerun", title="Don't run the tests again when nothing changed",
        why=(f"The tests were run {len(items)} more time{'s' if len(items) > 1 else ''} with no file changed since "
             "the last run, so the result could not be different. Each copy of the output was carried by every "
             "later call."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="Do not re-run a command when no file has changed since the last run. Read the first result.")


def untested_edits(s: Session) -> dict | None:
    """Code the main agent changed after the last test run of the chat, with no test run after it. Only in a chat
    that ran the tests at least once (so the project has tests). Not a cost: shown above the fixes."""
    last, changed = None, []
    for c in s.calls:
        for a in c.actions:
            if is_test(a):
                last, changed = c, []
            elif a.kind in ("write", "edit") and last is not None:
                changed += [f for f in (a.target or "").splitlines() if CODE_EXT.search(f.strip())]
    if last is None or not changed:
        return None
    names = list(dict.fromkeys(re.split(r"[\\/]", f.strip())[-1] for f in changed))
    return {"id": "untested-edits", "title": "Code was changed after the last test run",
            "times": [], "refused": 0,
            "why": (f"{', '.join(names[:5])}{' and more' if len(names) > 5 else ''} changed after the tests last ran "
                    f"(turn {last.turn}), and the tests were not run again"),
            "note": "Not a cost, but untested changes are how bugs get through.",
            "how": "After changing code, run the tests before you say the work is done."}


POLL_TOOLS = {"schedule"}                               # a timer to look again (Antigravity)
POLL_ACTIONS = {("manage_subagents", "list"), ("manage_subagents", "status")}


def _polls(a) -> bool:
    return a.tool in POLL_TOOLS or any(a.tool == t and f"({v})" in a.label for t, v in POLL_ACTIONS)


def helper_polling(s: Session, P) -> Fix | None:
    """Main-agent calls that only waited for a helper: a timer, or a look at the list of running helpers. A helper
    sends its answer back by itself, so each of these calls re-sent the whole conversation for nothing."""
    if not s.helpers:
        return None
    usd, tokens, items, turns = 0.0, 0, [], set()
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    helper_turns = {c.turn for h in s.helpers for c in h.calls}
    for c in s.calls:
        if not c.actions or c.turn not in helper_turns or not all(_polls(a) for a in c.actions):
            continue
        usd += sent_usd(c, P.rates(c))
        tokens += c.usage.context
        turns.add(c.turn)
        per_turn[c.turn] = per_turn.get(c.turn, 0) + c.usage.context
        hit_calls.append((c.agent, c.n))
        items.append(f"call {c.n} (turn {c.turn}): {'; '.join(a.label for a in c.actions)}, re-sending "
                     f"{c.usage.context:,} tokens")
    if not items:
        return None
    return Fix(
        id="helper-polling", title="Let helpers report back instead of checking on them",
        why=(f"{len(items)} call{'s' if len(items) > 1 else ''} of the main agent only waited for a helper: a timer, "
             "or a look at which helpers were still running. A helper sends its answer back by itself, and each of "
             "these calls re-sent the whole conversation to wait."),
        saving_usd=usd, saving_tokens=tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=per_turn, calls=hit_calls, paste=True,
        how="When a helper is working, wait for its message. Do not set timers or check on it in between.")


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
    usd, hits, turns, tokens, choices = 0.0, [], set(), 0, 0
    per_turn: dict[int, int] = {}
    hit_calls: list[tuple[str, int]] = []
    before = None                       # the last main call before this turn: what the agent said last
    for t in s.turns:
        prev, before = before, (t.calls[-1] if t.calls else before)
        said = (prev.last_text or prev.first_text) if prev is not None else ""
        if t.kind in ("typed", "queued") and t.calls and is_confirm(t.text, lettered(said)) and \
                (prev is None or asked(said)):
            c = t.calls[0]
            usd += sent_usd(c, P.rates(c))
            tokens += c.usage.context
            per_turn[t.n] = per_turn.get(t.n, 0) + c.usage.context
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
                usd += sent_usd(nxt, P.rates(nxt))
                tokens += nxt.usage.context
                per_turn[t.n] = per_turn.get(t.n, 0) + nxt.usage.context
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


GIT_COMMIT = re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*commit\b")
SKILL_FILE = re.compile(r"[\\/]skills[\\/]|[\\/]SKILL\.md$", re.I)


def task_starts(s: Session) -> list[tuple]:
    """(turn, why) for each turn where a new task likely began: after a long break, after the previous turn made a
    git commit, at a new /command, or when the agent first read a skill's files. The first turn is not one."""
    out, prev, skills = [], None, set()
    for t in s.turns:
        new_skill = {a.target for c in t.calls for a in c.actions
                     if a.kind == "read" and a.target and SKILL_FILE.search(a.target)} - skills
        if t.n > 1 and prev is not None:
            if t.waited_s >= LONG_BREAK_S:
                out.append((t, f"a {round(t.waited_s / 60)} min break"))
            elif any(a.kind == "command" and GIT_COMMIT.search(a.target or "") for c in prev.calls for a in c.actions):
                out.append((t, f"after a git commit in turn {prev.n}"))
            elif t.kind == "command":
                out.append((t, f"a new command in turn {t.n}"))
            elif new_skill:
                out.append((t, f"a new skill read in turn {t.n}"))
        skills |= new_skill
        if t.n > 0:
            prev = t
    return out


def long_chat(s: Session, P) -> Fix | None:
    """A long conversation carried on after a long break: every later call re-sent all of it. A new chat (or a
    compacted one) at the break would have carried a short summary instead. The saving is measured call by call:
    what each later call re-sent of the old conversation, minus the summary that would replace it."""
    calls = s.calls
    if len(calls) < 2:
        return None
    setup = calls[0].usage.context                      # the first call carries little more than the setup
    splits, why = [], {}
    for t, reason in task_starts(s):
        if t.n > 1 and t.calls and t.calls[0].usage.context - setup >= LONG_CHAT:
            splits.append(t)
            why[t.n] = reason
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
        items.append(f"before turn {t.n}: {why[t.n]}, with ~{base:,} tokens of conversation; "
                     f"a new chat would have cut ~{seg:,} tokens from the {end - start} calls after it")
    if not tokens:
        return None
    turns = [t.n for t in splits]
    breaks = all(why[n].endswith("break") for n in turns)
    where = (f"{len(turns)} long break{'s' if len(turns) > 1 else ''}" if breaks else
             f"{len(turns)} point{'s' if len(turns) > 1 else ''} where a task ended or a new one began")
    return Fix(
        id="long-chat", title="Start a new chat after a long break" if breaks else "Start a new chat for each new task",
        why=(f"This chat went on after {where} with "
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
                            repeated_reads(session, pricer), repeated_writes(session, pricer),
                            error_loop(session, pricer), helper_polling(session, pricer),
                            test_reruns(session, pricer),
                            confirm_turns(session, pricer),
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
