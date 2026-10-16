---
name: sessioncost
description: (SessionCost {{VERSION}}) Show how many tokens each turn of THIS coding session sent, the heaviest turn, and the top fix to cut them (with what it cost). Use when the user asks where the tokens went, why the context is so big, what this session or chat cost, why it was expensive, or runs /sessioncost.
---

# SessionCost: where did this session's tokens go?

Run the local `sessioncost` tool on the current session. It reads the session's log on this machine (Claude Code,
Codex, Antigravity or Cursor), writes an HTML report and prints a short summary. It uses no AI model and sends
nothing unless the user presses "Send the summary to the fix engine" in the report.

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   {{RUN}} last
   ```

   If that fails because the program is missing, try `sessioncost last`. If that fails too, tell the user
   SessionCost is not installed and to install it from https://github.com/kish21/sessioncost#install, then stop. Do
   not run an installer yourself.

2. Show the user the printed summary exactly as printed, in a code block. Do not reword or recompute it.

3. The tool tries to open the report in the browser, but some agents (Cursor, for example) run commands where no
   browser can open, so do not say it is open. Add one line: "The full report: <path>" with the path from the summary
   as a clickable link (on its own, so the user can click it), then: "(if it did not open by itself, click the
   path)". Do not offer to open it. Only if the user says it did not open, open the file with the system's default
   opener (`start "" <path>` on Windows, `open <path>` on macOS, `xdg-open <path>` on Linux).

## Deleting old reports

Each session keeps one report in `~/.sessioncost/reports/`. If the summary ends with an "Old reports: ..." line, show
it as printed and do not ask anything about it. Only when the user asks to delete or clean old reports (for example
"/sessioncost clean"), run (with the same fallbacks as step 1):

```
{{RUN}} clean
```

It deletes every saved report except the latest and prints what it freed. Show that line.

## Updating SessionCost

Only when the user asks to update SessionCost (for example "/sessioncost update"), run (with the same fallbacks as
step 1):

```
{{RUN}} update
```

It updates every copy on this computer (the program, and the Claude Code, Codex or Cursor plugin) and prints each
step. Show its output as printed, then tell the user to restart the agent.

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- Never delete reports unless the user asked for it.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent. Antigravity is always labelled that way.
