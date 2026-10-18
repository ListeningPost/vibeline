"""hermes vibeline — CLI subcommand tree."""
from __future__ import annotations

from .ledger import list_tasks, load_events, resolve_db
from .rails import render_dashboard, widget_data
from .widget import install_widget, print_panel
from . import sessions as hermes_sessions


def _print_md(md: str) -> None:
    try:
        from hermes_cli.cli_render import _render_final_assistant_content
        from rich.console import Console

        Console().print(_render_final_assistant_content(md))
    except Exception:
        print(md, end="" if md.endswith("\n") else "\n")


def run(args) -> None:
    cmd = getattr(args, "vibeline_cmd", None)
    if cmd == "check":
        from .doctor import run_check

        raise SystemExit(run_check(getattr(args, "db", None) or None))
    try:
        db = resolve_db(getattr(args, "db", None) or None)
    except FileNotFoundError:
        if cmd not in ("web", "open"):
            raise
        db = None  # live sessions still work without a harness ledger
    if cmd == "open":
        from .spawn import launch

        out = launch(args.session_id, hermes_sessions.state_db(), dry=args.dry)
        if not out.get("ok"):
            raise SystemExit(f"vibeline: {out.get('error') or 'spawn failed'}")
        inner = " ".join(out["cmd"])
        how = "dry-run" if out.get("dry") else "opened"
        print(f"vibeline: {how} {out['terminal']} → {inner}" + (f"  ({out['cwd']})" if out.get("cwd") else ""))
        return
    if cmd == "dashboard":
        tasks = list_tasks(db)
        _print_md(render_dashboard({t: load_events(db, t) for t in tasks}))
        return
    if cmd == "tasks":
        for t in list_tasks(db):
            print(t)
        return
    if cmd == "web":
        from .web import serve

        serve(db, port=args.port, host=args.host, open_browser=args.open)
        return
    task_id = getattr(args, "task_id", None) or None
    install_widget(str(db), task_id, auto_open=True)
    print_panel(widget_data(load_events(db, task_id)))


def setup_argparse(subparser) -> None:
    subs = subparser.add_subparsers(dest="vibeline_cmd")
    subs.required = False
    rails_p = subs.add_parser("rails", help="Markdown rails for a task (default)")
    rails_p.add_argument("task_id", nargs="?", default=None)
    rails_p.add_argument("--db", default="", help="Path to events sqlite")
    rails_p.add_argument("--tokens", action="store_true", help="Include token counts")
    dash = subs.add_parser("dashboard", help="Task × AC table")
    dash.add_argument("--db", default="", help="Path to events sqlite")
    tasks = subs.add_parser("tasks", help="List task ids")
    tasks.add_argument("--db", default="", help="Path to events sqlite")
    web = subs.add_parser("web", help="Serve the live rails page")
    web.add_argument("--db", default="", help="Path to events sqlite")
    web.add_argument("--port", type=int, default=8877)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--open", action="store_true", help="Open the page in a browser")
    op = subs.add_parser("open", help="Spawn a terminal resumed on a Hermes session")
    op.add_argument("session_id")
    op.add_argument("--dry", action="store_true", help="Print the command without spawning")
    chk = subs.add_parser("check", help="Verify the install: python, ledger, state.db, widget dir, terminal")
    chk.add_argument("--db", default="", help="Path to events sqlite")
    subparser.set_defaults(func=run, vibeline_cmd="rails")
