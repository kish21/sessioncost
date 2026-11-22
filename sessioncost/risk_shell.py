"""Reading a command the way the shell will run it: its parts, quoted text, written-in code, and paths inside or
outside the project."""
from __future__ import annotations

import os
import re


from .risk_patterns import _MOVE, _QUOTED, _SECRET, _norm


_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?")
_HERE_STRING = re.compile(r"@(['\"])\s*$")        # PowerShell: @' or @" at the end of a line opens a here-string
# the written-in code is run (piped or fed to an interpreter, or python -c): its text counts, not only the shell's
_RUNS_BODY = re.compile(r"(?:^|[|;&(]\s*)(?:&\s*)?(?:['\"]?[\w.~:\\/ -]*[\\/])?(?:python3?|py|node|ruby|perl|php|bash|sh|zsh|pwsh|powershell)(?:\.exe)?\b"
                        r"(?=[^|;&\n]*(?:\s-(?:\s|$|c\b|e\b|Command\b)|<<))|\|\s*(?:python3?|py|node|ruby|perl|php|bash|sh|zsh|pwsh|powershell)(?:\.exe)?\b",
                        re.I | re.M)
# in a script's text, a secret counts when it is the file a read call opens (open('.env'), Get-Content .env), not
# when it is only named (a regex that lists .env among file kinds reads nothing). load_dotenv is not a read: it
# loads the keys the way the app itself does when it starts
_READ_CALL = re.compile(r"\b(?:open|read_text|read_bytes|readFileSync|readFile|createReadStream|Get-Content|gc|cat|type|"
                        r"dotenv_values|ReadAllText|ReadAllLines|File\.read|IO\.read|fopen|file_get_contents)"
                        r"(?:\s*\(\s*|\s+(?:-\w+\s+)*)['\"]?[\w./\\~:-]*['\"/\\\s]?$", re.I)


# Listing only the NAMES in a .env file (not one value) is how an agent finds out which keys a project expects without
# reading them: these whole commands go ahead. Anything more in the command (another part, a pipe on) does not.
_ENV_NAME = r"(?:\./|\.\\)?\.env(?:\.[\w.-]+)?"
_NAMES_ONLY = re.compile(
    rf"cut\s+-d\s*['\"]?=['\"]?\s+-f\s*1\s+{_ENV_NAME}"                                           # cut -d= -f1 .env
    rf"|sed\s+(?:-E\s+)?['\"]s/=\.\*//g?['\"]\s+{_ENV_NAME}"                                       # sed 's/=.*//' .env
    rf"|awk\s+-F\s*['\"]?=['\"]?\s+['\"]\{{\s*print\s+\$1\s*\}}['\"]\s+{_ENV_NAME}"                # awk -F= '{print $1}' .env
    rf"|(?:Get-Content|gc|cat)\s+{_ENV_NAME}\s*\|\s*(?:ForEach-Object|%)\s*\{{\s*\(\s*\$_\s+-split\s+['\"]=['\"]\s*\)\[0\]\s*\}}"
    rf"|\(\s*(?:Get-Content|gc)\s+{_ENV_NAME}\s*\)\s+-replace\s+['\"]=\.\*['\"](?:\s*,\s*['\"]['\"])?",   # PowerShell
    re.I)


def _reads_secret(text: str) -> bool:
    for m in _SECRET.finditer(text):
        start = text.rfind("\n", 0, m.start()) + 1
        if _READ_CALL.search(text[max(start, m.start() - 120): m.start() + 1]):
            return True
    return False
_SCRIPT_ARG = re.compile(r"""((?:python3?|py|node|ruby|perl|php)(?:\.exe)?\s+(?:-[a-zA-Z]+\s+)*-[ce]\s+)("(?:[^"\\]|\\.)*"|'[^']*')""",
                         re.I | re.S)


def _shell_part(cmd: str) -> str:
    """The command without the code written into it (heredoc bodies, PowerShell here-strings @' … '@, python -c
    "..."): words inside that code (a regex that mentions git push, say) are not steps the shell took. The script
    itself is marked inline_script."""
    out, end = [], None
    for line in cmd.splitlines():
        if end in ("'@", '"@'):               # a here-string ends where a line starts with '@ (the rest is shell again)
            if line.lstrip().startswith(end):
                out.append(line.lstrip()[2:])
                end = None
            continue
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        if m := _HERE_STRING.search(line):
            end = m.group(1) + "@"
        elif m := _HEREDOC.search(line):
            end = m.group(1)
    return _SCRIPT_ARG.sub(lambda m: m.group(1) + "''", "\n".join(out))


_DOC = r"""(?:'[^'\n]+|"[^"\n]+|[^\s'"<>|;&$`]+)\.(?:md|mdx|markdown|txt|rst|adoc)['"]?"""
# a line that only writes the text below it into a document: cat > notes.md <<'EOF', tee -a notes.md <<'EOF'
_DOC_WRITE = re.compile(rf"""(?:^|&&|;)\s*(?:cat|tee)(?:\s+(?:-a|>>?\s*/dev/null|>>?\s*{_DOC}|{_DOC}|"""
                        r"""<<-?\s*(?:'\w+'|"\w+"|\\?\w+)))+\s*$""", re.I)
# PowerShell: @' … '@ | Set-Content notes.md (single quotes: nothing in it is expanded)
_PS_DOC_WRITE = re.compile(rf"""^\s*'@\s*\|\s*(?:Set-Content|Add-Content|Out-File|sc|ac)(?:\s+(?:-(?:Path|LiteralPath|FilePath)\s+)?"""
                           rf"""{_DOC}|\s+-(?:Encoding\s+\w+|Append|NoNewline|Force))+\s*$""", re.I)


def doc_bodies_out(cmd: str) -> str:
    """The command without the text it only writes into a document (cat > notes.md <<'EOF' … EOF, @' … '@ |
    Set-Content notes.md): that text runs nothing, so a handover that names ~/.sessioncost or the guard's switch is no
    step on the guard (a Codex handover was blocked, 2026-11-18). A body that can run something stays: an unquoted end
    word with $( or ` in the body, or a line that does more than write the document."""
    lines, out, i = cmd.splitlines(), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        if (m := _HERE_STRING.search(line)) and m.group(1) == "'" and not line[:m.start()].strip():
            j = next((k for k in range(i, len(lines)) if lines[k].lstrip().startswith("'@")), None)
            if j is not None and _PS_DOC_WRITE.search(lines[j]):
                out.append(lines[j])
                i = j + 1
        elif (m := _HEREDOC.search(line)) and _DOC_WRITE.search(line):
            j = next((k for k in range(i, len(lines)) if lines[k].strip() == m.group(1)), None)
            quoted = bool(re.search(r"<<-?\s*(?:['\"]|\\)", line))
            if j is not None and (quoted or not re.search(r"\$\(|`", "\n".join(lines[i:j]))):
                out.append(lines[j])
                i = j + 1
    return "\n".join(out)


# every quoted string, left to right, so quotes pair as the shell pairs them; _no_prose drops the ones that are prose.
# (Matching only strings with a space let the closing quote of 'codex' open a false string up to the next quote, and
# the real search words after it, 'uninstall|guard remove', then read as a command: owner 2026-11-21.)
_PROSE = re.compile(r"(?:-m|--message)\s+(?:\"(?:[^\"\\]|\\.)*\"|'[^']*')|\"[^\"\n]*\"|'[^'\n]*'")
_LOOK_ONLY = re.compile(r"^\s*(?:ls|dir|stat|test|\[|Test-Path|touch|file|wc\s+-l|git\s+(?:check-ignore|ls-files))\b", re.I)


def _parts(cmd: str) -> list[str]:
    return [p for p in re.split(r"&&|\|\||;|\n|\|", cmd) if p.strip()]


# a search: its words are what it looks for, not steps it takes (grep "pip install" TESTING.md installs nothing)
_SEARCH = re.compile(r"^\s*(?:grep|egrep|fgrep|rg|ag|ack|findstr|Select-String|sls|git\s+grep|git\s+log\b[^\n]*--grep)\b"
                     r"(?![^\n]*>)", re.I)


def _split_outside_quotes(cmd: str) -> list[str]:
    """_parts, but a | or ; inside quotes (grep "a\\|b") does not split."""
    mask = _QUOTED.sub(lambda m: "x" * len(m.group(0)), cmd)
    out, start = [], 0
    for m in re.finditer(r"&&|\|\||;|\n|\|", mask):
        out.append(cmd[start:m.start()])
        start = m.end()
    out.append(cmd[start:])
    return [p for p in out if p.strip()]


_PATTERN_OPT = re.compile(r"-e|--regexp|-Pattern", re.I)         # the next word is what it looks for
_PATH_OPT = re.compile(r"-Path|-LiteralPath|-g|--glob", re.I)    # the next word is a file it reads


def search_files(part: str) -> str:
    """A search without the words it looks for: `grep -iE ".env|x"` reads no file, `grep KEY .env` reads .env."""
    if not _SEARCH.match(part):
        return part
    words = re.findall(r"\"[^\"]*\"|'[^']*'|\S+", part)
    cmd = 2 if words[0].lower() == "git" else 1
    keep, found, nxt = words[:cmd], False, ""
    for w in words[cmd:]:
        if nxt:
            if nxt == "path":
                keep.append(w)
            nxt = ""
        elif _PATTERN_OPT.fullmatch(w):
            nxt, found = "pattern", True
        elif _PATH_OPT.fullmatch(w):
            nxt = "path"
        elif w.startswith("-"):
            continue                          # a flag: -i, -E, --name-only
        elif not found:
            found = True                      # the first plain word is the pattern
        else:
            keep.append(w)
    return " ".join(keep)


def search_safe_parts(cmd: str) -> list[str]:
    """The parts of a command, split outside quotes, each search without its pattern (the secrets check)."""
    return [search_files(p) for p in _split_outside_quotes(cmd)]


def _without_searches(cmd: str, inner: list[str]) -> str:
    """The command without its search parts, for the rules about steps it takes (installs, pushes, deletes...)."""
    # quoted text with spaces is prose (a commit message, an issue comment); bash -c "…" comes back in `inner`
    # (each quoted string on its own: "git" "push" "--force" is three words, not prose)
    prose = lambda p: _QUOTED.sub(lambda m: " " if re.search(r"\s", m.group(0)) else m.group(0), p)
    return "\n".join(_unquote(prose(p)) for c in [cmd, *inner] for p in _split_outside_quotes(c) if not _SEARCH.match(p))


def _no_prose(cmd: str) -> str:
    """The command without its prose: commit messages and quoted text with spaces (an echo, a search query) only
    mention things; a quoted single word ("git", '.env') was already unquoted."""
    return _PROSE.sub(lambda m: "''" if m.group(0)[0] == "-" or re.search(r"\s", m.group(0)) else m.group(0), cmd)


def _unquote(cmd: str) -> str:
    """Words as the shell sees them: "git" "push" is git push, .e''nv is .env (a way around simple text rules)."""
    cmd = re.sub(r"(?<=\w)(?:''|\"\")(?=[\w.])", "", cmd)
    return re.sub(r"(?<![\w\\])([\"'])([\w.+-]+)\1(?![\w])", r"\2", cmd)



def _inside(path: str, folder: str) -> bool:
    if not folder:
        return True
    p, f = _norm(path), _norm(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


# a command that reads or copies the files it names: a quoted name there is a path, not prose
_READ_VERB = re.compile(r"^\s*(?:&\s*)?(?:cat|type|Get-Content|gc|more|less|head|tail|copy|cp|Copy-Item|cpi|xcopy|robocopy|"
                        r"Select-String|sls|findstr|grep|rg|sqlite3|certutil|base64|xxd|od|strings|scp|curl)(?:\.exe)?\s", re.I)


def _quoted_secret(part: str) -> bool:
    """Get-Content "C:\\My Project\\.env": a secrets path in quotes (it has a space) given to a command that reads it."""
    return bool(_READ_VERB.match(part)) and any(_SECRET.search(f" {q.group(0)[1:-1]} ") for q in _QUOTED.finditer(part))


def _moves_out(t: str, cwd: str) -> bool:
    """mv / Move-Item whose destination (the last word) is outside the project, the temp folder included."""
    for part in _parts(t):
        m = _MOVE.match(part)
        words = m and [w for w in m.group(1).split() if not w.startswith("-")]
        if words and len(words) >= 2 and cwd:
            dest = words[-1].strip("'\"`")
            full = dest if os.path.isabs(_norm(dest)) or dest.startswith("~") else os.path.join(cwd, dest)
            if not _inside(full, cwd):
                return True
    return False


def _outside(path: str, cwd: str) -> bool:
    """A file changed outside the project folder (relative paths are inside it; the temp folder is fine)."""
    if not path or not cwd or not (os.path.isabs(_norm(path)) or path.startswith("~")):
        return False
    import tempfile                # loaded only here: it takes ~0.1 s at every guard start (it brings shutil, bz2)
    temps = {tempfile.gettempdir(), "/tmp", "/var/tmp", os.environ.get("TMP", ""), os.environ.get("TEMP", "")}
    return not _inside(path, cwd) and not any(t and _inside(path, t) for t in temps)
