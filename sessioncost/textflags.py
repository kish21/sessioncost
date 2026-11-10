"""Yes/no facts about a tool result's text, made on your computer. Only the flag names leave this module; the text
is never kept.

    too_large   the tool refused to return the output: too big ("Output too large", "exceeds maximum allowed tokens")
    truncated   the tool cut the output and said so ("<truncated 120 lines>", "[... 4000 tokens truncated ...]")
    blocked     a safety hook, permission rule or the agent's own reviewer (Codex) stopped the tool use
    rejected    you turned the tool use down when the agent asked

Only the start and end of a result are looked at, where agents put these notices, so a file that merely mentions
the words (like this one) does not count.

Signs of a prompt injection are looked for in the whole result (up to SCAN characters): text written to steer the
agent rather than inform it. Like the others, only the flag is kept; injection_quote gives the words around it for
the live warning in the chat, so you can judge it (never logged).

    injection   "ignore previous instructions", "you are now…", "new instructions:", fake <system> tags,
                "do not tell the user", "IMPORTANT for the AI/assistant/agent"
    hidden_text characters a person cannot see: Unicode tag characters, bidirectional overrides, or several
                zero-width characters (a lone one, as in an emoji, does not count)
    secret_value a value shaped like a key or token (an API key, a GitHub token, a private key): it reached the
                model and the chat's log

Text that speaks to the agent reading it (a note to whoever reads it, an assistant among them, or a line opening with
AI agents and a colon) is not an injection sign on its own, but the live guard treats what follows it as after
untrusted content (addresses_agent).
"""
from __future__ import annotations

import hashlib
import re

HEAD, TAIL = 400, 300

PATTERNS = {
    "too_large": re.compile(r"^\s*(?:<persisted-output>\s*)?(?:error:\s*)?(?:output too large|"
                            r".{0,80}exceeds maximum allowed tokens)", re.I),
    "truncated": re.compile(r"<truncated \d+ (?:bytes|lines|chars)>|\[truncated: partial view|"
                            r"\[\.\.\.\s*\d[\d,]* (?:lines|tokens|chars|bytes) (?:truncated|omitted)|"
                            r"…\s*\d[\d,]* tokens truncated|output (?:was )?truncated", re.I),
    "blocked": re.compile(r"^\s*(?:\w+:\w+ )?hook error:|^\s*(?:pre|post)tooluse:\w+ hook error|blocked by (?:a |the )?"
                          r"hook|permission to use \S+ (?:has been|was) denied|^\s*access to .{0,200} was blocked\.|"
                          r"action was rejected due to unacceptable risk",          # Codex's safety reviewer
                          re.I),
    "rejected": re.compile(r"^\s*the user doesn't want to proceed with this tool use|^\s*user rejected|"
                           r"^\s*rejected by (?:the )?user|^\s*aborted by user", re.I),
}


SCAN = 400_000
_INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+(?:(?:all|any|the|your|of\s+the)\s+)*(?:previous|prior|above|earlier|preceding|"
    r"system|original)\s+(?:instructions?|prompts?|rules|messages?|directions|guidelines)\b|"
    r"\byou\s+are\s+now\s+(?:a|an|in|the)\s|\bnew\s+(?:system\s+)?instructions?\s*:|"
    r"<\s*/?\s*(?:system|im_start|im_end)\s*>|\bBEGIN\s+(?:SYSTEM|ADMIN|DEVELOPER)\s+(?:PROMPT|INSTRUCTIONS?|MESSAGE)\b|"
    r"\b(?:do\s+not|don'?t|never)\s+(?:tell|inform|show|mention\s+(?:this\s+)?to)\s+the\s+user\b|"
    r"\b(?:IMPORTANT|URGENT|ATTENTION)\b[\s:!-]{0,4}(?:to|for)\s+(?:the\s+)?(?:AI|assistant|agent|model|LLM|Claude)\b", re.I)
_HIDDEN_ALWAYS = re.compile("[\U000E0000-\U000E007F‪-‮⁦-⁩]")
_ZERO_WIDTH = re.compile("[​‌⁠⁡⁢⁣⁤]|(?<=.)﻿", re.S)


# A character set in a pattern written in code, as in re.compile("[a-b]"): hidden characters there are what a
# detector looks for (SessionCost's own textflags.py), not a hidden message, which is a long run of them.
_PATTERN_SET = re.compile(r"(?<=[\"'(])\[[^\]\n]{1,40}\]")


def _no_pattern_sets(t: str) -> str:
    return _PATTERN_SET.sub(" ", t)


def injection_quote(txt: str, width: int = 120) -> str:
    """The words that made injection_signs say yes, about `width` characters around them, hidden characters shown as
    [hidden character]. Only for the warning in the chat, so you can judge it: never logged."""
    t = txt[:SCAN] if txt else ""
    h = _no_pattern_sets(t)
    m = _INJECTION.search(t) or _HIDDEN_ALWAYS.search(h) or _ZERO_WIDTH.search(h)
    t = t if _INJECTION.search(t) else h
    if not m:
        return ""
    pad = max(20, (width - len(m.group(0))) // 2)
    s, e = max(0, m.start() - pad), min(len(t), m.end() + pad)
    q = _ZERO_WIDTH.sub("[hidden character]", _HIDDEN_ALWAYS.sub("[hidden character]", t[s:e]))
    q = re.sub(r"(?:\[hidden character\])+", "[hidden character]", q)
    q = re.sub(r'\\([nrt"\\])', lambda x: {"n": " ", "r": " ", "t": " ", '"': "'"}.get(x.group(1), "\\"), q)   # JSON escapes
    q = " ".join(q.replace("`", "'").replace('"', "'").split())
    return ("…" if s else "") + q + ("…" if e < len(t) else "")


_INSTRUCTIONS = re.compile(r"(?:^|[\\/])(?:CLAUDE|AGENTS|GEMINI|SKILL)\.md$|(?:^|[\\/])\.cursorrules$|"
                           r"(?:^|[\\/])\.(?:claude|cursor[\\/]+rules|github[\\/]+instructions|agents)[\\/]|"
                           r"(?:^|[\\/])\.github[\\/]+copilot-instructions\.md$", re.I)


def instruction_file(path: str) -> bool:
    """A file written for the agent to follow (CLAUDE.md, AGENTS.md, rules folders): orders are what it holds, and the
    agent loads it as instructions anyway, so its words are no sign of an injection. Hidden characters still are."""
    return bool(_INSTRUCTIONS.search((path or "").strip()))


_TEST_FILE = re.compile(r"(?:^|[\\/])(?:tests?|__tests__|spec)[\\/]|(?:^|[\\/])test_[^\\/]*\.\w+$|"
                        r"_test\.\w+$|\.(?:test|spec)\.\w+$", re.I)


def test_file(path: str) -> bool:
    """A test file: tests quote attack text on purpose (a guard's, a filter's), so its words are no sign of an
    injection. Only for files inside the project (the caller checks); hidden characters still count."""
    return bool(_TEST_FILE.search((path or "").strip().strip("'\"")))


def injection_signs(txt: str) -> list[str]:
    """Yes/no signs that a result tries to steer the agent (see the module notes)."""
    if not txt:
        return []
    t = txt[:SCAN]
    out = []
    if _INJECTION.search(t):
        out.append("injection")
    h = _no_pattern_sets(t)
    if _HIDDEN_ALWAYS.search(h) or len(_ZERO_WIDTH.findall(h)) >= 3:
        out.append("hidden_text")
    return out


_TO_AGENT = re.compile(
    r"\b(?:whoever|anyone)\s+(?:who\s+)?(?:reads?|reading|sees?)\s+(?:this|these)\b|"
    r"\bif\s+you\s+are\s+an?\s+(?:AI|assistant|agent|LLM|(?:large\s+)?language\s+model|bot)\b|"
    r"\b(?:including|especially)\s+(?:an?|any)\s+(?:AI|assistant|agent|LLM|coding\s+agent)\b|"
    r"\b(?:dear|attention|note\s+to)\s+(?:the\s+|any\s+|all\s+)?(?:AI|assistants?|agents?|LLMs?)\b|"
    r"^\W*(?:AI\s+agents?|AI\s+assistants?|coding\s+agents?)\s*[:,]", re.I | re.M)


def addresses_agent(txt: str) -> bool:
    """Text written to the agent reading it, not to a person (see the module notes)."""
    return bool(txt) and bool(_TO_AGENT.search(txt[:SCAN]))


def secret_value(txt: str) -> bool:
    from .risks import _SECRET_VALUE
    return bool(txt) and bool(_SECRET_VALUE.search(txt[:SCAN]))


def text_flags(txt: str) -> list[str]:
    if not txt:
        return []
    head, tail = txt[:HEAD], txt[-TAIL:]
    out = injection_signs(txt) + (["secret_value"] if secret_value(txt) else [])
    for name, p in PATTERNS.items():
        if p.search(head) or (name == "truncated" and p.search(tail)):
            out.append(name)
    return out


# Parts of a tool result that change on every run even when the file or output is the same: Codex's wall time,
# chunk and session ids, and run times. They are left out of the fingerprint, so an unchanged file read twice
# matches.
VOLATILE = re.compile(r"wall[ _]time(?:_seconds)?\"?\s*[:=]?\s*[\d.]+(?:\s*seconds?)?|\"?(?:chunk_id|session_id)\"?\s*[:=]\s*"
                      r"\"?[\w-]+\"?|(?:took|elapsed|duration)\s*[:=]?\s*[\d.]+\s*m?s", re.I)


def fingerprint(txt: str) -> str:
    """A short hash of a result, the same for the same output on another run. The text itself is not kept."""
    return hashlib.sha1(VOLATILE.sub("", txt).encode("utf-8", "replace")).hexdigest()[:16]
