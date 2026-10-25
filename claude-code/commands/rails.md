---
description: Show harness-ledger rails for a task (latest if omitted)
argument-hint: "[task_id] [--tokens]"
allowed-tools: mcp__plugin_vibeline_vibeline__vibeline_rails
---
Call the `vibeline_rails` tool. Arguments from "$ARGUMENTS": a bare word is `task_id`; `--tokens` sets `show_tokens: true`; `--db PATH` sets `db`.

Then output the returned markdown exactly as-is, with no commentary before it. After it, add at most one line: the ledger path, and a note if it is the bundled sample. If the tool errors, show the error and suggest `/vibeline:check`.
