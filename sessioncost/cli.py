"""sessioncost last | list | <session.jsonl>  - see README.md."""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, locate, prices as pricemod, readers, report, zfile
from .analyse import analyse
from .readers import cursor
from .readers.antigravity import antigravity_homes
from .cli_setup import SKILL_HOMES, agent_installed, claude_plugin_installed, cmd_setup
from .names import GUARD_CMD
from .cli_setup import LIVE_SKILL, SKILL, plugin_installed, setup  # noqa: F401 (cli.<name> for other modules and tests)


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


def _applied_since(a: dict, fix_id: str) -> bool:
    """`sessioncost fix` applied this fix after the chat started: its waste is from before, not a to-do (the report's
    card says the same). Applied before the chat started and here again: it came back, still a to-do."""
    from datetime import datetime
    at, started = (a.get("fix_done") or {}).get(fix_id), (a.get("meta") or {}).get("started")
    try:
        return bool(at and started) and datetime.fromisoformat(started) < datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return False


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
    todo = [f for f in a["fixes"] if not _applied_since(a, f["id"])]
    if todo:
        f = todo[0]
        lines.append(f"Top fix: {f['title']} · {tokens(f['saving_tokens'])} fewer tokens sent · "
                     f"saves {money(f['saving_usd'])} (measured from this session)")
    elif a["fixes"]:
        lines.append("Top fix: all of them were already applied after this chat started")
    else:
        lines.append("Top fix: none of the built-in checks fired")
    for v in a.get("fix_verify") or []:
        lines.append(f"Fixed earlier: {v}")
    if mo.get("range") and mo.get("export_partial"):
        lines.append(f"Cost: {money(mo['range']['low'])}–{money(mo['range']['high'])} estimate. The usage export covered "
                     "only part of this chat; export a newer one (cursor.com/settings, Usage tab) for the turns after it")
    elif mo.get("range") and a["meta"]["agent"] != "cursor":
        lines.append(f"Cost: {money(mo['range']['low'])}–{money(mo['range']['high'])} estimate, API-equivalent. "
                     + (mo.get("counts_note") or f"{a['meta']['agent_label']} records few token counts and nothing "
                        "about the cache, so most tokens are estimated from the size of the text"))
    elif mo.get("range"):
        lines.append(f"Cost: {money(mo['range']['low'])}–{money(mo['range']['high'])} estimate. Cursor does not record "
                     "how much was re-read from the cache; its usage export gives the exact cost (cursor.com/settings, "
                     "Usage tab, export the CSV, then run this again: it is found in Downloads)")
    else:      # tokens first and exact; the money is an estimate of the bill
        how = mo["short"] if mo["short"].startswith("estimate") else f"{mo['short']} estimate" if mo.get("subscription") \
            else f"estimate {mo['short']}"
        exact = "tokens are exact" if mo.get("tokens_exact", True) else "tokens estimated from the text size"
        lines.append(f"Cost: about {money(t['usd'])} ({how}; {exact})")
    lines += [mo["stale"]] if mo.get("stale") else []          # an old price list, or a team file it could not read
    if path:
        lines.append(f"Report: {Path(path).as_posix()}")     # a chat's markdown eats "\." in C:\Users\me\.sessioncost
    return "\n".join(lines)


KEEP_REPORTS = 20          # one report per chat, ~0.5 MB each: after each report, older ones past the newest 20 go
GUARD_PAGE = "guard.html"  # the guard report across every chat: rewritten with each report, never an "old report"


def old_reports(keep: Path | None, folder: Path = REPORTS) -> list[Path]:
    """Every saved report except the one to keep (the current one; by default the newest)."""
    files = sorted((f for f in folder.glob("*.html") if f.name != GUARD_PAGE), key=lambda f: f.stat().st_mtime,
                   reverse=True) if folder.is_dir() else []
    keep = keep or (files[0] if files else None)
    return [f for f in files if keep is None or f.resolve() != keep.resolve()]


def trim_reports(keep: Path, folder: Path = REPORTS, n: int = KEEP_REPORTS) -> tuple[int, int]:
    """Delete the reports past the newest n (never the one just written); (how many, bytes freed). A deleted
    report comes back the next time that chat is opened, while its log is still on this computer."""
    gone = freed = 0
    for f in old_reports(keep, folder)[max(0, n - 1):]:
        try:
            b = f.stat().st_size
            f.unlink()
        except OSError:
            continue
        gone, freed = gone + 1, freed + b
    return gone, freed


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
    from . import fixer, guard
    a["guard_all"] = guard_totals(s.cwd)
    project = getattr(args, "project", None) or s.cwd
    a["fix_project"] = str(project or "")
    a["fix_verify"] = []
    if project:                    # fixes applied here: note this chat's waste, and say what each fix saved
        fixer.record_seen(a, project)
        a["fix_verify"] = fixer.verify_lines(project)
        a["fix_done"] = {v["id"]: v["at"] for v in fixer.verify(project) if v.get("id") and v.get("at")}
        fixer.for_report(a, project)
    out = None
    if not args.json and not (getattr(args, "live", False) and not guard.reports_on()):
        REPORTS.mkdir(parents=True, exist_ok=True)
        out = REPORTS / f"{s.session_id}.html"
        out.write_text(report.render(a), encoding="utf-8", errors="replace")   # an old guard log's broken character: '?'
        a["reports_trimmed"] = trim_reports(out, out.parent)
        if a["guard_all"]:                 # the report's "Open the full guard report" opens this page, next to it
            try:
                from .guard_report import write as write_guard_report
                write_guard_report(out=REPORTS / GUARD_PAGE)
            except OSError:
                pass
    return a, out


def guard_totals(cwd: str | None) -> dict | None:
    from .guard_report import guard_totals as totals      # lives there; tests patch it here
    return totals(cwd)


def cmd_list(args) -> int:
    loose: list = []
    found = locate.sessions(args.project, unlinked=loose)
    if not found and not loose:
        print(f"No Claude Code, Codex, Copilot CLI, Antigravity or Cursor sessions found for {args.project or Path.cwd()}" + zfile.codex_note("\n"))
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
        if len(found) > args.limit:
            print(f"\nShowing {args.limit} of {len(found)}. All of them: sessioncost list --limit {len(found)}")
    else:
        print(f"No session is recorded for {args.project or Path.cwd()}.")
    if loose:
        print("\nThe agent did not record a folder for these, so they may belong to any project:")
        rows(loose)
    print(zfile.codex_note("\n", "\n") + "\nOpen one: sessioncost <session>   (its first characters are enough)")
    return 0


def cmd_fix(argv: list[str]) -> int:
    """`sessioncost fix`: this project's token-waste to-do list, the change for each, applied on yes (backup + undo).
    `sessioncost fix auto on|off`: the same list after each answer, in the chat. Token waste only, never safety."""
    from . import fixer
    if argv[:1] == ["auto"]:
        way = argv[1] if len(argv) > 1 else ""
        if way not in ("", "on", "off"):
            print("sessioncost fix auto: use `sessioncost fix auto on`, `... off`, or `sessioncost fix auto` for its state.")
            return 2
        print(fixer.auto_text() if not way else fixer.set_auto(way == "on"))
        if way == "on":
            for line in fix_hooks():
                print(line)
        return 0
    if argv[:1] == ["learn"]:      # your agent, at a handover: a token waste SessionCost's checks do not know yet
        lp = argparse.ArgumentParser(prog="sessioncost fix learn",
                                     description="Keep a token waste your agent found, and the rule that prevents it: "
                                                 "`sessioncost fix` offers that rule in every project from then on.")
        lp.add_argument("what", help="what happened, one line")
        lp.add_argument("--rule", required=True, help="one rule that prevents it, one line")
        lp.add_argument("--project", help="the project it was found in (default: the current folder)")
        lp.add_argument("--agent", help="the agent that found it (default: the agent of this project's newest chat)")
        la = lp.parse_args(argv[1:])
        project = la.project or os.getcwd()
        code, msg = fixer.learn(la.what, la.rule, project, agent=la.agent or newest_agent(project))
        print(msg)
        return code
    if argv[:1] == ["long-chat"]:  # when a chat counts as long: the handover offer
        if len(argv) < 2:
            print(f"SessionCost fix: a chat over {fixer._tok(fixer.long_chat())} tokens gets the handover offer. "
                  'To change it, say "warn me at 150k tokens" (or type `sessioncost fix long-chat 150k`).')
            return 0
        code, msg = fixer.set_long_chat(argv[1])
        print(msg)
        return code
    if argv[:1] == ["budget"]:     # per-chat dollar or token ceiling for the handover offer
        if len(argv) < 2:
            print(fixer.budget_text())
            return 0
        code, msg = fixer.set_budget(argv[1])
        print(msg)
        return code
    ap = argparse.ArgumentParser(
        prog="sessioncost fix",
        description="This project's token waste, newest chat first: what to change, the change itself, applied on "
                    "your yes with a backup (`--undo` puts it back). `sessioncost fix auto on|off`: the same list "
                    f"after each answer, in the chat. Token waste only: the live guard (/{GUARD_CMD}) is for safety.")
    ap.add_argument("target", nargs="?", default="last",
                    help="'last' (the newest chat of this project, the default), a session id, or a log file")
    ap.add_argument("--project", help="project folder (default: the current folder)")
    ap.add_argument("--preview", "--dry-run", dest="preview", action="store_true", help="show the list and changes only")
    ap.add_argument("-y", "--yes", dest="yes", action="store_true",
                    help="apply without asking (for your agent, after you said yes in the chat)")
    ap.add_argument("--only", help="apply only these items (their ids, comma-separated, as the list shows them)")
    ap.add_argument("--undo", action="store_true", help="put back this project's newest fix")
    ap.add_argument("--handover", action="store_true", help="print where the handover of this project's last chat is")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in one")
    args = ap.parse_args(argv)
    project = fixer.project_path(args.project or os.getcwd())     # as given: the key itself follows links
    if args.handover:
        h = fixer.find_handover(project)
        print(h.as_posix() if h else f"No handover for {project} yet: a long chat gets one from `sessioncost fix`.")
        return 0 if h else 1
    if args.undo:
        code, msg = fixer.undo(project)
        print(msg)
        return code
    path, newest = find_session(args, chat=args.target), args.target == "last"
    if path is not None and not path.is_file() and not newest:
        path, newest = find_session(args, chat="last"), True   # a chat the live tip named but no log is found for
    if path is None:
        return 1
    try:
        a = analyse(readers.read(path), pricemod.load(args.prices))
    except (readers.NotSupported, ValueError, OSError) as e:
        print(f"sessioncost fix: could not read that chat: {e}")
        return 2
    fixer.record_seen(a, project)
    plan = fixer.plan(a, project, only={x.strip() for x in args.only.split(",") if x.strip()} if args.only else None)
    print(fixer.render(plan, fixer.verify_lines(project), newest=newest))
    fixer.mark_shown(plan)         # the live tip does not list these again in that chat
    if not any(i.can_apply or i.kind == "handover" for i in plan.items) or args.preview:
        return 0
    if not args.yes:
        try:
            yes = input("\nApply these? [y/N] ").strip().lower() in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            yes = False
        if not yes:
            print("Nothing applied. (An agent runs this with -y, and only after you said yes in the chat.)")
            return 0
    code, msg = fixer.apply(plan)
    print("\n" + msg)
    return code


def newest_agent(project) -> str:
    """The agent of the project's newest chat (for a rule learned there); "" when there is none."""
    try:
        found = locate.sessions(project)
        return readers.read(found[0][1]).agent if found else ""
    except Exception:              # a learned rule is kept anyway, just without the agent's name
        return ""


def fix_hooks(home: Path | None = None) -> list[str]:
    """`fix auto on` needs the hooks `sessioncost setup` puts into each agent (they read the switch); the guard itself
    stays as it was. Adds them where they are missing."""
    from . import guard
    home = home or Path.home()
    out = []
    for agent, folder, _, name in SKILL_HOMES:
        if not agent_installed(folder, home):
            continue
        if name == "claude" and claude_plugin_installed(home):
            out.append(f"{agent:12} the SessionCost plugin brings the hooks (update it if it is older than this copy)")
            continue
        try:
            text = guard._hooks_file(name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "sessioncost" in text.lower() and " hook " in text:
            out.append(f"{agent:12} hooks in place")
            continue
        guard.setup(name)
        out.append(f"{agent:12} hooks added (the guard stays off unless you turn it on); restart {agent}")
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["guard"], ["prices"]):   # the live guard's own commands (`guard on|off|auto on|...`), and prices
        from . import guard, prices_cmd
        return (guard.main if argv[0] == "guard" else prices_cmd.main)(argv[1:])
    if argv[:1] == ["auto"] and argv[1:2] in (["on"], ["off"]):   # each chat's report after each answer (on by default)
        from . import guard
        print(guard.command(f"reports-{argv[1]}", os.getcwd()))
        return 0
    if argv[:1] == ["fix"]:        # `sessioncost fix ...`: its own options (see cmd_fix)
        return cmd_fix(argv[1:])
    ap = argparse.ArgumentParser(
        prog="sessioncost",
        description="See where every token of your agent session went, turn by turn, and what to fix. "
                    "Local: reads log files on this computer and sends nothing anywhere.")
    ap.add_argument("target", nargs="?", default="last",
                    help="'last' (newest Claude Code, Codex, Copilot CLI, Antigravity or Cursor session of this project, the default), 'fix' (this project's token waste and the change for each, applied on your yes with a backup; `sessioncost fix --undo` puts it back; `sessioncost fix auto on|off`: the list after each answer, in the chat), 'list', 'serve' (a live report page on this computer only, with a Refresh button; Ctrl+C stops it), 'setup' (add /sessioncost to your agents),'clean' (delete all saved reports but the latest), 'update' (update every copy of SessionCost on this computer), 'version' (this copy's version; --all lists every copy on this computer), 'prices' (the prices used, where each came from and its date; `sessioncost prices set|import|team|update`),'auto' (`sessioncost auto on|off`: rebuild each chat's report after each answer, on by default), 'guard' (the live guard: `sessioncost guard on|off|auto on|off`; `sessioncost guard` alone shows its state), or a session file (.jsonl, or an Antigravity .db)")
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
    ap.add_argument("--port", type=int, default=0, help="serve: the port on this computer (default: a free one)")
    ap.add_argument("--dry-run", action="store_true", help="update: show the steps without running them")
    ap.add_argument("--all", action="store_true", help="version: list every copy on this computer, not only this one")
    ap.add_argument("--version", action="version", version=f"sessioncost {__version__}")
    ap.add_argument("--live", action="store_true", help=argparse.SUPPRESS)   # the hooks' run after each answer
    args = ap.parse_args(argv)

    if args.target in ("setup", "clean"):
        return cmd_setup() if args.target == "setup" else cmd_clean()
    if args.target == "update":
        from . import update
        return update.run(dry_run=args.dry_run)
    if args.target == "version":
        from . import update
        return update.show_versions(every=args.all)
    if args.target == "list":
        return cmd_list(args)
    if args.target == "serve":
        from . import serve
        return serve.run(args)
    path = find_session(args)
    if path is None:
        return 1
    try:
        a, out = build(path, args)
    except (readers.NotSupported, ValueError) as e:
        print(f"sessioncost: {e}")
        return 2
    if args.live:                  # after an answer, in the background: the to-do list for the next reply
        from . import fixer
        if fixer.auto_on():
            fixer.write_note(a, a["fix_project"] or os.getcwd())
        return 0
    if args.sarif:
        from . import sarif
        Path(args.sarif).write_text(json.dumps(sarif.build(a, path), indent=1, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(a, indent=1, ensure_ascii=False, default=str))
        return 0
    from .status import block      # what SessionCost is doing, before any number (NEXT-SESSION item 7)
    print(block(a, a.get("fix_project") or os.getcwd()))
    print(f"Report: {Path(out).as_posix()}" if args.brief else summary(a, out))
    if args.sarif:
        print(f"SARIF: {args.sarif}")
    gone, freed = a.get("reports_trimmed") or (0, 0)
    if gone:
        print(f"Old reports: deleted {gone} ({size(freed)}); the newest {KEEP_REPORTS} are kept.")
    if out and not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def find_session(args, chat: str | None = None) -> Path | None:
    """The chat's log file: 'last' = the newest session of the project, else a path or a session id's first
    characters. None (and says why) when there is none."""
    target = chat or args.target
    if target == "last":
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
            return None
        return found[0][1]
    path = Path(target).expanduser()
    if not path.is_file():   # a session id (or its first characters) from `sessioncost list`
        loose = []
        hits = [p for _, p in locate.sessions(args.project, unlinked=loose) + loose
                if locate.session_id(p).startswith(target)]
        if len(hits) == 1:
            path = hits[0]
    return path
