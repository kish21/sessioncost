# SessionCost

**See where every token of your AI coding chat went, and what to fix to send fewer.**

[![PyPI](https://img.shields.io/pypi/v/sessioncost)](https://pypi.org/project/sessioncost/) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) ![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Cursor%20%C2%B7%20Antigravity-1f6f78)

![Every turn of a chat on one path; tap a turn to see the calls inside it](https://raw.githubusercontent.com/kish21/sessioncost/main/media/path.gif)

Every message you send makes the agent send the **whole chat again**. SessionCost shows each turn on one path,
the calls inside it, and the fixes that would have sent less, measured from your own chat.

▶ [1-minute video tour](https://github.com/kish21/sessioncost/blob/main/media/sessioncost-demo.mp4) · <sub>pictures use a made-up demo chat</sub>

## Install

Pick the way that suits you. Each one adds `/sessioncost` to every agent on your computer: **Claude Code, Codex,
Cursor and Antigravity**.

### 1. Not technical? One line, nothing else needed

1. Open a terminal. **Windows:** press the Windows key, type `powershell`, press Enter. **Mac:** press Cmd + Space,
   type `terminal`, press Enter.
2. Paste the line for your computer and press Enter:

   | Windows | `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 \| iex` |
   |---|---|
   | **Mac / Linux** | `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh \| sh` |

3. Wait about a minute. It ends with `/sessioncost added` for each agent it found.

### 2. Use the command line? Install from PyPI

Needs Python 3.10 or newer.

```
python -m pip install -U sessioncost     # -U also updates an older copy
python -m sessioncost setup              # adds /sessioncost to your agents
```

On Mac or Linux use `python3` if `python` is not found. `python -m` works even when Windows cannot find the
`sessioncost` command (common with the Microsoft Store Python). Prefer isolated tools? `pipx install sessioncost` or
`uv tool install sessioncost`, then `sessioncost setup`.

### 3. Prefer plugins? Add it from your agent's plugin list

Plugins need Python 3.10 or newer on the computer (way 1 does not).

| Agent | How |
|---|---|
| **Claude Code** | In a chat: `/plugin marketplace add kish21/sessioncost`, then `/plugin install sessioncost@sessioncost`. Or type `/plugin`, open **Marketplaces**, add `kish21/sessioncost`, and install **sessioncost** from the **Plugins** tab. (Needs Git.) |
| **Codex** | First, in a terminal: `codex plugin marketplace add kish21/sessioncost`. Then **Codex app:** open **Customize → Plugins**, choose **sessioncost**, click **Install plugin**. **Codex CLI:** type `/plugins`, pick **sessioncost**, install it. Start a new chat afterwards. |
| **Cursor** | Open **Customize → Plugins**, choose **From GitHub Repository** (or **Add Marketplace**), paste `https://github.com/kish21/sessioncost`, then install **sessioncost** and restart Cursor. If no plugin shows up (some Cursor versions import a repo without listing its plugins), use way 1. |
| **Antigravity** | With the Antigravity command line: `agy plugin install https://github.com/kish21/sessioncost`, then start a new chat. In the Antigravity app, use way 1 (it adds `/sessioncost` to Antigravity directly). |

Use one way per agent: with the plugin installed, you don't need the copy from way 1 or 2 in that agent.

### Then, in your agent

Restart the agent, open any chat, and type **`/sessioncost`** (or ask: "run sessioncost"). The report opens in
your browser.

| Agent | Where `/sessioncost` is added |
|---|---|
| Claude Code | `~/.claude/skills/sessioncost` (or the plugin) |
| Codex (CLI, IDE, app) | `~/.codex/skills/sessioncost` |
| Cursor | `~/.cursor/skills/sessioncost` |
| Antigravity | `~/.gemini/config/skills/sessioncost` |

Installed another agent later? Run `sessioncost setup` again (or paste the line from way 1 again).

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
- **"sessioncost is not recognized"** (Windows): use `python -m sessioncost` instead of `sessioncost`.
- **Windows says scripts are disabled:** run
  `powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex"`.
- **Updated with pip:** `python -m pip install -U sessioncost`, then `python -m sessioncost setup`. **Plugin:** `/plugin` →
  **Marketplaces** → sessioncost → **Update**, then restart Claude Code.
- **"git not found" when adding the plugin:** use way 1 instead; it needs neither Git nor Python.
- **The deeper fixes take a while:** the engine sleeps when idle; the first check can take up to a minute.
</details>

<details>
<summary>How the money is counted</summary>

Each kind of token is priced on its own: new input, written to the cache, re-read from the cache (about a tenth of
the price), and output. Prices are the public API's, dated in the report; override them in
`~/.sessioncost/prices.json`. On a subscription, `--subscription` labels the money as API-equivalent.
</details>

_MIT license · made by [kish21](https://github.com/kish21)_
