"""Tool handlers. register() in __init__.py wires these."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ledger import list_tasks, load_events, resolve_db
from .rails import render_dashboard, render_markdown


def _db(explicit: str | None, ctx_db: str | None) -> Path:
    return resolve_db(explicit or ctx_db or None)


def rails_payload(task_id: str | None, db: Path, *, show_tokens: bool) -> dict[str, Any]:
    rows = load_events(db, task_id)
    md = render_markdown(rows, show_tokens=show_tokens)
    return {"ok": True, "task_id": rows[0]["task_id"], "db": str(db), "markdown": md}


def dashboard_payload(db: Path) -> dict[str, Any]:
    tasks = list_tasks(db)
    by_task = {t: load_events(db, t) for t in tasks}
    return {"ok": True, "db": str(db), "markdown": render_dashboard(by_task)}


def handle_vibeline_rails(args: dict, *, db_setting: str = "", show_tokens: bool = False, **_: Any) -> str:
    try:
        db = _db(args.get("db"), db_setting)
        out = rails_payload(args.get("task_id"), db, show_tokens=bool(args.get("show_tokens", show_tokens)))
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def handle_vibeline_dashboard(args: dict, *, db_setting: str = "", **_: Any) -> str:
    try:
        db = _db(args.get("db"), db_setting)
        return json.dumps(dashboard_payload(db))
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


SCHEMA_RAILS = {
    "name": "vibeline_rails",
    "description": (
        "Render the agent-harness task ledger as markdown rails "
        "(parent lane, child rails from spawn, FAIL stays, TDD RED is not AC fail). "
        "Return markdown for the CLI session. Default task is the latest in the ledger."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task id. Omit for the latest task."},
            "db": {"type": "string", "description": "Optional sqlite path. Default TRACE_DB / plugin setting."},
            "show_tokens": {"type": "boolean", "description": "Include token counts (off by default)."},
        },
    },
}

SCHEMA_DASHBOARD = {
    "name": "vibeline_dashboard",
    "description": "Markdown table of tasks × AC × fails-to-green × actors from the harness ledger.",
    "parameters": {
        "type": "object",
        "properties": {
            "db": {"type": "string", "description": "Optional sqlite path."},
        },
    },
}
