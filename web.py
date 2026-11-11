"""Local web UI: the rails page, served live from the ledger."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .ledger import EXAMPLE_DB, connect, load_all, verify_chain
from .rails import _epoch, group
from . import feedback, peers, rewind, sessions, spawn, swarm

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


def _machine_name() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname() or "local")[:40] or "local"


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def make_server(
    db: Path | None,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    *,
    token: str = "",
    peer_list: list[peers.Peer] | None = None,
    name: str | None = None,
    read_only: bool = False,
) -> ThreadingHTTPServer:
    name = name or _machine_name()
    hub = peers.Hub(peer_list or [], name)
    store = feedback.store_path()

    def local_data(src: str, only: str | None, full: bool) -> dict[str, Any]:
        if src == "ledger":
            if db is None:
                raise FileNotFoundError("no harness ledger found; pass --db")
            return web_data(db)
        if src == "swarm":
            return swarm.swarm_data(only=only)
        return sessions.sessions_data(sessions.state_db(), only=only, full=full)

    def local_version(src: str) -> str:
        if src == "ledger":
            if db is None:
                raise FileNotFoundError("no harness ledger found; pass --db")
            return version(db)
        return swarm.version() if src == "swarm" else sessions.version(sessions.state_db())

    def data(src: str, only: str | None, local: bool, full: bool) -> dict[str, Any]:
        if only and peers.SEP in only and not local:
            out = hub.one(src, only, full)
            peers._ns(peers.split(only)[0], out)
        else:
            out = local_data(src, only, full)
            for p in out["parents"]:
                p["machine"] = name
            if hub.peers and not local and not only and src != "ledger":
                out = hub.merge(src, out, sessions.MAX_SESSIONS)
        out["machine"] = name
        out["has_ledger"] = db is not None and Path(db).resolve() != EXAMPLE_DB  # the bundled sample doesn't earn a tab
        out["can"] = {"open": src == "sessions" and not read_only, "rewind": src == "sessions" and not read_only, "feedback": src == "sessions"}
        return out

    def export(rated_only: bool) -> list[dict[str, Any]]:
        def fetch(machine: str, session: str) -> dict[str, Any] | None:
            if machine:
                return hub.one("sessions", f"{machine}{peers.SEP}{session}", full=True)
            return sessions.sessions_data(sessions.state_db(), only=session, full=True)

        return feedback.export_rows(store, fetch, local_name=name, rated_only=rated_only)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, status: int = 200) -> None:
            self._send(json.dumps(obj).encode(), "application/json", status)

        def _query(self) -> dict[str, str]:
            return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

        def _local(self) -> bool:
            """A loopback peer that also names this server by a loopback Host. The address alone is not enough:
            a page whose domain re-resolves to 127.0.0.1 (DNS rebinding) connects from loopback too, but the
            browser still sends that domain as Host."""
            if not _is_loopback(self.client_address[0]):
                return False
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip().lower()
            names = ("127.0.0.1", "localhost", "[::1]")
            return host in {f"{n}:{port}" for n in names} or (port == 80 and host in names)

        def _check_token(self) -> None:
            """Loopback callers addressing a loopback Host are trusted; anyone else must present the token."""
            if self._local():
                return
            if not token:
                raise Refused(403, "unrecognised Host: open the URL that `hermes vibeline web` printed")
            auth = self.headers.get("Authorization") or ""
            given = auth[7:] if auth.startswith("Bearer ") else self._query().get("token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                raise Refused(401, "token required: open the URL that `hermes vibeline web` printed")

        def _body(self) -> dict[str, Any]:
            # A JSON content type forces a CORS preflight, which this server never answers: other sites cannot post here.
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise Refused(415, "send application/json")
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != (self.headers.get("Host") or ""):
                raise Refused(403, "cross-origin request refused")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 262144:
                raise Refused(413, "body too large")
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(body, dict):
                raise Refused(400, "expected a JSON object")
            return body

        def do_POST(self) -> None:
            path = self.path.split("?")[0].split("#")[0]
            try:
                self._check_token()
                if path not in ("/api/spawn", "/api/rewind", "/api/feedback", "/api/feedback/tool"):
                    raise Refused(404, "not found")
                body = self._body()
                sid = str(body.get("session") or "").strip()
                machine, raw = peers.split(sid)
                if path == "/api/feedback/tool":
                    out = feedback.flag_tool(store, str(body.get("tool") or ""), bool(body.get("bad")), str(body.get("note") or ""))
                elif path == "/api/feedback":
                    rating = body.get("rating")
                    out = feedback.rate(
                        store, machine=machine, session=raw, event=peers.split(str(body.get("event") or ""))[1],
                        lane=peers.split(str(body.get("lane") or ""))[1], rating=None if rating is None else int(rating),
                        note=str(body.get("note") or ""), snapshot=body.get("snapshot") if isinstance(body.get("snapshot"), dict) else None,
                    )
                elif read_only:
                    raise Refused(403, "this vibeline is read-only (--read-only)")
                elif machine:  # the session lives on another machine: act there
                    fwd = {k: body[k] for k in ("retry", "terminal") if k in body}
                    if body.get("event"):
                        fwd["event"] = peers.split(str(body["event"]))[1]
                    out = hub.forward(sid, path, fwd)
                elif path == "/api/rewind":
                    out = rewind.rewind(raw, str(body.get("event") or ""), sessions.state_db(), retry=body.get("retry", True) is not False,
                                        open_terminal=body.get("terminal", True) is not False)
                else:
                    out = spawn.launch(raw, sessions.state_db())
                self._json(out, 200 if out.get("ok") else 400)
            except Refused as e:
                self._json({"ok": False, "error": str(e)}, e.status)
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)

        def do_GET(self) -> None:
            path = self.path.split("?")[0].split("#")[0]
            try:
                self._check_token()
                q = self._query()
                src = q.get("src") if q.get("src") in ("ledger", "swarm") else "sessions"
                local = q.get("local") == "1"
                if path in ("/", "/index.html"):
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/data":
                    self._json(data(src, q.get("session") or None, local, q.get("full") == "1"))
                elif path == "/api/version":
                    v = local_version(src)
                    self._json({"version": hub.version(src, v) if hub.peers and not local and src != "ledger" else v})
                elif path == "/api/feedback":
                    self._json(feedback.summary(store, key=lambda m, raw: f"{m}{peers.SEP}{raw}" if m else raw))
                elif path in ("/api/feedback.csv", "/api/feedback.jsonl"):
                    rows = export(q.get("rated") == "1")
                    kind = path.rsplit(".", 1)[1]
                    text = feedback.to_csv(rows) if kind == "csv" else feedback.to_jsonl(rows)
                    self._send(text.encode(), "text/csv; charset=utf-8" if kind == "csv" else "application/x-ndjson",
                               headers={"Content-Disposition": f'attachment; filename="vibeline-feedback.{kind}"'})
                else:
                    self._send(b"not found", "text/plain", 404)
            except Refused as e:
                self._send(str(e).encode(), "text/plain", e.status)
            except Exception as e:
                self._json({"error": str(e)}, 500)

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def _secret(name: str) -> str:
    """A credential by env name. Under Hermes it comes through `agent.secret_scope.get_secret`, which honours
    the active profile; run standalone (`python3 . web`) there is no Hermes, so it is the process environment."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return os.environ.get(name, "").strip()
    return (get_secret(name) or "").strip()


def serve(
    db: Path | None,
    *,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    open_browser: bool = False,
    token: str = "",
    peer_specs: list[str] | None = None,
    name: str | None = None,
    read_only: bool = False,
) -> None:
    token = token or _secret("VIBELINE_TOKEN")
    specs = list(peer_specs or []) + _secret("VIBELINE_PEERS").replace(",", " ").split()  # peer URLs carry tokens
    if not _is_loopback(host) and not token:
        token = secrets.token_urlsafe(18)  # never listen on the network unauthenticated
    try:
        httpd = make_server(db, port, host, token=token, peer_list=[peers.parse(s) for s in specs], name=name, read_only=read_only)
    except ValueError as e:
        raise SystemExit(f"vibeline: {e}")
    except OSError as e:
        raise SystemExit(f"vibeline: cannot listen on {host}:{port} ({e.strerror}). Try --port.")
    url = f"http://{host}:{port}/"
    print(f"vibeline: {url}  sessions {sessions.state_db()}  ledger {db or 'none'}  (ctrl-c to stop)", flush=True)
    if not _is_loopback(host):
        shown = socket.gethostname() if host in ("0.0.0.0", "::") else host
        mode = "read-only" if read_only else "can open terminals and rewind sessions on this machine"
        print(f"vibeline: network access ({mode}). From another machine:\n"
              f"  browser   http://{shown}:{port}/?token={token}\n"
              f"  as a peer hermes vibeline web --peer {name or _machine_name()}=http://{shown}:{port}/?token={token}", flush=True)
    for s in specs:
        print(f"vibeline: peer {peers.parse(s).name} at {peers.parse(s).url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
