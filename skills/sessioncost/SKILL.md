---
name: sessioncost
description: Show where the tokens and money of THIS coding session went, turn by turn, with the costliest turn and the top fix. Use when the user asks what this session or chat cost, where the tokens went, why it was expensive, or runs /sessioncost.
---

# SessionCost: what did this session cost?

Run SessionCost, which came with this plugin, on the current session. It reads the session's log on this machine,
writes an HTML report and prints a short summary. It sends nothing anywhere and uses no AI model.

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   python "${CLAUDE_PLUGIN_ROOT}/run.py" last --no-open
   ```

   If `python` is not found (or opens the Microsoft Store), try `py` in its place (Windows), then `python3`. If none
   of them works, SessionCost needs Python 3.10 or newer, which this computer does not have. Tell the user to paste
   the one line for their system in a terminal, which installs it with its own Python, then restart Claude Code and
   stop:

   - Windows (PowerShell): `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex`
   - macOS or Linux: `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh | sh`

2. Show the user the printed summary exactly as printed, in a code block. Do not reword or recompute it.

3. Add one line: the report path from the summary, and offer to open it ("Want me to open the report?").
   If they say yes, open the file with the system's default opener (`start "" <path>` on Windows, `open <path>`
   on macOS, `xdg-open <path>` on Linux).

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent.
