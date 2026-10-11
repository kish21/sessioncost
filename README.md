# SessionCost

**See where every token of your coding-agent chat went, and what to fix to send fewer.**

[![PyPI](https://img.shields.io/pypi/v/sessioncost)](https://pypi.org/project/sessioncost/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Cursor%20%C2%B7%20Antigravity-1f6f78)

![The top of a SessionCost report: 9.9M tokens sent, 5.4M with the fixes, and the three biggest fixes](https://raw.githubusercontent.com/kish21/sessioncost/main/media/answer.png)

<sub>Screenshots and video use a made-up demo chat, not a real one.</sub>

▶ **[Watch the 1-minute tour](https://github.com/kish21/sessioncost/blob/main/media/sessioncost-demo.mp4)**

## The problem

Every message you send makes the agent send the **whole conversation again**: its setup, everything said so far,
every file it read. A chat that feels small can send millions of tokens. Your bill or usage meter shows a total;
it doesn't show which turn sent what, or why.

SessionCost reads the agent's own log on your computer and shows, turn by turn, what was sent, what was re-sent,
and the fixes that would have sent less, each one **measured from that chat**, not guessed.

## Sixty seconds

Open a terminal (Windows: press the Windows key, type `powershell`, press Enter. Mac: Cmd + Space, type
`terminal`, press Enter), paste one line, and press Enter:

| Your computer | Paste this |
|---|---|
| Windows | `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 \| iex` |
| Mac or Linux | `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh \| sh` |

You don't need Python or Git: the installer brings what it needs. It adds `/sessioncost` to every agent it finds.

![The install in PowerShell: /sessioncost added to Claude Code, Codex, Cursor and Antigravity](https://raw.githubusercontent.com/kish21/sessioncost/main/media/install.png)

Restart your agent, then type **`/sessioncost`** in any chat. You get a short summary in the chat, and the full
report opens in your browser. Asking costs almost nothing: the agent runs the tool and shows its summary, and
never reads the log or the report into the chat.

![/sessioncost in a Claude Code chat: tokens sent, the heaviest turn, the top fix and the cost](https://raw.githubusercontent.com/kish21/sessioncost/main/media/chat.png)

To update later, paste the same line again.

<details>
<summary>Already have Git and Python? Install it as a Claude Code plugin instead</summary>

In a Claude Code chat:

```
/plugin marketplace add kish21/sessioncost
/plugin install sessioncost@sessioncost
```

(Or **Manage Plugins → Marketplaces**, add `kish21/sessioncost`, then install **sessioncost** from the **Plugins**
tab.) Claude Code downloads plugins with Git, so this needs [Git](https://git-scm.com/downloads), and SessionCost
needs Python 3.10 or newer. If the plugin says "git not found", use the line above instead.
</details>

## See it

**The answer first.** Tokens sent now and after the fixes, what that cost, and the three biggest fixes (picture at
the top).

**The path.** Step 0 (the setup sent with every call), then every turn as a tile on one path. Redder tiles sent
more; the pipe between them is the conversation carried into the next turn; a flag means a fix applies there.

![The board: Step 0 and twelve turns as tiles on one path, the heaviest ones marked](https://raw.githubusercontent.com/kish21/sessioncost/main/media/board.png)

**Open any turn** to see its calls, what each one sent and wrote, and which fix applies. Here the agent stopped
twice to wait for a background job and woke up just to read its log: 137k tokens each time.

![Turn 5 opened: eight calls, two of them only "waiting" and two only reading a log](https://raw.githubusercontent.com/kish21/sessioncost/main/media/turn.png)

## Understand it

**Step 0** is what goes out before you say a word: the system prompt, tool definitions, skills, connected
services. SessionCost measures it and lists every tool, used or never used.

![Step 0: 32k tokens on every call, with the tool list and the unused connected tools](https://raw.githubusercontent.com/kish21/sessioncost/main/media/step0.png)

**Charts** show why the tokens and the money split so differently (most tokens are cheap cache re-reads), how the
conversation grows call by call, and where each fix helps.

![Charts: how each kind of token is priced, and the memory sent with each call](https://raw.githubusercontent.com/kish21/sessioncost/main/media/charts.png)

## Fix it

Each fix says **how many tokens it cuts and what that saves, in this chat**, where it happened, and what to do:
a rule to paste into your agent (press **Copy as a request**, paste it into the chat, and the agent adds it to its
instruction file), or steps for a setting you change yourself.

![What to fix: the recommended rules, and fix cards with the saving and the steps](https://raw.githubusercontent.com/kish21/sessioncost/main/media/fixes.png)

**Done it? Tick "I did this".** Your next reports show it as ✓ instead of repeating the advice. If the problem
comes back in a later chat, the report says so and what to check. The ticks stay in your browser.

The built-in fixes, found on your computer:

1. **Start a new chat after a long break.** A long chat that goes on after a break of half an hour or more
   re-sends all the old work with every later call. Measured call by call; the fix is `/compact` or `/clear`.
2. **Turn off connected tools this project does not use.** Their definitions go out with every call.
3. **Don't re-read a file that has not changed.** Counted on every later call that carried the copy.
4. **Skip the stop that only asks you to confirm.** A whole round trip to receive "yes".

## Go deeper

Press **Send the summary to the fix engine** and the fix engine checks **30 more patterns** from a catalog of
waste measured across over 200 real agent sessions: waking up to check a background job, output too large to
come back, edit scripts that fail, re-running a test with nothing changed, research piled into the main chat,
git look-ups around every commit, and more.

![The deeper-fixes box: the 30 patterns, what the summary holds, and the Send button](https://raw.githubusercontent.com/kish21/sessioncost/main/media/deeper.png)

The engine's fixes join the list, the board and the answer at the top, each with its measured saving.

![After sending: 9.9M tokens sent, 2.8M with all 16 fixes](https://raw.githubusercontent.com/kish21/sessioncost/main/media/with-deeper.png)

It is free. What goes out is a summary of numbers, tool names and yes/no marks, and only when you press the
button (see Privacy).

## On your phone too

The report is one HTML file that works offline. Open it on any screen.

<img src="https://raw.githubusercontent.com/kish21/sessioncost/main/media/phone.png" alt="The report on a phone" width="300">

## Works with

| Agent | Where its log is | Tokens |
|---|---|---|
| Claude Code | `~/.claude/projects/<project>/<session>.jsonl` | exact, from the log |
| Codex (CLI, IDE, app) | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | exact, from the log |
| Antigravity | `~/.gemini/antigravity-ide/conversations/<id>.db` | exact, from the log |
| Cursor | Cursor's `User/globalStorage/state.vscdb` | **estimated** from text size; exact with your usage export |

Cursor keeps no token counts on your computer. SessionCost rebuilds each call from the chat's messages, estimates
its size from the text (characters / 4), and scales it so the last call matches the context size Cursor itself
recorded. For exact numbers, download your usage export from the Cursor dashboard and pass it with
`--cursor-usage file.csv`.

## Private by design

Local first. SessionCost reads log files on your disk and writes one HTML file to `~/.sessioncost/reports/`. It
calls no AI model, and the report and its basic fixes are made entirely on your computer.

**The one thing it can send, only when you press the button.** The report's "Deeper fixes" box has a button,
**Send the summary to the fix engine**. Only when you press it, the report sends a *summary* of that one session to
our fix engine (`https://sessioncost-engine.onrender.com`), which answers with more fixes. The summary is numbers,
tool names and yes/no marks: tokens per call, tool sizes, whether a step failed, whether a tool's answer said
"output too large", was cut short, was stopped by a safety check or was turned down by you. It **never** holds your
messages, the agent's replies, code, file contents, command text, file or folder names, the project name or the
session id; files and commands appear only as codes like `f3`, new for every summary. Those yes/no marks are made
on your computer; the text they come from is never kept. The report shows you the exact summary before you send
it. The engine checks it in memory and keeps nothing: it is not stored or logged.

To hide the button entirely: `sessioncost --engine-url off` (or set `SESSIONCOST_ENGINE_URL=off`).

The "I did this" ticks in a report are kept in your browser only and never sent.

The report page loads a web font from Google Fonts when you are online and falls back to system fonts when you are
not.

## From a terminal

After the one-line install, `sessioncost` also works in a terminal, from your project folder, and costs zero tokens:

```
sessioncost                    # the newest chat of this folder's project (same as: sessioncost last)
sessioncost list               # recent chats: date, id, cost, turns, calls, first message
sessioncost 3f2a9c1e           # one chat, by the first characters of its id
sessioncost path/to/session.jsonl   # or an Antigravity .db
sessioncost 3f2a9c1e --cursor-usage usage-events.csv   # a Cursor chat, with exact tokens from your export
sessioncost setup              # add /sessioncost to your agents again
sessioncost clean              # delete all saved reports except the latest
```

Options: `--project <dir>` (another project folder), `--no-open` (write the report, do not open it), `--json`
(print the full model as JSON), `--prices <file>`, `--subscription` (see below), `--cursor-usage <file.csv>`,
`--design classic` (the earlier report design), `--engine-url off`.

`last` and `list` look at every agent above for the project folder. `last` picks the chat whose **last logged
message** is newest, not the newest file: reopening an old chat touches its file but does not make it the latest.

## Questions

**Updating.**

| How you installed it | How to update |
|---|---|
| The one line above | Paste the same line again, then restart your agent. |
| Claude Code plugin | **/plugin → Marketplaces → sessioncost → Update** (or type `/plugin marketplace update sessioncost`, then `/plugin update sessioncost@sessioncost`), then restart Claude Code. You can also turn on auto-update for this marketplace there. |
| pip | `pip install -U sessioncost`, then `sessioncost setup`. |

**Old reports.** Each chat keeps one report in `~/.sessioncost/reports/` (a few hundred KB each). When more than 10
older ones pile up, the summary says so. Type `/sessioncost clean` in a chat, or `sessioncost clean` in a terminal,
to delete all of them except the latest. Nothing is ever deleted unless you ask.

**If something goes wrong.**

- **`/sessioncost` does nothing:** restart the agent after installing.
- **Two `/sessioncost` in Claude Code** (the plugin and the one-line install): run `sessioncost setup` again; with the
  plugin installed it removes the extra copy.
- **"git not found" when adding the plugin:** use the one-line install instead; it needs neither Git nor Python.
- **"Python is not found" with the Claude Code plugin:** paste the one line for your computer from above.
- **Windows says running scripts is disabled:** run the line as
  `powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex"`.
- **Already have Python and prefer pip:** `pip install sessioncost`, then `python -m sessioncost setup`.
- **The deeper fixes take a while:** the fix engine sleeps when no one has used it for a while; the first check can
  take up to a minute.

**How is money labelled?** Every token class is priced on its own: new input, input written to the cache (5-minute
and 1-hour rates differ), input re-read from the cache, and output (the model's writing, hidden thinking included).
Each model call is counted once, even though the log writes one reply as several rows.

- **exact (API prices as of <date>)**: every model in the session has a checked rate in the price table. The
  figure is what the same tokens cost on the public API on that date.
- **estimate**: at least one model has no checked rate (the OpenAI and Gemini rates are from third-party price
  lists, not yet checked against the providers' own pages), or the tokens themselves are estimated (Cursor without
  its usage export).
- **API-equivalent**: with `--subscription`, and always for Antigravity. On a plan that is not billed per token
  (Claude Pro or Max, a ChatGPT plan, Antigravity) the figure is what the same work would cost at API prices.

When the log carries the agent's own running total (Claude Code), or you give Cursor's usage export with its cost
column, the report shows it as a cross-check. Prices live in `sessioncost/prices.json` (USD per million tokens).
Override any model in `~/.sessioncost/prices.json` (merged over the built-in table) or pass `--prices <file>`.

Sizes the log does not give directly (how much of a call's input was the fixed setup, how much of its output was a
file rather than a message) are estimated from the text (characters / 4) and scaled so the parts add up exactly to
the token counts in the log. They are marked "~ estimated" on the page.

**Do the fixes really save?** In a before-and-after test (5 sessions of the same task with the rules, 5 without),
the rules cut the tokens sent by about a third. Each fix in a report shows what it would have cut in that chat.

## Roadmap

- Compare two sessions of the same task side by side.

_MIT license. Made by [kish21](https://github.com/kish21)._
