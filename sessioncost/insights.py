"""Insights across all sessions and agents on this computer: Claude Code, Codex, Cursor, Antigravity, and Copilot
side by side, with tokens and cost, measured waste, live-guard safety, ranked recommendations, and week-by-week
trends. Every number links down to its sessions.

Read strictly from logs, instruction files, guard audit logs, and config files on this computer; nothing is sent
anywhere, and SessionCost uses no AI model (your own agent can write a narrative from the JSON or terminal output).

    sessioncost insights                        terminal summary + ~/.sessioncost/reports/insights.html
    sessioncost insights --project my-app       filter to one project
    sessioncost insights --agent claude         filter to one agent
    sessioncost insights --days 30              last 30 days only
    sessioncost insights --since 2026-10-01     sessions on or after a date
    sessioncost insights --json                 structured JSON on stdout (for agent narrative or scripts)
    sessioncost insights --html [FILE]          write HTML insights page only and print its path
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import math
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import fixer_store, guard_report, locate, prices as pricemod, readers, week
from .analyse import analyse
from .guard import WHY as GUARD_WHY
from .totals import (
    _as_dt,
    _iso_week,
    _key_matches,
    _money,
    _norm_path,
    _parse_since,
    _project_name,
    _tok,
    home,
    load_prices,
)

INSIGHTS_PAGE = "insights.html"
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|\n]\s*)git\s+(?:-[Cc]\s+\S+\s+)*commit\b")

AGENT_ALIASES: dict[str, str] = {
    "claude": "claude",
    "claude-code": "claude",
    "claudecode": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "antigravity": "antigravity",
    "copilot": "copilot",
    "copilot-cli": "copilot",
}

AGENT_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "antigravity": "Antigravity",
    "copilot": "Copilot",
}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _safe_anchor(sid: str) -> str:
    return "session-" + re.sub(r"[^A-Za-z0-9_-]", "_", str(sid or "unknown"))[:64]


def _safe_report_file(rep_dir: Path, sid: str) -> str:
    """Return '<sid>.html' only when sid is a safe single filename and resolves strictly inside rep_dir."""
    if not sid or sid.startswith(".") or not re.fullmatch(r"[A-Za-z0-9._-]+", sid):
        return ""
    candidate = rep_dir / f"{sid}.html"
    try:
        if not candidate.is_file():
            return ""
        if candidate.resolve().parent != rep_dir.resolve():
            return ""
    except OSError:
        return ""
    return f"{sid}.html"


def _hours_str(minutes: float) -> str:
    m = float(minutes or 0.0)
    if m >= 60.0:
        return f"{m / 60.0:.1f}h"
    if m >= 1.0:
        return f"{m:.0f}m"
    return "0m"


def _normalize_agent(spec: str | None) -> str | None:
    if not spec:
        return None
    key = spec.strip().lower().replace(" ", "-")
    return AGENT_ALIASES.get(key, key)


def _session_tool_and_commit_counts(s) -> tuple[int, int]:
    """Count total tool calls and non-error git commit commands in a Session."""
    calls = [c for t in getattr(s, "turns", ()) for c in getattr(t, "calls", ())]
    for h in getattr(s, "helpers", ()):
        calls.extend(getattr(h, "calls", ()))
    tools = 0
    commits = 0
    for c in calls:
        for act in getattr(c, "actions", ()):
            tools += 1
            if getattr(act, "kind", "") == "command" and not getattr(act, "is_error", False):
                target = str(getattr(act, "target", "") or "")
                if _GIT_COMMIT_RE.search(target):
                    commits += 1
    return tools, commits


def _collect_safety(guard_folder: Path | None = None, project: str | None = None,
                    agent: str | None = None, cutoff: date | None = None) -> dict:
    """Collect live-guard safety metrics, optionally filtered by project/agent/cutoff."""
    try:
        raw = guard_report.collect(guard_folder)
    except OSError:
        raw = {"chats": [], "steps": [], "total": {}, "intact": 0, "kinds": {"blocked": [], "asked": []},
               "days": [], "by_agent": [], "by_project": []}

    chats = []
    for c in raw.get("chats") or []:
        if project and not _key_matches(project, c.get("cwd", ""), c.get("project", "")):
            continue
        if agent and _normalize_agent(c.get("via")) != agent:
            continue
        if cutoff:
            ts_str = str(c.get("last") or c.get("first") or "")[:10]
            try:
                if ts_str and date.fromisoformat(ts_str) < cutoff:
                    continue
            except ValueError:
                pass
        chats.append(c)

    chat_ids = {c["id"] for c in chats}
    steps = [s for s in (raw.get("steps") or []) if s.get("chat") in chat_ids]

    checked = sum(int(c.get("checked") or 0) for c in chats)
    blocked = sum(int(c.get("blocked") or 0) for c in chats)
    asked = sum(int(c.get("asked") or 0) for c in chats)
    allowed = sum(int(c.get("allowed") or 0) for c in chats)
    injections = sum(int(c.get("injections") or 0) for c in chats)
    intact = sum(1 for c in chats if c.get("intact"))
    broken = len(chats) - intact

    reason_blocked: collections.Counter[str] = collections.Counter()
    reason_asked: collections.Counter[str] = collections.Counter()
    day_counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"blocked": 0, "asked": 0})
    week_counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"blocked": 0, "asked": 0})

    for st in steps:
        dec = st.get("decision")
        marks = st.get("marks") or ["other"]
        if dec == "blocked":
            reason_blocked.update(marks)
        elif dec == "asked":
            reason_asked.update(marks)
        day_str = str(st.get("ts") or "")[:10]
        if day_str:
            if dec in ("blocked", "asked"):
                day_counts[day_str][dec] += 1
            try:
                wk, _ = _iso_week(date.fromisoformat(day_str))
                if dec in ("blocked", "asked"):
                    week_counts[wk][dec] += 1
            except ValueError:
                pass

    all_reasons = sorted(
        set(reason_blocked) | set(reason_asked),
        key=lambda k: -(reason_blocked.get(k, 0) * 10 + reason_asked.get(k, 0)),
    )
    reasons = [
        {
            "key": k,
            "label": GUARD_WHY.get(k, k),
            "blocked": reason_blocked.get(k, 0),
            "asked": reason_asked.get(k, 0),
        }
        for k in all_reasons
    ]

    by_chat = {c["id"]: c for c in chats}
    return {
        "available": bool(chats),
        "chats": len(chats),
        "checked": checked,
        "blocked": blocked,
        "asked": asked,
        "allowed": allowed,
        "injections": injections,
        "intact": intact,
        "broken": broken,
        "intact_ok": broken == 0,
        "reasons": reasons,
        "recent_steps": steps[:50],
        "guarded_chats": chats[:100],
        "by_day": {d: dict(v) for d, v in day_counts.items()},
        "by_week": {w: dict(v) for w, v in week_counts.items()},
        "_by_chat": by_chat,
    }


def collect(
    sessions: list[tuple[datetime, object]] | None = None,
    prices: dict | None = None,
    project: str | None = None,
    agent: str | None = None,
    since: str | None = None,
    days: int | None = None,
    today: date | None = None,
    guard_folder: Path | None = None,
    reports_dir: Path | None = None,
    engines: list | None = None,
) -> dict:
    """Collect cross-session, cross-agent insights strictly from local logs on this computer."""
    ref_today = today or date.today()
    cutoff = _parse_since(since, days, ref_today)
    agent_filter = _normalize_agent(agent)

    P = prices if prices is not None else load_prices()
    found = sessions if sessions is not None else locate.all_sessions()
    rep_dir = reports_dir if reports_dir is not None else (home() / "reports")

    safety = _collect_safety(guard_folder=guard_folder, project=project, agent=agent_filter, cutoff=cutoff)
    guard_by_chat = safety.pop("_by_chat", {})

    session_rows: list[dict] = []
    matched_for_week: list[tuple[datetime, object]] = []
    project_agg: dict[str, dict] = {}
    agent_agg: dict[str, dict] = {}
    day_agg: dict[str, dict] = {}
    week_agg: dict[str, dict] = {}
    waste_agg: dict[str, dict] = {}
    rules_in_files: dict[str, dict] = {}
    skipped = 0

    for stamp, path in found:
        if cutoff and stamp is not None:
            stamp_dt = _as_dt(stamp)
            if stamp_dt is not None and (stamp_dt.astimezone().date() + timedelta(days=1)) < cutoff:
                continue
        try:
            s = readers.read(path)
            a = analyse(s, P, engines=engines)
        except Exception:
            skipped += 1
            continue

        meta = a["meta"]
        tot = a["totals"]
        when_dt = _as_dt(meta.get("ended")) or _as_dt(meta.get("started")) or _as_dt(stamp)
        if when_dt is None:
            skipped += 1
            continue
        chat_date = when_dt.astimezone().date()
        if cutoff and chat_date < cutoff:
            continue

        proj_path = str(meta.get("cwd") or s.cwd or "").strip()
        proj_name = _project_name(proj_path)
        if project and not _key_matches(project, proj_path, proj_name):
            continue

        agent_key = _normalize_agent(str(meta.get("agent") or "unknown")) or "unknown"
        agent_label = str(meta.get("agent_label") or AGENT_LABELS.get(agent_key, agent_key))
        if agent_filter and agent_key != agent_filter:
            continue

        matched_for_week.append((when_dt, path, s, a))

        # Track rules found in project instruction files
        for r in (meta.get("rules_check") or {}).get("rules") or []:
            if r.get("in_file"):
                for fid in r.get("ids") or []:
                    entry = rules_in_files.setdefault(fid, {"files": set(), "projects": set()})
                    entry["files"].add(str(r["in_file"]))
                    entry["projects"].add(proj_name)

        sid = str(meta.get("session_id") or locate.session_id(path))
        short_id = sid[:8]
        title = str(meta.get("title") or sid)
        usd = float(tot.get("usd") or 0.0)
        sent = int(tot.get("sent") or 0)
        output = int(tot.get("output") or 0)
        turns = int(tot.get("turns") or 0)
        calls_n = int(tot.get("calls") or 0) + int(tot.get("helper_calls") or 0)
        working_min = float(tot.get("working_min") or 0.0)
        waiting_min = float(tot.get("waiting_min") or 0.0)
        tools_n, commits_n = _session_tool_and_commit_counts(s)
        saving = a.get("saving") or {}
        waste_usd = float(saving.get("usd") or 0.0)
        waste_tok = int(saving.get("tokens") or 0)
        week_key, week_start = _iso_week(chat_date)
        day_key = chat_date.isoformat()

        report_file = _safe_report_file(rep_dir, sid)

        g_rec = guard_by_chat.get(sid)
        g_info = {
            "guarded": bool(g_rec),
            "blocked": int(g_rec.get("blocked") or 0) if g_rec else 0,
            "asked": int(g_rec.get("asked") or 0) if g_rec else 0,
            "intact": bool(g_rec.get("intact", True)) if g_rec else True,
            "status": str(g_rec.get("status") or "") if g_rec else "",
        }

        fix_ids: list[str] = []
        session_fixes: list[dict] = []
        seen_in_chat: set[str] = set()
        for f in a.get("fixes") or []:
            fid = str(f.get("id") or "").strip()
            if not fid:
                continue
            f_tok = int(f.get("saving_tokens") or 0)
            f_usd = float(f.get("saving_usd") or 0.0)
            session_fixes.append({"id": fid, "tok": f_tok, "usd": round(f_usd, 4)})
            w = waste_agg.setdefault(fid, {
                "id": fid,
                "title": week._waste_label(fid, str(f.get("title") or "")),
                "chats": 0,
                "projects": set(),
                "agents": set(),
                "saving_tokens": 0,
                "saving_usd": 0.0,
                "how": str(f.get("how") or ""),
                "session_ids": [],
            })
            if fid not in seen_in_chat:
                w["chats"] += 1
                w["session_ids"].append(sid)
                fix_ids.append(fid)
                seen_in_chat.add(fid)
            w["projects"].add(proj_name)
            w["agents"].add(agent_label)
            w["saving_tokens"] += f_tok
            w["saving_usd"] += f_usd
            if not w["how"] and f.get("how"):
                w["how"] = str(f["how"])

        session_rows.append({
            "session_id": sid,
            "short_id": short_id,
            "anchor": _safe_anchor(sid),
            "title": title,
            "when": when_dt.isoformat(),
            "date": day_key,
            "week": week_key,
            "project": proj_name,
            "project_path": proj_path,
            "agent": agent_key,
            "agent_label": agent_label,
            "model": str(meta.get("model") or "unknown"),
            "turns": turns,
            "calls": calls_n,
            "tools": tools_n,
            "commits": commits_n,
            "working_min": round(working_min, 2),
            "waiting_min": round(waiting_min, 2),
            "sent": sent,
            "output": output,
            "usd": round(usd, 6),
            "waste_tokens": waste_tok,
            "waste_usd": round(waste_usd, 6),
            "fixes": fix_ids,
            "fix_details": session_fixes,
            "report_file": report_file,
            "guard": g_info,
        })

        # Aggregate by project
        pkey = _norm_path(proj_path) if proj_path else f"::{proj_name.lower()}"
        prec = project_agg.setdefault(pkey, {
            "project": proj_name, "project_path": proj_path, "agents": set(),
            "chats": 0, "turns": 0, "calls": 0, "tools": 0, "commits": 0,
            "working_min": 0.0, "sent": 0, "output": 0, "usd": 0.0,
            "waste_tokens": 0, "waste_usd": 0.0, "session_ids": [],
        })
        prec["agents"].add(agent_label)
        prec["chats"] += 1
        prec["turns"] += turns
        prec["calls"] += calls_n
        prec["tools"] += tools_n
        prec["commits"] += commits_n
        prec["working_min"] += working_min
        prec["sent"] += sent
        prec["output"] += output
        prec["usd"] += usd
        prec["waste_tokens"] += waste_tok
        prec["waste_usd"] += waste_usd
        prec["session_ids"].append(sid)

        # Aggregate by agent
        arec = agent_agg.setdefault(agent_key, {
            "agent": agent_key, "agent_label": agent_label, "projects": set(),
            "chats": 0, "turns": 0, "calls": 0, "tools": 0, "commits": 0,
            "working_min": 0.0, "sent": 0, "output": 0, "usd": 0.0,
            "waste_tokens": 0, "waste_usd": 0.0, "session_ids": [],
        })
        arec["projects"].add(proj_name)
        arec["chats"] += 1
        arec["turns"] += turns
        arec["calls"] += calls_n
        arec["tools"] += tools_n
        arec["commits"] += commits_n
        arec["working_min"] += working_min
        arec["sent"] += sent
        arec["output"] += output
        arec["usd"] += usd
        arec["waste_tokens"] += waste_tok
        arec["waste_usd"] += waste_usd
        arec["session_ids"].append(sid)

        # Aggregate by day
        drec = day_agg.setdefault(day_key, {
            "date": day_key, "chats": 0, "sent": 0, "usd": 0.0, "waste_usd": 0.0,
            "blocked": 0, "asked": 0, "session_ids": [],
        })
        drec["chats"] += 1
        drec["sent"] += sent
        drec["usd"] += usd
        drec["waste_usd"] += waste_usd
        drec["session_ids"].append(sid)

        # Aggregate by ISO week
        wrec = week_agg.setdefault(week_key, {
            "week": week_key, "week_start": week_start, "chats": 0, "sent": 0,
            "usd": 0.0, "waste_tokens": 0, "waste_usd": 0.0, "blocked": 0, "asked": 0,
            "session_ids": [],
        })
        wrec["chats"] += 1
        wrec["sent"] += sent
        wrec["usd"] += usd
        wrec["waste_tokens"] += waste_tok
        wrec["waste_usd"] += waste_usd
        wrec["session_ids"].append(sid)

    # Sort sessions newest first
    session_rows.sort(key=lambda r: r["when"], reverse=True)
    session_order = {r["session_id"]: i for i, r in enumerate(session_rows)}

    def _sort_sids(sids: list[str]) -> list[str]:
        return sorted(dict.fromkeys(sids), key=lambda sid: session_order.get(sid, 10**9))

    # Finalize by_project
    by_project: list[dict] = []
    for rec in project_agg.values():
        usd = round(rec["usd"], 6)
        wusd = round(rec["waste_usd"], 6)
        by_project.append({
            "project": rec["project"],
            "project_path": rec["project_path"],
            "agents": sorted(rec["agents"]),
            "chats": rec["chats"],
            "turns": rec["turns"],
            "calls": rec["calls"],
            "tools": rec["tools"],
            "commits": rec["commits"],
            "working_min": round(rec["working_min"], 2),
            "hours": round(rec["working_min"] / 60.0, 2),
            "sent": rec["sent"],
            "output": rec["output"],
            "usd": usd,
            "waste_tokens": int(rec["waste_tokens"]),
            "waste_usd": wusd,
            "waste_share_pct": round((wusd / usd) * 100.0, 1) if usd > 0 else 0.0,
            "session_ids": _sort_sids(rec["session_ids"]),
        })
    by_project.sort(key=lambda r: (-r["usd"], r["project"].lower()))

    # Finalize by_agent
    by_agent: list[dict] = []
    for rec in agent_agg.values():
        usd = round(rec["usd"], 6)
        wusd = round(rec["waste_usd"], 6)
        chats = rec["chats"]
        by_agent.append({
            "agent": rec["agent"],
            "agent_label": rec["agent_label"],
            "projects": len(rec["projects"]),
            "chats": chats,
            "turns": rec["turns"],
            "calls": rec["calls"],
            "tools": rec["tools"],
            "commits": rec["commits"],
            "working_min": round(rec["working_min"], 2),
            "hours": round(rec["working_min"] / 60.0, 2),
            "sent": rec["sent"],
            "output": rec["output"],
            "usd": usd,
            "usd_per_chat": round(usd / chats, 6) if chats else 0.0,
            "waste_tokens": int(rec["waste_tokens"]),
            "waste_usd": wusd,
            "waste_share_pct": round((wusd / usd) * 100.0, 1) if usd > 0 else 0.0,
            "session_ids": _sort_sids(rec["session_ids"]),
        })
    by_agent.sort(key=lambda r: (-r["usd"], r["agent_label"].lower()))

    # Merge guard day/week counts into by_day and by_week
    for day_key, gcounts in (safety.get("by_day") or {}).items():
        if day_key in day_agg:
            day_agg[day_key]["blocked"] = gcounts.get("blocked", 0)
            day_agg[day_key]["asked"] = gcounts.get("asked", 0)
    by_day = [
        {
            **rec,
            "usd": round(rec["usd"], 6),
            "waste_usd": round(rec["waste_usd"], 6),
            "session_ids": _sort_sids(rec["session_ids"]),
        }
        for _, rec in sorted(day_agg.items(), reverse=True)
    ]

    for wk, gcounts in (safety.get("by_week") or {}).items():
        if wk in week_agg:
            week_agg[wk]["blocked"] = gcounts.get("blocked", 0)
            week_agg[wk]["asked"] = gcounts.get("asked", 0)
    by_week: list[dict] = []
    for _, rec in sorted(week_agg.items()):
        usd = round(rec["usd"], 6)
        wusd = round(rec["waste_usd"], 6)
        by_week.append({
            "week": rec["week"],
            "week_start": rec["week_start"],
            "chats": rec["chats"],
            "sent": rec["sent"],
            "usd": usd,
            "waste_tokens": int(rec["waste_tokens"]),
            "waste_usd": wusd,
            "waste_share_pct": round((wusd / usd) * 100.0, 1) if usd > 0 else 0.0,
            "blocked": rec["blocked"],
            "asked": rec["asked"],
            "session_ids": _sort_sids(rec["session_ids"]),
        })

    # Finalize wastes and ranked recommendations
    applied_fixes = week._load_applied_fixes()
    wastes: list[dict] = []
    for fid, w in waste_agg.items():
        rif = rules_in_files.get(fid)
        app = applied_fixes.get(fid) or []
        projs = sorted(w["projects"])
        files = sorted(rif["files"]) if rif else []
        wastes.append({
            "id": fid,
            "title": w["title"],
            "chats": w["chats"],
            "projects": projs,
            "agents": sorted(w["agents"]),
            "saving_tokens": int(w["saving_tokens"]),
            "saving_usd": round(float(w["saving_usd"]), 6),
            "fix": week._fix_text_for(fid, w["how"]),
            "can_auto_fix": fid in fixer_store.RULE_OF or fid in ("unused-tools", "unused-builtins", "long-chat"),
            "rule_in_files": files,
            "applied_count": len(app),
            "session_ids": _sort_sids(w["session_ids"]),
        })
    wastes.sort(key=lambda r: (-r["saving_usd"], -r["saving_tokens"], r["id"]))

    recommendations: list[dict] = []
    for rank, w in enumerate(wastes, 1):
        where_parts = []
        if w["projects"]:
            shown = ", ".join(w["projects"][:3]) + (f" (+{len(w['projects']) - 3} more)" if len(w["projects"]) > 3 else "")
            where_parts.append(shown)
        if w["rule_in_files"]:
            where_parts.append("rule in " + ", ".join(w["rule_in_files"][:2]))
        recommendations.append({
            "rank": rank,
            "id": w["id"],
            "title": w["title"],
            "what": w["fix"],
            "where": " · ".join(where_parts) if where_parts else "all projects",
            "projects": w["projects"],
            "rule_in_files": w["rule_in_files"],
            "saving_usd": w["saving_usd"],
            "saving_tokens": w["saving_tokens"],
            "chats": w["chats"],
            "can_auto_fix": w["can_auto_fix"],
            "applied_count": w["applied_count"],
            "session_ids": w["session_ids"],
        })

    # Reuse week.collect() for week-over-week trend and fix-impact proof without re-reading sessions from disk
    week_data = week.collect(
        pre_analysed=matched_for_week,
        prices=P,
        project=project,
        today=ref_today,
        engines=engines,
    )

    total_usd = round(sum(r["usd"] for r in session_rows), 6)
    total_waste_usd = round(sum(r["waste_usd"] for r in session_rows), 6)
    total_waste_tok = sum(r["waste_tokens"] for r in session_rows)
    total_sent = sum(r["sent"] for r in session_rows)
    total_output = sum(r["output"] for r in session_rows)
    total_working_min = round(sum(r["working_min"] for r in session_rows), 2)
    total_waiting_min = round(sum(r["waiting_min"] for r in session_rows), 2)

    biggest = recommendations[0] if recommendations else None
    summary = {
        "chats": len(session_rows),
        "projects": len(by_project),
        "agents": len(by_agent),
        "turns": sum(r["turns"] for r in session_rows),
        "calls": sum(r["calls"] for r in session_rows),
        "tools": sum(r["tools"] for r in session_rows),
        "commits": sum(r["commits"] for r in session_rows),
        "working_min": total_working_min,
        "waiting_min": total_waiting_min,
        "hours": round(total_working_min / 60.0, 2),
        "sent": total_sent,
        "output": total_output,
        "usd": total_usd,
        "waste_tokens": total_waste_tok,
        "waste_usd": total_waste_usd,
        "waste_share_pct": round((total_waste_usd / total_usd) * 100.0, 1) if total_usd > 0 else 0.0,
        "guard_chats": safety["chats"],
        "guard_blocked": safety["blocked"],
        "guard_asked": safety["asked"],
        "guard_intact": safety["intact"],
        "guard_broken": safety["broken"],
        "biggest_saving": biggest,
    }

    price_own = list(P.get("own") or [])
    price_note = (
        f"custom/organisation prices ({', '.join(price_own)})" if price_own
        else f"built-in price list (checked {pricemod.checked_day(P.get('as_of', ''))})"
    )

    return {
        "summary": summary,
        "by_project": by_project,
        "by_agent": by_agent,
        "by_day": by_day,
        "wastes": wastes,
        "safety": safety,
        "recommendations": recommendations,
        "trend": {
            "week_over_week": week_data,
            "by_week": by_week,
            "points": week_data["trend"],
        },
        "sessions": session_rows,
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project_filter": project or "",
            "agent_filter": agent_filter or "",
            "since": cutoff.isoformat() if cutoff else "",
            "days": days,
            "skipped": skipped,
            "prices_source": price_note,
            "prices_stale": pricemod.stale_note(P),
        },
    }


def render_text(data: dict, html_path: Path | None = None) -> str:
    """Concise terminal summary of cross-session, cross-agent insights."""
    s = data["summary"]
    meta = data["meta"]
    safety = data["safety"]
    wow = data["trend"]["week_over_week"]["comparison"]

    lines = [
        f"SessionCost insights ({s['chats']} chat{'s' if s['chats'] != 1 else ''} · "
        f"{s['projects']} project{'s' if s['projects'] != 1 else ''} · "
        f"{s['agents']} agent{'s' if s['agents'] != 1 else ''})",
        f"Totals: {_money(s['usd'])} spent ({_tok(s['sent'])} sent, {_tok(s['output'])} written) · "
        f"{_hours_str(s['working_min'])} working · {s['tools']} tool calls · {s['commits']} commits",
        f"Measured waste: {_money(s['waste_usd'])} ({_tok(s['waste_tokens'])} tokens, {s['waste_share_pct']:.0f}% of spend)",
    ]

    if safety["available"]:
        intact_note = (
            f"{safety['intact']}/{safety['chats']} records untouched"
            if safety["intact_ok"]
            else f"{safety['intact']}/{safety['chats']} records untouched ({safety['broken']} broken)"
        )
        lines.append(
            f"Safety (live guard): {safety['blocked']} stopped · {safety['asked']} asked you · "
            f"{safety['injections']} hidden instructions · {intact_note}"
        )
    else:
        lines.append("Safety (live guard): no guarded chats recorded yet.")

    lines.append(f"Trend: {wow['verdict']}")

    if s["biggest_saving"]:
        b = s["biggest_saving"]
        lines.append(
            f"\nBiggest saving: {b['title']} — {_money(b['saving_usd'])} "
            f"({_tok(b['saving_tokens'])} tokens across {b['chats']} chat{'s' if b['chats'] != 1 else ''})"
        )
        lines.append(f"  Where: {b['where']}")
        lines.append(f"  Fix: {b['what']}")

    if data["by_agent"]:
        lines.append("\nBy agent:")
        for a in data["by_agent"]:
            lines.append(
                f"  {a['agent_label']:12}  {a['chats']:>4} chats  {_money(a['usd']):>9}  "
                f"waste {_money(a['waste_usd']):>8} ({a['waste_share_pct']:.0f}%)"
            )

    if data["by_project"]:
        lines.append("\nTop projects:")
        for p in data["by_project"][:5]:
            lines.append(
                f"  {p['project'][:24]:24}  {p['chats']:>4} chats  {_money(p['usd']):>9}  "
                f"waste {_money(p['waste_usd']):>8}"
            )

    if data["recommendations"]:
        lines.append("\nTop recommendations:")
        for r in data["recommendations"][:3]:
            lines.append(
                f"  {r['rank']}. {r['title']} — {_money(r['saving_usd'])} ({r['where']})"
            )
            lines.append(f"     {r['what']}")

    if meta["prices_stale"]:
        lines.append(f"\nNote: {meta['prices_stale']}")
    if meta["skipped"]:
        lines.append(f"Note: skipped {meta['skipped']} unreadable log{'s' if meta['skipped'] != 1 else ''}.")
    if html_path:
        lines.append(f"\nInsights page: {Path(html_path).as_posix()}")
    return "\n".join(lines)


def _session_links_html(sids: list[str], sessions_by_id: dict[str, dict], limit: int = 3) -> str:
    """Render clickable links down to each session row in #sessions (and its saved HTML report if present)."""
    if not sids:
        return '<span class="muted">—</span>'
    parts = []
    for sid in sids[:limit]:
        rec = sessions_by_id.get(sid)
        anchor = rec["anchor"] if rec else _safe_anchor(sid)
        short = rec["short_id"] if rec else sid[:8]
        title = rec["title"] if rec else sid
        parts.append(f'<a class="slink num" href="#{_e(anchor)}" title="{_e(title)}">{_e(short)}</a>')
    extra = len(sids) - limit
    if extra > 0:
        parts.append(f'<span class="muted">+{extra}</span>')
    return " ".join(parts)


AGENT_COLORS = {
    "claude": "#4f68d0",
    "antigravity": "#8b5cf6",
    "codex": "#0d9488",
    "cursor": "#d97706",
    "copilot": "#0284c7",
}


def _sanitize_json(obj: object) -> object:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [_sanitize_json(v) for v in obj]
    return obj


def _json_for_script(obj: object) -> str:
    """Serialize JSON safely for embedding inside <script type="application/json">."""
    return (
        json.dumps(_sanitize_json(obj), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _date_range_label(meta: dict, sessions: list[dict]) -> str:
    dates = sorted(r["date"] for r in sessions if r.get("date"))
    span = f"{dates[0]} to {dates[-1]}" if len(dates) >= 2 and dates[0] != dates[-1] else (dates[0] if dates else "no chats")
    if meta.get("days"):
        return f"Last {meta['days']} days · {span}"
    if meta.get("since"):
        return f"Since {meta['since']} · {span}"
    return f"All time · {span}"


def _svg_efficiency_ring(score: float, projected: float | None = None) -> str:
    """Animated SVG Efficiency Score Ring (0-100) showing current productive share + optional projected arc."""
    sc = max(0.0, min(100.0, float(score)))
    proj = max(sc, min(100.0, float(projected if projected is not None else sc)))
    r = 62.0
    circ = 2.0 * 3.141592653589793 * r
    sc_off = circ * (1.0 - sc / 100.0)
    proj_off = circ * (1.0 - proj / 100.0)
    col = "var(--ok)" if sc >= 80 else ("var(--cut)" if sc >= 60 else "var(--bad)")
    grade = "Efficient" if sc >= 80 else ("Moderate waste" if sc >= 60 else "High waste")
    return (
        '<svg viewBox="0 0 160 160" class="ring-svg" id="effRingSvg" role="img" aria-label="Token efficiency score">'
        f'<circle cx="80" cy="80" r="{r:.0f}" fill="none" stroke="var(--raise)" stroke-width="14"/>'
        f'<circle id="ringProjArc" cx="80" cy="80" r="{r:.0f}" fill="none" stroke="var(--ok)" stroke-width="14" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.2f}" stroke-dashoffset="{proj_off:.2f}" '
        'opacity="0.28" transform="rotate(-90 80 80)" class="ring-arc"/>'
        f'<circle id="ringScoreArc" cx="80" cy="80" r="{r:.0f}" fill="none" stroke="{col}" stroke-width="14" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.2f}" stroke-dashoffset="{sc_off:.2f}" '
        'transform="rotate(-90 80 80)" class="ring-arc"/>'
        f'<text id="ringScoreVal" x="80" y="76" text-anchor="middle" class="ring-val">{sc:.0f}</text>'
        f'<text id="ringScoreLbl" x="80" y="96" text-anchor="middle" class="ring-sub">{_e(grade)}</text>'
        f'<text id="ringProjLbl" x="80" y="112" text-anchor="middle" class="ring-proj">'
        f'{f"→ {proj:.0f} with fixes" if proj > sc + 0.5 else "clean score"}</text>'
        '</svg>'
    )


def _svg_sankey(sessions: list[dict]) -> str:
    """Animated SVG Money Flow Sankey: Agents -> Projects -> Productive vs. Avoidable Waste."""
    if not sessions:
        return '<div class="empty-chart">No spend flow in this window yet.</div>'
    total = sum(max(0.0, float(r.get("usd") or 0.0)) for r in sessions)
    if total <= 0:
        return '<div class="empty-chart">No billable spend flow in this window yet.</div>'

    ag_tot: dict[str, dict] = {}
    pr_tot: dict[str, dict] = {}
    ap_links: dict[tuple[str, str], float] = {}
    for r in sessions:
        u = max(0.0, float(r.get("usd") or 0.0))
        if u <= 0:
            continue
        w = min(u, max(0.0, float(r.get("waste_usd") or 0.0)))
        ak = str(r.get("agent") or "unknown")
        al = str(r.get("agent_label") or ak)
        pn = str(r.get("project") or "unknown")
        a = ag_tot.setdefault(ak, {"key": ak, "label": al, "usd": 0.0})
        a["usd"] += u
        p = pr_tot.setdefault(pn, {"key": pn, "label": pn, "usd": 0.0, "waste": 0.0})
        p["usd"] += u
        p["waste"] += w
        ap_links[(ak, pn)] = ap_links.get((ak, pn), 0.0) + u

    agents = sorted(ag_tot.values(), key=lambda x: -x["usd"])[:6]
    projs_all = sorted(pr_tot.values(), key=lambda x: -x["usd"])
    if len(projs_all) > 5:
        top5 = projs_all[:5]
        other_u = sum(p["usd"] for p in projs_all[5:])
        other_w = sum(p["waste"] for p in projs_all[5:])
        other_names = {p["key"] for p in projs_all[5:]}
        projs = top5 + [{"key": "__other__", "label": f"Other ({len(projs_all) - 5})", "usd": other_u, "waste": other_w}]
        folded_links: dict[tuple[str, str], float] = {}
        for (ak, pn), val in ap_links.items():
            pk = "__other__" if pn in other_names else pn
            folded_links[(ak, pk)] = folded_links.get((ak, pk), 0.0) + val
        ap_links = folded_links
    else:
        projs = projs_all

    tot_waste = sum(p["waste"] for p in projs)
    tot_prod = max(0.0, total - tot_waste)
    outcomes = []
    if tot_prod > 0:
        outcomes.append({"key": "productive", "label": "Productive", "usd": tot_prod, "color": "var(--keep)"})
    if tot_waste > 0:
        outcomes.append({"key": "waste", "label": "Avoidable waste", "usd": tot_waste, "color": "var(--cut)"})

    W, H = 820, 260
    pad_y, gap, nw = 28.0, 10.0, 14.0
    x1, x2, x3 = 148.0, 403.0, 658.0
    avail_h = H - 2.0 * pad_y

    def _layout(nodes: list[dict], x: float) -> dict[str, dict]:
        s_val = sum(n["usd"] for n in nodes) or 1.0
        usable = max(40.0, avail_h - gap * max(0, len(nodes) - 1))
        y = pad_y
        out = {}
        for n in nodes:
            nh = max(6.0, (n["usd"] / s_val) * usable)
            out[n["key"]] = {**n, "x": x, "y": y, "h": nh, "out_y": y, "in_y": y}
            y += nh + gap
        return out

    a_pos = _layout(agents, x1)
    p_pos = _layout(projs, x2)
    o_pos = _layout(outcomes, x3)

    parts = [
        f'<svg viewBox="0 0 {W} {H}" class="sankey-svg" role="img" aria-label="Money flow from agents to projects to productive spend and waste">',
        f'<text x="{x1 + nw / 2:.0f}" y="16" text-anchor="middle" class="svg-eyebrow">AGENTS</text>',
        f'<text x="{x2 + nw / 2:.0f}" y="16" text-anchor="middle" class="svg-eyebrow">PROJECTS</text>',
        f'<text x="{x3 + nw / 2:.0f}" y="16" text-anchor="middle" class="svg-eyebrow">OUTCOME</text>',
    ]

    # Ribbons: Agent -> Project
    for a in agents:
        ap = a_pos[a["key"]]
        col = AGENT_COLORS.get(a["key"], "#64748b")
        for p in projs:
            val = ap_links.get((a["key"], p["key"]), 0.0)
            if val <= 0:
                continue
            pp = p_pos[p["key"]]
            bh = max(2.0, (val / max(ap["usd"], 1e-9)) * ap["h"])
            bh2 = max(2.0, (val / max(pp["usd"], 1e-9)) * pp["h"])
            y0 = ap["out_y"] + bh / 2.0
            y1 = pp["in_y"] + bh2 / 2.0
            ap["out_y"] += bh
            pp["in_y"] += bh2
            sw = max(2.0, (bh + bh2) / 2.0)
            mx = (x1 + nw + x2) / 2.0
            tip = f'{a["label"]} → {p["label"]}: {_money(val)}'
            parts.append(
                f'<path d="M{x1 + nw:.1f},{y0:.1f} C{mx:.1f},{y0:.1f} {mx:.1f},{y1:.1f} {x2:.1f},{y1:.1f}" '
                f'fill="none" stroke="{col}" stroke-width="{sw:.1f}" class="sk-link" '
                f'data-agent="{_e(a["key"])}" data-project="{_e(p["key"])}"><title>{_e(tip)}</title></path>'
            )

    # Ribbons: Project -> Productive / Waste
    for p in projs:
        pp = p_pos[p["key"]]
        prod_v = max(0.0, p["usd"] - p["waste"])
        waste_v = p["waste"]
        for ok_key, val, col, is_waste in (
            ("productive", prod_v, "var(--keep)", False),
            ("waste", waste_v, "var(--cut)", True),
        ):
            if val <= 0 or ok_key not in o_pos:
                continue
            op = o_pos[ok_key]
            bh = max(2.0, (val / max(pp["usd"], 1e-9)) * pp["h"])
            bh2 = max(2.0, (val / max(op["usd"], 1e-9)) * op["h"])
            y0 = pp["out_y"] + bh / 2.0
            y1 = op["in_y"] + bh2 / 2.0
            pp["out_y"] += bh
            op["in_y"] += bh2
            sw = max(2.0, (bh + bh2) / 2.0)
            mx = (x2 + nw + x3) / 2.0
            tip = f'{p["label"]} → {"Avoidable waste" if is_waste else "Productive"}: {_money(val)}'
            cls = "sk-link sk-waste" if is_waste else "sk-link"
            parts.append(
                f'<path d="M{x2 + nw:.1f},{y0:.1f} C{mx:.1f},{y0:.1f} {mx:.1f},{y1:.1f} {x3:.1f},{y1:.1f}" '
                f'fill="none" stroke="{col}" stroke-width="{sw:.1f}" class="{cls}" '
                f'data-project="{_e(p["key"])}"><title>{_e(tip)}</title></path>'
            )
            if is_waste:
                parts.append(
                    f'<path d="M{x2 + nw:.1f},{y0:.1f} C{mx:.1f},{y0:.1f} {mx:.1f},{y1:.1f} {x3:.1f},{y1:.1f}" '
                    f'fill="none" stroke="var(--bg)" stroke-width="{max(1.5, sw * 0.28):.1f}" '
                    f'stroke-dasharray="4 10" class="sk-flow" data-project="{_e(p["key"])}"/>'
                )

    # Nodes
    for a in agents:
        ap = a_pos[a["key"]]
        col = AGENT_COLORS.get(a["key"], "#64748b")
        parts.append(
            f'<g class="sk-node sk-click" data-fagent="{_e(a["key"])}" tabindex="0" role="button" aria-label="Filter by {_e(a["label"])} ({_e(_money(a["usd"]))})">'
            f'<title>Click to filter by {_e(a["label"])} ({_e(_money(a["usd"]))})</title>'
            f'<rect x="{ap["x"]:.1f}" y="{ap["y"]:.1f}" width="{nw:.0f}" height="{ap["h"]:.1f}" rx="4" fill="{col}"/>'
            f'<text x="{ap["x"] - 8:.1f}" y="{ap["y"] + ap["h"] / 2 - 2:.1f}" text-anchor="end" class="sk-lbl">{_e(a["label"][:18])}</text>'
            f'<text x="{ap["x"] - 8:.1f}" y="{ap["y"] + ap["h"] / 2 + 12:.1f}" text-anchor="end" class="sk-sub">{_e(_money(a["usd"]))}</text></g>'
        )
    for p in projs:
        pp = p_pos[p["key"]]
        is_other = p["key"] == "__other__"
        click_attr = "" if is_other else f' data-fproj="{_e(p["key"])}" tabindex="0" role="button" aria-label="Filter by {_e(p["label"])}"'
        cls = "sk-node" if is_other else "sk-node sk-click"
        parts.append(
            f'<g class="{cls}"{click_attr}>'
            f'<title>{_e(p["label"])}: {_e(_money(p["usd"]))} ({_e(_money(p["waste"]))} waste)</title>'
            f'<rect x="{pp["x"]:.1f}" y="{pp["y"]:.1f}" width="{nw:.0f}" height="{pp["h"]:.1f}" rx="4" fill="var(--ink-2)"/>'
            f'<text x="{pp["x"] + nw / 2:.1f}" y="{pp["y"] - 5:.1f}" text-anchor="middle" class="sk-mid">{_e(p["label"][:20])}</text></g>'
        )
    for o in outcomes:
        op = o_pos[o["key"]]
        pct = 100.0 * o["usd"] / total
        parts.append(
            f'<g class="sk-node">'
            f'<rect x="{op["x"]:.1f}" y="{op["y"]:.1f}" width="{nw:.0f}" height="{op["h"]:.1f}" rx="4" fill="{o["color"]}"/>'
            f'<text x="{op["x"] + nw + 8:.1f}" y="{op["y"] + op["h"] / 2 - 2:.1f}" text-anchor="start" class="sk-lbl">{_e(o["label"])}</text>'
            f'<text x="{op["x"] + nw + 8:.1f}" y="{op["y"] + op["h"] / 2 + 12:.1f}" text-anchor="start" class="sk-sub">{_e(_money(o["usd"]))} ({pct:.0f}%)</text></g>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _svg_heatmap(by_day: list[dict], ref_date_str: str) -> str:
    """12-week daily Spend & Waste Heatmap Calendar (GitHub-style, 100% inline SVG)."""
    day_map = {d["date"]: d for d in by_day if d.get("date")}
    try:
        end_d = date.fromisoformat(ref_date_str[:10]) if ref_date_str else date.today()
    except ValueError:
        end_d = date.today()
    if day_map:
        max_d_str = max(day_map.keys())
        try:
            max_d = date.fromisoformat(max_d_str)
            if max_d > end_d or (end_d - max_d).days > 84:
                end_d = max_d
        except ValueError:
            pass
    # Align start to Monday 11 weeks before end_d's week
    end_mon = end_d - timedelta(days=end_d.weekday())
    start_d = end_mon - timedelta(weeks=11)
    peak = max((float(d.get("usd") or 0.0) for d in day_map.values()), default=0.0) or 0.01

    cell, gap, pad_l, pad_t = 14, 4, 28, 20
    W = pad_l + 12 * (cell + gap) + 8
    H = pad_t + 7 * (cell + gap) + 6
    parts = [f'<svg viewBox="0 0 {W} {H}" class="heat-svg" role="img" aria-label="Daily spend and waste heatmap">']
    for idx, lbl in ((0, "M"), (2, "W"), (4, "F")):
        y = pad_t + idx * (cell + gap) + cell - 3
        parts.append(f'<text x="{pad_l - 6}" y="{y}" text-anchor="end" class="svg-ax">{lbl}</text>')

    seen_months: set[str] = set()
    cur = start_d
    for col in range(12):
        col_x = pad_l + col * (cell + gap)
        m_lbl = cur.strftime("%b")
        m_key = cur.strftime("%Y-%m")
        if m_key not in seen_months:
            seen_months.add(m_key)
            parts.append(f'<text x="{col_x}" y="12" class="svg-ax">{_e(m_lbl)}</text>')
        for row in range(7):
            ds = cur.isoformat()
            rec = day_map.get(ds)
            y = pad_t + row * (cell + gap)
            if rec and (rec.get("chats") or 0) > 0:
                u = float(rec.get("usd") or 0.0)
                w = float(rec.get("waste_usd") or 0.0)
                w_ratio = (w / u) if u > 0 else 0.0
                lvl = min(4, max(1, int((u / peak) * 4.0 + 0.99)))
                cls = f"hm-cell hm-w{lvl}" if w_ratio >= 0.25 else f"hm-cell hm-p{lvl}"
                tip = f'{ds}: {_money(u)} spend, {_money(w)} waste ({rec["chats"]} chats) — click to filter'
                parts.append(
                    f'<rect x="{col_x}" y="{y}" width="{cell}" height="{cell}" rx="3" class="{cls}" data-hday="{ds}" tabindex="0" role="button" aria-label="{_e(tip)}">'
                    f'<title>{_e(tip)}</title></rect>'
                )
            else:
                parts.append(
                    f'<rect x="{col_x}" y="{y}" width="{cell}" height="{cell}" rx="3" class="hm-cell hm-0">'
                    f'<title>{ds}: no activity</title></rect>'
                )
            cur += timedelta(days=1)
    parts.append("</svg>")
    return "".join(parts)


def _svg_stacked_bars(buckets: list[dict], label_key: str) -> str:
    """Pure inline SVG vertical stacked bar chart (productive spend + hatched amber waste)."""
    if not buckets:
        return '<div class="empty-chart">No activity in this window yet.</div>'
    items = list(reversed(buckets[:24])) if label_key == "date" else buckets[-24:]
    peak = max((float(b.get("usd") or 0.0) for b in items), default=0.0) or 0.01
    w, h = 760, 210
    pad_l, pad_r, pad_t, pad_b = 54, 14, 16, 34
    cw, ch = w - pad_l - pad_r, h - pad_t - pad_b
    n = max(1, len(items))
    slot = cw / n
    bar_w = max(8.0, min(32.0, slot * 0.64))

    grid_lines = []
    for frac in (0.0, 0.5, 1.0):
        y = pad_t + ch * (1.0 - frac)
        val = peak * frac
        grid_lines.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" class="svg-grid"/>'
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" class="svg-ax">{_e(_money(val))}</text>'
        )

    bars = []
    step_lbl = max(1, (n + 7) // 8)
    for i, b in enumerate(items):
        usd = max(0.0, float(b.get("usd") or 0.0))
        waste = min(usd, max(0.0, float(b.get("waste_usd") or 0.0)))
        prod = max(0.0, usd - waste)
        tot_h = (usd / peak) * ch
        prod_h = (prod / peak) * ch
        waste_h = max(0.0, tot_h - prod_h)
        x = pad_l + i * slot + (slot - bar_w) / 2.0
        y_prod = pad_t + ch - prod_h
        y_waste = y_prod - waste_h
        lbl = str(b.get(label_key) or "")
        short_lbl = lbl[5:] if label_key == "date" and len(lbl) == 10 else lbl
        tip = f"{lbl}: {_money(usd)} spend, {_money(waste)} waste ({b.get('chats', 0)} chats)"
        delay = min(0.45, i * 0.02)
        bars.append(
            f'<g class="bar-g"><title>{_e(tip)}</title>'
            + (f'<rect x="{x:.1f}" y="{y_prod:.1f}" width="{bar_w:.1f}" height="{prod_h:.1f}" rx="3" class="svg-keep bar-grow" style="animation-delay:{delay:.2f}s"/>' if prod_h > 0.5 else "")
            + (f'<rect x="{x:.1f}" y="{y_waste:.1f}" width="{bar_w:.1f}" height="{waste_h:.1f}" rx="3" fill="url(#wasteHatch)" class="svg-cut-b bar-grow" style="animation-delay:{delay + 0.05:.2f}s"/>' if waste_h > 0.5 else "")
            + (f'<text x="{x + bar_w / 2:.1f}" y="{h - 10}" text-anchor="middle" class="svg-ax">{_e(short_lbl)}</text>' if (i % step_lbl == 0 or i == n - 1) else "")
            + "</g>"
        )

    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart-svg" role="img" aria-label="Spend and waste over time">'
        '<defs><pattern id="wasteHatch" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">'
        '<rect width="6" height="6" fill="var(--cut-soft)"/>'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="var(--cut)" stroke-width="3"/></pattern></defs>'
        + "".join(grid_lines) + "".join(bars) + "</svg>"
    )


def _svg_agent_donut(by_agent: list[dict], total_usd: float) -> str:
    """Pure inline SVG donut chart of spend share across agents."""
    if not by_agent or total_usd <= 0:
        return '<div class="empty-chart">No agent spend yet.</div>'
    cx, cy, r, sw = 92, 92, 68, 22
    circ = 2.0 * 3.141592653589793 * r
    offset = 0.0
    segs = []
    for a in by_agent:
        usd = max(0.0, float(a.get("usd") or 0.0))
        if usd <= 0:
            continue
        frac = usd / total_usd
        dash = frac * circ
        color = AGENT_COLORS.get(a.get("agent", ""), "#64748b")
        tip = f"{a['agent_label']}: {_money(usd)} ({frac * 100:.0f}%)"
        segs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-dasharray="{dash:.2f} {circ:.2f}" stroke-dashoffset="{-offset:.2f}" '
            f'transform="rotate(-90 {cx} {cy})" class="donut-seg"><title>{_e(tip)}</title></circle>'
        )
        offset += dash
    return (
        f'<svg viewBox="0 0 184 184" class="donut-svg" role="img" aria-label="Spend by agent">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="var(--raise)" stroke-width="{sw}"/>'
        + "".join(segs)
        + f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" class="donut-val">{_e(_money(total_usd))}</text>'
        + f'<text x="{cx}" y="{cy + 15}" text-anchor="middle" class="donut-sub">total spend</text>'
        + "</svg>"
    )


def render_html(data: dict) -> str:
    """Self-contained visual, animated HTML insights dashboard with Efficiency Score Ring,
    What-If Savings Simulator, Money Flow Sankey, Waste Heatmap Calendar, LiveGuard Safety Pulse,
    and searchable session drill-down (100% offline, zero .innerHTML)."""
    s = data["summary"]
    meta = data["meta"]
    safety = data["safety"]
    week_data = data["trend"]["week_over_week"]
    by_week = data["trend"]["by_week"]
    by_day = data["by_day"]
    sessions = data["sessions"]
    sessions_by_id = {r["session_id"]: r for r in sessions}

    range_label = _date_range_label(meta, sessions)
    tot_usd = float(s["usd"] or 0.0)
    waste_usd = min(tot_usd, float(s["waste_usd"] or 0.0))
    after_usd = max(0.0, tot_usd - waste_usd)
    waste_pct = (100.0 * waste_usd / tot_usd) if tot_usd > 0 else 0.0
    prod_pct = max(0.0, 100.0 - waste_pct) if tot_usd > 0 else 100.0

    # Simulator initial projected score (all top wastes selected)
    top_sim_wastes = data["wastes"][:6]
    sim_save_init = min(waste_usd, sum(float(w["saving_usd"] or 0.0) for w in top_sim_wastes))
    proj_score = min(100.0, 100.0 * (after_usd + sim_save_init) / tot_usd) if tot_usd > 0 else 100.0
    ring_svg = _svg_efficiency_ring(prod_pct, proj_score)
    sankey_svg = _svg_sankey(sessions)
    heatmap_svg = _svg_heatmap(by_day, meta.get("generated_at", "")[:10])

    # Safety pulse line
    if not safety["available"]:
        safety_line = "LiveGuard is off — run /liveguard to block risky commands & catch hidden injections."
        safety_cls = "af-muted"
        safety_badge = "Guard off"
    elif not safety["intact_ok"]:
        safety_line = f'LiveGuard: {safety["blocked"]} stopped, {safety["asked"]} asked · {safety["broken"]} audit record(s) altered!'
        safety_cls = "af-warn"
        safety_badge = f'{safety["broken"]} broken'
    else:
        safety_line = f'LiveGuard active across {safety["chats"]} chats: {safety["blocked"]} stopped, {safety["asked"]} asked · {safety["intact"]}/{safety["chats"]} records untouched.'
        safety_cls = "af-ok"
        safety_badge = f'{safety["blocked"]} stopped · {safety["asked"]} asked'

    # Simulator rows (initial SSR)
    sim_rows = []
    auto_ids = [w["id"] for w in top_sim_wastes if w.get("can_auto_fix")]
    init_cmd = (
        f'sessioncost fix --only {",".join(auto_ids)}'
        if auto_ids else "/sessioncost fix"
    )
    for w in top_sim_wastes:
        auto_tag = '<span class="mini-pill ok">Auto-fix</span>' if w.get("can_auto_fix") else '<span class="mini-pill">Guide</span>'
        sim_rows.append(
            f'<label class="sim-row"><input type="checkbox" class="sim-chk" data-wid="{_e(w["id"])}" checked>'
            f'<span class="sim-switch" aria-hidden="true"></span>'
            f'<span class="sim-name">{_e(w["title"])} {auto_tag}</span>'
            f'<span class="num sim-save">-{_e(_money(w["saving_usd"]))}</span></label>'
        )
    if not sim_rows:
        sim_rows_html = '<div class="sim-clean">Zero waste triggers in this window — your agent workflow is already lean!</div>'
    else:
        sim_rows_html = "".join(sim_rows)

    # Single biggest saving callout
    biggest = s.get("biggest_saving")
    if biggest:
        biggest_html = (
            '<div class="callout" id="biggestCard">'
            '<div class="callout-top"><span class="eyebrow">Single biggest saving</span>'
            f'<span class="chip warn num">{_e(_money(biggest["saving_usd"]))} · {_e(_tok(biggest["saving_tokens"]))} tok</span></div>'
            f'<h3>{_e(biggest["title"])}</h3>'
            f'<div class="meta-line">{biggest["chats"]} chat{"s" if biggest["chats"] != 1 else ""} · Where: <b>{_e(biggest["where"])}</b></div>'
            f'<div class="fix-box"><div class="fix-txt"><b>Fix:</b> <code>{_e(biggest["what"])}</code></div>'
            f'<button type="button" class="btn copy-btn" data-copy="{_e(biggest["what"])}">Copy</button></div>'
            f'<div class="meta-line">Sessions: {_session_links_html(biggest["session_ids"], sessions_by_id, limit=5)}</div>'
            '</div>'
        )
    else:
        biggest_html = (
            '<div class="callout ok-border" id="biggestCard"><span class="eyebrow">Single biggest saving</span>'
            '<h3>Clean across all scanned sessions</h3>'
            '<div class="muted">No built-in waste checks fired in the scanned chats.</div></div>'
        )

    # 1. Time-series SVG chart
    time_svg = _svg_stacked_bars(by_day, "date")

    # 2. Agents visual cards + donut
    donut_svg = _svg_agent_donut(data["by_agent"], tot_usd)
    agent_cards = []
    for a in data["by_agent"]:
        color = AGENT_COLORS.get(a["agent"], "#64748b")
        wp = min(100.0, max(0.0, float(a["waste_share_pct"] or 0.0)))
        agent_cards.append(
            f'<div class="agent-card" data-fagent-card="{_e(a["agent"])}" tabindex="0" role="button" aria-label="Filter dashboard to {_e(a["agent_label"])}" title="Click to filter dashboard to {_e(a["agent_label"])}">'
            f'<div class="ac-head"><span class="dot" style="background:{color}"></span>'
            f'<b>{_e(a["agent_label"])}</b><span class="num ac-usd">{_e(_money(a["usd"]))}</span></div>'
            f'<div class="ac-sub">{a["chats"]} chats · {a["projects"]} proj · {_e(_hours_str(a["working_min"]))} · avg {_e(_money(a["usd_per_chat"]))}/chat</div>'
            f'<div class="meter-row"><div class="meter"><span class="meter-prod" style="width:{100.0 - wp:.1f}%"></span>'
            f'<span class="meter-waste" style="width:{wp:.1f}%"></span></div>'
            f'<span class="num warn">{_e(_money(a["waste_usd"]))} waste ({wp:.0f}%)</span></div>'
            f'<div class="ac-foot">{a["tools"]} tools · {a["commits"]} commits · {_session_links_html(a["session_ids"], sessions_by_id, limit=2)}</div>'
            '</div>'
        )

    # 3. Projects horizontal stacked bars
    peak_proj = max((float(p["usd"] or 0.0) for p in data["by_project"]), default=0.0) or 0.01
    proj_bars = []
    for p in data["by_project"]:
        pusd = float(p["usd"] or 0.0)
        pwaste = min(pusd, float(p["waste_usd"] or 0.0))
        pprod = max(0.0, pusd - pwaste)
        w_prod = min(100.0, 100.0 * pprod / peak_proj)
        w_waste = min(100.0 - w_prod, 100.0 * pwaste / peak_proj)
        proj_bars.append(
            f'<div class="hbar-row" data-project="{_e(p["project"])}" tabindex="0" role="button" aria-label="Filter dashboard to {_e(p["project"])}" title="Click to filter dashboard to {_e(p["project"])}">'
            f'<div class="hbar-head"><div><b>{_e(p["project"])}</b> '
            f'<span class="muted">{_e(", ".join(p["agents"]))} · {p["chats"]} chats · {_e(_hours_str(p["working_min"]))} · {p["commits"]} commits</span></div>'
            f'<div class="num"><b>{_e(_money(pusd))}</b> <span class="warn">({_e(_money(pwaste))} waste)</span></div></div>'
            f'<div class="hbar-track"><span class="hbar-prod" style="width:{w_prod:.1f}%"></span>'
            f'<span class="hbar-waste" style="width:{w_waste:.1f}%"></span></div>'
            f'<div class="hbar-foot">Sessions: {_session_links_html(p["session_ids"], sessions_by_id, limit=3)}</div>'
            '</div>'
        )

    # 4. Waste drivers horizontal bars
    peak_waste = max((float(w["saving_usd"] or 0.0) for w in data["wastes"]), default=0.0) or 0.01
    waste_bars = []
    for w in data["wastes"]:
        wusd = float(w["saving_usd"] or 0.0)
        pct = min(100.0, max(2.0 if wusd > 0 else 0.0, 100.0 * wusd / peak_waste))
        waste_bars.append(
            '<div class="wbar-row">'
            f'<div class="hbar-head"><div><b>{_e(w["title"])}</b> <span class="muted">· {w["chats"]} chats · {_e(", ".join(w["projects"][:3]))}</span></div>'
            f'<div class="num warn"><b>{_e(_money(wusd))}</b> <span class="muted">({_e(_tok(w["saving_tokens"]))} tok)</span></div></div>'
            f'<div class="hbar-track"><span class="hbar-waste" style="width:{pct:.1f}%"></span></div>'
            f'<div class="hbar-foot">Agents: {_e(", ".join(w["agents"]))} · {_session_links_html(w["session_ids"], sessions_by_id, limit=3)}</div>'
            '</div>'
        )

    # 5. Safety section
    tile = lambda n, label, cls="": f'<div class="tile {cls}"><b class="num">{_e(n)}</b><span>{_e(label)}</span></div>'
    if safety["available"]:
        safety_tiles = (
            tile(safety["chats"], "guarded chats")
            + tile(safety["blocked"], "stopped (did not run)", "bad" if safety["blocked"] else "")
            + tile(safety["asked"], "asked you first", "warn" if safety["asked"] else "")
            + tile(safety["injections"], "hidden instructions", "bad" if safety["injections"] else "")
            + tile(f"{safety['intact']}/{safety['chats']}", "records untouched",
                   "ok" if safety["intact_ok"] else "bad")
        )
        peak_reason = max((r["blocked"] + r["asked"] for r in safety["reasons"]), default=1) or 1
        reason_bars = "".join(
            f'<div class="rbar"><span>{_e(r["label"])}</span>'
            f'<div class="hbar-track"><span class="hbar-bad" style="width:{100 * r["blocked"] / peak_reason:.1f}%"></span>'
            f'<span class="hbar-waste" style="width:{100 * r["asked"] / peak_reason:.1f}%"></span></div>'
            f'<span class="num muted">{r["blocked"]} stop · {r["asked"]} ask</span></div>'
            for r in safety["reasons"]
        )
        step_rows = "".join(
            f'<tr><td class="num">{_e(st["ts"].replace("T", " ")[:16])}</td>'
            f'<td class="{"bad" if st["decision"] == "blocked" else "warn"}">'
            f'{"stopped" if st["decision"] == "blocked" else "asked"}</td>'
            f'<td>{_e(st.get("project", ""))}</td>'
            f'<td><code>{_e(st.get("tool", ""))}</code> {_e(st.get("what", "")[:60])}</td>'
            f'<td>{_session_links_html([st.get("chat", "")], sessions_by_id, limit=1)}</td></tr>'
            for st in safety["recent_steps"][:12]
        )
        safety_body = (
            f'<div class="tiles">{safety_tiles}</div>'
            '<div class="cols">'
            f'<div class="card"><h3>Why LiveGuard stepped in</h3>{reason_bars or "<p class=muted>Nothing stopped or asked about yet.</p>"}</div>'
            '<div class="card"><h3>Latest stopped or asked steps</h3><div class="tbl-wrap"><table><thead>'
            '<tr><th>When</th><th>Action</th><th>Project</th><th>Step</th><th>Session</th></tr></thead>'
            f'<tbody>{step_rows or "<tr><td colspan=5 class=muted>No stopped or asked steps recorded.</td></tr>"}</tbody>'
            '</table></div></div></div>'
        )
    else:
        safety_body = (
            '<div class="card safety-cta">'
            '<div><h3>Protect every chat with LiveGuard</h3>'
            '<p class="muted">Stop destructive shell commands, catch hidden prompt injections, and keep tamper-evident audit logs across all coding agents — 100% local.</p></div>'
            '<div class="fix-box"><code>/liveguard</code><button type="button" class="btn copy-btn" data-copy="/liveguard">Copy</button></div>'
            '</div>'
        )

    # 6. Action Playbook (Recommendations) + Proof
    rec_cards = []
    for r in data["recommendations"]:
        auto_badge = '<span class="pill ok">Auto-fixable via sessioncost fix</span>' if r["can_auto_fix"] else ""
        rec_cards.append(
            f'<div class="card rec-card"><div class="card-head">'
            f'<h3><span class="rank-num">#{r["rank"]}</span> {_e(r["title"])}</h3>'
            f'<div class="row">{auto_badge} <span class="chip warn num">{_e(_money(r["saving_usd"]))} · {_e(_tok(r["saving_tokens"]))} tok</span></div></div>'
            f'<div class="meta-line"><b>Where:</b> {_e(r["where"])} · '
            f'{r["chats"]} chat{"s" if r["chats"] != 1 else ""} · '
            f'Sessions: {_session_links_html(r["session_ids"], sessions_by_id, limit=4)}</div>'
            f'<div class="fix-box"><div class="fix-txt"><b>What to change:</b> <code>{_e(r["what"])}</code></div>'
            f'<button type="button" class="btn copy-btn" data-copy="{_e(r["what"])}">Copy</button></div></div>'
        )
    recs_html = "".join(rec_cards) or '<div class="card muted">No recommendations right now — none of the built-in waste checks fired.</div>'

    impact_rows = "".join(
        f'<tr><td>{_e(r["title"])}<div class="muted">{_e(r["source"])}</div></td>'
        f'<td class="n num">{_e(_money(r["prev_usd"]))}</td>'
        f'<td class="n num">{_e(_money(r["this_usd"]))}</td>'
        f'<td><span class="pill {"ok" if r["status"] in ("helped", "holding") else "warn"}">'
        f'{"Working" if r["status"] == "helped" else "Holding" if r["status"] == "holding" else "Still happening"}'
        f'</span> {_e(r["note"])}</td></tr>'
        for r in (week_data.get("fixes_impact") or [])
    )
    impact_html = (
        '<div class="card proof-card" style="margin-top:4px"><div class="card-head"><h3>Proof: did your pasted rules &amp; fixes help?</h3>'
        '<span class="pill ok">Closed-loop verification</span></div>'
        '<div class="tbl-wrap"><table><thead><tr><th>Rule / fix</th><th class="n">Last week</th>'
        '<th class="n">This week</th><th>Result</th></tr></thead>'
        f'<tbody>{impact_rows}</tbody></table></div></div>'
        if impact_rows else ""
    )

    # 7. Week-over-week + Week by week table
    wow_fragment = week.render_trend_html(week_data)
    wow_inner = re.sub(r'^<section id="trend">|</section>$', "", wow_fragment)
    peak_wk_usd = max((w["usd"] for w in by_week), default=0.0) or 0.01
    week_rows = "".join(
        f'<tr><td class="num">{_e(w["week"])}<div class="muted">{_e(w["week_start"])}</div></td>'
        f'<td class="bars"><span class="sp" style="width:{min(100.0, max(0.0, 100 * w["usd"] / peak_wk_usd)):.1f}%"></span>'
        f'<span class="ws" style="width:{min(100.0, max(0.0, 100 * w["waste_usd"] / peak_wk_usd)):.1f}%"></span></td>'
        f'<td class="n num">{w["chats"]}</td>'
        f'<td class="n num">{_e(_money(w["usd"]))}</td>'
        f'<td class="n num warn">{_e(_money(w["waste_usd"]))} ({w["waste_share_pct"]:.0f}%)</td>'
        f'<td class="n num bad">{w["blocked"] or "0"}</td>'
        f'<td class="n num warn">{w["asked"] or "0"}</td>'
        f'<td>{_session_links_html(w["session_ids"], sessions_by_id, limit=2)}</td></tr>'
        for w in reversed(by_week[-12:])
    )

    # 8. Sessions drill-down rows
    session_table_rows = []
    for r in sessions[:250]:
        sid_cell = (
            f'<a class="slink num" href="{_e(r["report_file"])}">{_e(r["short_id"])}</a>'
            if r["report_file"] else f'<span class="num">{_e(r["short_id"])}</span>'
        )
        g = r["guard"]
        g_txt = (
            f'<span class="bad">{g["blocked"]} stop</span> / <span class="warn">{g["asked"]} ask</span>'
            if g["guarded"] and (g["blocked"] or g["asked"])
            else '<span class="ok">guarded</span>' if g["guarded"] else '<span class="muted">—</span>'
        )
        session_table_rows.append(
            f'<tr id="{_e(r["anchor"])}" data-date="{_e(r["date"])}" data-project="{_e(r["project"])}" data-agent="{_e(r["agent"])}">'
            f'<td class="num">{_e(r["date"])}</td>'
            f'<td>{sid_cell}</td>'
            f'<td>{_e(r["agent_label"])}</td>'
            f'<td title="{_e(r["project_path"])}">{_e(r["project"])}</td>'
            f'<td>{_e(r["title"][:64])}{"…" if len(r["title"]) > 64 else ""}</td>'
            f'<td class="n num">{_e(_hours_str(r["working_min"]))}</td>'
            f'<td class="n num">{r["tools"]}</td>'
            f'<td class="n num">{_e(_tok(r["sent"]))}</td>'
            f'<td class="n num">{_e(_money(r["usd"]))}</td>'
            f'<td class="n num warn">{_e(_money(r["waste_usd"]))}</td>'
            f'<td>{g_txt}</td></tr>'
        )

    # Interactive payload embedded safely as application/json
    waste_catalog = {
        w["id"]: {
            "title": w["title"],
            "what": next((r["what"] for r in data["recommendations"] if r["id"] == w["id"]), w.get("how") or ""),
            "auto": bool(w.get("can_auto_fix")),
        }
        for w in data["wastes"]
    }
    client_payload = {
        "today": meta.get("generated_at", "")[:10],
        "initial_days": meta.get("days"),
        "sessions": [
            {
                "id": r["session_id"], "short": r["short_id"], "anchor": r["anchor"], "title": r["title"][:64],
                "date": r["date"], "week": r["week"], "month": r["date"][:7],
                "project": r["project"], "agent": r["agent"], "agent_label": r["agent_label"],
                "min": r["working_min"], "tools": r["tools"], "commits": r["commits"],
                "sent": r["sent"], "out": r["output"], "usd": r["usd"],
                "wusd": r["waste_usd"], "wtok": r["waste_tokens"],
                "fixes": r.get("fix_details") or [], "report": r["report_file"],
                "g": r["guard"],
            }
            for r in sessions
        ],
        "waste_catalog": waste_catalog,
        "agent_colors": AGENT_COLORS,
    }
    proj_options = "".join(f'<option value="{_e(p["project"])}">{_e(p["project"])}</option>' for p in data["by_project"])
    agent_pills = "".join(
        f'<button type="button" class="fpill" data-fagent="{_e(a["agent"])}">'
        f'<span class="dot" style="background:{AGENT_COLORS.get(a["agent"], "#64748b")}"></span>{_e(a["agent_label"])}</button>'
        for a in data["by_agent"]
    )

    return f"""<!doctype html><html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none';">
<title>SessionCost insights</title>
<script>try{{var th=localStorage.getItem('sessioncost-theme');if(th==='light'||th==='dark')document.documentElement.dataset.theme=th;}}catch(e){{}}</script>
<style>
:root{{--bg:oklch(0.22 0.015 258);--surface:oklch(0.265 0.016 258);--raise:oklch(0.315 0.018 258);--ink:oklch(0.96 0.005 255);
--ink-2:oklch(0.76 0.012 255);--line:oklch(0.37 0.018 258);--keep:oklch(0.64 0.17 265);--keep-ink:#0b1220;--keep-soft:oklch(0.33 0.06 265);
--cut:oklch(0.74 0.14 72);--cut-soft:oklch(0.35 0.05 72);--ok:oklch(0.72 0.14 155);--ok-soft:oklch(0.32 0.05 155);
--bad:oklch(0.70 0.19 22);--warn:oklch(0.78 0.14 72);--shadow:0 10px 28px rgb(0 0 0 / .28);color-scheme:dark}}
:root[data-theme="light"]{{--bg:#eef1f6;--surface:#ffffff;--raise:#e8ecf2;--ink:oklch(0.22 0.02 260);
--ink-2:oklch(0.46 0.015 260);--line:#d5dce5;--keep:oklch(0.51 0.16 265);--keep-ink:#ffffff;--keep-soft:oklch(0.93 0.04 265);
--cut:oklch(0.62 0.15 68);--cut-soft:oklch(0.94 0.05 78);--ok:oklch(0.44 0.14 155);--ok-soft:oklch(0.93 0.04 155);
--bad:oklch(0.54 0.20 25);--warn:oklch(0.49 0.14 68);--shadow:0 8px 24px rgb(16 24 40 / .06);color-scheme:light}}
*,*::before,*::after{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
:focus-visible{{outline:2px solid var(--keep);outline-offset:2px}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}
@keyframes barGrow{{from{{transform:scaleY(0)}}to{{transform:scaleY(1)}}}}
@keyframes flowDash{{from{{stroke-dashoffset:28}}to{{stroke-dashoffset:0}}}}
@keyframes pulseDot{{0%,100%{{transform:scale(1);opacity:.95}}50%{{transform:scale(1.35);opacity:.45}}}}
.layout{{display:grid;grid-template-columns:224px minmax(0,1fr);min-height:100vh}}
.sidebar{{position:sticky;top:0;height:100vh;padding:20px 14px;border-right:1px solid var(--line);background:var(--surface);display:flex;flex-direction:column;gap:4px;box-shadow:var(--shadow);z-index:2}}
.brand-row{{display:flex;align-items:center;justify-content:space-between;padding:0 6px 12px;border-bottom:1px solid var(--line);margin-bottom:8px}}
.brand{{font-weight:800;font-size:16px;letter-spacing:-.01em}}.brand span{{display:block;font-size:11.5px;font-weight:500;color:var(--ink-2)}}
.theme-btn{{width:34px;height:34px;border-radius:50%;border:1px solid var(--line);background:var(--raise);color:var(--ink);cursor:pointer;display:grid;place-items:center;font-size:15px;transition:transform .18s,border-color .18s}}
.theme-btn:hover{{border-color:var(--keep);color:var(--keep);transform:rotate(20deg)}}
.sidebar a{{display:flex;align-items:center;justify-content:space-between;padding:7px 10px;border-radius:8px;color:var(--ink);text-decoration:none;font-size:13.5px;font-weight:500;transition:background .15s,color .15s}}
.sidebar a:hover{{background:var(--raise);color:var(--keep)}}
.nav-foot{{margin-top:auto;padding:10px 8px 0;border-top:1px solid var(--line);font-size:11.5px;color:var(--ink-2)}}
main{{max-width:1180px;width:100%;margin:0 auto;padding:24px 24px 72px;display:grid;gap:22px}}
section{{scroll-margin-top:20px;display:grid;gap:12px;animation:fadeUp .4s ease-out both}}
h1{{font-size:24px;margin:0;letter-spacing:-.02em}}h2{{font-size:18px;margin:0;letter-spacing:-.01em}}h3{{font-size:15px;margin:0}}
p{{margin:0}}.muted{{color:var(--ink-2);font-size:13px}}.num{{font-variant-numeric:tabular-nums;font-family:ui-monospace,Consolas,monospace}}
code{{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;overflow-wrap:anywhere}}
.eyebrow{{font:700 11px/1.3 system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2)}}
.head-bar{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}}
.scope-badge{{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:999px;background:var(--surface);border:1px solid var(--line);font-size:12.5px;font-weight:600;box-shadow:var(--shadow)}}
.filter-bar{{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:10px 14px;box-shadow:var(--shadow)}}
.fgroup{{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}}
.fpill{{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:999px;padding:4px 10px;font-size:12.5px;font-weight:600;cursor:pointer;transition:all .15s}}
.fpill:hover{{border-color:var(--keep)}}.fpill.active{{background:var(--keep);border-color:var(--keep);color:var(--keep-ink)}}
.fselect{{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;padding:4px 8px;font:600 12.5px system-ui,sans-serif;max-width:100%}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:18px 20px;display:grid;gap:12px;box-shadow:var(--shadow)}}
.card-head{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}}
.row{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.btn{{border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:8px;padding:5px 11px;font:600 12.5px system-ui,sans-serif;cursor:pointer;transition:all .15s}}
.btn:hover{{border-color:var(--keep);color:var(--keep)}}
.btn-primary{{background:var(--keep);border-color:var(--keep);color:var(--keep-ink)}}.btn-primary:hover{{opacity:.92;color:var(--keep-ink)}}
.chip{{font:600 12px ui-monospace,Consolas,monospace;background:var(--cut-soft);color:var(--ink);border-radius:7px;padding:3px 9px;white-space:nowrap}}
.chip.ok-chip{{background:var(--ok-soft);color:var(--ok)}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid var(--line)}}
.pill.ok{{color:var(--ok);border-color:var(--ok)}}.pill.warn{{color:var(--warn);border-color:var(--warn)}}
.mini-pill{{display:inline-block;padding:1px 6px;border-radius:999px;font-size:10.5px;font-weight:700;background:var(--raise);color:var(--ink-2);margin-left:4px;vertical-align:1px}}
.mini-pill.ok{{background:var(--ok-soft);color:var(--ok)}}

/* Hero Deck: Efficiency Ring + What-If Simulator */
.hero-grid{{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(0,1fr);gap:16px;align-items:stretch}}
.score-body{{display:grid;grid-template-columns:168px minmax(0,1fr);gap:18px;align-items:center}}
.ring-svg{{width:160px;height:160px;display:block;margin:0 auto}}
.ring-arc{{transition:stroke-dashoffset .65s cubic-bezier(.22,1,.36,1),stroke .35s}}
.ring-val{{fill:var(--ink);font:800 34px ui-monospace,Consolas,monospace}}
.ring-sub{{fill:var(--ink-2);font:600 11.5px system-ui,sans-serif}}
.ring-proj{{fill:var(--ok);font:700 11px ui-monospace,Consolas,monospace}}
.hero-metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
.hm-box{{background:var(--bg);border:1px solid var(--line);border-radius:11px;padding:10px 12px}}
.hm-box b{{display:block;font-size:20px;line-height:1.2}}.hm-box span{{font-size:12px;color:var(--ink-2)}}
.hm-box.warn b{{color:var(--warn)}}.hm-box.ok b{{color:var(--ok)}}
.safety-strip{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 13px;border-radius:11px;border:1px solid var(--line);border-left:4px solid var(--ok);background:var(--bg);text-decoration:none;color:var(--ink);font-size:13px;font-weight:600;transition:background .15s}}
.safety-strip:hover{{background:var(--raise)}}.safety-strip.af-warn{{border-left-color:var(--bad)}}.safety-strip.af-muted{{border-left-color:var(--ink-2)}}
.pulse-dot{{width:8px;height:8px;border-radius:50%;background:var(--ok);display:inline-block;margin-right:8px;animation:pulseDot 2.2s ease-in-out infinite;flex:none}}
.safety-strip.af-warn .pulse-dot{{background:var(--bad)}}.safety-strip.af-muted .pulse-dot{{background:var(--ink-2);animation:none}}

/* What-If Simulator */
.sim-card{{background:linear-gradient(160deg,var(--surface) 60%,var(--keep-soft) 140%)}}
.sim-list{{display:grid;gap:7px;max-height:210px;overflow-y:auto;padding-right:2px}}
.sim-row{{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:9px;align-items:center;padding:8px 10px;border-radius:10px;background:var(--bg);border:1px solid var(--line);cursor:pointer;font-size:13px;transition:border-color .15s}}
.sim-row:hover{{border-color:var(--keep)}}
.sim-chk{{position:absolute;opacity:0;pointer-events:none}}
.sim-switch{{width:32px;height:18px;border-radius:999px;background:var(--line);position:relative;transition:background .2s}}
.sim-switch::after{{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:#fff;transition:transform .2s}}
.sim-chk:checked + .sim-switch{{background:var(--ok)}}
.sim-chk:focus-visible + .sim-switch{{outline:2px solid var(--keep);outline-offset:2px}}
.sim-chk:checked + .sim-switch::after{{transform:translateX(14px)}}
.sim-name{{font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.sim-save{{font-weight:700;color:var(--ok);font-size:12.5px}}
.sim-clean{{padding:14px;border-radius:10px;background:var(--ok-soft);color:var(--ok);font-weight:600;font-size:13.5px}}
.sim-cmd-bar{{display:flex;align-items:center;justify-content:space-between;gap:10px;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px 11px;margin-top:auto}}
.sim-cmd-bar code{{font-weight:600;color:var(--keep)}}

/* Spend vs Waste Bar */
.ba-wrap{{display:grid;gap:7px}}.ba-bar{{display:flex;height:20px;border-radius:999px;overflow:hidden;background:var(--raise);border:1px solid var(--line)}}
.ba-prod{{background:var(--keep);transition:width .45s cubic-bezier(.22,1,.36,1)}}
.ba-waste{{background:repeating-linear-gradient(135deg,var(--cut) 0 4px,var(--cut-soft) 4px 8px);transition:width .45s cubic-bezier(.22,1,.36,1)}}
.ba-legend{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 14px;font-size:12.5px}}
.sw{{width:11px;height:11px;border-radius:3px;display:inline-block;vertical-align:-1px;margin-right:5px}}
.sw-keep{{background:var(--keep)}}.sw-cut{{background:repeating-linear-gradient(135deg,var(--cut) 0 3px,var(--cut-soft) 3px 6px)}}

/* Sankey Money Flow */
.sankey-svg{{width:100%;height:auto;display:block}}
.svg-eyebrow{{fill:var(--ink-2);font:700 10.5px system-ui,sans-serif;letter-spacing:.09em}}
.sk-link{{opacity:.38;transition:opacity .2s}}.sk-link.sk-waste{{opacity:.52}}
.sk-link.dim{{opacity:.08}}.sk-link.hi{{opacity:.85}}
.sk-flow{{pointer-events:none;opacity:.75;animation:flowDash 1.4s linear infinite}}
.sk-click{{cursor:pointer}}.sk-click:hover rect{{filter:brightness(1.12)}}
.sk-lbl{{fill:var(--ink);font:600 12px system-ui,sans-serif}}.sk-sub{{fill:var(--ink-2);font:11px ui-monospace,Consolas,monospace}}
.sk-mid{{fill:var(--ink);font:600 11px system-ui,sans-serif}}

/* Heatmap Calendar */
.trend-split{{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(240px,.85fr);gap:16px;align-items:start}}
.heat-svg{{width:100%;max-width:260px;height:auto;display:block}}
.hm-cell{{transition:transform .12s,stroke .12s;cursor:pointer;stroke:transparent;stroke-width:1.5}}
.hm-cell:hover{{stroke:var(--ink)}}.hm-cell.active-day{{stroke:var(--keep);stroke-width:2}}
.hm-0{{fill:var(--raise);cursor:default}}
.hm-p1{{fill:oklch(0.76 0.08 265)}}.hm-p2{{fill:oklch(0.66 0.12 265)}}.hm-p3{{fill:oklch(0.56 0.15 265)}}.hm-p4{{fill:oklch(0.46 0.17 265)}}
.hm-w1{{fill:oklch(0.84 0.09 75)}}.hm-w2{{fill:oklch(0.75 0.13 72)}}.hm-w3{{fill:oklch(0.66 0.16 68)}}.hm-w4{{fill:oklch(0.58 0.18 40)}}
.heat-leg{{display:flex;align-items:center;gap:10px;font-size:11.5px;color:var(--ink-2);flex-wrap:wrap}}

/* Charts, Cards, Bars, Tables */
.callout{{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--cut);border-radius:14px;padding:14px 16px;display:grid;gap:8px;box-shadow:var(--shadow)}}
.callout.ok-border{{border-left-color:var(--ok)}}.callout-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}}
.fix-box{{display:flex;justify-content:space-between;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:8px 12px;font-size:13px}}
.fix-txt{{min-width:0}}.meta-line{{font-size:13px;color:var(--ink-2)}}.meta-line b{{color:var(--ink)}}
.chart-svg{{width:100%;height:auto;display:block}}
.svg-grid{{stroke:var(--line);stroke-dasharray:3 3}}.svg-ax{{fill:var(--ink-2);font:11px ui-monospace,Consolas,monospace}}
.svg-keep{{fill:var(--keep)}}.svg-cut-b{{stroke:var(--cut);stroke-width:.6}}
.bar-grow{{transform-box:fill-box;transform-origin:bottom;animation:barGrow .48s cubic-bezier(.22,1,.36,1) both}}
.empty-chart{{padding:28px;text-align:center;color:var(--ink-2);font-size:13.5px}}
.agents-grid{{display:grid;grid-template-columns:200px minmax(0,1fr);gap:18px;align-items:center}}
.donut-svg{{width:184px;height:184px;display:block;margin:0 auto}}
.donut-seg{{transition:stroke-dasharray .45s ease,opacity .2s}}.donut-seg:hover{{opacity:.82}}
.donut-val{{fill:var(--ink);font:700 18px ui-monospace,Consolas,monospace}}.donut-sub{{fill:var(--ink-2);font:11.5px system-ui,sans-serif}}
.agent-cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}}
.agent-card{{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:grid;gap:6px;cursor:pointer;transition:border-color .15s,transform .15s}}
.agent-card:hover{{border-color:var(--keep);transform:translateY(-1px)}}
.ac-head{{display:flex;align-items:center;gap:8px}}.ac-usd{{margin-left:auto;font-weight:700;font-size:16px}}
.ac-sub,.ac-foot{{font-size:12.5px;color:var(--ink-2)}}
.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;flex:none}}
.meter-row{{display:flex;align-items:center;gap:8px;font-size:12.5px}}
.meter{{flex:1;height:8px;border-radius:999px;background:var(--raise);overflow:hidden;display:flex}}
.meter-prod{{background:var(--keep)}}.meter-waste{{background:repeating-linear-gradient(135deg,var(--cut) 0 3px,var(--cut-soft) 3px 6px)}}
.hbar-list{{display:grid;gap:10px}}
.hbar-row,.wbar-row{{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:11px 14px;display:grid;gap:6px}}
.hbar-row{{cursor:pointer;transition:border-color .15s,transform .15s}}.hbar-row:hover{{border-color:var(--keep);transform:translateY(-1px)}}
.hbar-head{{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;font-size:13.5px}}
.hbar-track{{height:12px;border-radius:999px;background:var(--raise);overflow:hidden;display:flex}}
.hbar-prod{{background:var(--keep);transition:width .4s}}.hbar-waste{{background:repeating-linear-gradient(135deg,var(--cut) 0 3px,var(--cut-soft) 3px 6px);transition:width .4s}}
.hbar-bad{{background:var(--bad)}}.hbar-foot{{font-size:12px;color:var(--ink-2)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}}
.tile b{{display:block;font-size:21px;line-height:1.25}}.tile span{{color:var(--ink-2);font-size:12.5px}}
.tile.warn b{{color:var(--warn)}}.tile.ok b{{color:var(--ok)}}.tile.bad b{{color:var(--bad)}}
.rbar{{display:grid;grid-template-columns:minmax(120px,1.2fr) minmax(80px,1.5fr) auto;gap:8px;align-items:center;font-size:13px}}
.rank-num{{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--keep-soft);color:var(--keep);font:700 12px ui-monospace,Consolas,monospace;margin-right:6px}}
.safety-cta{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px;border-left:4px solid var(--keep)}}
.proof-card{{border-left:4px solid var(--ok)}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:14px}}
.tbl-wrap,.wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--ink-2);font-weight:600;font-size:12px;white-space:nowrap}}td.n,th.n{{text-align:right}}tr:last-child td{{border-bottom:0}}
tr:target{{background:oklch(0.55 0.12 265 / .18)}}
.slink{{display:inline-block;padding:1px 6px;border-radius:5px;border:1px solid var(--line);color:var(--ink);text-decoration:none;font-size:12px;margin:1px 0}}
.slink:hover{{border-color:var(--keep);color:var(--keep)}}
.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}
.bars{{width:24%;min-width:90px}}.bars span{{display:block;height:6px;border-radius:3px;margin:2px 0}}.bars .sp{{background:var(--keep)}}.bars .ws{{background:var(--cut)}}
.tbars{{display:grid;gap:8px;margin:8px 0}}.tbar{{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:13px}}
.tbar .track{{display:flex;flex-direction:column;gap:4px;margin:6px 0}}.tbar i{{display:block;height:8px;border-radius:4px}}
.tbar i.spend{{background:var(--keep)}}.tbar i.waste{{background:var(--cut)}}
.verdict{{background:var(--bg);border:1px solid var(--line);border-left:4px solid var(--keep);border-radius:10px;padding:11px 14px;font-size:13.5px}}
.verdict.ok{{border-left-color:var(--ok)}}.verdict.warn{{border-left-color:var(--cut)}}
details.drawer summary{{cursor:pointer;font-weight:700;font-size:16px;list-style:none;display:flex;align-items:center;justify-content:space-between}}
details.drawer summary::-webkit-details-marker{{display:none}}
.search-in{{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;padding:6px 10px;font:13px system-ui,sans-serif;width:min(320px,100%)}}
@media (max-width:980px){{.hero-grid,.trend-split{{grid-template-columns:1fr}}}}
@media (max-width:860px){{.layout{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto;flex-direction:row;flex-wrap:wrap;border-right:0;border-bottom:1px solid var(--line)}}.nav-foot{{display:none}}.agents-grid,.score-body{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;transition:none!important}}}}
@media (forced-colors:active){{.ba-prod,.ba-waste,.meter-prod,.meter-waste,.hbar-prod,.hbar-waste,.hbar-bad,.sim-switch,.dot,.pulse-dot{{forced-color-adjust:none;border:1px solid CanvasText}}.fpill.active,.btn-primary{{border:2px solid Highlight}}}}
</style></head><body><div class="layout">
<nav class="sidebar" aria-label="Insights sections">
<div class="brand-row"><div class="brand">SessionCost<span>Cost + Safety Control Plane</span></div>
<button type="button" class="theme-btn" id="themeBtn" title="Switch light / dark theme" aria-label="Switch light or dark theme">◐</button></div>
<a href="#summary">Overview &amp; Simulator</a>
<a href="#flow">Money flow</a>
<a href="#trend">Activity &amp; Trend</a>
<a href="#agents">Agents</a>
<a href="#projects">Projects</a>
<a href="#waste">Waste drivers</a>
<a href="#recommendations">Action playbook</a>
<a href="#safety">Safety (LiveGuard)</a>
<a href="#sessions">Sessions <span class="num muted" id="navSessCount">{len(sessions)}</span></a>
<div class="nav-foot">100% local · zero cloud · zero telemetry</div>
</nav>
<main>
<section id="summary">
<div class="head-bar">
  <div><h1>SessionCost insights</h1><p class="muted">Measure token waste, simulate fixes before running them, and verify safety across all coding agents</p></div>
  <span class="scope-badge" id="scopeBadge">{_e(range_label)}</span>
</div>

<div class="filter-bar" aria-label="Dashboard filters">
  <div class="fgroup" id="rangeGroup">
    <span class="eyebrow" style="margin-right:4px">Window</span>
    <button type="button" class="fpill{' active' if meta.get('days') == 7 else ''}" data-days="7">7d</button>
    <button type="button" class="fpill{' active' if meta.get('days') == 30 else ''}" data-days="30">30d</button>
    <button type="button" class="fpill" data-days="month">This month</button>
    <button type="button" class="fpill{' active' if not meta.get('days') else ''}" data-days="all">All</button>
  </div>
  <div class="fgroup">
    <span class="eyebrow" style="margin-right:4px">Project</span>
    <select id="projSelect" class="fselect" aria-label="Filter by project"><option value="">All projects</option>{proj_options}</select>
  </div>
  <div class="fgroup" id="agentGroup">
    <span class="eyebrow" style="margin-right:4px">Agent</span>
    <button type="button" class="fpill active" data-fagent="">All</button>
    {agent_pills}
  </div>
  <button type="button" class="btn" id="resetFilters" style="margin-left:auto;display:none">Reset filters</button>
</div>

<div class="hero-grid">
  <div class="card">
    <div class="card-head">
      <span class="eyebrow">Efficiency score &amp; spend</span>
      <span class="num muted" id="baHeadline">{_e(_money(tot_usd))} total · {_e(_money(after_usd))} after fixes</span>
    </div>
    <div class="score-body">
      <div id="effRingWrap">{ring_svg}</div>
      <div class="hero-metrics">
        <div class="hm-box"><b class="num" id="kpiUsd" data-count="{tot_usd:.4f}">{_e(_money(tot_usd))}</b><span id="kpiTok">{_e(_tok(s["sent"]))} sent · {_e(_tok(s["output"]))} written</span></div>
        <div class="hm-box {'warn' if waste_usd > 0 else 'ok'}"><b class="num" id="kpiWaste" data-count="{waste_usd:.4f}">{_e(_money(waste_usd))}</b><span id="kpiWasteSub">avoidable waste ({waste_pct:.0f}%)</span></div>
        <div class="hm-box ok"><b class="num" id="kpiAfter" data-count="{after_usd:.4f}">{_e(_money(after_usd))}</b><span id="kpiAfterSub">projected bill after fixes</span></div>
        <div class="hm-box"><b class="num" id="kpiChats">{s["chats"]} chats</b><span id="kpiAct">{_e(_hours_str(s["working_min"]))} · {s["projects"]} proj · {s["agents"]} agents</span></div>
      </div>
    </div>
    <div class="ba-wrap">
      <div class="ba-bar"><span class="ba-prod" id="baProd" style="width:{prod_pct:.1f}%"></span><span class="ba-waste" id="baWaste" style="width:{waste_pct:.1f}%"></span></div>
      <div class="ba-legend">
        <span><i class="sw sw-keep"></i>Productive: <b class="num" id="baProdVal">{_e(_money(after_usd))}</b></span>
        <span><i class="sw sw-cut"></i>Waste: <b class="num warn" id="baWasteVal">{_e(_money(waste_usd))}</b></span>
      </div>
    </div>
    <a class="safety-strip {safety_cls}" href="#safety">
      <span><i class="pulse-dot"></i><span id="heroSafety">{_e(safety_line)}</span></span>
      <span class="num muted" style="white-space:nowrap">{_e(safety_badge)} ↓</span>
    </a>
  </div>

  <div class="card sim-card">
    <div class="card-head">
      <div><span class="eyebrow">What-If Savings Simulator</span><h3>Toggle fixes to preview your savings</h3></div>
      <span class="chip ok-chip num" id="simBadge">Save {_e(_money(sim_save_init))}</span>
    </div>
    <div class="sim-list" id="simList">{sim_rows_html}</div>
    <div class="sim-cmd-bar">
      <div class="fix-txt"><span class="eyebrow" style="display:block">Run to apply selected fixes</span><code id="simCmd">{_e(init_cmd)}</code></div>
      <button type="button" class="btn btn-primary copy-btn" id="simCopyBtn" data-copy="{_e(init_cmd)}">Copy command</button>
    </div>
  </div>
</div>

{biggest_html}
<p class="muted">Prices: {_e(meta["prices_source"])}.{f" {_e(meta['prices_stale'])}" if meta["prices_stale"] else ""}
Counts: {s["turns"]} turns · {s["calls"]} model calls · {s["tools"]} tool calls · {s["commits"]} git commits.</p>
</section>

<section id="flow">
<div class="card">
  <div class="card-head">
    <div><h2>Where your money went</h2><span class="muted">Animated flow from Agents → Projects → Productive spend vs. Avoidable waste (click a node to filter)</span></div>
    <span class="muted"><i class="sw sw-keep"></i>Productive &nbsp; <i class="sw sw-cut"></i>Animated waste leak</span>
  </div>
  <div id="sankeyWrap">{sankey_svg}</div>
</div>
</section>

<section id="trend">
<div class="trend-split">
  <div class="card">
    <div class="card-head">
      <div><h2>Spend &amp; waste over time</h2><span class="muted"><i class="sw sw-keep"></i>Productive &nbsp; <i class="sw sw-cut"></i>Waste</span></div>
      <div class="fgroup" id="granGroup">
        <button type="button" class="fpill active" data-gran="day">By day</button>
        <button type="button" class="fpill" data-gran="week">By week</button>
        <button type="button" class="fpill" data-gran="month">By month</button>
      </div>
    </div>
    <div id="timeChart">{time_svg}</div>
  </div>
  <div class="card">
    <div class="card-head"><h2>Waste heatmap</h2><span class="muted">Last 12 weeks (click day)</span></div>
    <div id="heatmapWrap">{heatmap_svg}</div>
    <div class="heat-leg"><span><i class="sw sw-keep"></i>Clean day</span><span><i class="sw sw-cut"></i>High-waste day (≥25%)</span></div>
  </div>
</div>
<div class="card">
  {wow_inner}
  <h3 style="margin-top:8px">Week by week: cheaper &amp; safer?</h3>
  <div class="tbl-wrap"><table><thead><tr><th>Week</th><th></th><th class="n">Chats</th><th class="n">Spend</th><th class="n">Waste</th><th class="n">Stopped</th><th class="n">Asked</th><th>Sessions</th></tr></thead>
  <tbody>{week_rows or '<tr><td colspan="8" class="muted">No weekly trend data yet.</td></tr>'}</tbody></table></div>
</div>
</section>

<section id="agents">
<div class="card">
  <div class="card-head"><h2>Agents side by side</h2><span class="muted">Click any agent card to filter the dashboard</span></div>
  <div class="agents-grid">
    <div id="agentDonut">{donut_svg}</div>
    <div class="agent-cards" id="agentCards">{"".join(agent_cards) or '<p class="muted">No agent activity in this window.</p>'}</div>
  </div>
</div>
</section>

<section id="projects">
<div class="card">
  <div class="card-head"><h2>Projects</h2><span class="muted">Click any project bar to filter the dashboard to that project</span></div>
  <div class="hbar-list" id="projBars">{"".join(proj_bars) or '<p class="muted">No projects in this window.</p>'}</div>
</div>
</section>

<section id="waste">
<div class="card">
  <div class="card-head"><h2>Top waste drivers</h2><span class="muted">Ranked by measured dollar saving across chats</span></div>
  <div class="hbar-list" id="wasteBars">{"".join(waste_bars) or '<p class="muted">No built-in waste checks fired across these sessions.</p>'}</div>
</div>
</section>

<section id="recommendations">
<h2>Action playbook (ranked by measured saving)</h2>
{impact_html}
{recs_html}
</section>

<section id="safety">
<h2>Safety (LiveGuard)</h2>
{safety_body}
</section>

<section id="sessions">
<details class="card drawer" id="sessionsDrawer">
  <summary><span>Sessions drill-down (<span id="sessCount">{len(sessions)}</span>)</span><span class="muted" style="font-weight:500">Click to expand / search</span></summary>
  <div class="row" style="margin-top:8px"><input type="search" id="sessSearch" class="search-in" aria-label="Filter sessions by title, project, agent, or session id" placeholder="Filter by title, project, agent, or session id…"></div>
  <div class="tbl-wrap"><table><thead><tr><th>Date</th><th>Session</th><th>Agent</th><th>Project</th><th>First message</th><th class="n">Time</th><th class="n">Tools</th><th class="n">Sent</th><th class="n">Spend</th><th class="n">Waste</th><th>Guard</th></tr></thead>
  <tbody id="sessBody">{"".join(session_table_rows) or '<tr><td colspan="11" class="muted">No sessions found.</td></tr>'}</tbody></table></div>
</details>
</section>
</main></div>
<script type="application/json" id="sc-data">{_json_for_script(client_payload)}</script>
<script>
(function(){{
  var raw = document.getElementById('sc-data');
  if (!raw) return;
  var DATA = JSON.parse(raw.textContent);
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var state = {{ days: DATA.initial_days ? String(DATA.initial_days) : 'all', project: '', agent: '', gran: 'day', day: '', q: '' }};
  var simOff = {{}};

  function money(v){{ v = Number(v)||0; if(v>=1) return '$'+v.toFixed(2); if(v>=0.01) return '$'+v.toFixed(3); return '$'+v.toFixed(2); }}
  function tok(n){{ n = Number(n)||0; if(n>=1e6) return (n/1e6).toFixed(1)+'M'; if(n>=1e3) return Math.round(n/1e3)+'k'; return String(n); }}
  function hrs(m){{ m = Number(m)||0; return m>=60 ? (m/60).toFixed(1)+'h' : Math.round(m)+'m'; }}
  function el(tag, attrs, kids){{
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function(k){{
      if (k==='class') n.className = attrs[k];
      else if (k==='text') n.textContent = attrs[k];
      else if (k==='style' && typeof attrs[k]==='object') Object.assign(n.style, attrs[k]);
      else n.setAttribute(k, attrs[k]);
    }});
    (kids||[]).forEach(function(c){{ if (c!=null) n.appendChild(typeof c==='string' ? document.createTextNode(c) : c); }});
    return n;
  }}
  function sel(tag, attrs, kids){{
    var n = document.createElementNS(SVG_NS, tag);
    if (attrs) Object.keys(attrs).forEach(function(k){{
      if (k==='text') n.textContent = attrs[k];
      else n.setAttribute(k, String(attrs[k]));
    }});
    (kids||[]).forEach(function(c){{ if (c!=null) n.appendChild(typeof c==='string' ? document.createTextNode(c) : c); }});
    return n;
  }}

  var animCanceled = false;
  // Smooth count-up animation on first load (canceled immediately if user filters)
  if (!window.matchMedia || !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
    ['kpiUsd','kpiWaste','kpiAfter'].forEach(function(id){{
      var node = document.getElementById(id);
      if (!node) return;
      var target = parseFloat(node.getAttribute('data-count') || '0');
      if (!(target > 0)) return;
      var t0 = performance.now(), dur = 520;
      (function step(now){{
        if (animCanceled) return;
        var p = Math.min(1, (now - t0) / dur);
        var ease = 1 - Math.pow(1 - p, 3);
        node.textContent = money(target * ease);
        if (p < 1) requestAnimationFrame(step);
      }})(t0);
    }});
  }}

  // Theme toggle
  var tBtn = document.getElementById('themeBtn');
  if (tBtn) tBtn.addEventListener('click', function(){{
    var cur = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = cur;
    try{{ localStorage.setItem('sessioncost-theme', cur); }}catch(e){{}}
  }});

  // Copy buttons + session links
  document.addEventListener('click', function(e){{
    var b = e.target.closest('.copy-btn');
    if (b && navigator.clipboard && navigator.clipboard.writeText) {{
      if (!b.getAttribute('data-orig')) b.setAttribute('data-orig', b.textContent);
      navigator.clipboard.writeText(b.getAttribute('data-copy') || '').then(function(){{
        b.textContent = 'Copied ✓';
        clearTimeout(b._copyTimer);
        b._copyTimer = setTimeout(function(){{ b.textContent = b.getAttribute('data-orig') || 'Copy'; }}, 1400);
      }}).catch(function(){{}});
    }}
    var link = e.target.closest('a[href^="#session-"]');
    if (link) {{ var dr = document.getElementById('sessionsDrawer'); if (dr) dr.open = true; }}
  }});

  function filtered(){{
    var ref = DATA.today || (DATA.sessions[0] && DATA.sessions[0].date) || '';
    var cutoff = '';
    if (state.days === '7' || state.days === '30') {{
      var d = new Date(ref + 'T00:00:00Z');
      if (!isNaN(d.getTime())) {{ d.setUTCDate(d.getUTCDate() - Number(state.days)); cutoff = d.toISOString().slice(0,10); }}
    }} else if (state.days === 'month' && ref.length >= 7) {{
      cutoff = ref.slice(0,7) + '-01';
    }}
    return DATA.sessions.filter(function(r){{
      if (cutoff && r.date < cutoff) return false;
      if (state.project && r.project !== state.project) return false;
      if (state.agent && r.agent !== state.agent) return false;
      if (state.day && r.date !== state.day) return false;
      return true;
    }});
  }}

  function updateRing(score, proj){{
    var circ = 2 * Math.PI * 62;
    var sc = Math.max(0, Math.min(100, score));
    var pr = Math.max(sc, Math.min(100, proj));
    var col = sc >= 80 ? 'var(--ok)' : (sc >= 60 ? 'var(--cut)' : 'var(--bad)');
    var arc = document.getElementById('ringScoreArc');
    var parc = document.getElementById('ringProjArc');
    if (arc) {{ arc.setAttribute('stroke-dashoffset', (circ*(1-sc/100)).toFixed(2)); arc.setAttribute('stroke', col); }}
    if (parc) parc.setAttribute('stroke-dashoffset', (circ*(1-pr/100)).toFixed(2));
    var v = document.getElementById('ringScoreVal'), l = document.getElementById('ringScoreLbl'), pl = document.getElementById('ringProjLbl');
    if (v) v.textContent = String(Math.round(sc));
    if (l) l.textContent = sc >= 80 ? 'Efficient' : (sc >= 60 ? 'Moderate waste' : 'High waste');
    if (pl) pl.textContent = pr > sc + 0.5 ? ('→ ' + Math.round(pr) + ' with fixes') : 'clean score';
  }}

  function buildBarsSvg(rows){{
    var map = {{}};
    rows.forEach(function(r){{
      var k = state.gran === 'month' ? r.month : (state.gran === 'week' ? r.week : r.date);
      var b = map[k] || (map[k] = {{ k: k, usd: 0, w: 0, n: 0 }});
      b.usd += r.usd; b.w += r.wusd; b.n += 1;
    }});
    var items = Object.keys(map).sort().slice(-24).map(function(k){{ return map[k]; }});
    if (!items.length) return el('div', {{ class: 'empty-chart', text: 'No activity matches the current filter.' }});
    var peak = Math.max.apply(null, items.map(function(x){{ return x.usd; }})) || 0.01;
    var W=760, H=210, pL=54, pR=14, pT=16, pB=34, cW=W-pL-pR, cH=H-pT-pB, n=items.length, slot=cW/n, bw=Math.max(8, Math.min(32, slot*0.64));
    var pat = sel('pattern', {{ id:'wasteHatch', width:6, height:6, patternTransform:'rotate(45)', patternUnits:'userSpaceOnUse' }}, [
      sel('rect', {{ width:6, height:6, fill:'var(--cut-soft)' }}),
      sel('line', {{ x1:0, y1:0, x2:0, y2:6, stroke:'var(--cut)', 'stroke-width':3 }})
    ]);
    var svg = sel('svg', {{ viewBox:'0 0 '+W+' '+H, class:'chart-svg' }}, [sel('defs', null, [pat])]);
    [0, 0.5, 1].forEach(function(f){{
      var y = pT + cH*(1-f);
      svg.appendChild(sel('line', {{ x1:pL, y1:y.toFixed(1), x2:W-pR, y2:y.toFixed(1), class:'svg-grid' }}));
      svg.appendChild(sel('text', {{ x:pL-8, y:(y+4).toFixed(1), 'text-anchor':'end', class:'svg-ax', text:money(peak*f) }}));
    }});
    var step = Math.max(1, Math.ceil(n/8));
    items.forEach(function(b, i){{
      var w = Math.min(b.usd, b.w), p = Math.max(0, b.usd - w);
      var ph = (p/peak)*cH, wh = (w/peak)*cH, x = pL + i*slot + (slot-bw)/2, yp = pT + cH - ph, yw = yp - wh;
      var sl = (state.gran==='day' && b.k.length===10) ? b.k.slice(5) : b.k;
      var g = sel('g', null, [sel('title', {{ text: b.k+': '+money(b.usd)+' spend, '+money(w)+' waste ('+b.n+' chats)' }})]);
      if (ph>0.5) g.appendChild(sel('rect', {{ x:x.toFixed(1), y:yp.toFixed(1), width:bw.toFixed(1), height:ph.toFixed(1), rx:3, class:'svg-keep bar-grow' }}));
      if (wh>0.5) g.appendChild(sel('rect', {{ x:x.toFixed(1), y:yw.toFixed(1), width:bw.toFixed(1), height:wh.toFixed(1), rx:3, fill:'url(#wasteHatch)', class:'svg-cut-b bar-grow' }}));
      if (i%step===0 || i===n-1) g.appendChild(sel('text', {{ x:(x+bw/2).toFixed(1), y:H-10, 'text-anchor':'middle', class:'svg-ax', text:sl }}));
      svg.appendChild(g);
    }});
    return svg;
  }}

  function buildDonutSvg(agents, total){{
    if (!agents.length || total <= 0) return el('div', {{ class: 'empty-chart', text: 'No agent spend in filter.' }});
    var cx=92, cy=92, r=68, circ=2*Math.PI*r, off=0;
    var svg = sel('svg', {{ viewBox:'0 0 184 184', class:'donut-svg' }}, [
      sel('circle', {{ cx:92, cy:92, r:68, fill:'none', stroke:'var(--raise)', 'stroke-width':22 }})
    ]);
    agents.forEach(function(a){{
      if (a.usd<=0) return;
      var d = (a.usd/total)*circ, col = DATA.agent_colors[a.agent] || '#64748b';
      svg.appendChild(sel('circle', {{
        cx:92, cy:92, r:68, fill:'none', stroke:col, 'stroke-width':22, class:'donut-seg',
        'stroke-dasharray':d.toFixed(2)+' '+circ.toFixed(2), 'stroke-dashoffset':(-off).toFixed(2),
        transform:'rotate(-90 92 92)'
      }}, [sel('title', {{ text: a.label+': '+money(a.usd) }})]));
      off += d;
    }});
    svg.appendChild(sel('text', {{ x:92, y:88, 'text-anchor':'middle', class:'donut-val', text:money(total) }}));
    svg.appendChild(sel('text', {{ x:92, y:107, 'text-anchor':'middle', class:'donut-sub', text:'total spend' }}));
    return svg;
  }}

  function buildSankeySvg(rows){{
    var total = rows.reduce(function(s,r){{ return s + Math.max(0, r.usd); }}, 0);
    if (!rows.length || total <= 0) return el('div', {{ class:'empty-chart', text:'No spend flow in this filter.' }});
    var ag={{}}, pr={{}}, links={{}};
    rows.forEach(function(r){{
      var u = Math.max(0, r.usd); if (u<=0) return;
      var w = Math.min(u, Math.max(0, r.wusd));
      var a = ag[r.agent] || (ag[r.agent] = {{ key:r.agent, label:r.agent_label, usd:0 }}); a.usd += u;
      var p = pr[r.project] || (pr[r.project] = {{ key:r.project, label:r.project, usd:0, waste:0 }}); p.usd += u; p.waste += w;
      var lk = r.agent + '\0' + r.project; links[lk] = (links[lk]||0) + u;
    }});
    var agents = Object.values(ag).sort(function(a,b){{ return b.usd-a.usd; }}).slice(0,6);
    var pAll = Object.values(pr).sort(function(a,b){{ return b.usd-a.usd; }});
    var projs = pAll;
    if (pAll.length > 5) {{
      var top5 = pAll.slice(0,5), rest = pAll.slice(5), otherSet = {{}};
      var ou=0, ow=0; rest.forEach(function(x){{ ou+=x.usd; ow+=x.waste; otherSet[x.key]=1; }});
      projs = top5.concat([{{ key:'__other__', label:'Other ('+rest.length+')', usd:ou, waste:ow }}]);
      var fl = {{}};
      Object.keys(links).forEach(function(k){{
        var parts = k.split('\0'), nk = parts[0] + '\0' + (otherSet[parts[1]] ? '__other__' : parts[1]);
        fl[nk] = (fl[nk]||0) + links[k];
      }});
      links = fl;
    }}
    var tw = projs.reduce(function(s,p){{ return s+p.waste; }}, 0), tp = Math.max(0, total-tw);
    var outs = [];
    if (tp>0) outs.push({{ key:'productive', label:'Productive', usd:tp, color:'var(--keep)' }});
    if (tw>0) outs.push({{ key:'waste', label:'Avoidable waste', usd:tw, color:'var(--cut)' }});

    var W=820, H=260, padY=28, gap=10, nw=14, x1=148, x2=403, x3=658, availH=H-2*padY;
    function layout(nodes, x){{
      var sVal = nodes.reduce(function(s,n){{ return s+n.usd; }}, 0) || 1;
      var usable = Math.max(40, availH - gap*Math.max(0, nodes.length-1)), y = padY, res = {{}};
      nodes.forEach(function(n){{
        var nh = Math.max(6, (n.usd/sVal)*usable);
        res[n.key] = {{ key:n.key, label:n.label, usd:n.usd, waste:n.waste||0, color:n.color, x:x, y:y, h:nh, outY:y, inY:y }};
        y += nh + gap;
      }});
      return res;
    }}
    var aP = layout(agents, x1), pP = layout(projs, x2), oP = layout(outs, x3);
    var svg = sel('svg', {{ viewBox:'0 0 '+W+' '+H, class:'sankey-svg' }}, [
      sel('text', {{ x:x1+7, y:16, 'text-anchor':'middle', class:'svg-eyebrow', text:'AGENTS' }}),
      sel('text', {{ x:x2+7, y:16, 'text-anchor':'middle', class:'svg-eyebrow', text:'PROJECTS' }}),
      sel('text', {{ x:x3+7, y:16, 'text-anchor':'middle', class:'svg-eyebrow', text:'OUTCOME' }})
    ]);
    agents.forEach(function(a){{
      var ap = aP[a.key], col = DATA.agent_colors[a.key] || '#64748b';
      projs.forEach(function(p){{
        var val = links[a.key+'\0'+p.key] || 0; if (val<=0) return;
        var pp = pP[p.key], bh = Math.max(2, (val/ap.usd)*ap.h), bh2 = Math.max(2, (val/pp.usd)*pp.h);
        var y0 = ap.outY+bh/2, y1 = pp.inY+bh2/2; ap.outY+=bh; pp.inY+=bh2;
        var sw = Math.max(2, (bh+bh2)/2), mx = (x1+nw+x2)/2;
        svg.appendChild(sel('path', {{
          d:'M'+(x1+nw)+','+y0.toFixed(1)+' C'+mx+','+y0.toFixed(1)+' '+mx+','+y1.toFixed(1)+' '+x2+','+y1.toFixed(1),
          fill:'none', stroke:col, 'stroke-width':sw.toFixed(1), class:'sk-link', 'data-agent':a.key, 'data-project':p.key
        }}, [sel('title', {{ text:a.label+' → '+p.label+': '+money(val) }})]));
      }});
    }});
    projs.forEach(function(p){{
      var pp = pP[p.key];
      [['productive', Math.max(0, p.usd-p.waste), 'var(--keep)', false], ['waste', p.waste, 'var(--cut)', true]].forEach(function(it){{
        var ok=it[0], val=it[1], col=it[2], isW=it[3];
        if (val<=0 || !oP[ok]) return;
        var op = oP[ok], bh = Math.max(2, (val/pp.usd)*pp.h), bh2 = Math.max(2, (val/op.usd)*op.h);
        var y0 = pp.outY+bh/2, y1 = op.inY+bh2/2; pp.outY+=bh; op.inY+=bh2;
        var sw = Math.max(2, (bh+bh2)/2), mx = (x2+nw+x3)/2;
        var dStr = 'M'+(x2+nw)+','+y0.toFixed(1)+' C'+mx+','+y0.toFixed(1)+' '+mx+','+y1.toFixed(1)+' '+x3+','+y1.toFixed(1);
        svg.appendChild(sel('path', {{
          d:dStr, fill:'none', stroke:col, 'stroke-width':sw.toFixed(1), class:isW?'sk-link sk-waste':'sk-link', 'data-project':p.key
        }}, [sel('title', {{ text:p.label+' → '+(isW?'Avoidable waste':'Productive')+': '+money(val) }})]));
        if (isW) svg.appendChild(sel('path', {{
          d:dStr, fill:'none', stroke:'var(--bg)', 'stroke-width':Math.max(1.5, sw*0.28).toFixed(1),
          'stroke-dasharray':'4 10', class:'sk-flow', 'data-project':p.key
        }}));
      }});
    }});
    agents.forEach(function(a){{
      var ap = aP[a.key], col = DATA.agent_colors[a.key] || '#64748b';
      svg.appendChild(sel('g', {{ class:'sk-node sk-click', 'data-fagent':a.key, tabindex:'0', role:'button', 'aria-label':'Filter by '+a.label }}, [
        sel('title', {{ text:'Click to filter by '+a.label+' ('+money(a.usd)+')' }}),
        sel('rect', {{ x:ap.x, y:ap.y.toFixed(1), width:nw, height:ap.h.toFixed(1), rx:4, fill:col }}),
        sel('text', {{ x:ap.x-8, y:(ap.y+ap.h/2-2).toFixed(1), 'text-anchor':'end', class:'sk-lbl', text:a.label.slice(0,18) }}),
        sel('text', {{ x:ap.x-8, y:(ap.y+ap.h/2+12).toFixed(1), 'text-anchor':'end', class:'sk-sub', text:money(a.usd) }})
      ]));
    }});
    projs.forEach(function(p){{
      var pp = pP[p.key], isO = p.key==='__other__';
      var attrs = isO ? {{ class:'sk-node' }} : {{ class:'sk-node sk-click', 'data-fproj':p.key, tabindex:'0', role:'button', 'aria-label':'Filter by '+p.label }};
      svg.appendChild(sel('g', attrs, [
        sel('title', {{ text:p.label+': '+money(p.usd)+' ('+money(p.waste)+' waste)' }}),
        sel('rect', {{ x:pp.x, y:pp.y.toFixed(1), width:nw, height:pp.h.toFixed(1), rx:4, fill:'var(--ink-2)' }}),
        sel('text', {{ x:pp.x+nw/2, y:(pp.y-5).toFixed(1), 'text-anchor':'middle', class:'sk-mid', text:p.label.slice(0,20) }})
      ]));
    }});
    outs.forEach(function(o){{
      var op = oP[o.key], pct = Math.round(100*o.usd/total);
      svg.appendChild(sel('g', {{ class:'sk-node' }}, [
        sel('rect', {{ x:op.x, y:op.y.toFixed(1), width:nw, height:op.h.toFixed(1), rx:4, fill:o.color }}),
        sel('text', {{ x:op.x+nw+8, y:(op.y+op.h/2-2).toFixed(1), 'text-anchor':'start', class:'sk-lbl', text:o.label }}),
        sel('text', {{ x:op.x+nw+8, y:(op.y+op.h/2+12).toFixed(1), 'text-anchor':'start', class:'sk-sub', text:money(o.usd)+' ('+pct+'%)' }})
      ]));
    }});
    return svg;
  }}

  function renderSimulator(wList, totalUsd, wasteUsd){{
    var list = document.getElementById('simList');
    if (!list) return;
    var top = wList.slice(0, 6);
    var saved = 0, autoIds = [];
    if (!top.length) {{
      list.replaceChildren(el('div', {{ class:'sim-clean', text:'Zero waste triggers in this window — your agent workflow is already lean!' }}));
    }} else {{
      var nodes = top.map(function(w){{
        var cat = DATA.waste_catalog[w.id] || {{ title:w.id, auto:false }};
        var checked = !simOff[w.id];
        if (checked) {{ saved += w.usd; if (cat.auto) autoIds.push(w.id); }}
        var chk = el('input', {{ type:'checkbox', class:'sim-chk', 'data-wid':w.id }});
        chk.checked = checked;
        var pill = el('span', {{ class: cat.auto ? 'mini-pill ok' : 'mini-pill', text: cat.auto ? 'Auto-fix' : 'Guide' }});
        var nameSpan = el('span', {{ class:'sim-name' }}, [cat.title + ' ', pill]);
        return el('label', {{ class:'sim-row' }}, [
          chk,
          el('span', {{ class:'sim-switch', 'aria-hidden':'true' }}),
          nameSpan,
          el('span', {{ class:'num sim-save', text:'-'+money(w.usd) }})
        ]);
      }});
      list.replaceChildren.apply(list, nodes);
    }}
    saved = Math.min(wasteUsd, saved);
    var curScore = totalUsd > 0 ? Math.max(0, 100*(totalUsd-wasteUsd)/totalUsd) : 100;
    var projScore = totalUsd > 0 ? Math.min(100, 100*(totalUsd-wasteUsd+saved)/totalUsd) : 100;
    var projBill = Math.max(0, totalUsd - saved);
    updateRing(curScore, projScore);
    var sb = document.getElementById('simBadge');
    if (sb) sb.textContent = saved > 0 ? ('Save ' + money(saved)) : 'No fixes selected';
    var ka = document.getElementById('kpiAfter');
    if (ka) ka.textContent = money(projBill);
    var cmd = autoIds.length ? ('sessioncost fix --only ' + autoIds.join(',')) : '/sessioncost fix';
    var sc = document.getElementById('simCmd'), scb = document.getElementById('simCopyBtn');
    if (sc) sc.textContent = cmd;
    if (scb) scb.setAttribute('data-copy', cmd);
  }}

  function render(){{
    var rows = filtered();
    var usd=0, wusd=0, sent=0, outT=0, mins=0, projs={{}}, ags={{}}, wastes={{}};
    rows.forEach(function(r){{
      usd += r.usd; wusd += r.wusd; sent += r.sent; outT += r.out; mins += r.min;
      var p = projs[r.project] || (projs[r.project] = {{ project:r.project, usd:0, w:0, n:0, min:0, commits:0, ag:{{}} }});
      p.usd += r.usd; p.w += r.wusd; p.n++; p.min += r.min; p.commits += r.commits; p.ag[r.agent_label]=1;
      var a = ags[r.agent] || (ags[r.agent] = {{ agent:r.agent, label:r.agent_label, usd:0, w:0, n:0, min:0, tools:0, commits:0, pr:{{}} }});
      a.usd += r.usd; a.w += r.wusd; a.n++; a.min += r.min; a.tools += r.tools; a.commits += r.commits; a.pr[r.project]=1;
      (r.fixes||[]).forEach(function(f){{
        var w = wastes[f.id] || (wastes[f.id] = {{ id:f.id, usd:0, tok:0, n:0, pr:{{}} }});
        w.usd += f.usd; w.tok += f.tok; w.n++; w.pr[r.project]=1;
      }});
    }});
    wusd = Math.min(usd, wusd);
    var after = Math.max(0, usd - wusd), wp = usd>0 ? (100*wusd/usd) : 0, pp = usd>0 ? (100-wp) : 100;
    var pList = Object.values(projs).sort(function(a,b){{ return b.usd - a.usd; }});
    var aList = Object.values(ags).sort(function(a,b){{ return b.usd - a.usd; }});
    var wList = Object.values(wastes).sort(function(a,b){{ return b.usd - a.usd; }});

    animCanceled = true;
    var dates = rows.map(function(r){{ return r.date; }}).filter(Boolean).sort();
    var span = dates.length ? (dates[0] === dates[dates.length-1] ? dates[0] : dates[0]+' to '+dates[dates.length-1]) : 'no chats';
    var wLbl = state.days==='all' ? 'All time' : (state.days==='month' ? 'This month' : 'Last '+state.days+' days');
    var scope = wLbl + ' · ' + span + (state.project ? ' · '+state.project : '') + (state.agent ? ' · '+state.agent : '') + (state.day ? ' · '+state.day : '');
    document.getElementById('scopeBadge').textContent = scope;
    document.getElementById('baHeadline').textContent = money(usd)+' total · '+money(after)+' after fixes';
    document.getElementById('baProd').style.width = pp.toFixed(1)+'%';
    document.getElementById('baWaste').style.width = wp.toFixed(1)+'%';
    document.getElementById('baProdVal').textContent = money(after);
    document.getElementById('baWasteVal').textContent = money(wusd);
    document.getElementById('kpiUsd').textContent = money(usd);
    document.getElementById('kpiTok').textContent = tok(sent)+' sent · '+tok(outT)+' written';
    document.getElementById('kpiWaste').textContent = money(wusd);
    document.getElementById('kpiWasteSub').textContent = 'avoidable waste ('+Math.round(wp)+'%)';
    document.getElementById('kpiChats').textContent = rows.length+' chats';
    document.getElementById('kpiAct').textContent = hrs(mins)+' · '+pList.length+' proj · '+aList.length+' agents';
    document.getElementById('navSessCount').textContent = String(rows.length);
    document.getElementById('sessCount').textContent = String(rows.length);

    renderSimulator(wList, usd, wusd);
    document.getElementById('sankeyWrap').replaceChildren(buildSankeySvg(rows));
    document.getElementById('timeChart').replaceChildren(buildBarsSvg(rows));
    document.getElementById('agentDonut').replaceChildren(buildDonutSvg(aList, usd));

    // Agent cards (safe DOM)
    var acWrap = document.getElementById('agentCards');
    if (!aList.length) {{
      acWrap.replaceChildren(el('p', {{ class:'muted', text:'No agent activity in this window.' }}));
    }} else {{
      acWrap.replaceChildren.apply(acWrap, aList.map(function(a){{
        var col = DATA.agent_colors[a.agent] || '#64748b', awp = a.usd>0 ? Math.min(100, 100*a.w/a.usd) : 0;
        return el('div', {{ class:'agent-card', 'data-fagent-card':a.agent, tabindex:'0', role:'button', 'aria-label':'Filter dashboard to '+a.label }}, [
          el('div', {{ class:'ac-head' }}, [
            el('span', {{ class:'dot', style:{{ background:col }} }}),
            el('b', {{ text:a.label }}),
            el('span', {{ class:'num ac-usd', text:money(a.usd) }})
          ]),
          el('div', {{ class:'ac-sub', text:a.n+' chats · '+Object.keys(a.pr).length+' proj · '+hrs(a.min)+' · avg '+money(a.n?a.usd/a.n:0)+'/chat' }}),
          el('div', {{ class:'meter-row' }}, [
            el('div', {{ class:'meter' }}, [
              el('span', {{ class:'meter-prod', style:{{ width:(100-awp).toFixed(1)+'%' }} }}),
              el('span', {{ class:'meter-waste', style:{{ width:awp.toFixed(1)+'%' }} }})
            ]),
            el('span', {{ class:'num warn', text:money(a.w)+' waste ('+Math.round(awp)+'%)' }})
          ]),
          el('div', {{ class:'ac-foot', text:a.tools+' tools · '+a.commits+' commits' }})
        ]);
      }}));
    }}

    // Project bars (safe DOM)
    var pbWrap = document.getElementById('projBars');
    var peakP = (pList[0] && pList[0].usd) || 0.01;
    if (!pList.length) {{
      pbWrap.replaceChildren(el('p', {{ class:'muted', text:'No projects in this window.' }}));
    }} else {{
      pbWrap.replaceChildren.apply(pbWrap, pList.map(function(p){{
        var pw = Math.min(p.usd, p.w), ppw = Math.max(0, p.usd-pw);
        return el('div', {{ class:'hbar-row', 'data-project':p.project, tabindex:'0', role:'button', 'aria-label':'Filter dashboard to '+p.project }}, [
          el('div', {{ class:'hbar-head' }}, [
            el('div', null, [
              el('b', {{ text:p.project }}),
              ' ',
              el('span', {{ class:'muted', text:Object.keys(p.ag).join(', ')+' · '+p.n+' chats · '+hrs(p.min)+' · '+p.commits+' commits' }})
            ]),
            el('div', {{ class:'num' }}, [
              el('b', {{ text:money(p.usd) }}),
              ' ',
              el('span', {{ class:'warn', text:'('+money(pw)+' waste)' }})
            ])
          ]),
          el('div', {{ class:'hbar-track' }}, [
            el('span', {{ class:'hbar-prod', style:{{ width:(100*ppw/peakP).toFixed(1)+'%' }} }}),
            el('span', {{ class:'hbar-waste', style:{{ width:(100*pw/peakP).toFixed(1)+'%' }} }})
          ])
        ]);
      }}));
    }}

    // Waste bars (safe DOM)
    var wbWrap = document.getElementById('wasteBars');
    var peakW = (wList[0] && wList[0].usd) || 0.01;
    if (!wList.length) {{
      wbWrap.replaceChildren(el('p', {{ class:'muted', text:'No built-in waste checks fired in this filter.' }}));
    }} else {{
      wbWrap.replaceChildren.apply(wbWrap, wList.map(function(w){{
        var cat = DATA.waste_catalog[w.id] || {{ title: w.id }};
        return el('div', {{ class:'wbar-row' }}, [
          el('div', {{ class:'hbar-head' }}, [
            el('div', null, [
              el('b', {{ text:cat.title }}),
              ' ',
              el('span', {{ class:'muted', text:'· '+w.n+' chats · '+Object.keys(w.pr).slice(0,3).join(', ') }})
            ]),
            el('div', {{ class:'num warn' }}, [
              el('b', {{ text:money(w.usd) }}),
              ' ',
              el('span', {{ class:'muted', text:'('+tok(w.tok)+' tok)' }})
            ])
          ]),
          el('div', {{ class:'hbar-track' }}, [
            el('span', {{ class:'hbar-waste', style:{{ width:Math.max(2, 100*w.usd/peakW).toFixed(1)+'%' }} }})
          ])
        ]);
      }}));
    }}

    lastRows = rows;
    sessLimit = 100;
    renderSessionsTable(rows);

    // Highlight active heatmap day
    document.querySelectorAll('.hm-cell[data-hday]').forEach(function(c){{
      c.classList.toggle('active-day', c.getAttribute('data-hday') === state.day);
    }});

    var isFiltered = state.project || state.agent || state.day || (state.days !== (DATA.initial_days ? String(DATA.initial_days) : 'all'));
    document.getElementById('resetFilters').style.display = isFiltered ? '' : 'none';
  }}

  var lastRows = DATA.sessions;
  var sessLimit = 100;
  function renderSessionsTable(rows){{
    var body = document.getElementById('sessBody');
    if (!body) return;
    var q = state.q.trim().toLowerCase();
    var matched = !q ? rows : rows.filter(function(r){{
      var hay = ((r.date||'')+' '+(r.id||'')+' '+(r.agent_label||r.agent||'')+' '+(r.project||'')+' '+(r.title||'')).toLowerCase();
      return hay.indexOf(q) !== -1;
    }});
    document.getElementById('sessCount').textContent = String(matched.length);
    if (!matched.length) {{
      body.replaceChildren(el('tr', null, [el('td', {{ colspan:'11', class:'muted', text:'No sessions match this filter.' }})]));
      return;
    }}
    var slice = matched.slice(0, sessLimit);
    var trs = slice.map(function(r){{
      var idCell = r.report_file
        ? el('a', {{ href:r.report_file, class:'slink', text:(r.id||'').slice(0,10) }})
        : el('span', {{ class:'num', text:(r.id||'').slice(0,10) }});
      var gTxt = r.guard_stops ? (r.guard_stops+' stopped') : (r.guard_asks ? (r.guard_asks+' asked') : '—');
      return el('tr', {{ id:r.anchor || '' }}, [
        el('td', {{ class:'num', text:r.date || '' }}),
        el('td', null, [idCell]),
        el('td', {{ text:r.agent_label || r.agent || '' }}),
        el('td', {{ text:r.project || '' }}),
        el('td', {{ text:(r.title || '').slice(0,72) }}),
        el('td', {{ class:'n num', text:hrs(r.working_min || 0) }}),
        el('td', {{ class:'n num', text:String(r.tools || 0) }}),
        el('td', {{ class:'n num', text:tok(r.sent_tokens || 0) }}),
        el('td', {{ class:'n num', text:money(r.usd || 0) }}),
        el('td', {{ class:'n num warn', text:money(r.waste_usd || 0) }}),
        el('td', {{ class:r.guard_stops ? 'bad' : 'muted', text:gTxt }})
      ]);
    }});
    if (matched.length > slice.length) {{
      var moreBtn = el('button', {{ type:'button', class:'btn', text:'Show more ('+(matched.length - slice.length)+' remaining)' }});
      moreBtn.addEventListener('click', function(){{ sessLimit += 200; renderSessionsTable(lastRows); }});
      trs.push(el('tr', null, [el('td', {{ colspan:'11', style:{{ textAlign:'center' }} }}, [moreBtn])]));
    }}
    body.replaceChildren.apply(body, trs);
  }}

  function syncPressed(groupId){{
    document.querySelectorAll('#'+groupId+' .fpill').forEach(function(x){{
      x.setAttribute('aria-pressed', x.classList.contains('active') ? 'true' : 'false');
    }});
  }}
  function setAgent(ag){{
    state.agent = (state.agent === ag) ? '' : ag;
    document.querySelectorAll('#agentGroup .fpill').forEach(function(x){{ x.classList.toggle('active', x.getAttribute('data-fagent')===state.agent); }});
    syncPressed('agentGroup');
    render();
  }}
  function setProject(pr){{
    state.project = (state.project === pr) ? '' : pr;
    document.getElementById('projSelect').value = state.project;
    render();
  }}
  function onActivateKey(containerId, selector, fn){{
    var node = document.getElementById(containerId);
    if (!node) return;
    node.addEventListener('keydown', function(e){{
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var t = e.target.closest(selector);
      if (!t || e.target.closest('a')) return;
      e.preventDefault();
      fn(t);
    }});
  }}

  document.getElementById('simList').addEventListener('change', function(e){{
    var chk = e.target.closest('.sim-chk'); if (!chk) return;
    var wid = chk.getAttribute('data-wid');
    if (chk.checked) delete simOff[wid]; else simOff[wid] = true;
    render();
  }});
  document.getElementById('sankeyWrap').addEventListener('click', function(e){{
    var ag = e.target.closest('[data-fagent]'); if (ag) {{ setAgent(ag.getAttribute('data-fagent')); return; }}
    var pr = e.target.closest('[data-fproj]'); if (pr) {{ setProject(pr.getAttribute('data-fproj')); return; }}
  }});
  onActivateKey('sankeyWrap', '[data-fagent],[data-fproj]', function(t){{
    if (t.hasAttribute('data-fagent')) setAgent(t.getAttribute('data-fagent'));
    else if (t.hasAttribute('data-fproj')) setProject(t.getAttribute('data-fproj'));
  }});
  document.getElementById('sankeyWrap').addEventListener('mouseover', function(e){{
    var lk = e.target.closest('.sk-link');
    var wrap = this;
    if (!lk) {{ wrap.querySelectorAll('.sk-link').forEach(function(x){{ x.classList.remove('dim','hi'); }}); return; }}
    var pr = lk.getAttribute('data-project');
    wrap.querySelectorAll('.sk-link').forEach(function(x){{
      var match = x === lk || (pr && x.getAttribute('data-project') === pr);
      x.classList.toggle('hi', match); x.classList.toggle('dim', !match);
    }});
  }});
  document.getElementById('sankeyWrap').addEventListener('mouseleave', function(){{
    this.querySelectorAll('.sk-link').forEach(function(x){{ x.classList.remove('dim','hi'); }});
  }});
  document.getElementById('heatmapWrap').addEventListener('click', function(e){{
    var c = e.target.closest('.hm-cell[data-hday]'); if (!c) return;
    var d = c.getAttribute('data-hday');
    state.day = (state.day === d) ? '' : d;
    render();
  }});
  onActivateKey('heatmapWrap', '.hm-cell[data-hday]', function(c){{
    var d = c.getAttribute('data-hday');
    state.day = (state.day === d) ? '' : d;
    render();
  }});
  document.getElementById('agentCards').addEventListener('click', function(e){{
    var card = e.target.closest('.agent-card[data-fagent-card]'); if (!card || e.target.closest('a')) return;
    setAgent(card.getAttribute('data-fagent-card'));
  }});
  onActivateKey('agentCards', '.agent-card[data-fagent-card]', function(c){{ setAgent(c.getAttribute('data-fagent-card')); }});
  document.getElementById('rangeGroup').addEventListener('click', function(e){{
    var b = e.target.closest('[data-days]'); if (!b) return;
    state.days = b.getAttribute('data-days'); state.day = '';
    this.querySelectorAll('.fpill').forEach(function(x){{ x.classList.toggle('active', x===b); }});
    syncPressed('rangeGroup');
    render();
  }});
  document.getElementById('granGroup').addEventListener('click', function(e){{
    var b = e.target.closest('[data-gran]'); if (!b) return;
    state.gran = b.getAttribute('data-gran');
    this.querySelectorAll('.fpill').forEach(function(x){{ x.classList.toggle('active', x===b); }});
    syncPressed('granGroup');
    render();
  }});
  document.getElementById('agentGroup').addEventListener('click', function(e){{
    var b = e.target.closest('[data-fagent]'); if (!b) return;
    state.agent = b.getAttribute('data-fagent');
    this.querySelectorAll('.fpill').forEach(function(x){{ x.classList.toggle('active', x===b); }});
    syncPressed('agentGroup');
    render();
  }});
  document.getElementById('projSelect').addEventListener('change', function(){{ state.project = this.value; render(); }});
  document.getElementById('projBars').addEventListener('click', function(e){{
    var row = e.target.closest('.hbar-row[data-project]'); if (!row || e.target.closest('a')) return;
    setProject(row.getAttribute('data-project'));
  }});
  onActivateKey('projBars', '.hbar-row[data-project]', function(r){{ setProject(r.getAttribute('data-project')); }});
  var searchTimer = null;
  document.getElementById('sessSearch').addEventListener('input', function(){{
    var val = this.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function(){{ state.q = val; sessLimit = 100; renderSessionsTable(lastRows); }}, 120);
  }});
  document.getElementById('resetFilters').addEventListener('click', function(){{
    state.days = DATA.initial_days ? String(DATA.initial_days) : 'all'; state.project = ''; state.agent = ''; state.day = ''; state.q = '';
    document.getElementById('projSelect').value = ''; document.getElementById('sessSearch').value = '';
    document.querySelectorAll('#rangeGroup .fpill').forEach(function(x){{ x.classList.toggle('active', x.getAttribute('data-days')===state.days); }});
    document.querySelectorAll('#agentGroup .fpill').forEach(function(x){{ x.classList.toggle('active', x.getAttribute('data-fagent')===''); }});
    syncPressed('rangeGroup'); syncPressed('agentGroup');
    render();
  }});
  syncPressed('rangeGroup'); syncPressed('granGroup'); syncPressed('agentGroup');
}})();
</script>
</body></html>"""


def _atomic_write_html(path: Path, text: str) -> None:
    if path.is_symlink():
        raise OSError(f"Refusing to overwrite symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sessioncost insights",
        description="Insights across all coding-agent sessions on this computer: Summary, Projects, Agents, "
                    "Waste, Safety, Recommendations, and Trend. Local only; no AI model called.",
    )
    ap.add_argument("--project", help="filter to one project (name or folder path)")
    ap.add_argument("--agent", help="filter to one agent (claude, codex, cursor, antigravity, copilot)")
    ap.add_argument("--days", type=int, help="include only the last N days (default: 30 unless --since or --all)")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="include sessions on or after YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="include all sessions across all time")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in/organisation prices")
    ap.add_argument("--json", action="store_true", help="print the structured insights data as JSON to stdout")
    ap.add_argument("--html", nargs="?", const="default", metavar="FILE",
                    help="write the HTML insights page (default: ~/.sessioncost/reports/insights.html) and print its path")
    ap.add_argument("--no-html", action="store_true", help="do not write ~/.sessioncost/reports/insights.html")
    ap.add_argument("--open", action="store_true", help="open the HTML insights page in the browser")
    ap.add_argument("--no-open", dest="open", action="store_false", help=argparse.SUPPRESS)
    try:
        args = ap.parse_args(list(argv or []))
    except SystemExit as e:
        return int(e.code or 2)

    eff_days = args.days if (args.days is not None or args.since or args.all) else 30
    if not args.json and sys.stderr.isatty():
        scope = "all time" if eff_days is None and not args.since else (f"since {args.since}" if args.since else f"last {eff_days} days; pass --all for full history")
        print(f"Scanning sessions ({scope})...", file=sys.stderr, flush=True)
    try:
        P = load_prices(args.prices)
        data = collect(
            prices=P,
            project=args.project,
            agent=args.agent,
            since=args.since,
            days=eff_days,
        )
    except (ValueError, OverflowError) as e:
        print(f"sessioncost insights: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
        return 0

    html_out: Path | None = None
    if args.html is not None:
        html_out = (home() / "reports" / INSIGHTS_PAGE) if args.html == "default" else Path(args.html).expanduser()
        try:
            _atomic_write_html(html_out, render_html(data))
        except OSError as e:
            print(f"sessioncost insights: could not write {html_out}: {e}", file=sys.stderr)
            return 2
        print(f"SessionCost insights report: {html_out.as_posix()}")
        if args.open:
            from .opener import open_report
            open_report(html_out)
        return 0

    if not args.no_html:
        try:
            html_out = home() / "reports" / INSIGHTS_PAGE
            _atomic_write_html(html_out, render_html(data))
        except OSError:
            html_out = None

    print(render_text(data, html_path=html_out))
    if args.open and html_out:
        from .opener import open_report
        open_report(html_out)
    return 0
