"""Local web UI: the rails page, served live from the ledger."""
from __future__ import annotations

import json
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .ledger import connect, load_all, verify_chain
from .rails import _epoch, group
from . import sessions
from . import spawn

PAGE = Path(__file__).resolve().parent / "web" / "index.html"
DEFAULT_PORT = 8877


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.split("\n") if ln.strip()), "")


def _title(e: dict[str, Any]) -> str:
    k, ac = e["k"], e.get("ac_id") or "AC"
    if k == "open":
        return "Task opened"
    if k == "spawn":
        return f"Spawned {e['child']}" if e.get("child") else "Spawned child"
    if k in ("pass", "fail"):
        return f"{ac} {'passed' if k == 'pass' else 'failed'}"
    if k == "red":
        return f"{ac} expected red"
    if k == "done":
        return "Closed" + (f" · {e['result']}" if e.get("result") else "")
    return _first_line(e.get("why") or "")[:72] or e["label"]


def _event(e: dict[str, Any]) -> dict[str, Any]:
    why = (e.get("why") or "").strip()
    tin, tout = e.get("tokens_in") or 0, e.get("tokens_out") or 0
    out = {
        "id": str(e["id"]),
        "t": e["ts"],
        "kind": e["k"],
        "label": e["label"],
        "title": _title(e),
        "brief": _first_line(why)[:200] or e["label"],
        "context": why,
        "actor": e.get("actor"),
        "parent": e.get("parent"),
        "raw_kind": e.get("kind"),
        "ac_id": e.get("ac_id"),
        "result": e.get("result"),
        "tokens_in": tin,
        "tokens_out": tout,
        "tokens": tin + tout,
        "sha": e.get("sha"),
        "hash": e.get("hash"),
        "prev_hash": e.get("prev_hash"),
        "n": 1,
    }
    if e.get("child"):
        out["child"] = e["child"]
    return out


def _merge_bursts(events: list[dict[str, Any]], window_s: float = 2.0) -> list[dict[str, Any]]:
    """Checks fired together share one marker; nothing is dropped from the detail."""
    out: list[dict[str, Any]] = []
    for e in events:
        last = out[-1] if out else None
        if last and e["kind"] == "pass" == last["kind"] and _epoch(e["t"]) - _epoch(last["t"]) <= window_s:
            last["title"] += ", " + e["title"]
            last["brief"] += "; " + e["brief"]
            last["context"] += f"\n\n── {e['ac_id']} ──\n{e['context']}"
            for k in ("tokens_in", "tokens_out", "tokens", "n"):
                last[k] += e[k]
            last["ids"].append(e["id"])
            continue
        if e["kind"] == "pass":
            e = {**e, "context": f"── {e['ac_id']} ──\n{e['context']}"}
        out.append({**e, "ids": [e["id"]]})
    return out


def _acceptance(rows: list[dict[str, Any]], shown: dict[str, str], ac_dir: Path) -> list[dict[str, Any]]:
    acs: dict[str, dict[str, Any]] = {}
    for e in rows:
        ac_id = e.get("ac_id")
        if not ac_id:
            continue
        a = acs.setdefault(ac_id, {"id": ac_id, "met": False, "text": "not checked", "checks": []})
        a["checks"].append(
            {"id": shown.get(str(e["id"]), str(e["id"])), "t": e["ts"], "actor": e.get("actor"), "kind": e["k"], "label": e["label"]}
        )
        if e["k"] in ("pass", "fail"):
            a["met"] = e["k"] == "pass"
            a["text"] = _first_line(e.get("why") or "")[:120] or e["label"].lower()
    for a in acs.values():
        script = ac_dir / f"{a['id']}.check"
        if script.is_file():
            a["script"] = script.read_text(errors="replace")[:4000]
            a["script_path"] = str(script)
    return list(acs.values())


def web_data(db: Path) -> dict[str, Any]:
    rows = load_all(db)
    by_task: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
    parents = []
    for task_id, task_rows in by_task.items():
        g = group(task_rows)
        if not g["parent"]:
            continue
        closed = any(e["k"] == "done" for e in g["parent"])
        blocked = any(c["blocked"] for c in g["children"])
        spawn_why = {e["child"]: e for e in g["parent"] if e.get("child")}
        subs = []
        for c in g["children"]:
            events = [_event(e) for e in c["events"]]
            first, sp = events[0], spawn_why.get(c["id"])
            if first["label"] == "SPAWNED":
                first["id"] = f"spawned-{task_id}-{c['id']}"
                first["title"] = f"{c['id']} spawned by cli:parent"
                if sp:
                    first["brief"] = first["context"] = (sp.get("why") or "").strip()
            subs.append(
                {
                    "id": c["id"],
                    "name": c["id"],
                    "status": "blocked" if c["blocked"] else "complete" if closed else "in_progress",
                    "tokens": sum(e["tokens"] for e in events),
                    "events": events,
                }
            )
        events = _merge_bursts([_event(e) for e in g["parent"]])
        shown = {i: e["id"] for e in events for i in e["ids"]}
        classified = sorted(g["parent"] + [e for c in g["children"] for e in c["events"]], key=lambda e: e["ts"])
        tin = sum(r.get("tokens_in") or 0 for r in task_rows)
        tout = sum(r.get("tokens_out") or 0 for r in task_rows)
        parents.append(
            {
                "id": task_id,
                "name": task_id,
                "agent": "cli:parent",
                "status": "complete" if closed else "blocked" if blocked else "in_progress",
                "tokens_in": tin,
                "tokens_out": tout,
                "tokens": tin + tout,
                "goal": (g["parent"][0].get("why") or "").strip(),
                "ac": _acceptance(classified, shown, db.parent.parent / "ac"),
                "events": events,
                "subs": subs,
            }
        )
    acs = [a for p in parents for a in p["ac"]]
    tin, tout = sum(p["tokens_in"] for p in parents), sum(p["tokens_out"] for p in parents)
    return {
        "src": "ledger",
        "version": version(db),
        "db": str(db),
        "chain": verify_chain(rows),
        "totals": {
            "tokens_in": tin,
            "tokens_out": tout,
            "tokens": tin + tout,
            "tasks": len(parents),
            "events": len(rows),
            "running": sum(1 for p in parents if p.get("status") == "running"),
            "ac": len(acs),
            "ac_met": sum(1 for a in acs if a["met"]),
        },
        "parents": parents,
    }


def version(db: Path) -> str:
    with connect(db) as cx:
        n, last = cx.execute("SELECT COUNT(*), MAX(id) FROM events").fetchone()
    return f"{n}:{last}"


def make_server(db: Path | None, port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _source(self) -> tuple[Any, Any, Path]:
            q = parse_qs(urlparse(self.path).query)
            if q.get("src") == ["ledger"]:
                if db is None:
                    raise FileNotFoundError("no harness ledger found; pass --db")
                return web_data, version, db
            only = (q.get("session") or [None])[0]
            return partial(sessions.sessions_data, only=only), sessions.version, sessions.state_db()

        def do_POST(self) -> None:
            path = self.path.split("?")[0].split("#")[0]
            try:
                if path != "/api/spawn":
                    self._send(b"not found", "text/plain", 404)
                    return
                n = int(self.headers.get("Content-Length") or 0)
                if n > 4096:
                    self._send(json.dumps({"ok": False, "error": "body too large"}).encode(), "application/json", 413)
                    return
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                sid = str((body or {}).get("session") or "").strip()
                out = spawn.launch(sid, sessions.state_db())
                self._send(json.dumps(out).encode(), "application/json", 200 if out.get("ok") else 400)
            except Exception as e:
                self._send(json.dumps({"ok": False, "error": str(e)}).encode(), "application/json", 500)

        def do_GET(self) -> None:
            path = self.path.split("?")[0].split("#")[0]
            try:
                if path in ("/", "/index.html"):
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/data":
                    data, _, src = self._source()
                    self._send(json.dumps(data(src)).encode(), "application/json")
                elif path == "/api/version":
                    _, ver, src = self._source()
                    self._send(json.dumps({"version": ver(src)}).encode(), "application/json")
                else:
                    self._send(b"not found", "text/plain", 404)
            except Exception as e:
                self._send(json.dumps({"error": str(e)}).encode(), "application/json", 500)

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve(db: Path | None, *, port: int = DEFAULT_PORT, host: str = "127.0.0.1", open_browser: bool = False) -> None:
    try:
        httpd = make_server(db, port, host)
    except OSError as e:
        raise SystemExit(f"vibeline: cannot listen on {host}:{port} ({e.strerror}). Try --port.")
    url = f"http://{host}:{port}/"
    print(f"vibeline: {url}  sessions {sessions.state_db()}  ledger {db or 'none'}  (ctrl-c to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
