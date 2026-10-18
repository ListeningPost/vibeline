"""`hermes vibeline check` — verify a fresh install end to end. Exit 1 on any hard failure."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import sessions
from .ledger import EXAMPLE_DB, list_tasks, resolve_db
from .widget import TEMPLATE, widgets_dir


def _row(ok: bool | None, label: str, detail: str = "") -> bool:
    mark = {True: "ok  ", False: "FAIL", None: "warn"}[ok]
    print(f"  [{mark}] {label}" + (f"  {detail}" if detail else ""))
    return ok is not False


def run_check(db_arg: str | None = None) -> int:
    print("vibeline check")
    good = True

    v = sys.version_info
    good &= _row(v >= (3, 10), "python >= 3.10", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")

    try:
        import rich  # noqa: F401
        _row(True, "rich available", "classic-CLI panel will render")
    except Exception:
        _row(None, "rich missing", "panel falls back to plain text; Hermes ships rich, so run this via `hermes vibeline check`")

    hermes = shutil.which("hermes")
    _row(bool(hermes) or None, "hermes on PATH", hermes or "not found (standalone mode still works: python3 <dir> ...)")

    home = Path(os.environ.get("HERMES_HOME", "").strip() or Path.home() / ".hermes")
    plugin_link = home / "plugins" / "vibeline"
    here = Path(__file__).resolve().parent
    if plugin_link.exists():
        target = plugin_link.resolve()
        _row(target == here or None, "plugin linked", f"{plugin_link} -> {target}" + ("" if target == here else "  (points elsewhere)"))
    else:
        _row(None, "plugin not linked", f"expected {plugin_link}; run install.sh or `hermes plugins install {here}`")

    try:
        db = resolve_db(db_arg or None)
        tasks = list_tasks(db)
        src = "bundled sample" if db == EXAMPLE_DB else "configured"
        good &= _row(True, "ledger readable", f"{db}  ({src}, {len(tasks)} task{'s' if len(tasks) != 1 else ''})")
        if db == EXAMPLE_DB:
            _row(None, "using the bundled sample ledger", "set plugins.entries.vibeline.settings.db or VIBELINE_DB to point at a real one")
    except Exception as e:
        good &= _row(False, "ledger", str(e))

    sdb = sessions.state_db()
    if sdb.is_file():
        try:
            n = len(sessions.sessions_data(sdb).get("parents", []))
            _row(True, "hermes state.db readable", f"{sdb}  ({n} recent session{'s' if n != 1 else ''})")
        except Exception as e:
            _row(None, "hermes state.db unreadable", f"{sdb}: {e}  (Sessions tab will be empty)")
    else:
        _row(None, "no hermes state.db yet", f"{sdb}  (appears after the first Hermes session)")

    good &= _row(TEMPLATE.is_file(), "TUI widget template", str(TEMPLATE))
    wd = widgets_dir()
    try:
        wd.mkdir(parents=True, exist_ok=True)
        probe = wd / ".vibeline-write-test"
        probe.write_text("")
        probe.unlink()
        _row(True, "tui-widgets dir writable", str(wd))
    except Exception as e:
        good &= _row(False, "tui-widgets dir not writable", f"{wd}: {e}")

    try:
        from .spawn import pick_terminal
        which, _ = pick_terminal()
        _row(True, "terminal emulator for `open`", which)
    except Exception as e:
        _row(None, "no terminal emulator detected", f"{e}; set VIBELINE_TERMINAL=<name> (only affects open/resume)")

    print("\n" + ("all good" if good else "something failed above"))
    return 0 if good else 1
