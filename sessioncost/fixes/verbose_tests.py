"""Check verbose-tests (owner, 2026-11-24): test runs that poured long output into the chat. Names the verbose flag
in the command or in the project's own config (pyproject addopts, pytest.ini, package.json scripts) with its line,
and proposes the digester helper, which runs long commands and sends back only the lines that matter."""
from __future__ import annotations

import re

from ..model import Fix, Session
from .common import Ledger, short_target, src, tok, turns_where
from .reruns import is_test

MIN_EACH = 2_000             # a test run's output this big counts
CUT = 0.8                    # an estimate: failures and the summary line are about a fifth
_VERBOSE = re.compile(r"(?:^|\s)(-v{1,3}|--verbose|--reporter[= ](?:verbose|spec)|-rA)\b")
CONFIG = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "package.json")
_CFG_LINE = re.compile(r"(addopts|\"test[^\"]*\"\s*:).*?(?:^|[\s\"'=])(-v{1,3}|--verbose|--reporter[= ]?"
                       r"(?:verbose|spec)|-rA)\b", re.I)


def verbose_tests(s: Session, pr, home=None) -> Fix | None:
    led, items, turns, hit, flags = Ledger(), [], set(), [], set()
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        for i, c in enumerate(calls):
            for a in c.actions:
                n = tok(a.result_chars)
                if not is_test(a) or n < MIN_EACH or i + 1 >= len(calls):
                    continue
                led.carry(src(c, a), int(n * CUT), calls[i + 1:], pr)
                turns.add(c.turn)
                hit.append((c.agent, c.n))
                if m := _VERBOSE.search(a.target or ""):
                    flags.add(m.group(1))
                items.append(f"`{short_target(a.target, s.cwd)}` (~{n:,} tokens of output, call #{c.n})")
    if not items:
        return None
    from .. import workspace
    from ..helper_agent import make_stick, prompt, rules_file
    ws = workspace.of(s, home)
    cfg = [ln for name in CONFIG if (p := ws.project(name)) for ln in ws.find(_CFG_LINE, [p])]
    rule = ("Run tests quietly and show only failures (for example `pytest -q --tb=short`, or the test runner's "
            "quiet or dot reporter), and run only the tests of the code you changed.")
    steps = []
    if cfg:
        steps.append("Your project config turns on long output: " + "; ".join(ln.quote(80) for ln in cfg[:3])
                     + ". Remove the verbose flag there.")
    if flags:
        steps.append(f"The commands themselves used {', '.join(sorted(flags))}: drop it.")
    steps.append(f"Add to {rules_file(s.agent)}: \"{rule}\"")
    have = ws.helper_file("digester")
    helper = (make_stick(s.agent, ws.show(have), len(items), ws, "digester") if have
              else prompt(s.agent, "digester", ws))
    numbered = " ".join(f"{k}. {x}" for k, x in enumerate(steps, 1))
    return Fix(
        id="verbose-tests",
        title=f"Keep test output short ({len(items)} long test run{'s' if len(items) > 1 else ''})",
        why=("Test runs poured long output into the chat. Only the failures and the summary line are needed; the "
             "rest stayed in the chat and was sent again with every later message (an estimate: about "
             f"{round((1 - CUT) * 100)}% of the output is what matters)."),
        saving_usd=led.usd, saving_tokens=led.tokens, claims=led.claims, where=turns_where(sorted(turns)),
        turns=sorted(turns), items=items[:6], per_turn=led.per_turn(), calls=hit,
        how="Best practice: quiet test output, only the related tests, long runs through a helper. " + numbered,
        ask=f"Test runs here print too much. {numbered} Then: {helper}",
    )
