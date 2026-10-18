"""Read the append-only task-trace sqlite. Writer is the harness MCP, not this plugin."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

EXAMPLE_DB = Path(__file__).resolve().parent / "examples" / "trace.db"

DEFAULT_CANDIDATES = (
    Path.home() / ".hermes/vibeline/trace.db",
    EXAMPLE_DB,  # bundled sample so a fresh install renders something
)


def resolve_db(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"ledger not found: {p}")
        return p
    env = os.environ.get("VIBELINE_DB") or os.environ.get("TRACE_DB")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    for c in DEFAULT_CANDIDATES:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "no ledger. Set plugins.entries.vibeline.settings.db, VIBELINE_DB, or TRACE_DB, "
        "or put one at ~/.hermes/vibeline/trace.db."
    )


def connect(db: Path) -> sqlite3.Connection:
    cx = sqlite3.connect(str(db), timeout=10)
    cx.row_factory = sqlite3.Row
    return cx


def list_tasks(db: Path) -> list[str]:
    with connect(db) as cx:
        return [r[0] for r in cx.execute("SELECT DISTINCT task_id FROM events ORDER BY id")]


def load_events(db: Path, task_id: str | None = None) -> list[dict[str, Any]]:
    with connect(db) as cx:
        if task_id:
            rows = cx.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
            if not rows:
                raise ValueError(f"unknown task {task_id}")
        else:
            last = cx.execute("SELECT task_id FROM events ORDER BY id DESC LIMIT 1").fetchone()
            if not last:
                raise ValueError("empty ledger")
            rows = cx.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id", (last[0],)
            ).fetchall()
    return [dict(r) for r in rows]


def is_expected_red(why: str | None, result: str | None) -> bool:
    r = (result or "").lower()
    if r in ("red", "unexpected_green"):
        return True
    return (why or "").lstrip().startswith("RED:")


_CHAIN_KEYS = (
    "ts", "task_id", "actor", "parent", "kind", "ac_id", "result",
    "why", "tokens_in", "tokens_out", "sha", "prev_hash",
)


def load_all(db: Path) -> list[dict[str, Any]]:
    with connect(db) as cx:
        return [dict(r) for r in cx.execute("SELECT * FROM events ORDER BY id")]


def verify_chain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute the writer's hash chain. Returns the first row that does not match."""
    prev = "genesis"
    for r in rows:
        canon = json.dumps({k: r.get(k) for k in _CHAIN_KEYS}, sort_keys=True, default=str).encode()
        if r["prev_hash"] != prev or hashlib.sha256(prev.encode() + canon).hexdigest() != r["hash"]:
            return {"ok": False, "bad_id": r["id"]}
        prev = r["hash"]
    return {"ok": True, "bad_id": None}
