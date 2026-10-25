---
description: Open the live rails web page (Claude Code sessions, or the ledger)
argument-hint: "[ledger] [--port N] [--no-open]"
allowed-tools: mcp__plugin_vibeline_vibeline__vibeline_web
---
Call the `vibeline_web` tool. From "$ARGUMENTS": the word `ledger` sets `ledger: true`; `--port N` sets `port`; `--no-open` sets `open: false`.

Reply with the URL on its own line and one short sentence: whether this session started the server or reused one, and that it stops when this Claude Code session ends. Keys on the page: click a mark for the inspector, `t` or the open/resume button starts `claude --resume` in a new terminal.
