"""sessioncost last | list | <session.jsonl>  - see README.md."""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, locate, prices as pricemod, readers, report
from .analyse import analyse
from .readers import cursor
from .readers.antigravity import antigravity_homes

REPORTS = Path.home() / ".sessioncost" / "reports"
DOWNLOADS = Path.home() / "Downloads"      # where a browser saves Cursor's usage export (tests point it elsewhere)
DOWNLOADS_DAYS = 60                        # an export older than this cannot hold a recent chat


def money(x: float) -> str:
    return f"${x:,.2f}" if x >= 0.995 or x == 0 else f"${x:.3f}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def minutes(m: float) -> str:
    m = float(m or 0)
    if m >= 60:
        return f"{int(m // 60)}h {int(round(m % 60)):02d}m"
    return f"{m:.0f} min" if m >= 10 else f"{m:.1f} min"


def tokens(n: float) -> str:
    n = float(n or 0)
    if n >= 999_500:
        return f"{n / 1e6:.1f}M"
    return f"{n / 1e3:.0f}k" if n >= 1000 else f"{n:.0f}"


def summary(a: dict, path: Path | None) -> str:
    t, mo = a["totals"], a["money"]
    title = a["meta"]["title"]
    title = title if len(title) <= 70 else title[:69] + "…"
    lines = [f"SessionCost {__version__}: {title}"]
    helpers = f" (+{plural(t['helper_calls'], 'helper call')})" if t["helper_calls"] else ""
    lines.append(f"{tokens(t['sent'])} tokens sent, {tokens(t['output'])} written · {plural(t['turns'], 'turn')} · "
                 f"{plural(t['calls'], 'call')}{helpers} · "
                 f"{minutes(t['working_min'])} working, {minutes(t['waiting_min'])} waiting for you")
    turns = [x for x in a["turns"] if x["calls"]]
    if turns and t["sent"]:
        top = max(turns, key=lambda x: x["sent"] + x["helper_sent"])
        sent = top["sent"] + top["helper_sent"]
        text = top["text"] if len(top["text"]) <= 50 else top["text"][:49] + "…"
        lines.append(f"Heaviest turn: #{top['n']} “{text}” · {tokens(sent)} tokens sent ({sent / t['sent']:.0%}) · "
                     f"{money(top['usd'] + top['helper_usd'])}")
    if a["fixes"]:
        f = a["fixes"][0]
        lines.append(f"Top fix: {f['title']} · {tokens(f['saving_tokens'])} fewer tokens sent · "
                     f"saves {money(f['saving_usd'])} (measured from this session)")
    else:
        lines.append("Top fix: none of the built-in checks fired")
    if mo.get("range") and mo.get("export_partial"):
        lines.append(f"Cost: {money(mo['range']['low'])}–{money(mo['range']['high'])} estimate. The usage export covered "
                     "only part of this chat; export a newer one (cursor.com/settings, Usage tab) for the turns after it")
    elif mo.get("range") and a["meta"]["agent"] != "cursor":
        lines.append(f"Cost: {money(mo['range']['low'])}–{money(mo['range']['high'])} estimate, API-equivalent. "
                     f"{a['meta']['agent_label']} records few token counts and nothing about the cache, so most "
                     "tokens are estimated from the size of the text")
    elif mo.get("range"):
        lines.append(f"Cost: {money(mo['range']['low'])}–{money(mo['range']['high'])} estimate. Cursor does not record "
                     "how much was re-read from the cache; its usage export gives the exact cost (cursor.com/settings, "
                     "Usage tab, export the CSV, then run this again: it is found in Downloads)")
    else:      # tokens first and exact; the money is an estimate of the bill
        how = mo["short"] if mo["short"].startswith("estimate") else f"{mo['short']} estimate" if mo.get("subscription") \
            else f"estimate {mo['short']}"
        exact = "tokens are exact" if mo.get("tokens_exact", True) else "tokens estimated from the text size"
        lines.append(f"Cost: about {money(t['usd'])} ({how}; {exact})")
    if path:
        lines.append(f"Report: {path}")
    return "\n".join(lines)


OLD_REPORTS_HINT = 10      # past this many older reports, the summary says how to delete them (it never asks)
GUARD_PAGE = "guard.html"  # the guard report across every chat: rewritten with each report, never an "old report"


def old_reports(keep: Path | None, folder: Path = REPORTS) -> list[Path]:
    """Every saved report except the one to keep (the current one; by default the newest)."""
    files = sorted((f for f in folder.glob("*.html") if f.name != GUARD_PAGE), key=lambda f: f.stat().st_mtime,
                   reverse=True) if folder.is_dir() else []
    keep = keep or (files[0] if files else None)
    return [f for f in files if keep is None or f.resolve() != keep.resolve()]


def size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def cmd_clean(folder: Path = REPORTS) -> int:
    old = old_reports(None, folder)
    if not old:
        print("No older reports to delete.")
        return 0
    freed = sum(f.stat().st_size for f in old)
    for f in old:
        f.unlink()
    kept = sorted(f for f in folder.glob("*.html") if f.name != GUARD_PAGE)
    print(f"Deleted {plural(len(old), 'older report')} ({size(freed)})." +
          (f" Kept the latest: {kept[0]}" if kept else ""))
    return 0


def downloaded_usage(s, folder: Path | None = None) -> tuple[Path, int] | None:
    """A Cursor usage export in the Downloads folder that covers this chat: (file, rows used). Read on this computer
    only. Files are told by their columns, not their name (Cursor does not publish one); newest first."""
    import time
    folder = folder or DOWNLOADS
    try:
        files = sorted((f for f in folder.glob("*.csv") if time.time() - f.stat().st_mtime < DOWNLOADS_DAYS * 86400),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:20]
    except OSError:
        return None
    for f in files:
        if not cursor.is_usage_csv(f):
            continue
        try:
            used = cursor.apply_usage_csv(s, cursor.read_usage_csv(f))
        except (OSError, ValueError):
            continue
        if used:
            return f, used
    return None


def build(path, args) -> tuple[dict, Path | None]:
    s = readers.read(path)
    if s.agent == "cursor" and not args.cursor_usage and (found := downloaded_usage(s)):
        print(f"sessioncost: used Cursor's usage export from Downloads: {found[0].name} ({found[1]} requests matched "
              "this chat)")
    if args.cursor_usage:
        if s.agent != "cursor":
            print("sessioncost: --cursor-usage is for Cursor chats; ignored")
        else:
            used = cursor.apply_usage_csv(s, cursor.read_usage_csv(args.cursor_usage))
            print(f"sessioncost: {used} requests from the usage export matched this chat" if used else
                  "sessioncost: no row of the usage export falls inside this chat's turns (check the export's dates)")
    a = analyse(s, pricemod.load(args.prices), subscription=args.subscription)
    a["guard_all"] = guard_totals(s.cwd)
    out = None
    if not args.json:
        REPORTS.mkdir(parents=True, exist_ok=True)
        out = REPORTS / f"{s.session_id}.html"
        out.write_text(report.render(a), encoding="utf-8")
        if a["guard_all"]:                 # the report's "Open the full guard report" opens this page, next to it
            try:
                from .guard_report import write as write_guard_report
                write_guard_report(out=REPORTS / GUARD_PAGE)
            except OSError:
                pass
    return a, out


def guard_totals(cwd: str | None) -> dict | None:
    """The guarded chats of this chat's project, by name, as counts and reasons, plus how many chats and projects
    were guarded on this computer in all (numbers only: a chat's report may be shared, so no other project's name or
    any command). None when the guard has never been on here."""
    import collections
    from .guard import WHY
    from .guard_report import collect
    from .risks import SEVERITY
    try:
        r = collect()
    except OSError:
        return None
    if not r["chats"]:
        return None
    same = lambda p: bool(p) and bool(cwd) and os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(cwd))
    mine = [c for c in r["chats"] if same(c["cwd"])]
    ids = {c["id"] for c in mine}
    reasons = {"blocked": collections.Counter(), "asked": collections.Counter()}
    for st in r["steps"]:
        if st["chat"] in ids:
            reasons[st["decision"]].update(st["marks"])
    return {"project": os.path.basename(str(cwd or "").rstrip("\\/")) or "this project",
            "chats": len(mine), "intact": sum(c["intact"] for c in mine),
            **{k: sum(c[k] for c in mine) for k in ("checked", "blocked", "asked", "allowed", "injections")},
            "reasons": {d: [[WHY.get(m, m), n] for m, n in reasons[d].most_common(6) if SEVERITY.get(m) != "info"]
                        for d in reasons},
            "all_chats": len(r["chats"]), "all_projects": len({c["project"] for c in r["chats"]})}


SKILL = Path(__file__).parent / "skill" / "SKILL.md"
LIVE_SKILL = Path(__file__).parent / "skill" / "livesession.md"
SKILL_HOMES = [   # (agent, its folder, where its personal skills live inside it, its name for `guard setup`)
    ("Claude Code", ".claude", "skills", None),         # its guard comes with the plugin only
    ("Codex", ".codex", "skills", "codex"),
    ("Antigravity", ".gemini", "config/skills", "antigravity"),
    ("Cursor", ".cursor", "skills", "cursor"),
]


def claude_plugin_installed(home: Path) -> bool:
    """True when Claude Code has the SessionCost plugin, which brings its own /sessioncost."""
    try:
        plugins = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(k.startswith("sessioncost@") for k in (plugins.get("plugins") or {}))


def plugin_installed(folder: str, home: Path) -> bool:
    """True when this agent has the SessionCost plugin, which brings its own /sessioncost (2026-10-16: Cursor and Codex
    showed two /sessioncost, the plugin's and a copy setup had written)."""
    if folder == ".claude":
        return claude_plugin_installed(home)
    plugins = home / folder / "plugins"
    if folder == ".codex":
        return (plugins / "cache").is_dir() and any((plugins / "cache").glob("*/sessioncost"))
    if folder == ".cursor":
        return plugins.is_dir() and any(p.is_dir() for p in plugins.glob("**/sessioncost"))
    return False


def setup(home: Path | None = None) -> list[tuple[str, Path | None]]:
    """Add the /sessioncost skill to every agent installed for this user, and /livesession where `guard setup` can give
    that agent the guard. Returns (agent, the /sessioncost skill file written), with None for an agent whose
    SessionCost plugin already provides both."""
    home = home or Path.home()
    # The skills name this exact Python, so they run even when no 'python' or 'sessioncost' is on the PATH.
    exe = Path(sys.executable).as_posix()
    run = f'"{exe}" -m sessioncost' if " " in exe else f"{exe} -m sessioncost"
    fill = lambda f: f.read_text(encoding="utf-8").replace("{{RUN}}", run).replace("{{VERSION}}", __version__)
    text, live = fill(SKILL), fill(LIVE_SKILL)
    if " " in exe:
        text = text.replace("   If that fails because", "   In PowerShell, put `& ` in front of the quoted path. If that fails because")
        live = live.replace("     Add ` strict`", "     In PowerShell, put `& ` in front of the quoted path. Add ` strict`")
    done = []
    for agent, folder, sub, guard_name in SKILL_HOMES:
        if not (home / folder).is_dir():
            continue
        skills = {"sessioncost": text}
        if guard_name:
            skills["livesession"] = live.replace("{{AGENT}}", agent).replace("{{SETUP}}", guard_name)
        if plugin_installed(folder, home):
            # Two /sessioncost in one agent confuse people: remove copies an earlier setup wrote, add none.
            for name in skills:
                dest = home / folder / sub / name / "SKILL.md"
                if dest.is_file() and f"name: {name}" in dest.read_text(encoding="utf-8", errors="replace"):
                    dest.unlink()
                    if not any(dest.parent.iterdir()):
                        dest.parent.rmdir()
            done.append((agent, None))
            continue
        for name, body in skills.items():
            dest = home / folder / sub / name / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
        done.append((agent, home / folder / sub / "sessioncost" / "SKILL.md"))
    return done


def cmd_setup() -> int:
    done = setup()
    if not done:
        print("No Claude Code, Codex, Antigravity or Cursor folder found for this user; nothing to set up.")
        return 1
    for agent, dest in done:
        if not dest:
            print(f"{agent:12} /sessioncost comes from the SessionCost plugin; nothing added")
        elif agent == "Claude Code":
            print(f"{agent:12} /sessioncost added ({dest}). The live guard comes with the Claude Code plugin: "
                  "claude plugin marketplace add kish21/sessioncost")
        else:
            print(f"{agent:12} /sessioncost and /livesession added ({dest.parent.parent})")
    from . import guard
    for agent in guard.refresh():           # hooks an older version wrote get this version's commands
        print(f"{agent.capitalize():12} live guard hooks brought up to date")
    print("\nRestart the agent if it is open, then type /sessioncost in a chat. /livesession turns the live guard on.")
    return 0


def cmd_list(args) -> int:
    loose: list = []
    found = locate.sessions(args.project, unlinked=loose)
    if not found and not loose:
        print(f"No Claude Code, Codex, Copilot CLI, Antigravity or Cursor sessions found for {args.project or Path.cwd()}")
        return 1
    P = pricemod.load(args.prices)

    def rows(items):
        print(f"{'When':16}  {'Agent':11}  {'Session':8}  {'Cost':>8}  {'Turns':>5}  {'Calls':>5}  First message")
        for when, p in items[: args.limit]:
            try:
                a = analyse(readers.read(p), P, engines=[])
            except Exception as e:  # one broken log must not hide the others
                print(f"{when.astimezone():%Y-%m-%d %H:%M}  {'':11}  {locate.short_id(p)}  {'?':>8}  {'':>5}  {'':>5}  (could not read: {e})")
                continue
            t = a["totals"]
            title = a["meta"]["title"]
            print(f"{when.astimezone():%Y-%m-%d %H:%M}  {a['meta']['agent_label']:11}  {locate.short_id(p)}  {money(t['usd']):>8}  {t['turns']:>5}  {t['calls']:>5}  "
                  f"{title[:60]}{'…' if len(title) > 60 else ''}")

    if found:
        rows(found)
    else:
        print(f"No session is recorded for {args.project or Path.cwd()}.")
    if loose:
        print("\nThe agent did not record a folder for these, so they may belong to any project:")
        rows(loose)
    print("\nOpen one: sessioncost <session>   (its first characters are enough)")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["guard"]:      # `sessioncost guard on|off|start|auto on|...`: the live guard's own commands
        from . import guard
        return guard.main(argv[1:])
    if argv[:1] == ["auto"] and argv[1:2] in (["on"], ["off"]):   # each chat's report after each answer (on by default)
        from . import guard
        print(guard.command(f"reports-{argv[1]}", os.getcwd()))
        return 0
    ap = argparse.ArgumentParser(
        prog="sessioncost",
        description="See where every token of your agent session went, turn by turn, and what to fix. "
                    "Local: reads log files on this computer and sends nothing anywhere.")
    ap.add_argument("target", nargs="?", default="last",
                    help="'last' (newest Claude Code, Codex, Copilot CLI, Antigravity or Cursor session of this project, the default), 'list', 'setup' (add /sessioncost to your agents), 'clean' (delete all saved reports but the latest), 'update' (update every copy of SessionCost on this computer), 'version' (this copy's version; --all lists every copy on this computer), 'auto' (`sessioncost auto on|off`: rebuild each chat's report after each answer, on by default), 'guard' (the live guard: `sessioncost guard on|off|start|auto on|off`; `sessioncost guard` alone shows its state), or a session file (.jsonl, or an Antigravity .db)")
    ap.add_argument("--project", help="project folder (default: the current folder)")
    ap.add_argument("--no-open", action="store_true", help="write the report but do not open it")
    ap.add_argument("--brief", action="store_true", help="print only the report's path, not the summary (for agents: "
                                                         "text in a chat is re-sent with every later message)")
    ap.add_argument("--json", action="store_true", help="print the full model as JSON instead of a report")
    ap.add_argument("--sarif", metavar="FILE", help="also write the steps worth a look and the live guard's decisions "
                                                     "as SARIF 2.1.0 (for security tools)")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in one")
    ap.add_argument("--subscription", action="store_true",
                    help="you are on a subscription plan (Claude Pro/Max, ChatGPT): label money as API-equivalent, not billed")
    ap.add_argument("--cursor-usage", metavar="CSV",
                    help="Cursor only: your usage export from the Cursor dashboard, for exact token counts")
    ap.add_argument("--limit", type=int, default=10, help="list: how many sessions (default 10)")
    ap.add_argument("--dry-run", action="store_true", help="update: show the steps without running them")
    ap.add_argument("--all", action="store_true", help="version: list every copy on this computer, not only this one")
    ap.add_argument("--version", action="version", version=f"sessioncost {__version__}")
    args = ap.parse_args(argv)

    if args.target == "setup":
        return cmd_setup()
    if args.target == "clean":
        return cmd_clean()
    if args.target == "update":
        from . import update
        return update.run(dry_run=args.dry_run)
    if args.target == "version":
        from . import update
        return update.show_versions(every=args.all)
    if args.target == "list":
        return cmd_list(args)
    if args.target == "last":
        loose: list = []
        found = locate.sessions(args.project, unlinked=loose)
        if not found and loose:
            print(f"sessioncost: no session is recorded for {args.project or Path.cwd()}. Showing the newest session "
                  "whose folder the agent did not record; it may belong to another project. "
                  "`sessioncost list` shows the others.")
            found = loose
        if not found:
            from .readers.copilot import copilot_home
            print(f"No Claude Code, Codex, Copilot CLI, Antigravity or Cursor sessions found for {args.project or Path.cwd()} "
                  f"(looked in {locate.claude_home() / 'projects'}, {locate.codex_home() / 'sessions'} and "
                  f"{locate.codex_home() / 'archived_sessions'}, {copilot_home() / 'session-state'}, "
                  f"{', '.join(str(h / 'conversations') for h in antigravity_homes())}, and Cursor's chats)")
            return 1
        path = found[0][1]
    else:
        path = Path(args.target).expanduser()
        if not path.is_file():   # a session id (or its first characters) from `sessioncost list`
            loose = []
            hits = [p for _, p in locate.sessions(args.project, unlinked=loose) + loose
                    if locate.session_id(p).startswith(args.target)]
            if len(hits) == 1:
                path = hits[0]
    try:
        a, out = build(path, args)
    except (readers.NotSupported, ValueError) as e:
        print(f"sessioncost: {e}")
        return 2
    if args.sarif:
        from . import sarif
        Path(args.sarif).write_text(json.dumps(sarif.build(a, path), indent=1, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(a, indent=1, ensure_ascii=False, default=str))
        return 0
    print(f"Report: {out}" if args.brief else summary(a, out))
    if args.sarif:
        print(f"SARIF: {args.sarif}")
    old = old_reports(out) if out else []
    if len(old) > OLD_REPORTS_HINT:
        print(f"Old reports: {len(old)} ({size(sum(f.stat().st_size for f in old))}) · delete them with: sessioncost clean")
    if out and not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0
