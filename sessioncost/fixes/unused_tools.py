"""Check unused-tools: connected tools (MCP servers) sent with every call and never used."""
from __future__ import annotations

import re

from .. import prices as pricemod
from ..model import Fix, Session
from .common import (tok)

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


AGENT_OWN = {"claudevscode", "ide"}   # the editor bridge that comes with the agent (VS Code), like its built-in tools


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
        return bool(sv) and _loose(sv) not in AGENT_OWN and _loose(sv) not in used_servers and \
            not any(_loose(sv) in u or u in _loose(sv) for u in used_servers if u)

    unused = [t for t in s.setup.tools if t.what not in used and idle_server(server_of(t.what))]
    for t in unused:
        if server_of(t.what) not in idle_servers:
            idle_servers.append(server_of(t.what))
    tokens = sum(tok(t.chars) for t in unused)
    per: dict[str, int] = {}                 # tokens of each server, for `sessioncost fix` (it switches off some)
    for t in unused:
        per[server_of(t.what)] = per.get(server_of(t.what), 0) + tok(t.chars) * len(s.calls)
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
        weights = ({sv: sum(1 for n in idle if server_of(n) == sv) for sv in svs} if ad.kind == "tools"
                   else {sv: 1 for sv in svs})
        for sv, w in weights.items():
            per[sv] = per.get(sv, 0) + round(share * len(carry) * w / max(1, sum(weights.values())))
        late_items.append(f"{what}, ~{share:,} tokens from call {ad.after_call + 1} on ({len(carry)} calls)")
    if not saving or not idle_servers:
        return None
    by_server: dict[str, list] = {}
    for t in unused:
        by_server.setdefault(server_name(server_of(t.what)), []).append(t)
    items = [f"{sv}: {len(ts)} tool{'s' if len(ts) != 1 else ''}, ~{sum(tok(t.chars) for t in ts):,} tokens on every call"
             for sv, ts in sorted(by_server.items(), key=lambda x: -sum(t.chars for t in x[1]))] + late_items
    return Fix(how=turn_off_steps(s.agent, idle_servers), paste=False,   # a setting the user changes, not a rule
        id="unused-tools", title=TITLE, why=_why([server_name(x) for x in idle_servers], saving // len(s.calls), None),
        saving_usd=usd, saving_tokens=saving, where="every call", items=items[:8], targets=list(idle_servers),
        per_target={k: v for k, v in per.items() if k})


TITLE = "MCP servers and connectors you have not used lately"
MIN_PER_CALL = 2_000     # proposed (owner 2026-11-23): less than this on each call is not worth a card
RECENT = 2               # earlier chats in this project that must also leave a server unused


def _why(names: list[str], per_call: int, recent: int | None) -> str:
    when = f"this chat or your last {recent} chats in this project" if recent else "this chat"
    return (f"Not used in {when}: {', '.join(names)}. Their tool lists and instructions go out with every call "
            f"(~{per_call:,} tokens each time). Turn one off only if you do not need it; you can turn it back on any "
            "time. (The agent's own built-in tools come with it and are not counted.)")


def _less_saving(a: dict, f: dict, whole: int, now: int) -> None:
    """The report's total for all fixes together (analyse: each token counted once) loses what this card no longer
    claims. The part it shared with other cards stays: those cards still claim it."""
    sav = a.get("saving")
    gone = max(0, whole - now)
    if not isinstance(sav, dict) or not whole or not gone:
        return
    share = sum(int(v or 0) for v in (f.get("shared") or {}).values()) * gone / whole
    cut = max(0, int(gone - share))
    usd = float(f.get("saving_usd") or 0.0) * cut / whole
    for k in ("tokens", "setup_tokens"):
        if k in sav:
            sav[k] = max(0, int(sav[k] or 0) - cut)
    if "usd" in sav:
        sav["usd"] = max(0.0, float(sav["usd"] or 0.0) - usd)


def settle(a: dict, earlier: list[dict] | None) -> None:
    """The report's card (owner 2026-11-23: one chat is not enough to say a project does not need a server): keep
    only servers the last RECENT chats here left unused too, and only if they cost MIN_PER_CALL tokens a call.
    `earlier`: the project's remembered chats, newest first, without this one; None when there is no project."""
    fs = a.get("fixes") or []
    f = next((x for x in fs if x.get("id") == "unused-tools"), None)
    if not f:
        return
    calls = max(1, int((a.get("totals") or {}).get("calls") or 0))
    keep = list(f.get("targets") or [])
    if earlier is not None:
        last = [s for s in earlier if isinstance(s, dict) and "unused_targets" in s][:RECENT]
        keep = [t for t in keep if len(last) == RECENT and
                all(t in ((s.get("unused_targets") or {}).get("unused-tools") or []) for s in last)]
    per = f.get("per_target") or {}
    whole = int(f.get("saving_tokens") or 0)
    tokens = sum(int(per.get(t) or 0) for t in keep) if per else (whole if keep else 0)
    if not keep or tokens / calls < MIN_PER_CALL:
        _less_saving(a, f, whole, 0)
        a["fixes"] = [x for x in fs if x is not f]
        for x in a["fixes"]:                  # no other card names the one that is gone
            x["overlaps"] = [i for i in x.get("overlaps") or [] if i != f["id"]]
            x["shared"] = {k: v for k, v in (x.get("shared") or {}).items() if k != f["id"]}
        for row in list(a.get("turns") or []) + list(a.get("agents") or []):
            if isinstance(row, dict) and isinstance(row.get("fixes"), list):
                row["fixes"] = [i for i in row["fixes"] if i != f["id"]]
        return
    _less_saving(a, f, whole, tokens)
    names = [server_name(x) for x in keep]
    f.update(title=TITLE, why=_why(names, tokens // calls, RECENT if earlier is not None else None),
             how=turn_off_steps(str((a.get("meta") or {}).get("agent") or ""), keep),
             saving_usd=float(f.get("saving_usd") or 0.0) * tokens / whole if whole else 0.0, saving_tokens=tokens,
             items=[i for i in f.get("items") or [] if any(n in i for n in names)], targets=keep,
             per_target={t: per[t] for t in keep if t in per})
