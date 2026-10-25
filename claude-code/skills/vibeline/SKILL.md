---
name: vibeline
description: "Use when the user wants to see agent work as rails or a timeline: what their Claude Code sessions and subagents did, which are running, a visual of spawned subagents, or the agent-harness task ledger (tasks, acceptance criteria, PASS/FAIL, TDD RED). Tools vibeline_sessions, vibeline_web, vibeline_rails, vibeline_dashboard, vibeline_open, vibeline_check."
---

# Vibeline

Rails: one lane per agent, child lanes branching at spawn, tool calls and acceptance-criteria results as marks along the track. Two data sources:

- **Claude Code sessions**: every transcript under `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`), with subagents from `<session>/subagents/`. Read-only, no setup.
- **Harness ledger**: an append-only, hash-chained SQLite `events` table written by an agent-harness MCP. Path from `VIBELINE_DB`, then `TRACE_DB`, then `~/.claude/vibeline/trace.db`, else the bundled sample.

## Which tool

| User wants | Tool | Slash command |
|---|---|---|
| A visual of sessions/subagents, live | `vibeline_web` → give the URL | `/vibeline:web` |
| A quick list of recent sessions | `vibeline_sessions` | `/vibeline:sessions` |
| Rails for one ledger task | `vibeline_rails` | `/vibeline:rails` |
| All ledger tasks × AC | `vibeline_dashboard` | `/vibeline:dashboard` |
| Reopen a session in a terminal | `vibeline_open` | `/vibeline:open` |
| Something looks broken | `vibeline_check` | `/vibeline:check` |

## Rules

- Child rails begin at spawn. The parent owns the clock.
- FAIL stays on the rail. An expected TDD RED (`expect=red`, `why` starting `RED:`) is not an AC failure; never report it as one.
- Do not show token counts unless asked (`show_tokens`).
- Show tool markdown as returned; do not redraw the rails by hand.
- Ask before `vibeline_open` unless the user asked to open/resume a session: it opens a window on their desktop.
- The web server binds 127.0.0.1 only and lives as long as this Claude Code session.
