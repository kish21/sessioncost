# What's new

## 0.5.11

- **Ready for Anthropic's Claude plugin directory:** a plugin icon, and the skill no longer carries the install
  commands: when SessionCost is missing, it points you to the install page instead.
- **Reports load nothing from the internet:** the fonts are now built into each report (free open-licence fonts,
  about 220 KB), instead of being fetched from Google Fonts. Same look, works offline.

## 0.5.10

- **Listed in the HOL plugin registry** (awesome-ai-plugins, owner-verified): its live trust badge is in the README.

## 0.5.9

- **Security basics:** a SECURITY.md (report problems privately through GitHub), Dependabot, and a read-only
  security scan on every change (HOL's plugin scanner, score 100/100).

## 0.5.8

- **Video tour on YouTube** (1440p, with music), linked at the top of the README.

## 0.5.7

- **README: Cursor plugin steps** (Customize → Plugins → From GitHub Repository), with the one-line install as the
  fallback when Cursor imports the repo without listing the plugin.
- **Antigravity plugin:** `agy plugin install https://github.com/kish21/sessioncost`. The repo's root `plugin.json` now
  holds only the name and description, as Antigravity requires.

## 0.5.6

- **README: Codex app steps** for the plugin (Customize → Plugins → sessioncost → Install plugin), next to the
  Codex CLI's `/plugins`. Tested with the Codex app.
- **The "Turns and calls" tile names each number:** "12 turns (your messages)" next to "86 model calls (the
  agent's steps)", instead of "12 · 86".

## 0.5.5

- **When the fix engine is off or busy, the report says so plainly** ("not available right now", "busy: try
  again in a minute", "did not answer in time") instead of a technical error, and stops waiting after 100 seconds.
  The report and its basic fixes never depend on the engine.
- **Install guide by kind of user** in the README: one line (nothing else needed), PyPI, or the Claude Code
  plugin, then where `/sessioncost` lands in each agent. The PyPI way now uses `python -m pip install -U` and
  `python -m sessioncost setup`, which also update an older copy and work when Windows cannot find the
  `sessioncost` command.
- **Cursor: Step 0 no longer shows ~0 for a chat that is still running.** Cursor saves the size of a chat's setup
  only later; until then SessionCost takes it from your newest Cursor chat that has it (the same project first) and
  says so on the page.
- **Plugins for Codex too:** `codex plugin marketplace add kish21/sessioncost`, then install it from `/plugins`.
  The repo also carries the manifest Cursor's plugin marketplace reads (`.cursor-plugin/plugin.json`) and the
  portable `plugin.json`.
- **The agent no longer says the report is open when it may not be.** Some agents (Cursor, for example) run
  commands where no browser can open; the agent now gives the report as a link to click.
- **"+30 more checks" no longer reads like 30 problems found.** The card invites you to run the deeper check.
- **The version shows everywhere:** in the first line of the chat summary ("SessionCost 0.5.5: ..."), in the report's
  header, and in the skill's description in each agent's `/` menu, so an old copy is easy to spot.

## 0.5.4

- **A short README**: a moving picture of the path of turns and the calls inside a turn at the top, one line per
  picture, the details folded away. No change to how SessionCost works.

## 0.5.3

- **A clearer README** with pictures of every part of the report and a one-minute video tour (made with a demo
  chat, not a real one). No change to how SessionCost works.

## 0.5.2

- **More deeper fixes:** the fix engine now also finds tool answers that said "output too large" (the agent then
  had to read a saved file in pieces), cut output that was run again, steps a safety check stopped that the agent
  kept trying, tool uses you turned down more than once, digging in the tool's own files after a failure, and the
  same long text brought into the chat twice.
- **Still no content sent:** your computer turns each tool answer into yes/no marks (said "too large", was cut, was
  stopped, was turned down). Only those marks are in the summary; the report shows them before you send it.

## 0.5.1

- **"I did this": fixes you have done stop repeating.** Each fix you do once (a rule for the agent, a setting you
  change) has an **I did this** box, and the recommended rules have **I added these rules**. Your later reports then
  show it as **✓ Done**, folded away, instead of the same advice again. Fixes that no longer happen are listed as
  "Fixed by you, and not seen in this chat". If the problem comes back in a chat started after you ticked it, the
  report says so plainly (**⚠ marked done, but it happened again**) and what to check. Habits, like starting a new
  chat after a long break, have no box: they are about each chat. The ticks are kept in your browser only and are
  never sent.

## 0.5.0

- **Deeper fixes are here.** The report's "Deeper fixes" box now has a button, **Send the summary to the fix
  engine**. Press it and our fix engine checks the session for 24 more kinds of waste from our catalog of measured
  patterns, among them: waking up again and again to check a background job, edit scripts that fail, `cd` in front
  of every command, flaky tests run over and over, and helpers that read far too much. Its fixes join the list, the
  board and the answer at the top, each with its measured saving and what to do.
- **Nothing is sent unless you press the button.** What is sent is a summary: numbers, tool names and yes/no flags,
  never your messages, code, file names or project. The report shows the exact summary first, and the engine keeps
  nothing. `--engine-url off` hides the button. See Privacy in the README.

## 0.4.2

- **New basic fix: "Start a new chat after a long break."** A long chat that goes on after a break of half an hour or
  more re-sends the whole old conversation with every later call. The report measures, call by call, what a new
  (or compacted) chat at that break would have saved, often a third or more of a long session, and says how:
  /compact or /clear in Claude Code. The board marks those moments with an orange ↻.
- **Points you to more savings:** the fix list and the top of the report say "to cut more tokens, check the deeper
  fixes".
- **A small saving is said plainly:** when the fixes would cut less than 5%, the top of the report says "28M tokens
  sent, 97% cheap re-reads. Little to cut: −331k (1%)" instead of "28M, with the fixes 28M", which read as if the
  fixes did nothing.

## 0.4.1

- **"Turn off unused tools" now counts only what you can turn off:** connected MCP servers, claude.ai connectors
  and plugins. The agent's own built-in tools come with it and can't be turned off, so they are no longer counted
  (0.4.0 overstated this saving). The fix names each one and where to turn it off.
- **Fixes say whether you paste them or do them yourself:** a rule is pasted into the chat (the agent adds it to its
  instruction file); a setting like turning off a connected tool is done by you, with numbered steps. The report
  says which, so nobody pastes a setting into the chat.
- **Step 0's tool list** marks built-in tools (grey, can't be turned off) apart from connected tools never used
  (dashed, can be turned off).

## 0.4.0

- **A new report, the session board** (now the default; `--design classic` opens the earlier one):
  - **The answer first:** tokens sent now and after the fixes, and the biggest fixes, ranked by the money they save.
  - **The path:** Step 0, then every turn as a tile on one path. Tap a turn to zoom into its calls; tap a call for
    what it sent and wrote.
  - **Charts:** how each kind of token is priced and why the tokens and the money split so differently; the memory
    sent with each call, shown by how it was paid (mostly cheap cache re-reads, the new part on its own scale);
    where each fix helps in tokens, money and time; and what the agent spent its tokens on.
  - **Step 0 in full:** what the fixed setup is made of, and every tool, used or never used.
- **Text Claude Code adds mid-session is named:** tools it announces later (like the Chrome browser tools),
  connected-service instructions, reminders and file-change notes now show by name in "what came in", instead of
  "not itemised". If those tools are never used, the "turn off unused tools" fix counts them too and names the
  services to disconnect.
- **Find deeper fixes, opt-in:** with `--engine-url` (or `SESSIONCOST_ENGINE_URL`) the report offers a button
  that sends the session summary (numbers and tool names, never content) to that fix engine, and adds the fixes it
  returns. Without it nothing is ever sent.
- **"Skip the stop that only asks you to confirm" counts only real stops:** a "yes" or "ok" counts only when the
  agent's last message asked you something, not when you said "ok" after a finished job.
- **Recommended rules for your agent:** seven habits that keep an agent from wasting tokens, for everyone, at the
  top of "What to fix". The ones this session needed are marked with what they would have cut; one button copies
  them all as a request your agent carries out (it adds them to CLAUDE.md, AGENTS.md or GEMINI.md).
- **Rules go where each agent reads them:** CLAUDE.md for Claude Code, AGENTS.md for Codex and Cursor, GEMINI.md
  for Antigravity, and the report's "Copy as a request" names the right file.
- **Times never read "25h 60m"** in the new design (minutes are rounded before they are split into hours).
- **Newer Cursor works again:** newer Cursor builds stopped saving which folder a chat belongs to, so
  `sessioncost` found no Cursor chats for your project. It now also finds the folder from Cursor's per-project
  transcripts and its per-folder store, so old and new Cursor builds both work.
- **Archived Codex chats are found:** a Codex chat you archive moves to another folder; it is now searched too, and
  its helpers are read with it.
- **Codex helper costs are counted in full:** a Codex helper (the command reviewer, or an agent it starts) was
  sometimes read as empty, so its calls were left out of the total. Its own calls now count; the history it copied
  from the main chat still does not count twice.
- **Never "no session found" when one exists:** if no session names your folder, `sessioncost` shows the newest
  one whose folder the agent did not record, and says so. `sessioncost list` lists those separately.

## 0.3.1

- **A Step 0 row** at the top of "Turn by turn": the agent's setup, sent before your first message and again with
  every call, with its size per call and in all. Click it to see what it is made of.
- **Earlier conversation is counted by what it is**, not by whether it came from the cache. After a long pause the
  cache expires and the conversation is sent again at full price: it used to show as "new", now it shows as earlier
  conversation, marked "sent again at full price: the cache had expired".
- **What came from the cache, per turn:** each turn shows how much was re-read from the cache (cheap) and how much
  went at full price, as numbers and as a thin amber line under its bar; Step 0 shows it for the setup. Earlier
  conversation is now blue, so amber always means "re-read from cache".
- **Output tokens back in the headline and on the tile:** "It wrote 1.7K back: few tokens, but 12% of the cost."
- **Numbers right under every bar**, in the bar's colours: setup + conversation + new = sent, then what came from
  the cache (the words are in the hover text). Also for Step 0 and for each call.
- **An opened call reads step by step and adds up:** step 0 + step 1 + step 2 (with what came in, including the part
  the log does not itemise) = sent; then what came from the cache, what it wrote, and the cost. The log's own grouping
  by cache status is one click away.
- **`sessioncost clean`** (or `/sessioncost clean`) deletes all saved reports except the latest. Past 10 older
  reports the summary reminds you, without asking.
- README: how to update, for each way of installing.

## 0.3.0

- **What was sent, in three steps, in numbers:** step 0 the agent's fixed setup (sent before you type, with every
  call), step 1 the earlier conversation sent again, step 2 what was new. Shown at the top, on the tile, per turn and
  per call.
- Step 0 is now **measured** from the first call of the session, so it includes the parts the log does not spell out
  (Claude Code's built-in instructions) and works for agents that log no setup at all.
- **Find deeper fixes** (preview): shows exactly what SessionCost would share to check our catalog of measured waste
  patterns: numbers, tool names and yes/no flags, never messages, code or file names. Nothing is sent yet.
- A **light / dark** switch in the report.
- "Tokens to cut" is the last tile; tile titles and labels wrap instead of being cut off.

## 0.2.2

- The Claude Code plugin now also finds SessionCost when the one-line install brought it with its own Python, so
  computers without Python work with the plugin too.
- `/sessioncost` opens the report in your browser right away instead of asking first.
- The README leads with the one-line install, which needs neither Git nor Python; the plugin is for people who
  already have both (Claude Code downloads plugins with Git).

## 0.2.1

- `sessioncost setup` (and the one-line install) leaves Claude Code alone when the SessionCost plugin is
  installed, and removes an older copy it added there, so Claude Code never shows two `/sessioncost`.

## 0.2.0

- **Tokens first.** The summary and the report now lead with how many tokens each turn sent, the heaviest turn and
  the tokens each fix would cut. The cost is still shown, as the result.
- Fixes are ranked by tokens cut.
- Install in one line on Windows, macOS and Linux (it brings its own Python if needed), or as a Claude Code plugin
  from the Marketplaces tab.
- `/sessioncost` now works even when Python's script folder is not on the PATH.

## 0.1.0

- First version: turn-by-turn report for Claude Code, Codex, Cursor and Antigravity sessions, with replay, where the
  cost went, and three built-in fixes.
