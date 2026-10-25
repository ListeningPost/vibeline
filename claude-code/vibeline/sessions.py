"""Read Claude Code sessions (~/.claude/projects/*/*.jsonl) as rails: one lane per session, subagents as children.

Read-only. Every Claude Code process on the machine writes transcripts under the same projects dir, so this sees
them all. Subagent transcripts live next to their parent at <session>/subagents/**/agent-<id>.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import claude_home

MAX_SESSIONS = 8
LIVE_WINDOW_S = 15 * 60  # a transcript untouched for this long is idle, not running
_TEXT_CAP = 4000
_SPAWN_TOOLS = ("Agent", "Task")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_CACHE: dict[Path, tuple[tuple[int, int], list[dict[str, Any]]]] = {}


def projects_dir() -> Path:
    return claude_home() / "projects"


def _ok_id(sid: str | None) -> bool:
    return bool(sid) and bool(_ID_RE.match(sid))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _first_line(text: str | None) -> str:
    return next((ln.strip() for ln in (text or "").split("\n") if ln.strip()), "")


def _clip(text: str | None, cap: int = _TEXT_CAP) -> str:
    text = text or ""
    return text if len(text) <= cap else text[:cap] + f"\n… {len(text) - cap:,} more characters"


def _read(path: Path) -> list[dict[str, Any]]:
    """Parsed JSONL records, cached on (mtime, size). Half-written trailing lines are skipped."""
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    _CACHE[path] = (key, out)
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            parts.append(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type', 'block')}]")
        return "\n".join(parts)
    return ""


def _event(eid: str, ts: str, kind: str, label: str, title: str, brief: str, context: str, actor: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": eid,
        "t": ts,
        "kind": kind,
        "label": label,
        "title": title,
        "brief": brief[:200] or label,
        "context": context,
        "actor": actor,
        "parent": None,
        "raw_kind": extra.pop("raw_kind", kind),
        "ac_id": None,
        "result": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens": 0,
        "sha": None,
        "hash": None,
        "prev_hash": None,
        "n": 1,
        **extra,
    }


def _is_tool_results(content: Any) -> bool:
    return isinstance(content, list) and bool(content) and all(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _command(text: str) -> str | None:
    """`/name args` for a slash-command prompt, else None."""
    m = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if not m:
        return None
    a = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    name = m.group(1).strip()
    return (name if name.startswith("/") else "/" + name) + (f" {a.group(1).strip()}" if a and a.group(1).strip() else "")


def _summary(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """Lane metadata: title, cwd, branch, model, token totals, call counts."""
    info: dict[str, Any] = {"title": None, "agent_name": None, "cwd": None, "branch": None, "model": None,
                            "version": None, "first_prompt": "", "tokens_in": 0, "tokens_out": 0,
                            "cache_read": 0, "api_calls": 0, "tool_calls": 0, "first_t": None, "last_t": None}
    usage: dict[str, dict[str, Any]] = {}
    for r in recs:
        t = r.get("type")
        if t == "custom-title" and r.get("customTitle"):
            info["title"] = r["customTitle"]
        elif t == "agent-name" and r.get("agentName"):
            info["agent_name"] = r["agentName"]
        ts = r.get("timestamp")
        if ts:
            info["first_t"] = info["first_t"] or ts
            info["last_t"] = ts
        if r.get("cwd"):
            info["cwd"] = r["cwd"]
        if r.get("gitBranch"):
            info["branch"] = r["gitBranch"]
        if r.get("version"):
            info["version"] = r["version"]
        m = r.get("message") if isinstance(r.get("message"), dict) else None
        if not m:
            continue
        if t == "assistant":
            if m.get("model") and m["model"] != "<synthetic>":
                info["model"] = m["model"]
            mid = m.get("id") or r.get("uuid")
            if isinstance(m.get("usage"), dict):
                usage[mid] = m["usage"]  # repeated on every block of one message; keep one
            if isinstance(m.get("content"), list):
                info["tool_calls"] += sum(1 for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use")
        elif t == "user" and not info["first_prompt"] and not r.get("isMeta") and not _is_tool_results(m.get("content")):
            info["first_prompt"] = _text_of(m.get("content"))
    for u in usage.values():
        info["tokens_in"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        info["tokens_out"] += u.get("output_tokens") or 0
        info["cache_read"] += u.get("cache_read_input_tokens") or 0
    info["api_calls"] = len(usage)
    return info


def _lane_events(recs: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    results: dict[str, tuple[str, bool]] = {}
    for r in recs:
        m = r.get("message") if isinstance(r.get("message"), dict) else {}
        if r.get("type") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    results[b["tool_use_id"]] = (_result_text(b.get("content")), bool(b.get("is_error")))

    # One assistant message is written as several records (one per content block) sharing message.id.
    groups: dict[str, dict[str, Any]] = {}
    order: list[tuple[str, Any]] = []
    for r in recs:
        t = r.get("type")
        m = r.get("message") if isinstance(r.get("message"), dict) else {}
        if t == "assistant":
            mid = m.get("id") or r.get("uuid")
            g = groups.get(mid)
            if g is None:
                g = groups[mid] = {"rec": r, "blocks": [], "error": False}
                order.append(("assistant", mid))
            g["blocks"] += [b for b in (m.get("content") or []) if isinstance(b, dict)]
            g["error"] |= bool(r.get("isApiErrorMessage"))
        elif t in ("user", "system"):
            order.append((t, r))

    events: list[dict[str, Any]] = []
    for t, item in order:
        if t == "system":
            r = item
            sub = r.get("subtype")
            if sub == "compact_boundary":
                events.append(_event(f"m{r.get('uuid')}", r["timestamp"], "note", "COMPACT", "Conversation compacted", "context compacted", r.get("content") or "", actor, raw_kind=sub, quiet=True))
            elif sub == "api_error":
                txt = r.get("content") or json.dumps(r.get("error") or {})[:400]
                events.append(_event(f"m{r.get('uuid')}", r["timestamp"], "fail", "FAIL", "API error", _first_line(txt), _clip(txt), actor, raw_kind=sub))
            continue
        if t == "user":
            r = item
            m = r.get("message") or {}
            content = m.get("content")
            if _is_tool_results(content) or not r.get("timestamp"):
                continue
            text = _text_of(content)
            eid = f"m{r.get('uuid')}"
            origin = r.get("origin") if isinstance(r.get("origin"), dict) else {}
            if r.get("isMeta"):
                if origin.get("handback") or "<task-notification>" in text or text.startswith("Another Claude session sent a message"):
                    body = origin.get("handbackReportText") or text
                    events.append(_event(eid, r["timestamp"], "note", "RETURN", "Background work returned", _first_line(body), _clip(body), actor, raw_kind="handback", quiet=True))
                continue
            if "<local-command-stdout>" in text or "<local-command-caveat>" in text:
                continue
            if text.startswith("[Request interrupted"):
                events.append(_event(eid, r["timestamp"], "red", "STOP", "Interrupted by user", _first_line(text), text, actor, raw_kind="interrupt"))
                continue
            cmd = _command(text)
            if cmd:
                events.append(_event(eid, r["timestamp"], "open", "COMMAND", cmd[:72], cmd, _clip(cmd), actor, raw_kind="command"))
                continue
            if not text.strip():
                continue
            events.append(_event(eid, r["timestamp"], "open", "PROMPT", _first_line(text)[:72] or "Prompt", _first_line(text), _clip(text), actor, raw_kind="user"))
            continue

        g = groups[item]
        r, blocks = g["rec"], g["blocks"]
        eid = f"m{r.get('uuid')}"
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        if g["error"]:
            events.append(_event(eid, r["timestamp"], "fail", "FAIL", "Turn failed", _first_line(text), _clip(text), actor, raw_kind="api_error"))
            continue
        if not calls:
            if text:
                events.append(_event(eid, r["timestamp"], "pass", "REPLY", _first_line(text)[:72] or "Reply", _first_line(text), _clip(text), actor, raw_kind="assistant"))
            continue
        names = [c.get("name") or "tool" for c in calls]
        parts = [text] if text else []
        errored = False
        for c in calls:
            args = json.dumps(c.get("input") or {}, ensure_ascii=False)
            res = results.get(c.get("id") or "")
            if res:
                errored |= res[1]
                parts.append(f"▸ {c.get('name')}  {_clip(args, 1500)}\n→ {'(error) ' if res[1] else ''}{_clip(res[0], 1500)}")
            else:
                parts.append(f"▸ {c.get('name')}  {_clip(args, 1500)}\n→ (running)")
        uniq = list(dict.fromkeys(names))
        first_args = json.dumps(calls[0].get("input") or {}, ensure_ascii=False)
        events.append(
            _event(
                eid,
                r["timestamp"],
                "spawn" if any(n in _SPAWN_TOOLS for n in names) else "note",
                uniq[0].replace("mcp__", "").upper()[:9],
                ", ".join(uniq) + (f" ({len(calls)} calls)" if len(calls) > 1 else "") + (" · error" if errored else ""),
                text and _first_line(text) or _first_line(first_args),
                "\n\n".join(parts),
                actor,
                raw_kind="tool_call",
                quiet=True,
                n=len(calls),
                tool_use_ids=[c.get("id") for c in calls],
            )
        )
    return events


def _status(path: Path, now: float, finished: bool = False) -> str:
    if finished:
        return "complete"
    return "in_progress" if now - path.stat().st_mtime < LIVE_WINDOW_S else "idle"


def _meta(sid: str, info: dict[str, Any], extra: list[tuple[str, Any]] = ()) -> list[list[str]]:
    rows = [
        ("session", sid),
        *extra,
        ("model", info["model"]),
        ("cwd", info["cwd"]),
        ("branch", info["branch"]),
        ("claude code", info["version"]),
        ("api calls", info["api_calls"]),
        ("tool calls", info["tool_calls"]),
        ("cache read", f"{info['cache_read']:,}" if info["cache_read"] else None),
    ]
    return [[k, str(v)] for k, v in rows if v]


def session_files() -> list[Path]:
    """Top-level session transcripts, most recently written first."""
    root = projects_dir()
    if not root.is_dir():
        return []
    files = [p for p in root.glob("*/*.jsonl") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def subagent_files(session: Path) -> list[Path]:
    d = session.with_suffix("") / "subagents"
    return sorted(d.rglob("agent-*.jsonl")) if d.is_dir() else []


def _agent_meta(path: Path) -> dict[str, Any]:
    p = path.with_name(path.name[: -len(".jsonl")] + ".meta.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find(sid: str) -> tuple[Path, str | None] | None:
    """(session transcript, agent id or None) for a session id or a subagent id."""
    if not _ok_id(sid):
        return None
    root = projects_dir()
    for p in root.glob(f"*/{sid}.jsonl"):
        return p, None
    for p in root.glob(f"*/*/subagents/**/agent-{sid}.jsonl"):
        return _session_of(p), sid
    if len(sid) >= 6:  # an unambiguous prefix, as shown in the sessions table
        hits = list(root.glob(f"*/{sid}*.jsonl"))
        if len(hits) == 1:
            return hits[0], None
    return None


def _session_of(agent_file: Path) -> Path:
    cur = agent_file.parent
    while cur.name != "subagents":
        cur = cur.parent
    return cur.parent.with_suffix(".jsonl")


def get(sid: str) -> dict[str, Any] | None:
    """Enough about a session to resume it. A subagent id resolves to its parent session."""
    hit = find(sid)
    if not hit:
        return None
    path, agent = hit
    if not path.is_file():
        return None
    info = _summary(_read(path))
    real = path.stem
    return {
        "id": real,
        "agent": agent,
        "title": info["title"] or info["agent_name"] or _first_line(info["first_prompt"])[:60] or real,
        "cwd": info["cwd"],
        "ended": time.time() - path.stat().st_mtime >= LIVE_WINDOW_S,
    }


def version(_src: Any = None) -> str:
    sig = []
    for p in session_files()[: MAX_SESSIONS * 2]:
        st = p.stat()
        sig.append(f"{p.stem[:8]}{st.st_size}")
        for a in subagent_files(p):
            sig.append(str(a.stat().st_size))
    # The minute bucket lets a quiet session flip from running to idle without a new write.
    return f"{hash(tuple(sig)) & 0xFFFFFFFF:x}:{int(time.time() // 60)}"


def sessions_data(_src: Any = None, limit: int = MAX_SESSIONS, only: str | None = None) -> dict[str, Any]:
    """The most recently active sessions, or just `only` when a session id is given."""
    now = time.time()
    if only:
        hit = find(only)
        if not hit or hit[1]:
            raise ValueError(f"unknown session {only}")
        tops = [hit[0]]
    else:
        tops = session_files()[:limit]

    parents = []
    for path in tops:
        recs = _read(path)
        info = _summary(recs)
        sid = path.stem
        actor = f"claude · {info['model'] or 'model unknown'}"
        events = _lane_events(recs, actor)
        spawn_ev = {tid: e for e in events for tid in (e.get("tool_use_ids") or [])}
        subs = []
        tin, tout = info["tokens_in"], info["tokens_out"]
        kids = []
        for kf in subagent_files(path):
            krecs = _read(kf)
            if krecs:
                kids.append((krecs[0].get("timestamp") or _iso(kf.stat().st_mtime), kf, krecs))
        for i, (t0, kf, krecs) in enumerate(sorted(kids, key=lambda k: k[0]), 1):
            kid = kf.stem[len("agent-"):]
            meta = _agent_meta(kf)
            kinfo = _summary(krecs)
            goal = kinfo["first_prompt"]
            kind = meta.get("agentType") or "subagent"
            kactor = f"{kind} · {kinfo['model'] or 'model unknown'}"
            label = meta.get("description") or _first_line(goal)[:30] or f"subagent {i}"
            kev = _lane_events(krecs, kactor)
            kev.insert(0, _event(f"spawned-{kid}", t0, "spawn", "SPAWNED", f"{label} spawned", _first_line(goal), _clip(goal), kactor, raw_kind="spawn"))
            kev = [e for e in kev if not (e["label"] == "PROMPT" and e["context"] == kev[0]["context"])]
            via = spawn_ev.get(meta.get("toolUseId") or "")
            events.append(_event(f"sp-{kid}", via["t"] if via else t0, "spawn", "SPAWN", f"Spawned {label}", _first_line(goal), _clip(goal), actor, raw_kind="spawn", child=kid))
            tin, tout = tin + kinfo["tokens_in"], tout + kinfo["tokens_out"]
            subs.append(
                {
                    "id": kid,
                    "name": label[:40],
                    "status": _status(kf, now, finished=bool(meta.get("finished"))),
                    "tokens": kinfo["tokens_in"] + kinfo["tokens_out"],
                    "meta": _meta(kid, kinfo, [("type", kind), ("parent", sid)]),
                    "events": sorted(kev, key=lambda e: e["t"]),
                }
            )
        if not events:
            continue
        events.sort(key=lambda e: e["t"])
        parents.append(
            {
                "id": sid,
                "name": info["title"] or info["agent_name"] or _first_line(info["first_prompt"])[:72] or sid,
                "agent": actor,
                "status": _status(path, now),
                "tokens_in": tin,
                "tokens_out": tout,
                "tokens": tin + tout,
                "lane_tokens": info["tokens_in"] + info["tokens_out"],
                "goal": next((e["context"] for e in events if e["label"] == "PROMPT"), ""),
                "meta": _meta(sid, info, [("project", path.parent.name)]),
                "ac": [],
                "events": events,
                "subs": subs,
            }
        )
    tin, tout = sum(p["tokens_in"] for p in parents), sum(p["tokens_out"] for p in parents)
    lanes = [p for p in parents] + [s for p in parents for s in p["subs"]]
    return {
        "src": "sessions",
        "version": version(),
        "db": str(projects_dir()),
        "totals": {
            "tokens_in": tin,
            "tokens_out": tout,
            "tokens": tin + tout,
            "tasks": len(parents),
            "events": sum(len(x["events"]) for x in lanes),
            "running": sum(1 for x in lanes if x["status"] == "in_progress"),
        },
        "parents": parents,
    }


def render_sessions_markdown(data: dict[str, Any]) -> str:
    """Compact table of recent sessions for the model (the web page is the visual)."""
    lines = ["# Claude Code sessions", "", "| session | title | status | subagents | events | tokens | cwd |",
             "|---|---|---|---|---|---|---|"]
    for p in data["parents"]:
        cwd = next((v for k, v in p["meta"] if k == "cwd"), "")
        title = p["name"].replace("|", "\\|")[:60]
        lines.append(f"| `{p['id'][:8]}` | {title} | {p['status']} | {len(p['subs'])} | {len(p['events'])} | {p['tokens']:,} | {cwd} |")
        for s in p["subs"]:
            lines.append(f"| ↳ `{s['id'][:8]}` | {s['name'].replace('|', '/')} | {s['status']} | | {len(s['events'])} | {s['tokens']:,} | |")
    if not data["parents"]:
        lines.append("| _no sessions found_ | | | | | | |")
    return "\n".join(lines) + "\n"
