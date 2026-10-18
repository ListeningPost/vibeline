"""Read live Hermes sessions (state.db) as rails: one lane per session, subagents as children.

Read-only. Every Hermes process on the machine writes the same state.db, so this sees them all.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_SESSIONS = 8
LIVE_WINDOW_S = 15 * 60  # an unclosed session with no message for this long is idle, not running
_TEXT_CAP = 4000


def state_db() -> Path:
    home = os.environ.get("HERMES_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".hermes") / "state.db"


def _connect(db: Path) -> sqlite3.Connection:
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    cx.row_factory = sqlite3.Row
    return cx


def get(db: Path, sid: str) -> dict[str, Any] | None:
    with _connect(db) as cx:
        row = cx.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return dict(row) if row else None


def version(db: Path) -> str:
    with _connect(db) as cx:
        m = cx.execute("SELECT MAX(id) FROM messages").fetchone()[0]
        s = cx.execute(
            "SELECT COUNT(*), SUM(ended_at IS NOT NULL), SUM(input_tokens + output_tokens) FROM sessions"
        ).fetchone()
    # The minute bucket lets a quiet session flip from running to idle without a new row.
    return f"{m}:{s[0]}:{s[1]}:{s[2]}:{int(time.time() // 60)}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _first_line(text: str | None) -> str:
    return next((ln.strip() for ln in (text or "").split("\n") if ln.strip()), "")


def _clip(text: str | None, cap: int = _TEXT_CAP) -> str:
    text = text or ""
    return text if len(text) <= cap else text[:cap] + f"\n… {len(text) - cap:,} more characters"


def _calls(raw: str | None) -> list[dict[str, str]]:
    try:
        items = json.loads(raw) if raw else []
    except ValueError:
        return []
    out = []
    for c in items if isinstance(items, list) else []:
        fn = c.get("function") or {}
        out.append({"id": c.get("id") or c.get("call_id") or "", "name": fn.get("name") or "tool", "args": fn.get("arguments") or ""})
    return out


def _event(eid: str, ts: float, kind: str, label: str, title: str, brief: str, context: str, actor: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": eid,
        "t": _iso(ts),
        "kind": kind,
        "label": label,
        "title": title,
        "brief": brief[:200] or label,
        "context": context,
        "actor": actor,
        "parent": None,
        "raw_kind": extra.pop("raw_kind", kind),
        "ac_id": None,
        "result": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens": 0,
        "sha": None,
        "hash": None,
        "prev_hash": None,
        "n": 1,
        **extra,
    }


def _lane_events(s: sqlite3.Row, msgs: list[sqlite3.Row], actor: str) -> list[dict[str, Any]]:
    results = {m["tool_call_id"]: m for m in msgs if m["role"] == "tool" and m["tool_call_id"]}
    events: list[dict[str, Any]] = []
    for m in msgs:
        role, text, kind_tag = m["role"], m["content"] or "", m["display_kind"]
        eid = f"m{m['id']}"
        if role == "tool" or kind_tag == "hidden":
            continue
        if role == "user":
            if kind_tag in ("async_delegation_complete", "process_complete"):
                events.append(_event(eid, m["timestamp"], "note", "RETURN", "Background work returned", _first_line(text), _clip(text), actor, raw_kind=kind_tag, quiet=True))
            else:
                label = "STEER" if kind_tag == "steer" else "PROMPT"
                events.append(_event(eid, m["timestamp"], "open", label, _first_line(text)[:72] or label, _first_line(text), _clip(text), actor, raw_kind="user"))
            continue
        if kind_tag == "failed_turn":
            events.append(_event(eid, m["timestamp"], "fail", "FAIL", "Turn failed", _first_line(text), _clip(text), actor, raw_kind=kind_tag))
            continue
        calls = _calls(m["tool_calls"])
        if not calls:
            events.append(_event(eid, m["timestamp"], "pass", "REPLY", _first_line(text)[:72] or "Reply", _first_line(text), _clip(text), actor, raw_kind="assistant"))
            continue
        names = [c["name"] for c in calls]
        parts = [text.strip()] if text.strip() else []
        for c in calls:
            res = results.get(c["id"])
            parts.append(f"▸ {c['name']}  {_clip(c['args'], 1500)}" + (f"\n→ {_clip(res['content'], 1500)}" if res else "\n→ (running)"))
        uniq = list(dict.fromkeys(names))
        events.append(
            _event(
                eid,
                m["timestamp"],
                "spawn" if "delegate_task" in names else "note",
                uniq[0].upper()[:9],
                ", ".join(uniq) + (f" ({len(calls)} calls)" if len(calls) > 1 else ""),
                _first_line(text) or _first_line(calls[0]["args"]),
                "\n\n".join(parts),
                actor,
                raw_kind="tool_call",
                quiet=True,
                n=len(calls),
            )
        )
    if s["ended_at"]:
        reason = s["end_reason"] or "closed"
        events.append(_event(f"end-{s['id']}", s["ended_at"], "done", "END", f"Session ended · {reason}", reason, reason, actor, raw_kind="end"))
    return events


def _status(s: sqlite3.Row, msgs: list[sqlite3.Row], now: float) -> str:
    if s["ended_at"]:
        return "complete"
    last = msgs[-1]["timestamp"] if msgs else s["started_at"]
    return "in_progress" if now - last < LIVE_WINDOW_S else "idle"


def _tokens(s: sqlite3.Row) -> tuple[int, int]:
    return s["input_tokens"] or 0, s["output_tokens"] or 0


def _meta(s: sqlite3.Row) -> list[list[str]]:
    rows = [
        ("session", s["id"]),
        ("model", s["model"]),
        ("cwd", s["cwd"]),
        ("branch", s["git_branch"]),
        ("api calls", s["api_call_count"]),
        ("tool calls", s["tool_call_count"]),
        ("cache read", f"{s['cache_read_tokens']:,}" if s["cache_read_tokens"] else None),
        ("reasoning", f"{s['reasoning_tokens']:,}" if s["reasoning_tokens"] else None),
    ]
    return [[k, str(v)] for k, v in rows if v]


def sessions_data(db: Path, limit: int = MAX_SESSIONS, only: str | None = None) -> dict[str, Any]:
    """The most recently active sessions, or just `only` when a session id is given."""
    now = time.time()
    with _connect(db) as cx:
        tops = cx.execute("SELECT * FROM sessions WHERE id=?", (only,)).fetchall() if only else cx.execute(
            """SELECT s.*, COALESCE((SELECT MAX(timestamp) FROM messages m WHERE m.session_id = s.id), s.started_at) AS last_t
               FROM sessions s WHERE s.source != 'subagent' ORDER BY last_t DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        def messages(sid: str) -> list[sqlite3.Row]:
            return cx.execute(
                "SELECT id, role, content, tool_call_id, tool_calls, tool_name, timestamp, display_kind FROM messages WHERE session_id=? ORDER BY id",
                (sid,),
            ).fetchall()

        parents = []
        for s in tops:
            msgs = messages(s["id"])
            actor = f"{s['source']} · {s['model'] or 'model unknown'}"
            events = _lane_events(s, msgs, actor)
            kids = cx.execute(
                "SELECT * FROM sessions WHERE parent_session_id=? AND source='subagent' ORDER BY started_at", (s["id"],)
            ).fetchall()
            subs = []
            tin, tout = _tokens(s)
            for i, k in enumerate(kids, 1):
                kmsgs = messages(k["id"])
                goal = next((m["content"] for m in kmsgs if m["role"] == "user"), "") or ""
                kactor = f"subagent · {k['model'] or 'model unknown'}"
                kev = _lane_events(k, kmsgs, kactor)
                kev.insert(0, _event(f"spawned-{k['id']}", k["started_at"], "spawn", "SPAWNED", f"Subagent {i} spawned", _first_line(goal), _clip(goal), kactor, raw_kind="spawn"))
                kev = [e for e in kev if not (e["label"] == "PROMPT" and e["context"] == kev[0]["context"])]
                events.append(_event(f"sp-{k['id']}", k["started_at"], "spawn", "SPAWN", f"Spawned subagent {i}", _first_line(goal), _clip(goal), actor, raw_kind="spawn", child=k["id"]))
                kin, kout = _tokens(k)
                tin, tout = tin + kin, tout + kout
                subs.append(
                    {
                        "id": k["id"],
                        "name": (_first_line(goal)[:30] or f"subagent {i}"),
                        "status": _status(k, kmsgs, now),
                        "tokens": kin + kout,
                        "meta": _meta(k),
                        "events": sorted(kev, key=lambda e: e["t"]),
                    }
                )
            if not events:
                continue
            events.sort(key=lambda e: e["t"])
            own_in, own_out = _tokens(s)
            parents.append(
                {
                    "id": s["id"],
                    "name": s["title"] or s["id"],
                    "agent": actor,
                    "status": _status(s, msgs, now),
                    "tokens_in": tin,
                    "tokens_out": tout,
                    "tokens": tin + tout,
                    "lane_tokens": own_in + own_out,
                    "goal": next((e["context"] for e in events if e["label"] == "PROMPT"), ""),
                    "meta": _meta(s),
                    "ac": [],
                    "events": events,
                    "subs": subs,
                }
            )
    tin, tout = sum(p["tokens_in"] for p in parents), sum(p["tokens_out"] for p in parents)
    lanes = [p for p in parents] + [s for p in parents for s in p["subs"]]
    if only and not parents:
        raise ValueError(f"unknown session {only}")
    return {
        "src": "sessions",
        "version": version(db),
        "db": str(db),
        "totals": {
            "tokens_in": tin,
            "tokens_out": tout,
            "tokens": tin + tout,
            "tasks": len(parents),
            "events": sum(len(x["events"]) for x in lanes),
            "running": sum(1 for x in lanes if x["status"] == "in_progress"),
        },
        "parents": parents,
    }
