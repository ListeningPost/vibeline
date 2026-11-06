"""Name the program a shell tool call actually ran, so a step reads `shell: pytest` instead of `TERMINAL`.

A best-effort reading of the command line, not a shell parser: it never executes anything and falls back to
nothing when it can't tell.
"""
from __future__ import annotations

import os
import re
import shlex

SHELL_TOOLS = {"terminal"}  # Hermes tools whose `command` argument is a shell command line

# Plumbing around the command that did the work; only named when a script has nothing else.
_NOISE = {"cd", "echo", "printf", "sleep", "export", "set", "unset", "source", ".", "true", "false", ":", "wait",
          "pushd", "popd", "test", "[", "[[", "read", "local", "return", "exit", "trap", "shopt", "ulimit", "umask"}
# `sudo make` is make; `timeout 30 curl` is curl.
_WRAP = {"sudo", "nohup", "time", "env", "exec", "command", "nice", "ionice", "stdbuf", "setsid", "timeout",
         "xargs", "watch", "doas", "chronic", "unbuffer"}
_SKIP_LINE = {"for", "case", "esac", "function", "done", "fi", "select"}
_LEAD = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "(", "}", ")"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
_RUNNERS = {"npx", "bunx", "pnpx", "uvx", "pipx"}  # `npx tsc` is tsc
_RUN_SUB = {("uv", "run"), ("poetry", "run"), ("pnpm", "exec"), ("npm", "exec"), ("yarn", "exec"), ("bun", "x"),
            ("pipenv", "run"), ("hatch", "run"), ("pdm", "run"), ("bundle", "exec")}
# Programs where the first word after the name says what was done: `git status`, `docker compose`.
_SUB = {"git", "docker", "podman", "npm", "pnpm", "yarn", "bun", "go", "cargo", "kubectl", "helm", "hermes",
        "systemctl", "journalctl", "apt", "apt-get", "dnf", "pacman", "brew", "pip", "pip3", "uv", "poetry", "gh",
        "terraform", "aws", "gcloud", "az", "supabase", "tmux", "snap", "flatpak", "conda", "rustup", "dotnet"}
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_NAME = re.compile(r"^[\w.+@-]{1,32}$")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_AMOUNT = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def _strip_heredocs(script: str) -> str:
    out, end = [], None
    for line in script.split("\n"):
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = _HEREDOC.search(line)
        if m:
            end = m.group(2)
    return "\n".join(out)


def _statements(script: str) -> list[str]:
    """Split on ; && || & and newlines outside quotes; of a pipeline keep only the first command."""
    parts, cur, quote, piped, i, n = [], [], "", False, 0, len(script)

    def cut(next_piped: bool) -> None:
        nonlocal cur, piped
        if not piped:
            parts.append("".join(cur))
        cur, piped = [], next_piped

    while i < n:
        c = script[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                cur.append(script[i : i + 2]); i += 2; continue
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == "\\" and i + 1 < n:
            if script[i + 1] == "\n":
                i += 2; continue  # line continuation
            cur.append(script[i : i + 2]); i += 2; continue
        elif c == "#" and (not cur or cur[-1].isspace()):
            while i < n and script[i] != "\n":
                i += 1
            continue
        elif c in "\n;":
            cut(False); i += 1; continue
        elif c in "&|":
            if script[i : i + 2] in ("&&", "||"):
                cut(False); i += 2; continue
            if c == "|":
                cut(True); i += 2 if script[i : i + 2] == "|&" else 1; continue
            if not (cur and cur[-1] in "<>") and script[i + 1 : i + 2] != ">":  # not 2>&1 or &>
                cut(False); i += 1; continue
        cur.append(c); i += 1
    cut(False)
    return [p.strip() for p in parts if p.strip()]


def _tokens(stmt: str) -> list[str]:
    try:
        return shlex.split(stmt)
    except ValueError:
        return stmt.split()


def _one(stmt: str, depth: int) -> list[tuple[str, str]]:
    """(name, name plus subcommand) for one simple command; several when it is `bash -c '...'`."""
    t = _tokens(stmt)
    while t:
        head = t[0].lstrip("({")
        if not head or head in _LEAD:
            t = t[1:]
        elif head in _SKIP_LINE:
            return []
        elif _ASSIGN.match(head):
            t = t[1:]
        elif os.path.basename(head) in _WRAP:
            t = t[1:]
            while t and (t[0].startswith("-") or _AMOUNT.match(t[0]) or _ASSIGN.match(t[0])):
                t = t[1:]
        else:
            t[0] = head
            break
    if not t:
        return []
    name, rest = os.path.basename(t[0].rstrip(")}")), t[1:]
    args = [a for a in rest if not a.startswith("-")]
    if name in _SHELLS:
        for i, a in enumerate(rest):
            if a.startswith("-") and not a.startswith("--") and "c" in a and i + 1 < len(rest) and depth < 3:
                return _commands(rest[i + 1], depth + 1)
        if args:
            name, args = os.path.basename(args[0]), args[1:]  # `bash run.sh` is run.sh
    if re.fullmatch(r"python[\d.]*|pypy[\d.]*", name) and "-m" in rest and rest.index("-m") + 1 < len(rest):
        mod = rest[rest.index("-m") + 1]
        name, args = mod, [a for a in rest[rest.index("-m") + 2 :] if not a.startswith("-")]
    elif name in _RUNNERS and args:
        name, args = os.path.basename(args[0]), args[1:]
    elif args and (name, args[0]) in _RUN_SUB and len(args) > 1:
        name, args = os.path.basename(args[1]), args[2:]
    if not _NAME.match(name):
        return []
    sub = args[0] if name in _SUB and args and re.fullmatch(r"[a-z][\w-]{0,20}", args[0]) else ""
    return [(name, f"{name} {sub}".strip())]


def _commands(script: str, depth: int = 0) -> list[tuple[str, str]]:
    return [c for stmt in _statements(_strip_heredocs(script or "")) for c in _one(stmt, depth)]


def commands(script: str) -> list[str]:
    """The programs a command line runs, in order, without repeats: `cd x && git status | head` -> ['git status'].

    cd, echo, sleep and the like are left out unless the script has nothing else."""
    found = _commands(script)
    real = [full for name, full in found if name not in _NOISE] or [full for _, full in found]
    return list(dict.fromkeys(real))
