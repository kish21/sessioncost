"""Render the report model as one self-contained HTML page (inline CSS/JS, data embedded as JSON).

Two designs share the same data: "classic" (report_template.html, the turn list) and "board" (report_board.html,
the session board approved 2026-10-10). Both are kept so they can be compared side by side."""
from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

TEMPLATES = {"classic": Path(__file__).parent / "report_template.html",
             "board": Path(__file__).parent / "report_board.html"}
TEMPLATE = TEMPLATES["classic"]
FONTS = Path(__file__).parent / "fonts"   # the report's fonts (SIL Open Font License, fonts/OFL.txt), embedded so a report loads nothing


def fonts_css(design: str) -> str:
    """The design's @font-face rules with each font file embedded as a data URL: the page fetches nothing."""
    css = (FONTS / ("board.css" if design == "board" else "classic.css")).read_text(encoding="utf-8")
    return re.sub(r"url\(FONT:([^)]+)\)", lambda m: "url(data:font/woff2;base64,"
                  + base64.b64encode((FONTS / m.group(1)).read_bytes()).decode() + ")", css)


def render(a: dict, design: str = "classic") -> str:
    # "</" inside the embedded JSON would end the <script> block early; "<\/" means the same in JSON.
    data = json.dumps(a, ensure_ascii=False, default=str).replace("</", "<\/")
    page = TEMPLATES[design].read_text(encoding="utf-8")
    page = page.replace("__FONTS__", fonts_css(design))
    return page.replace("__TITLE__", html.escape(a["meta"]["title"][:80])).replace("__DATA__", data)
