"""Helper checks for guard_decide.py: git remotes and secret commits, project package lists, script files,
lockfile/large-file reads, out-of-workspace reads, and the WHY / INSTEAD messages."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .risk_shell import _inside, _outside

SCRIPT = re.compile(r"\.(?:sh|bash|zsh|ps1|psm1|bat|cmd|py|js|mjs|rb|pl)$|(?:^|[\\/])(?:Makefile|Dockerfile|package\.json)$", re.I)

_LOCK_NAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock",
    "cargo.lock", "poetry.lock", "composer.lock", "gemfile.lock",
}
_BUILD_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".chunk.js", ".map")
BIG_READ_LINES = 2_000
BIG_READ_BYTES = 80_000

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
            if "/" in w or "\\" in w or w.startswith(".") or w.startswith("~") or ":" in w:
                paths.append(w)
    return paths


def _file_is_big(raw: str, cwd: str) -> bool:
    raw = (raw or "").strip()
    if not raw:
        return False
    t = raw.replace("\\", "/").lower()
    base = t.rsplit("/", 1)[-1]
    is_lock_or_build = base in _LOCK_NAMES or base.endswith(_BUILD_SUFFIXES) or "/dist/" in f"/{t.lstrip('/')}"
    p = Path(os.path.expanduser(raw))
    p = p if p.is_absolute() or not cwd else Path(cwd) / p
    try:
        st = p.stat() if p.is_file() else None
    except OSError:
        st = None
    if is_lock_or_build:
        if st is None or st.st_size >= 12_000:
            return True
        try:
            with p.open(encoding="utf-8", errors="replace") as fh:
                if sum(1 for _ in fh) >= 200:
                    return True
        except OSError:
            return True
    elif st is not None:
        if st.st_size > BIG_READ_BYTES:
            return True
        if st.st_size >= BIG_READ_LINES:
            try:
                with p.open(encoding="utf-8", errors="replace") as fh:
                    if sum(1 for _ in fh) > BIG_READ_LINES:
                        return True
            except OSError:
                pass
    return False


def _big_read(a, cwd: str) -> bool:
    """True when a Read step or shell command reads a whole lockfile, build bundle or >2,000-line file without range limits."""
    if a.kind == "read":
        if getattr(a, "ranged", False) or not (a.target or "").strip():
            return False
        return any(_file_is_big(raw, cwd) for raw in (a.target or "").splitlines())
    if a.kind == "command":
        _, unlimited = _cmd_read_paths(a.target or "")
        return any(_file_is_big(p, cwd) for p in unlimited)
    return False


def _agent_roots() -> list[str]:
    home = Path.home()
    roots = [str(home / d) for d in (".claude", ".codex", ".cursor", ".gemini", ".copilot", ".agents", ".sessioncost")]
    for env in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "COPILOT_HOME", "SESSIONCOST_DIR", "SESSIONCOST_GUARD_DIR"):
        if os.environ.get(env):
            roots.append(os.environ[env])
    return roots


def _outside_read(path: str, cwd: str) -> bool:
    """True when a Read step reads a file outside the project root (excluding temp and agent config/skill folders).
    A relative path counts from the project folder, so ..\\other\\notes.md is outside too."""
    if path and cwd and not (os.path.isabs(path) or path.startswith("~")):
        path = os.path.normpath(os.path.join(cwd, path))
    if not _outside(path, cwd):
        return False
    return not any(_inside(path, r) for r in _agent_roots() if r)


def _outside_read_step(a, cwd: str) -> str | None:
    """Returns the outside path if this read/search/command step reads outside the project, else None."""
    if a.kind == "read":
        for p in (a.target or "").splitlines():
            if _outside_read(p, cwd):
                return p
    elif a.kind == "search":                    # only where it looks: the target is often the search text
        for p in (getattr(a, "paths", "") or "").splitlines():
            if _outside_read(p, cwd):
                return p
    elif a.kind == "command":
        paths, _ = _cmd_read_paths(a.target or "")
        for p in paths:
            if _outside_read(p, cwd):
                return p
        for p in _cmd_search_paths(a.target or ""):
            if _outside_read(p, cwd):
                return p
    return None


def _git_remotes(cwd: str) -> dict[str, str]:
    """{remote name: url} from the repo's .git/config, found from cwd upwards ({} outside a repo)."""
    d = Path(cwd or ".")
    for p in [d, *d.parents]:
        cfg = p / ".git" / "config"
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace") if cfg.is_file() else None
        except OSError:
            text = None
        if text is not None:
            out, name = {}, None
            for line in text.splitlines():
                if m := re.match(r'\s*\[\s*remote\s+"([^"]+)"\s*\]', line):
                    name = m.group(1)
                elif line.lstrip().startswith("["):
                    name = None
                elif name and (m := re.match(r"\s*url\s*=\s*(\S+)", line)):
                    out.setdefault(name, m.group(1))
            return out
    return {}


def _push_dest(a, cwd: str) -> None:
    """Where the step pushes, by URL when the remote is known (a remote renamed or re-pointed asks again), and
    whether it pushes straight to a web address that is not one of the repo's remotes."""
    from .risks import push_targets
    dests = push_targets(a.target or "")
    if not dests:
        return
    remotes = _git_remotes(cwd)
    urls = set(remotes.values())
    a.push_to = sorted({remotes.get(d, d) or "(default)" for d in dests})
    a.push_raw = any(d not in remotes and d not in urls and re.search(r"://|^[\w.-]+@[\w.-]+:", d) for d in dests)


_BULK_ADD = re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*add\b[^\n;&|]*\s(?:-A|--all|-u|--update|\.|\*|:/)(?=\s|$)", re.I)
_COMMIT_ALL = re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*commit\b[^\n;&|]*\s(?:-[b-zA-Z]*a[a-zA-Z]*|--all)(?=\s|$)")
_ADD_ARGS = re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*add\s+([^\n;&|]+)", re.I)


def _git(cwd: str, *args: str) -> list[str]:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", cwd or ".", *args], capture_output=True, text=True, timeout=3,
                           stdin=subprocess.DEVNULL, creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []


def _secret_commit(a, cwd: str) -> list[str]:
    """The secrets files a git commit would take into the history: staged before, or added by this same command."""
    from .risks import _COMMIT, _SECRET, _no_prose, _unquote
    t = _no_prose(_unquote(a.target or ""))
    if a.kind != "command" or not _COMMIT.search(t):
        return []
    files = _git(cwd, "diff", "--cached", "--name-only")
    if _BULK_ADD.search(t):
        files += _git(cwd, "ls-files", "-m", "-o", "--exclude-standard")
    elif _COMMIT_ALL.search(t):
        files += _git(cwd, "ls-files", "-m")
    for m in _ADD_ARGS.finditer(t):
        named = [w.strip("'\"") for w in m.group(1).split() if not w.startswith("-")]
        files += [f for f in named if f and not _git(cwd, "check-ignore", f)]      # an ignored file is not added
    return sorted({f for f in files if _SECRET.search("/" + f.replace("\\", "/"))})


_INSTALL_PY = re.compile(r"^\s*(?:(?:python3?|py)\s+-m\s+pip3?|pip3?|uv\s+pip)\s+install\s+(.*)$|^\s*uv\s+add\s+(.*)$", re.I)
_INSTALL_JS = re.compile(r"^\s*(?:npm|pnpm|bun)\s+(?:i|install|add)\s+(.*)$|^\s*yarn\s+add\s+(.*)$", re.I)
_TAKES_VALUE = {"-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url", "--extra-index-url",
                "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--group", "--registry", "--tag"}
_TOOLING = {"pip", "setuptools", "wheel", "uv", "npm", "pnpm", "yarn", "corepack"}


def _py_name(s: str) -> str:
    return re.sub(r"[-_.]+", "-", re.split(r"[\s\[<>=!~;@(]", s.strip(), maxsplit=1)[0]).lower()


def _js_name(s: str) -> str:
    s = s.strip()
    return ("@" + s[1:].split("@")[0] if s.startswith("@") else s.split("@")[0]).lower()


def _project_packages(cwd: str) -> tuple[set[str] | None, set[str] | None]:
    """The package names the project already lists (Python, JavaScript); None where it has no list to judge by."""
    root = Path(cwd or ".")
    py: set[str] | None = None
    for p in [root / "pyproject.toml", root / "uv.lock", *sorted(root.glob("requirements*.txt"))]:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        py = py or set()
        if p.suffix == ".txt":
            py |= {_py_name(ln) for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-"))}
        elif p.name == "uv.lock":
            py |= {_py_name(n) for n in re.findall(r'^name\s*=\s*"([^"]+)"', text, re.M)}
        else:                                 # every quoted requirement and every dependency table key
            py |= {_py_name(s) for s in re.findall(r'"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?\s*[^"]*)"', text)}
            py |= {_py_name(k) for k in re.findall(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*=", text, re.M)}
    js: set[str] | None = None
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        js = {_js_name(k) for sec in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
              for k in (pkg.get(sec) or {})}
    except (OSError, ValueError, AttributeError):
        pass
    return py, js


def _new_packages(a, cwd: str) -> list[str]:
    """Packages this install names that the project does not list (only where it has a list)."""
    from .risks import _parts, _shell_part, _unquote
    if a.kind != "command":
        return []
    py, js = _project_packages(cwd)
    out = []
    for part in _parts(_unquote(_shell_part(a.target or ""))):
        for rx, known, name in ((_INSTALL_PY, py, _py_name), (_INSTALL_JS, js, _js_name)):
            m = rx.match(part)
            if not m or known is None:
                continue
            words, skip = (m.group(1) or m.group(2) or "").split(), False
            for w in words:
                if skip or w.startswith("-"):
                    skip = not skip and w in _TAKES_VALUE
                    continue
                if (re.search(r"[\\/]|://|^\.|\.(?:whl|tar\.gz|zip|tgz)$", w) and not w.startswith("@")) or ":" in w:
                    continue                  # a path, a web address, a git+ or file: source: not a name from an index
                n = name(w.strip("'\""))
                if n and n not in known and n not in _TOOLING:
                    out.append(w.strip("'\""))
    return list(dict.fromkeys(out))


# What to do instead of a never-list step: blocking alone stops the work, so each block names the safer way
INSTEAD = {
    "secrets": "Reading it would send the keys to the AI model and keep them in this chat's log. The values are not needed: "
               "write code that reads them when it runs (for example os.environ[\"NAME\"] or a dotenv loader). To see which "
               "keys exist, list the names only: cut -d= -f1 .env (PowerShell: Get-Content .env | ForEach-Object "
               "{ ($_ -split '=')[0] }). Keys belong in .env (listed in .gitignore), never in code or in this chat.",
    "pipe_to_shell": "Download the script to a file, show it to the user, and run it only after they have read it.",
    "encoded_command": "Write the command out in plain text so the user can read what it does.",
    "env_dump": "To check that a variable is set, test it without printing its value (for example: test -n \"$NAME\" && echo set).",
    "force_push": "Push without --force; if the history really has to change, the user does it themselves.",
    "persistence": "Tell the user what would run and when, and let them set it up.",
    "security_off": "Leave the security tool on and tell the user what it stopped.",
    "skips_commit_checks": "Run the commit checks and fix what they find, or tell the user which check fails and why.",
    "secret_commit": "Take that file out of the commit (git restore --staged <file>), add it to .gitignore, and commit the rest. "
                     "If a key was ever committed or pushed before, tell the user to replace (rotate) it.",
    "wipe": "Delete only the specific files, and name them to the user first.",
}

WHY = {"pipe_to_shell": "runs a script straight from the internet", "encoded_command": "runs a hidden (encoded) command",
       "env_dump": "prints every environment variable (tokens and keys live there)", "force_push": "rewrites shared git history",
       "persistence": "sets something up to run again later", "security_off": "switches off a security tool",
       "skips_commit_checks": "switches off the commit checks", "secrets": "touches a secrets file or key",
       "secret_commit": "would commit a secrets file into the git history",
       "new_package": "installs a package that is not one of this project's",
       "repeat_fail": "this exact command already failed twice in a row, with no file changed in between",
       "after_untrusted": "a risky step right after untrusted content", "guardrails": "changes the agent's own rules, settings or memory",
       "git_push": "pushes with git", "deletes": "deletes files", "installs": "installs or fetches packages",
       "outside_project": "changes or reads a file outside the project", "network": "uses the network",
       "big_read": "reads a whole lockfile, build bundle or large file (>2,000 lines) without a line range (search first with Grep, or pass offset and limit)",
       "inline_script": "runs code written into the command", "injected": "its result showed signs of a prompt injection",
       "mcp_action": "an MCP tool that changes or sends something",
       "poisoned_tool": "a tool of an MCP server whose descriptions showed signs of a prompt injection",
       "key_in_output": "a key or token would land in this chat (a script that prints one, or sending data out after one came in)",
       "new_or_changed": "this skill or MCP server is new or changed since your last chat here",
       "staged_script": "writes or runs a script file with a never-list step in it",
       "guard_tamper": "would change or switch off the guard or the rules you set",
       "unlisted": "not in the project's allow list (allow-list mode)",
       "wipe": "deletes the whole disk or the home folder", "privilege": "raises its own rights or changes the computer itself (sudo, accounts, services, the machine's settings)"}

# the never rules alone, on the raw command: what still runs when the full check fails (fail closed for these)
_RAW_NEVER = None


def _raw_never(cmd: str) -> bool:
    global _RAW_NEVER
    if _RAW_NEVER is None:
        from . import risks
        _RAW_NEVER = [risks._PIPE_SHELL, risks._ENCODED, risks._ENV_DUMP, risks._FORCE, risks._PERSIST, risks._SEC_OFF,
                      risks._TAMPER_CMD, risks._WIPE]
    return any(rx.search(cmd or "") for rx in _RAW_NEVER)


_RUN_FILE = re.compile(r"(?:^|[;&|(]\s*)(?:sudo\s+)?(?:(?:sh|bash|zsh|python3?|py|node|pwsh|powershell|source|\.)\s+"
                       r"(?:-(?:File|ExecutionPolicy\s+\w+|\w+)\s+)*|\.[\\/]|&\s+)['\"]?([\w.\\/~:-]+\.(?:sh|bash|zsh|py|js|mjs|ps1|psm1|bat|cmd)|"
                       r"(?<=\./)[\w.-]+)['\"]?(?=\s|$|[;&|)])", re.I | re.M)
SCRIPT_MAX = 512 * 1024


def _run_scripts(cmd: str, cwd: str) -> str:
    """The text of the script files a command runs (sh setup.sh, python tool.py, ./build.sh, & .\\x.ps1), read
    from disk when they are there: what the shell will actually do is in them, not in the command."""
    out = []
    for m in _RUN_FILE.finditer(cmd or ""):
        p = Path(os.path.expanduser(m.group(1)))
        p = p if p.is_absolute() or not cwd else Path(cwd) / p
        try:
            if p.is_file() and p.stat().st_size <= SCRIPT_MAX:
                out.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(out)


def _step_fp(a) -> str:
    return hashlib.sha1(f"{a.tool}\0{a.target}\0{getattr(a, 'written', '')}".encode()).hexdigest()[:16]
