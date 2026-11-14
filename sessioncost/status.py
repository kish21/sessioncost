"""What is SessionCost doing? One short block, first in `sessioncost last` (and --brief, which /sessioncost runs) and in
`sessioncost guard`: the guard and what it did in this chat, token tips and the last tip, how exact this chat's numbers
are, and whether the prompt cache is still warm. Plain words; no vendor tool is named."""
from __future__ import annotations

from datetime import datetime, timezone

HEAD = "What SessionCost is doing:"
CACHE_MIN = {"5m": 5, "1h": 60}          # how long Claude keeps a cache write (reset on each use)


def _tok(n: float) -> str:
    n = float(n or 0)
    return f"{n / 1e6:.1f}M" if n >= 999_500 else f"{n / 1e3:.0f}k" if n >= 1000 else f"{n:.0f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def guard_line(g: dict | None = None, in_chat: bool = True) -> str:
    """g: this chat's guard summary (analyse's a["guard"]), None when no step of it was checked."""
    from .guard_store import _read, guard_on, home
    cfg = _read(home() / "config.json", {})
    if not guard_on(cfg):
        return 'Guard: off; steps are not checked. Say "turn the guard on" (or type `sessioncost guard on`)'
    line = f"Guard: on{' (strict)' if cfg.get('strict') else ''} for every chat"
    if not in_chat:
        return line
    if not g:
        return (f"{line}; no step of this chat was checked (this agent may not have the guard: run "
                "`sessioncost setup`, then start a new chat)")
    broken = "" if g.get("intact", True) else "; its log was changed or cut off"
    return (f"{line}; this chat: {_plural(int(g.get('checked') or 0), 'step')} checked, {g.get('blocked', 0)} blocked, "
            f"{g.get('asked', 0)} asked you{broken}")


def tips_line(chat: str = "") -> str:
    from .fixer_store import _load, auto_on, chat_file
    if not auto_on():
        return 'Token tips: off. Say "turn token tips on" (or type `sessioncost fix auto on`)'
    if not chat:
        return "Token tips: on for every chat"
    last = _load(chat_file(chat), {}).get("last_tip") or {}
    text = str(last.get("line") or "").replace("💡 SessionCost: ", "").strip()
    if not text:
        return "Token tips: on; none in this chat yet"
    text = text if len(text) <= 110 else text[:109] + "…"
    when = datetime.fromtimestamp(float(last.get("at") or 0)).strftime("%H:%M")
    return f"Token tips: on; last tip in this chat ({when}): {text}"


def numbers_line(a: dict) -> str:
    mo, label = a.get("money") or {}, (a.get("meta") or {}).get("agent_label") or "the agent"
    tok = (f"tokens exact (from {label}'s own log)" if mo.get("tokens_exact", True) else
           f"tokens estimated from the size of the text ({label} does not record them all)")
    if mo.get("subscription"):
        money = "money at API prices; your plan is not billed per token"
    else:
        short = str(mo.get("short") or "").removeprefix("estimate").strip(" :;,") or "at API prices"
        money = f"money an estimate {short}, not your bill"
    return f"Numbers: {tok}; {money}"


def _end(c: dict) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(c.get("end") or c.get("ts") or ""))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def cache_line(a: dict, now: datetime | None = None) -> str:
    mo, meta, t = a.get("money") or {}, a.get("meta") or {}, a.get("totals") or {}
    if not mo.get("tokens_exact", True):
        return f"Cache: {meta.get('agent_label') or 'this agent'}'s log does not record the cache"
    read = sum(c.get("tokens", 0) for c in t.get("by_class") or [] if c.get("key") == "cache_read")
    sent = float(t.get("sent") or 0)
    line = f"Cache: {read / sent:.0%} of what was sent was re-read from the cache (the cheap part)" if sent else \
        "Cache: nothing sent yet"
    main = [c for c in a.get("calls") or [] if c.get("agent", "main") == "main"]
    if not main or not str(meta.get("agent", "")).startswith("claude"):
        return line                       # only Claude's log says how long the cache is kept
    u = next((c.get("usage") or {} for c in reversed(main) if (c.get("usage") or {}).get("cache_write_1h")
              or (c.get("usage") or {}).get("cache_write_5m")), {})
    keep = "1h" if u.get("cache_write_1h") else "5m" if u.get("cache_write_5m") else ""
    end = _end(main[-1])
    if not keep or not end:
        return line
    ago = max(0.0, ((now or datetime.now(timezone.utc)) - end).total_seconds() / 60)
    kept = "1 hour" if keep == "1h" else "5 minutes"
    when = f"{ago:.0f} min" if ago < 120 else f"{ago / 60:.0f} hours" if ago < 2880 else f"{ago / 1440:.0f} days"
    if ago <= CACHE_MIN[keep]:
        return f"{line}; kept {kept}; last step {when} ago: still warm"
    return (f"{line}; kept {kept}; last step {when} ago: expired, so your next message re-sends "
            f"~{_tok(main[-1].get('context') or 0)} tokens without the cache discount (a fresh chat with a handover "
            "is cheaper)")


def block(a: dict, cwd: str = "") -> str:
    chat = str((a.get("meta") or {}).get("session_id") or "")
    lines = [guard_line(a.get("guard")), tips_line(chat), numbers_line(a), cache_line(a)]
    return "\n".join([HEAD, *("  " + x for x in lines)])


def short() -> str:
    """For `sessioncost guard` (no chat in hand): the guard is printed above it, so only the tips line."""
    return "  " + tips_line()
