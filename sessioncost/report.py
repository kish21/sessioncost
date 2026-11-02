"""Render the report model as one self-contained HTML page (inline CSS/JS, data embedded as JSON).

The page is the session board (report_board.html). It uses the fonts already on the computer (system-ui, Segoe UI,
Consolas...): no font files to ship or embed."""
from __future__ import annotations

import html
import json
from pathlib import Path

TEMPLATE = Path(__file__).parent / "report_board.html"


def render(a: dict) -> str:
    # "</" inside the embedded JSON would end the <script> block early; "<\/" means the same in JSON.
    data = json.dumps(a, ensure_ascii=False, default=str).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8")
    return page.replace("__TITLE__", html.escape(a["meta"]["title"][:80])).replace("__DATA__", data)
