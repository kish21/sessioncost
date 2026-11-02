---
name: sessioncost
description: (SessionCost {{VERSION}}) Show how many tokens each turn of THIS coding session sent, the heaviest turn, and the top fix to cut them (with what it cost). Use when the user asks where the tokens went, why the context is so big, what this session or chat cost, why it was expensive, or runs /sessioncost.
---

# SessionCost: where did this session's tokens go?

Run the local `sessioncost` tool on the current session. It reads the session's log on this machine (Claude Code,
Codex, Antigravity or Cursor) and writes an HTML report. It uses no AI model and sends nothing anywhere.

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   {{RUN}} last --brief
   ```

   If that fails because the program is missing, try `sessioncost last --brief`. If that fails too, tell the user
   SessionCost is not installed and to install it from https://github.com/kish21/sessioncost#install, then stop. Do
   not run an installer yourself.

2. It prints `Report: <path>`. Reply with only: "The full report: <path>" (the path as a clickable link, on its own
   line), then "(if it did not open by itself, click the path)". Add no summary: the report has every number, and
   text in this chat is re-sent with every later message. Do not say it is open (some agents, Cursor for example,
   cannot open a browser) and do not offer to open it. Only if the user says it did not open, open the file with the
   system's default opener (`start "" <path>` on Windows, `open <path>` on macOS, `xdg-open <path>` on Linux).

   Only if the user asks for the numbers in the chat, run it again without `--brief` and show the printed summary
   exactly as printed, in a code block.

## Old reports, updates

- An "Old reports: ..." line: show it as printed and ask nothing. Only when the user asks to delete or clean old
  reports (for example "/sessioncost clean"), run `{{RUN}} clean` and show the line it prints.
- Only when the user asks to update SessionCost (for example "/sessioncost update"), run `{{RUN}} update`, show its
  output as printed, then tell the user to restart the agent.
- Only when the user asks which version they have (for example "/sessioncost version"), run `{{RUN}} version` and
  show its output as printed, adding no advice of your own (add `--all` only when they ask for every copy).
- Only when the user asks to turn the report after each answer on or off (for example "/sessioncost auto off"), run
  `{{RUN}} auto on` or `{{RUN}} auto off` and show the line it prints. It is on by default.
- All four take the same fallbacks as step 1.

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- Never delete reports unless the user asked for it.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent. Antigravity is always labelled that way.
