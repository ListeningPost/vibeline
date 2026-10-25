"""`vibeline check` — verify a fresh install end to end. Exit 1 on any hard failure."""
from __future__ import annotations

import shutil
import sys

from . import sessions
from .ledger import EXAMPLE_DB, list_tasks, resolve_db
from .web import PAGE


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
        _row(True, "rich available", "standalone terminal panel will render")
    except Exception:
        _row(None, "rich missing", "only affects the standalone `rails` panel; markdown and web are unaffected")

    claude = shutil.which("claude")
    _row(bool(claude) or None, "claude on PATH", claude or "not found (needed only for open/resume)")

    try:
        db = resolve_db(db_arg or None)
        tasks = list_tasks(db)
        src = "bundled sample" if db == EXAMPLE_DB else "configured"
        good &= _row(True, "ledger readable", f"{db}  ({src}, {len(tasks)} task{'s' if len(tasks) != 1 else ''})")
        if db == EXAMPLE_DB:
            _row(None, "using the bundled sample ledger", "set VIBELINE_DB (settings.json \"env\") to point at a real one")
    except Exception as e:
        good &= _row(False, "ledger", str(e))

    pdir = sessions.projects_dir()
    if pdir.is_dir():
        try:
            n = len(sessions.sessions_data().get("parents", []))
            total = len(sessions.session_files())
            _row(True, "Claude Code transcripts readable", f"{pdir}  ({total} sessions, {n} shown)")
        except Exception as e:
            _row(None, "Claude Code transcripts unreadable", f"{pdir}: {e}  (Sessions view will be empty)")
    else:
        _row(None, "no Claude Code transcripts yet", f"{pdir}  (set CLAUDE_CONFIG_DIR if yours live elsewhere)")

    good &= _row(PAGE.is_file(), "web page", str(PAGE))

    try:
        from .spawn import pick_terminal
        which, _ = pick_terminal()
        _row(True, "terminal for open/resume", which)
    except Exception as e:
        _row(None, "no terminal detected", f"{e}; set VIBELINE_TERMINAL=<name> (only affects open/resume)")

    print("\n" + ("all good" if good else "something failed above"))
    return 0 if good else 1
