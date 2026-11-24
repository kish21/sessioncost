"""Which files a shell command only reads, prints or searches (cat, head, sed -n, grep, rg, findstr, Select-String),
so the guard knows a result came from those files. Split out of guard_checks.py (500-line rule, 2026-11-23)."""
from __future__ import annotations

import os
import re

SCRIPT = re.compile(r"\.(?:sh|bash|zsh|ps1|psm1|bat|cmd|py|js|mjs|rb|pl)$|(?:^|[\\/])(?:Makefile|Dockerfile|package\.json)$", re.I)

_LOCK_NAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock",
    "cargo.lock", "poetry.lock", "composer.lock", "gemfile.lock",
}
_BUILD_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".chunk.js", ".map")

_RANGED_CMD = re.compile(
    r"\b(?:head|tail)\b|"
    r"-(?:TotalCount|Head|Tail)\b|"
    r"\b(?:Select-Object|select)\b[^\n;&|]*-(?:First|Last)\b",
    re.I,
)
_SHELL_READ = re.compile(
    r"^\s*(?:&\s*)?(?:cat|type|Get-Content|gc|more|less)(?:\.exe)?\s+(.*)$",
    re.I,
)
_SHELL_SEARCH = re.compile(
    r"^\s*(?:&\s*)?(?:grep|rg|findstr|Select-String|sls)(?:\.exe)?\s+(.*)$",
    re.I,
)


def _words(s: str) -> list[str]:
    return [w.strip("'\"`") for w in re.findall(r'[^\s"\'`]+|"[^"]*"|\'[^\']*\'|`[^`]*`', s)]


def _cmd_read_paths(cmd: str) -> tuple[list[str], list[str]]:
    """(every file a shell command reads whole, those of them read with no line limit in their own pipeline).
    A head/tail elsewhere in the command (or git's HEAD) limits nothing."""
    paths, unlimited = [], []
    for seg in re.split(r"&&|\|\||;|\n", cmd):
        found = _pipeline_read_paths(seg)
        paths += found
        if not _RANGED_CMD.search(re.sub(r"\bHEAD\b", "", seg)):
            unlimited += found
    return paths, unlimited


_HEAD_TAIL = re.compile(r"^\s*(?:head|tail)(?:\.exe)?\s+(.*)$", re.I)


def _cmd_shown_paths(cmd: str) -> list[str]:
    """Every file a shell command prints, whole or a head/tail of it ([] when any part runs something else)."""
    paths = []
    for p in re.split(r"&&|\|\||;|\n|\|", cmd):
        if not p.strip():
            continue
        if m := _HEAD_TAIL.match(p):
            words, skip = _words(m.group(1)), False
            for w in words:
                if skip or w.startswith("-") or w.isdigit():
                    skip = w in ("-n", "-c")
                    continue
                paths.append(w)
        elif found := _pipeline_read_paths(p):
            paths += found
        else:
            return []
    return paths


def _pipeline_read_paths(cmd: str) -> list[str]:
    paths = []
    for p in cmd.split("|"):
        if m := _SHELL_READ.match(p.strip()):
            words = _words(m.group(1))
            skip = False
            for w in words:
                if skip:
                    skip = False
                    paths.append(w)
                elif w in ("-Path", "-LiteralPath", "-p"):
                    skip = True
                elif not w.startswith("-") and not (w.startswith("/") and len(w) <= 2):
                    paths.append(w)
    return paths


# search flags whose next word is the search text, a path, or some other value (grep/rg are case-sensitive,
# Select-String parameters and findstr options are not)
_PATTERN_FLAGS = {"-e", "--regexp"}
_PATH_FLAGS = {"-f", "--file"}
_VALUE_FLAGS = {"-A", "-B", "-C", "-m", "-g", "-t", "-T", "--max-count", "--glob", "--type", "--type-not",
                "--include", "--exclude", "--exclude-dir", "--context", "--after-context", "--before-context"}
_PS_PATTERN, _PS_PATH, _PS_VALUE = {"-pattern"}, {"-path", "-literalpath"}, {"-encoding", "-context", "-include", "-exclude"}


def _cmd_search_paths(cmd: str) -> list[str]:
    """The places a shell search (grep, rg, findstr, Select-String) looks in: never the search text itself,
    which is the first plain word unless -e / -Pattern / /C: gave it."""
    paths = []
    for p in re.split(r"&&|\|\||;|\n|\|", cmd):
        m = _SHELL_SEARCH.match(p.strip())
        if not m:
            continue
        ps = re.match(r"\s*(?:&\s*)?(?:Select-String|sls)\b", p.strip(), re.I) is not None
        findstr = re.match(r"\s*(?:&\s*)?findstr\b", p.strip(), re.I) is not None   # only findstr takes /x options
        pattern_given, plain, nxt, opts_done = False, [], None, False
        for w in _words(m.group(1)):
            low = w.lower()
            if nxt:
                if nxt == "path":
                    paths.append(w)
                nxt = None
            elif opts_done or not (w.startswith("-") or (findstr and re.match(r"/[A-Za-z?](?::|$)", w))):
                plain.append(w)
            elif w == "--":
                opts_done = True
            elif (ps and low in _PS_PATTERN) or w in _PATTERN_FLAGS or low in ("/c:", "/g:"):
                pattern_given, nxt = True, ("path" if low == "/g:" else "pattern")
            elif not ps and (re.match(r"--regexp=|-e.", w) or re.match(r"/c:.", w, re.I)):
                pattern_given = True                      # -e/api, --regexp=/api, findstr /C:text
            elif not ps and w.startswith("--file="):     # grep/rg read their patterns from that file
                pattern_given = True
                paths.append(w.split("=", 1)[1])
            elif (ps and low in _PS_PATH) or w in _PATH_FLAGS:
                pattern_given = pattern_given or not ps      # grep/rg -f: the patterns come from the file
                nxt = "path"
            elif (ps and low in _PS_VALUE) or w in _VALUE_FLAGS:
                nxt = "value"
        for w in (plain if pattern_given else plain[1:]):
            if "/" in w or "\\" in w or w.startswith(".") or w.startswith("~") or ":" in w or not (ps or findstr):
                paths.append(w)               # grep / rg: every word after the search text is a file or folder
    return paths


_FILTER = re.compile(r"^\s*(?:head|tail|sort|uniq|wc|cut)(?:\s+-\S+(?:\s+\d+)?)*\s*$|"   # reads only the pipe
                     r"^\s*(?:echo|printf|Write-(?:Host|Output))(?:\s+[^$`<>]*)?$", re.I)   # a label the agent typed itself
_SED = re.compile(r"^\s*sed(?:\.exe)?\s+(.*)$", re.I)


def _sed_paths(part: str) -> list[str]:
    """The files a printing sed reads (sed -n '1,40p' f); [] for an in-place edit or no file."""
    m = _SED.match(part)
    if not m:
        return []
    script_given, plain, nxt = False, [], False
    for w in _words(m.group(1)):
        if nxt:
            nxt = False
        elif w.startswith(("-i", "--in-place")):
            return []
        elif w in ("-e", "-f", "--expression", "--file"):
            script_given = nxt = True
        elif w.startswith("-"):
            continue
        else:
            plain.append(w)
    return plain if script_given else plain[1:]


_ASSIGN = re.compile(r"^\s*\$[\w:]+\s*=\s*\(?\s*")          # PowerShell `$c = Get-Content f`, `$i = ($c | ...`
_PS_SHAPE = {"select-object", "select", "where-object", "where", "foreach-object", "foreach", "sort-object", "sort",
             "measure-object", "measure", "group-object", "format-table", "ft", "format-list", "fl", "out-string",
             "select-string", "sls"}                         # with no -Path, Select-String searches only the pipe
_SUBEXPR = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)")


def _shapes_only(part: str) -> bool:
    """A PowerShell part that only reshapes what the command already read: variables, `$_` members, text, numbers and
    the pipe cmdlets above, with no other command anywhere (not even inside "$( )"). Owner 2026-11-21: reading our
    own CHANGELOG into `$c` and slicing it raised an injection warning."""
    t = re.sub(r"'[^']*'", "''", part)                         # single quotes: plain text
    t = re.sub(r'"[^"]*"', lambda m: " ".join(_SUBEXPR.findall(m.group(0))), t)   # double quotes: only $( ) runs
    if re.search(r"[&<>`]|\b-Path\b|\b-LiteralPath\b", t, re.I):
        return False
    words = re.findall(r"(?<![\w$.\-:\\/])[A-Za-z_][\w-]*", t)
    return all(w.lower() in _PS_SHAPE for w in words)


def _split_cmd(cmd: str) -> list[str]:
    """The parts of a command between && || ; | and new lines, never splitting inside quotes ('a|b' is one pattern)."""
    parts, cur = [], ""
    for tok in re.findall(r"'[^']*'|\"[^\"]*\"|&&|\|\||[;|\n]|[^'\"&|;\n]+|['\"&]", cmd):
        if tok in ("&&", "||", ";", "|", "\n"):
            parts.append(cur)
            cur = ""
        else:
            cur += tok
    return parts + [cur]


# `cd "<project>/backend"; sed -n 1,40p app/x.py` (owner 2026-11-23, MarkVid): the folder change is part of reading
_CD = re.compile(r"^\s*(?:cd|chdir|pushd|Set-Location|sl)(?:\s+/d)?\s+(?:-(?:Literal)?Path\s+)?(\S.*?)\s*$", re.I)
_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/~])")
# the folder is not known: popd goes back, `cd x | y` changes it only inside the pipe (bash), $HOME / %X% unresolved
_CD_UNSURE = re.compile(r"\b(?:popd|Pop-Location)\b|(?:^|[;&|\n(])\s*(?:cd|chdir|pushd|Set-Location|sl)\b"
                        r"[^;&|\n]*\|(?!\|)", re.I)


def cd_target(part: str) -> str | None:
    """The folder a `cd` part goes to; "" when it is not a cd, None when the folder cannot be known."""
    m = _CD.match(part)
    if not m:
        return ""
    to = _words(m.group(1))
    return None if len(to) != 1 or to[0] == "-" or re.search(r"[$%`]", to[0]) else to[0]


def _from(here: str, path: str) -> str:
    """A path the command names, from the folder it changed to (an absolute path stays as it is)."""
    return path if not here or _ABS.match(path) or os.path.isabs(path) else here.rstrip("\\/") + "/" + path


def _cmd_all_read_paths(cmd: str) -> list[str]:
    """Every file a command only prints or searches (cat, head, sed -n, grep, rg, findstr, Select-String), or []
    when any part does something else or reads no named file, so the whole result came from those files. A `cd`
    before them only sets the folder their paths count from."""
    paths, here = [], ""
    if _CD_UNSURE.search(cmd):
        return []
    for p in _split_cmd(cmd):
        p = _ASSIGN.sub("", p)
        if not p.strip() or _FILTER.match(p):
            continue
        if (to := cd_target(p)) is None:
            return []
        if to:
            here = _from(here, to)
            continue
        found = (_cmd_search_paths(p) if _SHELL_SEARCH.match(p.strip()) else _sed_paths(p)) or _cmd_shown_paths(p)
        if not found and not _shapes_only(p):
            return []
        paths += [_from(here, x) for x in found]
    return paths
