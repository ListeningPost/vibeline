"""Where Hermes keeps its state."""
from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    """The Hermes home. Under Hermes it comes from `hermes_constants.get_hermes_home`, which follows the active
    profile; run standalone (`python3 . web`) there is no Hermes, so it is $HERMES_HOME or ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home
    except ImportError:
        home = os.environ.get("HERMES_HOME", "").strip()
        return Path(home).expanduser() if home else Path.home() / ".hermes"
    return Path(get_hermes_home())
