"""The user's own setup, read once per report (owner, 2026-11-24): instruction files (project and personal, and the
ones the log shows were loaded, such as a memory index), helper agent files, settings and a few project files. Tips
use it to find WHY a waste happened (a soft rule, a missing line, a default setting) and to quote that file and line.
Plain code on the user's disk: no tokens, nothing sent anywhere, nothing written. The guard never uses it."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import rules as rulesmod

MAX_CHARS = 400_000          # a bigger file is not an instruction file worth quoting
# where each agent keeps a named helper's own file (helper_agent.py has the formats, from each agent's docs)
AGENT_DIRS = {"claude-code": [(".claude/agents", ".md")], "cursor": [(".cursor/agents", ".md")],
              "codex": [(".codex/agents", ".toml")], "antigravity": [(".agents/agents", ".md")],
              "copilot": [(".github/agents", ".agent.md")], "copilot-vscode": [(".github/agents", ".agent.md")]}
HOME_DIRS = {"claude-code": ".claude/agents", "cursor": ".cursor/agents", "codex": ".codex/agents"}
# Claude Code settings, strongest first (local, project, personal)
CLAUDE_SETTINGS = [(".claude/settings.local.json", False), (".claude/settings.json", False),
                   (".claude/settings.json", True)]


@dataclass(frozen=True)
class Line:
    path: str        # as shown: project-relative, or ~/... for the home folder
    n: int           # 1-based
    text: str

    def at(self) -> str:
        return f"`{self.path}` line {self.n}"

    def quote(self, width: int = 110) -> str:
        t = self.text.strip().rstrip(",")
        return f"{self.at()} (“{t[:width]}{'…' if len(t) > width else ''}”)"


class Workspace:
    def __init__(self, agent: str, cwd: str, home: Path | None = None, logged: list[str] | None = None):
        self.agent, self.cwd = agent, cwd or ""
        self.home = Path(home or rulesmod.HOME or Path.home())
        self.reads = 0                       # files opened (tests check each is read once)
        self._text: dict[str, str] = {}
        self._settings: list[tuple[Path, dict]] | None = None
        self.instruction_files = self._instructions(logged or [])

    # ---- files
    def text(self, p: Path) -> str:
        k = str(p)
        if k not in self._text:
            self.reads += 1
            try:
                self._text[k] = p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS] if p.is_file() else ""
            except OSError:
                self._text[k] = ""
        return self._text[k]

    def show(self, p: Path) -> str:
        q, home = p.as_posix(), self.home.as_posix()
        if self.cwd:
            c = Path(self.cwd).as_posix().rstrip("/")
            if q.lower().startswith(c.lower() + "/"):
                return q[len(c) + 1:]
        if q.lower().startswith(home.lower() + "/"):
            return "~" + q[len(home):]
        return q

    def project(self, rel: str) -> Path | None:
        if not self.cwd:
            return None
        p = Path(self.cwd) / rel
        return p if p.is_file() else None

    def _instructions(self, logged: list[str]) -> list[Path]:
        out = [p for _, p in rulesmod.files(self.agent, self.cwd, self.home)]
        for w in logged:
            p = Path(w) if Path(w).is_absolute() or not self.cwd else Path(self.cwd) / w
            if p.is_file() and p.suffix.lower() in (".md", ".mdc", ".txt") and p not in out:
                out.append(p)
        return out

    def find(self, pattern: str | re.Pattern, files: list[Path] | None = None, flags=re.I) -> list[Line]:
        rx = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        out = []
        for p in self.instruction_files if files is None else files:
            for i, line in enumerate(self.text(p).splitlines(), 1):
                if rx.search(line):
                    out.append(Line(self.show(p), i, line))
        return out

    # ---- helper agents
    def helper_file(self, name: str) -> Path | None:
        places = [Path(self.cwd) / d / f"{name}{ext}" for d, ext in AGENT_DIRS.get(self.agent, [])] if self.cwd else []
        if self.agent in HOME_DIRS:
            places.append(self.home / HOME_DIRS[self.agent] / f"{name}{AGENT_DIRS[self.agent][0][1]}")
        return next((p for p in places if p.is_file()), None)

    def helper_description(self, p: Path) -> str:
        m = re.search(r"^\s*description\s*[:=]\s*(.+)$", self.text(p), re.M | re.I)
        return m.group(1).strip().strip("\"'") if m else ""

    # ---- settings (Claude Code keeps them in JSON; the other agents' logs name no settings file we read)
    def settings(self) -> list[tuple[Path, dict]]:
        if self._settings is None:
            self._settings = []
            if self.agent == "claude-code":
                for rel, personal in CLAUDE_SETTINGS:
                    p = (self.home / rel) if personal else (Path(self.cwd) / rel if self.cwd else None)
                    if not p or not p.is_file():
                        continue
                    try:
                        self._settings.append((p, json.loads(self.text(p) or "{}")))
                    except ValueError:
                        continue
        return self._settings

    def setting(self, key: str) -> tuple[Line, object] | None:
        """The strongest settings file that sets `key` (top level), with its line and value."""
        for p, data in self.settings():
            if isinstance(data, dict) and key in data:
                return self.key_line(p, key), data[key]
        return None

    def key_line(self, p: Path, key: str) -> Line:
        for i, line in enumerate(self.text(p).splitlines(), 1):
            if f'"{key}"' in line:
                return Line(self.show(p), i, line)
        return Line(self.show(p), 1, "")


def of(s, home: Path | None = None) -> Workspace:
    """The one Workspace of this report: built on first use, kept on the session, shared by every tip."""
    ws = getattr(s, "_workspace", None)
    if ws is None or (home is not None and ws.home != Path(home)):
        ws = Workspace(s.agent, s.cwd, home, [f.what for f in s.setup.files])
        s._workspace = ws
    return ws
