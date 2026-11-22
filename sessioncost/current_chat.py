"""The chat the agent running SessionCost is in, from the id the agent passes to every command it runs.

"show my report" used to search every agent's chats on the computer for the newest one of the project: 2.5 s, most of
it in agents the user was not using. Claude Code (CLAUDE_CODE_SESSION_ID) and Codex (CODEX_THREAD_ID) name the chat
itself; its file is found directly. Cursor, Antigravity and Copilot pass no chat id that was checked: they keep the
search."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .locate import claude_home, codex_home

_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{7,63}")      # a chat id; never glob characters


def chat_file(env=None, claude: Path | None = None, codex: Path | None = None) -> Path | None:
    """The current chat's log, or None when the agent named none (a terminal) or its file is not there yet."""
    env = os.environ if env is None else env
    cid = env.get("CLAUDE_CODE_SESSION_ID", "")
    if _ID.fullmatch(cid):
        root = (claude or claude_home()) / "projects"
        hits = sorted(root.glob(f"*/{cid}.jsonl")) if root.is_dir() else []
        if hits:
            return hits[0]
    tid = env.get("CODEX_THREAD_ID", "")
    if _ID.fullmatch(tid):
        root = codex or codex_home()
        for pattern in (f"sessions/*/*/*/rollout-*-{tid}.jsonl", f"archived_sessions/rollout-*-{tid}.jsonl"):
            hits = sorted(root.glob(pattern))
            if hits:
                return hits[0]
    return None
