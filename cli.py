"""hermes vibeline — CLI subcommand tree."""
from __future__ import annotations

from .ledger import list_tasks, load_events, resolve_db
from .rails import render_dashboard, widget_data
from .widget import install_widget, print_panel
from . import sessions as hermes_sessions


def _print_md(md: str) -> None:
    try:
        from rich.console import Console
        from rich.markdown import Markdown

        Console().print(Markdown(md))
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
        if cmd not in ("web", "open", "rewind", "feedback"):
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

        serve(db, port=args.port, host=args.host, open_browser=args.open, token=args.token,
              peer_specs=args.peer, name=args.name or None, read_only=args.read_only)
        return
    if cmd == "rewind":
        from .rewind import rewind

        out = rewind(args.session_id, args.event, hermes_sessions.state_db(), retry=not args.no_retry, dry=args.dry)
        if not out.get("ok"):
            raise SystemExit(f"vibeline: {out.get('error') or 'rewind failed'}")
        if out.get("dry"):
            print(f"vibeline: dry-run, would drop {out['drops']} messages from {out['session']} starting at message {out['message']}")
        else:
            print(f"vibeline: rewound {out['rewound']} messages" + (", prompt resubmitted" if out.get("retried") else "")
                  + (f"  ({out['warning']})" if out.get("warning") else ""))
        return
    if cmd == "feedback":
        from . import feedback

        fetch = lambda machine, session: None if machine else hermes_sessions.sessions_data(hermes_sessions.state_db(), only=session, full=True)
        rows = feedback.export_rows(feedback.store_path(), fetch, rated_only=args.rated_only)
        text = feedback.to_jsonl(rows) if args.format == "jsonl" else feedback.to_csv(rows)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            print(f"vibeline: wrote {len(rows)} rows to {args.out}")
        else:
            print(text, end="")
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
    web.add_argument("--token", default="", help="Shared secret for non-loopback clients (generated when --host is not loopback)")
    web.add_argument("--peer", action="append", default=[], metavar="NAME=URL", help="Also show another machine's vibeline (repeatable)")
    web.add_argument("--name", default="", help="This machine's label (default: hostname)")
    web.add_argument("--read-only", action="store_true", help="Refuse open/resume/rewind requests")
    rw = subs.add_parser("rewind", help="Rewind a session to one of its prompts and reopen it")
    rw.add_argument("session_id")
    rw.add_argument("event", help="Prompt event id from the web UI, e.g. m1234")
    rw.add_argument("--dry", action="store_true", help="Report what would be dropped; change nothing")
    rw.add_argument("--no-retry", action="store_true", help="Open the session without resubmitting the prompt")
    fb = subs.add_parser("feedback", help="Export feedback-mode ratings joined to session activity")
    fb.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    fb.add_argument("--out", default="", help="Write to a file instead of stdout")
    fb.add_argument("--rated-only", action="store_true", help="Only rated events, not the whole session")
    op = subs.add_parser("open", help="Spawn a terminal resumed on a Hermes session")
    op.add_argument("session_id")
    op.add_argument("--dry", action="store_true", help="Print the command without spawning")
    chk = subs.add_parser("check", help="Verify the install: python, ledger, state.db, widget dir, terminal")
    chk.add_argument("--db", default="", help="Path to events sqlite")
    subparser.set_defaults(func=run, vibeline_cmd="rails")
