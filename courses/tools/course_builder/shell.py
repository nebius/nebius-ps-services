"""Shared static page framing and navigation."""

from __future__ import annotations
import html
from .config import COURSES, LICENSE_PATH


def license_footer() -> str:
    """Keep the complete license with each independently saved HTML document."""
    license_text = html.escape(LICENSE_PATH.read_text(encoding="utf-8"))
    return f"""<footer class="license-footer">
<small>© 2026 Nebius B.V. Provided free of charge for learning and education.
Licensed under <a href="#license">Apache License 2.0</a>.<br>
Third-party materials retain their respective licenses.</small>
<details id="license"><summary>License and notices</summary>
<pre class="license-text" tabindex="0">{license_text}</pre></details>
</footer>"""


def course_switcher(course_name: str, metadata: dict[str, dict]) -> str:
    items = []
    for name in COURSES:
        title = html.escape(metadata[name]["title"])
        if name == course_name:
            item = f'<span aria-current="page" data-course="{name}">{title}<span class="current-label">Current course</span></span>'
        else:
            item = f'<a href="../{name}/index.html">{title}</a>'
        items.append(f"<li>{item}</li>")
    return f"""<div class="catalog-navigation">
<a class="catalog-home" href="../index.html">← All courses</a>
<details class="course-switcher"><summary>Switch course</summary>
<ul>{"".join(items)}</ul></details></div>"""


def page_head(title: str, css: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><link rel="icon" href="data:,"><style>{css}</style></head>"""
