"""Spawn a terminal running `hermes --resume` for a session shown in the UI.

On a machine with no graphical session (a server reached through another machine's page) the session starts in a
detached tmux session instead, and the caller gets the `tmux attach` command to show."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import sessions

_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


class SpawnError(ValueError):
    pass


def _ok_id(sid: str) -> bool:
    return bool(sid) and sid.strip() == sid and all(c in _ID_OK for c in sid)


def hermes_argv(sid: str, query: str | None = None) -> list[str]:
    """`hermes --resume SID`, or with `query` a resumed chat that submits it as the first turn."""
    exe = shutil.which("hermes")
    if not exe:
        raise SpawnError("hermes not on PATH")
    if query:
        return [exe, "chat", "--resume", sid, "-q", query]
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


def _has_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def pick_terminal() -> tuple[str, Callable[[list[str], str | None, str], list[str]]]:
    override = (os.environ.get("VIBELINE_TERMINAL") or "").strip()
    if override:
        base = Path(override).name
        which = shutil.which(override) or (shutil.which(base) if base != override else None)
        if not which:
            raise SpawnError(f"terminal not found: {override}")
        if base in _BUILDERS:
            return which, _BUILDERS[base]
        return which, lambda inner, cwd, title: [which, "-e", *inner]
    for name, builder in _BUILDERS.items():
        which = shutil.which(name)
        if which:
            return which, builder
    raise SpawnError("no terminal emulator found")


def tmux_name(sid: str) -> str:
    """tmux session names can't hold `.` or `:`."""
    return "vibeline-" + "".join(c if c.isalnum() or c in "-_" else "_" for c in sid)


def _wants_tmux() -> bool:
    forced = Path((os.environ.get("VIBELINE_TERMINAL") or "").strip()).name == "tmux"
    return forced or not _has_display()


def _tmux_has(exe: str, name: str, run: Callable[..., Any]) -> bool:
    try:
        return run([exe, "has-session", "-t", f"={name}"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def terminal_argv(inner: list[str], cwd: str | None, title: str) -> list[str]:
    which, builder = pick_terminal()
    argv = builder(inner, cwd, title)
    argv[0] = which
    return argv


def plan(sid: str, db: Path, query: str | None = None, tmux: bool = False) -> dict[str, Any]:
    if not _ok_id(sid):
        raise SpawnError("bad session id")
    row = sessions.get(db, sid)
    if not row:
        raise SpawnError(f"unknown session {sid}")
    cwd = (row.get("cwd") or "").strip() or None
    if cwd and not os.path.isdir(cwd):
        cwd = None
    title = f"hermes · {row.get('title') or sid}"
    inner = hermes_argv(sid, query)
    attach = None
    if tmux:
        exe = shutil.which("tmux")
        if not exe:
            raise SpawnError("no graphical session here and tmux is not installed")
        name = tmux_name(sid)
        argv = [exe, "new-session", "-d", "-s", name, *(["-c", cwd] if cwd else []), *inner]
        attach = f"tmux attach -t {name}"
    else:
        argv = terminal_argv(inner, cwd, title)
    return {
        "attach": attach,
        "session": sid,
        "title": row.get("title") or sid,
        "cwd": cwd or row.get("cwd"),
        "ended": bool(row.get("ended_at")),
        "cmd": inner,
        "terminal": Path(argv[0]).name,
        "argv": argv,
    }


def launch(
    sid: str,
    db: Path,
    *,
    dry: bool = False,
    popen: Callable[..., Any] = subprocess.Popen,
    query: str | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    try:
        tmux = _wants_tmux() and (not dry or bool(shutil.which("tmux")))
        spec = plan(sid, db, query, tmux=tmux)
        if dry:
            return {"ok": True, "dry": True, **spec}
        keys = ("session", "title", "cwd", "ended", "cmd", "terminal", "attach")
        if tmux and _tmux_has(spec["argv"][0], tmux_name(sid), run):
            if query:
                raise SpawnError(f"already open in tmux; close it first ({spec['attach']})")
            return {"ok": True, "existing": True, **{k: spec[k] for k in keys}}
        popen(
            spec["argv"],
            cwd=spec["cwd"] if spec["cwd"] and os.path.isdir(str(spec["cwd"])) else None,
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, **{k: spec[k] for k in keys}}
    except SpawnError as e:
        return {"ok": False, "error": str(e)}
