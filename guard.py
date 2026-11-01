"""Runs the SessionCost guard from this folder without installing it (used by the Claude Code plugin's hooks and
the /livesession skill): `guard.py hook <event>` for a hook, `guard.py on|off|status` to switch it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sessioncost.guard import main  # noqa: E402

sys.exit(main())
