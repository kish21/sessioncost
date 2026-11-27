"""Render the report model as one self-contained HTML page (inline CSS/JS, data embedded as JSON).

The page is the session board (report_board.html). It uses the fonts already on the computer (system-ui, Segoe UI,
Consolas...): no font files to ship or embed."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

TEMPLATE = Path(__file__).parent / "report_board.html"
DEFAULT_CONTEXT_LIMIT = 200_000


def _tok(n: float) -> str:
    n = float(n or 0)
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:.1f}B"
    if a >= 1e6:
        return f"{n / 1e6:.0f}M" if a >= 1e7 else f"{n / 1e6:.1f}M"
    if a >= 1e3:
        return f"{round(n / 1e3)}k"
    return str(round(n))


def _usd(x: float) -> str:
    x = float(x or 0)
    if x == 0:
        return "$0"
    if x >= 100:
        return f"${x:.0f}"
    if x >= 0.995:
        return f"${x:.2f}"
    if x >= 0.01:
        return f"${x:.3f}"
    return f"${x:.4f}"


def _plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def _join_en(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def answer_summary(a: dict) -> dict:
    """Build the two plain-English answer-first sentences (cost + safety) and their detail links."""
    totals = a.get("totals") or {}
    money = a.get("money") or {}
    sent = int(totals.get("sent") or 0)
    usd_val = float(totals.get("usd") or 0)
    rng = money.get("range")
    cost_str = f"{_usd(rng['low'])}–{_usd(rng['high'])}" if isinstance(rng, dict) and "low" in rng and "high" in rng else _usd(usd_val)

    ctx = int(totals.get("context_max") or 0)
    limit = int(totals.get("context_limit") or DEFAULT_CONTEXT_LIMIT)
    if ctx > 0 and limit > 0:
        pct = round(ctx * 100 / limit)
        if pct > 100:
            usage_part = f"This chat went over your {_tok(limit)} limit ({pct}%, {_tok(sent)} tokens, {cost_str})"
        elif pct >= 1:
            usage_part = f"This chat used {pct}% of your {_tok(limit)} limit ({_tok(sent)} tokens, {cost_str})"
        else:
            usage_part = f"This chat used under 1% of your {_tok(limit)} limit ({_tok(sent)} tokens, {cost_str})"
    else:
        usage_part = f"This chat sent {_tok(sent)} tokens ({cost_str})"

    fixes = sorted(
        a.get("fixes") or [],
        key=lambda f: (float(f.get("saving_usd") or 0), int(f.get("saving_tokens") or 0)),
        reverse=True,
    )
    if fixes:
        top = fixes[0]
        top_title = str(top.get("title") or "top fix").strip()
        top_tok = int(top.get("saving_tokens") or 0)
        top_usd = float(top.get("saving_usd") or 0)
        save_bits = f"~{_tok(top_tok)} tokens, {_usd(top_usd)}" if top_tok > 0 else f"~{_usd(top_usd)}"
        if len(fixes) == 1:
            fix_part = f"1 fix ({top_title}) would save {save_bits}."
        else:
            fix_part = f"{len(fixes)} fixes found — {top_title} would save the most ({save_bits})."
        cost_target = "#secFixes"
        cost_cta = "See fixes ↓"
    else:
        fix_part = "nothing obvious to cut — clean bill of health."
        cost_target = "#secPath"
        cost_cta = "See the path ↓"
    cost_sentence = f"{usage_part}; {fix_part}"

    guard = a.get("guard")
    watch = a.get("watch") or {}
    sev = (a.get("watch_meta") or {}).get("severity") or {}
    risky_count = sum(int(n or 0) for m, n in watch.items() if n and sev.get(m) != "info")

    if isinstance(guard, dict):
        blocked = int(guard.get("blocked") or 0)
        asked = int(guard.get("asked") or 0)
        keys_seen = int(guard.get("keys_seen") or 0)
        injections = int(guard.get("injections") or 0) + len(guard.get("loaded") or [])
        checked = int(guard.get("checked") or 0)
        intact = guard.get("intact", True)
        parts: list[str] = []
        if blocked:
            parts.append(f"stopped {_plural(blocked, 'risky step')}")
        if asked:
            parts.append(f"asked you before {_plural(asked, 'step')}")
        if keys_seen:
            parts.append(f"flagged {_plural(keys_seen, 'key')} in output")
        if injections:
            parts.append(f"caught {_plural(injections, 'prompt injection')}")
        if parts:
            safety_sentence = f"The guard {_join_en(parts)}."
            safety_tone = "warn"
        elif checked > 0:
            safety_sentence = f"The guard was on and checked {_plural(checked, 'step')}; nothing risky was stopped or asked."
            safety_tone = "ok"
        else:
            safety_sentence = "The guard was on in this chat; no risky steps came up."
            safety_tone = "ok"
        if not intact:
            safety_sentence += " Warning: the guard log was changed or cut off."
            safety_tone = "warn"
        safety_target = "#secGuard"
        safety_cta = "Open guard report ↓"
    else:
        if risky_count > 0:
            safety_sentence = (
                f"The guard was off in this chat, so steps were not checked live; "
                f"the trace scan found {_plural(risky_count, 'risky step')} to review."
            )
            safety_target = "#secSafety"
            safety_cta = "Open safety rules ↓"
            safety_tone = "warn"
        else:
            safety_sentence = (
                "The guard was off in this chat, so steps were not checked live "
                "(no risky steps were spotted in the log)."
            )
            safety_target = "#secGuard"
            safety_cta = "Turn the guard on ↓"
            safety_tone = "muted"

    return {
        "cost": cost_sentence,
        "cost_target": cost_target,
        "cost_cta": cost_cta,
        "cost_tone": "warn" if fixes else "ok",
        "safety": safety_sentence,
        "safety_target": safety_target,
        "safety_cta": safety_cta,
        "safety_tone": safety_tone,
    }


def _render_answer_first(summary: dict) -> str:
    cost_item = (
        f'<a class="af-item af-{html.escape(summary["cost_tone"])}" data-kind="cost" '
        f'href="{html.escape(summary["cost_target"])}">'
        f'<span class="af-tag">Cost</span>'
        f'<span class="af-text" id="afCostText">{html.escape(summary["cost"])}</span>'
        f'<span class="af-go">{html.escape(summary["cost_cta"])}</span></a>'
    )
    safety_item = (
        f'<a class="af-item af-{html.escape(summary["safety_tone"])}" data-kind="safety" '
        f'href="{html.escape(summary["safety_target"])}">'
        f'<span class="af-tag">Safety</span>'
        f'<span class="af-text" id="afSafetyText">{html.escape(summary["safety"])}</span>'
        f'<span class="af-go">{html.escape(summary["safety_cta"])}</span></a>'
    )
    return cost_item + "\n      " + safety_item


def render(a: dict) -> str:
    # Escape <, >, & so embedded JSON can never close <script> or enter HTML5 script-data double-escape state (<!-- + <script).
    data = (
        json.dumps(a, ensure_ascii=False, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    page = TEMPLATE.read_text(encoding="utf-8")
    summary_html = _render_answer_first(answer_summary(a))
    subs = {
        "__TITLE__": html.escape(a["meta"]["title"][:80]),
        "__ANSWER_FIRST__": summary_html,
        "__DATA__": data,
    }
    return re.sub(r"__(?:TITLE|ANSWER_FIRST|DATA)__", lambda m: subs[m.group(0)], page)
