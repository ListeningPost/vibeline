# vibeline for Claude Code

A Claude Code plugin that shows agent work as **rails**: one lane per agent, child lanes branching at spawn, tool
calls and acceptance-criteria results as marks along the track. Port of the
vibeline Hermes plugin in the parent directory; it lives in `claude-code/` and shares no files with it at runtime.

Two data sources, same rendering:

- **Claude Code sessions.** Every transcript under `~/.claude/projects`, across all projects, with subagents
  (`Agent`/`Task` calls) as child rails. Read-only, no setup.
- **Harness ledger.** The append-only, hash-chained SQLite `events` table written by an agent-harness MCP: tasks,
  spawned workers, AC checks (PASS / FAIL / expected TDD RED). A sample ledger is bundled.

Runtime dependency: Python 3.10+ standard library only. `rich` is used by the standalone terminal panel if present.

## Install

```bash
claude plugin marketplace add ListeningPost/vibeline
```

```bash
claude plugin install vibeline@vibeline
```

From a local checkout, `claude plugin marketplace add /path/to/vibeline` works the same way.

Restart Claude Code, then run `/vibeline:check`.

The MCP server is started with `python`. On macOS/Linux, where that is often `python3`, set it once in
`~/.claude/settings.json`:

```json
{ "env": { "VIBELINE_PYTHON": "python3" } }
```

## Use

| Command | What you get |
|---|---|
| `/vibeline:web [ledger]` | Live page on `http://127.0.0.1:8877/`: every session as a rail, subagents branching at spawn. Click a mark for the inspector; **open / resume** (or `t`) runs `claude --resume` in a new terminal. `ledger` opens the ledger view. |
| `/vibeline:sessions [limit]` | Markdown table of recent sessions and their subagents |
| `/vibeline:open <id>` | New terminal window running `claude --resume <id>` in the session's cwd (8-char prefixes and subagent ids work) |
| `/vibeline:rails [task_id] [--tokens]` | Ledger rails for one task (latest if omitted) |
| `/vibeline:dashboard` | Task × AC × fails × actors table |
| `/vibeline:check` | Self-diagnostic |

The `vibeline` skill also lets Claude use the tools when you just ask ("show me what my subagents did").

MCP tools: `vibeline_sessions`, `vibeline_web`, `vibeline_open`, `vibeline_rails`, `vibeline_dashboard`, `vibeline_check`.

The web server runs inside the plugin's MCP process, so it lives as long as the Claude Code session that started it. A
second session reuses a server already on the port.

## Settings (environment variables)

Set these in the `"env"` block of `~/.claude/settings.json`:

| Variable | Meaning |
|---|---|
| `VIBELINE_DB` (or `TRACE_DB`) | Ledger path. Fallbacks: `~/.claude/vibeline/trace.db`, then the bundled sample. |
| `VIBELINE_SHOW_TOKENS` | `1` to include token counts in ledger markdown. |
| `VIBELINE_TERMINAL` | Terminal for open/resume. Defaults: Windows `wt` then `cmd`; macOS Terminal.app; Linux xdg-terminal-exec, gnome-terminal, kgx, ptyxis, kitty, konsole, wezterm, alacritty, foot, xfce4-terminal, xterm. |
| `VIBELINE_PYTHON` | Python used to start the MCP server (default `python`). |
| `CLAUDE_CONFIG_DIR` | Read transcripts from `<dir>/projects` instead of `~/.claude/projects`. |

## Standalone (no Claude Code needed)

```bash
python /path/to/vibeline/claude-code/vibeline sessions
```

Also: `rails [--markdown]`, `dashboard`, `tasks`, `web --open`, `open SESSION_ID [--dry]`, `check`, `mcp`.

## How sessions map to rails

| Transcript record | Mark |
|---|---|
| your prompt / slash command | PROMPT / COMMAND (open) |
| assistant text with no tools | REPLY (pass) |
| assistant tool calls (blocks of one API message merged) | tool name (note), or spawn for `Agent`/`Task` |
| subagent transcript | child rail, starting at SPAWNED; title from its description |
| background agent hand-back | RETURN |
| `[Request interrupted by user]` | STOP |
| API error | FAIL |
| compaction | COMPACT |

A session is **running** if its transcript was written in the last 15 minutes, else **idle**. Tokens are input +
cache-creation + output, counted once per API message.

## Differences from the Hermes plugin

- Hermes `state.db` → Claude Code JSONL transcripts; `hermes --resume` → `claude --resume`.
- The Hermes Ink TUI widget is gone (Claude Code has no widget API); `/vibeline:rails` shows markdown, which Claude Code renders.
- Hermes plugin settings → environment variables above.
- Added: Windows and macOS terminal spawning, `vibeline_sessions`, `vibeline_web` (server from the MCP process), prefix ids.

## Development

```bash
python -m pytest tests -q
```

```bash
claude plugin validate .
```

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 ListeningPost.
