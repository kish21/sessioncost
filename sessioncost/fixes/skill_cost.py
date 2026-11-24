"""Check skill-cost (owner yes 2026-11-24): WHERE the tokens go inside each skill or command the chat ran, and what
to change in that skill's own file. SessionCost never edits the skill: the card names the file and its lines, and the
user pastes the ask into that project's chat; the user's agent proposes the change and waits for a yes.

A skill run starts at a typed /command, or at a Skill step (Claude Code logs one) outside any run, and lasts until the
next typed /command; skills it loads on the way are steps inside it. Inside it: steps that each read, searched or ran one thing (batch them, or a cheaper
helper does them), helpers that only read or reviewed on the top model (helper_model.py prices those; named here so
the skill file gets the change), a review at high effort, files the skill names and so reads on every run, and history from before the skill began that every call
sent again (a new chat after each finished ticket). The saving counted here: the one-at-a-time steps priced on the
cheaper model (model_choice.py leaves those calls to this card) and the carried history, both from this chat."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..model import Call, Fix, Session, Turn
from .common import Ledger, setup_tokens, short_target, turns_where
from .helper_model import peer_saving, read_only
from .model_choice import _cheaper_peer, _is_mechanical

OWN = {"sessioncost", "liveguard", "livesession"}       # SessionCost's own (livesession: the guard's old name)
MIN_SINGLE = 8               # fewer one-at-a-time steps than this is not worth a line
MIN_CARRIED = 5_000          # history carried in from before the skill, per call, worth a new chat
MIN_SAVING_USD = 0.20
RECENT_CHATS = 4             # earlier chats of the project the per-skill line looks back on
MIN_DOCS = 3_000            # tokens of files the skill names and reads on every run, worth a line
_EFFORT = re.compile(r"\b(?:x?high|max)\b", re.I)
_HELPER_WORDS = re.compile(r"\b(?:sub-?agents?|Agent tool|Task tool|helper agents?)\b", re.I)


def _own(name: str) -> bool:
    return bool({name.split(":")[0], name.split(":")[-1]} & OWN)


# Claude Code's own commands that set something up and send no task: not a skill run (/review, /init do work: kept)
BUILT_IN = {"model", "compact", "context", "effort", "clear", "config", "cost", "usage", "help", "memory", "permissions",
            "resume", "status", "doctor", "login", "logout", "exit", "quit", "fast", "add-dir", "agents", "mcp", "plugin",
            "plugins", "hooks", "ide", "theme", "vim", "terminal-setup", "statusline", "export", "rewind", "bashes",
            "tasks", "todos", "output-style", "privacy-settings", "release-notes", "upgrade", "skills", "rename",
            "sandbox", "keybindings", "allowed-tools", "feedback", "bug", "stats", "focus"}


def _name(t: Turn) -> str:
    if t.kind != "command" or not t.text.strip():
        return ""
    name = t.text.split()[0].lstrip("/")
    return "" if name.lower() in BUILT_IN else name


def runs(s: Session) -> list[tuple[str, list[Turn]]]:
    """(skill name, its turns) for each skill or command the chat ran, outermost only (a skill it ran inside is a step)."""
    out: list[tuple[str, list[Turn]]] = []
    cur: tuple[str, list[Turn]] | None = None
    for t in s.turns:
        if t.kind == "start":
            continue
        name = _name(t)                                   # a typed /command always starts a new run
        if not name and cur is None:                      # else the first skill the agent loaded, outside any run
            name = next((a.target for c in t.calls for a in c.actions if a.kind == "skill" and a.target), "")
        if name:
            cur = None if _own(name) else (name, [t])     # SessionCost's own report ends a run
            if cur:
                out.append(cur)
        elif cur:
            cur[1].append(t)                              # the user's replies while the skill works on its task
    return out


def skill_file(name: str, project: str, home: Path | None = None) -> str:
    """The skill's own file: the project's first, then the user's. "" for plugin skills and bundled ones."""
    if ":" in name:
        return ""
    home = home or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    bases = ([Path(project) / ".claude"] if project else []) + [home]
    for b in bases:
        for p in (b / "skills" / name / "SKILL.md", b / "commands" / f"{name}.md"):
            if p.is_file():
                return str(p)
    return ""


def _line(lines: list[str], rx) -> int:
    return next((i for i, x in enumerate(lines, 1) if rx.search(x)), 0)


def _at(path: str, n: int) -> str:
    return f"`{path}` line {n}" if path and n else (f"`{path}`" if path else "the skill's instructions")


def _one_run(s: Session, name: str, turns: list[Turn], pr, home) -> dict:
    nums = {t.n for t in turns}
    calls: list[Call] = [c for t in turns for c in t.calls]
    helpers = [h for h in s.helpers if h.turn in nums]
    tokens = sum(c.usage.context + c.usage.output for c in calls + [c for h in helpers for c in h.calls])
    resent = sum(c.usage.cache_read for c in calls)
    singles = [c for c in calls if len(c.actions) == 1 and _is_mechanical(c)]
    nested = sorted({a.target for c in calls for a in c.actions if a.kind == "skill" and a.target and a.target != name})
    # skills started at high effort: "/code-review high ..." typed, or a Skill step whose arguments say so
    high = {name} if turns[0].kind == "command" and _EFFORT.search(" ".join(turns[0].text.split()[1:3])) else set()
    high |= {a.target for c in calls for a in c.actions if a.kind == "skill" and a.target
             and _EFFORT.search(a.label.split("`")[-1])}
    top_helpers = [h for h in helpers if h.calls and read_only(h) and any(_cheaper_peer(c.model) for c in h.calls)]
    full = skill_file(name, s.cwd, home)
    try:
        lines = Path(full).read_text(encoding="utf-8", errors="replace").splitlines() if full else []
    except OSError:
        lines = []
    path = short_target(full, s.cwd) if full and s.cwd and full.replace("\\", "/").lower().startswith(
        s.cwd.replace("\\", "/").lower().rstrip("/") + "/") else full.replace("\\", "/")
    led = Ledger()
    for c in singles:                                    # the one-at-a-time steps, priced on the cheaper model
        diff, _ = peer_saving([c], pr)
        if diff > 0:
            led.add(("skill-single", name, c.n), c, 0, diff, kind="price")
    carried = max(0, min((c.usage.context for c in calls), default=0) - setup_tokens(s)) if calls else 0
    if turns[0].n > 1 and carried >= MIN_CARRIED:        # history from before the skill began, sent again each call
        for c in calls:                                  # the same text the new-chat card drops: counted once (overlap.py)
            led.add(("history",), c, carried, carried * pr.rates(c)["cache_read"] / 1e6, kind="history")
    # files the skill names and the run read: they come back on every run
    docs: dict[str, int] = {}
    for c in calls:
        for a in c.actions:
            base = re.split(r"[\\/]", (a.target or "").strip())[-1]
            if a.kind == "read" and len(base) > 3 and "." in base and any(base in x for x in lines):
                docs[base] = docs.get(base, 0) + a.result_chars // 4
    return dict(name=name, turns=sorted(nums), calls=calls, helpers=helpers, tokens=tokens, resent=resent,
                singles=singles, nested=nested, high=sorted(high), top_helpers=top_helpers, path=path, lines=lines,
                carried=carried, docs=docs, led=led)


def _helper_line(lines: list[str], description: str) -> int:
    """The skill file's line that starts this helper: one naming a helper and a word of its task, else any helper."""
    words = re.findall(r"[a-z]{5,}", (description or "").lower())[:4]
    both = [i for i, x in enumerate(lines, 1) if _HELPER_WORDS.search(x) and any(w in x.lower() for w in words)]
    return both[0] if both else _line(lines, _HELPER_WORDS)


def _tips(r: dict) -> list[str]:
    """The ideas that apply to this run, each naming the file and line where it would change. A built-in or plugin
    skill has no file here: its ideas go where the user can act (the instruction file, how they start it)."""
    out, lines, path = [], r["lines"], r["path"]
    if len(r["singles"]) >= MIN_SINGLE:
        place = (_at(path, _line(lines, re.compile(r"\b(?:read|grep|search|tests?)\b", re.I))) if path
                 else "it has no file of its own here, so put that rule in your instruction file")
        out.append(f"{len(r['singles'])} steps each read, searched or ran one thing, and each sent the whole chat "
                   f"again: batch several reads in one step, and hand routine reads, searches and test runs to a "
                   f"cheaper helper ({place}).")
    docs = r["docs"]
    if sum(docs.values()) >= MIN_DOCS:
        names = sorted(docs, key=lambda d: -docs[d])[:4]
        n = min(_line(lines, re.compile(re.escape(d))) for d in names)
        listed = ", ".join(f"{d} ({docs[d]:,} tokens)" for d in names)
        out.append(f"Every run reads {listed}, which the skill names ({_at(path, n)}): keep those files short, or "
                   "point to the sections it needs.")
    for h in r["top_helpers"][:3] if path else []:
        out.append(f"Its helper “{h.description or h.agent_type}” ({h.agent_type}) ran on {h.calls[0].model} for "
                   f"read or review work: have the step that starts it ({_at(path, _helper_line(lines, h.description))})"
                   " name a cheaper model.")
    for sk in r["high"]:
        short = sk.split(":")[-1]
        n = _line(lines, re.compile(re.escape(short), re.I)) if sk != r["name"] else 0
        where = _at(path, n) if n else f"when you start it, e.g. `/{short} medium`"
        out.append(f"It ran /{short} at high effort ({where}): for small tickets a lower effort costs less "
                   "(trade-off: it may find fewer problems; keep high for big or risky changes).")
    if r["carried"] >= MIN_CARRIED and r["turns"][0] > 1:
        out.append(f"It began in a chat that already held {r['carried']:,} tokens, which every one of its "
                   f"{len(r['calls'])} calls sent again: start a new chat after each finished ticket or pull request.")
    return out


def add_history(a: dict, earlier: list[dict] | None) -> None:
    """The card's first line over the project's recent chats (fixer.record_seen keeps each chat's skill tokens):
    "/sr-ticket: 3 of the last 5 chats here, 61,000,000 tokens". `earlier`: newest first, without this chat."""
    f = next((x for x in a.get("fixes") or [] if x.get("id") == "skill-cost"), None)
    last = [s for s in earlier or [] if isinstance(s, dict) and "skill_tokens" in s][:RECENT_CHATS]
    if not f or not last:
        return
    lines = []
    for name, now in sorted((f.get("per_target") or {}).items(), key=lambda x: -x[1]):
        before = [int((s.get("skill_tokens") or {}).get(name) or 0) for s in last]
        if any(before):
            ran = 1 + sum(1 for b in before if b)
            lines.append(f"/{name}: in {ran} of the last {len(last) + 1} chats here, {now + sum(before):,} tokens "
                         "in all.")
    if lines:
        f["items"] = lines + list(f.get("items") or [])


def skill_cost(s: Session, pr, home: Path | None = None) -> Fix | None:
    found = [_one_run(s, name, turns, pr, home) for name, turns in runs(s)]
    found = [r for r in found if r["calls"] and _tips(r)]
    if not found:
        return None
    led = Ledger()
    for r in found:
        led.claims += r["led"].claims
    if led.usd < MIN_SAVING_USD:
        return None
    by_skill: dict[str, int] = {}
    for r in found:
        by_skill[r["name"]] = by_skill.get(r["name"], 0) + r["tokens"]
    top = max(by_skill, key=by_skill.get)
    items, asks = [], []
    for r in sorted(found, key=lambda r: -r["tokens"]):
        share = f"{100 * r['resent'] // max(1, r['tokens'])}%"
        items.append(f"/{r['name']} (turns {', '.join(map(str, r['turns']))}): {r['tokens']:,} tokens, {share} of "
                     f"them the chat history sent again; {len(r['calls'])} steps, {len(r['helpers'])} helper"
                     + ("s" if len(r["helpers"]) != 1 else "")
                     + (f", ran {', '.join('/' + n for n in r['nested'])} inside" if r["nested"] else "") + ".")
        items += ["   " + t for t in _tips(r)]
        asks.append(f"/{r['name']} ({r['path'] or 'built in or from a plugin: no file of its own to change'}): "
                    + " ".join(_tips(r)))
    turns = sorted({t for r in found for t in r["turns"]})
    return Fix(
        id="skill-cost",
        title=f"Where /{top} spends its tokens, and what to change in it",
        why=(f"/{top} used {by_skill[top]:,} tokens in this chat. Most went on sending the chat again with each "
             "small step, and on helpers running on the most expensive model."),
        saving_usd=led.usd, saving_tokens=led.tokens, where=turns_where(turns), turns=turns, items=items[:12],
        per_turn=led.per_turn(), calls=[cl.call for cl in led.claims], claims=led.claims,
        targets=sorted(by_skill), per_target=by_skill,
        how="Paste the ask into this project's chat: your agent proposes the changes to the skill file and waits for "
            "your yes. SessionCost never edits your skills.",
        ask=("Make these skills use fewer tokens. " + " ".join(asks) + " For each idea, show me the exact change "
             "to the skill file and wait for my yes before editing it."),
    )
