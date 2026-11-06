"""Feedback mode: thumbs up / neutral / down plus a note on any event, and per-tool "performing poorly" flags.

Stored in vibeline's own SQLite file (never in Hermes's state.db). Export joins the ratings back onto the full
session activity as CSV or JSONL for fine-tuning.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

RATINGS = {1: "up", 0: "neutral", -1: "down"}
COLUMNS = [
    "machine", "session_id", "session_title", "agent", "lane_id", "lane_name", "event_id", "time", "kind", "label",
    "tools", "title", "content", "duration_ms", "wait_ms", "rewound", "rating", "note", "tool_flagged", "tool_note",
]
_SNAP = ("t", "kind", "label", "title", "context", "actor", "tools", "dur_ms", "wait_ms", "ghost")


def store_path() -> Path:
    explicit = os.environ.get("VIBELINE_FEEDBACK_DB", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("HERMES_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".hermes") / "vibeline" / "feedback.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(path, timeout=5)
    cx.row_factory = sqlite3.Row
    cx.executescript(
        """
        CREATE TABLE IF NOT EXISTS ratings (
          machine TEXT NOT NULL DEFAULT '', session TEXT NOT NULL, lane TEXT NOT NULL DEFAULT '', event TEXT NOT NULL,
          rating INTEGER, note TEXT NOT NULL DEFAULT '', snapshot TEXT, updated_at REAL NOT NULL,
          PRIMARY KEY (machine, session, event));
        CREATE TABLE IF NOT EXISTS tool_flags (
          tool TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
        """
    )
    return cx


def rate(path: Path, *, machine: str, session: str, event: str, rating: int | None, note: str = "",
         lane: str = "", snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Set or clear one event's rating. No rating and no note removes the row."""
    if rating is not None and rating not in RATINGS:
        raise ValueError("rating must be 1, 0, -1 or null")
    if not session or not event:
        raise ValueError("session and event are required")
    note = (note or "").strip()[:4000]
    snap = json.dumps({k: snapshot[k] for k in _SNAP if k in snapshot}) if snapshot else None
    with _connect(path) as cx:
        if rating is None and not note:
            cx.execute("DELETE FROM ratings WHERE machine=? AND session=? AND event=?", (machine, session, event))
        else:
            cx.execute(
                """INSERT INTO ratings (machine, session, lane, event, rating, note, snapshot, updated_at) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(machine, session, event) DO UPDATE SET rating=excluded.rating, note=excluded.note,
                     lane=excluded.lane, snapshot=COALESCE(excluded.snapshot, snapshot), updated_at=excluded.updated_at""",
                (machine, session, lane, event, rating, note, snap, time.time()),
            )
    return {"ok": True}


def flag_tool(path: Path, tool: str, bad: bool, note: str = "") -> dict[str, Any]:
    tool = (tool or "").strip()
    if not tool:
        raise ValueError("tool is required")
    with _connect(path) as cx:
        if bad:
            cx.execute(
                "INSERT INTO tool_flags VALUES (?,?,?) ON CONFLICT(tool) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at",
                (tool, (note or "").strip()[:4000], time.time()),
            )
        else:
            cx.execute("DELETE FROM tool_flags WHERE tool=?", (tool,))
    return {"ok": True}


def _rows(path: Path) -> tuple[list[sqlite3.Row], dict[str, str]]:
    if not path.is_file():
        return [], {}
    with _connect(path) as cx:
        return (
            cx.execute("SELECT * FROM ratings ORDER BY machine, session, updated_at").fetchall(),
            {r["tool"]: r["note"] for r in cx.execute("SELECT tool, note FROM tool_flags")},
        )


def summary(path: Path, key: Callable[[str, str], str] = lambda machine, raw: raw) -> dict[str, Any]:
    """Everything the page needs: ratings keyed by the event id the page uses, and flagged tools."""
    ratings, tools = _rows(path)
    return {
        "ratings": {key(r["machine"], r["event"]): {"rating": r["rating"], "note": r["note"]} for r in ratings},
        "tools": tools,
        "store": str(path),
    }


def _row(machine: str, parent: dict[str, Any], lane: dict[str, Any], e: dict[str, Any], event_id: str,
         mine: sqlite3.Row | None, tools: dict[str, str]) -> dict[str, Any]:
    used = e.get("tools") or []
    flagged = [t for t in used if t in tools]
    return {
        "machine": machine,
        "session_id": parent.get("id", ""),
        "session_title": parent.get("name", ""),
        "agent": e.get("actor") or parent.get("agent", ""),
        "lane_id": lane.get("id", ""),
        "lane_name": lane.get("name", ""),
        "event_id": event_id,
        "time": e.get("t", ""),
        "kind": e.get("raw_kind") or e.get("kind", ""),
        "label": e.get("label", ""),
        "tools": " ".join(used),
        "title": e.get("title", ""),
        "content": e.get("context", ""),
        "duration_ms": "" if e.get("dur_ms") is None else e["dur_ms"],
        "wait_ms": "" if e.get("wait_ms") is None else e["wait_ms"],
        "rewound": int(bool(e.get("ghost"))),
        "rating": RATINGS.get(mine["rating"], "") if mine else "",
        "note": mine["note"] if mine else "",
        "tool_flagged": " ".join(flagged),
        "tool_note": " | ".join(f"{t}: {tools[t]}" if tools[t] else t for t in flagged),
    }


def export_rows(path: Path, fetch: Callable[[str, str], dict[str, Any] | None], local_name: str = "",
                rated_only: bool = False) -> list[dict[str, Any]]:
    """One row per event of every session that has feedback (or only the rated events).

    `fetch(machine, session)` returns that session's page data with unclipped text, or None when the machine
    is unreachable or the session is gone; the snapshot taken at rating time covers those.
    """
    ratings, tools = _rows(path)
    by_session: dict[tuple[str, str], dict[str, sqlite3.Row]] = {}
    for r in ratings:
        by_session.setdefault((r["machine"], r["session"]), {})[r["event"]] = r
    out: list[dict[str, Any]] = []
    for (machine, session), mine in by_session.items():
        name = machine or local_name
        try:
            data = fetch(machine, session)
        except Exception:
            data = None
        seen: set[str] = set()
        for parent in (data or {}).get("parents", []):
            lanes = [{"id": parent["id"], "name": parent.get("agent", ""), "events": parent["events"]}, *parent.get("subs", [])]
            for lane in lanes:
                for e in lane["events"]:
                    seen.add(e["id"])
                    if rated_only and e["id"] not in mine:
                        continue
                    out.append(_row(name, parent, lane, e, e["id"], mine.get(e["id"]), tools))
        for event, r in mine.items():  # rated, but the live data no longer has it
            if event in seen:
                continue
            snap = json.loads(r["snapshot"]) if r["snapshot"] else {}
            out.append(_row(name, {"id": session}, {"id": r["lane"]}, snap, event, r, tools))
    return out


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def to_jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
