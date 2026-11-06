"""Rewind a Hermes session to one of its prompts, then reopen it to retry.

This is the one place vibeline writes to Hermes state, and it does so through Hermes's own
`SessionDB.rewind_to_message` (the call behind `/rewind`): every message from the chosen prompt
onward is soft-deleted (active=0, still in state.db), never removed. Files the agent changed are
not restored.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from . import spawn

MAX_QUERY = 100_000  # argv-safe; longer prompts are rewound but not resubmitted


class RewindError(ValueError):
    pass


def _session_db(db: Path) -> Any:
    try:
        from hermes_state import SessionDB
    except Exception as e:  # standalone `python3 . web` has no Hermes on the path
        raise RewindError(f"rewind needs Hermes: start the UI with `hermes vibeline web` ({e.__class__.__name__})")
    return SessionDB(db_path=db)


def target(db: Path, sid: str, event: str) -> dict[str, Any]:
    """Validate the rewind target read-only and say what the rewind would drop."""
    if not spawn._ok_id(sid):
        raise RewindError("bad session id")
    if not (event.startswith("m") and event[1:].isdigit()):
        raise RewindError("not a message event")
    mid = int(event[1:])
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    cx.row_factory = sqlite3.Row
    try:
        s = cx.execute("SELECT source FROM sessions WHERE id=?", (sid,)).fetchone()
        if not s:
            raise RewindError(f"unknown session {sid}")
        if s["source"] == "subagent":
            raise RewindError("subagent sessions cannot be rewound; rewind the parent")
        has_active = any(r[1] == "active" for r in cx.execute("PRAGMA table_info(messages)"))
        live = " AND active=1" if has_active else ""
        m = cx.execute(f"SELECT role, content FROM messages WHERE id=? AND session_id=?{live}", (mid, sid)).fetchone()
        if not m:
            raise RewindError("that message is not in the live transcript (already rewound?)")
        if m["role"] != "user":
            raise RewindError("can only rewind to a prompt")
        drops = cx.execute(f"SELECT COUNT(*) FROM messages WHERE session_id=? AND id>=?{live}", (sid, mid)).fetchone()[0]
    finally:
        cx.close()
    return {"session": sid, "message": mid, "drops": drops, "prompt": m["content"] if isinstance(m["content"], str) else ""}


def rewind(
    sid: str,
    event: str,
    db: Path,
    *,
    retry: bool = True,
    dry: bool = False,
    open_terminal: bool = True,
    popen: Callable[..., Any] | None = None,
    session_db: Callable[[Path], Any] = _session_db,
) -> dict[str, Any]:
    try:
        t = target(db, sid, event)
        if dry:
            return {"ok": True, "dry": True, **t}
        sdb = session_db(db)
        try:
            out = sdb.rewind_to_message(sid, t["message"])
        except (ValueError, RuntimeError) as e:  # not a user turn, or a turn is running right now
            raise RewindError(str(e) or e.__class__.__name__)
        finally:
            close = getattr(sdb, "close", None)
            if callable(close):
                close()
    except RewindError as e:
        return {"ok": False, "error": str(e)}
    text = (out.get("target_message") or {}).get("content")
    prompt = text if isinstance(text, str) else t["prompt"]
    res: dict[str, Any] = {"ok": True, "session": sid, "rewound": out.get("rewound_count", t["drops"]), "prompt": prompt}
    if open_terminal:
        resend = prompt if retry and prompt and len(prompt) <= MAX_QUERY else None
        kw = {"popen": popen} if popen else {}
        term = spawn.launch(sid, db, query=resend, **kw)
        res["retried"] = bool(resend and term.get("ok"))
        res["terminal"] = term.get("terminal")
        if not term.get("ok"):
            res["warning"] = f"rewound, but no terminal opened: {term.get('error')}"
    return res
