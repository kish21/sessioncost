# What's new

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
