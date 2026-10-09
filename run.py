"""Runs SessionCost from this folder without installing it (used by the Claude Code plugin)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sessioncost.cli import main  # noqa: E402

sys.exit(main())
