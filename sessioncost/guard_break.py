"""A safe point for a new chat, in the middle of a long run (owner, 2026-11-23): one long turn can grow a chat from
100k to 360k tokens with no message from the user, so the handover offer at the next message comes too late.

Never in the middle of work: only right after a stage is finished and saved (a commit, a push or a pull request
that went through), and only when the chat is over the handover size (200k, or the user's own `fix long-chat`).
The agent's next step is then turned back once, with what to do instead: reply to the user with what is done and
what is next, and ask "handover" or "continue". A push or pull request right after the commit finishes the same
stage, so it goes through first. Said again only after the chat grew by another handover size.

Works for every agent the guard runs in (Claude Code, Codex, Cursor, Antigravity, Copilot): it uses the same
"turn this step back, with a reason" answer each of them takes. In Cursor only shell, tool-server and file-read
steps can be turned back, so the note waits for the next one of those.
Runs when the guard or token tips are on (as guard_waste.py). Never stops anything when the chat's size cannot be
read: it stays quiet."""
from __future__ import annotations

import re
from pathlib import Path

from .guard_store import _read, _safe_id, _write, home

# git push -n is a dry run too (git commit -n is --no-verify: a real commit)
STAGE = re.compile(r"(?:^|[\s;&|(])(?:git\s+(?:-C\s+\S+\s+)?(?:commit|push(?![^;&|]*\s-[A-Za-z]*n\b))\b"
                   r"(?![^;&|]*--dry-run)|"
                   r"gh\s+pr\s+(?:create|merge)\b|glab\s+mr\s+(?:create|merge)\b)", re.I)
SAME_STAGE = re.compile(r"(?:^|[\s;&|(])(?:git\s+(?:-C\s+\S+\s+)?push\b|gh\s+pr\s+(?:create|merge|view|checks)\b|"
                        r"glab\s+mr\s+(?:create|merge)\b)", re.I)
FAILED = re.compile(r"exit code:?\s*[1-9]|\bnothing to commit\b|\berror: failed to push\b|\[rejected\]|"
                    r"\bfatal:|\bhook(?:s)? failed\b", re.I)


def break_path(sid: str) -> Path:
    return home() / f"{_safe_id(sid)}.break.json"


def _stage(a) -> str:
    """The finished-stage step this is (commit, push, pull request), or ""."""
    if a.kind != "command":
        return ""
    from .risk_shell import _no_prose
    m = STAGE.search(_no_prose(a.target or ""))           # echo 'next: git push' or grep git push: words, not a stage
    if not m:
        return ""
    w = m.group(0).lower()
    return "a push" if "push" in w else "a commit" if "commit" in w else "a pull request"


TAIL_BYTES = 1_000_000


def tail_context(p: Path) -> int:
    """The newest model call's size from the end of a Claude Code or Codex .jsonl log (a 4 MB log read whole takes
    seconds; its last megabyte is enough), or 0."""
    import json
    if p.suffix != ".jsonl":
        return 0
    try:
        with p.open("rb") as f:
            f.seek(max(0, p.stat().st_size - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        if '"usage"' not in line and '"last_token_usage"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue                          # the first line cut in half by the seek
        m = r.get("message") if isinstance(r.get("message"), dict) else {}
        u = m.get("usage") if r.get("type") == "assistant" and not r.get("isSidechain") else None
        if isinstance(u, dict):               # Claude Code: new + cache written + cache read
            return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        info = (r.get("payload") or {}).get("info") if isinstance(r.get("payload"), dict) else None
        if isinstance(info, dict) and isinstance(info.get("last_token_usage"), dict):
            return int(info["last_token_usage"].get("input_tokens") or 0)    # Codex: input includes the cached part
    return 0


def context_now(inp: dict) -> int:
    """How many tokens this chat sends with each step now (its newest model call), or 0 when that cannot be read."""
    from . import locate, readers
    sid, path = str(inp.get("session_id") or ""), str(inp.get("transcript_path") or "")

    def size(target) -> int:
        try:
            calls = readers.read(target).calls
            return calls[-1].usage.context if calls else 0
        except Exception:                     # an odd or locked log: stay quiet, never stop a step for it
            return 0

    n = 0
    if path and Path(path).is_file():         # Claude Code and Codex name their log: its end is enough
        n = tail_context(Path(path)) or size(Path(path))
    if n or not sid:
        return n
    try:                                      # the others: this chat among the project's chats, by its id
        from .fixer_store import start_folder
        cwd = start_folder(sid, str(inp.get("cwd") or ""))
        target = next((p for _, p in locate.sessions(cwd or None) if locate.session_id(p) == sid), None)
    except Exception:
        return 0
    return size(target) if target is not None else 0


def note(inp: dict) -> None:
    """After a step ran: a finished stage in a big chat is kept, so the agent's next step can be turned back."""
    from .guard_decide import step_of
    from .guard_waste import _on
    sid = str(inp.get("session_id") or "")
    if not sid or inp.get("_failed"):
        return
    a = step_of(str(inp.get("tool_name") or ""), inp.get("tool_input") or {}, str(inp.get("tool_use_id") or ""))
    what = _stage(a)
    resp = inp.get("tool_response")
    if isinstance(resp, dict):                # Claude Code: {"stdout": ..., "stderr": ...}
        resp = "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr", "output", "error"))
    if isinstance(resp, str):                 # git echoes "[main abc1234] <subject>": the subject's words are not an error
        resp = re.sub(r"(?m)^\[[^\]\n]+ [0-9a-f]{7,40}\] .*$", "", resp)
    if not what or (isinstance(resp, str) and FAILED.search(resp[-2000:])) or not _on(sid):
        return
    from .fixer_store import long_chat
    size, st = long_chat(), _read(break_path(sid), {})
    ctx = context_now(inp)
    if ctx < size or ctx < int(st.get("said_at") or 0) + size:
        return
    _write(break_path(sid), {**st, "pending": {"what": what, "context": ctx}})


def stop(inp: dict) -> dict | None:
    """Before a step: the agent's first step after a finished stage in a big chat is turned back once, with what to
    say to the user. A push or pull request finishing the same stage goes through first."""
    sid = str(inp.get("session_id") or "")
    st = _read(break_path(sid), {}) if sid else {}
    p = st.get("pending")
    if not p:
        return None
    from .guard_waste import _on
    if not _on(sid):                          # the guard and tips were turned off since: nothing to stop
        return None
    from .guard_decide import step_of
    a = step_of(str(inp.get("tool_name") or ""), inp.get("tool_input") or {}, str(inp.get("tool_use_id") or ""))
    if a.kind == "command" and SAME_STAGE.search(a.target or ""):
        return None
    ctx = int(p.get("context") or 0)
    _write(break_path(sid), {"said_at": ctx})
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason(inp, str(p.get("what") or "a commit"), ctx)}}


def new_message(inp: dict) -> None:
    """The user wrote again: a stage finished in the last turn is no reason to stop the new task's first step."""
    sid = str(inp.get("session_id") or "")
    st = _read(break_path(sid), {}) if sid else {}
    if st.get("pending"):
        _write(break_path(sid), {k: v for k, v in st.items() if k != "pending"})


def reason(inp: dict, what: str, ctx: int) -> str:
    from . import fresh_chat
    from .fixer import ON_TOP
    from .fixer_store import _tok, handover_file, start_folder
    sid = str(inp.get("session_id") or "")
    project = start_folder(sid, str(inp.get("cwd") or "")) or "."
    h = handover_file(project).as_posix()
    size = _tok(ctx)
    return (f"SessionCost: a safe point for a new chat. The work so far is saved ({what} just went through), and this "
            f"chat now sends about {size} tokens with every step. This step was not run. Do not start new work yet: "
            "reply to the user now, in a few lines: what is done, what is next. Then end your reply with this line, "
            f"as written: \"This chat is about {size} tokens, and every next step sends all of it again. Say "
            "**handover** and I write a handover for a new chat, or **continue** to keep going here.\" "
            f"If they say handover: write it at the top of {h} ({ON_TOP}): the goal, what is done, files changed, "
            "tests run, what is next; then tell them to start a new chat and say \"continue from handover\" there."
            + fresh_chat.handover_words(str(inp.get("_agent") or "claude"), Path(project).as_posix())
            + " If they say continue: carry on with the step that was not run.")
