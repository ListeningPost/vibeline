from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SID = "11111111-2222-3333-4444-555555555555"


def _rec(t: str, ts: str, **kw) -> dict:
    return {"type": t, "timestamp": ts, "sessionId": SID, "cwd": str(ROOT), "gitBranch": "main", **kw}


def _write(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n{\"half-written", encoding="utf-8")


def _fixture(home: Path) -> None:
    proj = home / "projects" / "C--proj"
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 7, "output_tokens": 5}
    _write(
        proj / f"{SID}.jsonl",
        [
            {"type": "custom-title", "customTitle": "Fix the thing", "sessionId": SID},
            _rec("user", "2026-01-01T00:00:00.000Z", uuid="u1", message={"role": "user", "content": "do it"}),
            # one API message split over two records (thinking, then tool_use) sharing message.id
            _rec("assistant", "2026-01-01T00:00:01.000Z", uuid="a1",
                 message={"id": "msg1", "role": "assistant", "model": "claude-x", "usage": usage,
                          "content": [{"type": "thinking", "thinking": ""}]}),
            _rec("assistant", "2026-01-01T00:00:02.000Z", uuid="a2",
                 message={"id": "msg1", "role": "assistant", "model": "claude-x", "usage": usage,
                          "content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {"description": "kid"}}]}),
            _rec("user", "2026-01-01T00:00:09.000Z", uuid="u2",
                 message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "kid done"}]}),
            _rec("user", "2026-01-01T00:00:09.500Z", uuid="u3", isMeta=True,
                 message={"role": "user", "content": "<system-reminder>hidden</system-reminder>"}),
            _rec("assistant", "2026-01-01T00:00:10.000Z", uuid="a3",
                 message={"id": "msg2", "role": "assistant", "model": "claude-x", "usage": usage,
                          "content": [{"type": "text", "text": "All done."}]}),
            _rec("user", "2026-01-01T00:00:11.000Z", uuid="u4",
                 message={"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}),
        ],
    )
    sub = proj / SID / "subagents"
    _write(
        sub / "agent-abc123.jsonl",
        [
            _rec("user", "2026-01-01T00:00:03.000Z", uuid="k1", isSidechain=True, agentId="abc123",
                 message={"role": "user", "content": "child goal"}),
            _rec("assistant", "2026-01-01T00:00:08.000Z", uuid="k2", isSidechain=True, agentId="abc123",
                 message={"id": "kmsg", "role": "assistant", "model": "claude-y", "usage": usage,
                          "content": [{"type": "text", "text": "child report"}]}),
        ],
    )
    (sub / "agent-abc123.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "Find files", "toolUseId": "toolu_1", "finished": True})
    )


def _with_home(fn):
    def run():
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = td
            try:
                _fixture(Path(td))
                fn()
            finally:
                if old is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old

    run.__name__ = fn.__name__
    return run


@_with_home
def test_sessions_data():
    from vibeline.sessions import render_sessions_markdown, sessions_data

    data = sessions_data()
    top = data["parents"][0]
    assert top["id"] == SID and top["name"] == "Fix the thing"
    # usage counted once per message id: msg1 + msg2 on the parent, kmsg on the child
    assert top["lane_tokens"] == 30 and top["tokens"] == 45
    assert [e["label"] for e in top["events"]] == ["PROMPT", "AGENT", "SPAWN", "REPLY", "STOP"]
    assert top["events"][1]["kind"] == "spawn" and "kid done" in top["events"][1]["context"]
    assert top["events"][2]["child"] == "abc123"
    kid = top["subs"][0]
    assert kid["name"] == "Find files" and kid["status"] == "complete"
    assert [e["label"] for e in kid["events"]] == ["SPAWNED", "REPLY"]
    assert "Fix the thing" in render_sessions_markdown(data)


@_with_home
def test_find_and_spawn_plan():
    from vibeline import sessions, spawn

    assert sessions.get(SID)["title"] == "Fix the thing"
    assert sessions.get("abc123")["id"] == SID  # subagent id resumes its parent
    assert sessions.get(SID[:8])["id"] == SID  # unambiguous prefix
    assert sessions.get("../x") is None
    assert spawn.launch("bad id!", dry=True) == {"ok": False, "error": "bad session id"}

    real_which = spawn.shutil.which
    spawn.shutil.which = lambda n: {"claude": "/bin/claude", "wt": "/bin/wt", "gnome-terminal": "/bin/gnome-terminal",
                                    "osascript": "/usr/bin/osascript"}.get(n)
    os.environ["DISPLAY"] = os.environ.get("DISPLAY", ":0")
    try:
        out = spawn.launch(SID, dry=True)
    finally:
        spawn.shutil.which = real_which
    assert out["ok"] and out["cmd"] == ["/bin/claude", "--resume", SID]
    assert out["cwd"] == str(ROOT)


@_with_home
def test_mcp_roundtrip():
    from vibeline.mcp_server import handle

    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "vibeline"
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert {"vibeline_rails", "vibeline_sessions", "vibeline_web"} <= set(names)
    res = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "vibeline_sessions", "arguments": {}}})
    assert not res["result"]["isError"] and "Fix the thing" in res["result"]["content"][0]["text"]
    bad = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}})
    assert bad["error"]["code"] == -32602
