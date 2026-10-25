from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


sys.path.insert(0, str(ROOT))
EXAMPLE = ROOT / "vibeline" / "examples" / "trace.db"


def _load():
    import vibeline

    return vibeline


def test_red_is_not_fail():
    _load()
    from vibeline.rails import classify

    k, lab = classify(
        {
            "kind": "ac_check",
            "result": "fail",
            "why": "RED: 4 store tests failed as expected (unimplemented)",
            "ac_id": "AC-1",
        }
    )
    assert k == "red" and lab == "RED"
    k2, lab2 = classify({"kind": "ac_check", "result": "fail", "why": "tests still broken", "ac_id": "AC-1"})
    assert k2 == "fail" and lab2 == "FAIL"


def test_markdown_from_chirp_ledger():
    _load()
    from vibeline.ledger import load_events, resolve_db
    from vibeline.rails import render_markdown

    db = resolve_db(str(EXAMPLE))
    rows = load_events(db, "chirp-53869840")
    md = render_markdown(rows)
    assert md.startswith("# chirp-53869840")
    assert "```" in md
    assert "**RED**" in md
    assert "FAIL" not in md.split("## Lanes")[1].split("**cmds**")[0] or "RED" in md
    assert "SPAWNED" in md
    assert "● AC-1" in md
    assert "store" in md
    assert "Child rails begin at spawn" in md


def test_widget_data_chirp():
    _load()
    from vibeline.ledger import load_events, resolve_db
    from vibeline.rails import widget_data

    db = resolve_db(str(EXAMPLE))
    data = widget_data(load_events(db, "chirp-53869840"))
    assert data["task_id"] == "chirp-53869840"
    assert data["status"] == "complete"
    ids = [ln["id"] for ln in data["lanes"]]
    assert ids[0] == "parent"
    assert "store" in ids
    store = next(ln for ln in data["lanes"] if ln["id"] == "store")
    assert store["rail"][0]["x"] > 0
    assert any(e["k"] == "red" for e in store["events"])
    assert not any(e["k"] == "fail" for ln in data["lanes"] for e in ln["events"])
    assert all(a["met"] for a in data["ac"])


def test_web_data_chirp():
    _load()
    from vibeline.ledger import resolve_db
    from vibeline.web import web_data

    data = web_data(resolve_db(str(EXAMPLE)))
    assert data["chain"]["ok"]
    assert data["totals"]["tokens"] == data["totals"]["tokens_in"] + data["totals"]["tokens_out"]
    task = next(p for p in data["parents"] if p["id"] == "chirp-53869840")
    assert task["status"] == "complete"
    assert all(a["met"] for a in task["ac"])
    store = next(s for s in task["subs"] if s["id"] == "store")
    assert store["events"][0]["label"] == "SPAWNED"
    assert store["events"][0]["context"].startswith("Spawn store")
    ids = [e["id"] for p in data["parents"] for lane in [p["events"], *[s["events"] for s in p["subs"]]] for e in lane]
    assert len(ids) == len(set(ids))
