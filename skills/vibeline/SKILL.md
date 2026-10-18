---
name: vibeline
description: "Use when rendering agent-harness rails in the Hermes TUI. /rails Ink widget, hermes vibeline, vibeline_rails."
---

# Vibeline

Render the append-only task ledger as an Ink TUI widget (`/rails`), not as markdown in the transcript. Classic CLI (`display.interface: cli`) strips markdown — dumping rails into the assistant reply is the wrong artifact.

## Commands

- TUI: `/rails` — modal widget (parent/child rails, spawn-start, inspector). Esc/q closes.
- Classic CLI: `/rails` writes `~/.hermes/tui-widgets/rails.mjs` and prints a Rich panel.
- CLI: `hermes vibeline rails [task_id]` / `hermes vibeline open SESSION_ID`
- Tool: `vibeline_rails` — `{markdown}` for the model only
- Web: click **open** / **resume** (or press `t`) to spawn `hermes --resume` in that session's cwd

## Rules

- Child rails begin at spawn.
- FAIL stays. TDD RED is not FAIL.
- Do not dump tokens unless asked (`--tokens`).
- Do not paste markdown rails into the chat.
- Ledger path: plugin setting `db`, else `VIBELINE_DB` / `TRACE_DB`, else the sample harness db.
