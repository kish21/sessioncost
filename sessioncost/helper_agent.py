"""Cheaper helpers a report tip asks the user's own agent to set up, in that agent's own format (owner, 2026-11-23/24):
the reader (routine reads, searches, test runs), the reviewer (a finished change, findings only) and the digester
(commands with long output, only the lines that matter). The agent fills in how its project runs tests, so one prompt
fits any project.

Firm words matter (owner, 2026-11-24, from MarkVid): with "may use the reader" and a plain description the agent kept
its default (avoid helpers) and did every read itself. So each helper's description starts with "Use proactively"
(Claude Code's sub-agent docs: the description decides delegation; "use proactively" encourages it) and its rule line
says when, with a clear line. Codex delegates only when its AGENTS.md or the user asks, so there the rule line is all.
When a helper already exists, the tip reads the user's files (workspace.py) and quotes the soft or conflicting lines
instead of setting up a second one.

Formats, from each agent's docs (2026-11-23): Claude Code .claude/agents/*.md (model: sonnet); Cursor
.cursor/agents/*.md (model: fast, readonly: true); Codex .codex/agents/*.toml (name, description,
developer_instructions, model); Antigravity .agents/agents/*.md (model: inherit | flash | pro); Copilot
.github/agents/*.agent.md (model; VS Code and the CLI read it, the cloud agent ignores it)."""
from __future__ import annotations

import re
from dataclasses import dataclass

RULES_FILE = {"claude-code": "CLAUDE.md", "codex": "AGENTS.md", "cursor": ".cursor/rules/sessioncost.mdc",
              "antigravity": "GEMINI.md", "copilot": ".github/copilot-instructions.md",
              "copilot-vscode": ".github/copilot-instructions.md"}
PROACTIVE = "Use proactively"
LIMITS = ("never edit files, commit or push; stop after about 15 steps and say what is unfinished; report only real "
          "output.")


@dataclass(frozen=True)
class Spec:
    name: str
    for_: str          # what the description says it is for
    job: str
    rule: str          # the firm line for the rules file


HELPERS = {
    "reader": Spec(
        "reader", "multi-step read-only work: routine reading, searching and test runs, not design or debugging",
        "Its job: read files, search code and run the tests or checks it is given, then reply with only what is "
        "needed (file:line references, failing test names, error lines), never whole files. Its rules: run only the "
        "tests it is given, never the full suite; " + LIMITS + " Add one line saying how this project runs its tests "
        "and checks (use what you know of this project).",
        "Helper agents: always use the reader agent on your own for read-only work that needs more than two quick "
        "lookups (several searches, large files, test runs); do one or two quick checks, design and debugging "
        "yourself."),
    "reviewer": Spec(
        "reviewer", "reviewing a finished change and sending back only a short list of findings",
        "Its job: read the change (git diff) and the files it touches, then reply with only the findings: file:line, "
        "the problem, why it matters, a one-line fix; no file contents, no praise. Its rules: " + LIMITS,
        "Reviews: use the reviewer agent on your own once per finished change, not after every edit; keep a review "
        "on the most expensive model only for security or design questions."),
    "digester": Spec(
        "digester", "running commands whose output may be long (builds, full test runs, big diffs, logs) and "
        "sending back only the lines that matter",
        "Its job: run the command it is given, save the full output to a file in the system temp folder, then reply "
        "with only the failures, errors, warnings and the summary line, plus that file's path. Its rules: " + LIMITS,
        "Long output: run commands whose output may be long (builds, full test runs, big diffs, logs) through the "
        "digester agent on your own; it sends back only the lines that matter."),
}
TAIL = "Any other helper agent only when I ask."
RULE = HELPERS["reader"].rule + " " + TAIL           # the line the reader tip adds (kept for older callers)


def _desc(spec: Spec) -> str:
    return f"a description that starts with \"{PROACTIVE} for {spec.for_}\""


def _file(agent: str, spec: Spec) -> str:
    n, d = spec.name, _desc(spec)
    return {
        "claude-code": f"`.claude/agents/{n}.md` with front matter `name: {n}`, {d}, `model: sonnet` and "
                       "`tools: Read, Grep, Glob, Bash`",
        "cursor": f"`.cursor/agents/{n}.md` with front matter `name: {n}`, {d}, `model: fast` and `readonly: true`",
        "codex": f"`.codex/agents/{n}.toml` with `name = \"{n}\"`, {d}, `model` set to a smaller, cheaper model "
                 "than this chat's, and the job and rules below in `developer_instructions`",
        "antigravity": f"`.agents/agents/{n}.md` with front matter `name: {n}`, {d}, `model: flash`, "
                       "`subagent: true`, `mainAgent: false` and `tools: [view_file, grep_search, run_command]`",
        "copilot": f"`.github/agents/{n}.agent.md` with front matter `name: {n}`, {d}, `model` set to a cheaper "
                   "model my Copilot plan offers, and tools for reading, searching and running commands only",
        "copilot-vscode": f"`.github/agents/{n}.agent.md` with front matter `name: {n}`, {d}, `model` set to a "
                          "cheaper model my Copilot plan offers, and tools for reading, searching and running "
                          "commands only",
    }.get(agent) or f"a helper agent file in your own format, named {n}, on a cheaper model, with {d}"


FILE = {a: _file(a, HELPERS["reader"]) for a in RULES_FILE}     # the reader's file per agent (helper_model.py)


def rules_file(agent: str) -> str:
    return RULES_FILE.get(agent, "your instruction file")


def prompt(agent: str, name: str = "reader", ws=None) -> str:
    """What the user pastes into the chat once: the agent writes the helper file and the rule that makes it used.
    With the user's files read (ws), an existing helper gets firm words instead of a second copy."""
    spec = HELPERS[name]
    if ws is not None and (have := ws.helper_file(name)):
        return make_stick(agent, ws.show(have), 0, ws, name)
    clash = "".join(f" Also {x}" for x in conflicts(ws, name)) if ws is not None else ""
    return (f"Set up a cheaper helper agent: {name}. Create {_file(agent, spec)}. {spec.job} Then add this line to "
            f"{rules_file(agent)}: \"{spec.rule} {TAIL}\"{clash} Show me both files when done.")


_SOFT = re.compile(r"\b(may|can|could|optional(ly)?|if you like|feel free|might)\b", re.I)
_BLOCK = re.compile(r"\b(helpers?|sub-?agents?|helper agents?)\b.*\b(only when I ask|never|do not|don't|avoid)\b", re.I)


def conflicts(ws, name: str) -> list[str]:
    """Sentences naming the lines in the user's instruction files that keep this helper unused."""
    out = []
    for ln in ws.find(_BLOCK):
        # "any OTHER helper only when I ask" already leaves room for the named ones: not a block
        if not re.search(rf"\b{name}\b|\bother\b|\bexcept\b", ln.text, re.I):
            out.append(f"{ln.quote()} keeps helpers you did not ask for from running: say there that the {name} "
                       "agent is the exception.")
    return out[:3]


def make_stick(agent: str, path: str, steps: int, ws=None, name: str = "reader") -> str:
    """The helper exists but the agent did the work itself: quote what keeps it unused and give the firm words."""
    spec = HELPERS[name]
    kind = {"reader": "read, search or test steps", "reviewer": "reviews on the most expensive model",
            "digester": "commands with long output"}[name]
    did = f"in this chat {steps} {kind} ran without it" if steps else "it is not used on its own"
    fixes, n = [], 0
    if ws is not None:
        p = ws.helper_file(name)
        desc = ws.helper_description(p) if p else ""
        if PROACTIVE.lower() not in desc.lower():
            fixes.append(f"`{path}`: its description" + (f" (\"{desc[:80]}\")" if desc else "")
                         + f" does not say when to use it. Start it with \"{PROACTIVE} for {spec.for_}\".")
        mention = ws.find(rf"\b{name}\b")
        soft = [ln for ln in mention if _SOFT.search(ln.text)]
        for ln in soft[:3]:
            fixes.append(f"{ln.quote()} is soft. Replace it with: \"{spec.rule}\"")
        if not mention:
            fixes.append(f"No instruction file says when to use it. Add to {rules_file(agent)}: \"{spec.rule}\"")
        fixes += conflicts(ws, name)
    else:
        fixes = [f"Its description should start with \"{PROACTIVE} for {spec.for_}\".",
                 f"{rules_file(agent)}, and any other instruction file that mentions the {name}, should say: "
                 f"\"{spec.rule}\" Replace softer wording such as \"may use\"."]
    if not fixes:
        return (f"This project already has a {name} helper ({path}), and its description and my rules already say "
                f"when to use it. Nothing to change in the files: use it on your own for {spec.for_}.")
    steps_txt = " ".join(f"{(n := n + 1)}. {x}" for x in fixes)
    return (f"This project already has a {name} helper ({path}), but {did}. Make it firm so you use it on your own: "
            f"{steps_txt} Do not create a second helper. Propose the exact changes file by file and wait for my yes "
            "before editing.")
