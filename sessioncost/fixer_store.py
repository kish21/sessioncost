"""sessioncost fix, what it keeps: the files under base(), the fix auto switch, the long-chat setting, and the
learned list the developer's own agent grows."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import fixes as fixmod
from . import rules



LONG_CHAT = fixmod.LONG_CHAT            # one number for "this chat is long": the long-chat fix's
HEAD = "## SessionCost rules (fewer tokens)"
SETTINGS = Path(".claude") / "settings.local.json"   # Claude Code's personal project settings: never committed
BUILTIN_SETTING = {"Artifact": ("enableArtifact", False), "Workflow": ("disableWorkflows", True)}
RULE_OF = {fid: text for text, ids, _ in rules.RULES for fid in ids}
KEEP_SEEN = 60                          # chats remembered per project for verify
NOTE_MAX_AGE_S = 2 * 86400              # a to-do list older than this is not shown
CHAT_KEEP_S = 7 * 86400
# Not annoying (proposed numbers, owner 2026-11-05 "build it"): the list in the chat is one line; smaller waste waits
# for `sessioncost fix`; one list a chat unless something big turns up; an item offered and not fixed stays quiet in
# that project for a week.
MIN_TIP_TOKENS = 2_000
BIG_TIP_TOKENS = 20_000
OFFER_QUIET_S = 7 * 86400
# What SessionCost already checks for: the agent is told, so at a handover it reports only what is new.
KNOWN = ["unused connected tools (MCP servers, connectors)", "Claude Code's unused Artifact and Workflow tools",
         "re-reading files that did not change", "rewriting whole files instead of editing", "retrying a failing command",
         "re-running tests when nothing changed", "checking on a helper instead of waiting", "asking to confirm the next "
         "step", "long chats", "prompt cache expiring or breaking mid-chat",
         "large tool results staying in the chat across 10+ later calls",
         "single huge tool results or lockfile reads", "bloated Step 0 instructions or always-on rules",
         "screenshots or images carried across calls", "long verbose replies or heavy hidden thinking",
         "helpers re-reading files the main agent already read",
         "using a top-tier model for small mechanical steps"]
LEARN_MAX = 240                         # one plain line: what happened, and the rule that prevents it
SIMILAR = 0.8
ASKED_FIX = re.compile(r"^\s*[/$](?:sessioncost:)?sessioncost\b|\bsessioncost fix\b", re.I)   # "/sessioncost fix", "/sessioncost"
HANDOVER_ASK = re.compile(r"\b(?:continue|carry on|resume|pick up)\b.{0,40}\bhand-?over\b", re.I | re.S)


# ---- where things are kept ----
def base() -> Path:
    return Path(os.environ.get("SESSIONCOST_DIR") or Path.home() / ".sessioncost")


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def project_key(project) -> str:
    name = re.sub(r"[^\w.-]", "_", Path(str(project)).name or "root")[:40]
    return f"{name}-{hashlib.sha1(_norm(project).encode()).hexdigest()[:8]}"


def fixes_file(project) -> Path:
    return base() / "fixes" / f"{project_key(project)}.json"


def live_file(project) -> Path:
    return base() / "fixes" / "live" / f"{project_key(project)}.json"


def chat_file(chat) -> Path:
    return base() / "fixes" / "chats" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', str(chat or 'unknown'))[:80]}.json"


def backups_dir(project) -> Path:
    return base() / "backups" / project_key(project)


def handover_file(project) -> Path:
    return base() / "handover" / f"{project_key(project)}.md"


def offered_file(project) -> Path:
    return base() / "fixes" / "offered" / f"{project_key(project)}.json"


def learned_file() -> Path:
    """Token waste the developer's own agent found that SessionCost's checks do not cover: one list per developer,
    across every project, growing with each handover. Kept in a shape a team could collect later."""
    return base() / "learned.json"


def _load(p: Path, default):
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _save(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _ts(iso: str | None) -> float:
    try:
        d = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return 0.0
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tok(n: float) -> str:
    """1607 -> 1.6k (never "2k" for something under the 2k tip limit), 25_000 -> 25k, 1_700_000 -> 1.7M."""
    n = float(n or 0)
    if n >= 999_500:
        return f"{n / 1e6:.1f}M"
    if n >= 9_950:
        return f"{n / 1e3:.0f}k"
    if n >= 1000:
        return f"{n / 1e3:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n:.0f}"


def _first(text: str) -> str:
    m = re.match(r"(.+?[.!?])(?:\s|$)", str(text or "").strip(), re.S)
    return (m.group(1) if m else str(text or "")).strip()


# ---- the `fix auto` switch ----
def _config() -> Path:
    from . import guard
    return guard.home() / "config.json"


def auto_on() -> bool:
    return _load(_config(), {}).get("fix_auto") is True


def auto_text() -> str:
    return ("SessionCost fix auto: on for every chat, until `sessioncost fix auto off`." if auto_on() else
            "SessionCost fix auto: off. `sessioncost fix auto on` turns it on; `sessioncost fix` works either way.")


def set_auto(on: bool) -> str:
    cfg = _config()
    _save(cfg, {**_load(cfg, {}), "fix_auto": bool(on)})
    if on:
        return ("SessionCost fix auto: on for every chat, every day, until `sessioncost fix auto off`. After each answer "
                "SessionCost looks for new token waste; your agent lists it at the start of its next reply and asks "
                f"before it changes anything. A chat over {_tok(long_chat())} tokens gets a handover offer; "
                "`sessioncost fix long-chat <tokens>` changes that (for example 150k).")
    return "SessionCost fix auto: off. `sessioncost fix` still shows the to-do list whenever you ask."


def long_chat() -> int:
    """The developer's own "this chat is long" (`sessioncost fix long-chat 150k`), else LONG_CHAT."""
    v = _load(_config(), {}).get("long_chat")
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 10_000 else LONG_CHAT


def parse_tokens(text: str) -> int | None:
    """150k, 150000, 1m, 1.5M -> tokens; None when it is not a number like that."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([km]?)\s*", str(text or ""), re.I)
    if not m:
        return None
    return round(float(m.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()])


def set_long_chat(text: str) -> tuple[int, str]:
    n = parse_tokens(text)
    if n is None or n < 10_000:
        return 2, "sessioncost fix long-chat: give the tokens, 10k or more (for example 150k, or 1m)."
    cfg = _config()
    _save(cfg, {**_load(cfg, {}), "long_chat": n})
    return 0, f"SessionCost fix: a chat over {_tok(n)} tokens now gets the handover offer (it was {_tok(LONG_CHAT)} by default)."


def budget_usd() -> float | None:
    """The developer's per-chat dollar ceiling (`sessioncost fix budget 5`), else None."""
    v = _load(_config(), {}).get("budget_usd")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0.05 else None


def budget_text() -> str:
    b = budget_usd()
    usd = f"${b:.2f} dollar budget" if b is not None else "no dollar budget set"
    return (f"SessionCost fix budget: {_tok(long_chat())} token ceiling, {usd}. "
            "`sessioncost fix budget 5` (dollars) or `150k` (tokens) changes it; `sessioncost fix budget off` clears the "
            "dollar budget.")


def set_budget(text: str) -> tuple[int, str]:
    s = str(text or "").strip()
    if s.lower() in ("off", "none", "clear"):
        cfg = _config()
        data = _load(cfg, {})
        data.pop("budget_usd", None)
        _save(cfg, data)
        return 0, f"SessionCost fix budget: dollar budget cleared (token ceiling remains {_tok(long_chat())})."
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[km]", s, re.I) or (not s.startswith("$") and (parse_tokens(s) or 0) >= 10_000):
        return set_long_chat(s)
    # 5, $5, 5$, 5usd, 5 USD: an unquoted $5 never arrives (bash and PowerShell read it as a variable)
    m = re.fullmatch(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:\$|usd|dollars?)?", s, re.I)
    val = float(m.group(1)) if m else -1.0
    if val < 0.05:
        return 2, ("sessioncost fix budget: give a dollar amount (0.05 or more, e.g. `sessioncost fix budget 5`), "
                   "a token limit (e.g. 150k), or `off`.")
    cfg = _config()
    _save(cfg, {**_load(cfg, {}), "budget_usd": round(val, 4)})
    return 0, f"SessionCost fix budget: a chat over ${val:.2f} now gets the handover offer."



# ---- what the developer's own agent found: the list keeps growing ----
def _similar(a: str, b: str) -> bool:
    pa, pb = rules._plain(a), rules._plain(b)
    return bool(pa and pb) and (pa == pb or difflib.SequenceMatcher(None, pa, pb).ratio() >= SIMILAR)


def learned() -> list[dict]:
    return [x for x in _load(learned_file(), {}).get("items", []) if isinstance(x, dict) and x.get("rule")]


def known() -> list[str]:
    """Everything SessionCost knows as token waste: its own checks, then what agents found before."""
    return KNOWN + [x["what"] for x in learned()]


def learn(what: str, rule: str, project=None, agent: str = "") -> tuple[int, str]:
    """Keep a token waste the agent found (and the rule that prevents it), unless it is already known."""
    what, rule = " ".join(str(what or "").split()), " ".join(str(rule or "").split())
    if not what or not rule:
        return 2, "Not kept: give what happened and --rule, the rule that prevents it."
    if len(what) > LEARN_MAX or len(rule) > LEARN_MAX or re.search(r"https?://|www\.|`|[<>]", what + rule):
        return 2, (f"Not kept: what happened and the rule are one plain line each, {LEARN_MAX} characters at most, "
                   "with no links or code.")
    for text, _, keys in rules.RULES:
        if _similar(rule, text) or any(rules._plain(k) in rules._plain(rule) for k in keys):
            return 0, f"Already known: SessionCost checks for this (\"{text}\"). Nothing added."
    data = _load(learned_file(), {})
    items = [x for x in data.get("items", []) if isinstance(x, dict)]
    now, where = _now(), str(Path(project).resolve()) if project else ""
    for x in items:
        if _similar(rule, x.get("rule", "")) or _similar(what, x.get("what", "")):
            x["times"] = int(x.get("times") or 1) + 1
            x["last"] = now
            x["projects"] = sorted(set(x.get("projects") or []) | ({where} if where else set()))
            _save(learned_file(), {**data, "version": 1, "items": items})
            return 0, f"Already known (found {x['times']} times now): {x['what']}. Nothing added."
    items.append({"id": "learned-" + hashlib.sha1(rules._plain(rule).encode()).hexdigest()[:8], "what": what,
                  "rule": rule, "source": "agent", "agent": agent, "first": now, "last": now, "times": 1,
                  "projects": [where] if where else []})
    _save(learned_file(), {**data, "version": 1, "items": items})
    return 0, f"Kept: {what}. `sessioncost fix` offers its rule from now on, in every project."
