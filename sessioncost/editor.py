"""`sessioncost editor --json --project <folder>`: one short answer for the editor extension's status bar.

The extension (editor/ in this repo) shows only what this prints and runs the other `sessioncost` commands for the
rest; it holds no logic of its own. Read on this computer only; nothing is sent anywhere. The answer:

    schema      1; the extension checks it and offers the matching SessionCost when it differs
    version     this SessionCost's version
    project     the folder asked about
    guard       {"on", "strict", "dir"}: dir is the guard's folder (the extension watches it for changes)
    tips        {"on"}: token tips after each answer
    chat        this folder's newest chat, any agent, or null: {"id", "agent", "agent_label", "title", "log",
                "sent", "usd", "tokens_exact", "guard": {"checked", "blocked", "asked", "intact"} or null,
                "last_stop": {"ts", "decision", "what", "why"} or null, "last_tip"}
    handover    the handover file to continue from, or null
    watch       files whose change means this answer is out of date (the chat's log, its guard log, the switches)
    watch_dirs  folders where a new chat of this folder appears (any agent): a new entry there means "ask again"
    error       only when this chat could not be read: why, in plain words
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import __version__

SCHEMA = 1


def _last_stop(sid: str) -> dict | None:
    """The newest step the guard blocked or asked about in this chat, with its reason in plain words."""
    from .analyse import _hide_keys
    from .guard_checks import WHY
    from .guard_store import log_path, verify_status
    p = log_path(sid)
    if not sid or not p.is_file():
        return None
    for e in reversed(verify_status(p)["entries"]):
        if e.get("event") == "pre" and e.get("decision") in ("blocked", "asked"):
            return {"ts": e.get("ts", ""), "decision": e["decision"], "what": _hide_keys(e.get("what", ""))[:200],
                    "why": "; ".join(dict.fromkeys(WHY.get(m, m) for m in e.get("marks") or [])) or "a risky step"}
    return None


def _guard_counts(g: dict | None) -> dict | None:
    if not g:
        return None
    return {**{k: int(g.get(k) or 0) for k in ("checked", "blocked", "asked")}, "intact": bool(g.get("intact", True))}


def _chat(project: str) -> tuple[dict | None, str]:
    from . import locate, prices, readers
    from .analyse import analyse
    from .fixer_store import _load, chat_file
    found = locate.sessions(project)
    if not found:
        return None, ""
    path = found[0][1]
    try:
        a = analyse(readers.read(path), prices.load(None), engines=[])
    except Exception as e:  # noqa: BLE001 (a broken log must not break the status bar: it says why instead)
        return None, f"could not read the newest chat ({Path(path).name}): {e}"
    meta, t = a["meta"], a["totals"]
    sid = str(meta.get("session_id") or "")
    last = (_load(chat_file(sid), {}).get("last_tip") or {}).get("line") if sid else ""
    return {"id": sid, "agent": meta.get("agent", ""), "agent_label": meta.get("agent_label", ""),
            "title": str(meta.get("title") or "")[:120], "log": str(path),
            "sent": int(t.get("sent") or 0), "usd": round(float(t.get("usd") or 0), 4),
            "tokens_exact": bool((a.get("money") or {}).get("tokens_exact", True)),
            "guard": _guard_counts(a.get("guard")), "last_stop": _last_stop(sid),
            "last_tip": str(last or "").replace("💡 SessionCost: ", "").strip()}, ""


def _nearest(p: Path) -> Path | None:
    """p, or its closest folder that exists (a folder made later still shows up as a new entry there)."""
    for q in [p, *p.parents]:
        if q.is_dir():
            return q
    return None


def watch_dirs(project: str) -> list[str]:
    """The folders where a new chat of this project appears, any agent: a new entry there means a new chat may have
    started. Cursor keeps every chat in one database, which changes all the time; the bar checks it on its timer."""
    from datetime import datetime

    from . import locate
    from .readers.antigravity import antigravity_homes
    from .readers.copilot import copilot_home
    from .readers.vscode_chat import folder_of, vscode_homes
    want = [locate.project_dir(project) or locate.claude_home() / "projects",
            locate.codex_home() / "sessions" / datetime.now().strftime("%Y/%m/%d"),
            copilot_home() / "session-state"]
    want += [base / "conversations" for base in antigravity_homes()]
    for home in vscode_homes():
        ws = home / "workspaceStorage"
        for d in sorted(ws.glob("*/chatSessions")) if ws.is_dir() else []:
            folder = folder_of(d / "x")      # the window's folder, from workspace.json beside chatSessions
            if folder and locate._same_dir(folder, project):
                want.append(d)
    out = []
    for p in want:
        q = _nearest(p)
        if q is not None and q != q.parent and str(q) not in out and q != Path.home():
            out.append(str(q))
    return out


def _cursor_db() -> list[str]:
    """Cursor's chat database, when there is one: a new Cursor chat changes it (checked on the bar's timer)."""
    from .readers.cursor import state_db
    p = state_db()
    return [str(p)] if p.is_file() else []


def answer(project: str) -> dict:
    from .fixer_store import auto_on, find_handover, project_path
    from .guard_store import _read, guard_on, home, log_path
    cfg = _read(home() / "config.json", {})
    project = str(project_path(project))
    chat, error = _chat(project)
    h = find_handover(project)
    out = {"schema": SCHEMA, "version": __version__, "project": project,
           "guard": {"on": guard_on(cfg), "strict": bool(guard_on(cfg) and cfg.get("strict")), "dir": str(home())},
           "tips": {"on": auto_on()}, "chat": chat, "handover": str(h) if h else None,
           # Cursor's database changes whenever Cursor is used: watched only when this folder's chat is Cursor's (its
           # numbers live there) or there is none yet; otherwise the bar's minute timer finds a new Cursor chat
           "watch": list(dict.fromkeys([str(home() / "config.json")]
                                       + ([chat["log"], str(log_path(chat["id"]))] if chat else [])
                                       + (_cursor_db() if not chat or chat["agent"] == "cursor" else []))),
           "watch_dirs": watch_dirs(project)}
    if error:
        out["error"] = error
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="sessioncost editor",
                                 description="One short answer for the editor extension's status bar (JSON).")
    ap.add_argument("--json", action="store_true", help="print JSON (the only form there is)")
    ap.add_argument("--project", help="project folder (default: the current folder)")
    args = ap.parse_args(argv)
    print(json.dumps(answer(args.project or os.getcwd()), ensure_ascii=False))
    return 0
