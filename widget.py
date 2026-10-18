"""Install the Ink TUI rails widget into $HERMES_HOME/tui-widgets."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .ledger import load_events, resolve_db
from .rails import widget_data

TEMPLATE = Path(__file__).resolve().parent / "tui" / "rails.mjs"
WIDGET_NAME = "rails.mjs"


def widgets_dir() -> Path:
    home = os.environ.get("HERMES_HOME", "").strip()
    root = Path(home) if home else Path.home() / ".hermes"
    return root / "tui-widgets"


def widget_path() -> Path:
    return widgets_dir() / WIDGET_NAME


_GLYPH = {"open": "◉", "spawn": "◆", "note": "•", "red": "○", "fix": "○", "fail": "✕", "pass": "●", "done": "◉"}
# One hue per meaning, mid-tone so it reads on dark and light terminals.
_STYLE = {
    "open": "#E0B040",
    "spawn": "#5AA9E6",
    "note": "#9A8F78",
    "red": "#F0883E",
    "fix": "#F0883E",
    "fail": "bold #F05050",
    "pass": "#5FB760",
    "done": "bold #5FB760",
}
_RAIL = "#7A6A4A"
_BORDER = "#CD7F32"
_STATUS = {"complete": ("✔", "done"), "blocked": ("✕", "fail"), "open": ("◌", "red")}
# Who keeps the cell (and the label) when events collide; earlier wins.
_PRIORITY = ("fail", "done", "pass", "open", "red", "fix", "spawn", "note")
_LEGEND = (
    ("open", "open"),
    ("spawn", "spawn"),
    ("note", "note"),
    ("red", "tdd red"),
    ("fail", "fail"),
    ("pass", "pass"),
)


def _rank(k: str) -> int:
    return _PRIORITY.index(k) if k in _PRIORITY else len(_PRIORITY)


def _cell(x: float, width: int) -> int:
    return max(0, min(width - 1, round(x * (width - 1))))


def place_labels(events: list[dict], width: int) -> list[tuple[int, dict]]:
    """(start column, event) per label that fits. A clashing label slides off-centre before it is dropped."""
    taken = [False] * width
    placed: list[tuple[int, dict]] = []
    for e in sorted(events, key=lambda e: _rank(e["k"])):
        lab, x = e["label"], _cell(e["x"], width)
        if len(lab) > width:
            continue
        if any(o["label"] == lab and abs(_cell(o["x"], width) - x) <= len(lab) + 1 for _, o in placed):
            continue
        centre = max(0, min(width - len(lab), x - len(lab) // 2))
        for off in sorted(range(-len(lab) - 1, len(lab) + 2), key=abs):
            start = centre + off
            if start < 0 or start + len(lab) > width:
                continue
            if any(taken[max(0, start - 1) : start + len(lab) + 1]):
                continue
            taken[start : start + len(lab)] = [True] * len(lab)
            placed.append((start, e))
            break
    return sorted(placed, key=lambda p: p[0])


def install_widget(
    db: str | None = None,
    task_id: str | None = None,
    *,
    auto_open: bool = False,
) -> Path:
    data = widget_data(load_events(resolve_db(db), task_id))
    src = TEMPLATE.read_text()
    src = src.replace("__VIBELINE_DATA__", json.dumps(data, separators=(",", ":")))
    src = src.replace("__VIBELINE_AUTO_OPEN__", "true" if auto_open else "false")
    dest = widget_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src)
    return dest


def print_panel(data: dict, *, width: int = 88) -> None:
    """Classic-CLI panel. Ink TUI uses the widget; do not dump markdown."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
    except Exception:
        print(f"{data.get('task_id')}  {data.get('status')}  {data.get('dur')}")
        return

    console = Console(highlight=False, soft_wrap=False)
    term = console.size.width or 80
    panel_w = min(width + 4, term)
    content_w = max(36, panel_w - 4)
    name_w, rail_w = 10, max(16, content_w - 11)
    pad = " " * (name_w + 1)
    body = Text()

    status = data.get("status") or ""
    mark, tone = _STATUS.get(status, ("·", "note"))
    body.append(f"{mark} {status}", style=_STYLE[tone])
    body.append(f"  {data.get('dur')}\n", style="dim")
    acs = data.get("ac") or []
    if acs:
        body.append(f"AC {sum(1 for a in acs if a.get('met'))}/{len(acs)}", style="bold")
        for a in acs:
            met = a.get("met")
            body.append("   ")
            body.append(("✔ " if met else "○ ") + a.get("id", ""), style=_STYLE["pass"] if met else "dim")
    else:
        body.append("no AC", style="dim")
    body.append("\n\n")

    for ln in data.get("lanes") or []:
        events = ln.get("rail") or []
        blocked = ln.get("blocked")
        tag = (ln["id"] + ("!" if blocked else ""))[:name_w].ljust(name_w)
        body.append(tag + " ", style=_STYLE["fail"] if blocked else "bold")
        col = 0
        for start, e in place_labels(events, rail_w):
            body.append(" " * (start - col))
            body.append(e["label"], style=_STYLE.get(e["k"], "dim"))
            col = start + len(e["label"])
        body.append("\n" + pad)

        # The rail only exists between the lane's first and last event.
        cells: list[dict | None] = [None] * rail_w
        for e in events:
            x = _cell(e["x"], rail_w)
            if cells[x] is None or _rank(e["k"]) < _rank(cells[x]["k"]):
                cells[x] = e
        xs = [i for i, e in enumerate(cells) if e]
        for i, e in enumerate(cells):
            if e:
                body.append(_GLYPH.get(e["k"], "•"), style=_STYLE.get(e["k"], "dim"))
            elif xs and xs[0] < i < xs[-1]:
                body.append("─", style=_STYLE["fail"] if blocked else _RAIL)
            else:
                body.append(" ")
        body.append("\n")

    start_clock, _, end_clock = (data.get("clock") or "").partition(" → ")
    if start_clock:
        body.append(pad + start_clock + end_clock.rjust(rail_w - len(start_clock)) + "\n", style="dim")
    body.append("\n")
    for i, (k, text) in enumerate(_LEGEND):
        body.append("  " if i else "")
        body.append(_GLYPH[k], style=_STYLE[k])
        body.append(" " + text, style="dim")

    title = Text(data.get("task_id") or "rails", style=f"bold {_STYLE['open']}")
    console.print(Panel(body, title=title, border_style=_BORDER, width=panel_w, padding=(0, 1)))
