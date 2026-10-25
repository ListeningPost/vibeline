"""Tool handlers and JSON schemas. mcp_server.py exposes these to Claude Code."""
from __future__ import annotations

import contextlib
import io
import os
import webbrowser
from pathlib import Path
from typing import Any

from .ledger import list_tasks, load_events, resolve_db
from .rails import render_dashboard, render_markdown
from . import sessions


def _setting_db() -> str:
    return os.environ.get("VIBELINE_DB", "").strip()


def _setting_tokens() -> bool:
    return os.environ.get("VIBELINE_SHOW_TOKENS", "").strip().lower() in ("1", "true", "yes", "on")


def _db(explicit: str | None) -> Path:
    return resolve_db(explicit or _setting_db() or None)


def rails_payload(task_id: str | None, db: Path, *, show_tokens: bool) -> dict[str, Any]:
    rows = load_events(db, task_id)
    md = render_markdown(rows, show_tokens=show_tokens)
    return {"ok": True, "task_id": rows[0]["task_id"], "db": str(db), "markdown": md}


def dashboard_payload(db: Path) -> dict[str, Any]:
    tasks = list_tasks(db)
    by_task = {t: load_events(db, t) for t in tasks}
    return {"ok": True, "db": str(db), "markdown": render_dashboard(by_task)}


def handle_vibeline_rails(args: dict) -> dict[str, Any]:
    db = _db(args.get("db"))
    return rails_payload(args.get("task_id"), db, show_tokens=bool(args.get("show_tokens", _setting_tokens())))


def handle_vibeline_dashboard(args: dict) -> dict[str, Any]:
    return dashboard_payload(_db(args.get("db")))


def handle_vibeline_sessions(args: dict) -> dict[str, Any]:
    limit = int(args.get("limit") or sessions.MAX_SESSIONS)
    data = sessions.sessions_data(limit=max(1, min(limit, 50)))
    return {"ok": True, "dir": data["db"], "totals": data["totals"], "markdown": sessions.render_sessions_markdown(data)}


def handle_vibeline_web(args: dict) -> dict[str, Any]:
    from .web import DEFAULT_PORT, start_background

    try:
        db: Path | None = _db(args.get("db"))
    except FileNotFoundError:
        db = None
    out = start_background(db, port=int(args.get("port") or DEFAULT_PORT))
    url = out["url"] + ("?src=ledger" if args.get("ledger") else "")
    if args.get("open", True):
        webbrowser.open(url)
    return {"ok": True, **out, "url": url, "ledger": str(db) if db else None}


def handle_vibeline_open(args: dict) -> dict[str, Any]:
    from .spawn import launch

    return launch(str(args.get("session_id") or ""), dry=bool(args.get("dry")))


def handle_vibeline_check(args: dict) -> dict[str, Any]:
    from .doctor import run_check

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run_check(args.get("db") or _setting_db() or None)
    return {"ok": code == 0, "report": buf.getvalue()}


_DB = {"type": "string", "description": "Optional ledger sqlite path. Default: VIBELINE_DB / TRACE_DB, else the bundled sample."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "vibeline_rails",
        "description": (
            "Render one task from the agent-harness ledger as markdown rails "
            "(parent lane, child rails from spawn, FAIL stays, expected TDD RED is not an AC fail). "
            "Default task is the latest in the ledger."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id. Omit for the latest task."},
                "db": _DB,
                "show_tokens": {"type": "boolean", "description": "Include token counts (off by default)."},
            },
        },
        "handler": handle_vibeline_rails,
    },
    {
        "name": "vibeline_dashboard",
        "description": "Markdown table of tasks × AC × fails × actors from the agent-harness ledger.",
        "inputSchema": {"type": "object", "properties": {"db": _DB}},
        "handler": handle_vibeline_dashboard,
    },
    {
        "name": "vibeline_sessions",
        "description": (
            "Markdown table of the most recently active Claude Code sessions on this machine "
            "(every project), with their subagents, status, event and token counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many sessions (default 8, max 50)."}},
        },
        "handler": handle_vibeline_sessions,
    },
    {
        "name": "vibeline_web",
        "description": (
            "Start (or reuse) the live vibeline web page on localhost and return its URL: every Claude Code session as a "
            "rail with subagents branching at spawn, or the harness ledger with ledger=true. The server lives as long as "
            "this Claude Code session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "Port (default 8877)."},
                "ledger": {"type": "boolean", "description": "Open the ledger view instead of sessions."},
                "open": {"type": "boolean", "description": "Open the page in the default browser (default true)."},
                "db": _DB,
            },
        },
        "handler": handle_vibeline_web,
    },
    {
        "name": "vibeline_open",
        "description": "Open a new terminal window running `claude --resume SESSION_ID` in that session's working directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id (a subagent id resumes its parent)."},
                "dry": {"type": "boolean", "description": "Only report the command that would run."},
            },
            "required": ["session_id"],
        },
        "handler": handle_vibeline_open,
    },
    {
        "name": "vibeline_check",
        "description": "Self-diagnostic: python version, ledger, Claude Code transcripts, claude on PATH, terminal for resume.",
        "inputSchema": {"type": "object", "properties": {"db": _DB}},
        "handler": handle_vibeline_check,
    },
]
