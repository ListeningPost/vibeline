"""Several machines on one page.

Every machine runs its own `hermes vibeline web` (a node). A node started with `--peer NAME=URL` also acts as a
hub: it fetches each peer's data server-side, namespaces the ids as `NAME~id`, and merges them into its own
page. Actions on a peer's session (open, resume, rewind) are forwarded to that peer, so the terminal opens on
the machine that owns the session.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

SEP = "~"
TIMEOUT_S = 4
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


@dataclass(frozen=True)
class Peer:
    name: str
    url: str  # scheme://host:port, no trailing slash
    token: str = ""


def parse(spec: str) -> Peer:
    """`NAME=http://host:8877` or `NAME=http://host:8877/?token=SECRET` (the URL a node prints at startup)."""
    name, eq, url = spec.partition("=")
    name, url = name.strip(), url.strip()
    u = urlparse(url)
    if not eq or not _NAME.match(name) or u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"bad --peer {spec!r}; expected NAME=http://host:port[/?token=SECRET]")
    return Peer(name, f"{u.scheme}://{u.netloc}", (parse_qs(u.query).get("token") or [""])[0])


def split(ident: str) -> tuple[str, str]:
    """`box~abc` -> ('box', 'abc'); a local id -> ('', id)."""
    name, sep, rest = ident.partition(SEP)
    return (name, rest) if sep else ("", ident)


def call(peer: Peer, path: str, query: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> Any:
    url = f"{peer.url}{path}" + (f"?{urlencode(query)}" if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if peer.token:
        req.add_header("Authorization", f"Bearer {peer.token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            raise OSError(f"{peer.name}: HTTP {e.code}") from None


def _ns(peer: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefix every id a peer sent so two machines can never collide."""
    tag = lambda v: f"{peer}{SEP}{v}"
    for p in data.get("parents", []):
        p["id"], p["machine"] = tag(p["id"]), peer
        for lane in [p, *p.get("subs", [])]:
            if lane is not p:
                lane["id"] = tag(lane["id"])
            for e in lane["events"]:
                e["id"] = tag(e["id"])
                if e.get("child"):
                    e["child"] = tag(e["child"])
    return data.get("parents", [])


class Hub:
    def __init__(self, peers: list[Peer], local_name: str):
        names = [p.name for p in peers]
        if len(set(names)) != len(names) or local_name in names:
            raise ValueError("peer names must be unique and differ from this machine's --name")
        self.peers = {p.name: p for p in peers}
        self.local_name = local_name
        self._versions: dict[str, tuple[float, str]] = {}

    def _each(self, fn: Any) -> dict[str, Any]:
        """Run fn(peer) for every peer at once; an unreachable peer yields its exception."""
        if not self.peers:
            return {}

        def safe(p: Peer) -> Any:
            try:
                return fn(p)
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=len(self.peers)) as pool:
            return dict(zip(self.peers, pool.map(safe, self.peers.values())))

    def version(self, src: str, local: str) -> str:
        def one(p: Peer) -> str:
            at, v = self._versions.get(f"{src}:{p.name}", (0.0, ""))
            if time.time() - at > 1.5:  # several browser tabs share one upstream poll
                v = str(call(p, "/api/version", {"src": src, "local": "1"}).get("version"))
                self._versions[f"{src}:{p.name}"] = (time.time(), v)
            return v

        got = self._each(one)
        return local + "".join(f"|{n}={'down' if isinstance(v, Exception) else v}" for n, v in got.items())

    def one(self, src: str, ident: str, full: bool = False) -> dict[str, Any]:
        """A single session that lives on a peer."""
        name, raw = split(ident)
        peer = self.peers.get(name)
        if not peer:
            raise ValueError(f"unknown machine {name}")
        data = call(peer, "/api/data", {"src": src, "local": "1", "session": raw, **({"full": "1"} if full else {})})
        if data.get("error"):
            raise ValueError(f"{name}: {data['error']}")
        return data

    def merge(self, src: str, local: dict[str, Any], limit: int) -> dict[str, Any]:
        """Local data plus every reachable peer's, newest activity first."""
        for p in local["parents"]:
            p["machine"] = self.local_name
        machines = [{"name": self.local_name, "ok": True, "local": True, "sessions": len(local["parents"])}]
        got = self._each(lambda p: call(p, "/api/data", {"src": src, "local": "1"}))
        for name, data in got.items():
            err = str(data) if isinstance(data, Exception) else data.get("error")
            if err:
                machines.append({"name": name, "ok": False, "error": err, "sessions": 0})
                continue
            parents = _ns(name, data)
            machines.append({"name": name, "ok": True, "sessions": len(parents)})
            local["parents"] += parents
            for k, v in (data.get("totals") or {}).items():
                if isinstance(v, (int, float)):
                    local["totals"][k] = local["totals"].get(k, 0) + v
        latest = lambda p: max((e["t"] for l in [p, *p.get("subs", [])] for e in l["events"]), default="")
        local["parents"].sort(key=latest, reverse=True)
        local["parents"] = local["parents"][: max(limit, 1) * len(machines)]
        local["machines"] = machines
        return local

    def forward(self, ident: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        name, raw = split(ident)
        peer = self.peers.get(name)
        if not peer:
            return {"ok": False, "error": f"unknown machine {name}"}
        try:
            out = call(peer, path, body={**body, "session": raw})
        except OSError as e:
            return {"ok": False, "error": f"{name} unreachable: {e}"}
        return {**out, "machine": name} if isinstance(out, dict) else {"ok": False, "error": f"{name}: bad reply"}
