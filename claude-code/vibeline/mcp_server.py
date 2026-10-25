"""Minimal stdio MCP server (JSON-RPC 2.0, one message per line). Standard library only.

Claude Code starts this from the plugin's .mcp.json; tools come from tools.TOOLS.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import tools

PROTOCOL = "2025-06-18"
SERVER = {"name": "vibeline", "version": "0.2.0"}
_BY_NAME = {t["name"]: t for t in tools.TOOLS}


def _text(result: dict[str, Any]) -> str:
    """Markdown first so it renders, then the rest of the payload as compact JSON."""
    body = result.get("markdown") or result.get("report") or ""
    rest = {k: v for k, v in result.items() if k not in ("markdown", "report")}
    return (body + "\n\n" if body else "") + json.dumps(rest, ensure_ascii=False, default=str)


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification (initialized, cancelled, …)
        return None
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        res: Any = {"protocolVersion": asked, "capabilities": {"tools": {}}, "serverInfo": SERVER}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in tools.TOOLS]}
    elif method == "tools/call":
        p = msg.get("params") or {}
        tool = _BY_NAME.get(p.get("name"))
        if not tool:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {p.get('name')}"}}
        try:
            out = tool["handler"](p.get("arguments") or {})
            res = {"content": [{"type": "text", "text": _text(out)}], "isError": out.get("ok") is False}
        except Exception as e:
            res = {"content": [{"type": "text", "text": f"vibeline: {e}"}], "isError": True}
            traceback.print_exc(file=sys.stderr)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main() -> None:
    stdin = open(sys.stdin.fileno(), encoding="utf-8", errors="replace", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply: Any = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = [r for r in (handle(m) for m in msg) if r] if isinstance(msg, list) else handle(msg)
        if reply:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
