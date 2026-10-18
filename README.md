# vibeline

A [Hermes](https://github.com/NousResearch/hermes-agent) plugin that shows agent work as **rails**: one lane per
agent, child lanes branching at spawn, tool calls and acceptance-criteria results as marks along the track.

Three surfaces, same data:

| Surface | Command | What you get |
|---|---|---|
| Hermes TUI | `/rails` | Ink widget inside `hermes --tui` (parent/child rails, inspector, Esc/q closes) |
| Web | `hermes vibeline web --open` | Live page: every Hermes session on the machine, plus the harness ledger |
| CLI / model | `hermes vibeline rails`, tool `vibeline_rails` | Rich panel in the terminal, markdown for the model |

Runtime dependencies: Python 3.10+ standard library only. `rich` is used if present (Hermes ships it).

## Quick start (5 minutes)

```bash
git clone <this repo> ~/vibeline        # or unpack the archive anywhere
cd ~/vibeline
./install.sh                            # links into ~/.hermes/plugins, enables, runs self-check
```

The install script is idempotent. It refuses to overwrite a plugin link that points somewhere else.

Then:

```bash
hermes --tui            # inside the TUI type:  /rails
hermes vibeline web --open
```

With no ledger configured, vibeline renders the bundled sample at `examples/trace.db` so you can see it work
before pointing it at real data. The Sessions tab of the web UI needs no configuration at all. It reads Hermes's
own `~/.hermes/state.db` read-only.

## Manual install

If you prefer not to run the script:

```bash
ln -sfn /path/to/vibeline ~/.hermes/plugins/vibeline
hermes plugins enable vibeline
hermes vibeline check
```

`hermes vibeline check` verifies Python version, plugin link, ledger, `state.db`, widget directory and terminal
emulator. It exits non-zero on a hard failure. Run it first whenever something looks off.

## Pointing at a real ledger

The harness ledger is an append-only SQLite file written by the harness MCP (table `events`, hash-chained).
vibeline only reads it. Resolution order:

1. `--db PATH` on the command line, or the `db` argument on the tool
2. Plugin setting `plugins.entries.vibeline.settings.db` in `~/.hermes/config.yaml`
3. `VIBELINE_DB`, then `TRACE_DB` environment variables
4. `~/.hermes/vibeline/trace.db`
5. The bundled sample `examples/trace.db`

To set it once:

```bash
./install.sh --db /path/to/trace.db
# or
hermes config set plugins.entries.vibeline.settings.db /path/to/trace.db
```

Equivalent YAML:

```yaml
plugins:
  entries:
    vibeline:
      settings:
        db: /path/to/trace.db
        show_tokens: false
```

## Everyday commands

```bash
hermes vibeline rails [TASK_ID] [--db PATH] [--tokens]   # latest task if TASK_ID omitted
hermes vibeline dashboard                                 # task x AC table
hermes vibeline tasks                                     # list task ids
hermes vibeline web [--port 8877] [--host 127.0.0.1] [--open]
hermes vibeline open SESSION_ID [--dry]                   # new terminal running: hermes --resume SESSION_ID
hermes vibeline check [--db PATH]
```

In the web Sessions page, **open** / **resume** on a lane (or the `t` key) spawns a terminal in that session's cwd.
Terminal detection tries xdg-terminal-exec, gnome-terminal, kgx, ptyxis, kitty, konsole, wezterm, alacritty, foot,
xfce4-terminal, xterm in that order. Override with `VIBELINE_TERMINAL=<name>`.

Standalone, without Hermes installed (ledger and web views only):

```bash
python3 /path/to/vibeline rails
python3 /path/to/vibeline web --open
```

## Tools exposed to the model

- `vibeline_rails {task_id?, db?, show_tokens?}` returns `{ok, task_id, db, markdown}`
- `vibeline_dashboard {db?}` returns `{ok, db, markdown}`

The bundled skill (`skills/vibeline/SKILL.md`) tells the model to open the widget rather than paste rails into
the transcript, because classic CLI mode strips markdown.

## Rendering rules

- The parent owns the clock. Child rails start at their spawn event.
- FAIL stays on the rail. An expected TDD RED (`expect=red`, `why` starting `RED:`, or "as expected") is not an
  acceptance-criteria failure.
- Token counts are off unless `--tokens` or `show_tokens: true`.

## Development

```bash
pip install -r requirements-dev.txt   # pytest only
make test                             # or: python3 -m pytest tests -q
make check                            # python3 . check
hermes plugins validate .             # Hermes's own manifest/security check
```

Layout:

```
__init__.py   register(): tools, /rails slash command, `hermes vibeline` CLI, skill
cli.py        subcommand tree          doctor.py   `check` self-diagnostic
ledger.py     read + verify the ledger sessions.py read ~/.hermes/state.db (read-only)
rails.py      classify events, build lane/rail geometry, markdown
widget.py     write tui/rails.mjs into ~/.hermes/tui-widgets, Rich panel
web.py        HTTP server + JSON API for web/index.html
spawn.py      terminal spawning for open/resume
examples/     sample trace.db          sketches/   design mockups, not shipped to Hermes
```

## Troubleshooting

- **`/rails` does nothing in the TUI.** Run `hermes vibeline check`; the tui-widgets dir must be writable. The
  TUI hot-loads `~/.hermes/tui-widgets/rails.mjs`, which the plugin writes at registration and on each `/rails`.
- **"no ledger"**. Nothing in the resolution order above exists. Pass `--db` or set the plugin setting.
- **Web UI: "restart vibeline web"**. An older server process is still running on the port. Stop it or use `--port`.
- **`open` fails with "no terminal emulator found"**. Set `VIBELINE_TERMINAL` to the binary name of your terminal.
- **Plugin not loading after a Hermes upgrade.** `hermes plugins doctor` and `hermes plugins validate /path/to/vibeline`.
