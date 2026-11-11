"""Read Hermes Kanban Swarm runs (kanban.db) as rails. Read-only.

A swarm is a task graph on the kanban board: a planning root (the shared blackboard), parallel workers, a verifier
waiting on every worker, a synthesizer waiting on the verifier. The root is the parent lane; every other card is a
child rail that starts where the card was created.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .paths import hermes_home
from .sessions import _clip, _event, _first_line

BLACKBOARD = "[swarm:blackboard] "
MAX_SWARMS = 8
_BAD = ("fail", "crash", "timed_out", "gave_up", "blocked", "stale", "rejected")
_SKIP = ("created", "commented", "completed")
_STATUS = {"done": "complete", "archived": "complete", "running": "in_progress", "blocked": "blocked"}


def kanban_dbs() -> list[Path]:
    """The default board plus any named boards."""
    pin = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if pin:
        return [Path(pin).expanduser()]
    root = hermes_home()
    return [p for p in [root / "kanban.db", *sorted((root / "kanban" / "boards").glob("*/kanban.db"))] if p.is_file()]


def _connect(db: Path) -> sqlite3.Connection:
    cx = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    cx.row_factory = sqlite3.Row
    return cx


def version(dbs: list[Path] | None = None) -> str:
    parts = []
    for db in kanban_dbs() if dbs is None else dbs:
        with _connect(db) as cx:
            e = cx.execute("SELECT MAX(id) FROM task_events").fetchone()[0]
            c = cx.execute("SELECT MAX(id) FROM task_comments").fetchone()[0]
            r = cx.execute("SELECT COUNT(*), SUM(ended_at IS NOT NULL) FROM task_runs").fetchone()
        parts.append(f"{e}:{c}:{r[0]}:{r[1]}")
    return "|".join(parts) or "none"


def _payload(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except ValueError:
        return raw


def _card_events(cx: sqlite3.Connection, task: sqlite3.Row, actor: str, tag: str) -> list[dict[str, Any]]:
    tid = task["id"]
    out = []
    for ev in cx.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (tid,)):
        kind = ev["kind"]
        if kind in _SKIP:  # the card, its comments and its runs already draw these
            continue
        k = "fail" if any(b in kind for b in _BAD) else "pass" if kind == "completed" else "note"
        body = _payload(ev["payload"])
        out.append(_event(f"{tag}e{ev['id']}", ev["created_at"], k, kind.upper()[:9], kind.replace("_", " ").capitalize(),
                          _first_line(body) or kind, _clip(body), actor, raw_kind=kind, quiet=k == "note"))
    for c in cx.execute("SELECT * FROM task_comments WHERE task_id=? ORDER BY id", (tid,)):
        body = c["body"] or ""
        if body.startswith(BLACKBOARD):
            try:
                p = json.loads(body[len(BLACKBOARD):])
            except ValueError:
                p = {}
            key = str(p.get("key") or "update")
            text = json.dumps(p.get("value"), indent=2, ensure_ascii=False)
            out.append(_event(f"{tag}c{c['id']}", c["created_at"], "note", "BOARD", f"Blackboard · {key}", f"{key} by {c['author']}",
                              _clip(text), c["author"], raw_kind="blackboard", quiet=key == "topology"))
        else:
            out.append(_event(f"{tag}c{c['id']}", c["created_at"], "note", "COMMENT", _first_line(body)[:72] or "Comment",
                              _first_line(body), _clip(body), c["author"], raw_kind="comment"))
    for r in cx.execute("SELECT * FROM task_runs WHERE task_id=? ORDER BY id", (tid,)):
        if not r["ended_at"]:
            continue
        ok = r["outcome"] == "completed"
        text = "\n\n".join(x for x in (r["summary"], _payload(r["metadata"]), r["error"]) if x)
        out.append(_event(f"{tag}r{r['id']}", r["ended_at"], "pass" if ok else "fail", "PASS" if ok else (r["outcome"] or "FAIL").upper()[:9],
                          f"Run {r['id']} {r['outcome'] or 'ended'}", _first_line(text) or (r["outcome"] or ""), _clip(text), actor,
                          raw_kind="run", dur_ms=max(0, r["ended_at"] - r["started_at"]) * 1000, tools=[r["profile"] or actor]))
    return sorted(out, key=lambda e: e["t"])


def _meta(task: sqlite3.Row, runs: int) -> list[list[str]]:
    rows = [("task", task["id"]), ("assignee", task["assignee"]), ("card status", task["status"]), ("runs", runs or None),
            ("workspace", task["workspace_path"]), ("model", task["model_override"]), ("last failure", task["last_failure_error"])]
    return [[k, str(v)] for k, v in rows if v]


def _swarms(cx: sqlite3.Connection, board: str, only: str | None) -> list[dict[str, Any]]:
    tops: dict[str, dict[str, Any]] = {}
    for c in cx.execute("SELECT task_id, body FROM task_comments WHERE body LIKE ? ORDER BY id", (BLACKBOARD + "%",)):
        try:
            p = json.loads(c["body"][len(BLACKBOARD):])
        except ValueError:
            continue
        if p.get("key") == "topology" and isinstance(p.get("value"), dict):
            tops[c["task_id"]] = p["value"]
    parents = []
    for root_id, topo in tops.items():
        pid = f"{board}:{root_id}"
        if only and only != pid:
            continue
        root = cx.execute("SELECT * FROM tasks WHERE id=?", (root_id,)).fetchone()
        if not root:
            continue
        roles = [(str(w), f"worker {i}") for i, w in enumerate(topo.get("worker_ids") or [], 1)]
        roles += [(str(topo[k]), role) for k, role in (("verifier_id", "verifier"), ("synthesizer_id", "synthesizer")) if topo.get(k)]
        actor = f"swarm · {root['assignee'] or 'orchestrator'}"
        tag = f"{board}:"
        events = [_event(f"{tag}open-{root_id}", root["created_at"], "open", "SWARM", root["title"], _first_line(topo.get("goal") or root["title"]),
                         _clip(topo.get("goal") or root["body"] or ""), actor, raw_kind="swarm")]
        # The root is created blocked and completed at once so workers can start; neither is news.
        events += [e for e in _card_events(cx, root, actor, tag) if e["raw_kind"] not in ("run", "blocked")]
        subs, last = [], None
        for tid, role in roles:
            t = cx.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not t:
                continue
            last = t
            who = f"{role} · {t['assignee'] or 'unassigned'}"
            runs = cx.execute("SELECT COUNT(*) FROM task_runs WHERE task_id=?", (tid,)).fetchone()[0]
            kev = [_event(f"{tag}spawned-{tid}", t["created_at"], "spawn", "SPAWNED", f"{role} card created", _first_line(t["title"]),
                          _clip(t["body"] or ""), who, raw_kind="spawn"), *_card_events(cx, t, who, tag)]
            events.append(_event(f"{tag}sp-{tid}", t["created_at"], "spawn", "SPAWN", f"Spawned {role}", _first_line(t["title"]),
                                 _clip(t["body"] or ""), actor, raw_kind="spawn", child=f"{tag}{tid}"))
            subs.append({"id": f"{tag}{tid}", "name": f"{role} · {t['title']}"[:40], "role": role, "status": _STATUS.get(t["status"], "idle"),
                         "tokens": 0, "meta": _meta(t, runs), "events": kev})
        closed = bool(last and last["id"] == str(topo.get("synthesizer_id")) and last["status"] in ("done", "archived"))
        if closed:
            events.append(_event(f"{tag}end-{root_id}", last["completed_at"] or last["created_at"], "done", "DONE", "Swarm synthesized",
                                 _first_line(last["result"] or "") or "synthesizer finished", _clip(last["result"] or ""), actor, raw_kind="end"))
        states = {s["status"] for s in subs}
        status = "complete" if closed else "blocked" if "blocked" in states else "in_progress" if "in_progress" in states else "idle"
        parents.append({"id": pid, "name": root["title"], "agent": actor, "status": status, "tokens_in": 0, "tokens_out": 0, "tokens": 0,
                        "lane_tokens": 0, "goal": topo.get("goal") or "", "meta": _meta(root, 0), "ac": [],
                        "events": sorted(events, key=lambda e: e["t"]), "subs": subs})
    return parents


def swarm_data(dbs: list[Path] | None = None, limit: int = MAX_SWARMS, only: str | None = None) -> dict[str, Any]:
    dbs = kanban_dbs() if dbs is None else dbs
    parents: list[dict[str, Any]] = []
    for db in dbs:
        board = db.parent.name if db.parent.parent.name == "boards" else "default"
        with _connect(db) as cx:
            parents += _swarms(cx, board, only)
    if only and not parents:
        raise ValueError(f"unknown swarm {only}")
    parents.sort(key=lambda p: max(e["t"] for l in [p, *p["subs"]] for e in l["events"]), reverse=True)
    parents = parents[:limit]
    lanes = parents + [s for p in parents for s in p["subs"]]
    return {
        "src": "swarm",
        "version": version(dbs),
        "db": ", ".join(str(d) for d in dbs) or "no kanban.db yet (hermes kanban swarm …)",
        "totals": {"tokens_in": 0, "tokens_out": 0, "tokens": 0, "tasks": len(parents),
                   "events": sum(len(x["events"]) for x in lanes), "running": sum(1 for x in lanes if x["status"] == "in_progress")},
        "parents": parents,
    }
