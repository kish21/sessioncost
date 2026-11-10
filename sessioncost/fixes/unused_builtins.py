"""Check unused-builtin: Claude Code's big built-in tools that a setting can switch off, never used."""
from __future__ import annotations


from .. import prices as pricemod
from ..model import Fix, Session
from .common import (tok)

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
        id="unused-builtin", title=f"Switch off Claude Code's own {' and '.join(t.what for t in idle)} "
                                   f"tool{'s' if len(idle) > 1 else ''} (never used)",
        why=(f"Claude Code's own {names} tool{'s' if len(idle) > 1 else ''} went out with every call (~{tokens:,} "
             "tokens each time) and this session never used it. Unlike most built-in tools, a setting removes "
             f"{'them' if len(idle) > 1 else 'it'} from the prompt. Only do it if you do not use "
             f"{'them' if len(idle) > 1 else 'it'} in other chats either."),
        saving_usd=usd, saving_tokens=tokens * len(s.calls), where="every call",
        items=[f"{t.what}: ~{tok(t.chars):,} tokens on every call ({BUILTIN_SWITCHES[t.what][0]}, or "
               f"{BUILTIN_SWITCHES[t.what][1]})" for t in idle], targets=[t.what for t in idle])
