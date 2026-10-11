# SessionCost

**See where every token of your AI coding chat went, and what to fix to send fewer.**

[![PyPI](https://img.shields.io/pypi/v/sessioncost)](https://pypi.org/project/sessioncost/) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) ![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Cursor%20%C2%B7%20Antigravity-1f6f78)

![Every turn of a chat on one path; tap a turn to see the calls inside it](https://raw.githubusercontent.com/kish21/sessioncost/main/media/path.gif)

Every message you send makes the agent send the **whole chat again**. SessionCost shows each turn on one path,
the calls inside it, and the fixes that would have sent less, measured from your own chat.

▶ [1-minute video tour](https://github.com/kish21/sessioncost/blob/main/media/sessioncost-demo.mp4) · <sub>pictures use a made-up demo chat</sub>

## Install

Open a terminal, paste one line, press Enter. No Python or Git needed.

| Windows (PowerShell) | `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 \| iex` |
|---|---|
| **Mac / Linux** | `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh \| sh` |

Restart your agent and type **`/sessioncost`** in any chat. The report opens in your browser.

![/sessioncost in a Claude Code chat](https://raw.githubusercontent.com/kish21/sessioncost/main/media/chat.png)

## What you get

**How much was sent, and how much the fixes would cut**

![9.9M tokens sent, 5.4M with the fixes, and the three biggest fixes](https://raw.githubusercontent.com/kish21/sessioncost/main/media/answer.png)

**What to fix**: each fix with its saving and the rule to paste to your agent. Tick "I did this" and it stops
repeating.

![Recommended rules and fix cards](https://raw.githubusercontent.com/kish21/sessioncost/main/media/fixes.png)

**Deeper fixes**: one button checks 30 more patterns, from a catalog built on 200+ real agent sessions.

![After the deeper fixes: 9.9M tokens sent, 2.8M with all 16 fixes](https://raw.githubusercontent.com/kish21/sessioncost/main/media/with-deeper.png)

## Private

- Reads your agent's log on your computer. No AI model is called.
- Nothing is sent unless you press **Send the summary to the fix engine**, and then only numbers and yes/no marks,
  never your chat, code or file names. The report shows the exact summary first.
- Don't want the button? `sessioncost --engine-url off`.

## More

<details>
<summary>Which agents, and how exact</summary>

| Agent | Tokens |
|---|---|
| Claude Code | exact, from its log |
| Codex | exact, from its log |
| Antigravity | exact, from its log |
| Cursor | estimated from text size; exact with your usage export (`--cursor-usage file.csv`) |
</details>

<details>
<summary>Use it from a terminal</summary>

```
sessioncost                 # the newest chat of this folder's project
sessioncost list            # recent chats
sessioncost 3f2a9c1e        # one chat, by the start of its id
sessioncost setup           # add /sessioncost to your agents again
sessioncost clean           # delete old reports, keep the latest
```

Options: `--no-open`, `--json`, `--subscription` (money as API-equivalent on Pro/Max plans), `--design classic`,
`--engine-url off`.
</details>

<details>
<summary>Update, or fix a problem</summary>

- **Update:** paste the install line again, then restart your agent.
- **`/sessioncost` does nothing:** restart the agent after installing.
- **Windows says scripts are disabled:** run
  `powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex"`.
- **Prefer pip:** `pip install sessioncost`, then `python -m sessioncost setup`.
- **Claude Code plugin** (needs Git and Python): `/plugin marketplace add kish21/sessioncost`, then
  `/plugin install sessioncost@sessioncost`.
- **The deeper fixes take a while:** the engine sleeps when idle; the first check can take up to a minute.
</details>

<details>
<summary>How the money is counted</summary>

Each kind of token is priced on its own: new input, written to the cache, re-read from the cache (about a tenth of
the price), and output. Prices are the public API's, dated in the report; override them in
`~/.sessioncost/prices.json`. On a subscription, `--subscription` labels the money as API-equivalent.
</details>

_MIT license · made by [kish21](https://github.com/kish21)_
