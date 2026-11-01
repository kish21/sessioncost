# SessionCost

**See where every token of your AI coding chat went, and what to fix to send fewer.**

[![PyPI](https://img.shields.io/pypi/v/sessioncost)](https://pypi.org/project/sessioncost/) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) ![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Cursor%20%C2%B7%20Antigravity-1f6f78) [![HOL Guard](https://img.shields.io/endpoint?url=https%3A%2F%2Fhol.org%2Fapi%2Fregistry%2Fbadges%2Fplugin%3Fslug%3Dkish21%252Fsessioncost%26metric%3Dtrust)](https://hol.org/registry/plugins/kish21%2Fsessioncost)

![Every turn of a chat on one path; tap a turn to see the calls inside it](https://github.com/kish21/sessioncost/releases/download/media/path.gif)

Every message you send makes the agent send the **whole chat again**. SessionCost shows each turn on one path,
the calls inside it, and the fixes that would have sent less, measured from your own chat.

**Tokens: exact**, from the agent's own log. **Money: an estimate** at list prices; your bill may differ (plan,
contract rates, batch, cloud provider). Cursor and Copilot Chat keep no token counts, so for them tokens are estimated
from the size of the text.

📖 **[Help and guides](https://kish21.github.io/sessioncost-help/)** · ▶ **[Take the report tour](https://kish21.github.io/sessioncost-help/report/tour/)** · **[1-minute video](https://youtu.be/TYfaJuEWr7U)** · <sub>pictures use a made-up demo chat</sub>

## Install

One line adds `/sessioncost` to every agent on your computer: **Claude Code, Codex, Cursor and Antigravity**. It needs
neither Python nor Git. Open a terminal, paste the line for your computer, and press Enter:

| Windows (PowerShell) | `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 \| iex` |
|---|---|
| **Mac / Linux** | `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh \| sh` |

It ends with `/sessioncost added` for each agent it found. Then restart your agent.

Prefer PyPI (`python -m pip install -U sessioncost`, then `python -m sessioncost setup`), npm (`npx sessioncost`,
then `npx sessioncost setup`) or your agent's plugin list
(Claude Code: `/plugin marketplace add kish21/sessioncost`)? Every way, step by step:
**[Install SessionCost](https://kish21.github.io/sessioncost-help/start/install/)**.

## Use it

Open any chat and type **`/sessioncost`** (or ask: "run sessioncost"). The report opens in your browser.

![/sessioncost in a Claude Code chat](https://github.com/kish21/sessioncost/releases/download/media/chat.png)

**How much was sent, and how much the fixes would cut**

![9.9M tokens sent, 5.4M with the fixes, and the three biggest fixes](https://github.com/kish21/sessioncost/releases/download/media/answer.png)

**What to fix**: each fix with its saving and the rule to paste to your agent.

![Recommended rules and fix cards](https://github.com/kish21/sessioncost/releases/download/media/fixes.png)

New to the report? **[Your first report](https://kish21.github.io/sessioncost-help/start/first-report/)** walks
through it in five minutes, and every section of the report has a **?** that opens its help page.

## Commands

| In a chat | What it does |
|---|---|
| `/sessioncost` | Builds the report of this chat and gives you its link |
| `/sessioncost clean` | Deletes old reports, keeps the latest |
| `/sessioncost update` | Updates SessionCost, then restart your agent |
| `/sessioncost version` | Shows which SessionCost version this agent uses |
| `/livesession` | Claude Code: [the guard](https://kish21.github.io/sessioncost-help/guard/what-it-does/) checks each step before it runs, blocks the dangerous ones and asks before the risky ones, in every chat until `/livesession off` |

Terminal commands, report options and the guard's settings:
**[Commands](https://kish21.github.io/sessioncost-help/reference/commands/)**.

## Private

- Reads your agent's log on your computer. No AI model is called.
- Nothing is sent anywhere: the report is one file on your computer.

More: **[Privacy and security](https://kish21.github.io/sessioncost-help/reference/privacy/)**.

## More

- [Which agents, and how exact](https://kish21.github.io/sessioncost-help/reference/agents/)
- [How the money is counted](https://kish21.github.io/sessioncost-help/reference/money/)
- [Fix a problem](https://kish21.github.io/sessioncost-help/help/fix-a-problem/), including updating

_MIT license · made by [kish21](https://github.com/kish21)_
