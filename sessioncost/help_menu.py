"""What you can say to SessionCost in the chat: shown once on a computer's first guarded chat, and again whenever you
ask for it ("show me the SessionCost help", `sessioncost help`). Owner 2026-11-21: nobody should have to know the
commands; ask in your own words."""
from __future__ import annotations

import re

from .guard_store import _read, _write, home

GUIDE = "https://kish21.github.io/sessioncost-help/"
MENU = ("SessionCost: what you can say in this chat\n"
        '- "show my report": where this chat\'s tokens went\n'
        '- "show my insights", "how was my week?", "show my totals"\n'
        '- "fix my token waste": what to change, done on your yes, with undo\n'
        '- "write a handover", then "continue from handover" in a new chat\n'
        '- "warn me at 1m tokens": when the handover is offered\n'
        '- "turn the guard off" / "turn the guard on" / "turn on git guard"\n'
        '- "show my allowed steps": what you let the guard pass\n'
        '- "show my prices", "set up MCP", "which version do I have"\n'
        "Or just tell me what you want to do with SessionCost, in your own words.\n"
        f'Guide: {GUIDE} . To see this list again, say "show me the SessionCost help".')

_SC = r"(?:session\s*cost)"
_HELP = re.compile(rf"^\s*(?:please\s+)?(?:"
                   rf"[/$]{_SC}(?::{_SC})?\s+help|"
                   rf"(?:show|give|open)\s+(?:me\s+)?(?:the\s+)?(?:{_SC}\s+)?(?:help|menu|commands)(?:\s+for\s+{_SC})?|"
                   rf"{_SC}\s+(?:help|menu|commands)|"
                   rf"what\s+can\s+{_SC}\s+do"
                   rf")[\s.!?]*(?:please[\s.!?]*)?$", re.I)


def says_help(text: str) -> bool:
    """The user's own whole message asks for SessionCost's help list. "help me fix this bug" is not that."""
    return bool(_HELP.match(str(text or "")))


def menu_once() -> str:
    """The list, the first time on this computer; "" after that (config key help_shown)."""
    cfg = home() / "config.json"
    have = _read(cfg, {})
    have = have if isinstance(have, dict) else {}
    if have.get("help_shown"):
        return ""
    try:
        _write(cfg, {**have, "help_shown": True})
    except OSError:
        return ""                     # cannot remember it: better never than in every chat
    return MENU


def main(argv: list[str] | None = None) -> int:
    print(MENU)
    return 0
