"""sessioncost fix, the to-do list: one item per kind of waste found, the change that fixes each, and how it is shown."""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import fixes as fixmod
from . import rules
from .fixer_store import (BUILTIN_SETTING, HEAD, LONG_CHAT, RULE_OF, SETTINGS, _first, _load, _tok, _ts, budget_usd,
                          fixes_file, learned, long_chat)



# ---- the to-do list and the change for each item ----
@dataclass
class Change:
    path: Path
    before: str | None          # None: the file does not exist yet
    after: str

    def diff(self, project: Path) -> str:
        try:
            name = self.path.relative_to(project).as_posix()
        except ValueError:
            name = self.path.as_posix()
        a = [x if x.endswith("\n") else x + "\n" for x in (self.before or "").replace("\r\n", "\n").splitlines(True)]
        b = [x if x.endswith("\n") else x + "\n" for x in self.after.replace("\r\n", "\n").splitlines(True)]
        return "".join(difflib.unified_diff(a, b, "/dev/null" if self.before is None else f"a/{name}", f"b/{name}", n=1))


@dataclass
class Item:
    id: str
    title: str
    why: str
    tokens: int                 # the waste measured in this chat
    kind: str                   # rule | setting | manual | handover | came-back | not-followed
    line: str = ""              # the to-do line the agent shows
    change: Change | None = None
    steps: str = ""             # what the user does by hand (no setting SessionCost can change)
    problem: str = ""           # why it cannot be applied

    @property
    def can_apply(self) -> bool:
        return self.kind in ("rule", "setting") and self.change is not None and not self.problem


@dataclass
class Plan:
    project: Path
    agent: str
    chat: str
    title: str
    calls: int
    context: int
    usd: float = 0.0
    items: list[Item] = field(default_factory=list)
    originals: dict[str, bytes | None] = field(default_factory=dict)
    finals: dict[str, str] = field(default_factory=dict)

    @property
    def saving_tokens(self) -> int:
        """Only what applying would fix: never manual steps, the handover or waste that came back."""
        return sum(i.tokens for i in self.items if i.can_apply)


def rules_file(agent: str, project: Path) -> Path:
    if agent == "claude-code":
        return project / "CLAUDE.md"
    if agent == "antigravity":
        only_agents = (project / "AGENTS.md").is_file() and not (project / "GEMINI.md").is_file()
        return project / ("AGENTS.md" if only_agents else "GEMINI.md")
    if agent == "cursor":
        return project / ".cursor" / "rules" / "sessioncost.mdc"
    if agent in ("copilot", "copilot-vscode"):
        return project / ".github" / "copilot-instructions.md"
    return project / "AGENTS.md"


def add_rule(text: str | None, rule: str, path: Path) -> str:
    """The file with one rule more, under SessionCost's own heading; everything else as it was."""
    nl = "\r\n" if text and "\r\n" in text else "\n"
    bullet = f"- {rule}"
    if not text or not text.strip():
        top = (f"---{nl}description: SessionCost rules that cut token waste{nl}alwaysApply: true{nl}---{nl}{nl}"
               if path.suffix == ".mdc" else "")
        return f"{top}{HEAD}{nl}{nl}{bullet}{nl}"
    lines = text.splitlines()
    if HEAD in lines:
        i = lines.index(HEAD)
        end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("#")), len(lines))
        while end > i + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, bullet)
        return nl.join(lines) + nl
    return text.rstrip("\r\n") + nl + nl + HEAD + nl + nl + bullet + nl


def _read(path: Path) -> tuple[str | None, bytes | None, str]:
    """(text, bytes as they are, problem). A file that is not UTF-8 text is never touched."""
    if not path.exists():
        return None, None, ""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, None, f"could not read {path.name} ({e.strerror or e}), so it was not changed"
    try:
        return raw.decode("utf-8-sig"), raw, ""
    except UnicodeDecodeError:
        return None, raw, f"{path.name} is not UTF-8 text, so it was not changed"


def _settings(text: str | None, updates: dict) -> tuple[str | None, str]:
    """(new text, problem). A settings file that does not parse is never overwritten."""
    data: dict = {}
    if text and text.strip():
        try:
            data = json.loads(text)
        except ValueError:
            return None, "is not valid JSON, so SessionCost did not change it: fix it by hand, then run this again"
        if not isinstance(data, dict):
            return None, "is not a JSON object, so SessionCost did not change it"
    new, changed = dict(data), False
    for k, v in updates.items():
        if isinstance(v, list):
            have = new.get(k) if isinstance(new.get(k), list) else []
            add = [x for x in v if x not in have]
            if add:
                new[k], changed = have + add, True
        elif new.get(k) != v:
            new[k], changed = v, True
    return (json.dumps(new, indent=2) + "\n" if changed else text), ""


def split_servers(project: Path, targets: list[str]) -> tuple[list[str], list[str]]:
    """(servers of this project's .mcp.json, which SessionCost can switch off in Claude Code; the rest)."""
    mcp = _load(Path(project) / ".mcp.json", {}).get("mcpServers") or {}
    return [t for t in targets if t in mcp], [t for t in targets if t not in mcp]


def not_followed(rule: dict, started: float) -> bool:
    """The rule was in the agent's file before this chat began, and the waste happened anyway."""
    return bool(rule.get("in_file")) and bool(started) and 0 < _ts(rule.get("file_changed")) < started


def for_report(a: dict, project) -> None:
    """Tell the report which settings `sessioncost fix` changes by itself (Claude Code), so the card does not say
    "the agent cannot change it", and leaves the user's own steps only for the rest."""
    if (a.get("meta") or {}).get("agent") != "claude-code" or not project:
        return
    for f in a.get("fixes") or []:
        if f.get("id") == "unused-builtin":
            f["sc_can"] = [t for t in f.get("targets") or [] if t in BUILTIN_SETTING]
            if f["sc_can"]:
                f["how"] = ""
        elif f.get("id") == "unused-tools":
            idle, rest = split_servers(Path(project), f.get("targets") or [])
            if idle:
                f["sc_can"] = idle
                f["how"] = fixmod.turn_off_steps("claude-code", rest) if rest else ""


def plan(a: dict, project, only: set[str] | None = None, home: Path | None = None) -> Plan:
    """Each waste found in this chat, ranked by tokens, with the exact change that fixes it (nothing is written)."""
    project = Path(project).resolve()
    meta = a.get("meta") or {}
    agent = meta.get("agent") or "claude-code"
    main = [c for c in a.get("calls") or [] if c.get("agent", "main") == "main"]
    p = Plan(project=project, agent=agent, chat=str(meta.get("session_id") or ""), title=str(meta.get("title") or ""),
             calls=int((a.get("totals") or {}).get("calls") or len(main)),
             context=int(main[-1].get("context") or 0) if main else 0,
             usd=float((a.get("totals") or {}).get("usd") or 0.0))
    started = _ts(meta.get("started"))
    mem_file = _load(fixes_file(project), {})
    applied = {r.get("id"): r for r in mem_file.get("applied", []) if isinstance(r, dict)}
    seen_chats = mem_file.get("seen") or {}
    recent_seen = [s for cid, s in sorted(seen_chats.items(), key=lambda x: -_ts(x[1].get("started")))
                   if cid != p.chat and isinstance(s, dict) and "unused_targets" in s][:4]
    checked = {r["text"]: r for r in rules.check(agent, str(project), home)["rules"]}
    in_file = {t: r["in_file"] for t, r in checked.items()}
    texts: dict[Path, str | None] = {}
    problems: dict[Path, str] = {}

    def current(path: Path) -> tuple[str | None, str]:
        if path not in texts:
            text, raw, prob = _read(path)
            texts[path], problems[path], p.originals[str(path)] = text, prob, raw
        return texts[path], problems[path]

    def came_back(fid: str) -> dict | None:
        rec = applied.get(fid)
        return rec if rec and rec.get("chat") != p.chat and started > _ts(rec.get("at")) else None

    for f in a.get("fixes") or []:
        fid = str(f.get("id") or "")
        if fid in ("long-chat", "model-choice") or (only is not None and fid not in only):
            continue
        targets = list(f.get("targets") or [])
        if fid in ("unused-tools", "unused-builtin") and targets and recent_seen:
            targets = [t for t in targets if all(t in (s.get("unused_targets") or {}).get(fid, []) for s in recent_seen)]
            if not targets:
                continue
        it = Item(id=fid, title=str(f.get("title") or fid), why=_first(f.get("why", "")),
                  tokens=int(f.get("saving_tokens") or 0), kind="manual", steps=str(f.get("how") or ""))
        rule, updates, rest = RULE_OF.get(fid), {}, []
        if fid == "unused-builtin" and agent == "claude-code":
            updates = dict(BUILTIN_SETTING[t] for t in targets if t in BUILTIN_SETTING)
        elif fid == "unused-tools" and agent == "claude-code":
            idle, rest = split_servers(project, targets)
            if idle:
                updates = {"disabledMcpjsonServers": idle}
                per = f.get("per_target") or {}
                it.tokens = (sum(int(per.get(t) or 0) for t in idle) if per else
                             round(it.tokens * len(idle) / max(1, len(idle) + len(rest))))
        if rule:
            it.kind, it.steps = "rule", ""    # the rule is the change itself: nothing for the user to do by hand
            if in_file.get(rule):
                if came_back(fid):
                    it.change = None
                elif not_followed(checked[rule], started):
                    it.kind, it.problem = "not-followed", ""
                    it.line = (f"{it.title}: the rule is in {in_file[rule]}, but the agent did not follow it in this "
                               f"chat (~{_tok(it.tokens)} tokens)")
                    p.items.append(it)
                    continue
                else:
                    continue                  # already fixed (in a file the agent reads): not shown again
            else:
                path = rules_file(agent, project)
                before, prob = current(path)
                if prob:
                    it.problem = prob
                else:
                    it.change = Change(path, before, add_rule(before, rule, path))
                    texts[path] = it.change.after
        elif updates:
            it.kind, path = "setting", project / SETTINGS
            before, prob = current(path)
            after, prob2 = (None, prob) if prob else _settings(before, updates)
            if prob2:
                it.problem = f"{SETTINGS.as_posix()} {prob2}" if not prob else prob
            elif after == before:
                if not came_back(fid):
                    continue
                it.change = None
            else:
                it.change = Change(path, before, after)
                texts[path] = after
            it.steps = (f"Not in this project's .mcp.json, so SessionCost cannot switch these off: {', '.join(rest)}.\n"
                        + fixmod.turn_off_steps(agent, rest)) if rest else ""
        elif not it.steps:
            continue
        if (it.kind in ("rule", "setting")) and not it.change and not it.problem:
            rec = came_back(fid)
            it.kind = "came-back"
            it.line = (f"{it.title}: it came back after the fix of {str(rec.get('at'))[:10]} "
                       f"(~{_tok(it.tokens)} tokens in this chat)")
        else:
            it.line = f"{it.title} (~{_tok(it.tokens)} tokens wasted in this chat)"
        p.items.append(it)

    # rules the developer's own agent found in earlier chats: offered in every project until they are in its files
    have = " ".join(rules._plain(f.read_text(encoding="utf-8", errors="replace"))
                    for _, f in rules.files(agent, str(project), home))
    for x in learned():
        if (only is not None and x["id"] not in only) or rules._plain(x["rule"]) in have:
            continue
        it = Item(id=x["id"], title=x["what"], kind="rule", tokens=0,
                  why=f"Found by your agent in an earlier chat ({int(x.get('times') or 1)}x). SessionCost has no check "
                      "that measures it, so no tokens are counted for it.",
                  line=f"{x['what']} (found by your agent)")
        path = rules_file(agent, project)
        before, prob = current(path)
        if prob:
            it.problem = prob
        else:
            it.change = Change(path, before, add_rule(before, x["rule"], path))
            texts[path] = it.change.after
        p.items.append(it)

    lc = next((f for f in a.get("fixes") or [] if f.get("id") == "long-chat"), None)
    limit, busd = long_chat(), budget_usd()
    over_tok = p.context >= limit or (lc and limit <= LONG_CHAT)
    over_usd = busd is not None and p.usd >= busd
    if (only is None or "long-chat" in only) and (over_tok or over_usd):
        k = max(p.context, LONG_CHAT if lc else 0) // 1000
        if over_usd and not over_tok:
            why = f"This chat has reached ${p.usd:.2f} (your ${busd:.2f} budget; {k}k tokens in context): every message re-sends all of it."
            line = (f"This chat has reached ${p.usd:.2f} (your ${busd:.2f} budget; {k}k tokens): every message re-sends "
                    "all of it.")
        else:
            extra = f" (${p.usd:.2f}, past your ${busd:.2f} budget)" if over_usd else ""
            why = f"This chat is at {k}k tokens{extra}: every message re-sends all of it."
            line = f"This chat is at {k}k tokens{extra}: every message re-sends all of it."   # fixer._one_line asks
        p.items.append(Item(
            id="long-chat", title="Start a fresh chat with a handover", kind="handover",
            why=why, tokens=int(lc.get("saving_tokens") or 0) if lc else 0, line=line))
    p.items.sort(key=lambda i: -i.tokens)
    for path, text in texts.items():
        raw = p.originals.get(str(path))
        old = raw.decode("utf-8-sig") if raw is not None and not problems.get(path) else None
        if text is not None and text != old:
            p.finals[str(path)] = text
    return p


def render(p: Plan, verify: list[str] | None = None) -> str:
    out = []
    if verify:
        out += ["Earlier fixes in this project:"] + [f"  - {v}" for v in verify] + [""]
    if not p.items:
        out.append("SessionCost fix: no token waste to fix in the newest chat of this project.")
        return "\n".join(out)
    out.append(f"SessionCost fix: {len(p.items)} thing{'s' if len(p.items) != 1 else ''} wasting tokens in "
               f"\"{p.title[:60]}\" (the newest chat of this project), most tokens first:")
    for n, it in enumerate(p.items, 1):
        size = f"~{_tok(it.tokens)} tokens in this chat" if it.tokens or not it.id.startswith("learned-") else \
            "found by your agent, not measured"
        out.append(f"\n{n}. [{it.id}] {it.title} · {size}")
        out.append(f"   {it.why}")
        if it.problem:
            out.append(f"   Not changed: {it.problem}")
        elif it.kind == "came-back":
            out.append(f"   {it.line}. The fix is in place; nothing more to change.")
        elif it.kind == "not-followed":
            out.append(f"   {it.line}. Nothing to apply: remind the agent of the rule in the chat, or reword it more "
                       "plainly in that file.")
        elif it.change:
            out.append("   The change:")
            out += ["     " + x for x in it.change.diff(p.project).rstrip("\n").splitlines()]
        elif it.kind == "handover":
            out.append("   The fix: your agent writes a handover (goal, what is done, files changed, tests run, what is "
                       "next) and you start a fresh chat. Nothing in your files changes.")
        if it.steps:
            out.append("   You do the rest yourself:" if it.change else
                       "   You do this one (SessionCost has no setting to change it):")
            out += ["     " + x for x in it.steps.splitlines()]
    can = [n for n, it in enumerate(p.items, 1) if it.can_apply]
    if can:
        out.append(f"\nApplying {', '.join(map(str, can))} would have cut ~{_tok(p.saving_tokens)} tokens from this chat. "
                   "A backup is kept: to put it back, say \"undo the fix\" (or type `sessioncost fix --undo`).")
    return "\n".join(out)
