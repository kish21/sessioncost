"""Yes/no facts about a tool result's text, made on your computer. Only the flag names leave this module; the text
is never kept.

    too_large   the tool refused to return the output: too big ("Output too large", "exceeds maximum allowed tokens")
    truncated   the tool cut the output and said so ("<truncated 120 lines>", "[... 4000 tokens truncated ...]")
    blocked     a safety hook or permission rule stopped the tool use
    rejected    you turned the tool use down when the agent asked

Only the start and end of a result are looked at, where agents put these notices, so a file that merely mentions
the words (like this one) does not count.
"""
from __future__ import annotations

import re

HEAD, TAIL = 400, 300

PATTERNS = {
    "too_large": re.compile(r"^\s*(?:<persisted-output>\s*)?(?:error:\s*)?(?:output too large|"
                            r".{0,80}exceeds maximum allowed tokens)", re.I),
    "truncated": re.compile(r"<truncated \d+ (?:bytes|lines|chars)>|\[truncated: partial view|"
                            r"\[\.\.\.\s*\d[\d,]* (?:lines|tokens|chars|bytes) (?:truncated|omitted)|"
                            r"…\s*\d[\d,]* tokens truncated|output (?:was )?truncated", re.I),
    "blocked": re.compile(r"^\s*(?:\w+:\w+ )?hook error:|^\s*(?:pre|post)tooluse:\w+ hook error|blocked by (?:a |the )?"
                          r"hook|permission to use \S+ (?:has been|was) denied|^\s*access to .{0,200} was blocked\.",
                          re.I),
    "rejected": re.compile(r"^\s*the user doesn't want to proceed with this tool use|^\s*user rejected|"
                           r"^\s*rejected by (?:the )?user|^\s*aborted by user", re.I),
}


def text_flags(txt: str) -> list[str]:
    if not txt:
        return []
    head, tail = txt[:HEAD], txt[-TAIL:]
    out = []
    for name, p in PATTERNS.items():
        if p.search(head) or (name == "truncated" and p.search(tail)):
            out.append(name)
    return out
