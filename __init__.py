"""Vibeline — rails in the Hermes TUI (Ink widget) and classic CLI panel."""
from __future__ import annotations

from pathlib import Path

from . import tools
from .cli import run as cli_run
from .cli import setup_argparse
from .ledger import load_events, resolve_db
from .rails import widget_data
from .widget import install_widget, print_panel


def register(ctx):
    db_setting = ctx.get_config("db", default="") or ""
    show_tokens = bool(ctx.get_config("show_tokens", default=False))

    try:
        install_widget(db_setting or None, auto_open=False)
    except Exception:
        pass

    def rails_tool(args, **kw):
        return tools.handle_vibeline_rails(args, db_setting=db_setting, show_tokens=show_tokens, **kw)

    def dash_tool(args, **kw):
        return tools.handle_vibeline_dashboard(args, db_setting=db_setting, **kw)

    ctx.register_tool(name="vibeline_rails", toolset="vibeline", schema=tools.SCHEMA_RAILS, handler=rails_tool)
    ctx.register_tool(
        name="vibeline_dashboard", toolset="vibeline", schema=tools.SCHEMA_DASHBOARD, handler=dash_tool
    )

    def slash_rails(raw_args: str) -> str:
        parts = (raw_args or "").split()
        task_id = None
        db = db_setting
        i = 0
        while i < len(parts):
            if parts[i] in ("--tokens", "-t"):
                pass
            elif parts[i] == "--db" and i + 1 < len(parts):
                i += 1
                db = parts[i]
            elif not parts[i].startswith("-"):
                task_id = parts[i]
            i += 1
        try:
            install_widget(db or None, task_id, auto_open=True)
            print_panel(widget_data(load_events(resolve_db(db or None), task_id)))
        except Exception as e:
            return f"vibeline: {e}"
        return None

    ctx.register_command(
        "rails",
        handler=slash_rails,
        description="Open harness rails in the TUI (Ink widget) or print a CLI panel",
        args_hint="[task_id] [--db PATH] [--tokens]",
    )
    ctx.register_cli_command(
        name="vibeline",
        help="Markdown rails for the agent-harness ledger",
        setup_fn=setup_argparse,
        handler_fn=cli_run,
        description="hermes vibeline rails | dashboard | tasks | web | open | check",
    )
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.is_file():
                ctx.register_skill(child.name, skill_md)
