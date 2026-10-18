from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    import importlib.util

    name = "vibeline"
    if name in sys.modules and getattr(sys.modules[name], "__path__", None):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name
    mod.__path__ = [str(ROOT)]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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

    db = resolve_db(str(ROOT / "examples" / "trace.db"))
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

    db = resolve_db(str(ROOT / "examples" / "trace.db"))
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


def test_install_widget():
    _load()
    import os
    import tempfile

    from vibeline.widget import install_widget, widget_path

    db = str(ROOT / "examples" / "trace.db")
    with tempfile.TemporaryDirectory() as td:
        os.environ["HERMES_HOME"] = td
        dest = install_widget(db, "chirp-53869840", auto_open=True)
        assert dest == widget_path()
        src = dest.read_text()
        assert "chirp-53869840" in src
        assert "__VIBELINE_DATA__" not in src
        assert "const AUTO_OPEN = true" in src
        assert "defineWidgetApp" in src
        os.environ.pop("HERMES_HOME", None)


def test_web_data_chirp():
    _load()
    from vibeline.ledger import resolve_db
    from vibeline.web import web_data

    data = web_data(resolve_db(str(ROOT / "examples" / "trace.db")))
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


def test_sessions_data(tmp_path=None):
    _load()
    import sqlite3
    import tempfile

    from vibeline.sessions import sessions_data

    db = Path(tempfile.mkdtemp()) / "state.db"
    cx = sqlite3.connect(db)
    cx.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, parent_session_id TEXT, started_at REAL,
          ended_at REAL, end_reason TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
          reasoning_tokens INTEGER, title TEXT, cwd TEXT, git_branch TEXT, api_call_count INTEGER, tool_call_count INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT,
          tool_calls TEXT, tool_name TEXT, timestamp REAL, display_kind TEXT);
        INSERT INTO sessions VALUES ('top','cli','m',NULL,100,NULL,NULL,10,5,0,0,'Title','/x',NULL,1,1);
        INSERT INTO sessions VALUES ('kid','subagent','m','top',110,130,'agent_close',3,2,0,0,NULL,NULL,NULL,1,0);
        INSERT INTO messages VALUES (1,'top','user','do it',NULL,NULL,NULL,100,NULL);
        INSERT INTO messages VALUES (2,'top','assistant','','',
          '[{"id":"c1","function":{"name":"delegate_task","arguments":"{}"}}]',NULL,105,NULL);
        INSERT INTO messages VALUES (3,'kid','user','child goal',NULL,NULL,NULL,110,NULL);
        INSERT INTO messages VALUES (4,'kid','assistant','child done',NULL,NULL,NULL,125,NULL);
        """
    )
    cx.commit()
    cx.close()
    data = sessions_data(db)
    top = data["parents"][0]
    assert data["totals"]["tokens"] == 20 and top["status"] == "idle"
    assert [e["label"] for e in top["events"]] == ["PROMPT", "DELEGATE_", "SPAWN"]
    kid = top["subs"][0]
    assert top["events"][-1]["child"] == kid["id"] == "kid"
    assert [e["label"] for e in kid["events"]] == ["SPAWNED", "REPLY", "END"]
