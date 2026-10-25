"""python /path/to/vibeline [rails|dashboard|tasks|sessions|web|open|check|mcp]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run as a directory (python <plugin>/vibeline): make the package importable by name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["mcp"]:
        from vibeline.mcp_server import main as serve_mcp

        serve_mcp()
        return
    from vibeline.cli import run, setup_argparse

    p = argparse.ArgumentParser(prog="vibeline")
    setup_argparse(p)
    args = p.parse_args(argv)
    if not getattr(args, "vibeline_cmd", None):
        args.vibeline_cmd = "rails"
    run(args)


if __name__ == "__main__":
    main()
