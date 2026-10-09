# What's new

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
