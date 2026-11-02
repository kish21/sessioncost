# SessionCost

See how many tokens each turn of your coding-agent chat sends, why, and what to cut. Claude Code, Codex, Cursor,
Antigravity and GitHub Copilot. Local: it reads log files on your computer and sends nothing anywhere.

```
npx sessioncost            # the report of the newest chat in this folder
npx sessioncost list       # your recent chats here
npx sessioncost setup      # add /sessioncost to your agents
```

SessionCost is a Python tool. The first run installs it once into a private environment in
`~/.sessioncost/npm/venv` (with [uv](https://docs.astral.sh/uv/) when you have it, which also fetches Python for you;
otherwise with your Python 3.10 or newer). Your own Python is left untouched.

Every command: [kish21.github.io/sessioncost-help](https://kish21.github.io/sessioncost-help/reference/commands/).
Source and the other ways to install: [github.com/kish21/sessioncost](https://github.com/kish21/sessioncost).
