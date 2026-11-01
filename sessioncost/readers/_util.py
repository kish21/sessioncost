"""Small text helpers shared by the readers."""
from __future__ import annotations

import re

# programs whose full path (C:/.../Python313/python.exe, .venv/bin/pytest) says nothing a person needs
_PROGRAM = (r"python(?:w|3(?:\.\d+)?)?|py|pip3?|pytest|uvx?|ruff|mypy|node|npm|npx|pnpm|yarn|bun|deno|git|gh|go|cargo|"
            r"java|dotnet|bash|sh|pwsh|powershell|cmd|docker|make")
_EXE = re.compile(r"""(?<![\w.-])(?:"[^"]*[\\/](""" + _PROGRAM + r""")(?:\.exe|\.cmd|\.bat)?"|'[^']*[\\/]("""
                  + _PROGRAM + r""")(?:\.exe|\.cmd|\.bat)?'|[^\s"']*[\\/](""" + _PROGRAM
                  + r""")(?:\.exe|\.cmd|\.bat)?)(?=\s|$)""", re.I)
_CD = re.compile(r"""^\s*cd\s+("[^"]*"|'[^']*'|\S+)\s*(?:&&|;)\s*""")
_HOME = re.compile(r"(?:[A-Za-z]:[\\/]+Users|/Users|/home)[\\/]+[^\\/\s\"']+[\\/]+", re.I)


def clean_cmd(cmd: str, cwd: str = "", n: int = 80) -> str:
    """A command as a person would say it: its first line, a leading `cd <folder> &&` dropped, programs by name
    (python, not C:/Users/me/AppData/.../python.exe), paths inside the project relative to it and the home folder
    as ~/. Cut at a word, at most n characters."""
    lines = [x for x in str(cmd or "").splitlines() if x.strip()]
    s = re.sub(r"\s+", " ", _CD.sub("", lines[0]) if lines else "").strip()
    s = re.sub(r"^&\s+", "", s)                       # PowerShell's call operator before a quoted program
    s = _EXE.sub(lambda m: next(g for g in m.groups() if g).lower(), s)
    if cwd:
        for root in {cwd.rstrip("\\/"), cwd.rstrip("\\/").replace("\\", "/"), cwd.rstrip("\\/").replace("/", "\\")}:
            if len(root) > 3:
                s = re.sub(re.escape(root) + r"[\\/]+", "", s, flags=re.I)
                s = re.sub(re.escape(root) + r"(?=[\s\"']|$)", ".", s, flags=re.I)
    s = _HOME.sub("~/", s)
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > n * 0.6 else cut).rstrip() + "…"
