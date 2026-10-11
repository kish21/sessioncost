"""Render the report model as one self-contained HTML page (inline CSS/JS, data embedded as JSON).

Two designs share the same data: "classic" (report_template.html, the turn list) and "board" (report_board.html,
the session board approved 2026-10-10). Both are kept so they can be compared side by side."""
from __future__ import annotations

import html
import json
from pathlib import Path

TEMPLATES = {"classic": Path(__file__).parent / "report_template.html",
             "board": Path(__file__).parent / "report_board.html"}
TEMPLATE = TEMPLATES["classic"]


def render(a: dict, design: str = "classic") -> str:
    # "</" inside the embedded JSON would end the <script> block early; "<\/" means the same in JSON.
    data = json.dumps(a, ensure_ascii=False, default=str).replace("</", "<\/")
    page = TEMPLATES[design].read_text(encoding="utf-8")
    return page.replace("__TITLE__", html.escape(a["meta"]["title"][:80])).replace("__DATA__", data)
