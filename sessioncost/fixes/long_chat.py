"""Check long-chat: a long conversation carried on after a break or into a new task."""
from __future__ import annotations

import re

from .. import prices as pricemod
from ..model import Fix, Session
from .cache_break import cache_break
from .common import (_where)

LONG_CHAT = 100_000      # a conversation this long, carried into a turn after a break, is worth a new chat
LONG_BREAK_S = 30 * 60   # a break this long: the cache has usually expired, and the task has often changed
SUMMARY = 15_000         # what a new chat would carry instead: a short summary of where you are

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


def _break_limit(s: Session, t) -> int:
    prev = [c.usage for x in s.turns if 0 < x.n < t.n for c in x.calls]
    has_1h = any(u.cache_write_1h > 0 for u in prev) and not any(u.cache_write_5m > 0 for u in prev)
    hit_cache = bool(t.calls and t.calls[0].usage.cache_read >= t.calls[0].usage.context * 0.4)
    return 60 * 60 if (has_1h and hit_cache) else LONG_BREAK_S


def task_starts(s: Session) -> list[tuple]:
    """(turn, why) for each turn where a new task likely began: after a long break, after the previous turn made a
    git commit, at a new /command, or when the agent first read a skill's files. The first turn is not one."""
    out, prev, skills = [], None, set()
    for t in s.turns:
        new_skill = {a.target for c in t.calls for a in c.actions
                     if a.kind == "read" and a.target and SKILL_FILE.search(a.target)} - skills
        if t.n > 1 and prev is not None:
            if t.waited_s >= _break_limit(s, t):
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
    has_cb = cache_break(s, P) is not None
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
            rate = (pricemod.new_input_rate(c.usage, r)
                    if i == start and not has_cb and c.usage.cache_read < base / 2 else r["cache_read"])
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
