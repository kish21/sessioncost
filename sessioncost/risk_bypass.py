"""Ways around the plain text rules, read the way the shell or the interpreter will run them (owner 2026-11-13).

- a glob that matches a secrets file: cat .e*, type .en?, cat .[e]nv
- a file name worked out while the command runs: cat $(ls -a | grep en), ls -a | grep env | xargs cat
- a name built from pieces: open('.'+'env'), Get-Content ('.'+'env')
- code written into the command that deletes, prints every variable, or downloads and runs code
- git settings that switch the commit checks off: -c core.hooksPath=..., git config core.hooksPath <no hooks>,
  an alias that adds --no-verify

The guard is a safety net for an agent's mistakes, not a wall: code can always build a name in a way no text rule
sees (chr(46), a file it writes first). These close the forms agents and simple tricks reach for."""
from __future__ import annotations

import os
import re
from fnmatch import fnmatchcase

from .risk_patterns import _QUOTED, _SECRET, _norm

_GLOB_WORD = re.compile(r"""(?<![\w$])(['"]?)([^\s'"`;&|()<>]*[*?\[][^\s'"`;&|()<>]*)\1""")
_CONCAT = re.compile(r"""(['"])([^'"\n]*)\1\s*\+\s*(['"])([^'"\n]*)\3""")
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_LISTER = re.compile(r"^\s*(ls|dir|gci|Get-ChildItem|find|echo|printf|Write-Output)\b(.*)$", re.I)
_FILTER = re.compile(r"^\s*(?:grep|egrep|fgrep|rg|findstr|Select-String|sls)\b(.*)$", re.I)
_XARGS = re.compile(r"^\s*xargs\b(?:\s+-(?:I\s*\S+|\S+))*\s+([A-Za-z][\w.-]*)", re.I)
_PS_FILE = re.compile(r"\s*(?:Get-Content|gc|Copy-Item|cpi|Get-Item|gi|Select-String|sls|type)\b", re.I)
_BARE_READ =re.compile(r"^\s*(Get-Content|gc|cat|type)\s*$", re.I)


def fold(text: str) -> str:
    """'.' + 'env' as the one string it makes: '.env'."""
    for _ in range(20):
        new = _CONCAT.sub(lambda m: m.group(1) + m.group(2) + m.group(4) + m.group(1), text)
        if new == text:
            return text
        text = new
    return text


def _names(cwd: str, folder: str = "") -> list[str]:
    """The files a pattern can match: the ones really there (the shell leaves a glob that matches nothing as it is)."""
    try:
        here = os.path.join(cwd or ".", os.path.expanduser(folder)) if folder else (cwd or ".")
        return sorted(os.listdir(here))[:500]
    except OSError:
        return []


def _secret(name: str) -> bool:
    return bool(_SECRET.search(f" {name} "))


def _glob_hit(pattern: str, cwd: str) -> str:
    """The secrets file a glob would open ('' for none). A leading dot is matched only by a dot, as in the shell."""
    folder, base = os.path.split(pattern.replace("\\", "/"))
    for n in _names(cwd, folder):
        if (base.startswith(".") or not n.startswith(".")) and fnmatchcase(n, base) and _secret(n):
            return (folder + "/" if folder else "") + n
    return ""


def _listed(cmd: str, cwd: str) -> list[str]:
    """The file names a listing pipeline can print (ls -a | grep en), or [] when it is not one."""
    stages = [s for s in cmd.split("|") if s.strip()]
    m = stages and _LISTER.match(stages[0])
    if not m:
        return []
    verb, rest = m.group(1).lower(), m.group(2)
    if verb in ("echo", "printf", "write-output"):
        names = rest.split()
    else:
        # the folder it lists: -Path / -LiteralPath, or the first plain word (find's start folder); the project when none
        words, folder, skip = rest.split(), "", False
        for i, w in enumerate(words):
            if skip:
                skip = False
            elif re.fullmatch(r"-(?:Path|LiteralPath)", w, re.I) and i + 1 < len(words):
                folder = words[i + 1].strip("'\"")
                break
            elif w.startswith("-"):
                skip = verb == "find" or bool(re.fullmatch(r"-(?:Filter|Include|Exclude|Depth)", w, re.I))
                if verb == "find":
                    break                                        # find's folders come before its first option
            elif not re.search(r"[*?\[]", w):
                folder = w.strip("'\"")
                break
        if folder in (".", "./", ".\\"):
            folder = ""
        if folder and ("$" in folder or "%" in folder):
            return []                                            # a folder named by a variable: unknown
        names = _names(cwd, _norm(folder) if folder else "")
        if verb in ("ls", "dir") and not re.search(r"\s-(?:\w*a\w*|A|-all|Force|Hidden)\b|\s/a", rest):
            names = [n for n in names if not n.startswith(".")]      # dot files show only with ls -a
        for pat in re.findall(r"-i?name\s+['\"]?([^\s'\"]+)", rest) + \
                re.findall(r"-(?:Filter|Include)\s+['\"]?([^\s'\"]+)", rest, re.I):
            names = [n for n in names if fnmatchcase(n.lower(), pat.lower())]
    for s in stages[1:]:
        f = _FILTER.match(s)
        if not f:
            continue                                             # sort, head...: the same names, or some of them
        words = [w.strip("'\"") for w in f.group(1).split()]
        pat = next((w for w in words if not w.startswith("-")), "")
        flags = re.I if any(re.fullmatch(r"-\w*i\w*|/i", w, re.I) for w in words) else 0
        invert = any(re.fullmatch(r"-\w*v\w*|/v", w) for w in words)
        try:
            names = [n for n in names if bool(re.search(pat, n, flags)) != invert]
        except re.error:
            names = [n for n in names if (pat.lower() in n.lower()) != invert]
    return names


def expand(t: str, cwd: str) -> str:
    """The command with the secrets file a glob, a $(...) or an xargs would hand over written out, so the plain
    secrets rule sees it."""
    def sub(m):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        hit = next((n for n in _listed(inner, cwd) if _secret(n)), "")
        return hit or m.group(0)
    t = _SUBST.sub(sub, t)
    # the shell leaves a quoted glob as it is (grep '.*', count(*) in SQL); PowerShell's file commands still expand it
    quoted = [q.span() for q in _QUOTED.finditer(t)]
    ps = lambda at: bool(_PS_FILE.match(re.split(r"[;&|\n]", t[:at])[-1]))
    t = _GLOB_WORD.sub(lambda m: ((not any(a <= m.start() < b for a, b in quoted) or (m.group(1) and ps(m.start())))
                                  and _glob_hit(m.group(2), cwd)) or m.group(0), t)
    extra = []
    for line in t.splitlines():
        stages = line.split("|")
        for i, s in enumerate(stages[1:], 1):
            verb = (_XARGS.match(s) or _BARE_READ.match(s))
            if verb:
                hit = next((n for n in _listed("|".join(stages[:i]), cwd) if _secret(n)), "")
                if hit:
                    extra.append(f"{verb.group(1)} {hit}")
    return t + "".join("\n" + e for e in extra)


# code written into the command
_SCRIPT_DELETE = re.compile(r"\b(?:os\.(?:remove|unlink|rmdir|removedirs)|shutil\.rmtree|rmtree|File\.delete|FileUtils\.rm\w*|"
                            r"fs\.(?:rm|rmdir|unlink)(?:Sync)?|unlink)\s*\(|\.(?:rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync)\s*\(|"
                            r"\bunlink\b", re.I)
_SCRIPT_FETCH_RUN = re.compile(r"\b(?:exec|eval)\s*\([^\n]*\b(?:urlopen|urllib|requests\.get|httpx|fetch|http\.client)\b", re.I)
_SCRIPT_SECRET = re.compile(r"\bdotenv_values\s*\(\s*\)")
_NODE_ENV = re.compile(r"\bnode(?:\.exe)?\s+(?:-\w+\s+)*-p\s+['\"]?process\.env['\"]?\s*(?:$|[;&|])", re.I | re.M)
_PERL_RUBY_ENV = re.compile(r"\bprint\b[^\n]*%ENV\b|\b(?:p|puts|pp)\s+ENV\b(?!\[)")


def script_marks(full: str) -> list[str]:
    """Marks for code run from the command line (python -c, node -e, perl -e...), checked on the code itself."""
    out = []
    if _SCRIPT_DELETE.search(full):
        out.append("deletes")
    if _SCRIPT_FETCH_RUN.search(full):
        out.append("pipe_to_shell")
    if _SCRIPT_SECRET.search(full):
        out.append("secrets")
    if _PERL_RUBY_ENV.search(full):
        out.append("env_dump")
    return out


def env_print(t: str) -> bool:
    return bool(_NODE_ENV.search(t))


# git settings that switch the commit checks off
_HOOKS_C = re.compile(r"\bgit(?:\.exe)?\s+(?:-\w\s+\S+\s+)*-c\s+['\"]?core\.hooksPath=", re.I)
_HOOKS_SET = re.compile(r"\bgit(?:\.exe)?\s+config\s+(?:--(?:local|global|system|worktree)\s+)*core\.hooksPath\s+(['\"]?)([^\s'\";&|]*)\1", re.I)
_ALIAS_SKIP = re.compile(r"\bgit(?:\.exe)?\s+config\s+(?:--\w+\s+)*alias\.\S+\s+[^\n;&|]*(?:--no-verify\b|\bcommit\b[^\n;&|]*\s-\w*n\b)", re.I)


def hooks_off(t: str, cwd: str) -> bool:
    if _HOOKS_C.search(t) or _ALIAS_SKIP.search(t):
        return True
    for m in _HOOKS_SET.finditer(t):
        to = m.group(2)
        if not to or to.lower() in ("/dev/null", "nul", "$null"):
            return True
        folder = os.path.join(cwd or ".", os.path.expanduser(to))
        if not (os.path.isdir(folder) and os.listdir(folder)):
            return True                                          # a folder with no hooks in it: none run
    return False


# the agent's settings file that holds the guard's hooks, moved or deleted (writing into it is checked by its content)
_HOOKS_HOME = r"(?:~|\$HOME|\$\{HOME\}|\$env:USERPROFILE|%USERPROFILE%|" + re.escape(os.path.expanduser("~")).replace(r"\\", r"[\\/]") + r")"
_MOVES_SETTINGS = re.compile(r"(?:^|[;&|(]\s*)(?:mv|move|ren|rename|rm|del|erase|Move-Item|Rename-Item|Remove-Item|mi|rni|ri)\b[^\n;&|]*?"
                             + _HOOKS_HOME + r"[\\/]+\.claude[\\/]+settings\.json\b", re.I | re.M)


def moves_guard_settings(t: str) -> bool:
    return bool(_MOVES_SETTINGS.search(t))
