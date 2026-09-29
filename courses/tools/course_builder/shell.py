"""Shared static page framing and navigation."""

from __future__ import annotations
import html
from .config import CATALOG_ENTRIES, GUIDE_TITLE, LICENSE_PATH, catalog_destination


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
    if course_name not in CATALOG_ENTRIES:
        raise ValueError(f"unknown catalog resource: {course_name}")
    prefix = "" if course_name == "lab-guide" else "../"
    items = []
    for name in CATALOG_ENTRIES:
        title = html.escape(
            GUIDE_TITLE if name == "lab-guide" else metadata[name]["title"]
        )
        if name == course_name:
            kind = "guide" if name == "lab-guide" else "course"
            item = f'<span aria-current="page" data-{kind}="{name}">{title}<span class="current-label">Current {kind}</span></span>'
        else:
            item = f'<a href="{prefix}{catalog_destination(name)}">{title}</a>'
        items.append(f"<li>{item}</li>")
    return f"""<div class="catalog-navigation">
<a class="catalog-home" href="{prefix}index.html">← Course catalog</a>
<details class="course-switcher"><summary>Courses and guide</summary>
<ul>{"".join(items)}</ul></details></div>"""


def page_head(title: str, css: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><link rel="icon" href="data:,"><style>{css}</style></head>"""
