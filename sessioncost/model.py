"""The one in-memory shape every reader produces.

A reader for any coding agent (Claude Code today; Codex, Antigravity, Cursor later) turns that agent's own log
into a `Session`. Everything after the reader (cost, drill-down, fixes, the report) works on a `Session` only, so
a new agent needs a reader and nothing else.

Session -> Turns (each starts with something the user really typed) -> Calls (one model call each, counted once)
-> Actions (the tools a call asked for). Helpers (subagents) keep their own calls.

A number the log does not hold stays None; the report then says "not in this log" instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Usage:
    """Token counts of one model call, split by how each class is priced."""
    fresh_input: int = 0        # new input, not cached
    cache_write_5m: int = 0     # new input written to the 5-minute cache
    cache_write_1h: int = 0     # new input written to the 1-hour cache
    cache_read: int = 0         # input re-read from the cache
    output: int = 0             # everything the model wrote, hidden thinking included
    thinking: int | None = None  # the hidden-thinking share of `output`; None = the log does not separate it
    write_split_known: bool = True  # False: the log does not say which cache writes were 5-minute or 1-hour

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def new_input(self) -> int:
        return self.fresh_input + self.cache_write

    @property
    def context(self) -> int:
        """Everything sent to the model in this call."""
        return self.fresh_input + self.cache_write + self.cache_read


@dataclass
class Action:
    """One tool use a call asked for."""
    id: str
    tool: str                    # the agent's own tool name, e.g. "Read"
    kind: str                    # read | search | edit | write | command | web | helper | question | skill | plan | other
    label: str                   # plain words, e.g. "read `plan.md`"
    target: str = ""             # the file, command, query or url
    args_chars: int = 0          # size of what the model wrote into the call
    written_chars: int = 0       # text put into a file (write / edit)
    result_chars: int = 0        # size of what came back
    result_hash: str = ""        # fingerprint of the result (to spot an unchanged re-read); the text is not kept
    is_error: bool = False
    ts: datetime | None = None
    result_ts: datetime | None = None
    answer_text: str = ""        # question cards: the answers given


@dataclass
class Call:
    n: int                       # 1-based within its thread (main or one helper)
    msg_id: str
    agent: str                   # "main" or the helper's id
    model: str
    usage: Usage
    ts_first: datetime | None = None
    ts_last: datetime | None = None
    turn: int = 0                # the turn it belongs to
    text_chars: int = 0          # visible text written to the user
    first_text: str = ""         # the first sentence it wrote (for plain-words labels)
    thinking_chars: int = 0      # visible thinking text (usually empty: thinking is hidden)
    actions: list[Action] = field(default_factory=list)


@dataclass
class Event:
    """Something that entered the conversation that is not a model call: what the user typed, a command, a question
    card answer, or text the agent's harness injected (labelled, never shown as typed by the user)."""
    kind: str                    # typed | command | queued | answer | injected | instructions
    label: str                   # plain words: "you typed", "editor selection (added by the IDE)", ...
    text: str                    # cleaned text (short)
    chars: int                   # full size of what entered
    ts: datetime | None = None


@dataclass
class Turn:
    n: int
    kind: str                    # typed | command | queued | start (calls before any user message)
    text: str                    # what the user asked, cleaned
    ts: datetime | None = None
    waited_s: float = 0.0        # time the agent waited for the user before this turn started
    events: list[Event] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)


@dataclass
class Helper:
    id: str
    description: str
    agent_type: str
    calls: list[Call] = field(default_factory=list)
    turn: int = 0                # the main turn it ran in


@dataclass
class SetupPart:
    what: str
    chars: int


@dataclass
class Setup:
    """What the agent sends with every call before anyone says a word: system prompt, tool definitions, lists."""
    known: bool = False
    parts: list[SetupPart] = field(default_factory=list)
    tools: list[SetupPart] = field(default_factory=list)     # one entry per tool definition

    @property
    def chars(self) -> int:
        return sum(p.chars for p in self.parts)


@dataclass
class Fix:
    """One fix, with a saving measured from this session's own calls."""
    id: str
    title: str
    why: str                     # one or two plain sentences
    saving_usd: float
    saving_tokens: int
    where: str = ""              # "turn 4", "calls 3, 9, 12"
    turns: list[int] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    source: str = "built-in"     # which engine found it


@dataclass
class Session:
    agent: str                   # "claude-code"
    session_id: str
    source: str
    title: str = ""
    cwd: str = ""
    model: str = ""
    agent_version: str = ""
    started: datetime | None = None
    ended: datetime | None = None
    turns: list[Turn] = field(default_factory=list)
    helpers: list[Helper] = field(default_factory=list)
    setup: Setup = field(default_factory=Setup)
    own_cost_usd: float | None = None   # the agent's own running total, when its log carries one
    billed_per_token: bool = True       # False: the agent's plan never bills per token (money is API-equivalent)
    tokens_exact: bool = True           # False: the log has no token counts; they are estimated from text size
    notes: list[str] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)   # filled by the fix engines (see fixes.py)

    @property
    def calls(self) -> list[Call]:
        """Main-thread calls, in order."""
        return [c for t in self.turns for c in t.calls]

    @property
    def helper_calls(self) -> list[Call]:
        return [c for h in self.helpers for c in h.calls]

    @property
    def all_calls(self) -> list[Call]:
        return self.calls + self.helper_calls

    def actions(self, include_helpers: bool = True) -> list[Action]:
        cs = self.all_calls if include_helpers else self.calls
        return [a for c in cs for a in c.actions]
