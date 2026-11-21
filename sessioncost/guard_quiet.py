"""When the guard stays quiet (owner 2026-11-18: "no needless pop-up"). A replay of 597 saved chats found ~80% of
asks needless (docs/guard-popup-replay.md). The guard now asks only for threats the agent itself misses:

- push, install, delete, a web page, an MCP action: recorded, not asked, unless a threat sign is on the step
  (outside content came in this turn, a key is in the chat, a push to a web address that is not a remote);
- the agent's rules and memory: changed freely, unless outside content came in or the edit weakens a check;
- a new package: asked only when its name looks like a misspelling of a popular one;
- the user's own typed message allows the step it names, for that turn (never pasted text, never a tool result).
"""
from __future__ import annotations

import json
import os
import re

RECORD = {"network", "git_push", "deletes", "installs", "mcp_action"}

# what the user's own words name; a "no" in front ("do not push", "don't install") is not a yes
_NAMES = {"git_push": r"\bpush", "installs": r"\b(?:install|pip|npm|uv add|pnpm|yarn add)\b",
          "deletes": r"\b(?:delete|remove|rm|clean ?up|clear out)\b",
          "guardrails": r"claude\.md|agents\.md|gemini\.md|\bmemory\b|\bremember\b|\brules?\b|\bsettings\b|\bhooks?\b|"
                        r"\bci\b|workflow|pre-commit|copilot-instructions",
          "mcp_action": r"\b(?:send|post|publish|create|update|comment|reply)\b",
          "search": r"\b(?:search|look (?:it )?up|google)\b"}
_NO = re.compile(r"\b(?:don'?t|do not|never|no|not|without|stop)\s+(?:\w+\s+){0,2}$", re.I)
_PASTED = re.compile(r"<(pasted_content|system-reminder|command-\w+)\b[^>]*>.*?</\1>", re.S | re.I)
_URL_HOST = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:[/:\s]|$)", re.I)


def said(prompt: str) -> list[str]:
    """The kinds of step the user's own message asks for, and the sites it names ("site:<host>")."""
    own = _PASTED.sub(" ", prompt or "")
    out = []
    for kind, rx in _NAMES.items():
        if any(not _NO.search(own[:m.start()][-40:]) for m in re.finditer(rx, own, re.I)):
            out.append(kind)
    out += [f"site:{h.lower()}" for h in _URL_HOST.findall(own) if "." in h and not h.lower().endswith((".md", ".py", ".txt"))]
    return sorted(set(out))


def covered(mark: str, a, kinds: set[str]) -> bool:
    """The user's own message this turn named this step."""
    if mark == "network":
        from .guard_decide import _host
        if a.kind == "web" and not re.match(r"https?://", a.target or ""):
            return "search" in kinds                        # a web search: "search for ..."
        hosts = {_host(u) for u in re.findall(r"https?://\S+", a.target or "")}
        return bool(hosts) and all(f"site:{h}" in kinds or any(h.endswith("." + k[5:]) for k in kinds if k.startswith("site:"))
                                   for h in hosts)
    return mark in kinds


# ---- steps that cannot be undone: asked even with no threat sign (owner 2026-11-18) ----
_CANNOT_UNDO = re.compile(
    r"\bgit\s+(?:checkout\s+(?:HEAD\s+)?(?:--(?:\s|$)|\.(?:\s|$))|restore\b|reset\s+--hard|stash\s+(?:drop|clear)|clean\s+-\w*f)|"   # uncommitted work
    r"\bredis-cli\b[^\n]*\bFLUSH(?:ALL|DB)\b|\bterraform\s+(?:destroy|apply\b[^\n|;&]*-destroy)|\bkubectl\s+delete\b|"   # data and servers
    r"\bhelm\s+(?:uninstall|delete)\b|\bdocker\s+(?:system\s+prune|volume\s+(?:rm|prune))\b", re.I)
# (docker rm: a container is made again by the next run; its volumes stay, owner 2026-11-19)
_SENT_OUT = re.compile(
    r"\b(?:curl|wget)\b[^\n|;&]*\s(?:-d|--data\S*|-F|--form|-T|--upload-file)\b|"                           # sent out
    r"\bInvoke-(?:WebRequest|RestMethod)\b[^\n|;&]*\s-(?:Body|InFile)\b|\bSend-MailMessage\b|\bsendmail\b|\bmail\s+-s\b|"
    r"\b(?:nslookup|dig|Resolve-DnsName)\b", re.I)
_LOCAL_URL = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(?:[:/]|$)", re.I)


def cannot_undo(a, marks: list[str]) -> bool:
    """Throws away uncommitted work, wipes data or servers, or sends project data to a site (not to this computer:
    a local dev server is the user's own)."""
    from .risk_patterns import _DB_CLIENT, _DB_WIPE, _SQL_DESTROY
    t = a.target or ""
    if (a.tool or "").startswith("mcp__"):
        return "deletes" in marks                         # an MCP tool dropping or deleting data
    if a.kind != "command":
        return False
    urls = re.findall(r"https?://[^\s'\"]+", t)
    sent = bool(_SENT_OUT.search(t)) and not (urls and all(_LOCAL_URL.match(u) for u in urls))
    return bool(_CANNOT_UNDO.search(t) or sent or (_DB_CLIENT.search(t) and (_SQL_DESTROY.search(t) or _DB_WIPE.search(t))))


# ---- an edit that weakens a check ----
_CHECK = re.compile(r"--no-verify|\bnever\b|\bmust\b|\bdo not\b|\bdon'?t\b|\btests?\b|\bpytest\b|\blint\w*|\bchecks?\b|"
                    r"\bverify\b|\bhooks?\b|pre-commit|\bdeny\b|\bblock\w*|\brequired?\b|\breview\b|\bask\b", re.I)
_OFF = re.compile(r"disableAllHooks|--no-verify|continue-on-error:\s*true|\|\|\s*true\b|\bSKIP=|HUSKY=0|bypassPermissions|"
                  r"dangerously|\"allow\"\s*:\s*\[[^\]]*\"(?:Bash\(\*\)|\*)\"", re.I)
_DOC = re.compile(r"\.(?:md|mdx|txt|rst)$", re.I)


def _texts(a) -> tuple[str, str]:
    """(old, new) text of an Edit / Write; old of a Write is the file on disk."""
    try:
        w = json.loads(getattr(a, "written", "") or "{}")
    except ValueError:
        w = {}
    edits = w.get("edits") if isinstance(w.get("edits"), list) else []
    new = "\n".join(str(x) for x in [w.get("new_string"), w.get("content"), w.get("new_source"),
                                      *[e.get("new_string") for e in edits if isinstance(e, dict)]] if x)
    old = getattr(a, "replaced", None) or ""
    if a.kind == "write" and not old:
        path = (a.target or "").strip()
        try:
            if os.path.isfile(path) and os.path.getsize(path) < 200_000:
                with open(path, encoding="utf-8", errors="replace") as f:
                    old = f.read()
        except OSError:
            old = ""
    return old, new


def weakens(a) -> bool:
    """An edit to the agent's rules or checks that drops a check (a "never", a test, a hook...) or switches one off."""
    if a.kind == "command":
        t = a.target or ""
        return bool(_OFF.search(t) or re.search(r"\bsed\b[^\n|;&]*/d['\"\s]|(?:^|[;&|]\s*):?\s*>\s*\S|\b(?:rm|del|Remove-Item)\b", t))
    if getattr(a, "written", "") is None:
        return False                          # tools/guard_replay.py: the text written is not in the log
    old, new = _texts(a)
    lost = {w.lower() for w in _CHECK.findall(old)} - {w.lower() for w in _CHECK.findall(new)}
    added_off = not _DOC.search((a.target or "").strip()) and bool(_OFF.search(new)) and not _OFF.search(old)
    return bool(lost) or added_off


# ---- a package name one letter off a popular one (attackers register such names) ----
POPULAR = {
    "py": "requests numpy pandas scipy matplotlib django flask fastapi pydantic sqlalchemy pytest boto3 botocore urllib3 "
          "setuptools certifi idna charset-normalizer six python-dateutil pyyaml jinja2 markupsafe click attrs packaging "
          "cryptography pillow tqdm rich httpx aiohttp uvicorn gunicorn celery redis psycopg2 pymongo openai anthropic "
          "tensorflow torch scikit-learn transformers beautifulsoup4 lxml selenium playwright black ruff mypy flake8 "
          "colorama tabulate toml tomli typer pyjwt paramiko docker kubernetes grpcio protobuf websockets langchain",
    "js": "react react-dom next vue express lodash axios moment dayjs chalk commander yargs typescript webpack vite "
          "eslint prettier jest mocha chai dotenv uuid debug cors body-parser mongoose redis pg mysql2 socket.io "
          "tailwindcss postcss autoprefixer babel-loader rxjs zod jsonwebtoken bcrypt nodemon ws classnames "
          "styled-components openai @anthropic-ai/sdk node-fetch cross-env rimraf glob minimist semver",
}
_POPULAR = {n for v in POPULAR.values() for n in v.split()}


def _one_off(a: str, b: str) -> bool:
    """One letter added, dropped, changed, or two side by side swapped."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def misspelled(names: list[str]) -> list[tuple[str, str]]:
    """(name, the popular name it is one letter off) for each likely misspelling."""
    out = []
    for raw in names:
        n = re.sub(r"(?<=.)@.*$", "", raw.lower())         # lodash@4, @scope/name@1: the version left out
        n = re.split(r"[=<>!~\[]", n, maxsplit=1)[0]        # requests==2.0, httpx[http2]
        if len(n) < 4 or n in _POPULAR:
            continue
        hit = next((p for p in sorted(_POPULAR) if _one_off(n, p)), None)
        if hit:
            out.append((raw, hit))
    return out
