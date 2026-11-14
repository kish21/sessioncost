---
name: sessioncost
description: (SessionCost {{VERSION}}) Show how many tokens each turn of THIS coding session sent, the heaviest turn, and the top fix to cut them (with what it cost), and fix that waste for the user. Use when the user asks where the tokens went, why the context is so big, what this session or chat cost, why it was expensive, runs /sessioncost or /sessioncost fix, or says it in plain words: "show my report", "fix my token waste", "fix it", "write a handover", "continue from handover", "undo the fix", "turn token tips on/off", "warn me at 150k tokens", "set my budget to $5", "show my prices", "update the prices", "which version do I have".
---

# SessionCost: where did this session's tokens go?

Run the local `sessioncost` tool on the current session. It reads the session's log on this machine (Claude Code,
Codex, Antigravity or Cursor) and writes an HTML report. It uses no AI model and sends nothing anywhere (only
`prices update`, when the user asks for it, fetches SessionCost's price list).

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   {{RUN}} last --brief
   ```

   If that fails because the program is missing, try `sessioncost last --brief`. If that fails too, tell the user
   SessionCost is not installed and to install it from https://github.com/kish21/sessioncost#install, then stop. Do
   not run an installer yourself.

2. It prints a short "What SessionCost is doing:" block, then `Report: <path>`. Reply with only: that block exactly as
   printed, in a code block; then "The full report: <path>" (the path as a clickable link, on its own
   line), then "(if it did not open by itself, click the path)". Add no other summary: the report has every number, and
   text in this chat is re-sent with every later message. Do not say it is open (some agents, Cursor for example,
   cannot open a browser) and do not offer to open it. Only if the user says it did not open, open the file with the
   system's default opener (`start "" <path>` on Windows, `open <path>` on macOS, `xdg-open <path>` on Linux).

   Only if the user asks for the numbers in the chat, run it again without `--brief` and show the printed summary
   exactly as printed, in a code block.

## Fix it for me

Every command below also comes in plain words (shown first): the user never has to type it. When you tell the user
how to do one of these, give the words to say first and the command in brackets.

- Only when the user asks to fix the waste ("fix my token waste", or "/sessioncost fix"), run `{{RUN}} fix --preview`
  and show its output as printed, then end with: Say "fix it" (or "fix 1 and 3") to apply these. Only after they say
  so, run `{{RUN}} fix -y` (add `--only <ids>` when they chose some of them) and show what it prints. Never run
  `fix -y` without it.
- If it says a file was not changed (it does not parse, for example), tell the user and stop there: never edit that
  file yourself, not even to repair it, unless the user asks you to in their own words.
- "/sessioncost fix learn" with nothing after it: write both lines yourself from this chat (what wasted tokens, one
  line; one rule that prevents it, one line), show them, and ask one question: "Record this rule?". Only after their
  yes, run `{{RUN}} fix learn "<what happened>" --rule "<the rule>"`. Never ask the user to write the lines.
- If what it prints asks you to write a handover, write it yourself in the file it names: the goal, what is done,
  files changed, tests run, what is next, and the "Other token waste" section it asks for (run the `fix learn` line
  it gives for each new one). Show it in your reply, then tell the user to start a new chat and say "continue from
  handover" there.
- "undo the fix" (or "/sessioncost fix --undo"): run `{{RUN}} fix --undo` and show the lines it prints.
- "turn token tips on", "... off" or "are token tips on?" (or "/sessioncost fix auto on", "... off" or "... auto"
  alone): run `{{RUN}} fix auto on`, `{{RUN}} fix auto off` or
  `{{RUN}} fix auto` and show the line it prints. While it is on, a SessionCost to-do list can arrive with the
  user's message: follow the instructions that come with it. Right after turning it on, ask once: "A handover is
  offered when a chat passes 100k tokens. Keep 100k, or another number?" If they give a number, run
  `{{RUN}} fix long-chat <number>` (for example 150k) and show the line it prints.
- "warn me at 150k tokens" (or "/sessioncost fix long-chat <number>"): run `{{RUN}} fix long-chat <number>` and show
  the line it prints.
- "set my budget to $5", "clear my budget" or "what is my budget?" (or "/sessioncost fix budget 5", "... off" or
  "... budget" alone): run `{{RUN}} fix budget 5`, `{{RUN}} fix budget off` or `{{RUN}} fix budget` and show the line
  it prints.
- "fix it" or "write a handover" after a SessionCost 💡 line: follow the instructions that came with that line.
- When the user says "continue from handover": run `{{RUN}} fix --handover`. It prints the handover file's path; read
  that file and carry on from its "what is next".

## Prices

- "show my prices" (or "/sessioncost prices"): run `{{RUN}} prices` and show its output as printed, in a code block.
- When the user gives their own prices in their words (for example "Opus: 4 input, 20 output"), run
  `{{RUN}} prices set <model> --input 4 --output 20` (add `--cache-read R` and `--cache-write W` only when they gave
  those) and show the lines it prints. Use the model name as the report or `prices` shows it. Prices are US dollars
  per million tokens: if they gave another unit, ask before converting.
- "/sessioncost prices import <file>": run `{{RUN}} prices import <file>` (a .csv with the columns model, input,
  output, cache_read, cache_write, or a .json) and show what it prints.
- "/sessioncost prices team <file>" or "... team off": run `{{RUN}} prices team <file>` (or `team off`) and show what
  it prints.
- Only when the user asks to update the prices ("update the prices", or "/sessioncost prices update"), run
  `{{RUN}} prices update` and show what it prints. It is the only command that goes online; never run it by yourself.

## Old reports, updates

- An "Old reports: ..." line: show it as printed and ask nothing. Only when the user asks to delete or clean old
  reports ("delete my old reports", or "/sessioncost clean"), run `{{RUN}} clean` and show the line it prints.
- Only when the user asks to update SessionCost ("update SessionCost", or "/sessioncost update"), run
  `{{RUN}} update`, show its output as printed, then tell the user to restart the agent.
- Only when the user asks which version they have ("which version do I have?", or "/sessioncost version"), run
  `{{RUN}} version` and show its output as printed, adding no advice of your own (add `--all` only when they ask for
  every copy).
- Only when the user asks to turn the report after each answer on or off ("turn the report after each answer off",
  or "/sessioncost auto off"), run
  `{{RUN}} auto on` or `{{RUN}} auto off` and show the line it prints. It is on by default.
- All of these take the same fallbacks as step 1.

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- Never delete reports unless the user asked for it.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent. Antigravity is always labelled that way.
