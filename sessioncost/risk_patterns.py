"""The watch list's patterns (see risks.py for what each mark means): what a command, a path or a written file is
matched against, and the small checks that sit with them."""
from __future__ import annotations

import os
import re

from .model import Session



WATCH = ("guard_tamper", "wipe", "pipe_to_shell", "encoded_command", "env_dump", "persistence", "security_off", "force_push", "privilege",
         "injected", "after_untrusted", "poisoned_tool", "key_in_output", "guardrails", "mcp_action", "secrets", "secret_commit", "skips_commit_checks",
         "git_push", "deletes", "installs", "new_package", "repeat_fail", "outside_project", "big_read", "network", "inline_script")
SEVERITY = {"guard_tamper": "high", "wipe": "high", "privilege": "high", "pipe_to_shell": "high", "encoded_command": "high", "env_dump": "high", "persistence": "high",
            "security_off": "high", "force_push": "high", "injected": "high", "poisoned_tool": "high", "key_in_output": "high", "mcp_action": "review",
            "after_untrusted": "high", "guardrails": "high", "secrets": "high", "secret_commit": "high", "skips_commit_checks": "high",
            "git_push": "review", "deletes": "review", "installs": "review", "new_package": "review", "repeat_fail": "review",
            "outside_project": "review", "big_read": "review",
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
                     r"(?:^|[\\/\s'\"`=@~])\.(?:npmrc|pypirc|netrc)\b|\bsecrets?\.(?:json|ya?ml|toml|env)\b|"
                     # the other places tools keep logins (2026-11-01): git, GitHub CLI, Docker, Kubernetes, Azure,
                     # Google Cloud, Terraform state, a browser's saved cookies and passwords
                     r"(?:^|[\\/\s'\"`=@~])\.(?:git-credentials|kube[\\/]config|docker[\\/]config\.json)\b|"
                     r"[\\/]gh[\\/]hosts\.ya?ml\b|[\\/]\.azure[\\/](?:accessTokens\.json|msal_token_cache)|"
                     r"\bapplication_default_credentials\.json\b|[\\/]gcloud[\\/](?:credentials|access_tokens)\.db\b|"
                     r"\.tfstate(?:\.backup)?\b|[\\/](?:Cookies|Login Data|cookies\.sqlite|logins\.json|key4\.db)['\"]?(?:$|[\s'\"`;|&)])|"
                     # the AI agents' own logins (2026-11-06): Codex, Gemini CLI / Antigravity
                     r"[\\/]\.codex[\\/]auth\.json\b|[\\/]\.gemini[\\/]oauth_creds\.json\b",
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
_TAMPER_CMD = re.compile(r"\bguard(?:\.py)?[\"']?\s+(?:off|remove|allow(?:-\w+)?)\b|\bSESSIONCOST_GUARD_DIR\b|\bdisableAllHooks\b|"
                         r"^\s*hooks\s*=\s*false\b", re.I | re.M)      # (the last: Codex's [features] switch)
# Written into a file: the switch-off words anywhere, and SESSIONCOST_GUARD_DIR only where the hooks read their
# environment from (settings, shell start-up and .env files) or made permanent (setx). A test or script that hands
# its own child process another guard folder cannot reach the real guard.
_TAMPER_WRITE = re.compile(r"\bguard(?:\.py)?[\"']?\s+(?:off|remove|allow(?:-\w+)?)\b|\bdisableAllHooks\b|^\s*hooks\s*=\s*false\b|"
                           r"\b(?:setx|SetEnvironmentVariable)\b[^\n]*\bSESSIONCOST_GUARD_DIR\b", re.I | re.M)
_ENV_FILE = re.compile(r"(?:^|[\\/])(?:\.env(?:\.[^\\/]*)?|\.(?:bash|zsh)rc|\.(?:bash_)?profile|\.zprofile|\.zshenv|"
                       r"[^\\/]*profile\.ps1|settings(?:\.local)?\.json|hooks\.json|config\.toml|environment)$", re.I)


def tamper_write(target: str, written: str) -> bool:
    return bool(_TAMPER_WRITE.search(written) or
                (_ENV_FILE.search(target or "") and re.search(r"\bSESSIONCOST_GUARD_DIR\b", written)))
_READ_ONLY = re.compile(r"^\s*(?:cat|type|Get-Content|gc|head|tail|less|more|grep|rg|findstr|Select-String|ls|dir|"
                        r"cd|pushd|Set-Location|sl|jq|"
                        r"Get-ChildItem|wc|stat|file|python3?\s+-m\s+json\.tool)\b[^>|;&]*$", re.I)
_SINKS = {"guard_tamper", "wipe", "privilege", "guardrails", "secrets", "skips_commit_checks", "git_push", "deletes", "installs", "force_push",
          "pipe_to_shell", "encoded_command", "env_dump", "persistence", "security_off"}
_ENV_DUMP = re.compile(r"(?:^|[;&|(]\s*)(?:printenv|env|set|export\s+-p)\s*(?:$|[;&|>])|\b(?:Get-ChildItem|gci|Get-Item|gi|dir|ls)\s+env:(?![A-Za-z_])|"
                       r"\[Environment\]::GetEnvironmentVariables|/proc/[\w*]+/environ", re.I | re.M)
# python -c / -m and node -e / -p take their code from the line and read the download only as data (a JSON check)
_RUNS_STDIN = (r"(?:sh|bash|zsh|dash|iex|Invoke-Expression|pwsh|powershell|"
               r"(?:python3?|node)(?![\w.-])(?!(?:\s+-[A-Za-z]+)*?\s+-[cmep]\b))\b")
_PIPE_SHELL = re.compile(r"\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n;]*\|\s*(?:sudo\s+)?" + _RUNS_STDIN + "|"
                         r"\b(?:iex|Invoke-Expression)\b[^\n;]*\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|DownloadString)\b|"
                         r"\b(?:ba|z)?sh\s+<\(\s*(?:curl|wget)\b|"
                         r"https?://[^\n;|]*\|\s*(?:sudo\s+)?" + _RUNS_STDIN,
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

def _norm(p: str) -> str:
    p = os.path.expanduser(p.strip().strip("'\"`"))
    if re.match(r"^/[a-zA-Z]/", p):                      # Git Bash: /c/Users/... is C:\Users\...
        p = p[1] + ":" + p[2:]
    return os.path.normcase(os.path.normpath(p))
