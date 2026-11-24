"""Check unused-skills: skills whose name and description went out with every call, and the chat never used.

Claude Code only: its log holds the skill list. `sessioncost fix` hides the plain ones from the agent in this project
with "skillOverrides": {"<name>": "user-invocable-only"} (the user can still type /<name>; code.claude.com/docs
skills, 2026-11-23). Plugin skills are not affected by that setting, and claude.ai skills are switched off on
claude.ai, so those two stay the user's own steps."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import prices as pricemod
from ..model import Fix, Session
from .common import before_change_note, changed_after_start, tok

OWN = {"sessioncost", "liveguard"}       # SessionCost's own skills: the report and the guard need them
CLAUDE_AI = "anthropic-skills:"          # skills that come from the user's claude.ai account
HIDE = "user-invocable-only"             # hidden from the agent, still typeable as /name
MIN_TOKENS = 150                         # less than this on every call is not worth a tip
SKILL_DIR = re.compile(r"[\\/]skills[\\/]([^\\/]+)[\\/]SKILL\.md$", re.I)


def kind(name: str, project=None, home: Path | None = None) -> str:
    """Where a skill comes from: "plugin", "claude.ai" (synced from the user's account), "project" (this project's
    own .claude/skills: other chats here may need it, so it is never hidden by itself) or "own" (the user's own
    skills and Claude Code's bundled ones, which skillOverrides reaches)."""
    if name.startswith(CLAUDE_AI):
        return "claude.ai"
    if ":" in name:
        return "plugin"
    here = Path(project) / ".claude" if project else None
    if here and ((here / "skills" / name).is_dir() or (here / "commands" / f"{name}.md").is_file()):
        return "project"           # a project's own skill or command (/jr-ticket): listed the same way
    claude = home / ".claude" if home else Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    synced = claude / "skills" / "synced"
    if synced.is_dir() and any((d / name).is_dir() for d in synced.iterdir() if d.is_dir()):
        return "claude.ai"
    return "own"


def plain(name: str, project=None, home: Path | None = None) -> bool:
    """A skill `sessioncost fix` hides by itself (skillOverrides)."""
    return kind(name, project, home) == "own"


def _short(name: str) -> str:
    return name[len(CLAUDE_AI):] if name.startswith(CLAUDE_AI) else name


def used_skills(s: Session) -> set[str]:
    """Skills the chat loaded, typed as /name, or read the SKILL.md of; both "plugin:skill" and "skill"."""
    names = {a.target for a in s.actions() if a.kind == "skill" and a.target}
    names |= {t.text.split()[0].lstrip("/") for t in s.turns if t.kind == "command" and t.text.strip()}
    names |= {m.group(1) for a in s.actions() if a.kind == "read" for m in [SKILL_DIR.search(a.target or "")] if m}
    return names | {n.split(":")[-1] for n in names}


def off_steps(names: list[str], project=None, home: Path | None = None, ws=None) -> str:
    """Where each kind of skill is switched off in Claude Code, as numbered steps."""
    by = {n: kind(n, project, home) for n in names}
    own = [n for n in names if by[n] == "own"]
    mine = [n for n in names if by[n] == "project"]
    synced = [n for n in names if by[n] == "claude.ai"]
    plug = [n for n in names if by[n] == "plugin"]
    steps = []
    if own:
        overrides = ", ".join(f'"{n}": "{HIDE}"' for n in own)
        steps.append(f"Hide {', '.join(own)} from the agent in this project: in .claude/settings.local.json add "
                     f'"skillOverrides": {{{overrides}}}. You can still type /{own[0]} when you want it. '
                     "(`sessioncost fix` does this for you.)")
    if mine:
        steps.append(f"This project's own skills ({', '.join(mine)}): other chats here may need them, so SessionCost "
                     "leaves them on. If one is not needed any more, type /skills and hide it.")
    if synced:
        steps.append(f"Skills from your claude.ai account ({', '.join(_short(n) for n in synced)}): turn off the "
                     "ones you do not use at claude.ai, Settings, Skills. This applies to every project.")
    if plug:
        plugins = sorted({n.split(':')[0] for n in plug})
        steps.append(f"Skills of a plugin ({', '.join(plug)}): type /plugin, open Installed, and disable "
                     f"{', '.join(plugins)} if you do not use it.")
        if ws is not None:          # the settings line that turns it on, so the user sees where it comes from
            on = [ln for p, _ in ws.settings() for ln in ws.find(rf'"({"|".join(map(re.escape, plugins))})@[^"]*"\s*:'
                                                                 r'\s*true', [p])]
            if on:
                steps[-1] += " It is turned on at " + "; ".join(ln.quote(80) for ln in on[:3]) + ": set it to false there."
    steps.append("Start a new chat: the change applies from then on.")
    return "\n".join(f"{i}. {x}" for i, x in enumerate(steps, 1))


def unused_skills(s: Session, P) -> Fix | None:
    if s.agent != "claude-code" or not s.calls or not s.setup.skills:
        return None
    used = used_skills(s)
    idle = [x for x in s.setup.skills if not {x.what, x.what.split(":")[0], x.what.split(":")[-1]} & OWN
            and x.what not in used and x.what.split(":")[-1] not in used]     # sessioncost:liveguard is ours too
    tokens = sum(tok(x.chars) for x in idle)
    if tokens < MIN_TOKENS:
        return None
    usd = 0.0
    for c in s.calls:                    # the skill list is the front of the prompt: mostly read from the cache
        r, u = P.rates(c), c.usage
        from_cache = min(tokens, u.cache_read)
        as_new = min(tokens - from_cache, u.new_input)
        usd += (from_cache * r["cache_read"] + as_new * pricemod.new_input_rate(u, r)) / 1e6
    idle.sort(key=lambda x: -x.chars)
    names = [x.what for x in idle]
    shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    here = Path(s.cwd) / ".claude" if s.cwd else None
    settings = [claude / "settings.json"] + ([here / "settings.json", here / "settings.local.json"] if here else [])
    changed = changed_after_start(s, settings)
    from .startup_bloat import _short_path
    note = before_change_note([_short_path(p) for p in changed])
    from .. import workspace
    return Fix(how=off_steps(names, s.cwd or None, ws=workspace.of(s)), paste=False,
        id="unused-skills", title=f"Hide the {len(names)} skill{'s' if len(names) != 1 else ''} this chat never used",
        why=(f"The name and description of {shown} went out with every call (~{tokens:,} tokens each time) and this "
             "chat never used them. Hidden from the agent, they cost nothing; you can still type /name to run one."
             + note),
        saving_usd=usd, saving_tokens=tokens * len(s.calls), where="every call",
        items=[f"{x.what}: ~{tok(x.chars):,} tokens on every call" for x in idle[:8]], targets=names,
        per_target={x.what: tok(x.chars) * len(s.calls) for x in idle})
