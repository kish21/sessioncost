"""Check 4 (1.1): Step 0 (instruction files, always-on rules, or skill list) is bloated on every call."""
from __future__ import annotations

import re
from pathlib import Path

from .. import rules as rulesmod
from ..model import Fix, Session, said_kind
from .common import before_change_note, changed_after_start, first_and_cached, setup_tokens, tok

STEP0_NON_TOOL_BLOAT_TOKENS = 15_000
INSTRUCTION_BLOAT_TOKENS = 2_500
SKILL_LIST_BLOAT_TOKENS = 2_500

_LANG_HINTS = re.compile(r"\b(react|nextjs|vue|angular|tailwind|pytest|django|fastapi|sql|graphql)\b|"
                         r"\.(py|ts|tsx|js|jsx|vue|css|scss|sql|rs|go)\b", re.I)


FILE_NAMED_TOKENS = 500      # an instruction file this big is named in the tip
# how each agent turns one skill off (their docs, 2026-11-23); Cursor, Copilot and Antigravity have no switch
SKILL_OFF = {
    "claude-code": "The \"Hide the skills\" tip names them and `sessioncost fix` hides them; or type /skills.",
    "codex": "In ~/.codex/config.toml add, per skill: [[skills.config]] name = \"<skill>\" enabled = false.",
    "cursor": "Cursor has no switch for one skill: move the ones you do not use out of .cursor/skills, .agents/skills "
              "or ~/.cursor/skills.",
    "copilot": "Copilot has no switch for one skill: move the ones you do not use out of .github/skills, "
               ".agents/skills or ~/.copilot/skills.",
    "antigravity": "Antigravity has no switch for one skill: move the ones you do not use out of .agents/skills or "
                   "~/.gemini/config/skills.",
    "other": "Move the skills you do not use out of the agent's skills folders.",
}
SKILL_OFF["copilot-vscode"] = SKILL_OFF["copilot"]     # Copilot Chat in VS Code reads the same skill folders


def _short_path(p: str) -> str:
    """C:\\Users\\u\\.claude\\CLAUDE.md -> ~/.claude/CLAUDE.md (the home folder is not shown)."""
    q = p.replace("\\", "/")
    home = Path.home().as_posix()
    if q.lower().startswith(home.lower() + "/"):
        return "~" + q[len(home):]
    return re.sub(r"^[A-Za-z]:/Users/[^/]+", "~", q)


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
    from ..helper_agent import rules_file
    # the instruction files the log names, biggest first (owner, 2026-11-23: say which ones, and trim, never delete)
    big = sorted((f for f in s.setup.files if tok(f.chars) >= FILE_NAMED_TOKENS), key=lambda f: -f.chars)
    names = [_short_path(f.what) for f in big] or [rules_file(s.agent)]
    ask = (f"Shorten {' and '.join(names)} (they are sent with every message). First, for each file, propose what "
           "to cut: keep the rules needed in every chat, and move reference parts and long workflows into separate "
           "files read only when a task needs them, with one line saying where each one is. In a memory index keep "
           "one short line per memory. Do not drop any rule. Show me the proposal file by file and wait for my yes "
           "before you edit anything.")
    changed = changed_after_start(s, [f.what if Path(f.what).is_absolute() else str(Path(s.cwd or ".") / f.what)
                                      for f in big])
    note = before_change_note([_short_path(p) for p in changed])
    how_lines = []
    if big:
        how_lines.append("Shorten these files; keep them, they hold your rules: "
                         + "; ".join(f"{_short_path(f.what)} (~{tok(f.chars):,} tokens)" for f in big)
                         + ". Paste the request below into your chat and your agent does it with you.")
    skill_tok = sum(tok(p.chars) for p in s.setup.parts if "skill" in p.what.lower())
    if skill_tok >= SKILL_LIST_BLOAT_TOKENS or not big:
        how_lines.append("Hide the skills and plugins this project does not use: each one's description is sent "
                         "with every call. " + SKILL_OFF.get(s.agent, SKILL_OFF["other"]))
    if mdc_glob_hint:
        how_lines.append("In `.cursor/rules/*.mdc`, replace `alwaysApply: true` with `globs: [\"*.tsx\", ...]` for "
                         "file-specific rules.")
    how_lines = [f"{i}. {x}" for i, x in enumerate(how_lines, 1)]
    return Fix(
        id="startup-bloat",
        title=f"Trim Step 0 instructions and skill descriptions (~{cut_per_call:,} tokens on every call)",
        why=(f"Before anyone says a word, Step 0 sends ~{non_tool_tok:,} tokens of instructions and skill descriptions "
             f"on every call. Trimming always-on instructions and unused skills saves ~{total_tok:,} tokens across {len(calls)} calls "
             f"(an estimate: it assumes about half of the instructions and 60% of an unused skill list can go)."
             + note),
        saving_usd=total_usd, saving_tokens=total_tok,
        where="every call", turns=[], items=items[:6],
        calls=[("main", c.n) for c in calls],
        how="\n".join(how_lines),
        paste=False, ask=ask,
    )
