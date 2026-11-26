---
name: sessioncost
description: (SessionCost 1.2.9) Show how many tokens each turn of THIS coding session sent, the heaviest turn, and the top fix to cut them (with what it cost), and fix that waste for the user. Use when the user asks where the tokens went, why the context is so big, what this session or chat cost, why it was expensive, runs /sessioncost or /sessioncost fix, or says it in plain words: "show my report", "fix my token waste", "fix it", "write a handover", "continue from handover", "undo the fix", "turn token tips on/off", "warn me at 150k tokens", "set my budget to $5", "show my prices", "update the prices", "which version do I have", "show me the SessionCost help", "what can SessionCost do", or asks how to do something with SessionCost.
---

# SessionCost: where did this session's tokens go?

Run SessionCost, which came with this plugin, on the current session. It reads the session's log on this machine and
writes an HTML report. It uses no AI model and sends nothing anywhere (only `prices update`, when the user asks for
it, fetches SessionCost's price list).

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   <run> last --brief
   ```

   `<run>` is SessionCost's starter in this plugin's root folder: two folders above this SKILL.md (in Claude Code
   `${CLAUDE_PLUGIN_ROOT}`; in Codex or Cursor use the full path of that folder). Windows: `run.cmd`, run as
   `& "<folder>\run.cmd"` in PowerShell or `"<folder>/run.cmd"` in bash. Mac or Linux: `sh "<folder>/run.sh"`. Use
   `<run>` for every command in this skill. It finds Python by itself and fetches one through uv when there is none:
   never look for Python, `py` or uv yourself.

   Every SessionCost command writes to `~/.sessioncost` in the user's home folder. If your commands run in a sandbox
   that cannot write there (Codex does), ask for permission to run outside it on the FIRST try of each command: a
   try inside the sandbox only fails.

   Run each command once. It finishes in seconds but can take up to a minute: if it is still running, wait for it and
   read its output; never start it again, and never run it a second time to check. If it says SessionCost needs
   Python, or that it could not write to its folder (`~/.sessioncost`), your sandbox stopped it: ask the user once
   for permission and run the same command again with it. If it fails again, show the user the lines it printed and
   stop. Do not try other ways or other folders.

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

- Only when the user asks to fix the waste ("fix my token waste", or "/sessioncost fix"), run
  `<run> fix --preview` and show its output as printed, then end with: Say "fix it"
  (or "fix 1 and 3") to apply these. Only after they say so, run `<run> fix -y` (add
  `--only <ids>` when they chose some of them) and show what it prints. Never run `fix -y` without it.
- If it says a file was not changed (it does not parse, for example), tell the user and stop there: never edit that
  file yourself, not even to repair it, unless the user asks you to in their own words.
- "/sessioncost fix learn" with nothing after it: write both lines yourself from this chat (what wasted tokens, one
  line; one rule that prevents it, one line), show them, and ask one question: "Record this rule?". Only after their
  yes, run `<run> fix learn "<what happened>" --rule "<the rule>"`. Never ask the
  user to write the lines.
- If what it prints asks you to write a handover, write it yourself in the file it names: the goal, what is done,
  files changed, tests run, what is next, and the "Other token waste" section it asks for (run the `fix learn` line
  it gives for each new one). Show it in your reply, then tell the user to start a new chat and say "continue from
  handover" there.
- "undo the fix" (or "/sessioncost fix --undo"): run `<run> fix --undo` and show
  the lines it prints.
- "turn token tips on", "... off" or "are token tips on?" (or "/sessioncost fix auto on", "... off" or "... auto"
  alone): run `<run> fix auto on`,
  `... fix auto off` or `... fix auto` and show the line it prints. While it is on, a SessionCost to-do list can
  arrive with the user's message: follow the instructions that come with it. Right after turning it on, ask once:
  "A handover is offered when a chat passes 200k tokens. Keep 200k, or another number, like 1m?" If they give a number, run
  `<run> fix long-chat <number>` (for example 1m) and show the line it prints.
- "warn me at 150k tokens" (or "/sessioncost fix long-chat <number>"): run
  `<run> fix long-chat <number>` and show the line it prints.
- "open the fresh chat for me", "stop opening the fresh chat" or "is the fresh chat on?" (or "/sessioncost fix
  fresh-chat on", "... off" or "... fresh-chat" alone): run `<run> fix fresh-chat on`, `... off` or `... fresh-chat`
  and show the line it prints.
- "set my budget to $5", "clear my budget" or "what is my budget?" (or "/sessioncost fix budget 5", "... off" or
  "... budget" alone): run `<run> fix budget 5`, `... fix budget off` or
  `... fix budget` and show the line it prints.
- "fix it" or "write a handover" after a SessionCost 💡 line: follow the instructions that came with that line.
- When the user says "continue from handover": run `<run> fix --handover`. It prints
  the handover file's path; read that file and carry on from its "what is next".

## Prices

- "show my prices" (or "/sessioncost prices"): run `<run> prices` and show its output as printed, in a code
  block.
- When the user gives their own prices in their words (for example "Opus: 4 input, 20 output"), run
  `<run> prices set <model> --input 4 --output 20` (add `--cache-read R` and
  `--cache-write W` only when they gave those) and show the lines it prints. Use the model name as the report or
  `prices` shows it. Prices are US dollars per million tokens: if they gave another unit, ask before converting.
- "/sessioncost prices import <file>": run `<run> prices import <file>` (a .csv with
  the columns model, input, output, cache_read, cache_write, or a .json) and show what it prints.
- "/sessioncost prices team <file>" or "... team off": run `<run> prices team <file>`
  (or `team off`) and show what it prints.
- Only when the user asks to update the prices ("update the prices", or "/sessioncost prices update"), run
  `<run> prices update` and show what it prints. It is the only command that goes
  online; never run it by yourself.

## Old reports, updates

- An "Old reports: ..." line: show it as printed and ask nothing. Only when the user asks to delete or clean old
  reports ("delete my old reports", or "/sessioncost clean"), run `<run> clean` and show the line it
  prints.
- Only when the user asks to update SessionCost ("update SessionCost", or "/sessioncost update"), run
  `<run> update`, show its output as printed, then tell the user to restart the agent. Never re-run the `irm | iex`
  or `curl | sh` installer to update (LiveGuard blocks piped shell scripts). If updating via `pip install -U sessioncost`
  right after a new release while LiveGuard is on, LiveGuard may pause once for a fresh release (`fresh_release`) — that
  is expected; the user can confirm it or run the command in their own terminal.
- When the user asks to remove or uninstall SessionCost: never run it yourself (it asks a question only a person can
  answer in a terminal). Tell them to type this in a terminal (PowerShell, Terminal), not in the chat, with the real
  folder: Windows `& "<folder>\run.cmd" uninstall`, Mac or Linux `sh "<folder>/run.sh" uninstall`. It shows what it
  takes out, keeps a backup, and lists what only they can remove (this plugin, for one).
- Only when the user asks which version they have ("which version do I have?", or "/sessioncost version"), run
  `<run> version` and show its output as printed, adding no advice of your own (add
  `--all` only when they ask for every copy).
- Only when the user asks to turn the report after each answer on or off ("turn the report after each answer off",
  or "/sessioncost auto off"), run
  `<run> auto on` or `... auto off` and show the line it prints. It is on by default.
- All of these take the same fallbacks as step 1.

## Help

- When the user asks for SessionCost's help ("show me the SessionCost help", "what can SessionCost do?", or
  "/sessioncost help"), run `<run> help` and show its output as printed, as a quote
  block. If the guard already put that list in this chat for this message, show it from there and run nothing.
- When the user asks how to do something with SessionCost ("how do I stop the pop-ups?", "how do I see my
  tokens?"), answer in one to three lines: what to say in the chat (or the command to type), from this skill or that
  list. If you are not sure, say so and point to the guide, https://kish21.github.io/sessioncost-help/ . Never
  make up a command.

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- Never delete reports unless the user asked for it.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent.
