# SessionCost

See how many tokens each turn of your coding-agent chat sends, why, and what to cut.

Every message you send makes the agent send the whole conversation again, plus its setup, plus whatever it read.
Type `/sessioncost` in a chat and see, for each of your messages, how many tokens went out and what they were, the
heaviest turn, and the fixes that would have sent fewer, measured from that chat. Fewer tokens is what makes an agent
cheaper and faster, so the cost is shown too, as the result. It works in **Claude Code, Codex, Cursor and Antigravity**,
runs on your computer, and sends nothing anywhere.

## Get it

**Using Claude Code?** Type these two lines in a Claude Code chat:

```
/plugin marketplace add kish21/sessioncost
/plugin install sessioncost@sessioncost
```

(Or in the editor: **Manage Plugins → Marketplaces**, add `kish21/sessioncost`, then install **sessioncost** from
the **Plugins** tab.) This needs Python 3.10 or newer on your computer. If you have none, `/sessioncost` says so and
gives you the line below.

**Using Codex, Cursor or Antigravity (or Claude Code without Python)?** Paste one line in a terminal:

| Your computer | Paste this |
|---|---|
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 \| iex` |
| macOS or Linux | `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh \| sh` |

It installs SessionCost (bringing its own Python if your computer has none, via [uv](https://docs.astral.sh/uv/)),
then adds `/sessioncost` to every agent you have. Run the same line again to update.

**Then:** restart your agent and type `/sessioncost` in any chat.

## What you get

A short summary in the chat:

```
SessionCost: Add a CSV export to the reports page
1.9M tokens sent, 24k written · 5 turns · 31 calls · 22 min working, 6.0 min waiting for you
Heaviest turn: #3 “now add tests” · 910k tokens sent (48%) · $1.12
Top fix: Turn off tools this session never used · 420k fewer tokens sent · saves $0.214 (measured from this session)
Cost: $2.31 exact (API prices 2026-10-08)
Report: ~/.sessioncost/reports/<session-id>.html
```

(The numbers above are an example.) Say yes when it offers to open the report: a page in your browser with

- **Turn by turn**: one row per thing you asked, with the tokens it sent and wrote, its calls and minutes, and its
  cost. Click a turn to see its calls, and a call to see what it sent (fixed setup, carried history, new input) and
  what it wrote. Every set of parts adds up to the number above it.
- **Replay**: step or play through the chat call by call and watch the context grow.
- **Where the tokens went**: by kind of step (reading, running commands, writing files, helpers...) and by kind of
  token.
- **What to fix**: the fixes that apply to this chat, each with the tokens it would cut and the money that saves.
- **Sent with every call**: the agent's fixed setup, and which tools were never used.

Asking costs almost nothing: the agent runs the tool and shows its summary, and never reads the log or the report
into the chat.

## If something goes wrong

- **`/sessioncost` does nothing:** restart the agent after installing.
- **Two `/sessioncost` in Claude Code** (the plugin and the one-line install): run `sessioncost setup` again; with the
  plugin installed it removes the extra copy.
- **"Python is not found" with the Claude Code plugin:** paste the one line for your computer from above.
- **Windows says running scripts is disabled:** run the line as
  `powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex"`.
- **Already have Python and prefer pip:** `pip install https://github.com/kish21/sessioncost/archive/refs/heads/main.zip`,
  then `python -m sessioncost setup`.

## Which agents, and how exact

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

## From a terminal

After the one-line install, `sessioncost` also works in a terminal, from your project folder, and costs zero tokens:

```
sessioncost                    # the newest chat of this folder's project (same as: sessioncost last)
sessioncost list               # recent chats: date, id, cost, turns, calls, first message
sessioncost 3f2a9c1e           # one chat, by the first characters of its id
sessioncost path/to/session.jsonl   # or an Antigravity .db
sessioncost 3f2a9c1e --cursor-usage usage-events.csv   # a Cursor chat, with exact tokens from your export
sessioncost setup              # add /sessioncost to your agents again
```

Options: `--project <dir>` (another project folder), `--no-open` (write the report, do not open it), `--json`
(print the full model as JSON), `--prices <file>`, `--subscription` (see below), `--cursor-usage <file.csv>`.

`last` and `list` look at every agent above for the project folder. `last` picks the chat whose **last logged
message** is newest, not the newest file: reopening an old chat touches its file but does not make it the latest.

## Privacy

Local only. SessionCost reads log files on your disk and writes one HTML file to `~/.sessioncost/reports/`. It
sends nothing anywhere, calls no AI model and needs no network. The report page loads a web font from Google Fonts
when you are online and falls back to system fonts when you are not.

## How money is labelled

Every token class is priced on its own: new input, input written to the cache (5-minute and 1-hour rates differ),
input re-read from the cache, and output (the model's writing, hidden thinking included). Each model call is
counted once, even though the log writes one reply as several rows.

- **exact (API prices as of <date>)**: every model in the session has a checked rate in the price table. The
  figure is what the same tokens cost on the public API on that date.
- **estimate**: at least one model has no checked rate (the OpenAI and Gemini rates are from third-party price
  lists, not yet checked against the providers' own pages), or the tokens themselves are estimated (Cursor without
  its usage export).
- **API-equivalent**: with `--subscription`, and always for Antigravity. On a plan that is not billed per token
  (Claude Pro or Max, a ChatGPT plan, Antigravity) the figure is what the same work would cost at API prices.

When the log carries the agent's own running total (Claude Code), or you give Cursor's usage export with its cost
column, the report shows it as a cross-check.

Prices live in `sessioncost/prices.json` (USD per million tokens). Override any model in
`~/.sessioncost/prices.json` (merged over the built-in table) or pass `--prices <file>`.

Sizes the log does not give directly (how much of a call's input was the fixed setup, how much of its output was a
file rather than a message) are estimated from the text (characters / 4) and scaled so the parts add up exactly to
the token counts in the log. They are marked "~ estimated" on the page.

## The built-in fixes

Three simple checks. Each one shows the tokens it would cut and what they cost, in this session.

1. **Tools never used, sent with every call.** Tool definitions travel with every model call. The saving is what
   the unused ones cost across the session's calls.
2. **The same unchanged file read again.** A read that returned exactly the same text as an earlier read in the
   same chat. The saving is what that copy cost when it came in, plus on every later call that carried it.
3. **A confirm-only stop.** You typed only "yes", "ok", "save" or "go" (or picked such an answer on a question
   card), and the agent had stopped just to ask. The saving is the cost of re-sending the conversation to receive
   that answer.

## Roadmap

- Compare two sessions of the same task side by side.
- An optional fix engine with more checks.

_Status: early. MIT license._
