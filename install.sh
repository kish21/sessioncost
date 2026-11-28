#!/bin/sh
# SessionCost installer for macOS and Linux:
# Recommended (no remote script execution):
#   python3 -m pip install sessioncost==1.2.11 && python3 -m sessioncost setup
#   uv tool install sessioncost==1.2.11 && sessioncost setup
# Corporate / zero-network mode: pass `--offline` (or set `SESSIONCOST_OFFLINE=1`).
# First-time install only. To update an existing install, run `sessioncost update` (or `/sessioncost update` in a chat) — do not re-run the installer.
set -e
VERSION="${SESSIONCOST_VERSION:-1.2.11}"
SRC="${SESSIONCOST_SOURCE:-sessioncost==${VERSION}}"
OFFLINE=""
for arg in "$@"; do
  if [ "$arg" = "--offline" ]; then
    OFFLINE="--offline"
  fi
done
case "$(printf '%s' "${SESSIONCOST_OFFLINE:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) OFFLINE="--offline" ;;
esac

if command -v uv >/dev/null 2>&1; then
  echo "Installing SessionCost ($SRC) with uv..."
  uv tool install --quiet --reinstall --python ">=3.10" "$SRC"
  "$(uv tool dir --bin)/sessioncost" setup $OFFLINE
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then
  echo "Installing SessionCost ($SRC) with pip..."
  python3 -m pip install --user "$SRC"
  python3 -m sessioncost setup $OFFLINE
  exit 0
fi

echo "Neither uv nor Python 3.10+ was found. Install uv (e.g. 'brew install uv' or https://docs.astral.sh/uv/) or Python 3.10+, then run 'python3 -m pip install sessioncost==${VERSION}' and 'python3 -m sessioncost setup'." >&2
exit 1
