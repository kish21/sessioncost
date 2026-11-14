"""Best practices: one entry per check in sessioncost/fixes, in our own words.

Each entry says why that waste costs tokens and what to do about it. `fix` shows both lines per item, the in-chat tip
adds the short clause to its top item, and `page()` writes the help-site page with the full list.
Owner rule (2026-11-13): no links to vendor pages and no pointing to vendor usage or cost tools. Naming an action the
user takes (a fresh chat, /compact, /model) is fine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Practice:
    name: str    # the habit, as a heading
    why: str     # why it costs tokens
    do: str      # what to do instead
    short: str   # one clause for the in-chat tip (no ";", the tip joins items with it)


PRACTICES: dict[str, Practice] = {
    "long-chat": Practice(
        "Start a fresh chat for each new task",
        "Every message sends the whole chat again, so each new question in a long chat pays for all the old ones.",
        "When a task is done, or after a long break, ask the agent for a short handover (goal, what is done, what "
        "is next) and start a fresh chat from it.",
        "every message re-sends the whole chat, so a fresh chat with a handover is cheaper"),
    "cache-break": Practice(
        "Keep the prompt cache warm",
        "The agent keeps the chat so far in a short-lived cache (minutes, or about an hour on some plans) and "
        "re-reads it at a small part of the price. After a long pause, or when the model, tools or settings change "
        "mid-chat, the cache is gone and the whole chat is sent again at full price.",
        "Finish a step before a long pause. After the pause, start a fresh chat from a handover instead of carrying "
        "on. Pick the model, tools and settings at the start of a chat and keep them.",
        "after a pause or a mid-chat change the cache is gone and the whole chat is paid in full"),
    "repeated-reads": Practice(
        "Read a file once",
        "A file the agent already read is still in the chat. Reading it again adds a second copy, and both copies "
        "are sent with every later message.",
        "Ask the agent to refer back to what it read, and re-read only after the file changed. For big files, read "
        "the lines needed, not the whole file.",
        "a second read adds a second copy that rides along with every later message"),
    "repeated-writes": Practice(
        "Edit, don't rewrite",
        "Writing a whole file again sends every line of it, even when only a few lines changed, and the new copy "
        "stays in the chat.",
        "Ask for small edits to the lines that change. Rewrite a file whole only when most of it changes.",
        "a full rewrite sends every line again for a few changed ones"),
    "error-loop": Practice(
        "Stop after two failures and find the cause",
        "Each retry of a failing step sends the chat again, and the error output piles up in it. The same step "
        "rarely works on the third try without a change.",
        "After a step fails twice, have the agent stop, read the error and change the approach, or ask you.",
        "each retry re-sends the chat and piles up error output"),
    "helper-polling": Practice(
        "Let helpers report back",
        "Each time the main agent checks on a helper, it sends the whole main chat again just to learn 'not done "
        "yet'.",
        "Start helpers and wait for them to report when they finish. Check only if one seems stuck.",
        "each check on a helper re-sends the whole main chat"),
    "test-rerun": Practice(
        "Run tests again only after a change",
        "Running the same tests with nothing changed gives the same result, and its output is added to the chat "
        "again. Edits that are never tested cost a second round later.",
        "Run the tests that touch a change once after making it. Run the full suite before a commit, not after "
        "every step.",
        "the same run gives the same result and adds its output again"),
    "confirm-turn": Practice(
        "Say yes once, up front",
        "When the agent stops only to ask 'shall I go ahead?', your 'yes' starts a new turn that sends the whole "
        "chat again.",
        "Say in your request what the agent may do without asking (for example: edit and test, but ask before "
        "pushing), so it only stops for real decisions.",
        "a stop just to ask for a yes costs a full extra turn"),
    "unused-tools": Practice(
        "Turn off tools a project does not use",
        "Every connected tool server sends its tool list with every message, used or not.",
        "Keep on only the servers and connectors this project uses. Turn others on in the projects that need them.",
        "every connected tool rides along with every message, used or not"),
    "unused-builtin": Practice(
        "Switch off built-in tools you never use",
        "The agent's own big tools are described on every message, even in chats that never call them.",
        "Switch off the built-in tools your work never uses. You can switch them on again any time.",
        "their descriptions ride along with every message"),
    "stale-context": Practice(
        "Don't let old output linger",
        "A big result the agent no longer needs (a long log, a big file) stays in the chat and is sent with every "
        "later message.",
        "When a step that produced big output is done, start a fresh chat from a handover, or compact the chat "
        "(/compact) if you carry on right away.",
        "old big output is still sent with every later message"),
    "big-results": Practice(
        "Keep tool results small",
        "One huge result (a lockfile, build output, a full log) fills the chat at once and is sent with every later "
        "message.",
        "Read only the lines needed, search instead of reading whole files, and cut long output to its head and "
        "tail. Leave lockfiles and build folders out.",
        "one huge result is sent again with every later message"),
    "startup-bloat": Practice(
        "Keep always-on instructions short",
        "Instruction files, always-on rules and skill lists are sent at the start of every message. Every extra "
        "line there is paid on every message of every chat.",
        "Keep those files short and to the point. Move details the agent needs only sometimes into separate files "
        "it reads when needed.",
        "always-on text is paid on every message of every chat"),
    "images": Practice(
        "Use screenshots sparingly",
        "An image costs many tokens, and once it is in the chat it is sent with every later message.",
        "Paste text (an error, a log line) instead of a screenshot when you can. After a few images, start a fresh "
        "chat from a handover.",
        "an image is sent again with every later message"),
    "long-output": Practice(
        "Ask for short answers",
        "Long replies and heavy hidden thinking are output tokens, the most expensive kind, and the reply then "
        "stays in the chat.",
        "Ask for short answers and summaries. Use deep thinking for hard problems, not for routine steps.",
        "output is the most expensive kind of token and stays in the chat"),
    "helper-rereads": Practice(
        "Give helpers what the main agent already knows",
        "A helper starts with an empty chat, so it reads again the files the main agent already read.",
        "When starting a helper, name the files and lines it needs, or pass the facts it needs in the request.",
        "a helper reads again what the main agent already read"),
    "model-choice": Practice(
        "Use a smaller model for routine steps",
        "Reading files, searching and running quick commands cost the same top-tier price per token as hard design "
        "work, though a smaller model does them just as well.",
        "Pick the model at the start of a chat for the work it holds, or hand routine steps to a helper on a "
        "smaller model. Switching models mid-chat breaks the prompt cache.",
        "routine reads and searches do not need the top-tier price"),
}


def get(fix_id: str) -> Practice | None:
    return PRACTICES.get(fix_id)


def page() -> str:
    """The help-site page (Starlight markdown) with every entry, in the order of PRACTICES."""
    out = ["---", "title: Token best practices",
           "description: Why each kind of token waste costs, and what to do about it.", "---", "",
           "SessionCost checks every chat for the kinds of waste below. When it finds one, the fix list "
           "(`sessioncost fix`) shows these same two lines for it: why it costs, and the best practice.", ""]
    for fid, p in PRACTICES.items():
        out += [f"## {p.name}", "", f"**Why it costs:** {p.why}", "", f"**Best practice:** {p.do}", "",
                f"Check: `{fid}`", ""]
    return "\n".join(out)
