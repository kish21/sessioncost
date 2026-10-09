# What's new

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
