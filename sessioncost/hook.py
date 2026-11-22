"""Starts the guard from an installed SessionCost (one-line install, npm, pip) with `python -S hook.py hook <event>`.

-S skips the add-ons that load at every Python start: on the owner's PC 2026-11-22 they cost 1.4 s at every guard
check, twice per step (before and after). The guard needs only Python's own library, so this file puts the folder
that holds the sessioncost package on the path itself and runs the guard."""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
if sys.path and os.path.abspath(sys.path[0] or os.curdir) == here:
    sys.path.pop(0)                    # the package's own folder: its modules must never stand in for Python's own
sys.path.insert(0, os.path.dirname(here))

from sessioncost.guard import main  # noqa: E402

sys.exit(main())
