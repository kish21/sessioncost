---
name: sessioncost
description: (SessionCost 1.1.0-rc.1) Show how many tokens each turn of THIS coding session sent, the heaviest turn, and the top fix to cut them (with what it cost), and fix that waste for the user. Use when the user asks where the tokens went, why the context is so big, what this session or chat cost, why it was expensive, runs /sessioncost or /sessioncost fix, or says "continue from handover".
---

# SessionCost: where did this session's tokens go?

Run SessionCost, which came with this plugin, on the current session. It reads the session's log on this machine and
writes an HTML report. It uses no AI model and sends nothing anywhere.

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   python "${CLAUDE_PLUGIN_ROOT}/run.py" last --brief
   ```

   `run.py` is in this plugin's root folder: two folders above this SKILL.md. In Claude Code that is
   `${CLAUDE_PLUGIN_ROOT}`; in Codex or Cursor (where that variable is not set) use the full path of the folder two
   levels above this file instead.

   If `python` is not found (or opens the Microsoft Store, or is older than 3.10), try `py` in its place (Windows),
   then `python3`. If none of them works, get a Python through uv (it puts one in its own folder and changes nothing
   else), and do it yourself, without sending the user to another way of installing:

   1. `uv python find --no-project ">=3.10"`. If it prints a path, use that path in place of `python` everywhere in
      this skill, and skip to step 1 above. If `uv` is not found, try `~/.local/bin/uv` the same way.
   2. No uv: install it. Windows (PowerShell):
      `powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"`.
      Mac or Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`. It goes to `~/.local/bin`.
   3. `uv python install 3.12` (the uv you found, or `~/.local/bin/uv` after step 2), then
      `uv python find --no-project ">=3.10"` the same way, and use the path it prints in place of `python`.

   Tell the user in one line that you fetched Python for SessionCost through uv.

2. It prints `Report: <path>`. Reply with only: "The full report: <path>" (the path as a clickable link, on its own
   line), then "(if it did not open by itself, click the path)". Add no summary: the report has every number, and
   text in this chat is re-sent with every later message. Do not say it is open (some agents, Cursor for example,
   cannot open a browser) and do not offer to open it. Only if the user says it did not open, open the file with the
   system's default opener (`start "" <path>` on Windows, `open <path>` on macOS, `xdg-open <path>` on Linux).

   Only if the user asks for the numbers in the chat, run it again without `--brief` and show the printed summary
   exactly as printed, in a code block.

## Fix it for me

- Only when the user asks to fix the waste (for example "/sessioncost fix"), run
  `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix --preview` and show its output as printed, then ask "Shall I apply
  these?". Only after their yes, run `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix -y` (add `--only <ids>` when they
  chose some of them) and show what it prints. Never run `fix -y` without their yes.
- If it says a file was not changed (it does not parse, for example), tell the user and stop there: never edit that
  file yourself, not even to repair it, unless the user asks you to in their own words.
- "/sessioncost fix learn" with nothing after it: write both lines yourself from this chat (what wasted tokens, one
  line; one rule that prevents it, one line), show them, and ask one question: "Record this rule?". Only after their
  yes, run `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix learn "<what happened>" --rule "<the rule>"`. Never ask the
  user to write the lines.
- If what it prints asks you to write a handover, write it yourself in the file it names: the goal, what is done,
  files changed, tests run, what is next, and the "Other token waste" section it asks for (run the `fix learn` line
  it gives for each new one). Show it in your reply, then tell the user to start a new chat and say "continue from
  handover" there.
- "/sessioncost fix --undo": run `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix --undo` and show the lines it prints.
- "/sessioncost fix auto on", "... off" or "... auto" alone: run `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix auto on`,
  `... fix auto off` or `... fix auto` and show the line it prints. While it is on, a SessionCost to-do list can
  arrive with the user's message: follow the instructions that come with it. Right after turning it on, ask once:
  "A handover is offered when a chat passes 100k tokens. Keep 100k, or another number?" If they give a number, run
  `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix long-chat <number>` (for example 150k) and show the line it prints.
- "/sessioncost fix long-chat <number>": run `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix long-chat <number>` and show
  the line it prints.
- When the user says "continue from handover": run `python "${CLAUDE_PLUGIN_ROOT}/run.py" fix --handover`. It prints
  the handover file's path; read that file and carry on from its "what is next".

## Old reports, updates

- An "Old reports: ..." line: show it as printed and ask nothing. Only when the user asks to delete or clean old
  reports (for example "/sessioncost clean"), run `python "${CLAUDE_PLUGIN_ROOT}/run.py" clean` and show the line it
  prints.
- Only when the user asks to update SessionCost (for example "/sessioncost update"), run
  `python "${CLAUDE_PLUGIN_ROOT}/run.py" update`, show its output as printed, then tell the user to restart the agent.
- Only when the user asks which version they have (for example "/sessioncost version"), run
  `python "${CLAUDE_PLUGIN_ROOT}/run.py" version` and show its output as printed, adding no advice of your own (add
  `--all` only when they ask for every copy).
- Only when the user asks to turn the report after each answer on or off (for example "/sessioncost auto off"), run
  `python "${CLAUDE_PLUGIN_ROOT}/run.py" auto on` or `... auto off` and show the line it prints. It is on by default.
- All of these take the same fallbacks as step 1.

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- Never delete reports unless the user asked for it.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent.
