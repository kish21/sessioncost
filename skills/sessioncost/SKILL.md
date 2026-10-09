---
name: sessioncost
description: Show how many tokens each turn of THIS coding session sent, the heaviest turn, and the top fix to cut them (with what it cost). Use when the user asks where the tokens went, why the context is so big, what this session or chat cost, why it was expensive, or runs /sessioncost.
---

# SessionCost: where did this session's tokens go?

Run SessionCost, which came with this plugin, on the current session. It reads the session's log on this machine,
writes an HTML report and prints a short summary. It sends nothing anywhere and uses no AI model.

## Steps

1. Run this from the project folder of the current session (one command, nothing else):

   ```
   python "${CLAUDE_PLUGIN_ROOT}/run.py" last
   ```

   If `python` is not found (or opens the Microsoft Store, or is older than 3.10), try `py` in its place (Windows),
   then `python3`. If none of them works, the one-line install may have brought SessionCost with its own Python: try
   `sessioncost last`, then `~/.local/bin/sessioncost last`. If that fails too, SessionCost needs
   Python 3.10 or newer, which this computer does not have. Tell the user to paste the one line for their system in a
   terminal, which installs it with its own Python, then restart Claude Code and stop:

   - Windows (PowerShell): `irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex`
   - macOS or Linux: `curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh | sh`

2. Show the user the printed summary exactly as printed, in a code block. Do not reword or recompute it.

3. The tool opens the report in the browser by itself. Add one line: "The full report is open in your browser"
   and its path from the summary. Do not offer to open it. Only if the user says it did not open, open the file with
   the system's default opener (`start "" <path>` on Windows, `open <path>` on macOS, `xdg-open <path>` on Linux).

## Deleting old reports

Each session keeps one report in `~/.sessioncost/reports/`. If the summary ends with an "Old reports: ..." line, show
it as printed and do not ask anything about it. Only when the user asks to delete or clean old reports (for example
"/sessioncost clean"), run (with the same fallbacks as step 1):

```
python "${CLAUDE_PLUGIN_ROOT}/run.py" clean
```

It deletes every saved report except the latest and prints what it freed. Show that line.

## Never

- Never read the session log or the HTML report into the conversation. Both are large; reading them would cost more
  tokens than the session you are measuring.
- Never run `--json` unless the user asks for the raw numbers, and then show only the part they asked about.
- Never estimate costs yourself. The tool's numbers are the answer.
- Never delete reports unless the user asked for it.
- If the user says they are on a subscription plan (Claude Pro or Max, a ChatGPT plan), add `--subscription` so money
  is labelled API-equivalent.
