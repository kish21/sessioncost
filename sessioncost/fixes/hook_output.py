"""Check hook-output (owner, 2026-11-24): a hook printed long text into the chat. Everything a hook prints for the
agent stays in the chat and goes out again with every later message. Claude Code only: its log is the one that marks
hook output. The settings lines that define the hooks are quoted (workspace.py), SessionCost's own hooks left out."""
from __future__ import annotations

import json
import re

from ..model import Fix, Session
from .common import Ledger, tok, turns_where

MIN_EACH = 300               # tokens one hook output must reach to count
ONE_LINE = 40                # what a one-line hook output costs instead
MIN_TOTAL = 2_000            # carried tokens the tip needs to be worth showing
OURS = ("sessioncost", "continue from the handover of an earlier chat")   # SessionCost's own hooks
SHOWN_ONLY = "to you only: "     # the reader's mark for a hook message shown to the user, not sent to the agent
# hook events whose output the agent sees on a normal run (Claude Code hooks docs, read 2026-11-24); a PreToolUse
# guard speaks only when it blocks, and the status line is not a hook
TALKS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolBatch", "SubagentStart")
# a value after Bearer / token= / password: in a hook's command line; known key shapes are caught by _SECRET_VALUE
_NAMED_SECRET = re.compile(r"(\bBearer\s+|\b(?:[\w-]*(?:token|key|secret|password|passwd|pwd))\s*[=:]\s*['\"]?)"
                           r"[^\s'\"]+", re.I)


def _no_keys(text: str) -> str:
    """A hook's command or words as the tip quotes them: keys and tokens hidden (the tip's text is pasted into chat)."""
    from ..risk_patterns import _SECRET_VALUE
    return _NAMED_SECRET.sub(r"\1[secret hidden]", _SECRET_VALUE.sub("[secret hidden]", text or ""))


def hook_output(s: Session, pr, home=None) -> Fix | None:
    calls = s.calls
    if s.agent != "claude-code" or len(calls) < 2:
        return None
    order = {id(c): i for i, c in enumerate(calls)}
    led, items, turns, hit = Ledger(), [], set(), []
    for t in s.turns:
        if not t.calls:
            continue
        first = order.get(id(t.calls[0]))
        for k, e in enumerate(t.events):
            if e.kind != "injected" or "hook output" not in e.label or any(o in e.text.lower() for o in OURS) \
                    or e.text.startswith((SHOWN_ONLY, "<ide_")):      # the editor's own notes are not your hooks
                continue
            n = tok(e.chars)
            if n < MIN_EACH or first is None:
                continue
            start = first
            if e.ts:                 # output after a tool step: carried from the next call on
                start = next((order[id(c)] for c in t.calls if c.ts_first and c.ts_first >= e.ts), first)
            led.carry(("hook", t.n, k), n - ONE_LINE, calls[start:], pr)
            turns.add(t.n)
            hit.append(("main", calls[start].n))
            items.append(f"turn {t.n}: ~{n:,} tokens of hook output" + (f" (“{_no_keys(e.text)[:60]}”)" if e.text else ""))
    if led.tokens < MIN_TOTAL:
        return None
    from .. import workspace
    ws = workspace.of(s, home)
    defined = []
    for p, data in ws.settings():           # only events whose output reaches the agent on a normal run
        hooks = data.get("hooks") if isinstance(data, dict) else None
        for event, groups in (hooks if isinstance(hooks, dict) else {}).items():
            if event not in TALKS:
                continue
            for g in groups if isinstance(groups, list) else []:
                hs = g.get("hooks") if isinstance(g, dict) else None
                for h in hs if isinstance(hs, list) else []:     # a hand-edited file can hold any JSON shape
                    cmd = str(h.get("command") or "") if isinstance(h, dict) else ""
                    if cmd and not any(o in cmd.lower() for o in OURS):
                        as_json = json.dumps(cmd)[1:41]          # as written in the file (quotes escaped)
                        ln = next((x for x in ws.find(re.escape(as_json), [p], 0)), None)
                        defined.append(f"{event}: {ln.at() if ln else ws.show(p)} (“{_no_keys(cmd)[:70]}”)")
    where = "; ".join(defined[:4]) if defined else "your hooks (Claude Code settings, `hooks`)"
    return Fix(
        id="hook-output",
        title=f"Make your hooks print one line ({len(items)} long hook output{'s' if len(items) > 1 else ''})",
        why=(f"Your hooks added long text to the chat {len(items)} time{'s' if len(items) > 1 else ''}. What a hook "
             "prints for the agent stays in the chat and is sent again with every later message."),
        saving_usd=led.usd, saving_tokens=led.tokens, claims=led.claims, where=turns_where(sorted(turns)),
        turns=sorted(turns), items=items[:6], per_turn=led.per_turn(), calls=hit,
        how=(f"Best practice: a hook prints at most one short line for the agent and writes any detail to a log "
             f"file. Your hooks are set at: {where}."),
        ask=(f"My hooks print long text into the chat. They are set at: {where}. For each one, propose a change so it "
             "prints one short line for you and writes the rest to a log file. Wait for my yes before editing."),
    )
