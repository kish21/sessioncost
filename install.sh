#!/bin/sh
# SessionCost installer for macOS and Linux:
#   curl -fsSL https://raw.githubusercontent.com/kish21/sessioncost/main/install.sh | sh
# Uses uv (https://docs.astral.sh/uv/), which brings its own Python when this computer has none.
# Run it again to update.
set -e
SRC="${SESSIONCOST_SOURCE:-https://github.com/kish21/sessioncost/archive/refs/heads/main.zip}"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (it fetches Python for SessionCost if needed)..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  else
    wget -qO- https://astral.sh/uv/install.sh | sh
  fi
  PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$HOME/.local/bin:$PATH"
fi

echo "Installing SessionCost..."
uv tool install --quiet --reinstall --python ">=3.10" "$SRC"
"$(uv tool dir --bin)/sessioncost" setup
