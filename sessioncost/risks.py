"""Risky steps, found on your computer: not a cost, but worth knowing.

skips_commit_checks  the agent tried to switch off the commit checks (pre-commit, husky): SKIP=... with a commit,
                     git commit --no-verify / -n, git push --no-verify, HUSKY=0, HUSKY_SKIP_HOOKS=1,
                     PRE_COMMIT_ALLOW_NO_CONFIG. Marked whether the command ran or was refused: the attempt is the
                     finding. Seen 2026-10-16: Codex set $env:SKIP to every check, then git commit.

The watch list (shown in the report's Sky, never sent anywhere): steps worth a look, not costs.

outside_project  changed a file outside the project folder (the system temp folder is fine: agents keep scratch
                 files there)
network          reached outside this computer: a web tool, an MCP or publishing tool, curl / wget / ssh / gh,
                 git clone / fetch / pull, a DNS look-up, an email, a web address in a command
inline_script    ran code written into the command itself: a heredoc (<<EOF), python -c, node -e
secrets          touched a secrets file or key: .env, ~/.ssh, id_rsa, .pem / .key, credentials, .npmrc / .pypirc,
                 .git-credentials, GitHub CLI / Docker / Kubernetes / Azure / Google Cloud logins, Terraform state,
                 a browser's cookies or saved passwords; or printed a stored login (cmdkey /list, gh auth token)
deletes          deleted files or work: rm, del, Remove-Item, rmdir, git clean, git reset --hard, git checkout -- /
                 restore / stash drop, emptying a file, moving one out of the project, docker prune, kubectl delete,
                 terraform destroy, a database client dropping or deleting data
git_push         pushed with git
installs         installed or fetched packages to run: pip / uv / npm / yarn / pnpm / apt / brew / winget, npx, uvx
guardrails       changed the agent's own rules or checks: CLAUDE.md / AGENTS.md / GEMINI.md, .claude/settings, .mcp.json,
                 .codex/config.toml, .cursor rules, git hooks, pre-commit, husky, CI workflows
after_untrusted  a step above (or one that sends data out) taken in the same turn after untrusted content came in:
                 a web page, an MCP or network result, a file from outside the project. The turn's human message is
                 the checkpoint: content read before it was seen by you
env_dump         printed all the environment variables (where tokens and keys live): printenv, env, Get-ChildItem env:
pipe_to_shell    ran a script straight from the internet: curl ... | sh, iex (iwr ...), bash <(curl ...)
remote_access    opened this computer to others: a public tunnel (ngrok, cloudflared tunnel, ssh -R, serveo), a reverse
                 shell (nc -e, socat exec:, /dev/tcp/)
open_server      started a server on every network address (--host 0.0.0.0, php -S 0.0.0.0): asked, not blocked
encoded_command  ran a hidden command: powershell -EncodedCommand, base64 -d ... | sh
force_push       rewrote shared history: git push --force / -f / --force-with-lease, git filter-branch / filter-repo
persistence      set itself up to run again later: crontab, schtasks /create, Register-ScheduledTask, a Run key, a
                 startup folder, systemctl enable, launchctl load, a shell start-up file (~/.bashrc, $PROFILE), a
                 file put in a log-in place (~/Library/LaunchAgents, ~/.config/autostart, the Startup folder)
security_off     switched off a security tool: Defender, the firewall, SELinux, Gatekeeper
wipe             deleted the whole disk or the home folder: rm -rf / or ~ or $HOME, Remove-Item on a drive or $env:USERPROFILE,
                 format, diskpart, mkfs, dd onto a disk
privilege        raised its own rights or changed the computer itself: sudo / su / runas, chmod 777, icacls /grant
                 Everyone, an execution policy bypass, user accounts and admin groups, services, the machine's registry
                 or PATH, shutting down, a container with the host's rights, rundll32 / regsvr32 / mshta / wscript
guard_tamper     tried to change or switch off the SessionCost guard or the rules you set: its settings, log or folder
                 (~/.sessioncost/guard), the project's .sessioncost-allow, `guard off`, SESSIONCOST_GUARD_DIR,
                 Claude Code's disableAllHooks, or an installed plugin (~/.claude/plugins). Reading them is fine. Only
                 you change them (the guard blocks the agent, always)
injected         a result (a web page, a file, a tool's answer) showed signs of a prompt injection (textflags.py):
                 text written to steer the agent, or characters a person cannot see. The steps after it are judged
                 more strictly (the guard blocks a risky one instead of asking)
key_in_output    a key or token showed up in a tool result (a script printed it, a file held it), or a script about
                 to run prints a key, token or password variable: the value reaches the model and the chat's log.
                 The guard asks first, and after one came in, asks before anything that sends data out
poisoned_tool    a tool of an MCP server whose tool descriptions showed signs of a prompt injection when the chat
                 started (the guard reads them itself): the guard blocks every tool of that server for the chat
secret_commit    a git commit that would take a secrets file into the history (.env, a key, credentials): staged before, or
                 added by the same command (git add -A / ., commit -a). The live guard asks git which files would go in
new_package      installs a package that is not in the project's own lists (pyproject.toml, requirements*.txt, uv.lock,
                 package.json): an agent can invent a name, and attackers register such names. Judged only where a list is
repeat_fail      runs a command that already failed twice in a row the same way, with no file changed in between
                 (Claude Code tells the guard when a step fails)
mcp_action       an MCP tool that changes or sends something (its name: delete, drop, merge, push, send, publish,
                 create, update, write, run, execute…); its arguments are checked like a command (a key, a path
                 outside the project, DROP / DELETE FROM)
A key-looking value (an API key, a token, a private key) in a command, a web address or an MCP tool's arguments is
"secrets" too.
Each mark has a severity (SEVERITY): high, review or info. The live guard (guard.py) blocks the NEVER marks, asks
about the rest of high and review, and only logs info. A project can expect some steps: one line per step in
.sessioncost-allow in the project folder, "mark  pattern" (the pattern matches the command, the file or the tool
name; * is a wildcard), e.g. "outside_project  ~/.claude/projects/*/memory/*". Those are listed as allowed, not red.

Only the yes/no mark leaves this module; the command text is never kept.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re

from . import risk_bypass
from .model import Session
from .risk_patterns import (_DB_CLIENT, _DB_WIPE, _DELETE, _ENCODED, _ENV_DUMP, _FILE_API, _FORCE, _GUARD, _GUARD_IN_CMD,
                            _INLINE, _INSTALL, _MCP_ACT, _MEMORY, _NET_CMD, _OUTWARD_TOOLS, _OWN_PATH, _PERSIST,
                            _NC_SHELL, _OPEN_SERVER, _PIPE_SHELL, _SSH_FWD_OPT, _PLUGINS_PATH, _REMOTE, _PRINTS_KEY, _PRIVILEGE, _PUSH, _QUIET, _QUOTED, _RC_FILE,
                            _READ_ONLY, _SCRIPT_ENV, _SEC_OFF, _SET_VAR, _SECRET, _SECRET_CMD, _SECRET_VALUE, _SECRET_VAR, _SEND,
                            _SEND_ONLY, _SINKS, _SQL_DESTROY, _TAMPER_CMD, _TAMPER_PATH, _TO_PLUGINS, _VENDOR, _WIPE,
                            _WRITES, ALLOW_FILE, SEVERITY, _direct, _downloads_then_runs, _inner_cmds, _rules_file)
from .risk_shell import (_LOOK_ONLY, _NAMES_ONLY, _RUNS_BODY, _moves_out, _no_prose, doc_bodies_out, _outside, _parts, _quoted_secret,
                         _reads_secret, _shell_part, _split_outside_quotes, _unquote, _without_searches,
                         search_safe_parts)
# Names other modules and tests use as risks.<name>; they live in risk_patterns.py.
from .risk_patterns import (_COMMIT, _RULES_NAME, NEVER, WATCH, _norm, mark_commit_check_skips,  # noqa: F401
                            code_text, prints_key, real_key, tamper_write)


_PRINTS_PATHS = re.compile(r"\s*(?:echo|printf|Write-Output|Write-Host|ls|dir|Get-ChildItem|gci|find|"
                           r"Resolve-Path)\b", re.I)    # what it prints is a path: a pipe can hand it to a writer
# pnpx is `pnpm dlx`: it always downloads, so only npx and bunx (which run node_modules/.bin first) are here
_NPX_RUN = re.compile(r"^\s*(?:&\s*)?(?:npx|bunx)(?:\.cmd|\.exe)?\s+([a-z0-9][\w.\-]*)(?=\s|$)", re.I)
# a part that starts a command from text or runs a package the install check does not see: xargs npx,
# eval "npm i x", sh -c "...". npm run build, uv run pytest, ./test.sh are ordinary steps
_MAY_INSTALL = re.compile(r"^\s*(?:&\s*)?(?:sudo\s+)?(?:pnpx|uvx|pipx\s+run|eval|xargs|iex|Invoke-Expression|"
                          r"(?:ba|z|da)?sh\s+-\w*c|cmd(?:\.exe)?\s+/[ck]|(?:pwsh|powershell)(?:\.exe)?\s+-c)\b", re.I)


def _local_npx(run: str, cwd: str, raw: str = "") -> bool:
    """True when every install-like part is `npx <tool>` and the folder it runs in (after any `cd`) has
    node_modules/.bin/<tool>: the project's own tool, nothing fetched (owner yes 2026-11-24). A version
    (tsc@5), a flag before the name (-y, -p) or a tool not there still counts as fetching. Any other part that
    names an installer or runs a command from text (xargs, eval) keeps the question."""
    from .guard_reads import _CD_UNSURE, _from, _split_cmd, cd_target
    if _CD_UNSURE.search(raw or run):
        return False                          # cd sub | true; npx x: npx runs in the first folder (raw: the | is kept)
    here = ""
    for p in _split_cmd(run):
        if (to := cd_target(p)) is None:
            return False
        if to:
            here = _from(here, to)
            continue
        m = _NPX_RUN.match(p)
        if not m:
            if _INSTALL.search(p) or _MAY_INSTALL.search(p):
                return False
            continue
        if re.search(r"&|\$\(|`|[<>]\(", p[m.end():]) or _INSTALL.search(p[m.end():]):
            return False                      # npx tsc & npm i x, $(npm i x): an install hides in the same part
        base = here if here and (os.path.isabs(here) or here[:1] in "\\/~") else os.path.join(cwd or ".", here)
        bin_dir = os.path.join(os.path.expanduser(base), "node_modules", ".bin")
        if not any(os.path.isfile(os.path.join(bin_dir, m.group(1) + ext)) for ext in ("", ".cmd", ".exe")):
            return False
    return True


def watch_marks(a, cwd: str) -> list[str]:
    """The watch-list marks of one step (see the module notes)."""
    out = []
    tool, full = (a.tool or "").lower(), a.target or ""
    if a.kind == "command":
        full = risk_bypass.fold(full)                                     # '.' + 'env' is '.env'
    t = _unquote(_shell_part(full)) if a.kind == "command" else full
    ticks = "powershell" not in tool                  # in PowerShell a backtick escapes a letter, it runs nothing
    if a.kind == "command" and (inner := _inner_cmds(_shell_part(full), ticks=ticks)):
        t += "".join("\n" + _unquote(_shell_part(i)) for i in inner)      # bash -c "...": the inner command, on its own line
    if a.kind == "command":
        t = risk_bypass.expand(t, cwd)                                    # cat .e*, cat $(ls -a | grep en): the file named
    script = a.kind == "command" and bool(_INLINE.search(full) and _RUNS_BODY.search(t))
    by_script = risk_bypass.script_marks(full) if script else []
    # the steps it takes, search words left out
    run = _without_searches(_shell_part(full), [_shell_part(i) for i in _inner_cmds(_shell_part(full), ticks=ticks)]) if a.kind == "command" else t
    if a.kind in ("edit", "write") and any(_outside(p, cwd) for p in full.splitlines()):
        out.append("outside_project")
    from .guard_scan import own_app_tool
    own_app = own_app_tool(a)                 # the agent's own plan, to-do, docs and schedule tools: the user's own app
    if not own_app and (a.kind == "web" or tool.startswith("mcp__") or tool in _OUTWARD_TOOLS or
                        (a.kind == "command" and _NET_CMD.search(run))):
        out.append("network")
    if a.kind == "command" and _INLINE.search(full):
        out.append("inline_script")
    if a.kind == "command":                               # a part that opens or sends the file, not one that lists it
        hit = not _NAMES_ONLY.fullmatch(t.strip()) and (      # names only, no values: not a secret read
            any(_SECRET.search(p) and not _LOOK_ONLY.search(p) for p in search_safe_parts(_no_prose(t))) or
            any(_quoted_secret(p) for p in search_safe_parts(t)) or
            bool(_INLINE.search(full) and _RUNS_BODY.search(t) and _reads_secret(full)))
    else:
        hit = a.kind != "search" and bool(_SECRET.search(t))      # a search pattern that mentions .env reads no secret
        hit = hit or (tool in ("grep", "grep_search") and bool(_SECRET.search(" " + str(getattr(a, "paths", "") or ""))))   # searching inside one does
    if hit or "secrets" in by_script or ((a.kind in ("command", "web") or tool.startswith("mcp__")) and real_key(full)):
        out.append("secrets")
    if tool.startswith("mcp__") and not own_app:
        out += _mcp_marks(tool.split("__")[-1], full, cwd)
    if a.kind == "command" and (_DELETE.search(run) or _moves_out(run, cwd) or "deletes" in by_script or
                                (_DB_CLIENT.search(run) and (_SQL_DESTROY.search(full) or _DB_WIPE.search(full)))):
        out.append("deletes")
    if a.kind == "command" and _PUSH.search(run):
        out.append("git_push")
    if a.kind == "command" and _INSTALL.search(run) and not _local_npx(run, cwd, _shell_part(full)):
        out.append("installs")
    if a.kind == "command" and (_SSH_FWD_OPT.search(_shell_part(full)) or _NC_SHELL.search(_no_prose(_shell_part(full)))):
        out.append("remote_access")
    if (a.kind in ("edit", "write") and any(_GUARD.search(p.strip()) or _MEMORY.search(p.strip()) or _rules_file(p.strip(), cwd)
                                            for p in full.splitlines())) or \
            (a.kind == "command" and (_GUARD_IN_CMD.search(t) or _rules_file(t, cwd)) and _WRITES.search(_QUIET.sub(" ", t))):
        out.append("guardrails")
    if a.kind == "command":
        for mark, rx, text in (("pipe_to_shell", _PIPE_SHELL, t), ("encoded_command", _ENCODED, full), ("env_dump", _ENV_DUMP, _QUOTED.sub("''", t)),
                               ("force_push", _FORCE, _no_prose(run)), ("persistence", _PERSIST, run), ("security_off", _SEC_OFF, run),
                               ("wipe", _WIPE, run), ("privilege", _PRIVILEGE, run), ("remote_access", _REMOTE, _no_prose(run)),
                               ("open_server", _OPEN_SERVER, _no_prose(run))):
            if rx.search(text) or mark in by_script:
                out.append(mark)
        if "env_dump" not in out and risk_bypass.env_print(t):
            out.append("env_dump")                                        # node -p process.env
        if "pipe_to_shell" not in out and _downloads_then_runs(t):
            out.append("pipe_to_shell")
        if "env_dump" not in out and _INLINE.search(full) and _RUNS_BODY.search(t) and _SCRIPT_ENV.search(full):
            out.append("env_dump")
        if "secrets" not in out and (_SECRET_VAR.search(t) or (_INLINE.search(full) and _PRINTS_KEY.search(full)) or
                                     (_FILE_API.search(full) and _SECRET.search(_QUOTED.sub(lambda m: " " + m.group(0)[1:-1] + " ", full)))):
            out.append("secrets")       # python -c "print(os.environ['X_SECRET'])", [IO.File]::ReadAllText('.env')
        if "secrets" not in out and _SECRET_CMD.search(t):
            out.append("secrets")       # cmdkey /list, gh auth token: a stored login printed
        if _RC_FILE.search(t) and _WRITES.search(_QUIET.sub(" ", t)) and "persistence" not in out:
            out.append("persistence")
        if _direct(run) or risk_bypass.hooks_off(t, cwd):
            out.append("skips_commit_checks")
    elif a.kind in ("edit", "write") and any(_RC_FILE.search(p.strip()) for p in full.splitlines()):
        out.append("persistence")
    if {"injection", "hidden_text"} & set(getattr(a, "result_flags", None) or []):
        out.append("injected")
    if "secret_value" in (getattr(a, "result_flags", None) or []):
        out.append("key_in_output")
    if _tampers(a, full, t) or (a.kind == "command" and risk_bypass.moves_guard_settings(t)):
        out.append("guard_tamper")
    return list(dict.fromkeys(out))


def _glob_own(word: str) -> bool:
    """A path with a wildcard or {a,b} that can stand for the guard's folder or allow file: ~/.session*,
    ~/.s*/gu?rd, ~/.sessio{n,}cost. The shell's * skips dot folders, so only a piece starting with "." can stand
    for .sessioncost (cd packages/* is no guard path)."""
    import fnmatch
    word = word.strip("'\"")
    if not re.search(r"[*?\[{]", word):
        return False
    words, done = [word], []
    for _ in range(8):                        # {a,b} pieces, one at a time
        nxt = []
        for w in words:
            m = re.search(r"\{([^{}]*)\}", w)
            (nxt if m else done).extend([w[:m.start()] + alt + w[m.end():] for alt in m.group(1).split(",")]
                                        if m else [w])
        words = nxt
    for w in done + words:
        segs = [s for s in re.split(r"[\\/]+", w) if s]
        for i, s in enumerate(segs):
            if not s.startswith("."):
                continue
            if fnmatch.fnmatch(".sessioncost-allow", s) and i == len(segs) - 1:
                return True
            if fnmatch.fnmatch(".sessioncost", s) and (i == len(segs) - 1 or fnmatch.fnmatch("guard", segs[i + 1])):
                return True
    return False


def _tampers(a, full: str, shell: str) -> bool:
    """A step that would change the guard or the rules you set (see guard_tamper in the module notes)."""
    if a.kind in ("edit", "write"):
        return any(_TAMPER_PATH.search(p.strip()) for p in full.splitlines())
    if a.kind == "command":
        full = doc_bodies_out(full)           # text only written into a document runs nothing (a handover's notes)
        if _TAMPER_CMD.search(_no_prose(full)):
            return True
        bare = _no_prose(shell)
        q = _QUIET.sub(" ", bare)
        parts = _parts(q)                     # each part of a pipe too: cat … | head reads; … | tee <file> writes
        inside = False                        # after cd into the guard's folder, every later part acts on it
        names = []                            # $g = "<guard folder>": a later part that uses $g acts on it
        stack = []                            # pushd remembers where it came from; popd goes back there
        for seg in re.split(r"&&|\|\||;|\n", q):
            fed = False                       # echo <guard path> | xargs rm: what a pipe hands on names the guard
            for p in (x for x in seg.split("|") if x.strip()):
                uses = any(re.search(rf"\$\{{?{n}\b", p) for n in names)
                if re.match(r"\s*(?:popd|Pop-Location)\b", p, re.I):
                    inside = stack.pop() if stack else False
                    continue
                if cd := re.match(r"\s*(cd|pushd|Push-Location|Set-Location|sl)\b", p, re.I):
                    words = [w.strip("'\"") for w in p.split()[1:]
                             if not re.fullmatch(r"-(?:Path|LiteralPath|P|L|-)", w, re.I)]
                    to = words[0] if words else ""
                    if cd.group(1).lower() in ("pushd", "push-location"):
                        stack.append(inside)
                    inside = (bool(_OWN_PATH.search(p)) or uses or _glob_own(to) or    # cd $g, cd ~/.session*
                              (inside and to and not re.match(r"[\\/~$%]|[A-Za-z]:", to)))   # cd .. from it: beside it
                    continue
                if (m := _SET_VAR.match(p)) and _OWN_PATH.search(" " + m.group(2)):
                    names.append(m.group(1))
                    continue
                if inside or uses or _OWN_PATH.search(p) or any(_glob_own(w) for w in p.split()[1:]):
                    if not _READ_ONLY.search(p):
                        return True           # the guard's folder and your allow file: read them, nothing else
                    fed = fed or bool(_PRINTS_PATHS.match(p))
                elif fed and not _READ_ONLY.search(p):
                    return True
        if any(re.search(rf"\b(?:export|setx|declare\s+-\w*x|typeset\s+-\w*x)\s+[^;&|\n]*\b{n}\b", q) for n in names) \
                or (names and re.search(r"\bset\s+(?:-\w*a|-o\s+allexport)\b", q)):
            return True                       # g=<guard folder>; export g; ./x.sh: a script it starts reads $g
        if _INLINE.search(full) and (_OWN_PATH.search(full.replace("~/", " ~/")) or "SESSIONCOST_GUARD_DIR" in full.upper()):
            return True                       # a script written into the command can do anything with them
        # an installed plugin: running its scripts is how skills work; a write, move or delete aimed at its files is not
        return any(_PLUGINS_PATH.search(p) and (_DELETE.search(p) or _TO_PLUGINS.search(_QUIET.sub(" ", p))) for p in parts)
    if (a.tool or "").startswith("mcp__"):
        from .guard_scan import own_app_tool  # the agent's own docs and plans: text about the guard changes nothing
        return not own_app_tool(a) and bool(_TAMPER_PATH.search(full) or _TAMPER_CMD.search(full))
    return False


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def _mcp_marks(name: str, args: str, cwd: str) -> list[str]:
    """An MCP tool's own marks: what its name says it does, and what is in its arguments."""
    out = []
    if _MCP_ACT.search(name):
        out.append("mcp_action")
    if _SQL_DESTROY.search(args):
        out.append("deletes")
    try:
        vals = list(_strings(json.loads(args)))
    except ValueError:
        vals = []
    if re.search(r"write|edit|move|create|upload|delete|remove|rename|save", name, re.I) and \
            any(len(v) < 400 and "\n" not in v and _outside(v, cwd) for v in vals):
        out.append("outside_project")
    return out


def _untrusted_in(a, cwd: str, strict: bool = False) -> bool:
    """A step that brought content the user did not write into the chat: the web, an MCP or network result, any
    result showing signs of an injection; strict: also a file from outside the project or from a folder of other
    people's code (node_modules, vendor, site-packages…). The user's own files elsewhere are not untrusted (owner
    2026-11-18: memory notes and handovers were the source of most needless asks). A web page, network command output
    or the agent's own app tools count only in strict mode: otherwise their words are scanned (guard_scan.py)."""
    from .guard_scan import scan_only
    if {"injection", "hidden_text"} & set(getattr(a, "result_flags", None) or []) or "injected" in a.risks:
        return True
    if "network" in a.risks and (a.tool or "").lower() not in _SEND_ONLY and (strict or not scan_only(a)):
        return True                                                             # it brought something back
    paths = (a.target or "").splitlines()
    return strict and a.kind == "read" and any(_outside(p, cwd) or _VENDOR.search(p) for p in paths)


def _sends_out(a) -> bool:
    return a.kind == "command" and bool(_SEND.search(_shell_part(a.target or "")))


_PUSH_VALUE_OPT = {"-o", "--push-option", "--receive-pack", "--exec"}


def push_targets(cmd: str) -> list[str]:
    """Where each git push in a command goes: the remote name or the URL as written ('' for the default remote)."""
    out = []
    for p in _parts(_unquote(_shell_part(cmd or ""))):
        m = re.search(r"\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+|-C\s+\S+\s+)*push\b(.*)$", p, re.I)
        if not m:
            continue
        words, dest, skip = m.group(1).split(), "", False
        for w in words:
            if skip:
                skip = False
            elif w.startswith("--repo="):
                dest = w.split("=", 1)[1]
                break
            elif w in _PUSH_VALUE_OPT:
                skip = True
            elif not w.startswith("-"):
                dest = w.strip("'\"")
                break
        out.append(dest)
    return out


def read_allow(cwd: str) -> list[tuple[str, str]]:
    """The project's expected steps: (mark, pattern) from .sessioncost-allow in the project folder and the guard's
    per-project allow file."""
    out = []
    f = os.path.join(cwd, ALLOW_FILE) if cwd else ""
    if f and os.path.isfile(f):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split("#", 1)[0].split(None, 1)
                    if len(parts) == 2 and (parts[0] in SEVERITY or parts[0] == "step"):   # step: allowed in allow-list mode
                        out.append((parts[0], parts[1].strip()))
        except OSError:
            pass
    if cwd:
        from .guard_store import _read, project_allow_path
        p = project_allow_path(cwd)
        if p.is_file():
            for item in (_read(p, {}).get("rules") or []):
                if isinstance(item, list) and len(item) == 2 and (item[0] in SEVERITY or item[0] == "step") and \
                        item[0] not in ("secrets", "key_in_output"):     # a saved project rule never unlocks keys
                    pair = (str(item[0]), str(item[1]).strip())
                    if pair not in out:
                        out.append(pair)
    return out


def _slashes(p: str) -> str:
    home = os.path.expanduser("~").replace("\\", "/")
    p = p.replace("\\", "/")
    if home and p.lower().startswith(home.lower()):
        p = "~" + p[len(home):]
    return p.lower() if os.name == "nt" else p


def _rule_re(pat: str, chain: bool = True) -> re.Pattern:
    """A .sessioncost-allow pattern. With `chain`, its * and ? stop at a chain (&&, ||, ;, |, a new line), so
    `npm run build*` never covers `npm run build && git push`."""
    star = r"[^&|;\n]" if chain else "."
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        end = pat.find("]", i + 2) if c == "[" else -1
        if c == "*":
            out.append(star + "*")
        elif c == "?":
            out.append(star)
        elif end > 0:                                    # [abc] or [!abc], as in shell patterns
            body = pat[i + 1:end].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out), re.S)


def _allowed(a, mark: str, rules: list[tuple[str, str]], cwd: str = "", carries=None) -> bool:
    """Whether the project's rules for `mark` cover step `a`. A rule matches the tool name, the whole target, or each
    part of it: in a chained command (or a target of several lines) every part that carries the mark on its own must
    match a rule, so one expected step never covers another chained to it. `carries(part_action)` says whether a part
    carries the mark (default: risks.watch_marks); when no part does on its own, every part must match."""
    pats = [_slashes(os.path.expanduser(pat)) for m, pat in rules if m == mark]   # ~ is the home folder on both sides
    if not pats:
        return False
    parts = _split_outside_quotes(a.target or "") if a.kind == "command" else (a.target or "").splitlines()
    parts = [x.strip() for x in parts if x.strip()]
    loose = [_rule_re(p, chain=False) for p in pats]
    hit = lambda x, rx=loose: any(p.fullmatch(x) for p in rx)
    whole = _slashes(a.target or "").strip()
    if hit((a.tool or "").lower() if os.name == "nt" else a.tool or "") or (whole and hit(whole, loose if len(parts) < 2 else
                                                                                         [_rule_re(p) for p in pats])):
        return True
    if len(parts) < 2:
        return False
    carries = carries or (lambda sub: mark in watch_marks(sub, cwd))
    marked = [x for x in parts if carries(dataclasses.replace(a, target=x))] or parts
    return all(hit(_slashes(x)) for x in marked)


def mark_watch(s: Session) -> None:
    """Add the watch-list marks to every step of the main chat and the helpers, move the ones the project expects
    (.sessioncost-allow) to `allowed`, then mark the risky steps taken after untrusted content in the same turn."""
    rules = read_allow(s.cwd)
    for c in s.all_calls:
        for a in c.actions:
            for m in watch_marks(a, s.cwd):
                if m in a.risks or m in a.allowed:
                    continue
                (a.allowed if _allowed(a, m, rules, s.cwd) else a.risks).append(m)
    for t in s.turns:                       # the main chat, turn by turn: your message is the checkpoint
        source = None
        for c in t.calls:
            for a in c.actions:
                if source and "after_untrusted" not in a.risks and (_SINKS & set(a.risks) or _sends_out(a)):
                    if _allowed(a, "after_untrusted", rules, s.cwd):
                        a.allowed.append("after_untrusted")
                    else:
                        a.risks.append("after_untrusted")
                        a.chain_from = source
            if source is None and any(_untrusted_in(a, s.cwd) for a in c.actions):
                source = (c.agent, c.n)
