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

# Our hosted fix engine (the deeper fixes). Nothing goes there unless you press the button in the report.
DEFAULT_ENGINE_URL = "https://sessioncost-engine.onrender.com"
REPORTS = Path.home() / ".sessioncost" / "reports"


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
    lines.append(f"Cost: {money(t['usd'])} {mo['short']}")
    if path:
        lines.append(f"Report: {path}")
    return "\n".join(lines)


OLD_REPORTS_HINT = 10      # past this many older reports, the summary says how to delete them (it never asks)


def old_reports(keep: Path | None, folder: Path = REPORTS) -> list[Path]:
    """Every saved report except the one to keep (the current one; by default the newest)."""
    files = sorted(folder.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True) if folder.is_dir() else []
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
    kept = sorted(folder.glob("*.html"))
    print(f"Deleted {plural(len(old), 'older report')} ({size(freed)})." +
          (f" Kept the latest: {kept[0]}" if kept else ""))
    return 0


def build(path, args) -> tuple[dict, Path | None]:
    s = readers.read(path)
    if args.cursor_usage:
        if s.agent != "cursor":
            print("sessioncost: --cursor-usage is for Cursor chats; ignored")
        else:
            used = cursor.apply_usage_csv(s, cursor.read_usage_csv(args.cursor_usage))
            print(f"sessioncost: {used} requests from the usage export matched this chat" if used else
                  "sessioncost: no row of the usage export falls inside this chat's turns (check the export's dates)")
    a = analyse(s, pricemod.load(args.prices), subscription=args.subscription)
    # where "Find deeper fixes" may send the summary: only when you press the button in the report. "off" hides it.
    url = (getattr(args, "engine_url", None) or os.environ.get("SESSIONCOST_ENGINE_URL") or DEFAULT_ENGINE_URL).strip()
    a["meta"]["engine_url"] = "" if url.lower() in ("off", "none", "no", "0") else url
    out = None
    args.extra = []
    if not args.json:
        REPORTS.mkdir(parents=True, exist_ok=True)
        design = getattr(args, "design", "new") or "new"
        out = REPORTS / f"{s.session_id}.html"
        out.write_text(report.render(a, "board" if design == "new" else "classic"), encoding="utf-8")
        if design == "both":            # the new design next to the old one, to compare them
            new = REPORTS / f"{s.session_id}-new.html"
            new.write_text(report.render(a, "board"), encoding="utf-8")
            args.extra.append(new)
    return a, out


SKILL = Path(__file__).parent / "skill" / "SKILL.md"
SKILL_HOMES = [   # (agent, its folder, where its personal skills live inside it)
    ("Claude Code", ".claude", "skills"),
    ("Codex", ".codex", "skills"),
    ("Antigravity", ".gemini", "config/skills"),
    ("Cursor", ".cursor", "skills"),
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
    """Add the /sessioncost skill to every agent installed for this user. Returns (agent, skill file written), with
    None for an agent whose SessionCost plugin already provides /sessioncost."""
    home = home or Path.home()
    # The skill names this exact Python, so it runs even when no 'python' or 'sessioncost' is on the PATH.
    exe = Path(sys.executable).as_posix()
    run = f'"{exe}" -m sessioncost' if " " in exe else f"{exe} -m sessioncost"
    text = SKILL.read_text(encoding="utf-8").replace("{{RUN}}", run).replace("{{VERSION}}", __version__)
    if " " in exe:
        text = text.replace("   If that fails because", "   In PowerShell, put `& ` in front of the quoted path. If that fails because")
    done = []
    for agent, folder, sub in SKILL_HOMES:
        if not (home / folder).is_dir():
            continue
        dest = home / folder / sub / "sessioncost" / "SKILL.md"
        if plugin_installed(folder, home):
            # Two /sessioncost in one agent confuse people: remove a copy an earlier setup wrote, add none.
            if dest.is_file() and "name: sessioncost" in dest.read_text(encoding="utf-8", errors="replace"):
                dest.unlink()
                if not any(dest.parent.iterdir()):
                    dest.parent.rmdir()
            done.append((agent, None))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        done.append((agent, dest))
    return done


def cmd_setup() -> int:
    done = setup()
    if not done:
        print("No Claude Code, Codex, Antigravity or Cursor folder found for this user; nothing to set up.")
        return 1
    for agent, dest in done:
        print(f"{agent:12} /sessioncost added ({dest})" if dest else
              f"{agent:12} /sessioncost comes from the SessionCost plugin; nothing added")
    print("\nRestart the agent if it is open, then type /sessioncost in a chat.")
    return 0


def cmd_list(args) -> int:
    loose: list = []
    found = locate.sessions(args.project, unlinked=loose)
    if not found and not loose:
        print(f"No Claude Code, Codex, Antigravity or Cursor sessions found for {args.project or Path.cwd()}")
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
    ap = argparse.ArgumentParser(
        prog="sessioncost",
        description="See where every token of your agent session went, turn by turn, and what to fix. "
                    "Local: reads log files; sends nothing unless you press 'Send the summary to the fix engine' in the report.")
    ap.add_argument("target", nargs="?", default="last",
                    help="'last' (newest Claude Code, Codex, Antigravity or Cursor session of this project, the default), 'list', 'setup' (add /sessioncost to your agents), 'clean' (delete all saved reports but the latest), 'update' (update every copy of SessionCost on this computer), or a session file (.jsonl, or an Antigravity .db)")
    ap.add_argument("--project", help="project folder (default: the current folder)")
    ap.add_argument("--no-open", action="store_true", help="write the report but do not open it")
    ap.add_argument("--json", action="store_true", help="print the full model as JSON instead of a report")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in one")
    ap.add_argument("--subscription", action="store_true",
                    help="you are on a subscription plan (Claude Pro/Max, ChatGPT): label money as API-equivalent, not billed")
    ap.add_argument("--cursor-usage", metavar="CSV",
                    help="Cursor only: your usage export from the Cursor dashboard, for exact token counts")
    ap.add_argument("--limit", type=int, default=10, help="list: how many sessions (default 10)")
    ap.add_argument("--design", choices=["new", "classic", "both"], default="new",
                    help="report design: new (the session board, the default), classic (the turn list), or both side by side")
    ap.add_argument("--engine-url", metavar="URL",
                    help="the fix engine offered under 'Find deeper fixes' (or set SESSIONCOST_ENGINE_URL; default: "
                         f"{DEFAULT_ENGINE_URL}; 'off' hides the button). The summary is sent only when you "
                         "press the button in the report")
    ap.add_argument("--dry-run", action="store_true", help="update: show the steps without running them")
    ap.add_argument("--version", action="version", version=f"sessioncost {__version__}")
    args = ap.parse_args(argv)

    if args.target == "setup":
        return cmd_setup()
    if args.target == "clean":
        return cmd_clean()
    if args.target == "update":
        from . import update
        return update.run(dry_run=args.dry_run)
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
            print(f"No Claude Code, Codex, Antigravity or Cursor sessions found for {args.project or Path.cwd()} "
                  f"(looked in {locate.claude_home() / 'projects'}, {locate.codex_home() / 'sessions'} and "
                  f"{locate.codex_home() / 'archived_sessions'}, {locate.antigravity_home() / 'conversations'}, "
                  "and Cursor's chats)")
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
    if args.json:
        print(json.dumps(a, indent=1, ensure_ascii=False, default=str))
        return 0
    print(summary(a, out))
    for x in args.extra:
        print(f"New design: {x}")
    old = [f for f in old_reports(out) if f not in args.extra] if out else []
    if len(old) > OLD_REPORTS_HINT:
        print(f"Old reports: {len(old)} ({size(sum(f.stat().st_size for f in old))}) · delete them with: sessioncost clean")
    if out and not args.no_open:
        webbrowser.open(out.resolve().as_uri())
        for x in args.extra:
            webbrowser.open(x.resolve().as_uri())
    return 0
