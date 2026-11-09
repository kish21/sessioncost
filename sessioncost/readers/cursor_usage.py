"""Cursor's usage export (dashboard > usage > export CSV): exact tokens per request, matched to the chat's calls."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from ..model import Call, Session, Usage
from .claude import ts



# ---- Cursor's usage export (dashboard > usage > export CSV): exact tokens per request ---------------------------

COLUMNS = {
    "date": ("date", "timestamp", "time", "created at"),
    "model": ("model",),
    "input_write": ("input (w/ cache write)", "input with cache write", "cache write", "input_with_cache_write"),
    "input_plain": ("input (w/o cache write)", "input without cache write", "input", "input tokens",
                    "input_without_cache_write"),
    "cache_read": ("cache read", "cache read tokens", "cache_read"),
    "output": ("output tokens", "output", "output_tokens"),
    "total": ("total tokens", "total", "total_tokens"),
    "cost": ("cost", "cost ($)", "cost (usd)", "usd"),
}


def _num(x) -> float | None:
    x = str(x or "").replace(",", "").replace("$", "").strip()
    try:
        return float(x)
    except ValueError:
        return None


def _when(x: str) -> datetime | None:
    x = str(x or "").strip().strip('"')
    w = ts(x)
    if w is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%b %d, %Y, %I:%M:%S %p",
                    "%b %d, %Y, %I:%M %p", "%Y-%m-%d %H:%M"):
            try:
                w = datetime.strptime(x, fmt)
                break
            except ValueError:
                continue
    if w is not None and w.tzinfo is None:
        w = w.astimezone()             # no zone in the file: it is the user's local time
    return w


def is_usage_csv(path: str | Path) -> bool:
    """Is this a Cursor usage export? Only its first line is read: a date column, a model column, and Cursor's own
    names for the input columns (another provider's usage file has other names)."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            head = [h.strip().lower() for h in next(csv.reader(f), [])]
    except (OSError, UnicodeDecodeError):
        return False
    return any(n in head for n in COLUMNS["date"]) and "model" in head and \
        any(n in head for n in ("input (w/ cache write)", "input (w/o cache write)", "input with cache write"))


def read_usage_csv(path: str | Path) -> list[dict]:
    """Rows of Cursor's usage export as {when, model, write, plain, cache_read, output, cost}."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    col = {}
    for k, names in COLUMNS.items():
        for n in names:
            if n in head:
                col[k] = head.index(n)
                break
    if "date" not in col or not ({"input_plain", "output", "total"} & col.keys()):
        raise ValueError(f"{Path(path).name}: not a Cursor usage export (need a date column and token columns; "
                         f"found {', '.join(rows[0])})")
    out = []
    for r in rows[1:]:
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else ""
        when = _when(get("date"))
        if when is None:
            continue
        write, plain = int(_num(get("input_write")) or 0), int(_num(get("input_plain")) or 0)
        read, output = int(_num(get("cache_read")) or 0), int(_num(get("output")) or 0)
        if not (write or plain or read or output):
            total = int(_num(get("total")) or 0)
            if not total:
                continue
            plain = total
        out.append({"when": when, "model": get("model").strip(), "write": write, "plain": plain,
                    "cache_read": read, "output": output, "cost": _num(get("cost"))})
    return out


def apply_usage_csv(s: Session, rows: list[dict], slack_s: float = 60.0) -> int:
    """Replace estimated tokens with Cursor's exact ones, turn by turn: each export row (one request) belongs to the
    turn whose time span holds it; the turn's exact tokens are spread over its calls, and those of the helpers that
    worked in it, in proportion to the estimates.
    Returns the number of export rows used. Rows of other chats running at the same time cannot be told apart."""
    used = 0
    cost = 0.0
    have_cost = False
    spans = []
    for i, t in enumerate(s.turns):
        if not t.calls:
            continue
        start = t.ts or t.calls[0].ts_first
        end = max((c.ts_last or c.ts_first for c in t.calls if c.ts_last or c.ts_first), default=start)
        if start and end:
            spans.append((t, start, end))
    owner: dict[int, list[dict]] = {}
    for r in rows:      # each request belongs to one turn: the one it falls in, else the nearest one before it
        near = [k for k, (_, start, end) in enumerate(spans)
                if start - _td(slack_s) <= r["when"] <= end + _td(slack_s)]
        if near:
            inside = [k for k in near if spans[k][1] <= r["when"]]
            owner.setdefault(inside[-1] if inside else near[0], []).append(r)
    for k, (t, start, end) in enumerate(spans):
        mine = owner.get(k, [])
        if not mine:
            continue
        used += len(mine)
        tot = {k: sum(r[k] for r in mine) for k in ("write", "plain", "cache_read", "output")}
        for r in mine:
            if r["cost"] is not None:
                cost += r["cost"]
                have_cost = True
        if mine[0]["model"]:
            for c in t.calls:
                c.model = mine[0]["model"]
        # the export has no chat or helper id: a helper working in this turn is in these rows too, so the turn's
        # exact tokens are shared with its helpers rather than added on top of their estimates
        _spread(t.calls + [c for h in s.helpers for c in h.calls if c.turn == t.n], tot)
    if used:
        s.tokens_exact = len(owner) == len(spans)      # every turn got its exact numbers
        s.notes = [n for n in s.notes if not n.startswith("Cursor does not keep token counts")]
        s.notes.append(f"Tokens from Cursor's usage export ({used} requests), spread over each turn's calls in "
                       "proportion to their size; the turn totals are exact.")
        if have_cost:
            s.own_cost_usd = cost
    return used


def _td(sec: float):
    from datetime import timedelta
    return timedelta(seconds=sec)


def _spread(calls: list[Call], tot: dict) -> None:
    def share(values: list[int], total: int) -> list[int]:
        w = sum(values) or len(values)
        parts = [int(total * (v if sum(values) else 1) / w) for v in values]
        if parts:
            parts[-1] += total - sum(parts)
        return parts

    ctx = [c.usage.context for c in calls]
    outs = [c.usage.output for c in calls]
    writes, plains, reads = share(ctx, tot["write"]), share(ctx, tot["plain"]), share(ctx, tot["cache_read"])
    for c, w, p, r, o in zip(calls, writes, plains, reads, share(outs, tot["output"])):
        think = min(c.usage.thinking or 0, o)
        c.usage = Usage(fresh_input=p, cache_write_5m=w, cache_read=r, output=o, thinking=think)
