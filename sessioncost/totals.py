"""Totals across every chat on this computer: by project, team, agent, model and week, with per-project / per-team
monthly budgets, organisation price file support, CSV export, JSON export, and an HTML page in the guard report's style.

Read strictly from logs and config files on this computer; nothing is sent anywhere.

    sessioncost totals                              terminal summary + ~/.sessioncost/reports/totals.html
    sessioncost totals --csv [FILE]                 spreadsheet-ready CSV (stdout or FILE with UTF-8 BOM)
    sessioncost totals --json                       structured JSON on stdout
    sessioncost totals --html [FILE]                write HTML report only and print its path
    sessioncost totals budget                       show project & team budgets
    sessioncost totals budget <project> <usd>       set a project's monthly budget in ~/.sessioncost/budgets.json
    sessioncost totals budget --team-file <file|off>  use a shared organisation budgets file
"""
from __future__ import annotations

import argparse
import collections
import csv
import html
import io
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import locate, prices as pricemod, readers
from .analyse import analyse
from .summary_cache import SummaryCache

AUTO = "auto"
NO_PROJECT = "(no project)"
UNASSIGNED_TEAM = "(unassigned)"
CSV_GROUPS = ("chats", "project", "team", "agent", "model", "week")


def home() -> Path:
    return Path(os.environ.get("SESSIONCOST_DIR") or Path.home() / ".sessioncost")


def own_budgets_file() -> Path:
    return home() / "budgets.json"


def team_budgets_pointer() -> Path:
    return home() / "budgets-team.json"


def team_budgets_file() -> Path | None:
    try:
        f = json.loads(team_budgets_pointer().read_text(encoding="utf-8")).get("file")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(f) if f else None


def _norm_path(p: str) -> str:
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(str(p).strip().rstrip("\\/")))


def _project_name(cwd: str) -> str:
    cleaned = str(cwd or "").strip().rstrip("\\/")
    if not cleaned:
        return NO_PROJECT
    name = os.path.basename(cleaned)
    return name or cleaned


def _parse_money(v, what: str = "budget") -> float:
    if v is None or str(v).strip() == "":
        raise ValueError(f"{what} must be a positive number")
    try:
        n = float(str(v).strip().lstrip("$").replace(",", ""))
    except ValueError:
        raise ValueError(f"{what} must be a number in US dollars, not {v!r}") from None
    if not math.isfinite(n) or n < 0:
        raise ValueError(f"{what} cannot be {v!r}")
    return n


def _parse_budget_doc(data: dict, source_path: Path) -> dict:
    """Validate and normalize one budgets JSON document."""
    if not isinstance(data, dict):
        raise ValueError("must be a JSON object")
    out: dict = {"default_monthly_usd": None, "projects": {}, "teams": {}, "prices": None}
    if data.get("default_monthly_usd") is not None:
        out["default_monthly_usd"] = _parse_money(data["default_monthly_usd"], "default_monthly_usd")
    if data.get("prices"):
        p = Path(str(data["prices"])).expanduser()
        if not p.is_absolute():
            p = (source_path.parent / p).resolve()
        out["prices"] = p
    raw_teams = data.get("teams") or {}
    if not isinstance(raw_teams, dict):
        raise ValueError('"teams" must be a JSON object')
    for tname, tval in raw_teams.items():
        if not isinstance(tname, str) or not tname.strip():
            continue
        tkey = tname.strip()
        if isinstance(tval, (int, float)) and not isinstance(tval, bool):
            out["teams"][tkey] = {"monthly_usd": _parse_money(tval, f"team {tkey}"), "projects": []}
        elif isinstance(tval, dict):
            usd_raw = tval.get("monthly_usd")
            if usd_raw is None:
                usd_raw = tval.get("budget") if tval.get("budget") is not None else tval.get("monthly")
            projs = [str(x).strip() for x in (tval.get("projects") or []) if str(x).strip()]
            out["teams"][tkey] = {
                "monthly_usd": _parse_money(usd_raw, f"team {tkey}") if usd_raw is not None else None,
                "projects": projs,
            }
        else:
            raise ValueError(f"team {tkey!r} must be a number or object")
    raw_projects = data.get("projects") or {}
    if not isinstance(raw_projects, dict):
        raise ValueError('"projects" must be a JSON object')
    for pname, pval in raw_projects.items():
        if not isinstance(pname, str) or not pname.strip():
            continue
        pkey = pname.strip()
        if isinstance(pval, (int, float)) and not isinstance(pval, bool):
            out["projects"][pkey] = {"monthly_usd": _parse_money(pval, f"project {pkey}"), "team": ""}
        elif isinstance(pval, dict):
            usd_raw = pval.get("monthly_usd")
            if usd_raw is None:
                usd_raw = pval.get("budget") if pval.get("budget") is not None else pval.get("monthly")
            out["projects"][pkey] = {
                "monthly_usd": _parse_money(usd_raw, f"project {pkey}") if usd_raw is not None else None,
                "team": str(pval.get("team") or "").strip(),
            }
        else:
            raise ValueError(f"project {pkey!r} must be a number or object")
    return out


def _merge_budgets(base: dict, extra: dict) -> dict:
    out = {
        "default_monthly_usd": extra["default_monthly_usd"] if extra["default_monthly_usd"] is not None
        else base["default_monthly_usd"],
        "projects": {k: dict(v) for k, v in base["projects"].items()},
        "teams": {k: {"monthly_usd": v["monthly_usd"], "projects": list(v["projects"])}
                  for k, v in base["teams"].items()},
        "prices": extra["prices"] or base["prices"],
    }
    for k, v in extra["projects"].items():
        prev = out["projects"].get(k, {"monthly_usd": None, "team": ""})
        out["projects"][k] = {
            "monthly_usd": v["monthly_usd"] if v["monthly_usd"] is not None else prev["monthly_usd"],
            "team": v["team"] or prev["team"],
        }
    for k, v in extra["teams"].items():
        prev = out["teams"].get(k, {"monthly_usd": None, "projects": []})
        projs = list(dict.fromkeys(prev["projects"] + v["projects"]))
        out["teams"][k] = {
            "monthly_usd": v["monthly_usd"] if v["monthly_usd"] is not None else prev["monthly_usd"],
            "projects": projs,
        }
    return out


def load_budgets(extra_file: str | Path | None = None, user_file=AUTO) -> dict:
    """Load budgets & team mappings: organisation/team file, then ~/.sessioncost/budgets.json, then --budgets <file>.
    Later files override earlier ones."""
    personal = user_file == AUTO
    merged: dict = {"default_monthly_usd": None, "projects": {}, "teams": {}, "prices": None}
    sources: list[str] = []
    problems: list[str] = []

    candidates: list[tuple[Path | None, str, bool]] = []
    if personal:
        candidates.append((team_budgets_file(), "organisation budgets file", False))
        candidates.append((own_budgets_file(), "local budgets file", False))
    elif user_file:
        candidates.append((Path(user_file).expanduser(), "budgets file", False))
    if extra_file:
        candidates.append((Path(extra_file).expanduser(), "--budgets", True))

    for f, label, strict in candidates:
        if not f:
            continue
        if not f.is_file():
            if strict or label == "organisation budgets file":
                msg = f"The {label} {f} was not found."
                if strict:
                    raise ValueError(msg)
                problems.append(msg)
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8-sig"))
            parsed = _parse_budget_doc(raw, f)
        except (OSError, ValueError) as e:
            msg = f"The {label} {f} could not be read ({e})."
            if strict:
                raise ValueError(msg) from None
            problems.append(msg)
            continue
        merged = _merge_budgets(merged, parsed)
        sources.append(str(f))

    merged["sources"] = sources
    merged["problems"] = problems
    return merged


def _key_matches(spec: str, project_path: str, project_name: str) -> bool:
    if not spec:
        return False
    if project_path and _norm_path(spec) == _norm_path(project_path):
        return True
    return spec.strip().lower() == project_name.strip().lower()


def match_project_config(project_path: str, project_name: str, cfg: dict) -> tuple[float | None, str]:
    """Return (monthly_budget_usd, team_name) for a project."""
    budget_usd: float | None = None
    team_name = ""

    # Exact full-path match wins over basename match
    projects = cfg.get("projects") or {}
    norm_target = _norm_path(project_path)
    exact = None
    by_name = None
    for k, v in projects.items():
        if norm_target and _norm_path(k) == norm_target:
            exact = v
            break
        if k.strip().lower() == project_name.strip().lower():
            by_name = v
    chosen = exact or by_name
    if chosen:
        budget_usd = chosen.get("monthly_usd")
        team_name = chosen.get("team") or ""

    if not team_name:
        for tname, tval in (cfg.get("teams") or {}).items():
            if any(_key_matches(spec, project_path, project_name) for spec in (tval.get("projects") or [])):
                team_name = tname
                break

    if budget_usd is None and cfg.get("default_monthly_usd") is not None:
        budget_usd = cfg["default_monthly_usd"]
    return budget_usd, team_name


def budget_info(month_usd: float, budget_usd: float | None) -> dict:
    if budget_usd is None or budget_usd <= 0:
        return {"budget_usd": None, "budget_pct": None, "budget_status": "", "budget_text": ""}
    pct = (month_usd / budget_usd) * 100.0
    pct_round = round(pct)
    status = "over" if pct > 100.0 else "warn" if pct >= 80.0 else "ok"
    text = f"{pct_round}% of this month's budget used" + (" (over budget)" if pct > 100.0 else "")
    return {
        "budget_usd": round(budget_usd, 2),
        "budget_pct": round(pct, 1),
        "budget_status": status,
        "budget_text": text,
    }


def load_prices(extra_prices: str | None = None, budgets_cfg: dict | None = None, user_file=pricemod.AUTO) -> dict:
    """Load prices: uses organisation/team price file automatically when present (via `sessioncost prices team` or
    the budgets file's `"prices"` field), then local prices, then `--prices`."""
    P = pricemod.load(extra_prices, user_file=user_file)
    org_file = (budgets_cfg or {}).get("prices")
    if org_file and not extra_prices:
        org_path = Path(org_file)
        try:
            got = pricemod.read_table(org_path, P["models"])
            P = pricemod._merge(P, {"models": got})
            P.setdefault("from", {}).update({m: f"org file {org_path.name}" for m in got})
            own = list(P.get("own") or [])
            if org_path.name not in own:
                own.insert(0, org_path.name)
            P["own"] = own
        except (OSError, ValueError) as e:
            prev = P.get("team_problem") or ""
            msg = f"The organisation price file {org_path} could not be read ({e})."
            P["team_problem"] = f"{prev} {msg}".strip()
    return P


def _as_dt(ts: str | datetime | None) -> datetime | None:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso_week(d: date) -> tuple[str, str]:
    iso_y, iso_w, _ = d.isocalendar()
    monday = date.fromisocalendar(iso_y, iso_w, 1)
    return f"{iso_y}-W{iso_w:02d}", monday.isoformat()


def _parse_since(since: str | None, days: int | None, today: date) -> date | None:
    if since:
        try:
            return date.fromisoformat(since.strip())
        except ValueError:
            raise ValueError(f"--since must be YYYY-MM-DD, not {since!r}") from None
    if days is not None:
        if days < 1:
            raise ValueError("--days must be at least 1")
        return today - timedelta(days=days - 1)
    return None


def collect(sessions: list[tuple[datetime, object]] | None = None, prices: dict | None = None,
            budgets: dict | None = None, project: str | None = None, team: str | None = None,
            since: str | None = None, days: int | None = None, today: date | None = None) -> dict:
    """Collect and aggregate totals across every session on this computer."""
    ref_today = today or date.today()
    cur_month = ref_today.strftime("%Y-%m")
    cutoff = _parse_since(since, days, ref_today)

    budgets_cfg = budgets if budgets is not None else load_budgets()
    P = prices if prices is not None else load_prices(budgets_cfg=budgets_cfg)
    found = sessions if sessions is not None else locate.all_sessions()

    chats: list[dict] = []
    skipped = 0
    model_agg: dict[str, dict] = {}
    project_agg: dict[str, dict] = {}
    agent_agg: dict[str, dict] = {}
    week_agg: dict[str, dict] = {}
    cache = SummaryCache(P)

    for stamp, path in found:
        try:
            a = cache.get_or_analyse(path, P, mode="totals")
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

        proj_path = str(meta.get("cwd") or "").strip()
        proj_name = _project_name(proj_path)
        if project and not _key_matches(project, proj_path, proj_name):
            continue

        proj_budget, proj_team = match_project_config(proj_path, proj_name, budgets_cfg)
        team_label = proj_team or UNASSIGNED_TEAM
        if team and team_label.lower() != team.strip().lower():
            continue

        week_key, week_start = _iso_week(chat_date)
        month_key = chat_date.strftime("%Y-%m")
        usd = float(tot.get("usd") or 0.0)
        sent = int(tot.get("sent") or 0)
        output = int(tot.get("output") or 0)
        turns = int(tot.get("turns") or 0)
        calls_n = int(tot.get("calls") or 0) + int(tot.get("helper_calls") or 0)
        primary_model = str(meta.get("model") or "unknown")
        agent_key = str(meta.get("agent") or "unknown")
        agent_label = str(meta.get("agent_label") or agent_key)
        sid = str(meta.get("session_id") or locate.session_id(path))

        chat_row = {
            "session_id": sid,
            "when": when_dt.isoformat(),
            "date": chat_date.isoformat(),
            "week": week_key,
            "week_start": week_start,
            "month": month_key,
            "project": proj_name,
            "project_path": proj_path,
            "team": proj_team,
            "agent": agent_key,
            "agent_label": agent_label,
            "model": primary_model,
            "turns": turns,
            "calls": calls_n,
            "sent": sent,
            "output": output,
            "usd": round(usd, 6),
            "tokens_exact": bool((a.get("money") or {}).get("tokens_exact", True)),
        }
        chats.append(chat_row)

        # Group by project (keyed by normalized path if present, else project name)
        pkey = _norm_path(proj_path) if proj_path else f"::{proj_name.lower()}"
        p_rec = project_agg.setdefault(pkey, {
            "project": proj_name, "project_path": proj_path, "team": proj_team,
            "chats": 0, "turns": 0, "calls": 0, "sent": 0, "output": 0,
            "usd": 0.0, "month_usd": 0.0, "_budget": proj_budget,
        })
        p_rec["chats"] += 1
        p_rec["turns"] += turns
        p_rec["calls"] += calls_n
        p_rec["sent"] += sent
        p_rec["output"] += output
        p_rec["usd"] += usd
        if month_key == cur_month:
            p_rec["month_usd"] += usd

        # Group by agent
        a_rec = agent_agg.setdefault(agent_key, {
            "agent": agent_key, "agent_label": agent_label,
            "chats": 0, "turns": 0, "calls": 0, "sent": 0, "output": 0, "usd": 0.0,
        })
        a_rec["chats"] += 1
        a_rec["turns"] += turns
        a_rec["calls"] += calls_n
        a_rec["sent"] += sent
        a_rec["output"] += output
        a_rec["usd"] += usd

        # Group by week
        w_rec = week_agg.setdefault(week_key, {
            "week": week_key, "week_start": week_start,
            "chats": 0, "turns": 0, "calls": 0, "sent": 0, "output": 0, "usd": 0.0,
        })
        w_rec["chats"] += 1
        w_rec["turns"] += turns
        w_rec["calls"] += calls_n
        w_rec["sent"] += sent
        w_rec["output"] += output
        w_rec["usd"] += usd

        # Group by model (call-level so helper/secondary models get accurate attribution)
        by_model = a.get("by_model")
        if by_model:
            for mrow in by_model:
                mname = str(mrow.get("model") or primary_model or "unknown")
                m_rec = model_agg.setdefault(mname, {
                    "model": mname, "chats": 0, "calls": 0, "sent": 0, "output": 0, "usd": 0.0,
                })
                m_rec["chats"] += 1
                m_rec["calls"] += int(mrow.get("calls") or 0)
                m_rec["sent"] += int(mrow.get("sent") or 0)
                m_rec["output"] += int(mrow.get("output") or 0)
                m_rec["usd"] += float(mrow.get("usd") or 0.0)
        else:
            seen_models: set[str] = set()
            call_list = a.get("calls") or []
            if call_list:
                for c in call_list:
                    mname = str(c.get("model") or primary_model or "unknown")
                    m_rec = model_agg.setdefault(mname, {
                        "model": mname, "chats": 0, "calls": 0, "sent": 0, "output": 0, "usd": 0.0,
                    })
                    m_rec["calls"] += 1
                    m_rec["sent"] += int((c.get("sent") or {}).get("tokens") or 0)
                    m_rec["output"] += int((c.get("written") or {}).get("tokens") or 0)
                    m_rec["usd"] += float(c.get("usd") or 0.0)
                    seen_models.add(mname)
                for mname in seen_models:
                    model_agg[mname]["chats"] += 1
            else:
                m_rec = model_agg.setdefault(primary_model, {
                    "model": primary_model, "chats": 0, "calls": 0, "sent": 0, "output": 0, "usd": 0.0,
                })
                m_rec["chats"] += 1
                m_rec["calls"] += calls_n
                m_rec["sent"] += sent
                m_rec["output"] += output
                m_rec["usd"] += usd

    cache.save()
    chats.sort(key=lambda c: c["when"], reverse=True)

    # Finalize projects with budgets
    by_project: list[dict] = []
    proj_budget_map: dict[str, dict] = {}
    for pkey, rec in project_agg.items():
        binfo = budget_info(rec["month_usd"], rec.pop("_budget"))
        rec["usd"] = round(rec["usd"], 6)
        rec["month_usd"] = round(rec["month_usd"], 6)
        rec.update(binfo)
        by_project.append(rec)
        proj_budget_map[pkey] = rec
    by_project.sort(key=lambda r: (-r["usd"], r["project"].lower()))

    # Annotate each chat with its project's monthly spend & budget for CSV export
    for c in chats:
        pkey = _norm_path(c["project_path"]) if c["project_path"] else f"::{c['project'].lower()}"
        prec = proj_budget_map.get(pkey, {})
        c["project_month_usd"] = round(float(prec.get("month_usd") or 0.0), 4)
        c["project_budget_usd"] = prec.get("budget_usd")
        c["project_budget_pct"] = prec.get("budget_pct")

    # Group by team
    team_agg: dict[str, dict] = {}
    for prec in by_project:
        tname = prec["team"] or UNASSIGNED_TEAM
        t_rec = team_agg.setdefault(tname, {
            "team": tname, "projects": 0, "chats": 0, "turns": 0, "calls": 0,
            "sent": 0, "output": 0, "usd": 0.0, "month_usd": 0.0, "_sum_proj_budget": 0.0, "_has_proj_budget": False,
        })
        t_rec["projects"] += 1
        t_rec["chats"] += prec["chats"]
        t_rec["turns"] += prec["turns"]
        t_rec["calls"] += prec["calls"]
        t_rec["sent"] += prec["sent"]
        t_rec["output"] += prec["output"]
        t_rec["usd"] += prec["usd"]
        t_rec["month_usd"] += prec["month_usd"]
        if prec["budget_usd"] is not None:
            t_rec["_sum_proj_budget"] += prec["budget_usd"]
            t_rec["_has_proj_budget"] = True

    by_team: list[dict] = []
    cfg_teams = budgets_cfg.get("teams") or {}
    for tname, rec in team_agg.items():
        explicit = (cfg_teams.get(tname) or {}).get("monthly_usd")
        sum_b = rec.pop("_sum_proj_budget")
        has_b = rec.pop("_has_proj_budget")
        t_budget = explicit if explicit is not None else (sum_b if has_b and tname != UNASSIGNED_TEAM else None)
        rec["usd"] = round(rec["usd"], 6)
        rec["month_usd"] = round(rec["month_usd"], 6)
        rec.update(budget_info(rec["month_usd"], t_budget))
        by_team.append(rec)
    by_team.sort(key=lambda r: (-r["usd"], r["team"].lower()))

    by_agent = sorted(
        ({**r, "usd": round(r["usd"], 6)} for r in agent_agg.values()),
        key=lambda r: (-r["usd"], r["agent_label"].lower()),
    )
    by_model = sorted(
        ({**r, "usd": round(r["usd"], 6)} for r in model_agg.values()),
        key=lambda r: (-r["usd"], r["model"].lower()),
    )
    by_week = sorted(
        ({**r, "usd": round(r["usd"], 6)} for r in week_agg.values()),
        key=lambda r: r["week"],
        reverse=True,
    )

    total_usd = round(sum(c["usd"] for c in chats), 6)
    month_usd = round(sum(c["usd"] for c in chats if c["month"] == cur_month), 6)
    price_own = list(P.get("own") or [])
    price_note = (f"custom/organisation prices ({', '.join(price_own)})" if price_own
                  else f"built-in price list (checked {pricemod.checked_day(P.get('as_of', ''))})")

    return {
        "summary": {
            "chats": len(chats),
            "projects": len(by_project),
            "teams": sum(1 for t in by_team if t["team"] != UNASSIGNED_TEAM),
            "turns": sum(c["turns"] for c in chats),
            "calls": sum(c["calls"] for c in chats),
            "sent": sum(c["sent"] for c in chats),
            "output": sum(c["output"] for c in chats),
            "usd": total_usd,
            "month": cur_month,
            "month_usd": month_usd,
            "skipped": skipped,
            "prices_source": price_note,
            "prices_stale": pricemod.stale_note(P),
            "budget_sources": list(budgets_cfg.get("sources") or []),
            "budget_problems": list(budgets_cfg.get("problems") or []),
        },
        "by_project": by_project,
        "by_team": by_team,
        "by_agent": by_agent,
        "by_model": by_model,
        "by_week": by_week,
        "chats": chats,
    }


def _money(x: float | None) -> str:
    if x is None:
        return ""
    x = float(x)
    return f"${x:,.2f}" if x >= 0.995 or x == 0 else f"${x:.3f}"


def _tok(n: float) -> str:
    n = float(n or 0)
    if n >= 999_500:
        return f"{n / 1e6:.1f}M"
    return f"{n / 1e3:.0f}k" if n >= 1000 else f"{n:.0f}"


def _csv_safe(v) -> str:
    """Prevent spreadsheet formula injection on string cells."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s and (s[0] in ("=", "+", "-", "@", "\t", "\r", "\n") or s.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))):
        return "'" + s
    return s


def to_csv(data: dict, group: str = "chats") -> str:
    """Render totals as RFC 4180 CSV that opens cleanly in Excel, Google Sheets, and LibreOffice Calc."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if group == "chats":
        w.writerow([
            "date", "week", "month", "project", "team", "agent", "model", "session_id",
            "turns", "calls", "tokens_sent", "tokens_output", "cost_usd",
            "project_month_usd", "project_budget_usd", "project_budget_pct",
        ])
        for c in data["chats"]:
            w.writerow([
                c["date"], c["week"], c["month"], _csv_safe(c["project"]), _csv_safe(c["team"]),
                _csv_safe(c["agent_label"]), _csv_safe(c["model"]), _csv_safe(c["session_id"]),
                c["turns"], c["calls"], c["sent"], c["output"], f"{c['usd']:.4f}",
                f"{c['project_month_usd']:.4f}",
                "" if c["project_budget_usd"] is None else f"{c['project_budget_usd']:.2f}",
                "" if c["project_budget_pct"] is None else f"{c['project_budget_pct']:.1f}",
            ])
    elif group == "project":
        w.writerow([
            "project", "team", "chats", "turns", "calls", "tokens_sent", "tokens_output",
            "cost_usd", "month_usd", "budget_usd", "budget_pct", "budget_status",
        ])
        for r in data["by_project"]:
            w.writerow([
                _csv_safe(r["project"]), _csv_safe(r["team"]), r["chats"], r["turns"], r["calls"],
                r["sent"], r["output"], f"{r['usd']:.4f}", f"{r['month_usd']:.4f}",
                "" if r["budget_usd"] is None else f"{r['budget_usd']:.2f}",
                "" if r["budget_pct"] is None else f"{r['budget_pct']:.1f}",
                _csv_safe(r["budget_text"]),
            ])
    elif group == "team":
        w.writerow([
            "team", "projects", "chats", "turns", "calls", "tokens_sent", "tokens_output",
            "cost_usd", "month_usd", "budget_usd", "budget_pct", "budget_status",
        ])
        for r in data["by_team"]:
            w.writerow([
                _csv_safe(r["team"]), r["projects"], r["chats"], r["turns"], r["calls"],
                r["sent"], r["output"], f"{r['usd']:.4f}", f"{r['month_usd']:.4f}",
                "" if r["budget_usd"] is None else f"{r['budget_usd']:.2f}",
                "" if r["budget_pct"] is None else f"{r['budget_pct']:.1f}",
                _csv_safe(r["budget_text"]),
            ])
    elif group == "agent":
        w.writerow(["agent", "chats", "turns", "calls", "tokens_sent", "tokens_output", "cost_usd"])
        for r in data["by_agent"]:
            w.writerow([
                _csv_safe(r["agent_label"]), r["chats"], r["turns"], r["calls"],
                r["sent"], r["output"], f"{r['usd']:.4f}",
            ])
    elif group == "model":
        w.writerow(["model", "chats", "calls", "tokens_sent", "tokens_output", "cost_usd"])
        for r in data["by_model"]:
            w.writerow([
                _csv_safe(r["model"]), r["chats"], r["calls"], r["sent"], r["output"], f"{r['usd']:.4f}",
            ])
    elif group == "week":
        w.writerow(["week", "week_start", "chats", "turns", "calls", "tokens_sent", "tokens_output", "cost_usd"])
        for r in data["by_week"]:
            w.writerow([
                r["week"], r["week_start"], r["chats"], r["turns"], r["calls"],
                r["sent"], r["output"], f"{r['usd']:.4f}",
            ])
    else:
        raise ValueError(f"unknown CSV group {group!r}; choose from {', '.join(CSV_GROUPS)}")
    return buf.getvalue()


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def render_html(data: dict) -> str:
    """HTML totals report in the guard report's style: single self-contained file, zero network requests."""
    s = data["summary"]
    tile = lambda n, label, cls="": f'<div class="tile {cls}"><b class="num">{_e(n)}</b><span>{_e(label)}</span></div>'

    def budget_cell(r: dict) -> str:
        if r.get("budget_usd") is None:
            return '<span class="muted">—</span>'
        pct = min(100.0, max(0.0, float(r.get("budget_pct") or 0.0)))
        cls = "bad" if r["budget_status"] == "over" else "warn" if r["budget_status"] == "warn" else "ok"
        return (f'<div class="bbar"><span class="{cls}" style="width:{pct:.1f}%"></span></div>'
                f'<div class="muted {cls}">{_e(r["budget_text"])} ({_e(_money(r["month_usd"]))} / {_e(_money(r["budget_usd"]))})</div>')

    proj_rows = "".join(
        f'<tr><td title="{_e(r["project_path"])}">{_e(r["project"])}</td>'
        f'<td>{_e(r["team"] or "—")}</td>'
        f'<td class="n num">{r["chats"]}</td>'
        f'<td class="n num">{_e(_tok(r["sent"]))}</td>'
        f'<td class="n num">{_e(_money(r["usd"]))}</td>'
        f'<td class="n num">{_e(_money(r["month_usd"]))}</td>'
        f'<td>{budget_cell(r)}</td></tr>'
        for r in data["by_project"]
    )
    has_teams = any(t["team"] != UNASSIGNED_TEAM for t in data["by_team"])
    team_section = ""
    if has_teams:
        team_rows = "".join(
            f'<tr><td>{_e(r["team"])}</td><td class="n num">{r["projects"]}</td><td class="n num">{r["chats"]}</td>'
            f'<td class="n num">{_e(_tok(r["sent"]))}</td><td class="n num">{_e(_money(r["usd"]))}</td>'
            f'<td class="n num">{_e(_money(r["month_usd"]))}</td><td>{budget_cell(r)}</td></tr>'
            for r in data["by_team"]
        )
        team_section = (
            '<h2>By team</h2><div class="wrap"><table><thead><tr><th>Team</th><th class="n">Projects</th>'
            '<th class="n">Chats</th><th class="n">Tokens sent</th><th class="n">Total cost</th>'
            f'<th class="n">This month ({_e(s["month"])})</th><th>Monthly budget</th></tr></thead>'
            f'<tbody>{team_rows}</tbody></table></div>'
        )

    agent_rows = "".join(
        f'<tr><td>{_e(r["agent_label"])}</td><td class="n num">{r["chats"]}</td>'
        f'<td class="n num">{_e(_tok(r["sent"]))}</td><td class="n num">{_e(_money(r["usd"]))}</td></tr>'
        for r in data["by_agent"]
    )
    model_rows = "".join(
        f'<tr><td><code>{_e(r["model"])}</code></td><td class="n num">{r["chats"]}</td>'
        f'<td class="n num">{_e(_tok(r["sent"]))}</td><td class="n num">{_e(_money(r["usd"]))}</td></tr>'
        for r in data["by_model"]
    )
    peak_week = max([r["usd"] for r in data["by_week"]] or [1.0]) or 1.0
    week_rows = "".join(
        f'<tr><td class="num">{_e(r["week"])} <span class="muted">({_e(r["week_start"])})</span></td>'
        f'<td class="bars"><span class="w" style="width:{100 * r["usd"] / peak_week:.1f}%"></span></td>'
        f'<td class="n num">{r["chats"]}</td><td class="n num">{_e(_tok(r["sent"]))}</td>'
        f'<td class="n num">{_e(_money(r["usd"]))}</td></tr>'
        for r in data["by_week"][:52]
    )
    chat_rows = "".join(
        f'<tr><td class="num">{_e(c["date"])}</td><td>{_e(c["project"])}</td>'
        f'<td>{_e(c["agent_label"])}</td><td><code>{_e(c["model"])}</code></td>'
        f'<td class="n num">{c["turns"]}</td><td class="n num">{_e(_tok(c["sent"]))}</td>'
        f'<td class="n num">{_e(_money(c["usd"]))}</td><td class="muted num">{_e(c["session_id"][:8])}</td></tr>'
        for c in data["chats"][:200]
    )
    over_count = sum(1 for r in data["by_project"] if r["budget_status"] == "over")
    warn_count = sum(1 for r in data["by_project"] if r["budget_status"] == "warn")
    budget_tile = ""
    if any(r["budget_usd"] is not None for r in data["by_project"]):
        if over_count:
            budget_tile = tile(over_count, "projects over monthly budget", "bad")
        elif warn_count:
            budget_tile = tile(warn_count, "projects at 80%+ of budget", "warn")
        else:
            budget_tile = tile("Inside budget", "all budgeted projects", "ok")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<title>SessionCost totals</title><style>
:root{{--bg:oklch(0.17 0.012 280);--surface:oklch(0.21 0.014 280);--ink:oklch(0.96 0.004 280);--ink-2:oklch(0.72 0.012 280);
--line:oklch(0.30 0.012 280);--bad:oklch(0.70 0.19 20);--warn:oklch(0.78 0.14 75);--ok:oklch(0.72 0.13 155);--bar:oklch(0.62 0.17 265);color-scheme:dark}}
@media (prefers-color-scheme: light){{:root{{--bg:oklch(0.945 0.008 265);--surface:oklch(1 0 0);--ink:oklch(0.24 0.02 260);
--ink-2:oklch(0.45 0.015 260);--line:oklch(0.88 0.008 265);--bad:oklch(0.53 0.20 25);--warn:oklch(0.55 0.13 70);--ok:oklch(0.51 0.13 155);--bar:oklch(0.52 0.17 265);color-scheme:light}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 60px}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:17px;margin:34px 0 10px}}
.muted{{color:var(--ink-2);font-size:12.5px}}.num{{font-variant-numeric:tabular-nums;font-family:Consolas,ui-monospace,monospace}}
code{{font-family:Consolas,ui-monospace,monospace;font-size:12.5px;overflow-wrap:anywhere}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:18px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}.tile b{{display:block;font-size:24px}}
.tile span{{color:var(--ink-2);font-size:13px}}.tile.bad b{{color:var(--bad)}}.tile.warn b{{color:var(--warn)}}.tile.ok b{{color:var(--ok)}}
.wrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--ink-2);font-weight:600;font-size:12.5px;white-space:nowrap}}td.n,th.n{{text-align:right}}tr:last-child td{{border-bottom:0}}
.bad{{color:var(--bad)}}.warn{{color:var(--warn)}}.ok{{color:var(--ok)}}.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
.bars{{width:42%;min-width:120px}}.bars span{{display:inline-block;height:10px;vertical-align:middle;border-radius:3px}}.bars .w{{background:var(--bar)}}
.bbar{{height:8px;background:var(--line);border-radius:4px;overflow:hidden;min-width:120px;margin-bottom:3px}}
.bbar span{{display:block;height:100%}}.bbar .ok{{background:var(--ok)}}.bbar .warn{{background:var(--warn)}}.bbar .bad{{background:var(--bad)}}
</style></head><body><main>
<h1>SessionCost totals</h1>
<p>Totals across every coding-agent chat found on this computer, grouped by project, team, agent, model and week.
Read strictly from local logs; nothing here was sent anywhere.</p>
<div class="tiles">{tile(_money(s["usd"]), "total cost")}{tile(_money(s["month_usd"]), f"this month ({s['month']})")}{tile(s["chats"], "chats")}{tile(s["projects"], "projects")}{tile(_tok(s["sent"]), "tokens sent")}{budget_tile}</div>
<p class="muted">Prices: {_e(s["prices_source"])}.{f" {_e(s['prices_stale'])}" if s["prices_stale"] else ""}
Export to a spreadsheet: <code>sessioncost totals --csv totals.csv</code>.</p>
<h2>By project</h2><div class="wrap"><table><thead><tr><th>Project</th><th>Team</th><th class="n">Chats</th><th class="n">Tokens sent</th><th class="n">Total cost</th><th class="n">This month ({_e(s["month"])})</th><th>Monthly budget</th></tr></thead>
<tbody>{proj_rows or '<tr><td colspan="7" class="muted">No chats found.</td></tr>'}</tbody></table></div>
{team_section}
<div class="cols"><div><h2>By agent</h2><div class="wrap"><table><thead><tr><th>Agent</th><th class="n">Chats</th><th class="n">Tokens sent</th><th class="n">Cost</th></tr></thead>
<tbody>{agent_rows or '<tr><td colspan="4" class="muted">No chats found.</td></tr>'}</tbody></table></div></div>
<div><h2>By model</h2><div class="wrap"><table><thead><tr><th>Model</th><th class="n">Chats</th><th class="n">Tokens sent</th><th class="n">Cost</th></tr></thead>
<tbody>{model_rows or '<tr><td colspan="4" class="muted">No chats found.</td></tr>'}</tbody></table></div></div></div>
<h2>By week</h2><div class="wrap"><table><thead><tr><th>Week</th><th></th><th class="n">Chats</th><th class="n">Tokens sent</th><th class="n">Cost</th></tr></thead>
<tbody>{week_rows or '<tr><td colspan="5" class="muted">No chats found.</td></tr>'}</tbody></table></div>
<h2>Recent chats</h2><div class="wrap"><table><thead><tr><th>Date</th><th>Project</th><th>Agent</th><th>Model</th><th class="n">Turns</th><th class="n">Tokens sent</th><th class="n">Cost</th><th>Chat</th></tr></thead>
<tbody>{chat_rows or '<tr><td colspan="8" class="muted">No chats found.</td></tr>'}</tbody></table></div>
</main></body></html>"""


def render_text(data: dict, html_path: Path | None = None) -> str:
    s = data["summary"]
    lines = [
        f"SessionCost totals: {_money(s['usd'])} across {s['chats']} chat{'s' if s['chats'] != 1 else ''} "
        f"in {s['projects']} project{'s' if s['projects'] != 1 else ''} "
        f"(this month {s['month']}: {_money(s['month_usd'])})",
        f"Tokens: {_tok(s['sent'])} sent, {_tok(s['output'])} written · Prices: {s['prices_source']}",
    ]
    if s["prices_stale"]:
        lines.append(f"Note: {s['prices_stale']}")
    for prob in s["budget_problems"]:
        lines.append(f"Note: {prob}")
    if s["skipped"]:
        lines.append(f"Note: skipped {s['skipped']} unreadable log{'s' if s['skipped'] != 1 else ''}.")

    if not data["chats"]:
        lines.append("\nNo chats found on this computer.")
        return "\n".join(lines)

    has_budget = any(r["budget_usd"] is not None for r in data["by_project"])
    lines.append("\nBy project:")
    if has_budget:
        lines.append(f"  {'Project':22}  {'Chats':>5}  {'Total':>9}  {'Month':>9}  Budget")
        for r in data["by_project"]:
            btxt = r["budget_text"] or "—"
            lines.append(f"  {r['project'][:22]:22}  {r['chats']:>5}  {_money(r['usd']):>9}  {_money(r['month_usd']):>9}  {btxt}")
    else:
        lines.append(f"  {'Project':22}  {'Chats':>5}  {'Sent':>7}  {'Total':>9}  {'Month':>9}")
        for r in data["by_project"]:
            lines.append(f"  {r['project'][:22]:22}  {r['chats']:>5}  {_tok(r['sent']):>7}  {_money(r['usd']):>9}  {_money(r['month_usd']):>9}")

    if any(t["team"] != UNASSIGNED_TEAM for t in data["by_team"]):
        lines.append("\nBy team:")
        lines.append(f"  {'Team':22}  {'Proj':>4}  {'Chats':>5}  {'Total':>9}  {'Month':>9}  Budget")
        for r in data["by_team"]:
            btxt = r["budget_text"] or "—"
            lines.append(f"  {r['team'][:22]:22}  {r['projects']:>4}  {r['chats']:>5}  {_money(r['usd']):>9}  {_money(r['month_usd']):>9}  {btxt}")

    lines.append("\nBy agent:")
    for r in data["by_agent"]:
        lines.append(f"  {r['agent_label'][:22]:22}  {r['chats']:>5} chats  {_tok(r['sent']):>7} sent  {_money(r['usd']):>9}")

    lines.append("\nBy model:")
    for r in data["by_model"][:10]:
        lines.append(f"  {r['model'][:28]:28}  {r['chats']:>5} chats  {_tok(r['sent']):>7} sent  {_money(r['usd']):>9}")

    lines.append("\nBy week:")
    for r in data["by_week"][:12]:
        lines.append(f"  {r['week']} ({r['week_start']})  {r['chats']:>5} chats  {_tok(r['sent']):>7} sent  {_money(r['usd']):>9}")

    if html_path:
        lines.append(f"\nReport: {Path(html_path).as_posix()}")
    lines.append("CSV export: sessioncost totals --csv totals.csv")
    return "\n".join(lines)


def cmd_budget(argv: list[str]) -> int:
    """Manage local or organisation project/team monthly budgets."""
    ap = argparse.ArgumentParser(
        prog="sessioncost totals budget",
        description="View or set monthly project budgets (saved in ~/.sessioncost/budgets.json, or point to an "
                    "organisation budgets file with --team-file).",
    )
    ap.add_argument("project", nargs="?", help="project name or folder path")
    ap.add_argument("amount", nargs="?", help="monthly budget in US dollars (e.g. 200 or $200)")
    ap.add_argument("--team", help="assign this project to a team name")
    ap.add_argument("--team-file", dest="team_file", metavar="FILE|off",
                    help="use a shared organisation budgets JSON file (or 'off' to clear)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 2)

    if a.team_file is not None:
        if a.team_file == "off":
            team_budgets_pointer().unlink(missing_ok=True)
            print("SessionCost budgets: cleared organisation budgets file.")
            return 0
        f = Path(a.team_file).expanduser().resolve()
        try:
            raw = json.loads(f.read_text(encoding="utf-8-sig"))
            _parse_budget_doc(raw, f)
        except (OSError, ValueError) as e:
            print(f"SessionCost budgets: organisation file not set: {e}")
            return 2
        team_budgets_pointer().parent.mkdir(parents=True, exist_ok=True)
        team_budgets_pointer().write_text(json.dumps({"file": str(f)}), encoding="utf-8")
        print(f"SessionCost budgets: using organisation file {f}. Local budgets in {own_budgets_file()} still override it.")
        return 0

    if not a.project:
        cfg = load_budgets()
        tf = team_budgets_file()
        print(f"Organisation budgets file: {tf}" if tf else "Organisation budgets file: none")
        print(f"Local budgets file: {own_budgets_file()}" if own_budgets_file().is_file()
              else f"Local budgets file: none ({own_budgets_file()})")
        for prob in cfg["problems"]:
            print(f"Note: {prob}")
        if cfg["default_monthly_usd"] is not None:
            print(f"Default monthly budget: {_money(cfg['default_monthly_usd'])}")
        if cfg["teams"]:
            print("\nTeams:")
            for tname, tval in sorted(cfg["teams"].items()):
                b = _money(tval["monthly_usd"]) if tval["monthly_usd"] is not None else "—"
                projs = ", ".join(tval["projects"]) if tval["projects"] else "—"
                print(f"  {tname:20}  {b:>10}/mo  projects: {projs}")
        if cfg["projects"]:
            print("\nProjects:")
            for pname, pval in sorted(cfg["projects"].items()):
                b = _money(pval["monthly_usd"]) if pval["monthly_usd"] is not None else "—"
                t = f"  team: {pval['team']}" if pval.get("team") else ""
                print(f"  {pname:28}  {b:>10}/mo{t}")
        if not cfg["projects"] and not cfg["teams"] and cfg["default_monthly_usd"] is None:
            print("\nNo project budgets set yet. Example: sessioncost totals budget my-app 200 --team platform")
        return 0

    if a.amount is None:
        print("sessioncost totals budget <project> <monthly_usd> [--team <name>]")
        return 2
    try:
        usd = _parse_money(a.amount, "monthly budget")
    except ValueError as e:
        print(f"SessionCost budgets: {e}")
        return 2

    f = own_budgets_file()
    data = {}
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as e:
            print(f"SessionCost budgets: {f} could not be read ({e}). Fix or remove it first.")
            return 2
    projects = dict(data.get("projects") or {})
    entry: dict = {"monthly_usd": usd}
    if a.team:
        entry["team"] = a.team.strip()
    elif isinstance(projects.get(a.project), dict) and projects[a.project].get("team"):
        entry["team"] = projects[a.project]["team"]
    projects[a.project] = entry
    data["projects"] = projects
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    team_note = f" (team: {entry['team']})" if entry.get("team") else ""
    print(f"SessionCost budgets: set {a.project} monthly budget to {_money(usd)}{team_note} in {f}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if argv[:1] == ["budget"]:
        return cmd_budget(argv[1:])

    ap = argparse.ArgumentParser(
        prog="sessioncost totals",
        description="Totals across every chat on this computer by project, team, agent, model and week, with monthly "
                    "budgets and CSV/JSON/HTML export. Local only: nothing is sent anywhere.",
    )
    ap.add_argument("--project", help="filter to one project (name or folder path)")
    ap.add_argument("--team", help="filter to one team name")
    ap.add_argument("--days", type=int, help="include only chats from the last N days")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="include only chats on or after this date")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in/organisation prices")
    ap.add_argument("--budgets", help="an organisation or local budgets.json file")
    ap.add_argument("--json", action="store_true", help="print totals as JSON to stdout")
    ap.add_argument("--csv", nargs="?", const="-", metavar="FILE",
                    help="export CSV to FILE (UTF-8 with BOM for Excel) or stdout if FILE is omitted")
    ap.add_argument("--csv-group", choices=CSV_GROUPS, default="chats",
                    help="what each CSV row represents (default: chats)")
    ap.add_argument("--html", nargs="?", const="default", metavar="FILE",
                    help="write the HTML totals page (default: ~/.sessioncost/reports/totals.html) and print its path")
    ap.add_argument("--no-html", action="store_true", help="do not write ~/.sessioncost/reports/totals.html")
    ap.add_argument("--open", action="store_true", help="open the HTML totals page in the browser")
    ap.add_argument("--no-open", dest="open", action="store_false", help=argparse.SUPPRESS)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 2)

    try:
        budgets_cfg = load_budgets(args.budgets)
        P = load_prices(args.prices, budgets_cfg=budgets_cfg)
        data = collect(prices=P, budgets=budgets_cfg, project=args.project, team=args.team,
                       since=args.since, days=args.days)
    except ValueError as e:
        print(f"sessioncost totals: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
        return 0

    if args.csv is not None:
        csv_text = to_csv(data, group=args.csv_group)
        if args.csv == "-":
            sys.stdout.write(csv_text)
        else:
            out = Path(args.csv).expanduser()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(csv_text, encoding="utf-8-sig")
            print(f"SessionCost totals CSV: {out.as_posix()} ({len(data['chats'])} chats)")
        return 0

    html_out: Path | None = None
    if args.html is not None:
        html_out = (home() / "reports" / "totals.html") if args.html == "default" else Path(args.html).expanduser()
        html_out.parent.mkdir(parents=True, exist_ok=True)
        html_out.write_text(render_html(data), encoding="utf-8", errors="replace")
        print(f"SessionCost totals report: {html_out.as_posix()}")
        if args.open:
            from .opener import open_report
            open_report(html_out)
        return 0

    if not args.no_html:
        try:
            html_out = home() / "reports" / "totals.html"
            html_out.parent.mkdir(parents=True, exist_ok=True)
            html_out.write_text(render_html(data), encoding="utf-8", errors="replace")
        except OSError:
            html_out = None
    print(render_text(data, html_path=html_out))
    if args.open and html_out:
        from .opener import open_report
        open_report(html_out)
    return 0
