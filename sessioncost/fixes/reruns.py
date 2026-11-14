"""Check test-rerun: the same tests run again with nothing changed; and edits never tested."""
from __future__ import annotations

import re

from ..model import Fix, Session
from .common import Ledger, _where, helper_name, src, tok

TEST_CMD = re.compile(r"(?:^|[\s;&|(/\\])(?:pytest|py\.test|jest|vitest|mocha|rspec|phpunit|tox|nox|"
                      r"python3?\s+-m\s+(?:pytest|unittest)|go\s+test|cargo\s+test|dotnet\s+test|deno\s+test|bun\s+test|"
                      r"(?:npm|pnpm|yarn)\s+(?:run\s+)?test|make\s+(?:check|test)|(?:mvn|gradlew?)\s+(?:\S+\s+)*test)"
                      r"(?=$|[\s;&|)])", re.I)
CODE_EXT = re.compile(r"\.(py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|cs|cpp|cc|c|h|hpp|rb|php|swift|scala|vue|svelte|sql)$", re.I)


def is_test(a) -> bool:
    return a.kind == "command" and bool(TEST_CMD.search(a.target or ""))


def test_reruns(s: Session, P) -> Fix | None:
    """The same test command run again with nothing changed in between: no file written or edited, no other
    command. Its second result tells nothing new, and it was carried by every later call."""
    led, items, turns = Ledger(), [], set()
    hit_calls: list[tuple[str, int]] = []
    for calls in [s.calls] + [h.calls for h in s.helpers]:
        ran: set[str] = set()                     # test commands run since the last change
        for i, c in enumerate(calls):
            for a in c.actions:
                if a.kind in ("write", "edit") or (a.kind == "command" and not is_test(a)):
                    ran.clear()
                    continue
                if not is_test(a):
                    continue
                key = re.sub(r"\s+", " ", a.target).strip()
                if key not in ran:
                    ran.add(key)
                    continue
                led.carry(src(c, a), tok(a.result_chars), calls[i + 1:], P)
                turns.add(c.turn)
                hit_calls.append((c.agent, c.n))
                items.append(f"{a.label} again with nothing changed, call {c.n}"
                             + ("" if c.agent == "main" else f" of the helper {helper_name(s, c.agent)}"))
    if not items:
        return None
    return Fix(
        id="test-rerun", title="Don't run the tests again when nothing changed",
        why=(f"The tests were run {len(items)} more time{'s' if len(items) > 1 else ''} with no file changed since "
             "the last run, so the result could not be different. Each copy of the output was carried by every "
             "later call."),
        saving_usd=led.usd, saving_tokens=led.tokens, where=_where(turns), turns=sorted(turns), items=items[:8],
        per_turn=led.per_turn(), calls=hit_calls, paste=True, claims=led.claims,
        how="Do not re-run a command when no file has changed since the last run. Read the first result.")


def untested_edits(s: Session) -> dict | None:
    """Code the main agent changed after the last test run of the chat, with no test run after it. Only in a chat
    that ran the tests at least once (so the project has tests). Not a cost: shown above the fixes."""
    last, changed = None, []
    for c in s.calls:
        for a in c.actions:
            if is_test(a):
                last, changed = c, []
            elif a.kind in ("write", "edit") and last is not None:
                changed += [f for f in (a.target or "").splitlines() if CODE_EXT.search(f.strip())]
    if last is None or not changed:
        return None
    names = list(dict.fromkeys(re.split(r"[\\/]", f.strip())[-1] for f in changed))
    return {"id": "untested-edits", "title": "Code was changed after the last test run",
            "times": [], "refused": 0,
            "why": (f"{', '.join(names[:5])}{' and more' if len(names) > 5 else ''} changed after the tests last ran "
                    f"(turn {last.turn}), and the tests were not run again"),
            "note": "Not a cost, but untested changes are how bugs get through.",
            "how": "After changing code, run the tests before you say the work is done."}
