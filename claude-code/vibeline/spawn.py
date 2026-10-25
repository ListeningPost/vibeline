"""Spawn a terminal running `claude --resume` for a session shown in the UI."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import sessions

_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


class SpawnError(ValueError):
    pass


def _ok_id(sid: str) -> bool:
    return bool(sid) and sid.strip() == sid and all(c in _ID_OK for c in sid)


def claude_argv(sid: str) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise SpawnError("claude not on PATH")
    return [exe, "--resume", sid]


def _gnome(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["gnome-terminal"]
    if title:
        cmd += ["--title", title]
    if cwd:
        cmd += ["--working-directory", cwd]
    return cmd + ["--", *inner]


def _kgx(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["kgx"]
    if title:
        cmd += ["--title", title]
    if cwd:
        cmd += ["--working-directory", cwd]
    return cmd + ["--", *inner]


def _kitty(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["kitty"]
    if title:
        cmd += ["--title", title]
    if cwd:
        cmd += ["--directory", cwd]
    return cmd + inner


def _konsole(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["konsole"]
    if cwd:
        cmd += ["--workdir", cwd]
    return cmd + ["-e", *inner]


def _wezterm(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["wezterm", "start"]
    if cwd:
        cmd += ["--cwd", cwd]
    return cmd + ["--", *inner]


def _alacritty(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["alacritty"]
    if title:
        cmd += ["--title", title]
    if cwd:
        cmd += ["--working-directory", cwd]
    return cmd + ["-e", *inner]


def _foot(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["foot"]
    if title:
        cmd += ["-T", title]
    if cwd:
        cmd += ["-D", cwd]
    return cmd + inner


def _xfce(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["xfce4-terminal"]
    if title:
        cmd += ["--title", title]
    if cwd:
        cmd += ["--working-directory", cwd]
    return cmd + ["-x", *inner]


def _xterm(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["xterm"]
    if title:
        cmd += ["-T", title]
    return cmd + ["-e", *inner]


def _xdg(inner: list[str], cwd: str | None, title: str) -> list[str]:
    return ["xdg-terminal-exec", "--", *inner]


def _wt(inner: list[str], cwd: str | None, title: str) -> list[str]:
    cmd = ["wt", "new-tab"]
    if title:
        cmd += ["--title", title]
    if cwd:
        cmd += ["-d", cwd]
    return cmd + inner


def _cmd(inner: list[str], cwd: str | None, title: str) -> list[str]:
    # `start` opens a new console window; the empty-ish title arg is required when the program path is quoted.
    cmd = ["cmd", "/c", "start", title or "claude"]
    if cwd:
        cmd += ["/D", cwd]
    return cmd + ["cmd", "/k", *inner]


def _mac_terminal(inner: list[str], cwd: str | None, title: str) -> list[str]:
    line = (f"cd {shlex.quote(cwd)} && " if cwd else "") + " ".join(shlex.quote(a) for a in inner)
    script = f'tell application "Terminal" to do script "{line.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
    return ["osascript", "-e", script, "-e", 'tell application "Terminal" to activate']


_BUILDERS: dict[str, Callable[[list[str], str | None, str], list[str]]] = {
    "xdg-terminal-exec": _xdg,
    "gnome-terminal": _gnome,
    "kgx": _kgx,
    "ptyxis": lambda inner, cwd, title: (
        ["ptyxis", "--new-window"] + (["--working-directory", cwd] if cwd else []) + ["--", *inner]
    ),
    "kitty": _kitty,
    "konsole": _konsole,
    "wezterm": _wezterm,
    "alacritty": _alacritty,
    "foot": _foot,
    "xfce4-terminal": _xfce,
    "xterm": _xterm,
}


_WINDOWS: dict[str, Callable[[list[str], str | None, str], list[str]]] = {"wt": _wt, "cmd": _cmd}
_MAC: dict[str, Callable[[list[str], str | None, str], list[str]]] = {"wezterm": _wezterm, "kitty": _kitty, "alacritty": _alacritty, "osascript": _mac_terminal}


def _platform_builders() -> dict[str, Callable[[list[str], str | None, str], list[str]]]:
    if sys.platform == "win32":
        return {**_WINDOWS, **_BUILDERS}
    if sys.platform == "darwin":
        return _MAC
    return _BUILDERS


def _has_display() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def pick_terminal() -> tuple[str, Callable[[list[str], str | None, str], list[str]]]:
    builders = _platform_builders()
    override = (os.environ.get("VIBELINE_TERMINAL") or "").strip()
    if override:
        base = Path(override).name
        stem = Path(override).stem
        which = shutil.which(override) or (shutil.which(base) if base != override else None)
        if not which:
            raise SpawnError(f"terminal not found: {override}")
        known = {**_WINDOWS, **_MAC, **_BUILDERS}
        if base in known or stem in known:
            return which, known.get(base) or known[stem]
        return which, lambda inner, cwd, title: [which, "-e", *inner]
    for name, builder in builders.items():
        which = shutil.which(name)
        if which:
            return which, builder
    raise SpawnError("no terminal emulator found")


def terminal_argv(inner: list[str], cwd: str | None, title: str) -> list[str]:
    which, builder = pick_terminal()
    argv = builder(inner, cwd, title)
    argv[0] = which
    return argv


def plan(sid: str) -> dict[str, Any]:
    if not _ok_id(sid):
        raise SpawnError("bad session id")
    row = sessions.get(sid)
    if not row:
        raise SpawnError(f"unknown session {sid}")
    real = row["id"]  # a subagent id resumes its parent session
    cwd = (row.get("cwd") or "").strip() or None
    if cwd and not os.path.isdir(cwd):
        cwd = None
    title = f"claude · {row.get('title') or real}"
    inner = claude_argv(real)
    argv = terminal_argv(inner, cwd, title)
    return {
        "session": real,
        "title": row.get("title") or real,
        "cwd": cwd or row.get("cwd"),
        "ended": bool(row.get("ended")),
        "cmd": inner,
        "terminal": Path(argv[0]).name,
        "argv": argv,
    }


def launch(
    sid: str,
    *,
    dry: bool = False,
    popen: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    try:
        if not _has_display() and not dry:
            raise SpawnError("no graphical session (DISPLAY/WAYLAND_DISPLAY unset)")
        spec = plan(sid)
        if dry:
            return {"ok": True, "dry": True, **spec}
        kw: dict[str, Any] = {}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        else:
            kw["start_new_session"] = True
        popen(
            spec["argv"],
            cwd=spec["cwd"] if spec["cwd"] and os.path.isdir(str(spec["cwd"])) else None,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kw,
        )
        return {"ok": True, **{k: spec[k] for k in ("session", "title", "cwd", "ended", "cmd", "terminal")}}
    except SpawnError as e:
        return {"ok": False, "error": str(e)}
