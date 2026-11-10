#!/usr/bin/env bash
# SessionCost guard launcher for Claude Code hooks. Turned off: it exits at once, before starting Python, so a
# chat without the guard pays almost nothing per step. On (the default): it finds a Python 3.10+ once, remembers it, and runs
# guard.py with the hook's JSON on stdin; no Python on the PATH: the one uv manages. None at all: it passes (the
# guard cannot run) and says so on stderr.
# Stop (once per answer) also starts Python while reports after each answer are on (the default; `sessioncost auto off`).
# Unless `sessioncost fix auto off`, session start, each message and Stop start it too (the to-do list after each answer).
G="${SESSIONCOST_GUARD_DIR:-$HOME/.sessioncost/guard}"
[ -f "$G/pending.json" ] || ls "$G"/*.state.json >/dev/null 2>&1 || ! grep -q '"on": false' "$G/config.json" 2>/dev/null || \
  { [ "$1" = stop ] && ! grep -q '"auto": false' "$G/config.json" 2>/dev/null; } || \
  { case "$1" in session-start|prompt|stop) ! grep -q '"fix_auto": false' "$G/config.json" 2>/dev/null;; *) false;; esac; } || exit 0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$(cat "$G/python" 2>/dev/null)"
if [ -z "$PY" ] || ! command -v "$PY" >/dev/null 2>&1; then
  PY=""
  for p in python3 python py; do
    if "$p" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then PY="$p"; break; fi
  done
  # no Python on the PATH: the one uv manages (/sessioncost or /liveguard fetched it on a computer without Python)
  if [ -z "$PY" ]; then
    for u in uv "$HOME/.local/bin/uv" "$HOME/.local/bin/uv.exe"; do
      p="$("$u" python find --no-project ">=3.10" 2>/dev/null)" && [ -n "$p" ] && { PY="$p"; break; }
    done
  fi
  [ -n "$PY" ] || { echo "SessionCost guard: no Python 3.10+ found, so it cannot check this step" >&2; exit 0; }
  mkdir -p "$G" && printf '%s' "$PY" > "$G/python"
fi
exec "$PY" "$ROOT/guard.py" hook "$@"
