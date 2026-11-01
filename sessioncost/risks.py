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
encoded_command  ran a hidden command: powershell -EncodedCommand, base64 -d ... | sh
force_push       rewrote shared history: git push --force / -f / --force-with-lease, git filter-branch / filter-repo
persistence      set itself up to run again later: crontab, schtasks /create, Register-ScheduledTask, a Run key, a
                 startup folder, systemctl enable, launchctl load, a shell start-up file (~/.bashrc, $PROFILE)
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

import fnmatch
import json
import os
import re
import tempfile

from .model import Session

WATCH = ("guard_tamper", "wipe", "pipe_to_shell", "encoded_command", "env_dump", "persistence", "security_off", "force_push", "privilege",
         "injected", "after_untrusted", "poisoned_tool", "key_in_output", "guardrails", "mcp_action", "secrets", "secret_commit", "skips_commit_checks",
         "git_push", "deletes", "installs", "new_package", "repeat_fail", "outside_project", "network", "inline_script")
SEVERITY = {"guard_tamper": "high", "wipe": "high", "privilege": "high", "pipe_to_shell": "high", "encoded_command": "high", "env_dump": "high", "persistence": "high",
            "security_off": "high", "force_push": "high", "injected": "high", "poisoned_tool": "high", "key_in_output": "high", "mcp_action": "review",
            "after_untrusted": "high", "guardrails": "high", "secrets": "high", "secret_commit": "high", "skips_commit_checks": "high",
            "git_push": "review", "deletes": "review", "installs": "review", "new_package": "review", "repeat_fail": "review",
            "outside_project": "review",
            "network": "review", "inline_script": "info"}
NEVER = {"guard_tamper", "wipe", "pipe_to_shell", "encoded_command", "env_dump", "persistence", "security_off", "force_push",
         "skips_commit_checks", "secret_commit"}      # the live guard blocks these (reading a secret too: see guard.tier)
ALLOW_FILE = ".sessioncost-allow"

_COMMIT = re.compile(r"\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+)*commit\b", re.I)
_SKIP_SET = re.compile(r"(?:^|[\s;&|(])(?:\$env:SKIP\s*=|(?:export\s+|set\s+)?SKIP\s*=)", re.I)
# the options before any quoted message: a message that mentions "-n" or "--no-verify" is not the option
_NO_VERIFY = re.compile(r"""\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+)*(?:commit|push)\b[^\n;&|"']*?\s--no-verify\b""", re.I)
_COMMIT_N = re.compile(r"""\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+)*commit\b[^\n;&|"']*?\s-(?=[a-z]*n)[a-z]+\b""", re.I)   # -n, -an
_HOOKS_OFF = re.compile(r"\b(?:HUSKY\s*=\s*['\"]?0|HUSKY_SKIP_HOOKS\s*=\s*['\"]?1|PRE_COMMIT_ALLOW_NO_CONFIG\s*=)", re.I)
LOOK_AHEAD = 3           # a SKIP set on its own counts when one of the next few commands commits


def _direct(cmd: str) -> bool:
    if _NO_VERIFY.search(cmd) or _COMMIT_N.search(cmd):
        return True
    if (_HOOKS_OFF.search(cmd) or _SKIP_SET.search(cmd)) and _COMMIT.search(cmd):
        return True
    return False


def mark_commit_check_skips(s: Session) -> None:
    """Add 'skips_commit_checks' to the risk marks of every command that tried to switch the commit checks off."""
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        cmds = [a for c in calls for a in c.actions if a.kind == "command" and a.target]
        for i, a in enumerate(cmds):
            hit = _direct(a.target)
            if not hit and (_SKIP_SET.search(a.target) or _HOOKS_OFF.search(a.target)):
                hit = any(_COMMIT.search(b.target) for b in cmds[i + 1:i + 1 + LOOK_AHEAD])
            if hit and "skips_commit_checks" not in a.risks:
                a.risks.append("skips_commit_checks")


_OUTWARD_TOOLS = {"webfetch", "websearch", "artifact", "senduserfile", "pushnotification",
                  "remotetrigger", "sendmessage", "web_search", "web_fetch", "read_url_content", "search_web"}
_SEND_ONLY = {"senduserfile", "pushnotification", "sendmessage", "remotetrigger"}   # network out, nothing comes back in
_NET_CMD = re.compile(r"(?:^|[\s;&|(`])(?:curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm|ssh|scp|sftp|rsync|ftp|"
                      r"gh\s+\w+|git\s+(?:-c\s+\S+\s+)*(?:clone|fetch|pull)|nslookup|dig|Resolve-DnsName|"
                      r"Send-MailMessage|sendmail|mail\s+-s)\b|https?://", re.I)
_INLINE = re.compile(r"<<-?\s*['\"]?[A-Za-z_]\w*|@['\"][ \t]*$|"
                     r"(?:^|[\s;&|(\\/])(?:python3?|py|node|ruby|perl|php)(?:\.exe)?\s+(?:-[a-zA-Z]+\s+)*-[ce]\s",
                     re.I | re.M)
_SECRET = re.compile(r"(?:^|[\\/\s'\"`=@])\.env(?!\.(?:example|sample|template|dist|defaults?)\b)(?:\.[\w.-]+)?(?:$|[\s'\"`;|&)])|[\\/]\.ssh(?:[\\/]|$)|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|"
                     r"\.(?:pem|key|p12|pfx)(?:$|[\s'\"`;|&)])|[\\/]\.aws[\\/]credentials|\bcredentials\.json\b|"
                     r"(?:^|[\\/\s'\"`])\.(?:npmrc|pypirc|netrc)\b|\bsecrets?\.(?:json|ya?ml|toml|env)\b|"
                     # the other places tools keep logins (2026-11-01): git, GitHub CLI, Docker, Kubernetes, Azure,
                     # Google Cloud, Terraform state, a browser's saved cookies and passwords
                     r"(?:^|[\\/\s'\"`=~])\.(?:git-credentials|kube[\\/]config|docker[\\/]config\.json)\b|"
                     r"[\\/]gh[\\/]hosts\.ya?ml\b|[\\/]\.azure[\\/](?:accessTokens\.json|msal_token_cache)|"
                     r"\bapplication_default_credentials\.json\b|[\\/]gcloud[\\/](?:credentials|access_tokens)\.db\b|"
                     r"\.tfstate(?:\.backup)?\b|[\\/](?:Cookies|Login Data|cookies\.sqlite|logins\.json|key4\.db)['\"]?(?:$|[\s'\"`;|&)])",
                     re.I)
# commands that print a stored login: Windows Credential Manager, the macOS keychain, git's and GitHub CLI's helpers
_SECRET_CMD = re.compile(r"\bcmdkey(?:\.exe)?\s+/list\b|\bvaultcmd\b|\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain)\b|"
                         r"\bgit(?:\.exe)?\s+credential(?:-\w+)?\s+(?:fill|get)\b|\bgh(?:\.exe)?\s+auth\s+token\b|"
                         r"\bGet-StoredCredential\b", re.I)
_DELETE = re.compile(r"(?:^|[\s;&|(`])(?:rm|del|erase|rmdir|rd|Remove-Item|ri)\s|"
                     r"\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+)*(?:clean\s+(?:-\w+\s+)*-\w*f\w*|reset\s+--hard|branch\s+-D|"
                     # throwing away uncommitted work: checkout of paths, restore (not --staged), dropping stashes
                     r"checkout\s+(?:\S+\s+)?--\s+\S|checkout\s+\.(?=\s|$)|restore\s+(?![^\n;&|]*--staged)\S|stash\s+(?:drop|clear))|"
                     # containers, clusters and infrastructure
                     r"\bdocker(?:\.exe)?\s+(?:(?:system|image|volume|container|network|builder)\s+prune|rm|rmi)\b|"
                     r"\bkubectl(?:\.exe)?\s+delete\b|\bhelm\s+(?:uninstall|delete)\b|\bterraform(?:\.exe)?\s+destroy\b|"
                     r"\bterraform(?:\.exe)?\s+apply\b[^\n;&|]*\s-destroy\b|"
                     # emptying a file
                     r"\b(?:Clear-Content|clc)\s|\btruncate\s+(?:-\w+\s+)*-s\s*0\b", re.I | re.M)
# a database client running a statement that drops or deletes data (psql -c "DROP TABLE users")
_DB_CLIENT = re.compile(r"\b(?:psql|mysql|mariadb|sqlite3|sqlcmd|mongosh|mongo|redis-cli|duckdb|clickhouse-client)(?:\.exe)?\b", re.I)
_DB_WIPE = re.compile(r"\bflushall\b|\bflushdb\b|\bdropDatabase\(|\.drop\(\)|\.deleteMany\(", re.I)
# moving a file out of the project removes it from there: mv app/x.py /tmp/ asks like a delete
_MOVE = re.compile(r"^\s*(?:mv|move|Move-Item|mi)\s+(.+?)\s*$", re.I)
_PUSH = re.compile(r"\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+)*push\b", re.I)
# Windows names count too: .\.venv\Scripts\pip.exe install (Antigravity, 2026-11-01), npm.cmd install
_INSTALL = re.compile(r"\b(?:pip3?(?:\.exe)?|uv(?:\.exe)?\s+pip|python3?(?:\.exe)?\s+-m\s+pip)\s+install\b|"
                      r"\buv(?:\.exe)?\s+(?:add|tool\s+install)\b|"
                      r"\b(?:npm|pnpm|yarn|bun)(?:\.cmd|\.exe)?\s+(?:i|install|add)\b|"
                      r"(?:^|[\s;&|(\\/])(?:npx|uvx|pnpx|bunx)(?:\.cmd|\.exe)?\s|"
                      r"\b(?:apt(?:-get)?|brew|winget|choco|scoop|cargo|go|gem)(?:\.exe)?\s+install\b|"
                      r"\b(?:poetry|pdm|rye)(?:\.exe)?\s+add\b|\bpipx(?:\.exe)?\s+(?:install|run)\b|"
                      r"\b(?:conda|mamba|micromamba)(?:\.exe)?\s+install\b", re.I)


_GUARD = re.compile(r"(?:^|[\\/])(?:\.cursorrules|\.mcp\.json|\.pre-commit-config\.ya?ml|"
                    r"\.gitlab-ci\.ya?ml)$|(?:^|[\\/])\.claude[\\/]settings(?:\.local)?\.json$|(?:^|[\\/])\.cursor[\\/](?:rules[\\/]|mcp\.json$)|"
                    r"(?:^|[\\/])\.codex[\\/]config\.toml$|(?:^|[\\/])\.gemini[\\/]settings\.json$|"
                    r"(?:^|[\\/])\.git[\\/]hooks[\\/]|(?:^|[\\/])\.husky[\\/]|(?:^|[\\/])\.github[\\/]workflows[\\/]|"
                    r"(?:^|[\\/])\.github[\\/]copilot-instructions\.md$|(?:^|[\\/])\.copilot[\\/]copilot-instructions\.md$", re.I)
# the agent's memory: notes it reads in every later chat (instructions planted there outlive this chat)
_MEMORY = re.compile(r"[\\/]\.claude[\\/]projects[\\/][^\\/]+[\\/]memory[\\/]|(?:^|[\\/])MEMORY\.md$|[\\/]\.codex[\\/]memor|"
                     r"[\\/]\.gemini[\\/]GEMINI\.md$", re.I)
# the rules files agents read, by their upper-case names (a docs page called agents.md is not one, #25)
_RULES_NAME = re.compile(r"(?:^|(?<=[\\/\s'\"`=>]))((?:CLAUDE|AGENTS|GEMINI)\.md)(?=$|[\s'\"`;|&)])", re.I)
_RULES_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")


def _rules_file(text: str, cwd: str) -> bool:
    """A rules file named in a path or a command: its own name, or another spelling of it where the real file is
    there (on Windows and macOS that is the same file)."""
    for m in _RULES_NAME.finditer(text or ""):
        name = m.group(1)
        if name in _RULES_NAMES:
            return True
        folder = re.split(r"[\s'\"`=>]", text[:m.start(1)])[-1]
        try:
            if name[:-3].upper() + ".md" in os.listdir(_norm(os.path.join(cwd or "", folder) or ".")):
                return True
        except OSError:
            pass
    return False


_GUARD_IN_CMD = re.compile(r"(?:MEMORY\.md|\.cursorrules|\.mcp\.json|\.pre-commit-config\.ya?ml|"
                           r"\.claude[\\/]settings|\.claude[\\/]projects[\\/][^\\/\s]+[\\/]memory|\.codex[\\/]config\.toml|"
                           r"\.codex[\\/]memor|\.git[\\/]hooks|\.husky[\\/]|\.github[\\/]workflows|copilot-instructions\.md)", re.I)
# a value that looks like a key or token (sent out in a command, a URL or an MCP tool's arguments)
_SECRET_VALUE = re.compile(r"\b(?:sk-(?:ant-|proj-|live-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
                           r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abprs]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{20,}|"
                           r"npm_[A-Za-z0-9]{36})\b|-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----")
_MCP_ACT = re.compile(r"(?:^|_)(?:delete|remove|drop|destroy|truncate|purge|merge|push|send|post|publish|create|update|"
                      r"write|edit|move|rename|upload|run|exec|execute|deploy|invoke|transfer|revoke|close|archive|"
                      r"approve|comment|reply|query)(?:_|$)", re.I)
_SQL_DESTROY = re.compile(r"\b(?:drop\s+(?:table|database|schema)|truncate\s+table|delete\s+from|alter\s+table)\b", re.I)
_VENDOR = re.compile(r"(?:^|[\\/])(?:node_modules|vendor|site-packages|third_party|bower_components|\.cargo[\\/]registry|"
                     r"pkg[\\/]mod)(?:[\\/]|$)", re.I)
# redirections that write nothing anyone keeps: 2>/dev/null, >NUL, 2>&1
_QUIET = re.compile(r"\d?>>?\s*(?:/dev/null|\$null|NUL|nul)\b|\d?>&\d|&>\s*/dev/null")
_WRITES = re.compile(r">|\btee\b|\bsed\s+-i|\b(?:cp|mv|rm|Set-Content|Add-Content|Out-File|Copy-Item|Move-Item|Remove-Item)\b", re.I)
# a command that sends data out (not only fetches): curl/wget with a body or upload, PowerShell with a body or POST/PUT
_SEND = re.compile(r"\b(?:curl|wget)\b[^\n|;&]*\s(?:-d|--data(?:-\w+)?|-F|--form|-T|--upload-file|-X\s*(?:POST|PUT|PATCH)|"
                   r"--post-(?:data|file))\b|\b(?:Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\b[^\n|;&]*\s-(?:Body|InFile|Method\s+(?:Post|Put|Patch))\b|"
                   r"\bgh\s+(?:gist\s+create|issue\s+create|pr\s+create|api\b)|\bscp\b|\brsync\b", re.I)
# the guard and the rules you set: no step of the agent may change them (reading is fine)
_OWN_PATH = re.compile(r"(?:^|[\\/~\s'\"`=])\.sessioncost(?:[\\/]+guard(?:[\\/]|\b)|[\\/]?(?=['\"`\s)]|$))|"
                       r"(?:^|[\\/\s'\"`])\.sessioncost-allow\b|"
                       r"[\\/]\.(?:codex|cursor)[\\/]+hooks\.json\b|"          # where the guard's hooks live in Codex / Cursor,
                       r"[\\/]\.gemini[\\/]+config[\\/]+hooks\.json\b|(?:^|[\\/\s'\"`])\.agents[\\/]+hooks\.json\b|"   # Antigravity,
                       r"[\\/]\.copilot[\\/]+(?:hooks[\\/]|settings\.json\b)|"                                           # Copilot CLI
                       r"(?:^|[\\/\s'\"`])\.github[\\/]+(?:hooks[\\/]|copilot[\\/]+settings(?:\.local)?\.json\b)", re.I)
_PLUGINS_PATH = re.compile(r"[\\/]\.claude[\\/]+plugins[\\/]", re.I)
_TAMPER_PATH = re.compile(_OWN_PATH.pattern + "|" + _PLUGINS_PATH.pattern, re.I)
# a write aimed at an installed plugin: redirected into it, or the last argument of a copy / move / edit
_TO_PLUGINS = re.compile(r">>?\s*['\"]?[^\s'\"]*[\\/]\.claude[\\/]+plugins[\\/]|"
                         r"\b(?:cp|mv|tee|Copy-Item|Move-Item|Set-Content|Add-Content|Out-File|sed\s+-i)\b.*[\\/]\.claude[\\/]+plugins[\\/][^\s]*\s*$", re.I)
_TAMPER_CMD = re.compile(r"\bguard(?:\.py)?[\"']?\s+(?:off|remove)\b|\bSESSIONCOST_GUARD_DIR\b|\bdisableAllHooks\b|"
                         r"^\s*hooks\s*=\s*false\b", re.I | re.M)      # (the last: Codex's [features] switch)
_READ_ONLY = re.compile(r"^\s*(?:cat|type|Get-Content|gc|head|tail|less|more|grep|rg|findstr|Select-String|ls|dir|"
                        r"Get-ChildItem|wc|stat|file|python3?\s+-m\s+json\.tool)\b[^>|;&]*$", re.I)
_SINKS = {"guard_tamper", "wipe", "privilege", "guardrails", "secrets", "skips_commit_checks", "git_push", "deletes", "installs", "force_push",
          "pipe_to_shell", "encoded_command", "env_dump", "persistence", "security_off"}
_ENV_DUMP = re.compile(r"(?:^|[;&|(]\s*)(?:printenv|env|set|export\s+-p)\s*(?:$|[;&|>])|\b(?:Get-ChildItem|gci|Get-Item|gi|dir|ls)\s+env:(?![A-Za-z_])|"
                       r"\[Environment\]::GetEnvironmentVariables|/proc/[\w*]+/environ", re.I | re.M)
_PIPE_SHELL = re.compile(r"\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n;]*\|\s*(?:sudo\s+)?"
                         r"(?:sh|bash|zsh|dash|python3?|node|iex|Invoke-Expression|pwsh|powershell)\b|"
                         r"\b(?:iex|Invoke-Expression)\b[^\n;]*\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|DownloadString)\b|"
                         r"\b(?:ba|z)?sh\s+<\(\s*(?:curl|wget)\b|"
                         r"https?://[^\n;|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|python3?|node|iex|Invoke-Expression|pwsh|powershell)\b",
                         re.I)                         # (the last: any web address piped into a shell, whatever fetched it)
_ENCODED = re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*\s-e(?:c|n\w*)?\s+[A-Za-z0-9+/=]{16,}|"
                      r"\bbase64\s+(?:-d|--decode)\b[^\n;]*\|\s*(?:sh|bash|zsh|python3?|node)\b|\bFromBase64String\b[^\n]*\b(?:iex|Invoke-Expression)\b", re.I)
_FORCE = re.compile(r"\bgit(?:\.exe)?\s+(?:-c\s+\S+\s+)*push\b[^\n;&|]*\s(?:--force(?:-with-lease)?\b|-f\b|\+\S+)|\bgit(?:\.exe)?\s+filter-(?:branch|repo)\b", re.I)
_PERSIST = re.compile(r"\bcrontab\s+(?!-l\b)\S|\bschtasks(?:\.exe)?\s+/create\b|\bRegister-ScheduledTask\b|"
                      r"\breg(?:\.exe)?\s+add\s+\S*\\Run(?:Once)?\b|\\CurrentVersion\\Run(?:Once)?\b[^\n]*\b(?:Set|New)-ItemProperty\b|"
                      r"\b(?:Set|New)-ItemProperty\b[^\n]*\\CurrentVersion\\Run|\bsystemctl\s+(?:--user\s+)?enable\b|\blaunchctl\s+(?:load|bootstrap)\b|"
                      r"[\\/]Start Menu[\\/]Programs[\\/]Startup\b", re.I)
_RC_FILE = re.compile(r"(?:^|[\\/])(?:\.bashrc|\.bash_profile|\.zshrc|\.zprofile|\.profile|\.config[\\/]fish[\\/]config\.fish|"
                      r"Microsoft\.PowerShell_profile\.ps1|profile\.ps1)$|\$PROFILE\b", re.I)
_QUOTED = re.compile(r"'[^'\n]*'|\"(?:[^\"\\\n]|\\.)*\"")      # quoted text: a pattern or a message, not a command
# printing one secret variable: echo $OPENAI_API_KEY, Write-Output $env:GITHUB_TOKEN, or $env:X_SECRET on its own
_SECRET_VAR = re.compile(r"(?:^|[;&|(]\s*)(?:echo|printf|print|Write-Output|Write-Host|cat\s+<<<)\b[^;&|\n]*"
                         r"\$\{?(?:env:)?\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?)\}?(?![\w])|"
                         r"^\s*\$env:\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT)\s*$|"
                         # cmd's echo %X_SECRET%, .NET's GetEnvironmentVariable('X_SECRET'), Get-Item env:X_SECRET
                         r"(?:^|[;&|(]\s*)echo\b[^;&|\n]*%\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?)\w*%|"
                         r"\[(?:System\.)?Environment\]::GetEnvironmentVariable\(\s*['\"]?\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?)\w*['\"]?\s*[,)]|"
                         r"\b(?:Get-Item|gi|Get-Content|gc|cat|type)\s+env:\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?)\w*\b",
                         re.I | re.M)
# a script run from the command that prints every environment variable (one variable read by name is fine)
_SCRIPT_ENV = re.compile(r"\b(?:print|pprint|json\.dumps|repr|str|len)\(\s*(?:dict\(\s*)?os\.environ\s*\)|"
                         r"\bos\.environ\.(?:items|keys|values|copy)\(\)|\bfor\s+\w+(?:\s*,\s*\w+)?\s+in\s+(?:sorted\()?os\.environ\b|"
                         r"\bconsole\.(?:log|dir|table)\(\s*process\.env\s*\)|\bJSON\.stringify\(\s*process\.env\b|"
                         r"\bObject\.(?:keys|entries|values)\(\s*process\.env\s*\)|\bputs\s+ENV\b|\bENV\.(?:to_h|each|inspect)\b")
# a script run from the command that prints one key, token or password variable (os.environ["X_SECRET"], $env:GH_TOKEN)
_KEY_VAR = r"\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?)\w*"
_PRINTS_KEY = re.compile(rf"\b(?:print|pprint|console\.(?:log|info|error)|puts|echo|printf|Write-(?:Output|Host))\b[^\n]*?"
                         rf"(?:os\.environ\s*\[\s*['\"]{_KEY_VAR}['\"]|os\.(?:getenv|environ\.get)\(\s*['\"]{_KEY_VAR}['\"]|"
                         rf"process\.env\.{_KEY_VAR}\b|ENV\[\s*['\"]{_KEY_VAR}['\"]|\$\{{?(?:env:)?{_KEY_VAR}\}}?)", re.I)


# .NET's file reads, which name the file inside quotes: [IO.File]::ReadAllText('.env')
_FILE_API = re.compile(r"\[(?:System\.)?IO\.File\]::(?:Read|Open|Copy)\w*|\bFile\.(?:Read|Open)\w*\(", re.I)


def prints_key(script: str) -> bool:
    return bool(_PRINTS_KEY.search(script or ""))


# a command wrapped in another shell: bash -c "cat .env", sh -lc '...', pwsh -Command "...", cmd /c type .env. The
# words inside the quotes are the command the inner shell runs, not prose: they are checked like the outer command
_WRAPPED = re.compile(r"(?:^|[\s;&|(`])(?:(?:ba|z|da|k)?sh|pwsh|powershell|cmd)(?:\.exe)?(?:\s+-\w+(?:\s+[A-Za-z]\w*)?)*?"
                      r"\s+(?:-[a-zA-Z]*c|-Command|/[ck])\s+(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\n;&|]+)", re.I)


def _inner_cmds(cmd: str, depth: int = 3) -> list[str]:
    out = []
    for m in _WRAPPED.finditer(cmd or ""):
        body = m.group(1).strip()
        if body[:1] in "\"'" and body[-1:] == body[:1]:
            body = body[1:-1].replace('\\"', '"') if body[0] == '"' else body[1:-1]
        out.append(body)
        if depth > 1:
            out += _inner_cmds(body, depth - 1)
    return out


# a file downloaded in the command (curl -o, wget -O, Invoke-WebRequest -OutFile), then run in the same command
_DOWNLOAD_TO = re.compile(r"\b(?:curl|wget|iwr|Invoke-WebRequest)\b[^;&|\n]*?\s(?:-o|-O|--output|--output-document|-OutFile)\s+['\"]?([^\s'\";&|]+)", re.I)


def _downloads_then_runs(t: str) -> bool:
    for m in _DOWNLOAD_TO.finditer(t):
        name = re.escape(m.group(1))
        base = re.escape(re.split(r"[\\/]", m.group(1))[-1])
        run = re.compile(rf"(?:^|[;&|(]\s*)(?:sudo\s+)?(?:(?:sh|bash|zsh|python3?|py|node|pwsh|powershell|source|\.|&|iex)\s+"
                         rf"(?:-\w+\s+)*)?['\"]?(?:\.[\\/])?(?:{name}|{base})['\"]?(?=\s|$|[;&|)])", re.I | re.M)
        if run.search(t, m.end()):
            return True
    return False


# deleting the whole disk or the home folder
_WIPE = re.compile(r"(?:^|[;&|(]\s*)(?:sudo\s+)?rm\s+(?:-\w+\s+)*(?:--no-preserve-root\s+)?['\"]?(?:/\*?|~/?\*?|\$HOME/?\*?|\$\{HOME\}/?)['\"]?(?=\s*(?:$|[;&|)]))|"
                   r"\b(?:Remove-Item|rm|ri|del|rd|rmdir)\b[^;&|\n]*?\s['\"]?(?:[A-Za-z]:\\?\*?|~|\$HOME|\$env:USERPROFILE|%USERPROFILE%|\$env:HOMEDRIVE\\?)['\"]?"
                   r"(?=\s*(?:$|[;&|)]|\s-))|"
                   # formatting or overwriting a whole disk
                   r"(?:^|[;&|(]\s*)format(?:\.com)?\s+[A-Za-z]:|\bdiskpart\b|\bmkfs(?:\.\w+)?\s|\bdd\s+[^\n;&|]*\bof=/dev/(?!null\b)|"
                   r"\b(?:Format-Volume|Clear-Disk|Initialize-Disk)\b", re.I | re.M)
# raising the agent's own rights: sudo / su / runas, opening permissions to everyone, lifting PowerShell's script policy
_PRIVILEGE = re.compile(r"(?:^|[;&|(]\s*)(?:sudo|su|doas|runas|gsudo)\b|\bStart-Process\b[^\n]*-Verb\s+RunAs\b|"
                        r"\bchmod\s+(?:-\w+\s+)*(?:0?777|a\+rwx|o\+w)\b|\bicacls\b[^\n]*/grant\s+(?:Everyone|\*S-1-1-0)|"
                        r"\bSet-ExecutionPolicy\b[^\n]*\b(?:Unrestricted|Bypass)\b|"
                        r"\s-(?:ExecutionPolicy|ep|exec)\s+(?:Bypass|Unrestricted)\b|"
                        # changing the computer itself (2026-11-01): accounts, services, the machine's registry and
                        # PATH, shutting it down, a container with the host's rights, Windows tools that run hidden code
                        r"\bnet(?:\.exe)?\s+(?:user\s+\S+[^\n;&|]*\s/add\b|localgroup\s+administrators\b)|"
                        r"\b(?:New-LocalUser|Add-LocalGroupMember)\b|\buseradd\b|\busermod\s+[^\n]*-aG?\s*(?:sudo|wheel|admin)\b|"
                        r"\bsc(?:\.exe)?\s+(?:create|config|delete)\b|\b(?:New-Service|Set-Service)\b|"
                        r"\breg(?:\.exe)?\s+(?:add|delete|import)\s+(?:HKLM|HKEY_LOCAL_MACHINE)\b|"
                        r"\b(?:Set|New|Remove)-ItemProperty\b[^\n]*\bHKLM:|\bsetx(?:\.exe)?\s+(?:PATH\b|[^\n]*\s/M\b)|"
                        r"(?:^|[;&|(]\s*)(?:shutdown(?:\.exe)?\s|reboot\b|halt\b|poweroff\b)|\b(?:Stop-Computer|Restart-Computer)\b|"
                        r"\bdocker(?:\.exe)?\s+run\b[^\n;&|]*(?:--privileged\b|-v\s+['\"]?/:/|--pid[= ]host\b)|"
                        r"\b(?:rundll32|regsvr32|mshta|wscript|cscript)(?:\.exe)?\s", re.I | re.M)
_SEC_OFF = re.compile(r"\bSet-MpPreference\b[^\n]*-Disable\w*\s+\$?(?:true|1)\b|\bAdd-MpPreference\b[^\n]*-Exclusion|"
                      r"\bnetsh\s+(?:adv)?firewall\b[^\n]*\bstate\s+off\b|\bufw\s+disable\b|\bsetenforce\s+0\b|"
                      r"\bspctl\s+--master-disable\b|\bsystemctl\s+(?:stop|disable)\s+(?:firewalld|apparmor|auditd)\b", re.I)

_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?")
_HERE_STRING = re.compile(r"@(['\"])\s*$")        # PowerShell: @' or @" at the end of a line opens a here-string
# the written-in code is run (piped or fed to an interpreter, or python -c): its text counts, not only the shell's
_RUNS_BODY = re.compile(r"(?:^|[|;&(]\s*)(?:python3?|py|node|ruby|perl|php|bash|sh|zsh|pwsh|powershell)(?:\.exe)?\b"
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
_SCRIPT_ARG = re.compile(r"""((?:python3?|py|node|ruby|perl|php)\s+(?:-[a-zA-Z]+\s+)*-[ce]\s+)("(?:[^"\\]|\\.)*"|'[^']*')""",
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


_PROSE = re.compile(r"(?:-m|--message)\s+(?:\"(?:[^\"\\]|\\.)*\"|'[^']*')|\"[^\"\n]*\s[^\"\n]*\"|'[^'\n]*\s[^'\n]*'")
_LOOK_ONLY = re.compile(r"^\s*(?:ls|dir|stat|test|\[|Test-Path|touch|file|wc\s+-l|git\s+(?:check-ignore|ls-files))\b", re.I)


def _parts(cmd: str) -> list[str]:
    return [p for p in re.split(r"&&|\|\||;|\n|\|", cmd) if p.strip()]


def _no_prose(cmd: str) -> str:
    """The command without its prose: commit messages and quoted text with spaces (an echo, a search query) only
    mention things; a quoted single word ("git", '.env') was already unquoted."""
    return _PROSE.sub("''", cmd)


def _unquote(cmd: str) -> str:
    """Words as the shell sees them: "git" "push" is git push, .e''nv is .env (a way around simple text rules)."""
    cmd = re.sub(r"(?<=\w)(?:''|\"\")(?=[\w.])", "", cmd)
    return re.sub(r"(?<![\w\\])([\"'])([\w.+-]+)\1(?![\w])", r"\2", cmd)


def _norm(p: str) -> str:
    p = os.path.expanduser(p.strip().strip("'\"`"))
    if re.match(r"^/[a-zA-Z]/", p):                      # Git Bash: /c/Users/... is C:\Users\...
        p = p[1] + ":" + p[2:]
    return os.path.normcase(os.path.normpath(p))


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
    temps = {tempfile.gettempdir(), "/tmp", "/var/tmp", os.environ.get("TMP", ""), os.environ.get("TEMP", "")}
    return not _inside(path, cwd) and not any(t and _inside(path, t) for t in temps)


def watch_marks(a, cwd: str) -> list[str]:
    """The watch-list marks of one step (see the module notes)."""
    out = []
    tool, full = (a.tool or "").lower(), a.target or ""
    t = _unquote(_shell_part(full)) if a.kind == "command" else full
    if a.kind == "command" and (inner := _inner_cmds(_shell_part(full))):
        t += "".join("\n" + _unquote(_shell_part(i)) for i in inner)      # bash -c "...": the inner command, on its own line
    if a.kind in ("edit", "write") and any(_outside(p, cwd) for p in full.splitlines()):
        out.append("outside_project")
    if a.kind == "web" or tool.startswith("mcp__") or tool in _OUTWARD_TOOLS or (a.kind == "command" and _NET_CMD.search(t)):
        out.append("network")
    if a.kind == "command" and _INLINE.search(full):
        out.append("inline_script")
    if a.kind == "command":                               # a part that opens or sends the file, not one that lists it
        hit = not _NAMES_ONLY.fullmatch(t.strip()) and (      # names only, no values: not a secret read
            any(_SECRET.search(p) and not _LOOK_ONLY.search(p) for p in _parts(_no_prose(t))) or
            any(_quoted_secret(p) for p in _parts(t)) or
            bool(_INLINE.search(full) and _RUNS_BODY.search(t) and _reads_secret(full)))
    else:
        hit = a.kind != "search" and bool(_SECRET.search(t))      # a search pattern that mentions .env reads no secret
        hit = hit or (tool in ("grep", "grep_search") and bool(_SECRET.search(" " + str(getattr(a, "paths", "") or ""))))   # searching inside one does
    if hit or ((a.kind in ("command", "web") or tool.startswith("mcp__")) and _SECRET_VALUE.search(full)):   # a key sent out
        out.append("secrets")
    if tool.startswith("mcp__"):
        out += _mcp_marks(tool.split("__")[-1], full, cwd)
    if a.kind == "command" and (_DELETE.search(t) or _moves_out(t, cwd) or
                                (_DB_CLIENT.search(t) and (_SQL_DESTROY.search(full) or _DB_WIPE.search(full)))):
        out.append("deletes")
    if a.kind == "command" and _PUSH.search(t):
        out.append("git_push")
    if a.kind == "command" and _INSTALL.search(t):
        out.append("installs")
    if (a.kind in ("edit", "write") and any(_GUARD.search(p.strip()) or _MEMORY.search(p.strip()) or _rules_file(p.strip(), cwd)
                                            for p in full.splitlines())) or \
            (a.kind == "command" and (_GUARD_IN_CMD.search(t) or _rules_file(t, cwd)) and _WRITES.search(_QUIET.sub(" ", t))):
        out.append("guardrails")
    if a.kind == "command":
        for mark, rx, text in (("pipe_to_shell", _PIPE_SHELL, t), ("encoded_command", _ENCODED, full), ("env_dump", _ENV_DUMP, _QUOTED.sub("''", t)),
                               ("force_push", _FORCE, _no_prose(t)), ("persistence", _PERSIST, t), ("security_off", _SEC_OFF, t),
                               ("wipe", _WIPE, t), ("privilege", _PRIVILEGE, t)):
            if rx.search(text):
                out.append(mark)
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
        if _direct(t):
            out.append("skips_commit_checks")
    elif a.kind in ("edit", "write") and any(_RC_FILE.search(p.strip()) for p in full.splitlines()):
        out.append("persistence")
    if {"injection", "hidden_text"} & set(getattr(a, "result_flags", None) or []):
        out.append("injected")
    if "secret_value" in (getattr(a, "result_flags", None) or []):
        out.append("key_in_output")
    if _tampers(a, full, t):
        out.append("guard_tamper")
    return list(dict.fromkeys(out))


def _tampers(a, full: str, shell: str) -> bool:
    """A step that would change the guard or the rules you set (see guard_tamper in the module notes)."""
    if a.kind in ("edit", "write"):
        return any(_TAMPER_PATH.search(p.strip()) for p in full.splitlines())
    if a.kind == "command":
        if _TAMPER_CMD.search(_no_prose(full)):
            return True
        bare = _no_prose(shell)
        parts = [p for p in re.split(r"&&|\|\||;|\n", bare) if p.strip()]
        if any(_OWN_PATH.search(p) and not _READ_ONLY.search(p) for p in parts):
            return True                       # the guard's folder and your allow file: read them, nothing else
        if _INLINE.search(full) and _OWN_PATH.search(full.replace("~/", " ~/")):
            return True                       # a script written into the command can do anything with them
        # an installed plugin: running its scripts is how skills work; a write, move or delete aimed at its files is not
        return any(_PLUGINS_PATH.search(p) and (_DELETE.search(p) or _TO_PLUGINS.search(_QUIET.sub(" ", p))) for p in parts)
    if (a.tool or "").startswith("mcp__"):
        return bool(_TAMPER_PATH.search(full) or _TAMPER_CMD.search(full))
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
    """A step that brought content the user did not write into the chat: the web, an MCP or network result, a file
    from outside the project, any result showing signs of an injection; strict: also a file from a folder of other
    people's code (node_modules, vendor, site-packages…)."""
    if {"injection", "hidden_text"} & set(getattr(a, "result_flags", None) or []) or "injected" in a.risks:
        return True
    if "network" in a.risks and (a.tool or "").lower() not in _SEND_ONLY:      # it brought something back
        return True
    paths = (a.target or "").splitlines()
    return a.kind == "read" and any(_outside(p, cwd) or (strict and _VENDOR.search(p)) for p in paths)


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
    """The project's expected steps: (mark, pattern) from .sessioncost-allow in the project folder."""
    f = os.path.join(cwd, ALLOW_FILE) if cwd else ""
    if not f or not os.path.isfile(f):
        return []
    out = []
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split("#", 1)[0].split(None, 1)
                if len(parts) == 2 and (parts[0] in SEVERITY or parts[0] == "step"):   # step: allowed in allow-list mode
                    out.append((parts[0], parts[1].strip()))
    except OSError:
        return []
    return out


def _slashes(p: str) -> str:
    home = os.path.expanduser("~").replace("\\", "/")
    p = p.replace("\\", "/")
    if home and p.lower().startswith(home.lower()):
        p = "~" + p[len(home):]
    return p.lower() if os.name == "nt" else p


def _allowed(a, mark: str, rules: list[tuple[str, str]]) -> bool:
    things = [_slashes(x.strip()) for x in (a.target or "").splitlines() if x.strip()] + [_slashes(a.target or "").strip()]
    things.append((a.tool or "").lower() if os.name == "nt" else a.tool or "")
    for m, pat in rules:
        if m == mark:
            p = _slashes(os.path.expanduser(pat))        # ~ and the home folder are the same thing on both sides
            if any(fnmatch.fnmatchcase(x, p) for x in things if x):
                return True
    return False


def mark_watch(s: Session) -> None:
    """Add the watch-list marks to every step of the main chat and the helpers, move the ones the project expects
    (.sessioncost-allow) to `allowed`, then mark the risky steps taken after untrusted content in the same turn."""
    rules = read_allow(s.cwd)
    for c in s.all_calls:
        for a in c.actions:
            for m in watch_marks(a, s.cwd):
                if m in a.risks or m in a.allowed:
                    continue
                (a.allowed if _allowed(a, m, rules) else a.risks).append(m)
    for t in s.turns:                       # the main chat, turn by turn: your message is the checkpoint
        source = None
        for c in t.calls:
            for a in c.actions:
                if source and "after_untrusted" not in a.risks and (_SINKS & set(a.risks) or _sends_out(a)):
                    if _allowed(a, "after_untrusted", rules):
                        a.allowed.append("after_untrusted")
                    else:
                        a.risks.append("after_untrusted")
                        a.chain_from = source
            if source is None and any(_untrusted_in(a, s.cwd) for a in c.actions):
                source = (c.agent, c.n)
