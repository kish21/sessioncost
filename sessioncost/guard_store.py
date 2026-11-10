"""The guard's files: its folder, each chat's state, and the audit log (one JSON line per decision, chained by
SHA-256 so a changed or removed line shows)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path


if os.name == "nt":
    import msvcrt
else:
    import fcntl

def _default_home() -> Path:
    return Path(os.environ.get("SESSIONCOST_GUARD_DIR") or Path.home() / ".sessioncost" / "guard")


def home() -> Path:
    """The guard's folder, looked up on guard.py at each call: tools/guard_check.py points guard.home at a folder of
    its own, and every guard module must follow it there."""
    from . import guard
    return guard.home()


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def guard_on(cfg: dict) -> bool:
    """The guard is on for every chat unless you turned it off."""
    return cfg.get("on", True) is not False


def fix_auto_on(cfg: dict) -> bool:
    """The to-do list after each answer is on unless you turned it off."""
    return cfg.get("fix_auto", True) is not False


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


def _safe_id(sid: str) -> str:
    return re.sub(r"[^\w.-]", "_", str(sid or "unknown"))[:80]


def state_path(sid: str) -> Path:
    return home() / f"{_safe_id(sid)}.state.json"


def log_path(sid: str) -> Path:
    return home() / f"{_safe_id(sid)}.jsonl"


def project_allow_path(cwd: str) -> Path:
    raw = os.path.normcase(os.path.normpath(str(cwd or ".")))
    name = re.sub(r"[^\w.-]", "_", Path(str(cwd or ".")).name or "root")[:40]
    return home() / "projects" / f"{name}-{hashlib.sha1(raw.encode()).hexdigest()[:8]}.json"



def _same_dir(a: str, b: str) -> bool:
    n = lambda p: os.path.normcase(os.path.normpath(str(p or "")))
    return bool(a and b) and n(a) == n(b)


# ---- the audit log: one JSON line per decision, each carrying the hash of the line before ----
def _last_line(fh) -> bytes:
    """The file's last line, however long, with its line break if it has one."""
    pos = fh.seek(0, 2)
    tail = b""
    while pos > 0 and b"\n" not in tail[:-1]:
        step = min(65536, pos)
        pos -= step
        fh.seek(pos)
        tail = fh.read(step) + tail
    return tail[tail.rfind(b"\n", 0, max(0, len(tail) - 1)) + 1:]


def _prev_hash(p: Path) -> tuple[str, bool]:
    """(the last entry's hash, whether the file ends with a line break). The zero hash for a new or empty log;
    "broken" when the last line is not a whole entry, so the next one never claims to continue a good chain."""
    try:
        with open(p, "rb") as fh:
            last = _last_line(fh)
    except FileNotFoundError:
        return "0" * 64, True
    except OSError:
        return "broken", True
    if not last.strip():
        return "0" * 64, True
    try:
        h = json.loads(last).get("h")
    except (ValueError, AttributeError):
        h = None
    return (h if isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) else "broken"), last.endswith(b"\n")


LOCK_WAIT = 2.0                                 # seconds one hook waits for another hook of the same chat


@contextlib.contextmanager
def _locked(p: Path):
    """Hold <log>.lock while one entry is read and written, so two hooks of one chat at once cannot fork the chain;
    other chats have their own lock. Yields False when the lock could not be had in time. The system lets go of the
    lock when a process dies holding it."""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield False
        return
    got, end = False, time.monotonic() + LOCK_WAIT
    try:
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.monotonic() > end:
                    break
                time.sleep(0.01)
        yield got
    finally:
        if got:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def append_log(sid: str, entry: dict) -> None:
    p = log_path(sid)
    with _locked(p) as got:
        prev, whole = _prev_hash(p)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry, "prev": prev, **({} if got else {"unlocked": True})}
        entry["h"] = hashlib.sha256((prev + json.dumps(entry, sort_keys=True)).encode("utf-8")).hexdigest()
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(("" if whole else "\n") + json.dumps(entry) + "\n")   # a cut-off last line stays on its own


def verify_status(p: Path) -> dict:
    """{"status", "entries", "error"}. status: intact (every line's hash matches its content and the line before it),
    empty, missing, unreadable, corrupted (a line edited, removed or not an entry) or incomplete (a write was cut off:
    the last line, or a line the guard's next entry marks as "broken"). error names the first bad line, never its
    content."""
    try:
        text = Path(p).read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return {"status": "missing", "entries": [], "error": None}
    except OSError as e:
        return {"status": "unreadable", "entries": [], "error": type(e).__name__}
    if not text.strip():
        return {"status": "empty", "entries": [], "error": None}
    lines = text.splitlines()
    entries, prev, status, error = [], "0" * 64, "intact", None
    for i, line in enumerate(lines, 1):
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                raise ValueError
        except ValueError:
            if status == "intact":
                cut = (i == len(lines) and not text.endswith("\n")) or (i < len(lines) and '"prev": "broken"' in lines[i])
                status, error = ("incomplete", f"line {i} was cut off") if cut else ("corrupted", f"line {i} is not an entry")
            prev = "broken"                           # what the guard's next entry says it follows
            continue
        h = e.pop("h", "")
        if e.get("prev") != prev or hashlib.sha256((prev + json.dumps(e, sort_keys=True)).encode("utf-8")).hexdigest() != h:
            if status == "intact":
                status, error = "corrupted", f"line {i} was changed, or a line before it removed"
        prev = h
        entries.append(e)
    return {"status": status, "entries": entries, "error": error}


def verify_log(p: Path) -> tuple[bool, list[dict]]:
    """(intact, entries): intact only when the log was read and every line checks out (an empty log has nothing
    edited); a missing, unreadable, corrupted or cut-off log is not. verify_status says which."""
    v = verify_status(p)
    return v["status"] in ("intact", "empty"), v["entries"]
