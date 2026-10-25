---
description: Resume a Claude Code session in a new terminal window
argument-hint: "<session_id or 8-char prefix> [--dry]"
allowed-tools: mcp__plugin_vibeline_vibeline__vibeline_open
---
Call the `vibeline_open` tool with `session_id` set to the id in "$ARGUMENTS" (a full id, a subagent id, or an unambiguous prefix of at least 6 characters as shown by `/vibeline:sessions`). If "$ARGUMENTS" contains `--dry`, also pass `dry: true`.

Report in one line which terminal opened, the command, and the working directory, or the error. If no id was given, ask for one and suggest `/vibeline:sessions`.
