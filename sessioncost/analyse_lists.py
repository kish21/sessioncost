"""The report's lists beside the turns: what each agent did, Cursor's cost range, the most-read files, and plain
text for the page."""
from __future__ import annotations

import re


from . import prices as pricemod
from .model import Call, Session, Usage

from .analyse_calls import Pricer, tok


DID = [("read", "read", "reads"), ("write", "file written", "files written"), ("edit", "edit", "edits"),
       ("search", "search", "searches"), ("web", "web lookup", "web lookups"), ("command", "command", "commands"),
       ("helper", "step with a helper", "steps with helpers"), ("question", "question to you", "questions to you")]


def agents(s: Session, by_obj: dict, total_usd: float) -> list[dict]:
    """Who did the work: the main agent, then each helper, with why it ran, what it did, what it cost and which
    fixes point at its calls. A helper that failed to start is listed too, with no calls."""
    fixes_of: dict[str, list[str]] = {}
    for f in s.fixes:
        for agent in dict.fromkeys(a for a, _ in f.calls):
            fixes_of.setdefault(agent, []).append(f.id)

    def row(name: str, why: str, kind: str, calls: list[Call], agent: str) -> dict:
        kinds: dict[str, int] = {}
        for a in (a for c in calls for a in c.actions):
            kinds[a.kind] = kinds.get(a.kind, 0) + 1
        did = [f"{kinds[k]} {one if kinds[k] == 1 else many}"
               for k, one, many in sorted((d for d in DID if kinds.get(d[0])), key=lambda d: -kinds[d[0]])]
        usd = sum(by_obj[id(c)]["usd"] for c in calls)
        return {"name": name, "why": why, "kind": kind, "calls": len(calls),
                "sent": sum(c.usage.context for c in calls), "output": sum(c.usage.output for c in calls),
                "usd": usd, "share": usd / total_usd if total_usd else 0.0,
                "turns": sorted({c.turn for c in calls}), "did": did[:4], "fixes": fixes_of.get(agent, [])}

    out = [row("Main agent", "your chat with it", "main", s.calls, "main")]
    for h in s.helpers:
        agent = h.calls[0].agent if h.calls else f"helper:{h.id}"
        woke = sorted({c.turn for c in h.calls} - {h.turn})
        why = (f"started by the helper “{h.parent}” in turn {h.turn}" if h.parent else f"started in turn {h.turn}") + (
            f", woken again in turn{'s' if len(woke) > 1 else ''} {', '.join(map(str, woke))}" if woke else "")
        out.append(dict(row(h.description or h.id, why, "reviewer" if h.agent_type == "reviewer" else "helper",
                            h.calls, agent), parent=h.parent))
    names = {h.description for h in s.helpers}
    for c in s.calls:                    # asked for, but never ran: no calls of its own to show
        for a in c.actions:
            if a.kind == "helper" and a.is_error and a.label.startswith("started a helper") and a.target not in names:
                names.add(a.target)
                out.append(dict(row(a.target or "a helper", f"asked for in turn {c.turn}; it did not start",
                                    "failed", [], ""), turns=[c.turn]))
    return out


REUSE_GAP_S = 300       # the low end of a Cursor range: a call this soon after the last one re-read its conversation


def cursor_range(s: Session, P: Pricer) -> dict | None:
    """Cursor records no cache reads, so its cost can only be a range: low if each call that came within 5 minutes of
    the one before re-read that call's conversation from the cache, high if nothing was re-read (the estimate as it
    stands). The usage export gives the real number."""
    low = 0.0
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        prev = None
        for c in calls:
            u, reuse = c.usage, 0
            if prev is not None and c.ts_first and (prev.ts_last or prev.ts_first) and \
                    (c.ts_first - (prev.ts_last or prev.ts_first)).total_seconds() <= REUSE_GAP_S:
                reuse = min(prev.usage.context, u.context)
            alt = Usage(fresh_input=u.context - reuse, cache_read=reuse, output=u.output, thinking=u.thinking) \
                if reuse else u
            low += sum(pricemod.cost(alt, P.rates(c)).values())
            prev = c
    high = sum(P.total(c) for c in s.all_calls)
    return {"low": low, "high": high} if low < high else None



def _base(path: str) -> str:
    return re.split(r"[\\/]", path.strip().rstrip("\\/"))[-1] if path else ""



TOP_FILES = 5


def top_files(s: Session) -> list[dict]:
    """The files that weighed most: what each read brought in, and what each write or edit put out, times the later
    calls of that thread that carried it. A compacted chat drops some of it, so this is the most each one cost."""
    by: dict[str, dict] = {}
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        for i, c in enumerate(calls):
            later = len(calls) - i - 1
            for a in c.actions:
                if a.kind not in ("read", "write", "edit") or not a.target:
                    continue
                f = a.target.splitlines()[0]
                t = tok(a.result_chars if a.kind == "read" else a.written_chars)
                d = by.setdefault(f, {"path": f, "tokens": 0, "reads": 0, "writes": 0})
                d["tokens"] += t * later
                d["reads" if a.kind == "read" else "writes"] += 1
    rows = sorted((d for d in by.values() if d["tokens"] > 0), key=lambda d: -d["tokens"])[:TOP_FILES]
    names = [_base(d["path"]) for d in rows]
    for d, n in zip(rows, names):        # the same name in two folders: add the folder
        parts = re.split(r"[\\/]", d.pop("path").strip())
        d["name"] = "/".join(parts[-2:]) if names.count(n) > 1 and len(parts) > 1 else n
    return rows


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
PATH_RE = re.compile(r"(?<![\w:/\\])(?:[A-Za-z]:[\\/]+|~[\\/]+|[\\/]+)(?:[^\s\\/\"'`()\[\]]+[\\/]+)+([^\s\\/\"'`()\[\]]*)")


def plain(text: str) -> str:
    """What a person reads: markdown links become their text, file paths become their last name
    ("[$architect](C:/Users/me/SKILL.md)" -> "$architect"). Also keeps folder names out of shared screenshots."""
    text = LINK_RE.sub(lambda m: m.group(1), text or "")
    text = re.sub(r"@\[([^\]]+)\]", lambda m: "@" + re.split(r"[\\/]", m.group(1).rstrip("\\/"))[-1], text)
    return PATH_RE.sub(lambda m: m.group(1) or "folder", text).strip()


def _folder(cwd: str) -> str:
    return cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if cwd else ""
