"""Markdown rails: parent lane, children from spawn, FAIL stays, RED is not FAIL."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .ledger import is_expected_red


def _child_of(why: str | None) -> str | None:
    if not why:
        return None
    m = re.search(r"Spawn (\w+)", why, re.I)
    return m.group(1).lower() if m else None


def _epoch(ts: str) -> float:
    t = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(t).timestamp()


def _clock(ts: str) -> str:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if d.tzinfo:
        d = d.astimezone()
    h = d.hour % 12 or 12
    ampm = "AM" if d.hour < 12 else "PM"
    return f"{h}:{d.minute:02d}:{d.second:02d} {ampm}"


def classify(row: dict[str, Any]) -> tuple[str, str]:
    kind, result, why = row.get("kind"), row.get("result"), row.get("why")
    if kind == "open":
        return "open", "OPEN"
    if kind == "spawn":
        return "spawn", "SPAWN"
    if kind == "close":
        return "done", "DONE"
    if kind == "ac_check" and result == "fail" and is_expected_red(why, result):
        return "red", "RED"
    if kind == "ac_check" and result == "fail":
        return "fail", "FAIL"
    if kind == "ac_check" and result == "pass":
        return "pass", "PASS"
    if kind == "decision" and is_expected_red(why, result):
        return "red", "RED"
    if kind == "decision":
        return "note", "NOTE"
    return kind or "note", (kind or "NOTE").upper()[:5]


GLYPH = {
    "open": "●",
    "done": "●",
    "pass": "●",
    "fail": "✕",
    "red": "○",
    "note": "○",
    "spawn": "◆",
    "fix": "○",
}


def group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    parent: list[dict[str, Any]] = []
    spawn_t: dict[str, str] = {}
    subs: dict[str, list[dict[str, Any]]] = {}
    acs: dict[str, dict[str, Any]] = {}
    task_id = rows[0]["task_id"] if rows else ""
    for r in rows:
        k, lab = classify(r)
        ev = {**r, "k": k, "label": lab, "child": _child_of(r.get("why")) if r.get("kind") == "spawn" else None}
        if r.get("kind") == "ac_check" and r.get("ac_id"):
            acs.setdefault(r["ac_id"], {"id": r["ac_id"], "met": False})
            if r.get("result") == "pass":
                acs[r["ac_id"]]["met"] = True
        actor = r.get("actor") or ""
        if actor == "cli:parent" or not str(actor).startswith("sub:"):
            parent.append(ev)
            if ev.get("child"):
                spawn_t[ev["child"]] = r["ts"]
        else:
            name = str(actor).split(":")[-1]
            subs.setdefault(name, []).append(ev)
    order = list(spawn_t.keys()) + [n for n in subs if n not in spawn_t]
    seen = []
    for n in order:
        if n not in seen:
            seen.append(n)
    children = []
    for name in seen:
        events = list(subs.get(name, []))
        st = spawn_t.get(name)
        if st and not any(e["ts"] == st for e in events):
            events.insert(
                0,
                {
                    "ts": st,
                    "k": "spawn",
                    "label": "SPAWNED",
                    "why": f"SPAWN {name}",
                    "actor": f"sub:{name}",
                    "kind": "spawn",
                    "id": f"spawned-{name}",
                },
            )
        events.sort(key=lambda e: e["ts"])
        real_fail = any(e["k"] == "fail" for e in events)
        children.append({"id": name, "spawn_t": st, "events": events, "blocked": real_fail})
    return {"task_id": task_id, "parent": parent, "children": children, "ac": list(acs.values())}


def _rail_line(name: str, events: list[dict[str, Any]], t0: float, span: float, width: int = 52) -> tuple[str, str]:
    cells = ["─"] * width
    labels: dict[int, str] = {}
    for e in events:
        if e.get("child") and e.get("k") == "spawn" and not str(e.get("actor", "")).startswith("sub:"):
            x = int((( _epoch(e["ts"]) - t0) / span) * (width - 1)) if span else 0
            x = max(0, min(width - 1, x))
            cells[x] = GLYPH["spawn"]
            continue
        x = int(((_epoch(e["ts"]) - t0) / span) * (width - 1)) if span else 0
        x = max(0, min(width - 1, x))
        cells[x] = GLYPH.get(e["k"], "○")
        labels[x] = e["label"]
    rail = "".join(cells)
    lab = [" "] * width
    for x, text in sorted(labels.items()):
        start = max(0, min(width - len(text), x - len(text) // 2))
        for i, ch in enumerate(text):
            if start + i < width and lab[start + i] == " ":
                lab[start + i] = ch
    return f"{name:<10} {rail}"


def _work_end(g: dict[str, Any]) -> float:
    ends = []
    for e in g["parent"]:
        if e["k"] in ("open", "spawn", "pass", "fail", "done"):
            ends.append(_epoch(e["ts"]))
    for c in g["children"]:
        for e in c["events"]:
            if e["k"] in ("spawn", "note", "red", "fail", "pass", "done"):
                ends.append(_epoch(e["ts"]))
    return max(ends) if ends else _epoch(g["parent"][-1]["ts"])


def _task_meta(g: dict[str, Any]) -> dict[str, Any]:
    parent, children = g["parent"], g["children"]
    t0 = _epoch(parent[0]["ts"])
    t1 = _work_end(g)
    span = max(t1 - t0, 1.0)
    dur_s = int(round(t1 - t0))
    dur = f"{dur_s // 60}m {dur_s % 60:02d}s" if dur_s >= 60 else f"{dur_s}s"
    status = "complete" if any(e["k"] == "done" for e in parent) else "open"
    if any(c["blocked"] for c in children) and status != "complete":
        status = "blocked"
    last = next((e for e in reversed(parent) if e["k"] == "done"), parent[-1])
    return {"t0": t0, "t1": t1, "span": span, "dur": dur, "status": status, "last": last}


def _pack_event(e: dict[str, Any], t0: float, span: float) -> dict[str, Any]:
    return {
        "id": str(e.get("id", "")),
        "t": e["ts"],
        "clock": _clock(e["ts"]),
        "k": e["k"],
        "label": e["label"],
        "why": ((e.get("why") or "").strip().split("\n")[0])[:160],
        "child": e.get("child"),
        "x": max(0.0, min(1.0, (_epoch(e["ts"]) - t0) / span)),
    }


def widget_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """JSON payload for the Ink TUI widget (and Rich panel)."""
    g = group(rows)
    if not g["parent"]:
        return {"task_id": g["task_id"], "status": "empty", "dur": "0s", "clock": "", "ac": [], "lanes": []}
    meta = _task_meta(g)
    t0, t1, span = meta["t0"], meta["t1"], meta["span"]

    def rail_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for e in events:
            if _epoch(e["ts"]) - t1 > 1.0:
                continue
            out.append(_pack_event(e, t0, span))
        return out

    parent_rail_src = [e for e in g["parent"] if e["k"] in ("open", "spawn", "pass", "fail", "done")]
    lanes = [
        {
            "id": "parent",
            "actor": "cli:parent",
            "blocked": False,
            "events": [_pack_event(e, t0, span) for e in g["parent"]],
            "rail": rail_of(parent_rail_src),
        }
    ]
    for c in g["children"]:
        lanes.append(
            {
                "id": c["id"],
                "actor": f"sub:{c['id']}",
                "blocked": c["blocked"],
                "events": [_pack_event(e, t0, span) for e in c["events"]],
                "rail": rail_of(c["events"]),
            }
        )
    last = meta["last"]
    return {
        "task_id": g["task_id"],
        "status": meta["status"],
        "dur": meta["dur"],
        "clock": f"{_clock(g['parent'][0]['ts'])} → {_clock(last['ts'])}",
        "ac": g["ac"],
        "lanes": lanes,
    }


def render_markdown(rows: list[dict[str, Any]], *, show_tokens: bool = False) -> str:
    g = group(rows)
    parent, children = g["parent"], g["children"]
    if not parent:
        return "_empty task_"
    t0 = _epoch(parent[0]["ts"])
    t1 = _work_end(g)
    span = max(t1 - t0, 1.0)
    dur_s = int(round(t1 - t0))
    dur = f"{dur_s // 60}m {dur_s % 60:02d}s" if dur_s >= 60 else f"{dur_s}s"
    status = "complete" if any(e["k"] == "done" for e in parent) else "open"
    if any(c["blocked"] for c in children) and status != "complete":
        status = "blocked"
    last = next((e for e in reversed(parent) if e["k"] == "done"), parent[-1])
    parent_rail = [e for e in parent if e["k"] in ("open", "spawn", "pass", "fail", "done")]
    lines = [
        f"# {g['task_id']}",
        "",
        f"parent · {status} · {dur} · {_clock(parent[0]['ts'])} → {_clock(last['ts'])}",
        "",
        "Child rails begin at spawn. FAIL stays. Expected TDD RED is not an AC fail.",
        "",
        "```",
    ]
    lines.append(_rail_line("parent", parent_rail, t0, span))
    for c in children:
        tag = c["id"] + (" !" if c["blocked"] else "")
        lines.append(_rail_line(tag[:10], c["events"], t0, span))
    lines.append("```")
    lines += ["", "## Acceptance", ""]
    if g["ac"]:
        for a in g["ac"]:
            lines.append(f"- {'●' if a['met'] else '○'} {a['id']}")
    else:
        lines.append("- _none yet_")
    lines += ["", "## Lanes", ""]
    lines.append("**parent** (`cli:parent`)")
    for e in parent:
        extra = f" → {e['child']}" if e.get("child") else ""
        why = ((e.get("why") or "").strip().split("\n")[0])[:100]
        tok = ""
        if show_tokens:
            n = (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
            if n:
                tok = f" · {n} tok"
        lines.append(f"- `{_clock(e['ts'])}` **{e['label']}**{extra}{tok} — {why}")
    for c in children:
        st = "blocked" if c["blocked"] else "complete"
        lines.append("")
        lines.append(f"**{c['id']}** (`sub:{c['id']}` · {st})")
        for e in c["events"]:
            why = ((e.get("why") or "").strip().split("\n")[0])[:100]
            lines.append(f"- `{_clock(e['ts'])}` **{e['label']}** — {why}")
    return "\n".join(lines) + "\n"


def render_dashboard(rows_by_task: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["# Vibeline dashboard", "", "| task | AC | fails | actors | state |", "|---|---|---|---|---|"]
    for task_id, rows in rows_by_task.items():
        g = group(rows)
        met = sum(1 for a in g["ac"] if a["met"])
        n = len(g["ac"])
        fails = sum(1 for e in rows if classify(e)[0] == "fail")
        actors = sorted({r.get("actor") or "" for r in rows})
        state = "closed" if any(e["k"] == "done" for e in g["parent"]) else "open"
        if fails and state != "closed":
            state = "blocked"
        lines.append(
            f"| `{task_id}` | {met}/{n} | {fails} | {len(actors)} | {state} |"
        )
    return "\n".join(lines) + "\n"
