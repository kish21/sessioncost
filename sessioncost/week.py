"""Weekly summary across every chat and project on this computer: the top three wastes, what they cost,
the fix for each, and how this week compares with the week before (including whether pasted rules and
applied fixes helped).

Read strictly from logs, instruction files, and config files on this computer; nothing is sent anywhere.

    sessioncost week                    terminal summary + ~/.sessioncost/reports/week.html
    sessioncost week --week 2026-W11    specific ISO week compared with the week before it
    sessioncost week --project my-app   filter to one project
    sessioncost week --json             structured JSON on stdout
    sessioncost week --html [FILE]      write HTML report only and print its path
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import fixer_store, locate, prices as pricemod, readers
from .analyse import analyse
from .summary_cache import SummaryCache
from .totals import _as_dt, _key_matches, _money, _norm_path, _project_name, _tok, home, load_prices

WEEK_PAGE = "week.html"
DEFAULT_DAYS = 7
TOP_N = 3

# Canonical human-readable names for built-in waste detectors when aggregating across many chats
# (individual chats often have session-specific titles like "3 files were read again without changing").
WASTE_LABELS: dict[str, str] = {
    "repeated-reads": "Re-reading files that did not change",
    "repeated-writes": "Rewriting whole files instead of editing",
    "error-loop": "Retrying a failing command",
    "test-rerun": "Re-running tests when nothing changed",
    "re-run": "Re-running commands when nothing changed",
    "confirm-turn": "Asking to confirm the next step",
    "long-chat": "Carrying on a long chat instead of starting fresh",
    "unused-tools": "Unused connected tools (MCP servers / connectors)",
    "unused-builtins": "Unused built-in tools",
    "unused-skills": "Unused skills loaded at start-up",
    "helper-polling": "Checking on a helper instead of waiting",
    "helper-rereads": "Helpers re-reading files the main agent already read",
    "helper-model": "Using a heavy model for helper agents",
    "model-choice": "Using a top-tier model for small mechanical steps",
    "big-results": "Large tool results flooding the chat",
    "stale-context": "Large tool results staying in the chat across later calls",
    "cache-break": "Prompt cache breaking mid-chat",
    "startup-bloat": "Bloated start-up instructions or always-on rules",
    "images": "Screenshots or images carried across calls",
    "long-output": "Long verbose replies or heavy hidden thinking",
    "skill-cost": "Heavy skill definitions loaded in the chat",
    "hook-output": "Verbose hook output added to every turn",
    "ignore-folders": "Reading generated or ignored folders",
    "large-files": "Reading huge files or lockfiles in full",
    "verbose-tests": "Verbose test output in the chat",
    "notes-file": "Re-reading large notes or plan files repeatedly",
}

# Self-contained 1-line fixes for non-rule detectors whose single-chat `how` text refers to UI elements ("above").
WASTE_FIXES: dict[str, str] = {
    "long-chat": 'Start a fresh chat when a task finishes or after a long break: say "write a handover", then "continue from handover" in a new chat.',
    "skill-cost": "Trim large skill files or split reference material into separate files loaded only when needed.",
    "model-choice": "Use a faster/cheaper default model for routine steps, and switch to a top-tier model at the start of complex design or debugging chats.",
    "helper-model": "Use a smaller, cheaper model for helper/subagent tasks that only search or summarize.",
    "unused-tools": "Disable unused MCP servers or connectors in this project so their tool schemas are not sent on every call (or run `sessioncost fix`).",
    "unused-builtins": "Disable unused built-in tools in project settings (or run `sessioncost fix`).",
    "unused-skills": "Remove or scope unused skills so their instructions are not loaded at start-up.",
    "cache-break": "Avoid switching models or editing top-level instructions mid-chat, and keep turns within the prompt cache TTL.",
    "startup-bloat": "Trim always-on instruction files (CLAUDE.md / AGENTS.md / rules) to concise, high-signal rules.",
}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _iso_week_bounds(spec: str) -> tuple[date, date]:
    """Parse 'YYYY-Www' into (monday, sunday)."""
    m = re.fullmatch(r"(\d{4})-?W(\d{2})", spec.strip(), re.I)
    if not m:
        raise ValueError(f"--week must be YYYY-Www (e.g. 2026-W11), not {spec!r}")
    year, wk = int(m.group(1)), int(m.group(2))
    try:
        monday = date.fromisocalendar(year, wk, 1)
    except ValueError:
        raise ValueError(f"invalid ISO week {spec!r}") from None
    return monday, monday + timedelta(days=6)


def _resolve_windows(today: date | None = None, days: int = DEFAULT_DAYS,
                     end: str | None = None, week_spec: str | None = None) -> tuple[date, date, date, date, str, str]:
    """Return (this_start, this_end, prev_start, prev_end, this_label, prev_label)."""
    if week_spec:
        this_start, this_end = _iso_week_bounds(week_spec)
        prev_start = this_start - timedelta(days=7)
        prev_end = this_start - timedelta(days=1)
        iso_y, iso_w, _ = this_start.isocalendar()
        p_y, p_w, _ = prev_start.isocalendar()
        return this_start, this_end, prev_start, prev_end, f"{iso_y}-W{iso_w:02d}", f"{p_y}-W{p_w:02d}"

    if days < 1:
        raise ValueError("--days must be at least 1")
    if end:
        try:
            ref_end = date.fromisoformat(end.strip())
        except ValueError:
            raise ValueError(f"--end must be YYYY-MM-DD, not {end!r}") from None
    else:
        ref_end = today or date.today()

    this_start = ref_end - timedelta(days=days - 1)
    this_end = ref_end
    prev_end = this_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return (
        this_start, this_end, prev_start, prev_end,
        f"{this_start.isoformat()} to {this_end.isoformat()}",
        f"{prev_start.isoformat()} to {prev_end.isoformat()}",
    )


def _load_applied_fixes() -> dict[str, list[dict]]:
    """Read applied fixes recorded by `sessioncost fix` across all projects under ~/.sessioncost/fixes/."""
    fixes_dir = fixer_store.base() / "fixes"
    by_id: dict[str, list[dict]] = {}
    if not fixes_dir.is_dir():
        return by_id
    for f in sorted(fixes_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        proj_path = str(data.get("project") or "")
        proj_name = _project_name(proj_path) if proj_path else f.stem
        for rec in data.get("applied") or []:
            fid = str(rec.get("id") or "").strip()
            if not fid:
                continue
            by_id.setdefault(fid, []).append({
                "id": fid,
                "title": str(rec.get("title") or WASTE_LABELS.get(fid, fid)),
                "at": str(rec.get("at") or ""),
                "project": proj_name,
                "project_path": proj_path,
            })
    return by_id


def _pct_change(cur: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return round(((cur - prev) / prev) * 100.0, 1)


def _waste_label(fid: str, fallback_title: str) -> str:
    return WASTE_LABELS.get(fid) or fallback_title or fid


def _fix_text_for(fid: str, how: str) -> str:
    if fid in WASTE_FIXES:
        return WASTE_FIXES[fid]
    rule = fixer_store.RULE_OF.get(fid, "")
    if rule:
        return rule
    if how:
        return " ".join(how.split())
    return "Run `sessioncost fix` to apply the recommended change."


def _empty_bucket(label: str, start: date, end: date) -> dict:
    return {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "chats": 0,
        "turns": 0,
        "calls": 0,
        "sent": 0,
        "output": 0,
        "usd": 0.0,
        "waste_tokens": 0,
        "waste_usd": 0.0,
        "projects": set(),
        "agents": {},
        "by_project": {},
        "wastes": {},
        "chat_rows": [],
    }


def _finalize_bucket(b: dict) -> dict:
    chats = b["chats"]
    usd = round(b["usd"], 6)
    waste_usd = round(b["waste_usd"], 6)
    waste_share = round((waste_usd / usd) * 100.0, 1) if usd > 0 else 0.0
    by_project = sorted(
        ({**r, "usd": round(r["usd"], 6), "waste_usd": round(r["waste_usd"], 6)} for r in b["by_project"].values()),
        key=lambda r: (-r["usd"], r["project"].lower()),
    )
    by_agent = sorted(
        ({**r, "usd": round(r["usd"], 6), "waste_usd": round(r["waste_usd"], 6)} for r in b["agents"].values()),
        key=lambda r: (-r["usd"], r["agent_label"].lower()),
    )
    return {
        "label": b["label"],
        "start": b["start"],
        "end": b["end"],
        "chats": chats,
        "projects": len(b["projects"]),
        "turns": b["turns"],
        "calls": b["calls"],
        "sent": b["sent"],
        "output": b["output"],
        "usd": usd,
        "waste_tokens": int(b["waste_tokens"]),
        "waste_usd": waste_usd,
        "waste_share_pct": waste_share,
        "waste_usd_per_chat": round(waste_usd / chats, 6) if chats else 0.0,
        "waste_tokens_per_chat": round(b["waste_tokens"] / chats) if chats else 0,
        "by_project": by_project,
        "by_agent": by_agent,
    }


def collect(sessions: list[tuple[datetime, object]] | None = None, prices: dict | None = None,
            project: str | None = None, days: int = DEFAULT_DAYS, end: str | None = None,
            week_spec: str | None = None, today: date | None = None,
            engines: list | None = None,
            pre_analysed: list[tuple[datetime, object, object, dict]] | None = None) -> dict:
    """Collect and compare this week's chats and wastes against the previous week across all projects."""
    this_start, this_end, prev_start, prev_end, this_label, prev_label = _resolve_windows(
        today=today, days=days, end=end, week_spec=week_spec,
    )
    P = prices if prices is not None else load_prices()
    found = sessions if sessions is not None else ([] if pre_analysed is not None else locate.all_sessions())

    this_b = _empty_bucket(this_label, this_start, this_end)
    prev_b = _empty_bucket(prev_label, prev_start, prev_end)
    # Track rules found in project instruction files across scanned sessions: fid -> {files: set, projects: set}
    rules_in_files: dict[str, dict] = {}
    skipped = 0
    prefilter_cutoff = prev_start - timedelta(days=2)
    cache = SummaryCache(P) if pre_analysed is None else None

    items = (
        [(stamp, path, s, a) for stamp, path, s, a in pre_analysed]
        if pre_analysed is not None
        else [(stamp, path, None, None) for stamp, path in found]
    )
    for stamp, path, s, a in items:
        # Fast pre-filter on file mtime / session stamp so ancient logs don't need full analysis
        stamp_dt = _as_dt(stamp)
        if stamp_dt is not None and stamp_dt.astimezone().date() < prefilter_cutoff:
            continue
        if a is None:
            try:
                assert cache is not None
                a = cache.get_or_analyse(path, P, mode="full", engines=engines)
            except Exception:
                skipped += 1
                continue

        meta = a["meta"]
        tot = a["totals"]
        when_dt = _as_dt(meta.get("ended")) or _as_dt(meta.get("started")) or stamp_dt
        if when_dt is None:
            skipped += 1
            continue
        chat_date = when_dt.astimezone().date()

        if this_start <= chat_date <= this_end:
            bucket = this_b
        elif prev_start <= chat_date <= prev_end:
            bucket = prev_b
        else:
            continue

        proj_path = str(meta.get("cwd") or (getattr(s, "cwd", "") if s is not None else "") or "").strip()
        proj_name = _project_name(proj_path)
        if project and not _key_matches(project, proj_path, proj_name):
            continue

        # Record which recommended rules are currently present in instruction files
        rcheck = (meta.get("rules_check") or {}).get("rules") or []
        for r in rcheck:
            if r.get("in_file"):
                for fid in r.get("ids") or []:
                    entry = rules_in_files.setdefault(fid, {"files": set(), "projects": set()})
                    entry["files"].add(str(r["in_file"]))
                    entry["projects"].add(proj_name)

        usd = float(tot.get("usd") or 0.0)
        sent = int(tot.get("sent") or 0)
        output = int(tot.get("output") or 0)
        turns = int(tot.get("turns") or 0)
        calls_n = int(tot.get("calls") or 0) + int(tot.get("helper_calls") or 0)
        saving = a.get("saving") or {}
        chat_waste_usd = float(saving.get("usd") or 0.0)
        chat_waste_tok = int(saving.get("tokens") or 0)
        agent_key = str(meta.get("agent") or "unknown")
        agent_label = str(meta.get("agent_label") or agent_key)
        sid = str(meta.get("session_id") or locate.session_id(path))

        bucket["chats"] += 1
        bucket["turns"] += turns
        bucket["calls"] += calls_n
        bucket["sent"] += sent
        bucket["output"] += output
        bucket["usd"] += usd
        bucket["waste_tokens"] += chat_waste_tok
        bucket["waste_usd"] += chat_waste_usd
        bucket["projects"].add(proj_name)

        pkey = _norm_path(proj_path) if proj_path else f"::{proj_name.lower()}"
        prec = bucket["by_project"].setdefault(pkey, {
            "project": proj_name, "project_path": proj_path,
            "chats": 0, "sent": 0, "usd": 0.0, "waste_tokens": 0, "waste_usd": 0.0,
        })
        prec["chats"] += 1
        prec["sent"] += sent
        prec["usd"] += usd
        prec["waste_tokens"] += chat_waste_tok
        prec["waste_usd"] += chat_waste_usd

        arec = bucket["agents"].setdefault(agent_key, {
            "agent": agent_key, "agent_label": agent_label,
            "chats": 0, "sent": 0, "usd": 0.0, "waste_tokens": 0, "waste_usd": 0.0,
        })
        arec["chats"] += 1
        arec["sent"] += sent
        arec["usd"] += usd
        arec["waste_tokens"] += chat_waste_tok
        arec["waste_usd"] += chat_waste_usd

        # Aggregate every detected fix in this chat
        seen_in_chat: set[str] = set()
        for f in a.get("fixes") or []:
            fid = str(f.get("id") or "").strip()
            if not fid:
                continue
            w = bucket["wastes"].setdefault(fid, {
                "id": fid,
                "title": _waste_label(fid, str(f.get("title") or "")),
                "chats": 0,
                "projects": set(),
                "saving_tokens": 0,
                "saving_usd": 0.0,
                "how": str(f.get("how") or ""),
                "paste": bool(f.get("paste")),
            })
            if fid not in seen_in_chat:
                w["chats"] += 1
                seen_in_chat.add(fid)
            w["projects"].add(proj_name)
            w["saving_tokens"] += int(f.get("saving_tokens") or 0)
            w["saving_usd"] += float(f.get("saving_usd") or 0.0)
            if not w["how"] and f.get("how"):
                w["how"] = str(f["how"])
            if f.get("paste"):
                w["paste"] = True

        bucket["chat_rows"].append({
            "session_id": sid,
            "date": chat_date.isoformat(),
            "project": proj_name,
            "agent": agent_key,
            "agent_label": agent_label,
            "usd": round(usd, 6),
            "waste_usd": round(chat_waste_usd, 6),
            "waste_tokens": chat_waste_tok,
        })

    if cache is not None:
        cache.save()

    applied_fixes = _load_applied_fixes()
    this_summary = _finalize_bucket(this_b)
    prev_summary = _finalize_bucket(prev_b)

    # Build full ranked list of wastes for this week, annotated with last-week delta & fix guidance
    all_wastes: list[dict] = []
    for fid, w in this_b["wastes"].items():
        pw = prev_b["wastes"].get(fid)
        prev_usd = round(float(pw["saving_usd"]), 6) if pw else 0.0
        prev_tok = int(pw["saving_tokens"]) if pw else 0
        prev_chats = int(pw["chats"]) if pw else 0
        cur_usd = round(float(w["saving_usd"]), 6)
        cur_tok = int(w["saving_tokens"])
        delta_usd = round(cur_usd - prev_usd, 6)
        delta_pct = _pct_change(cur_usd, prev_usd) if prev_usd > 0 else _pct_change(cur_tok, prev_tok)
        if prev_chats == 0:
            trend_status = "new"
        elif delta_usd < -0.005 or (abs(delta_usd) <= 0.005 and cur_tok < prev_tok):
            trend_status = "down"
        elif delta_usd > 0.005 or (abs(delta_usd) <= 0.005 and cur_tok > prev_tok):
            trend_status = "up"
        else:
            trend_status = "same"

        rif = rules_in_files.get(fid)
        app = applied_fixes.get(fid) or []
        all_wastes.append({
            "id": fid,
            "title": w["title"],
            "chats": w["chats"],
            "projects": sorted(w["projects"]),
            "saving_tokens": cur_tok,
            "saving_usd": cur_usd,
            "prev_chats": prev_chats,
            "prev_tokens": prev_tok,
            "prev_usd": prev_usd,
            "delta_usd": delta_usd,
            "delta_pct": delta_pct,
            "trend": trend_status,
            "fix": _fix_text_for(fid, w["how"]),
            "how": w["how"],
            "paste": w["paste"],
            "can_auto_fix": fid in fixer_store.RULE_OF or fid in ("unused-tools", "unused-builtins", "long-chat"),
            "rule_in_files": sorted(rif["files"]) if rif else [],
            "applied_count": len(app),
        })
    all_wastes.sort(key=lambda r: (-r["saving_usd"], -r["saving_tokens"], r["id"]))
    top_wastes = all_wastes[:TOP_N]

    # Evaluate whether pasted rules and applied fixes helped (compare this week vs last week)
    tracked_ids = sorted(set(rules_in_files) | set(applied_fixes))
    fixes_impact: list[dict] = []
    for fid in tracked_ids:
        cur_w = this_b["wastes"].get(fid)
        prev_w = prev_b["wastes"].get(fid)
        app = applied_fixes.get(fid) or []
        rif = rules_in_files.get(fid)
        # Only report a rule if it actually fired in either week OR was explicitly applied via `sessioncost fix`
        if not cur_w and not prev_w and not app:
            continue
        cur_usd = round(float(cur_w["saving_usd"]), 6) if cur_w else 0.0
        cur_tok = int(cur_w["saving_tokens"]) if cur_w else 0
        cur_chats = int(cur_w["chats"]) if cur_w else 0
        prev_usd = round(float(prev_w["saving_usd"]), 6) if prev_w else 0.0
        prev_tok = int(prev_w["saving_tokens"]) if prev_w else 0
        prev_chats = int(prev_w["chats"]) if prev_w else 0
        delta_usd = round(cur_usd - prev_usd, 6)
        delta_pct = _pct_change(cur_usd, prev_usd) if prev_usd > 0 else _pct_change(cur_tok, prev_tok)

        title = _waste_label(
            fid,
            (cur_w["title"] if cur_w else prev_w["title"] if prev_w else app[0]["title"] if app else fid),
        )
        sources = []
        if app:
            sources.append(f"applied via `sessioncost fix` ({len(app)} project{'s' if len(app) != 1 else ''})")
        if rif:
            sources.append("rule in " + ", ".join(sorted(rif["files"])[:2]))

        if prev_chats > 0 and cur_chats == 0:
            status = "helped"
            note = f"Gone this week (was {_money(prev_usd)} across {prev_chats} chat{'s' if prev_chats != 1 else ''} last week)."
        elif prev_chats > 0 and (cur_usd < prev_usd - 0.005 or (abs(cur_usd - prev_usd) <= 0.005 and cur_tok < prev_tok)):
            status = "helped"
            pct_str = f"{abs(delta_pct):.0f}%" if delta_pct is not None else "down"
            note = f"Down {pct_str} ({_money(prev_usd)} -> {_money(cur_usd)})."
        elif cur_chats == 0 and prev_chats == 0:
            status = "holding"
            note = "No waste of this kind this week."
        elif prev_chats == 0 and cur_chats > 0:
            status = "still_happening"
            note = f"Still fired in {cur_chats} chat{'s' if cur_chats != 1 else ''} ({_money(cur_usd)}) even with the rule in place."
        else:
            status = "still_happening"
            note = f"Did not drop ({_money(prev_usd)} -> {_money(cur_usd)}) despite the rule being in place."

        fixes_impact.append({
            "id": fid,
            "title": title,
            "status": status,
            "note": note,
            "source": "; ".join(sources),
            "prev_usd": prev_usd,
            "prev_tokens": prev_tok,
            "prev_chats": prev_chats,
            "this_usd": cur_usd,
            "this_tokens": cur_tok,
            "this_chats": cur_chats,
            "delta_usd": delta_usd,
            "delta_pct": delta_pct,
        })
    status_order = {"helped": 0, "still_happening": 1, "holding": 2}
    fixes_impact.sort(key=lambda r: (status_order.get(r["status"], 9), -(r["prev_usd"] + r["this_usd"]), r["id"]))

    # Also detect wastes that disappeared this week even without a tracked rule
    gone_wastes: list[dict] = []
    for fid, pw in prev_b["wastes"].items():
        if fid not in this_b["wastes"]:
            gone_wastes.append({
                "id": fid,
                "title": pw["title"],
                "prev_usd": round(float(pw["saving_usd"]), 6),
                "prev_tokens": int(pw["saving_tokens"]),
                "prev_chats": int(pw["chats"]),
            })
    gone_wastes.sort(key=lambda r: (-r["prev_usd"], -r["prev_tokens"]))

    # Week-over-week comparison & plain-English verdict
    usd_delta = round(this_summary["usd"] - prev_summary["usd"], 6)
    usd_delta_pct = _pct_change(this_summary["usd"], prev_summary["usd"])
    waste_delta = round(this_summary["waste_usd"] - prev_summary["waste_usd"], 6)
    waste_delta_pct = _pct_change(this_summary["waste_usd"], prev_summary["waste_usd"])
    waste_tok_delta = this_summary["waste_tokens"] - prev_summary["waste_tokens"]
    waste_tok_delta_pct = _pct_change(this_summary["waste_tokens"], prev_summary["waste_tokens"])
    share_delta_pp = round(this_summary["waste_share_pct"] - prev_summary["waste_share_pct"], 1)

    if this_summary["chats"] == 0 and prev_summary["chats"] == 0:
        verdict_tone = "neutral"
        verdict = "No chats recorded in the last two weeks."
    elif this_summary["chats"] == 0:
        verdict_tone = "neutral"
        verdict = f"No chats recorded this week (last week: {prev_summary['chats']} chats, {_money(prev_summary['usd'])})."
    elif prev_summary["chats"] == 0:
        verdict_tone = "neutral"
        verdict = (
            f"First week with recorded chats ({this_summary['chats']} chat{'s' if this_summary['chats'] != 1 else ''}, "
            f"{_money(this_summary['waste_usd'])} measured waste). Check back next week to see the trend."
        )
    elif this_summary["waste_usd"] == 0 and prev_summary["waste_usd"] == 0:
        verdict_tone = "ok"
        verdict = "Clean week: no built-in waste checks fired this week or last week."
    elif waste_delta < -0.005 or (abs(waste_delta) <= 0.005 and waste_tok_delta < 0):
        verdict_tone = "ok"
        pct = f" ({abs(waste_delta_pct):.0f}% less)" if waste_delta_pct is not None else ""
        verdict = (
            f"Waste dropped from {_money(prev_summary['waste_usd'])} to {_money(this_summary['waste_usd'])}{pct} "
            f"compared with last week ({prev_summary['waste_share_pct']:.0f}% -> {this_summary['waste_share_pct']:.0f}% of spend)."
        )
    elif waste_delta > 0.005 or (abs(waste_delta) <= 0.005 and waste_tok_delta > 0):
        verdict_tone = "warn"
        pct = f" (+{waste_delta_pct:.0f}%)" if waste_delta_pct is not None else ""
        verdict = (
            f"Waste rose from {_money(prev_summary['waste_usd'])} to {_money(this_summary['waste_usd'])}{pct} "
            f"compared with last week ({prev_summary['waste_share_pct']:.0f}% -> {this_summary['waste_share_pct']:.0f}% of spend)."
        )
    else:
        verdict_tone = "neutral"
        verdict = (
            f"Waste stayed about the same ({_money(this_summary['waste_usd'])} vs {_money(prev_summary['waste_usd'])} last week)."
        )

    price_own = list(P.get("own") or [])
    price_note = (f"custom/organisation prices ({', '.join(price_own)})" if price_own
                  else f"built-in price list (checked {pricemod.checked_day(P.get('as_of', ''))})")

    # Reusable trend array (oldest -> newest) for #28's Trend section
    trend = [
        {
            "period": "last_week",
            "label": prev_summary["label"],
            "start": prev_summary["start"],
            "end": prev_summary["end"],
            "chats": prev_summary["chats"],
            "sent": prev_summary["sent"],
            "usd": prev_summary["usd"],
            "waste_tokens": prev_summary["waste_tokens"],
            "waste_usd": prev_summary["waste_usd"],
            "waste_share_pct": prev_summary["waste_share_pct"],
        },
        {
            "period": "this_week",
            "label": this_summary["label"],
            "start": this_summary["start"],
            "end": this_summary["end"],
            "chats": this_summary["chats"],
            "sent": this_summary["sent"],
            "usd": this_summary["usd"],
            "waste_tokens": this_summary["waste_tokens"],
            "waste_usd": this_summary["waste_usd"],
            "waste_share_pct": this_summary["waste_share_pct"],
        },
    ]

    return {
        "this_week": this_summary,
        "last_week": prev_summary,
        "comparison": {
            "usd_delta": usd_delta,
            "usd_delta_pct": usd_delta_pct,
            "waste_usd_delta": waste_delta,
            "waste_usd_delta_pct": waste_delta_pct,
            "waste_tokens_delta": waste_tok_delta,
            "waste_tokens_delta_pct": waste_tok_delta_pct,
            "waste_share_delta_pp": share_delta_pp,
            "chats_delta": this_summary["chats"] - prev_summary["chats"],
            "verdict_tone": verdict_tone,
            "verdict": verdict,
        },
        "top_wastes": top_wastes,
        "all_wastes": all_wastes,
        "fixes_impact": fixes_impact,
        "gone_wastes": gone_wastes,
        "trend": trend,
        "meta": {
            "days": days,
            "project_filter": project or "",
            "skipped": skipped,
            "prices_source": price_note,
            "prices_stale": pricemod.stale_note(P),
        },
    }


def _fmt_delta_usd(delta: float, pct: float | None) -> str:
    sign = "+" if delta > 0 else "-" if delta < 0 else ""
    base = f"{sign}{_money(abs(delta))}"
    if pct is not None:
        return f"{base} ({'+' if pct > 0 else ''}{pct:.0f}%)"
    return base


def render_text(data: dict, html_path: Path | None = None) -> str:
    tw = data["this_week"]
    lw = data["last_week"]
    cmp = data["comparison"]
    meta = data["meta"]

    lines = [
        f"SessionCost weekly summary ({tw['label']})",
        f"This week: {_money(tw['usd'])} across {tw['chats']} chat{'s' if tw['chats'] != 1 else ''} "
        f"in {tw['projects']} project{'s' if tw['projects'] != 1 else ''} · "
        f"measured waste: {_money(tw['waste_usd'])} ({_tok(tw['waste_tokens'])} tokens, {tw['waste_share_pct']:.0f}% of spend)",
    ]
    if lw["chats"] > 0:
        lines.append(
            f"Last week ({lw['label']}): {_money(lw['usd'])} across {lw['chats']} chat{'s' if lw['chats'] != 1 else ''} · "
            f"waste: {_money(lw['waste_usd'])} ({_tok(lw['waste_tokens'])} tokens, {lw['waste_share_pct']:.0f}% of spend)"
        )
    lines.append(f"Trend: {cmp['verdict']}")

    if data["top_wastes"]:
        lines.append("\nTop wastes this week:")
        for idx, w in enumerate(data["top_wastes"], 1):
            if w["trend"] == "new":
                chg = "new this week"
            elif w["trend"] == "down":
                chg = f"down vs last week ({_money(w['prev_usd'])} -> {_money(w['saving_usd'])})"
            elif w["trend"] == "up":
                chg = f"up vs last week ({_money(w['prev_usd'])} -> {_money(w['saving_usd'])})"
            else:
                chg = f"same as last week ({_money(w['prev_usd'])})"
            projs = ", ".join(w["projects"][:3]) + ("…" if len(w["projects"]) > 3 else "")
            tok_part = f"{_tok(w['saving_tokens'])} tokens, " if w["saving_tokens"] > 0 else ""
            lines.append(
                f"  {idx}. {w['title']} — {_money(w['saving_usd'])} ({tok_part}"
                f"{w['chats']} chat{'s' if w['chats'] != 1 else ''}, {projs}) · {chg}"
            )
            lines.append(f"     Fix: {w['fix']}")
    elif tw["chats"] > 0:
        lines.append("\nTop wastes this week: none of the built-in waste checks fired.")

    if data["fixes_impact"]:
        lines.append("\nDid your fixes help?")
        for r in data["fixes_impact"][:6]:
            tag = "WORKING" if r["status"] == "helped" else "HOLDING" if r["status"] == "holding" else "STILL HAPPENING"
            lines.append(f"  [{tag}] {r['title']}: {r['note']}")
    elif data["gone_wastes"]:
        lines.append("\nWastes from last week that did not come back:")
        for g in data["gone_wastes"][:3]:
            lines.append(f"  - {g['title']} (was {_money(g['prev_usd'])} in {g['prev_chats']} chat{'s' if g['prev_chats'] != 1 else ''})")

    if meta["prices_stale"]:
        lines.append(f"\nNote: {meta['prices_stale']}")
    if meta["skipped"]:
        lines.append(f"Note: skipped {meta['skipped']} unreadable log{'s' if meta['skipped'] != 1 else ''}.")
    if html_path:
        lines.append(f"\nReport: {Path(html_path).as_posix()}")
    return "\n".join(lines)


def render_trend_html(data: dict) -> str:
    """Reusable HTML fragment comparing this week vs last week (also designed for the #28 insights Trend section)."""
    tw = data["this_week"]
    lw = data["last_week"]
    cmp = data["comparison"]
    peak_usd = max(tw["usd"], lw["usd"], 0.01)
    peak_waste = max(tw["waste_usd"], lw["waste_usd"], 0.01)

    def row(label: str, last_val: str, this_val: str, delta: str = "", cls: str = "") -> str:
        return (
            f'<tr><td>{_e(label)}</td><td class="n num">{_e(last_val)}</td>'
            f'<td class="n num">{_e(this_val)}</td><td class="n num {cls}">{_e(delta)}</td></tr>'
        )

    waste_cls = "ok" if cmp["waste_usd_delta"] < -0.005 else "warn" if cmp["waste_usd_delta"] > 0.005 else ""
    usd_delta_s = _fmt_delta_usd(cmp["usd_delta"], cmp["usd_delta_pct"]) if lw["chats"] else "—"
    waste_delta_s = _fmt_delta_usd(cmp["waste_usd_delta"], cmp["waste_usd_delta_pct"]) if lw["chats"] else "—"
    share_delta_s = (
        f"{'+' if cmp['waste_share_delta_pp'] > 0 else ''}{cmp['waste_share_delta_pp']:.1f} pp"
        if lw["chats"] else "—"
    )
    chats_delta = cmp["chats_delta"]
    chats_delta_s = f"{'+' if chats_delta > 0 else ''}{chats_delta}" if lw["chats"] else "—"

    rows = "".join([
        row("Total spend", _money(lw["usd"]), _money(tw["usd"]), usd_delta_s),
        row("Measured waste", _money(lw["waste_usd"]), _money(tw["waste_usd"]), waste_delta_s, waste_cls),
        row("Waste % of spend", f"{lw['waste_share_pct']:.1f}%", f"{tw['waste_share_pct']:.1f}%", share_delta_s, waste_cls),
        row("Waste per chat", _money(lw["waste_usd_per_chat"]), _money(tw["waste_usd_per_chat"]), ""),
        row("Chats", str(lw["chats"]), str(tw["chats"]), chats_delta_s),
        row("Tokens sent", _tok(lw["sent"]), _tok(tw["sent"]), ""),
    ])

    bars = (
        '<div class="tbars">'
        f'<div class="tbar"><span>Last week ({_e(lw["label"])})</span>'
        f'<div class="track"><i class="spend" style="width:{100 * lw["usd"] / peak_usd:.1f}%"></i>'
        f'<i class="waste" style="width:{100 * lw["waste_usd"] / peak_waste:.1f}%"></i></div>'
        f'<b class="num">{_e(_money(lw["usd"]))} total · {_e(_money(lw["waste_usd"]))} waste</b></div>'
        f'<div class="tbar"><span>This week ({_e(tw["label"])})</span>'
        f'<div class="track"><i class="spend" style="width:{100 * tw["usd"] / peak_usd:.1f}%"></i>'
        f'<i class="waste" style="width:{100 * tw["waste_usd"] / peak_waste:.1f}%"></i></div>'
        f'<b class="num">{_e(_money(tw["usd"]))} total · {_e(_money(tw["waste_usd"]))} waste</b></div>'
        '</div>'
    )

    return (
        f'<section id="trend"><h2>This week vs last week</h2>'
        f'<div class="verdict {cmp["verdict_tone"]}">{_e(cmp["verdict"])}</div>'
        f'{bars}'
        f'<div class="wrap"><table><thead><tr><th>Metric</th><th class="n">Last week ({_e(lw["label"])})</th>'
        f'<th class="n">This week ({_e(tw["label"])})</th><th class="n">Change</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div></section>'
    )


def render_html(data: dict) -> str:
    """Self-contained HTML weekly summary page (zero network requests)."""
    tw = data["this_week"]
    lw = data["last_week"]
    cmp = data["comparison"]
    meta = data["meta"]

    tile = lambda n, label, cls="": f'<div class="tile {cls}"><b class="num">{_e(n)}</b><span>{_e(label)}</span></div>'
    waste_cls = "ok" if cmp["waste_usd_delta"] < -0.005 else "warn" if cmp["waste_usd_delta"] > 0.005 else ""
    helped_n = sum(1 for r in data["fixes_impact"] if r["status"] == "helped")

    waste_cards = []
    for idx, w in enumerate(data["top_wastes"], 1):
        badge_cls = "ok" if w["trend"] == "down" else "warn" if w["trend"] in ("up", "new") else "muted"
        if w["trend"] == "new":
            badge_txt = "New this week"
        elif w["trend"] == "down":
            badge_txt = f"Down from {_money(w['prev_usd'])}"
        elif w["trend"] == "up":
            badge_txt = f"Up from {_money(w['prev_usd'])}"
        else:
            badge_txt = "Unchanged"
        projs = ", ".join(w["projects"][:4]) + ("…" if len(w["projects"]) > 4 else "")
        rule_note = (
            f'<div class="muted">Rule already in: {_e(", ".join(w["rule_in_files"]))}</div>'
            if w["rule_in_files"] else ""
        )
        tok_html = f'<span class="num">{_e(_tok(w["saving_tokens"]))} tokens</span> · ' if w["saving_tokens"] > 0 else ""
        waste_cards.append(
            f'<div class="card"><div class="card-head">'
            f'<h3>#{idx} {_e(w["title"])}</h3>'
            f'<span class="pill {badge_cls}">{_e(badge_txt)}</span></div>'
            f'<div class="meta-line"><b class="num">{_e(_money(w["saving_usd"]))}</b> · '
            f'{tok_html}'
            f'{w["chats"]} chat{"s" if w["chats"] != 1 else ""} · {_e(projs)}</div>'
            f'<div class="fix-box"><b>Fix:</b> <code>{_e(w["fix"])}</code></div>'
            f'{rule_note}</div>'
        )
    top_wastes_html = (
        "".join(waste_cards) if waste_cards
        else '<div class="card muted">No built-in waste checks fired this week.</div>'
    )

    if data["fixes_impact"]:
        impact_rows = "".join(
            f'<tr><td>{_e(r["title"])}<div class="muted">{_e(r["source"])}</div></td>'
            f'<td class="n num">{_e(_money(r["prev_usd"]))}</td>'
            f'<td class="n num">{_e(_money(r["this_usd"]))}</td>'
            f'<td><span class="pill {"ok" if r["status"] in ("helped", "holding") else "warn"}">'
            f'{"Working" if r["status"] == "helped" else "Holding" if r["status"] == "holding" else "Still happening"}'
            f'</span> {_e(r["note"])}</td></tr>'
            for r in data["fixes_impact"]
        )
        impact_section = (
            '<h2>Did your pasted rules &amp; fixes help?</h2>'
            '<div class="wrap"><table><thead><tr><th>Rule / fix</th><th class="n">Last week</th>'
            '<th class="n">This week</th><th>Result</th></tr></thead>'
            f'<tbody>{impact_rows}</tbody></table></div>'
        )
    else:
        impact_section = (
            '<h2>Did your pasted rules &amp; fixes help?</h2>'
            '<div class="card muted">No pasted SessionCost rules or applied fixes were detected yet. '
            'Run <code>sessioncost fix</code> in a project to apply a fix, then check back next week.</div>'
        )

    proj_rows = "".join(
        f'<tr><td title="{_e(r["project_path"])}">{_e(r["project"])}</td>'
        f'<td class="n num">{r["chats"]}</td><td class="n num">{_e(_tok(r["sent"]))}</td>'
        f'<td class="n num">{_e(_money(r["usd"]))}</td><td class="n num">{_e(_money(r["waste_usd"]))}</td></tr>'
        for r in tw["by_project"]
    )
    agent_rows = "".join(
        f'<tr><td>{_e(r["agent_label"])}</td><td class="n num">{r["chats"]}</td>'
        f'<td class="n num">{_e(_tok(r["sent"]))}</td><td class="n num">{_e(_money(r["usd"]))}</td>'
        f'<td class="n num">{_e(_money(r["waste_usd"]))}</td></tr>'
        for r in tw["by_agent"]
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<title>SessionCost weekly summary</title><style>
:root{{--bg:oklch(0.17 0.012 280);--surface:oklch(0.21 0.014 280);--ink:oklch(0.96 0.004 280);--ink-2:oklch(0.72 0.012 280);
--line:oklch(0.30 0.012 280);--bad:oklch(0.70 0.19 20);--warn:oklch(0.78 0.14 75);--ok:oklch(0.72 0.13 155);--bar:oklch(0.62 0.17 265);color-scheme:dark}}
@media (prefers-color-scheme: light){{:root{{--bg:oklch(0.945 0.008 265);--surface:oklch(1 0 0);--ink:oklch(0.24 0.02 260);
--ink-2:oklch(0.45 0.015 260);--line:oklch(0.88 0.008 265);--bad:oklch(0.53 0.20 25);--warn:oklch(0.55 0.13 70);--ok:oklch(0.51 0.13 155);--bar:oklch(0.52 0.17 265);color-scheme:light}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:28px 16px 60px}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:17px;margin:30px 0 10px}}h3{{font-size:15px;margin:0}}
.muted{{color:var(--ink-2);font-size:12.5px}}.num{{font-variant-numeric:tabular-nums;font-family:Consolas,ui-monospace,monospace}}
code{{font-family:Consolas,ui-monospace,monospace;font-size:12.5px;overflow-wrap:anywhere}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:16px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}.tile b{{display:block;font-size:24px}}
.tile span{{color:var(--ink-2);font-size:13px}}.tile.warn b{{color:var(--warn)}}.tile.ok b{{color:var(--ok)}}
.verdict{{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--bar);border-radius:10px;padding:12px 14px;margin:12px 0}}
.verdict.ok{{border-left-color:var(--ok)}}.verdict.warn{{border-left-color:var(--warn)}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px}}
.card-head{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}}
.meta-line{{margin:6px 0 10px;font-size:13.5px;color:var(--ink-2)}}.meta-line b{{color:var(--ink)}}
.fix-box{{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid var(--line)}}
.pill.ok{{color:var(--ok);border-color:var(--ok)}}.pill.warn{{color:var(--warn);border-color:var(--warn)}}
.wrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--ink-2);font-weight:600;font-size:12.5px;white-space:nowrap}}td.n,th.n{{text-align:right}}tr:last-child td{{border-bottom:0}}
.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
.tbars{{display:grid;gap:8px;margin:10px 0 14px}}.tbar{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:13px}}
.tbar .track{{display:flex;flex-direction:column;gap:4px;margin:6px 0}}.tbar i{{display:block;height:8px;border-radius:4px}}
.tbar i.spend{{background:var(--bar)}}.tbar i.waste{{background:var(--warn)}}
</style></head><body><main>
<h1>SessionCost weekly summary</h1>
<p>All coding-agent chats on this computer for <b>{_e(tw["label"])}</b>, compared with <b>{_e(lw["label"])}</b>.
Built locally from your logs; nothing was sent anywhere.</p>
<div class="tiles">{tile(_money(tw["usd"]), "spent this week")}{tile(_money(tw["waste_usd"]), f"measured waste ({tw['waste_share_pct']:.0f}% of spend)", waste_cls)}{tile(tw["chats"], f"chats in {tw['projects']} projects")}{tile(helped_n, "rules/fixes helping", "ok" if helped_n else "")}</div>
<p class="muted">Prices: {_e(meta["prices_source"])}.{f" {_e(meta['prices_stale'])}" if meta["prices_stale"] else ""}
Run <code>sessioncost fix</code> in a project to apply the top fixes automatically.</p>
<h2>Top {TOP_N} wastes this week</h2>
{top_wastes_html}
{impact_section}
{render_trend_html(data)}
<div class="cols"><div><h2>By project (this week)</h2><div class="wrap"><table><thead><tr><th>Project</th><th class="n">Chats</th><th class="n">Sent</th><th class="n">Spend</th><th class="n">Waste</th></tr></thead>
<tbody>{proj_rows or '<tr><td colspan="5" class="muted">No chats this week.</td></tr>'}</tbody></table></div></div>
<div><h2>By agent (this week)</h2><div class="wrap"><table><thead><tr><th>Agent</th><th class="n">Chats</th><th class="n">Sent</th><th class="n">Spend</th><th class="n">Waste</th></tr></thead>
<tbody>{agent_rows or '<tr><td colspan="5" class="muted">No chats this week.</td></tr>'}</tbody></table></div></div></div>
</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sessioncost week",
        description="Weekly summary across all chats and projects on this computer: top 3 wastes, what they cost, "
                    "the fix for each, and how this week compares with the week before. Local only.",
    )
    ap.add_argument("--project", help="filter to one project (name or folder path)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="days per period (default: 7)")
    ap.add_argument("--end", metavar="YYYY-MM-DD", help="end date of this week (default: today)")
    ap.add_argument("--week", dest="week_spec", metavar="YYYY-Www",
                    help="summarize a specific ISO week (e.g. 2026-W11) vs the week before it")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in/organisation prices")
    ap.add_argument("--json", action="store_true", help="print the weekly summary as JSON to stdout")
    ap.add_argument("--html", nargs="?", const="default", metavar="FILE",
                    help="write the HTML weekly summary page (default: ~/.sessioncost/reports/week.html) and print its path")
    ap.add_argument("--no-html", action="store_true", help="do not write ~/.sessioncost/reports/week.html")
    ap.add_argument("--open", action="store_true", help="open the HTML weekly summary in the browser")
    ap.add_argument("--no-open", dest="open", action="store_false", help=argparse.SUPPRESS)
    try:
        args = ap.parse_args(list(argv or []))
    except SystemExit as e:
        return int(e.code or 2)

    try:
        P = load_prices(args.prices)
        data = collect(prices=P, project=args.project, days=args.days, end=args.end, week_spec=args.week_spec)
    except ValueError as e:
        print(f"sessioncost week: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
        return 0

    html_out: Path | None = None
    if args.html is not None:
        html_out = (home() / "reports" / WEEK_PAGE) if args.html == "default" else Path(args.html).expanduser()
        html_out.parent.mkdir(parents=True, exist_ok=True)
        html_out.write_text(render_html(data), encoding="utf-8", errors="replace")
        print(f"SessionCost weekly summary report: {html_out.as_posix()}")
        if args.open:
            from .opener import open_report
            open_report(html_out)
        return 0

    if not args.no_html:
        try:
            html_out = home() / "reports" / WEEK_PAGE
            html_out.parent.mkdir(parents=True, exist_ok=True)
            html_out.write_text(render_html(data), encoding="utf-8", errors="replace")
        except OSError:
            html_out = None

    print(render_text(data, html_path=html_out))
    if args.open and html_out:
        from .opener import open_report
        open_report(html_out)
    return 0
