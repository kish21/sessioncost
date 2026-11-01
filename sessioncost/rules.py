"""The recommended rules, and which of them are already in the agent's instruction files.

Each agent reads instruction files at the start of every chat: the project's own (CLAUDE.md, AGENTS.md, GEMINI.md,
Cursor's rules) and a personal one in your home folder. SessionCost reads them on your computer to see which
recommended rules are already there, so the report does not ask you to add a rule twice, and can say when a rule
was in place but the problem still happened. Only "in the file: yes/no" per rule goes into the report; the files'
text never leaves this computer.

The files are read as they are now, so a rule added after the chat started shows as "in the file now".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# (rule text, the fixes that show a chat needed it, key phrases that mean the rule is there in other words)
RULES = [
    ("When the next step is clear, go ahead without asking me to confirm. Tell me what you did afterwards.",
     ["confirm-turn"], ["without asking me to confirm", "without asking for confirmation", "don't ask me to confirm",
                        "do not ask me to confirm"]),
    ("When the next steps are the same kind of short command, run them together in one script or one chained "
     "command and print the result.", ["mechanical"], ["one script or one chained command"]),
    ("Do not read a file again if it has not changed since you last read it: use what you already read.",
     ["repeated-reads"], ["read a file again", "re-read a file", "reread a file", "use what you already read"]),
    ("When you need several files or searches that do not depend on each other, do them all in one step.",
     ["one-tool-per-call", "one-file-per-call"], ["do them all in one step", "batch independent reads"]),
    ("Write a new file once. When changing an existing file, edit only the lines that changed instead of writing "
     "the whole file again.", ["repeated-writes"],
     ["edit only the lines", "instead of writing the whole file", "instead of rewriting", "don't rewrite the whole file",
      "do not rewrite the whole file"]),
    ("When a command or tool fails twice in a row, stop and find the cause before trying again. Do not retry the "
     "same thing with small changes.", ["error-loop"],
     ["fails twice", "find the cause", "stop and diagnose", "don't retry", "do not retry", "never repeat an unchanged"]),
    ("When a helper is working, wait for its message. Do not set timers or check on it in between.",
     ["helper-polling"], ["wait for its message", "do not set timers", "don't poll", "do not poll"]),
    ("Make all the changes to one file in a single edit.", ["edits-spread"],
     ["single edit", "one edit per file", "all the changes to one file"]),
    ("Keep command output short: use head/tail or a quiet flag, or write long output to a file and read only the "
     "lines you need.", ["big-results"], ["keep command output short", "quiet flag", "head/tail"]),
    ("Do not re-run a command when no file has changed since the last run. Read the first result.",
     ["re-run", "test-rerun"],
     ["re-run a command", "rerun a command", "read the first result"]),
]

HOME: Path | None = None  # the home folder to look in (tests set it); None = your own


def files(agent: str, project: str, home: Path | None = None) -> list[tuple[str, Path]]:
    """(name to show, path) of the instruction files this agent reads for this project, project first."""
    home = home or HOME or Path.home()
    p = Path(project) if project else None
    own = {
        "claude-code": ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"],
        "codex": ["AGENTS.md"],
        "cursor": ["AGENTS.md", ".cursorrules"],
        "antigravity": ["GEMINI.md", "AGENTS.md"],
        "copilot": [".github/copilot-instructions.md", "AGENTS.md"],
        "copilot-vscode": [".github/copilot-instructions.md", "AGENTS.md"],
    }.get(agent, [])
    personal = {"claude-code": ".claude/CLAUDE.md", "codex": ".codex/AGENTS.md", "antigravity": ".gemini/GEMINI.md",
                "copilot": ".copilot/copilot-instructions.md"}
    out = [(name, p / name) for name in own] if p else []
    if p and agent == "cursor":
        out += [(f".cursor/rules/{f.name}", f) for f in sorted((p / ".cursor" / "rules").glob("*.mdc"))]
    if agent in personal:
        out.append((f"~/{personal[agent]} (personal)", home / personal[agent]))
    return [(n, f) for n, f in out if f.is_file()]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`>#-]+", " ", text.lower())).strip()


def _rule_scope(text: str) -> tuple[str, bool]:
    """How a Cursor .mdc applies: (scope, counts on every call).

    alwaysApply: true, or a file with no front matter, is on every call. A glob, or alwaysApply: false,
    is not: Cursor adds it only when a matching file is open, or when you ask for it.
    """
    m = re.match(r"(?s)\A---\s*\n(.*?)\n---", text)
    if not m:
        return "always", True
    head = m.group(1)
    if re.search(r"(?im)^alwaysApply:\s*true\b", head):
        return "always", True
    if re.search(r"(?im)^globs?\s*:", head):
        return "glob", False
    if re.search(r"(?im)^alwaysApply:\s*false\b", head):
        return "manual", False
    return "always", True


def check(agent: str, project: str, home: Path | None = None) -> dict:
    """Which recommended rules are in the instruction files, and how big those files are. No file text is kept."""
    found = []
    for name, path in files(agent, project, home):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            changed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            continue
        if text.strip():                                   # an empty file holds no rules and costs nothing
            scope, every = ("always", True)
            if name.startswith(".cursor/rules/"):
                scope, every = _rule_scope(text)
            found.append((name, _plain(text), round(len(text) / 4), changed, scope, every))
    rules = []
    for text, ids, keys in RULES:
        where = next((name for name, plain, _, _, _, _ in found
                      if _plain(text) in plain or any(_plain(k) in plain for k in keys)), None)
        rules.append({"text": text, "ids": ids, "in_file": where,
                      "file_changed": next((c for n, _, _, c, _, _ in found if n == where), None)})
    return {"rules": rules, "files": [{"name": n, "tokens": t, "changed": c, "scope": s, "every_call": e}
                                      for n, _, t, c, s, e in found]}
