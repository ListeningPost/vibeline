"""Standalone (no Hermes needed): python3 /path/to/vibeline rails | dashboard | web | check"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load():
    name = "vibeline"
    if name in sys.modules and getattr(sys.modules[name], "__path__", None):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name
    mod.__path__ = [str(ROOT)]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> None:
    _load()
    from vibeline.cli import run, setup_argparse

    p = argparse.ArgumentParser(prog="vibeline")
    setup_argparse(p)
    args = p.parse_args(argv)
    if not getattr(args, "vibeline_cmd", None):
        args.vibeline_cmd = "rails"
    run(args)


if __name__ == "__main__":
    main()
