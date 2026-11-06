"""Stopwatch timing, rewind, feedback export, swarm rails, and the multi-machine hub."""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

from test_spawn import _Patch, _load


def _db() -> Path:
    db = Path(tempfile.mkdtemp()) / "state.db"
    cx = sqlite3.connect(db)
    cx.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, parent_session_id TEXT, started_at REAL,
          ended_at REAL, end_reason TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
          reasoning_tokens INTEGER, title TEXT, cwd TEXT, git_branch TEXT, api_call_count INTEGER, tool_call_count INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT,
          tool_calls TEXT, tool_name TEXT, timestamp REAL, display_kind TEXT, active INTEGER NOT NULL DEFAULT 1);
        INSERT INTO sessions VALUES ('s1','cli','m',NULL,100,NULL,NULL,10,5,0,0,'One','/tmp',NULL,1,1);
        INSERT INTO sessions VALUES ('kid','subagent','m','s1',104,NULL,NULL,1,1,0,0,NULL,'/tmp',NULL,1,1);
        INSERT INTO messages VALUES (1,'s1','user','fix the bug',NULL,NULL,NULL,100,NULL,1);
        INSERT INTO messages VALUES (2,'s1','assistant','looking',NULL,'[{"id":"c1","function":{"name":"terminal","arguments":"ls"}},{"id":"c2","function":{"name":"read_file","arguments":"a"}}]',NULL,103,NULL,1);
        INSERT INTO messages VALUES (3,'s1','tool','ok','c2',NULL,'read_file',103.5,NULL,1);
        INSERT INTO messages VALUES (4,'s1','tool','files','c1',NULL,'terminal',109,NULL,1);
        INSERT INTO messages VALUES (5,'s1','assistant','done',NULL,NULL,NULL,111,NULL,1);
        INSERT INTO messages VALUES (6,'s1','user','now add tests',NULL,NULL,NULL,200,NULL,1);
        INSERT INTO messages VALUES (7,'s1','assistant','old attempt',NULL,NULL,NULL,205,NULL,0);
        INSERT INTO messages VALUES (8,'kid','user','sub goal',NULL,NULL,NULL,104,NULL,1);
        """
    )
    cx.commit()
    cx.close()
    return db


def _events(db: Path, **kw):
    from vibeline import sessions

    p = sessions.sessions_data(db, **kw)["parents"][0]
    return p, {e["id"]: e for e in p["events"]}


def test_timing_ghosts_and_rewind_targets():
    _load()
    p, ev = _events(_db())
    tool = ev["m2"]
    assert tool["dur_ms"] == 6000  # assistant message -> last tool result
    assert tool["calls"] == [{"name": "terminal", "ms": 6000}, {"name": "read_file", "ms": 500}]
    assert tool["wait_ms"] == 3000 and tool["tools"] == ["terminal", "read_file"]
    assert ev["m5"]["wait_ms"] == 2000 and ev["m5"].get("dur_ms") is None
    assert "wait_ms" not in ev["m1"]  # a prompt's gap is the human, not the model
    assert ev["m7"]["ghost"] and not ev["m6"]["ghost"]
    assert ev["m1"]["can_rewind"] and ev["m6"]["can_rewind"]
    assert not any(e.get("can_rewind") for s in p["subs"] for e in s["events"])


def test_running_call_has_no_duration_and_old_schema_loads():
    _load()
    db = _db()
    cx = sqlite3.connect(db)
    cx.execute("DELETE FROM messages WHERE id=4")
    cx.commit()
    assert _events(db)[1]["m2"]["dur_ms"] is None
    cx.execute("ALTER TABLE messages DROP COLUMN active")  # Hermes before rewind existed
    cx.commit()
    cx.close()
    assert _events(db)[1]["m7"]["ghost"] is False


def test_full_keeps_long_text():
    _load()
    db = _db()
    cx = sqlite3.connect(db)
    cx.execute("UPDATE messages SET content=? WHERE id=5", ("x" * 9000,))
    cx.commit()
    cx.close()
    assert len(_events(db)[1]["m5"]["context"]) < 9000
    assert len(_events(db, only="s1", full=True)[1]["m5"]["context"]) == 9000


class _FakeSessionDB:
    calls: list = []

    def __init__(self, db):
        self.db = db

    def rewind_to_message(self, sid, mid):
        _FakeSessionDB.calls.append((sid, mid))
        return {"rewound_count": 2, "target_message": {"content": "now add tests"}}


def test_rewind_validates_then_resubmits_prompt():
    _load()
    from vibeline import rewind, spawn

    db = _db()
    assert rewind.rewind("s1", "m6", db, dry=True) == {"ok": True, "dry": True, "session": "s1", "message": 6, "drops": 1, "prompt": "now add tests"}
    assert rewind.rewind("s1", "m1", db, dry=True)["drops"] == 6  # ghosts are already gone
    for sid, event, why in [("s1", "m5", "prompt"), ("s1", "m7", "live transcript"), ("kid", "m8", "parent"),
                            ("s1", "m8", "live transcript"), ("nope", "m1", "unknown"), ("s1", "end-s1", "message event")]:
        out = rewind.rewind(sid, event, db, session_db=_FakeSessionDB)
        assert out["ok"] is False and why in out["error"], out
    assert _FakeSessionDB.calls == []
    seen = {}
    os.environ.update(VIBELINE_TERMINAL="gnome-terminal", DISPLAY=":0")
    with _Patch(spawn.shutil, "which", lambda n: f"/bin/{n}"):
        out = rewind.rewind("s1", "m6", db, session_db=_FakeSessionDB, popen=lambda argv, **kw: seen.update(argv=argv))
        assert out["ok"] and out["rewound"] == 2 and out["retried"]
        assert seen["argv"][-5:] == ["chat", "--resume", "s1", "-q", "now add tests"]
        rewind.rewind("s1", "m6", db, retry=False, session_db=_FakeSessionDB, popen=lambda argv, **kw: seen.update(argv=argv))
        assert seen["argv"][-2:] == ["--resume", "s1"]
    assert _FakeSessionDB.calls == [("s1", 6), ("s1", 6)]


def test_headless_machine_uses_tmux():
    _load()
    from vibeline import spawn

    db = _db()
    saved = {k: os.environ.pop(k, None) for k in ("DISPLAY", "WAYLAND_DISPLAY", "VIBELINE_TERMINAL")}
    seen, has = {}, {"code": 1}

    class _Done:
        def __init__(self, code):
            self.returncode = code

    run = lambda argv, **kw: _Done(has["code"])
    popen = lambda argv, **kw: seen.update(argv=argv)
    try:
        with _Patch(spawn.shutil, "which", lambda n: f"/bin/{n}"):
            out = spawn.launch("s1", db, popen=popen, run=run)
            assert out["ok"] and out["terminal"] == "tmux" and out["attach"] == "tmux attach -t vibeline-s1"
            assert seen["argv"][:5] == ["/bin/tmux", "new-session", "-d", "-s", "vibeline-s1"]
            assert seen["argv"][-3:] == ["/bin/hermes", "--resume", "s1"]
            seen.clear()
            has["code"] = 0  # the tmux session is still there: point at it, never start a second one
            again = spawn.launch("s1", db, popen=popen, run=run)
            assert again["ok"] and again["existing"] and not seen
            retry = spawn.launch("s1", db, popen=popen, run=run, query="again")
            assert retry["ok"] is False and "tmux attach -t vibeline-s1" in retry["error"]
        with _Patch(spawn.shutil, "which", lambda n: None if n == "tmux" else f"/bin/{n}"):
            assert "tmux is not installed" in spawn.launch("s1", db, popen=popen, run=run)["error"]
        assert spawn.tmux_name("20261018_22.28:f2") == "vibeline-20261018_22_28_f2"
    finally:
        os.environ.update({k: v for k, v in saved.items() if v is not None})


def test_rewind_without_hermes_says_so():
    _load()
    from vibeline import rewind

    out = rewind.rewind("s1", "m6", _db())
    assert out["ok"] is False and "hermes vibeline web" in out["error"]


def test_feedback_roundtrip_and_export():
    _load()
    from vibeline import feedback, sessions

    db, store = _db(), Path(tempfile.mkdtemp()) / "fb" / "feedback.db"
    assert feedback.summary(store)["ratings"] == {}
    feedback.rate(store, machine="", session="s1", lane="s1", event="m2", rating=-1, note='slow, and "ls" was wrong')
    feedback.rate(store, machine="", session="s1", lane="s1", event="m5", rating=1)
    feedback.rate(store, machine="box2", session="gone", lane="gone", event="m9", rating=0, note="meh",
                  snapshot={"t": "T", "label": "TERMINAL", "tools": ["terminal"], "context": "ctx", "junk": 1})
    feedback.flag_tool(store, "terminal", True, "times out")
    s = feedback.summary(store, key=lambda m, raw: f"{m}~{raw}" if m else raw)
    assert s["ratings"]["m2"] == {"rating": -1, "note": 'slow, and "ls" was wrong'} and "box2~m9" in s["ratings"]
    assert s["tools"] == {"terminal": "times out"}

    fetch = lambda machine, session: None if machine else sessions.sessions_data(db, only=session, full=True)
    rows = feedback.export_rows(store, fetch, local_name="hub")
    by = {(r["machine"], r["event_id"]): r for r in rows}
    assert by[("hub", "m2")]["rating"] == "down" and by[("hub", "m2")]["duration_ms"] == 6000
    assert by[("hub", "m2")]["tool_flagged"] == "terminal" and by[("hub", "m2")]["tool_note"] == "terminal: times out"
    assert by[("hub", "m5")]["rating"] == "up" and by[("hub", "m1")]["rating"] == ""  # unrated context rides along
    assert by[("hub", "spawned-kid")]["lane_id"] == "kid" and by[("hub", "m7")]["rewound"] == 1
    gone = by[("box2", "m9")]  # unreachable machine: the snapshot taken at rating time
    assert gone["rating"] == "neutral" and gone["content"] == "ctx" and gone["tool_flagged"] == "terminal"
    assert {r["event_id"] for r in feedback.export_rows(store, fetch, rated_only=True)} == {"m2", "m5", "m9"}

    parsed = list(csv.DictReader(io.StringIO(feedback.to_csv(rows))))
    assert list(parsed[0]) == feedback.COLUMNS and len(parsed) == len(rows)
    assert next(r for r in parsed if r["event_id"] == "m2")["note"] == 'slow, and "ls" was wrong'
    assert [json.loads(line)["event_id"] for line in feedback.to_jsonl(rows).splitlines()] == [r["event_id"] for r in rows]

    feedback.rate(store, machine="", session="s1", event="m5", rating=None)  # cleared
    feedback.flag_tool(store, "terminal", False)
    s = feedback.summary(store)
    assert "m5" not in s["ratings"] and s["tools"] == {}
    for bad in ({"rating": 5}, {"rating": 1, "event": ""}):
        try:
            feedback.rate(store, **{"machine": "", "session": "s1", "event": "m1", "rating": 1, **bad})
            raise AssertionError("accepted")
        except ValueError:
            pass


def _kanban() -> Path:
    db = Path(tempfile.mkdtemp()) / "kanban.db"
    cx = sqlite3.connect(db)
    cx.executescript(
        """
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT, status TEXT, created_at INTEGER,
          completed_at INTEGER, workspace_path TEXT, result TEXT, model_override TEXT, last_failure_error TEXT);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
        CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT, created_at INTEGER);
        CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT, status TEXT, started_at INTEGER,
          ended_at INTEGER, outcome TEXT, summary TEXT, metadata TEXT, error TEXT);
        INSERT INTO tasks VALUES ('root','Swarm: audit','Goal','orch','done',100,100,NULL,NULL,NULL,NULL);
        INSERT INTO tasks VALUES ('w1','Map code','b','researcher','done',100,160,NULL,'mapped',NULL,NULL);
        INSERT INTO tasks VALUES ('w2','Repro bug','b','tester','running',100,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO tasks VALUES ('ver','Verify','b','reviewer','todo',100,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO tasks VALUES ('syn','Synthesize','b','writer','todo',100,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO tasks VALUES ('other','Plain card','b','x','ready',100,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO task_events VALUES (1,'root',NULL,'created',NULL,100);
        INSERT INTO task_events VALUES (2,'root',NULL,'blocked',NULL,100);
        INSERT INTO task_events VALUES (3,'w1',NULL,'claimed','{"pid":1}',110);
        INSERT INTO task_events VALUES (4,'w2',NULL,'timed_out',NULL,150);
        INSERT INTO task_comments VALUES (1,'root','orch','[swarm:blackboard] {"key":"topology","value":{"root_id":"root","worker_ids":["w1","w2"],"verifier_id":"ver","synthesizer_id":"syn","goal":"Audit login"}}',100);
        INSERT INTO task_comments VALUES (2,'root','researcher','[swarm:blackboard] {"key":"auth_files","value":["login.py"]}',140);
        INSERT INTO task_comments VALUES (3,'w1','researcher','two entry points',130);
        INSERT INTO task_runs VALUES (1,'w1','researcher','done',110,160,'completed','2 paths',NULL,NULL);
        INSERT INTO task_runs VALUES (2,'w2','tester','running',120,NULL,NULL,NULL,NULL,NULL);
        """
    )
    cx.commit()
    cx.close()
    return db


def test_swarm_rails():
    _load()
    from vibeline import swarm

    db = _kanban()
    d = swarm.swarm_data([db])
    assert d["src"] == "swarm" and d["totals"]["tasks"] == 1 and d["totals"]["running"] == 2  # the swarm and worker 2
    (p,) = d["parents"]
    assert p["id"] == "default:root" and p["goal"] == "Audit login" and p["status"] == "in_progress"
    labels = [e["label"] for e in p["events"]]
    assert labels[0] == "SWARM" and labels.count("SPAWN") == 4 and "BLOCKED" not in labels and "CREATED" not in labels
    assert any(e["title"] == "Blackboard · auth_files" for e in p["events"])
    subs = {s["id"]: s for s in p["subs"]}
    assert [s["role"] for s in p["subs"]] == ["worker 1", "worker 2", "verifier", "synthesizer"]
    assert {e["child"] for e in p["events"] if e.get("child")} == set(subs)
    w1 = subs["default:w1"]
    assert w1["status"] == "complete" and [e["label"] for e in w1["events"]] == ["SPAWNED", "CLAIMED", "COMMENT", "PASS"]
    assert w1["events"][-1]["dur_ms"] == 50000
    assert subs["default:w2"]["status"] == "in_progress" and subs["default:w2"]["events"][-1]["kind"] == "fail"
    assert subs["default:ver"]["status"] == "idle"
    assert swarm.swarm_data([db], only="default:root")["parents"][0]["id"] == "default:root"
    assert swarm.swarm_data([])["parents"] == [] and swarm.version([]) == "none"

    cx = sqlite3.connect(db)
    v0 = swarm.version([db])
    cx.execute("UPDATE tasks SET status='done', completed_at=300, result='final report' WHERE id='syn'")
    cx.execute("INSERT INTO task_events VALUES (9,'syn',NULL,'claimed',NULL,290)")
    cx.commit()
    cx.close()
    assert swarm.version([db]) != v0
    done = swarm.swarm_data([db])["parents"][0]
    assert done["events"][-1]["label"] == "DONE" and done["status"] == "complete"


@contextmanager
def _serve(**kw):
    from vibeline.web import make_server

    httpd = make_server(None, port=0, host="127.0.0.1", **kw)
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield "http://%s:%s" % httpd.server_address[:2]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _http(url: str, body=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            data, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        data, status = e.read(), e.code
    return status, (data.decode() if raw else json.loads(data))


@contextmanager
def _env(db: Path):
    import vibeline.sessions as sess

    os.environ["VIBELINE_FEEDBACK_DB"] = str(Path(tempfile.mkdtemp()) / "feedback.db")
    os.environ["HERMES_KANBAN_DB"] = str(_kanban())
    try:
        with _Patch(sess, "state_db", lambda: db):
            yield
    finally:
        del os.environ["VIBELINE_FEEDBACK_DB"], os.environ["HERMES_KANBAN_DB"]


def test_token_guards_network_clients_and_posts_need_json():
    _load()
    from vibeline import web

    with _env(_db()), _serve(token="sekrit") as url:
        assert _http(url + "/api/version")[0] == 200  # loopback is trusted, as before
        with _Patch(web, "_is_loopback", lambda host: False):
            assert _http(url + "/api/version", raw=True)[0] == 401
            assert _http(url + "/", raw=True)[0] == 401
            assert _http(url + "/api/version", headers={"Authorization": "Bearer nope"}, raw=True)[0] == 401
            assert _http(url + "/api/spawn", {"session": "s1"})[0] == 401
            assert _http(url + "/api/version?token=sekrit")[0] == 200
            assert _http(url + "/api/version", headers={"Authorization": "Bearer sekrit"})[0] == 200
        # what a hostile page can send without a preflight
        assert _http(url + "/api/spawn", {"session": "s1"}, headers={"Content-Type": "text/plain"})[0] == 415
        assert _http(url + "/api/feedback", {"session": "s1"}, headers={"Origin": "http://evil.example"})[0] == 403
        assert _http(url + "/api/nope", {})[0] == 404


def test_read_only_refuses_actions_but_keeps_feedback():
    _load()
    with _env(_db()), _serve(read_only=True) as url:
        can = _http(url + "/api/data")[1]["can"]
        assert can == {"open": False, "rewind": False, "feedback": True}
        assert _http(url + "/api/spawn", {"session": "s1"})[0] == 403
        assert _http(url + "/api/rewind", {"session": "s1", "event": "m6"})[0] == 403
        assert _http(url + "/api/feedback", {"session": "s1", "event": "m2", "rating": 1})[1]["ok"]


def test_sources_and_feedback_api():
    _load()
    with _env(_db()), _serve(name="hub") as url:
        d = _http(url + "/api/data")[1]
        assert d["machine"] == "hub" and d["parents"][0]["machine"] == "hub" and "machines" not in d
        assert _http(url + "/api/data?src=swarm")[1]["parents"][0]["id"] == "default:root"
        assert _http(url + "/api/version?src=swarm")[1]["version"]
        assert _http(url + "/api/rewind", {"session": "s1", "event": "m5"})[1]["error"] == "can only rewind to a prompt"
        assert _http(url + "/api/feedback", {"session": "s1", "lane": "s1", "event": "m2", "rating": -1, "note": "slow"})[1]["ok"]
        assert _http(url + "/api/feedback", {"session": "s1", "event": "m2", "rating": 7})[0] == 400
        assert _http(url + "/api/feedback/tool", {"tool": "terminal", "bad": True, "note": "hangs"})[1]["ok"]
        fb = _http(url + "/api/feedback")[1]
        assert fb["ratings"] == {"m2": {"rating": -1, "note": "slow"}} and fb["tools"] == {"terminal": "hangs"}
        status, text = _http(url + "/api/feedback.csv", raw=True)
        rows = list(csv.DictReader(io.StringIO(text)))
        assert status == 200 and len(rows) == 7 and rows[1]["rating"] == "down" and rows[1]["tool_note"] == "terminal: hangs"
        assert len(_http(url + "/api/feedback.jsonl?rated=1", raw=True)[1].splitlines()) == 1


def test_hub_merges_peers_and_forwards_actions():
    _load()
    from vibeline import peers, rewind, spawn

    assert peers.parse("box2=http://h:1/?token=t") == peers.Peer("box2", "http://h:1", "t")
    assert peers.split("box2~m1") == ("box2", "m1") and peers.split("m1") == ("", "m1")
    for bad in ("nourl", "bad name=http://h:1", "x=ftp://h", "=http://h:1"):
        try:
            peers.parse(bad)
            raise AssertionError(bad)
        except ValueError:
            pass

    seen = []
    launch = lambda sid, db, **kw: seen.append(("spawn", sid)) or {"ok": True, "session": sid, "terminal": "t"}
    rw = lambda sid, event, db, **kw: seen.append(("rewind", sid, event, kw["retry"])) or {"ok": True, "rewound": 1}
    with _env(_db()), _Patch(spawn, "launch", launch), _Patch(rewind, "rewind", rw), _serve(name="box2", token="sekrit") as node:
        peer_list = [peers.parse(f"box2={node}/?token=sekrit"), peers.Peer("dead", "http://127.0.0.1:9")]
        with _serve(name="hub", peer_list=peer_list) as hub:
            d = _http(hub + "/api/data")[1]
            assert [(m["name"], m["ok"]) for m in d["machines"]] == [("hub", True), ("box2", True), ("dead", False)]
            ids = {p["id"]: p for p in d["parents"]}
            assert set(ids) == {"s1", "box2~s1"} and ids["box2~s1"]["machine"] == "box2"
            far = ids["box2~s1"]
            assert far["events"][0]["id"] == "box2~m1" and far["subs"][0]["id"] == "box2~kid"
            assert {e["child"] for e in far["events"] if e.get("child")} == {"box2~kid"}
            assert d["totals"]["tasks"] == 2
            v = _http(hub + "/api/version")[1]["version"]
            assert "|box2=" in v and v.endswith("|dead=down")

            one = _http(hub + "/api/data?session=box2~s1")[1]  # focus a session that lives elsewhere
            assert [p["id"] for p in one["parents"]] == ["box2~s1"] and one["parents"][0]["events"][0]["id"] == "box2~m1"

            assert _http(hub + "/api/spawn", {"session": "box2~s1"})[1] == {"ok": True, "session": "s1", "terminal": "t", "machine": "box2"}
            assert _http(hub + "/api/rewind", {"session": "box2~s1", "event": "box2~m6", "retry": False})[1]["machine"] == "box2"
            assert _http(hub + "/api/spawn", {"session": "s1"})[1]["ok"]
            assert seen == [("spawn", "s1"), ("rewind", "s1", "m6", False), ("spawn", "s1")]
            assert "unreachable" in _http(hub + "/api/spawn", {"session": "dead~s1"})[1]["error"]
            assert _http(hub + "/api/spawn", {"session": "who~s1"})[1]["error"] == "unknown machine who"

            # feedback lives on the hub, tagged with the machine, and export pulls the peer's full session
            assert _http(hub + "/api/feedback", {"session": "box2~s1", "lane": "box2~s1", "event": "box2~m2", "rating": 1})[1]["ok"]
            assert list(_http(hub + "/api/feedback")[1]["ratings"]) == ["box2~m2"]
            rows = list(csv.DictReader(io.StringIO(_http(hub + "/api/feedback.csv", raw=True)[1])))
            assert {r["machine"] for r in rows} == {"box2"} and len(rows) == 7
            assert next(r for r in rows if r["event_id"] == "m2")["rating"] == "up"

    try:
        peers.Hub([peers.Peer("hub", "http://h:1")], "hub")
        raise AssertionError("duplicate name accepted")
    except ValueError:
        pass
