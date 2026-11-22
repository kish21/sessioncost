#!/bin/sh
# SessionCost launcher for Mac and Linux. It finds a Python 3.10+ (python3, python, the one uv manages), fetches one
# through uv when there is none (into uv's own folder; nothing else changes), then runs SessionCost with the
# arguments given; "run.sh guard ..." runs the guard. One command, so an agent never has to hunt for Python.
ROOT="$(cd "$(dirname "$0")" && pwd)"
LB="$HOME/.local/bin"
ok() { "$1" -S -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; }
uvfind() {
  for u in uv "$LB/uv"; do
    p="$("$u" python find --no-project ">=3.10" 2>/dev/null)" && [ -n "$p" ] && { PY="$p"; return 0; }
  done
  return 0
}
PY=""
for p in python3 python; do ok "$p" && { PY="$p"; break; }; done
[ -n "$PY" ] || for f in "$LB"/python3.*; do ok "$f" && { PY="$f"; break; }; done
[ -n "$PY" ] || uvfind
if [ -z "$PY" ] && [ -z "$SESSIONCOST_NO_FETCH" ]; then
  echo "SessionCost: no Python 3.10+ on this computer. Fetching one through uv, into its own folder; nothing else changes."
  command -v uv >/dev/null 2>&1 || [ -x "$LB/uv" ] || curl -LsSf https://astral.sh/uv/install.sh 2>/dev/null | sh >/dev/null 2>&1
  for u in uv "$LB/uv"; do "$u" python install 3.12 >/dev/null 2>&1 && break; done
  uvfind
  [ -n "$PY" ] && echo "SessionCost: fetched Python 3.12 through uv."
fi
if [ -z "$PY" ]; then
  echo "SessionCost needs Python 3.10 or newer. None was found here, and fetching one through uv did not work"
  echo "(a sandbox may block downloads or writing to the user's folder)."
  echo "Agent: ask the user once to allow this same command outside the sandbox, then run it again. Try nothing else."
  echo "If it fails again, the user types these two lines once in Terminal, then asks again:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "  ~/.local/bin/uv python install 3.12"
  exit 3
fi
# -S: SessionCost needs only Python's own library; add-ons that load at every start can take seconds
exec "$PY" -S "$ROOT/run.py" "$@"
