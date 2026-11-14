"""Check 3 (1.1): single huge tool results (10k+ tokens) or lockfile / build-output reads."""
from __future__ import annotations

from ..model import Call, Fix, Session
from ..guard_checks import _BUILD_SUFFIXES, _LOCK_NAMES      # one list for the guard and this check
from .common import Ledger, short_target, src, tok, turns_where

BIG_RESULT_TOKENS = 10_000
LOCKFILE_TOKENS = 3_000
CUT = 0.8                # an estimate: reading only the lines needed, or a short output, keeps about a fifth


def _is_lock_or_build(target: str) -> bool:
    p = target.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    if base in _LOCK_NAMES or base.endswith(_BUILD_SUFFIXES):
        return True
    return "/dist/" in p or "/build/" in p or "/node_modules/" in p


def big_result_action_ids(calls: list[Call]) -> set[int]:
    edited = {a.target for c in calls for a in c.actions if a.kind in ("edit", "write") and a.target}
    searched_so_far = False
    out: set[int] = set()
    for c in calls:
        for a in c.actions:
            if a.kind == "search":
                searched_so_far = True
                continue
            t = tok(a.result_chars)
            if a.kind == "read" and _is_lock_or_build(a.target):
                if t >= LOCKFILE_TOKENS:
                    out.add(id(a))
            elif t >= BIG_RESULT_TOKENS:
                if a.kind != "read" or not ("(lines " in a.label or a.target in edited or searched_so_far):
                    out.add(id(a))
    return out


def big_results(s: Session, pr) -> Fix | None:
    items: list[str] = []
    led = Ledger()
    hit_calls: list[tuple[str, int]] = []
    kinds_hit: set[str] = set()
    hit_turns: set[int] = set()

    for calls in [s.calls] + [h.calls for h in s.helpers]:
        edited = {a.target for c in calls for a in c.actions if a.kind in ("edit", "write") and a.target}
        searched_so_far = False
        for i, c in enumerate(calls):
            for a in c.actions:
                if a.kind == "search":
                    searched_so_far = True
                    continue
                t = tok(a.result_chars)
                lock_or_build = a.kind == "read" and _is_lock_or_build(a.target)
                if lock_or_build:
                    if t < LOCKFILE_TOKENS:
                        continue
                elif t < BIG_RESULT_TOKENS:
                    continue
                elif a.kind == "read":
                    if "(lines " in a.label or a.target in edited or searched_so_far:
                        continue
                later = calls[i + 1 :]
                if not later:
                    continue                     # the chat ended before the result was sent back
                led.carry(src(c, a), int(t * CUT), later, pr)
                if (c.agent, c.n) not in hit_calls:
                    hit_calls.append((c.agent, c.n))
                hit_turns.add(c.turn)
                kinds_hit.add("read" if a.kind == "read" else "cmd")
                name = short_target(a.target or a.label, s.cwd)
                tag = "lockfile / build file" if lock_or_build else ("whole file read" if a.kind == "read" else "tool output")
                items.append(f"`{name}` (call #{c.n}, ~{t:,} tokens, {tag})")

    if not items:
        return None
    total_tok, per_turn = led.tokens, led.per_turn()
    turns = sorted(hit_turns)
    n = len(items)
    if kinds_hit == {"read"}:
        what = f"{n} large file read{'s' if n != 1 else ''}"
    elif kinds_hit == {"cmd"}:
        what = f"{n} huge command or tool output{'s' if n != 1 else ''}"
    else:
        what = f"{n} huge tool result{'s' if n != 1 else ''} (files and command output)"
    return Fix(
        id="big-results",
        title=f"Trim {what} before they enter the chat",
        why=(f"{what.capitalize()} poured tens of thousands of tokens straight into the conversation. "
             f"Searching first or reading only the lines you need would cut ~{total_tok:,} tokens across the chat "
             f"(an estimate: it assumes about {round(CUT * 100)}% of each result was not needed)."),
        saving_usd=led.usd, saving_tokens=total_tok, claims=led.claims,
        where=turns_where(turns), turns=turns, items=items[:6], per_turn=per_turn, calls=hit_calls,
        how="Keep command output short: use head/tail or a quiet flag, or write long output to a file and read only the lines you need.",
        paste=True,
    )
