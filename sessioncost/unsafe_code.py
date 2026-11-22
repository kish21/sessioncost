"""Unsafe code the agent writes: a tip, never a pop-up (1.2.4, owner 2026-11-21).

After the agent writes or edits a code file, the new text is checked for a few well-known weaknesses. Each finding is
one plain line: what the code does, the CWE weakness (MITRE's catalogue of software weaknesses) and the good practice.
Nothing is blocked or asked. Only patterns that are rarely fine are listed, so the tip stays rare. Test files are
skipped (fake keys and switched-off checks are normal there). Each weakness is named once per file per chat.

Where the tip appears: Claude Code at once (its after-step hook can add a line); Codex, Copilot and Antigravity at the
user's next message, the same way token tips arrive (guard_auto.TIP_EVENTS). Cursor has no hook after a file edit,
so it gets none."""
from __future__ import annotations

import re
from pathlib import PurePath

from .guard_store import _read, _safe_id, _write, home

CODE = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".java", ".kt", ".cs", ".php", ".rb", ".rs",
        ".swift", ".sh", ".bash", ".zsh", ".ps1", ".vue", ".svelte"}
_TEST = re.compile(r"(?:^|[\\/])(?:tests?|__tests__|spec|fixtures?)[\\/]|(?:^|[\\/])test_[^\\/]*$|"
                   r"[._-](?:test|spec)\.[a-z]+$", re.I)
_SQL = r"(?:SELECT|INSERT|UPDATE|DELETE)\b"

# (CWE, its name, what this code does, the good practice, the pattern)
CHECKS = [
    ("CWE-295", "improper certificate validation", "turns off the HTTPS certificate check",
     "keep the check on; for your own certificate, point the check at that certificate file",
     re.compile(r"\bverify\s*=\s*False\b|\brejectUnauthorized\s*:\s*false\b|\bInsecureSkipVerify\s*:\s*true\b|"
                r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0|\bssl\._create_unverified_context\b|"
                r"\bcurl\s+(?:[^\n|;&]*\s)?(?:-k|--insecure)\b")),
    ("CWE-89", "SQL injection", "builds an SQL query by pasting values into its text",
     "pass the values as query parameters (placeholders like ? or %s), never inside the query text",
     re.compile(r"\.(?:execute|executemany|query|raw)\(\s*(?:f\"\s*" + _SQL + r"[^\"\n]*\{|f'\s*" + _SQL + r"[^'\n]*\{|"
                r"\"\s*" + _SQL + r"[^\"\n]*\"\s*(?:\+|%\s*[\w(])|'\s*" + _SQL + r"[^'\n]*'\s*(?:\+|%\s*[\w(])|"
                r"`\s*" + _SQL + r"[^`]*\$\{)", re.I)),
    ("CWE-78", "OS command injection", "runs a shell command built from text that can change",
     "pass the program and its arguments as a list without a shell (shell=False), and never paste input into a command",
     re.compile(r"\bos\.system\(\s*(?:f[\"']|[^)\n]*\+)|\bsubprocess\.\w+\(\s*(?:f[\"']|[^,)\n]*(?:\+|\.format\())"
                r"[^)\n]*shell\s*=\s*True|\b(?:child_process\.)?execSync?\(\s*`[^`]*\$\{")),
    ("CWE-94", "code injection", "runs text as code",
     "never eval or exec text that comes from a user, a file or the web; parse it (json.loads, int()) instead",
     re.compile(r"\b(?:eval|exec)\(\s*(?:request\b|req\.|input\(|sys\.argv|params\b|process\.argv)")),
    ("CWE-502", "deserialization of untrusted data", "loads YAML in a way that can create any object",
     "use yaml.safe_load (or Loader=yaml.SafeLoader)",
     re.compile(r"\byaml\.load\((?![^)\n]*Loader\s*=\s*(?:yaml\.)?C?SafeLoader)")),
    ("CWE-79", "cross-site scripting", "puts text into a web page as HTML",
     "set textContent (or let the framework escape it); if it must be HTML, clean it with a sanitizer first",
     re.compile(r"\.innerHTML\s*=(?!=)(?!\s*([\"'`])[^\"'`$]*\1\s*;?\s*$)|\bdangerouslySetInnerHTML\b|"
                r"\bdocument\.write\(\s*[^\"'`)]", re.M)),
]
KEY = ("CWE-798", "hard-coded credentials", "puts a secret key into the code",
       "read it from an environment variable or a secret store, and replace the key if it was a real one")


def is_code(path: str) -> bool:
    return PurePath(path or "").suffix.lower() in CODE and not _TEST.search(path or "")


def findings(path: str, written: str) -> list[tuple[str, str, str, str]]:
    """(CWE, name, what it does, good practice) for each weakness in the new text of a code file."""
    if not written or not is_code(path):
        return []
    out = [(c, n, w, g) for c, n, w, g, p in CHECKS if p.search(written)]
    from .risk_patterns import real_key
    if real_key(written):
        out.append(KEY)
    return out


def edits(inp: dict) -> list[tuple[str, str]]:
    """(file, its new text) for a write or edit step; a Codex patch gives each file's added lines."""
    path = str(inp.get("file_path") or inp.get("path") or "")
    texts = [inp.get(k) for k in ("content", "new_string", "new_source")]
    texts += [e.get("new_string") for e in inp.get("edits") or [] if isinstance(e, dict)]
    text = "\n".join(t for t in texts if isinstance(t, str))
    if "*** Begin Patch" not in text:
        return [(path, text)] if path and text else []
    out = []
    for name, body in re.findall(r"^\*\*\* (?:Add|Update) File: (.+?)\s*$\n(.*?)(?=^\*\*\* |\Z)", text, re.M | re.S):
        added = "\n".join(ln[1:] for ln in body.splitlines() if ln.startswith("+"))
        if added:
            out.append((name, added))
    return out


def line(path: str, f: tuple[str, str, str, str]) -> str:
    cwe, name, what, good = f
    return f"💡 SessionCost: {PurePath(path).name} {what} ({cwe}, {name}). Good practice: {good}."


def new_tips(st: dict, path: str, written: str) -> list[str]:
    """The tips for this edit, each weakness once per file in this chat (remembered in the chat's state)."""
    seen = st.setdefault("code_tips", [])
    out = []
    for f in findings(path, written):
        key = f"{f[0]} {path}"
        if key not in seen:
            seen.append(key)
            out.append(line(path, f))
    return out


SAY = ("Tell the user this at the end of your answer, as a quote block (begin it with \"> \"), and change the code only "
       "if they ask: ")


def keep(sid: str, tips: list[str]) -> None:
    """For agents that cannot show it now: said at the user's next message."""
    p, k = home() / "code_tips.json", _safe_id(sid)
    data = _read(p, {})
    data = data if isinstance(data, dict) else {}
    data[k] = (data.get(k) or []) + tips
    _write(p, dict(list(data.items())[-50:]))


def take(sid: str) -> str:
    p, k = home() / "code_tips.json", _safe_id(sid)
    data = _read(p, {})
    tips = data.pop(k, None) if isinstance(data, dict) else None
    if not tips:
        return ""
    _write(p, data)
    return "SessionCost found unsafe code you wrote earlier in this chat. " + SAY + " ".join(tips)
