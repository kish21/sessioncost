"""Check 4 (1.1): Step 0 (instruction files, always-on rules, or skill list) is bloated on every call."""
from __future__ import annotations

import re
from pathlib import Path

from .. import rules as rulesmod
from ..model import Fix, Session, said_kind
from .common import first_and_cached, setup_tokens, tok

STEP0_NON_TOOL_BLOAT_TOKENS = 15_000
INSTRUCTION_BLOAT_TOKENS = 2_500
SKILL_LIST_BLOAT_TOKENS = 2_500

_LANG_HINTS = re.compile(r"\b(react|nextjs|vue|angular|tailwind|pytest|django|fastapi|sql|graphql)\b|"
                         r"\.(py|ts|tsx|js|jsx|vue|css|scss|sql|rs|go)\b", re.I)


def _used_any_skill(s: Session) -> bool:
    if any(a.kind == "skill" for a in s.actions()):
        return True
    return any(t.kind == "command" or said_kind(t.text) == "command" for t in s.turns)


def startup_bloat(s: Session, pr) -> Fix | None:
    calls = s.calls
    if not calls:
        return None
    non_tool_tok = sum(tok(p.chars) for p in s.setup.parts if "tool definition" not in p.what.lower())
    if not non_tool_tok:
        non_tool_tok = setup_tokens(s)
    items: list[str] = []
    cut_per_call = 0
    mdc_glob_hint = False

    # 1. Check instruction files on disk when the project folder is available
    file_tok_total = 0
    if s.cwd and Path(s.cwd).is_dir():
        info = rulesmod.check(s.agent, s.cwd)
        for f in info.get("files") or []:
            if not f.get("every_call"):
                continue
            ftok = int(f.get("tokens") or 0)
            file_tok_total += ftok
            path_label = str(f.get("name") or f.get("where") or "instructions")
            full = Path(s.cwd) / path_label.split(" (", 1)[0]
            txt = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""
            if ftok >= 1_500:
                cut = int(ftok * 0.5)
                cut_per_call += cut
                sc_note = ""
                if "## SessionCost rules" in txt:
                    sc_part = txt.split("## SessionCost rules", 1)[1]
                    sc_tok = tok(len(sc_part))
                    if sc_tok >= 200:
                        sc_note = f", including ~{sc_tok:,} tokens in SessionCost rules"
                items.append(f"`{path_label}` (~{ftok:,} tokens sent on every call{sc_note})")
            if path_label.endswith(".mdc") and f.get("every_call"):
                if _LANG_HINTS.search(txt) and ftok >= 200:
                    mdc_glob_hint = True
                    if ftok < 1_500:
                        cut_per_call += ftok
                    items.append(f"`{path_label}` has `alwaysApply: true` — switch to `globs:` so it only loads on matching files")

    # 2. Check logged Step 0 parts (instruction files and skill list)
    for p in s.setup.parts:
        ptok = tok(p.chars)
        w = p.what.lower()
        if "instruction" in w and ptok >= INSTRUCTION_BLOAT_TOKENS and file_tok_total < ptok // 2:
            cut = int(ptok * 0.5)
            cut_per_call += cut
            items.append(f"{p.what} (~{ptok:,} tokens on every call)")
        elif "skill" in w and ptok >= SKILL_LIST_BLOAT_TOKENS and not _used_any_skill(s):
            cut = int(ptok * 0.6)
            cut_per_call += cut
            items.append(f"{p.what} (~{ptok:,} tokens on every call, no skill used in this chat)")

    if non_tool_tok < STEP0_NON_TOOL_BLOAT_TOKENS and cut_per_call < 1_500 and not mdc_glob_hint:
        return None
    if cut_per_call < 1_000 and non_tool_tok >= STEP0_NON_TOOL_BLOAT_TOKENS:
        cut_per_call = max(1_500, (non_tool_tok - 10_000) // 2)
        if not items:
            items.append(f"Step 0 instructions and skills total ~{non_tool_tok:,} tokens on every call")
    if cut_per_call <= 0:
        return None

    total_tok = cut_per_call * len(calls)
    total_usd = first_and_cached(cut_per_call, calls, pr)
    how_lines = [
        "1. Keep your main instruction file under ~200 lines; move reference docs and workflows into on-demand files or skills.",
    ]
    if mdc_glob_hint:
        how_lines.append("2. In `.cursor/rules/*.mdc`, replace `alwaysApply: true` with `globs: [\"*.tsx\", ...]` for file-specific rules.")
    else:
        how_lines.append("2. Disable unused skills or plugins so their descriptions are not sent on every call.")
    return Fix(
        id="startup-bloat",
        title=f"Trim Step 0 instructions and skill descriptions (~{cut_per_call:,} tokens on every call)",
        why=(f"Before anyone says a word, Step 0 sends ~{non_tool_tok:,} tokens of instructions and skill descriptions "
             f"on every call. Trimming always-on instructions and unused skills saves ~{total_tok:,} tokens across {len(calls)} calls."),
        saving_usd=total_usd, saving_tokens=total_tok,
        where="every call", turns=[], items=items[:6],
        calls=[("main", c.n) for c in calls],
        how="\n".join(how_lines),
        paste=False,
    )
