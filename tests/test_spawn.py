from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
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


def _db() -> Path:
    db = Path(tempfile.mkdtemp()) / "state.db"
    cx = sqlite3.connect(db)
    cx.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, parent_session_id TEXT, started_at REAL,
          ended_at REAL, end_reason TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
          reasoning_tokens INTEGER, title TEXT, cwd TEXT, git_branch TEXT, api_call_count INTEGER, tool_call_count INTEGER);
        INSERT INTO sessions VALUES ('live_abc','cli','m',NULL,100,NULL,NULL,10,5,0,0,'Live','/tmp',NULL,1,1);
        INSERT INTO sessions VALUES ('dead_xyz','cli','m',NULL,100,200,'cli_close',10,5,0,0,'Dead','/tmp',NULL,1,1);
        """
    )
    cx.commit()
    cx.close()
    return db


class _Patch:
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value
        self.prev = getattr(obj, name)

    def __enter__(self):
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.prev)


def _which(*names: str):
    return lambda n: f"/bin/{n}" if n in names else None


def test_plan_live_and_ended():
    _load()
    from vibeline import spawn

    db = _db()
    os.environ["VIBELINE_TERMINAL"] = "gnome-terminal"
    with _Patch(spawn.shutil, "which", _which("hermes", "gnome-terminal")):
        live = spawn.plan("live_abc", db)
        assert live["cmd"] == ["/bin/hermes", "--resume", "live_abc"]
        assert live["ended"] is False
        assert live["argv"][0] == "/bin/gnome-terminal"
        assert "--resume" in live["argv"] and "live_abc" in live["argv"]
        dead = spawn.plan("dead_xyz", db)
        assert dead["ended"] is True
        assert dead["cmd"][-1] == "dead_xyz"


def test_reject_bad_id_and_unknown():
    _load()
    from vibeline import spawn

    db = _db()
    with _Patch(spawn.shutil, "which", lambda n: f"/bin/{n}"):
        assert spawn.launch("rm -rf /", db, dry=True)["ok"] is False
        assert spawn.launch("nope", db, dry=True)["error"] == "unknown session nope"


def test_launch_popen():
    _load()
    from vibeline import spawn

    db = _db()
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        seen["cwd"] = kw.get("cwd")
        return object()

    os.environ["VIBELINE_TERMINAL"] = "gnome-terminal"
    os.environ["DISPLAY"] = ":0"
    with _Patch(spawn.shutil, "which", _which("hermes", "gnome-terminal")):
        out = spawn.launch("live_abc", db, popen=fake_popen)
    assert out["ok"] and not out["ended"]
    assert seen["argv"][-2:] == ["--resume", "live_abc"]
    assert seen["cwd"] == "/tmp"


def test_post_spawn():
    _load()
    from vibeline.web import make_server
    from vibeline import spawn
    import vibeline.sessions as sess

    db = _db()
    os.environ["VIBELINE_TERMINAL"] = "gnome-terminal"
    os.environ["DISPLAY"] = ":0"
    fake = lambda sid, d, **k: {
        "ok": True,
        "session": sid,
        "ended": False,
        "terminal": "gnome-terminal",
        "cmd": ["hermes", "--resume", sid],
    }
    httpd = make_server(None, port=0, host="127.0.0.1")
    host, port = httpd.server_address[:2]
    from threading import Thread

    t = Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        import urllib.request

        with _Patch(sess, "state_db", lambda: db), _Patch(spawn, "launch", fake):
            req = urllib.request.Request(
                f"http://{host}:{port}/api/spawn",
                data=json.dumps({"session": "live_abc"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as r:
                body = json.loads(r.read())
        assert body["ok"] and body["session"] == "live_abc"
    finally:
        httpd.shutdown()
        httpd.server_close()
