"""After each answer: the auto report build and the live fix tip."""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from .guard_store import _read, _safe_id, _write, fix_auto_on, home

AUTO_GAP_S = 60       # auto mode: at most one report build a minute per chat
AUTO_KEEP_S = 2 * 86400   # a chat the guard is not on for: when its report was last built, kept two days in auto.json


def reports_on() -> bool:
    """Reports after each answer (#14): on unless you turned them off (`sessioncost auto off`)."""
    return _read(home() / "config.json", {}).get("auto", True) is not False


def _fix_auto() -> bool:
    """`sessioncost fix auto on`: after each answer, look for new token waste; the next reply lists it (fixer.py)."""
    return fix_auto_on(_read(home() / "config.json", {}))


# The to-do list reaches the agent where its hooks can add text for the model, and only at the start of a reply
# (the agent is told to say it as the reply's last line):
# Claude Code and Codex at the user's next message (UserPromptSubmit additionalContext); Antigravity before the first
# model call of a turn (PreInvocation injectSteps, antigravity.google/docs/hooks); Cursor and Copilot drop what a
# prompt hook answers (cursor.com/docs/hooks, docs.github.com hooks-configuration), so there it comes when the next
# chat starts (sessionStart additional context).
TIP_EVENTS = {"claude": ("prompt", "session-start"), "codex": ("prompt", "session-start"),
              "cursor": ("session-start",), "copilot": ("session-start",), "antigravity": ("invocation",)}


def _fix_tip(event: str, inp: dict, raw: dict, agent: str) -> str:
    if event not in TIP_EVENTS.get(agent, ()):
        return ""
    from . import fixer
    sid = str(inp.get("session_id") or "")
    cwd = fixer.start_folder(sid, str(inp.get("cwd") or ""))    # the project: where the chat started, not where it is now
    if agent == "antigravity":                # runs before every model call: once per user message
        said = str(raw.get("lastUserInput") or "")
        return fixer.tip(sid, cwd, prompt=said, turn=hashlib.sha1(said.encode()).hexdigest()[:16], agent=agent)
    return fixer.tip(sid, cwd, prompt=str(inp.get("prompt") or ""), start=event == "session-start", agent=agent)


def _with_tip(event: str, out: dict | None, tip: str, agent: str) -> dict | None:
    if not tip:
        return out
    out = dict(out or {})
    hs = dict(out.get("hookSpecificOutput") or {"hookEventName": "SessionStart" if event == "session-start"
                                                 else "UserPromptSubmit"})
    hs["additionalContext"] = (str(hs.get("additionalContext") or "") + "\n\n" + tip).strip()
    out["hookSpecificOutput"] = hs
    return out


def _auto_report(inp: dict, st: dict) -> str:
    """Auto mode: build this chat's report in the background after each answer (never waits for it). The first
    time in a chat it returns a line naming where the report is."""
    import subprocess
    reports, fix = reports_on(), _fix_auto()
    if not (reports or fix) or time.time() - st.get("report_at", 0) < AUTO_GAP_S:
        return ""
    st["report_at"] = time.time()
    tp = str(inp.get("transcript_path") or "")
    target = tp if tp and os.path.isfile(tp) and tp.endswith((".jsonl", ".db")) else "last"
    root = Path(__file__).resolve().parent.parent
    run = [str(root / "run.py")] if (root / "run.py").is_file() else ["-m", "sessioncost"]
    # --live: also write this chat's to-do list (fix auto), and build the report only when reports are on
    from .fixer_store import start_folder
    cwd = start_folder(str(inp.get("session_id") or ""), str(inp.get("cwd") or ""))
    cmd = [sys.executable, *run, target, "--no-open", *(["--project", cwd] if cwd else []),
           *(["--live"] if fix else [])]
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, creationflags=flags, start_new_session=os.name != "nt")
    except OSError:
        return ""
    if st.get("report_told") or not reports:
        return ""
    st["report_told"] = True
    where = Path.home() / ".sessioncost" / "reports" / (f"{Path(target).stem}.html" if target != "last" else "")
    return f"SessionCost report (updated after each answer): {where}"


def _report_unguarded(inp: dict) -> dict | None:
    """A chat the guard is not on for: only its report after each answer, timed in auto.json (one entry a chat)."""
    if not (reports_on() or _fix_auto()):
        return None
    p, sid, now = home() / "auto.json", _safe_id(inp.get("session_id")), time.time()
    done = {k: v for k, v in _read(p, {}).items() if isinstance(v, dict) and now - v.get("at", 0) < AUTO_KEEP_S}
    st = {"report_at": done.get(sid, {}).get("at", 0), "report_told": done.get(sid, {}).get("told", False)}
    line = _auto_report(inp, st)
    if st["report_at"] != done.get(sid, {}).get("at", 0):
        done[sid] = {"at": st["report_at"], "told": st.get("report_told", False)}
        _write(p, done)
    return {"systemMessage": line} if line else None
