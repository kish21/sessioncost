"""Local per-session summary cache for cross-session scans (`insights`, `week`, `totals`).

Caches compact per-session summaries in `~/.sessioncost/cache/session-summaries.json`
(or `$SESSIONCOST_DIR/cache/session-summaries.json`) keyed by:
  - canonical session path (or `cursor:<db>#<id>` for Cursor SQLite chats)
  - file `(mtime_ns, size)`
  - SessionCost version + schema version + pricing fingerprint

Never caches project instruction-file checks (`rules_check`), which are always evaluated
fresh per `(agent, cwd)` during a scan so edits to `CLAUDE.md`/`AGENTS.md` take effect
immediately. All writes are atomic (`tempfile` + `os.replace`) and bounded in size.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from . import __version__, locate, readers, rules
from .analyse import analyse

CACHE_SCHEMA = 1
MAX_ENTRIES = 5000
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|\n]\s*)git\s+(?:-[Cc]\s+\S+\s+)*commit\b")


def _cache_dir() -> Path:
    return Path(os.environ.get("SESSIONCOST_DIR") or Path.home() / ".sessioncost") / "cache"


def cache_path() -> Path:
    return _cache_dir() / "session-summaries.json"


def prices_fingerprint(prices: dict | None) -> str:
    """Stable short hash of pricing inputs so price/provider/tier/region changes invalidate cached USD."""
    if not isinstance(prices, dict):
        return "default"
    payload = {
        "models": prices.get("models") or {},
        "provider": prices.get("provider") or "",
        "tier": prices.get("tier") or "",
        "region": prices.get("region") or "",
        "checked": prices.get("checked") or "",
        "own": prices.get("own") or [],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def session_file_key(path: Any) -> tuple[str, int, int] | None:
    """Return `(cache_key, mtime_ns, size)` for a session path, or None if not stat-able."""
    try:
        if isinstance(path, Path):
            p = path
            st = p.stat()
            return (str(p.resolve()), int(st.st_mtime_ns), int(st.st_size))
        # CursorChat(db: Path, id: str)
        db = getattr(path, "db", None)
        cid = getattr(path, "id", None)
        if isinstance(db, Path) and cid:
            st = db.stat()
            return (f"cursor:{db.resolve()}#{cid}", int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None
    return None


def _tool_and_commit_counts(s: Any) -> tuple[int, int]:
    """Count total tool calls and non-error git commit commands in a Session."""
    calls = [c for t in getattr(s, "turns", ()) for c in getattr(t, "calls", ())]
    for h in getattr(s, "helpers", ()):
        calls.extend(getattr(h, "calls", ()))
    tools = 0
    commits = 0
    for c in calls:
        for act in getattr(c, "actions", ()):
            tools += 1
            if getattr(act, "kind", "") == "command" and not getattr(act, "is_error", False):
                target = str(getattr(act, "target", "") or "")
                if _GIT_COMMIT_RE.search(target):
                    commits += 1
    return tools, commits


def _compact_by_model(a: dict, primary_model: str, calls_n: int, sent: int, output: int, usd: float) -> list[dict]:
    """Aggregate call-level model usage into a tiny per-model list for `totals`."""
    call_list = a.get("calls") or []
    if not call_list:
        return [{
            "model": primary_model,
            "calls": calls_n,
            "sent": sent,
            "output": output,
            "usd": round(usd, 8),
        }]
    agg: dict[str, dict] = {}
    for c in call_list:
        mname = str(c.get("model") or primary_model or "unknown")
        rec = agg.setdefault(mname, {"model": mname, "calls": 0, "sent": 0, "output": 0, "usd": 0.0})
        rec["calls"] += 1
        rec["sent"] += int((c.get("sent") or {}).get("tokens") or 0)
        rec["output"] += int((c.get("written") or {}).get("tokens") or 0)
        rec["usd"] += float(c.get("usd") or 0.0)
    return [
        {**r, "usd": round(r["usd"], 8)}
        for r in agg.values()
    ]


def build_summary(s: Any, a: dict, path: Any, mode: str) -> dict:
    """Extract the compact, JSON-serializable summary needed by `insights`, `week`, and `totals`."""
    meta = a.get("meta") or {}
    tot = a.get("totals") or {}
    saving = a.get("saving") or {}
    money = a.get("money") or {}

    cwd = str(meta.get("cwd") or getattr(s, "cwd", "") or "").strip()
    primary_model = str(meta.get("model") or "unknown")
    usd = float(tot.get("usd") or 0.0)
    sent = int(tot.get("sent") or 0)
    output = int(tot.get("output") or 0)
    turns = int(tot.get("turns") or 0)
    calls = int(tot.get("calls") or 0)
    helper_calls = int(tot.get("helper_calls") or 0)
    calls_n = calls + helper_calls
    sid = str(meta.get("session_id") or locate.session_id(path))

    summary: dict[str, Any] = {
        "mode": mode,
        "meta": {
            "session_id": sid,
            "title": str(meta.get("title") or sid),
            "cwd": cwd,
            "agent": str(meta.get("agent") or getattr(s, "agent", "unknown") or "unknown"),
            "agent_label": str(meta.get("agent_label") or ""),
            "model": primary_model,
            "started": meta.get("started"),
            "ended": meta.get("ended"),
        },
        "totals": {
            "usd": round(usd, 8),
            "sent": sent,
            "output": output,
            "turns": turns,
            "calls": calls,
            "helper_calls": helper_calls,
            "working_min": float(tot.get("working_min") or 0.0),
            "waiting_min": float(tot.get("waiting_min") or 0.0),
        },
        "money": {
            "tokens_exact": bool(money.get("tokens_exact", True)),
        },
        "by_model": _compact_by_model(a, primary_model, calls_n, sent, output, usd),
    }

    if mode == "full":
        tools_n, commits_n = _tool_and_commit_counts(s)
        summary["tools"] = tools_n
        summary["commits"] = commits_n
        summary["saving"] = {
            "usd": round(float(saving.get("usd") or 0.0), 8),
            "tokens": int(saving.get("tokens") or 0),
        }
        summary["fixes"] = [
            {
                "id": str(f.get("id") or "").strip(),
                "title": str(f.get("title") or ""),
                "saving_tokens": int(f.get("saving_tokens") or 0),
                "saving_usd": round(float(f.get("saving_usd") or 0.0), 8),
                "how": str(f.get("how") or ""),
                "paste": bool(f.get("paste")),
            }
            for f in (a.get("fixes") or [])
            if str(f.get("id") or "").strip()
        ]
    return summary


class SummaryCache:
    """In-memory handle over `~/.sessioncost/cache/session-summaries.json` for a single scan."""

    def __init__(self, prices: dict | None, path: Path | None = None) -> None:
        self._path = path or cache_path()
        self._prices_hash = prices_fingerprint(prices)
        self._entries: dict[str, dict] = {}
        self._dirty = False
        self._rules_memo: dict[tuple[str, str], dict] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        if raw.get("schema") != CACHE_SCHEMA or raw.get("version") != __version__:
            return
        entries = raw.get("entries")
        if isinstance(entries, dict):
            self._entries = entries

    def _fresh_rules(self, agent: str, cwd: str) -> dict:
        key = (agent, cwd)
        if key not in self._rules_memo:
            try:
                self._rules_memo[key] = rules.check(agent, cwd)
            except Exception:
                self._rules_memo[key] = {}
        return self._rules_memo[key]

    def get_or_analyse(
        self,
        path: Any,
        prices: dict,
        *,
        mode: str = "full",
        engines: list | None = None,
    ) -> dict:
        """Return a session summary dict, using the local cache when `(path, mtime_ns, size, prices)` matches."""
        use_cache = (mode == "totals") or (mode == "full" and engines is None)
        finfo = session_file_key(path) if use_cache else None

        if finfo is not None:
            key, mtime_ns, size = finfo
            cached = self._entries.get(key)
            if (
                isinstance(cached, dict)
                and cached.get("mtime_ns") == mtime_ns
                and cached.get("size") == size
                and cached.get("prices_hash") == self._prices_hash
                and (cached.get("mode") == "full" or cached.get("mode") == mode)
                and isinstance(cached.get("summary"), dict)
            ):
                self.hits += 1
                cached["atime"] = int(time.time())
                summary = dict(cached["summary"])
                if mode == "full":
                    meta = dict(summary.get("meta") or {})
                    meta["rules_check"] = self._fresh_rules(
                        str(meta.get("agent") or ""), str(meta.get("cwd") or ""),
                    )
                    summary["meta"] = meta
                return summary

        self.misses += 1
        s = readers.read(path)
        run_engines = [] if mode == "totals" else engines
        a = analyse(s, prices, engines=run_engines)
        summary = build_summary(s, a, path, mode)

        if finfo is not None:
            key, mtime_ns, size = finfo
            self._entries[key] = {
                "mtime_ns": mtime_ns,
                "size": size,
                "prices_hash": self._prices_hash,
                "mode": mode,
                "atime": int(time.time()),
                "summary": summary,
            }
            self._dirty = True

        if mode == "full":
            out = dict(summary)
            meta = dict(out.get("meta") or {})
            meta["rules_check"] = (a.get("meta") or {}).get("rules_check") or self._fresh_rules(
                str(meta.get("agent") or ""), str(meta.get("cwd") or ""),
            )
            out["meta"] = meta
            return out
        return summary

    def save(self) -> None:
        """Atomically persist updated cache entries to disk if anything changed."""
        if not self._dirty:
            return
        try:
            if len(self._entries) > MAX_ENTRIES:
                ordered = sorted(
                    self._entries.items(),
                    key=lambda kv: int((kv[1] or {}).get("atime") or 0),
                    reverse=True,
                )
                self._entries = dict(ordered[:MAX_ENTRIES])
            self._path.parent.mkdir(parents=True, exist_ok=True)
            doc = {
                "schema": CACHE_SCHEMA,
                "version": __version__,
                "entries": self._entries,
            }
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".summaries-", suffix=".tmp")
            try:
                if os.name != "nt":
                    try:
                        os.chmod(tmp, 0o600)
                    except OSError:
                        pass
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, separators=(",", ":"))
                os.replace(tmp, self._path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._dirty = False
        except OSError:
            # Caching is strictly best-effort; never fail a report if cache dir is read-only.
            pass
