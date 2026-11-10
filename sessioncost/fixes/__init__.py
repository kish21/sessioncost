"""Fixes: each one names what to change and a saving measured from this session's own calls.

The open tool ships nine detectors (BasicFixes: unused tools, unused built-ins, repeated reads, repeated writes, error
loops, helper polling, test reruns, confirm turns, long chats), one file each in this folder; common.py has what they
share. Every saving is the cost of the tokens or calls involved in THIS session, priced like the rest of the report -
nothing is projected or guessed.

PLUG-IN POINT: a separate fix engine can add detectors without changing this repo. Set the environment variable
SESSIONCOST_ENGINE to an importable module name; that module must expose `Engine()`, an object with a `name` and
`find(session, pricer) -> list[Fix]` (the FixEngine interface below). It runs after the built-in detectors and its
fixes are ranked together with theirs.
"""
from __future__ import annotations

import importlib
import os
from typing import Protocol

from ..model import Fix, Session
from .big_results import big_results
from .cache_break import cache_break
from .common import (CONFIRM, asked, helper_name, is_confirm, lettered,  # noqa: F401 (fixes.<name> for other modules)
                     sent_usd, tok)
from .confirm_turns import confirm_turns, skill_in_use  # noqa: F401
from .error_loop import error_loop
from .helper_polling import helper_polling
from .helper_rereads import helper_rereads
from .images import images
from .long_chat import LONG_BREAK_S, LONG_CHAT, SUMMARY, long_chat, new_chat_steps, task_starts  # noqa: F401
from .long_output import long_output
from .model_choice import model_choice
from .repeated_reads import repeated_reads
from .repeated_writes import repeated_writes
from .reruns import is_test, test_reruns, untested_edits  # noqa: F401 (not test_*.py: pytest would collect it)
from .stale_context import stale_context
from .startup_bloat import startup_bloat
from .unused_builtins import unused_builtins
from .unused_tools import server_name, server_of, turn_off_steps, unused_tools  # noqa: F401

MAX_FIXES = 6


class FixEngine(Protocol):
    name: str

    def find(self, session: Session, pricer) -> list[Fix]: ...



class BasicFixes:
    name = "built-in"

    def find(self, session: Session, pricer) -> list[Fix]:
        return [f for f in (unused_tools(session, pricer), unused_builtins(session, pricer),
                            repeated_reads(session, pricer), repeated_writes(session, pricer),
                            error_loop(session, pricer), helper_polling(session, pricer),
                            test_reruns(session, pricer),
                            confirm_turns(session, pricer),
                            long_chat(session, pricer),
                            cache_break(session, pricer), stale_context(session, pricer),
                            big_results(session, pricer), startup_bloat(session, pricer),
                            images(session, pricer), long_output(session, pricer),
                            helper_rereads(session, pricer), model_choice(session, pricer)) if f]


def load_engines() -> list:
    engines: list = [BasicFixes()]
    # ---- PLUG-IN POINT (see the module docstring). Only loaded when the user names it explicitly.
    mod = os.environ.get("SESSIONCOST_ENGINE", "").strip()
    if mod:
        try:
            engines.append(importlib.import_module(mod).Engine())
        except Exception as e:  # an engine problem must never break the report
            print(f"sessioncost: fix engine {mod!r} not loaded: {e}")
    return engines


def find(session: Session, pricer, engines: list | None = None) -> list[Fix]:
    out: list[Fix] = []
    for eng in engines if engines is not None else load_engines():
        for f in eng.find(session, pricer) or []:
            if f.source == "built-in" and getattr(eng, "name", "built-in") != "built-in":
                f.source = eng.name
            out.append(f)
    out = [f for f in out if f.saving_usd > 0]
    out.sort(key=lambda f: (-f.saving_tokens, -f.saving_usd))   # tokens cut first; money follows
    top = out[:MAX_FIXES]
    by_usd = {id(f) for f in sorted(out, key=lambda f: -f.saving_usd)[:MAX_FIXES]}
    for f in out[MAX_FIXES:]:
        if f.saving_tokens == 0 and id(f) in by_usd:
            top.append(f)
    return top
