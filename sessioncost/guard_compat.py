"""Agent version self-check and unreadable-hook health tracking (#35).

Records the version of each supported agent the guard was last tested on, warns when an installed agent is
newer, and tracks when an agent's hook ran but sent a payload the guard could not parse as a tool step.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .guard_store import _read, _write, home as guard_home

# Last tested version per agent key: (human label, tested version).
TESTED_VERSIONS: dict[str, tuple[str, str]] = {
    "claude": ("Claude Code", "2.1.100"),
    "codex": ("Codex", "0.120.0"),
    "cursor": ("Cursor", "1.1.0"),
    "copilot": ("Copilot", "1.400.0"),
    "antigravity": ("Antigravity", "1.20.0"),
}

_AGENT_FOLDERS: dict[str, str] = {
    "claude": ".claude",
    "codex": ".codex",
    "cursor": ".cursor",
    "copilot": ".copilot",
    "antigravity": ".gemini",
}

_CLI_NAMES: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "copilot": "copilot",
    "antigravity": "antigravity",
}

_VER_RE = re.compile(r"\b(\d+(?:\.\d+)+)\b")
HEALTH_FILE = "hook_health.json"
HEALTH_MAX_AGE_S = 7 * 86400


def parse_version(text: str) -> str:
    """Extract the first numeric dotted version (e.g. '2.1.83') from text, or '' if none."""
    m = _VER_RE.search(text or "")
    return m.group(1) if m else ""


def ver_tuple(v: str) -> tuple[int, ...]:
    s = parse_version(v)
    return tuple(int(p) for p in s.split(".")) if s else ()


def is_newer(installed: str, tested: str) -> bool:
    """True when installed version is strictly newer than tested version."""
    a, b = ver_tuple(installed), ver_tuple(tested)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def _run_version(cmd: str) -> str:
    exe = shutil.which(cmd)
    if not exe:
        return ""
    try:
        r = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=1.5,
            stdin=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return parse_version((r.stdout or "") + "\n" + (r.stderr or ""))


def _local_version(agent: str, h: Path) -> str:
    """Fast file-only version check inside the user's home folder."""
    if agent == "copilot":
        best = ""
        for ext_root in (".vscode", ".vscode-insiders"):
            ext_dir = h / ext_root / "extensions"
            if not ext_dir.is_dir():
                continue
            for p in ext_dir.glob("github.copilot-chat-*/package.json"):
                try:
                    v = parse_version(str(json.loads(p.read_text(encoding="utf-8")).get("version") or ""))
                except (OSError, ValueError, AttributeError):
                    v = parse_version(p.parent.name)
                if v and (not best or is_newer(v, best)):
                    best = v
        return best
    if agent == "antigravity":
        for p in (h / ".gemini" / "antigravity" / "version", h / ".gemini" / "version"):
            try:
                if p.is_file():
                    if v := parse_version(p.read_text(encoding="utf-8", errors="replace")):
                        return v
            except OSError:
                pass
    return ""


def detect_versions(home: Path | None = None, *, probe_cli: bool | None = None) -> dict[str, str]:
    """Return {agent_key: installed_version} for detected agents where a version can be read locally."""
    from .cli_setup import agent_installed
    h = home or Path.home()
    if probe_cli is None:
        probe_cli = home is None and not os.environ.get("SESSIONCOST_GUARD_DIR")
    found: dict[str, str] = {}
    for agent, folder in _AGENT_FOLDERS.items():
        if not agent_installed(folder, h):
            continue
        ver = _local_version(agent, h)
        if not ver and probe_cli:
            ver = _run_version(_CLI_NAMES[agent])
        if ver:
            found[agent] = ver
    return found


def version_warnings(versions: dict[str, str] | None = None, home: Path | None = None) -> list[str]:
    """One-line warnings for installed agents newer than TESTED_VERSIONS."""
    seen = versions if versions is not None else detect_versions(home=home)
    out: list[str] = []
    for agent, (label, tested) in TESTED_VERSIONS.items():
        installed = seen.get(agent) or ""
        if installed and is_newer(installed, tested):
            out.append(f"{label} updated to {installed}; the guard is not yet tested on it (last tested: {tested}).")
    return out


def _health_path() -> Path:
    return guard_home() / HEALTH_FILE


def record_unreadable(agent: str, event: str, reason: str) -> None:
    """Record that an agent's hook ran but sent a message the guard could not read."""
    try:
        p = _health_path()
        data = _read(p, {})
        if not isinstance(data, dict):
            data = {}
        prev = data.get(agent) if isinstance(data.get(agent), dict) else {}
        data[agent] = {
            "at": time.time(),
            "event": str(event or "unknown"),
            "reason": str(reason or "unreadable hook payload"),
            "count": int(prev.get("count") or 0) + 1,
        }
        _write(p, data)
    except OSError:
        pass


def clear_unreadable(agent: str) -> None:
    """Clear an unreadable-hook warning once a valid step from that agent is read."""
    try:
        p = _health_path()
        if not p.exists():
            return
        data = _read(p, {})
        if isinstance(data, dict) and agent in data:
            del data[agent]
            _write(p, data)
    except OSError:
        pass


def hook_warnings(max_age_s: float = HEALTH_MAX_AGE_S) -> list[str]:
    """One-line warnings for agents whose hooks ran recently but sent unreadable steps."""
    p = _health_path()
    if not p.exists():
        return []
    data = _read(p, {})
    if not isinstance(data, dict):
        return []
    now = time.time()
    out: list[str] = []
    for agent, info in data.items():
        if not isinstance(info, dict):
            continue
        at = float(info.get("at") or 0)
        if max_age_s > 0 and now - at > max_age_s:
            continue
        label = TESTED_VERSIONS.get(agent, (agent.capitalize(), ""))[0]
        ev = str(info.get("event") or "hook")
        out.append(f"{label} hooks ran ({ev}), but the guard could not read the step (hook format may have changed).")
    return out


def compat_warnings(versions: dict[str, str] | None = None, home: Path | None = None) -> list[str]:
    """All compatibility warnings: newer untested agent versions + unreadable hook payloads."""
    return [*version_warnings(versions=versions, home=home), *hook_warnings()]
