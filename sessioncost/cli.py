"""sessioncost last | list | <session.jsonl>  - see README.md."""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__, locate, prices as pricemod, readers, report
from .analyse import analyse
from .readers import cursor

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
    lines = [f"SessionCost: {title}"]
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
    out = None
    if not args.json:
        REPORTS.mkdir(parents=True, exist_ok=True)
        out = REPORTS / f"{s.session_id}.html"
        out.write_text(report.render(a), encoding="utf-8")
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


def setup(home: Path | None = None) -> list[tuple[str, Path | None]]:
    """Add the /sessioncost skill to every agent installed for this user. Returns (agent, skill file written), with
    None for Claude Code when the plugin already provides /sessioncost."""
    home = home or Path.home()
    # The skill names this exact Python, so it runs even when no 'python' or 'sessioncost' is on the PATH.
    exe = Path(sys.executable).as_posix()
    run = f'"{exe}" -m sessioncost' if " " in exe else f"{exe} -m sessioncost"
    text = SKILL.read_text(encoding="utf-8").replace("{{RUN}}", run)
    if " " in exe:
        text = text.replace("   If that fails because", "   In PowerShell, put `& ` in front of the quoted path. If that fails because")
    done = []
    for agent, folder, sub in SKILL_HOMES:
        if not (home / folder).is_dir():
            continue
        dest = home / folder / sub / "sessioncost" / "SKILL.md"
        if folder == ".claude" and claude_plugin_installed(home):
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
    found = locate.sessions(args.project)
    if not found:
        print(f"No Claude Code, Codex, Antigravity or Cursor sessions found for {args.project or Path.cwd()}")
        return 1
    P = pricemod.load(args.prices)
    print(f"{'When':16}  {'Agent':11}  {'Session':8}  {'Cost':>8}  {'Turns':>5}  {'Calls':>5}  First message")
    for when, p in found[: args.limit]:
        try:
            a = analyse(readers.read(p), P, engines=[])
        except Exception as e:  # one broken log must not hide the others
            print(f"{when.astimezone():%Y-%m-%d %H:%M}  {'':11}  {locate.short_id(p)}  {'?':>8}  {'':>5}  {'':>5}  (could not read: {e})")
            continue
        t = a["totals"]
        title = a["meta"]["title"]
        print(f"{when.astimezone():%Y-%m-%d %H:%M}  {a['meta']['agent_label']:11}  {locate.short_id(p)}  {money(t['usd']):>8}  {t['turns']:>5}  {t['calls']:>5}  "
              f"{title[:60]}{'…' if len(title) > 60 else ''}")
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
                    "Local only: reads log files, sends nothing anywhere.")
    ap.add_argument("target", nargs="?", default="last",
                    help="'last' (newest Claude Code, Codex, Antigravity or Cursor session of this project, the default), 'list', 'setup' (add /sessioncost to your agents), or a session file (.jsonl, or an Antigravity .db)")
    ap.add_argument("--project", help="project folder (default: the current folder)")
    ap.add_argument("--no-open", action="store_true", help="write the report but do not open it")
    ap.add_argument("--json", action="store_true", help="print the full model as JSON instead of a report")
    ap.add_argument("--prices", help="a prices.json to merge over the built-in one")
    ap.add_argument("--subscription", action="store_true",
                    help="you are on a subscription plan (Claude Pro/Max, ChatGPT): label money as API-equivalent, not billed")
    ap.add_argument("--cursor-usage", metavar="CSV",
                    help="Cursor only: your usage export from the Cursor dashboard, for exact token counts")
    ap.add_argument("--limit", type=int, default=10, help="list: how many sessions (default 10)")
    ap.add_argument("--version", action="version", version=f"sessioncost {__version__}")
    args = ap.parse_args(argv)

    if args.target == "setup":
        return cmd_setup()
    if args.target == "list":
        return cmd_list(args)
    if args.target == "last":
        found = locate.sessions(args.project)
        if not found:
            print(f"No Claude Code, Codex, Antigravity or Cursor sessions found for {args.project or Path.cwd()} "
                  f"(looked in {locate.claude_home() / 'projects'}, {locate.codex_home() / 'sessions'} and "
                  f"{locate.antigravity_home() / 'conversations'}, and Cursor's chats)")
            return 1
        path = found[0][1]
    else:
        path = Path(args.target).expanduser()
        if not path.is_file():   # a session id (or its first characters) from `sessioncost list`
            hits = [p for _, p in locate.sessions(args.project) if locate.session_id(p).startswith(args.target)]
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
    if out and not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0
