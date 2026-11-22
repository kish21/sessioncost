"""Runs SessionCost from this folder without installing it (used by the agent plugins, through run.cmd or run.sh):
`run.py <command>` for SessionCost, `run.py guard <command>` for the guard (the same as guard.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.argv[1:2] == ["guard"]:
    from sessioncost.guard import main  # noqa: E402
    sys.exit(main(sys.argv[2:]))
from sessioncost.cli import main  # noqa: E402

sys.exit(main())
