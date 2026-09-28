"""Small ordinary links to external course downloads; never inline payloads."""

from __future__ import annotations

import html
from pathlib import Path


def dashboard_pointer() -> str:
    return '<p>Find this dashboard in the <a href="#course-downloads">course results ZIP</a>.</p>'


def course_downloads(course: Path) -> str:
    slug = html.escape(course.name, quote=True)
    return (
        '<div id="course-downloads"><h3>Download results</h3>'
        '<p><strong>Download all lab results:</strong><br>\n'
        f'<a class="course-download" download="{slug}-lab-results.zip" '
        f'data-asset="lab-results" href="reference/{slug}-lab-results.zip">'
        "Grafana dashboards, Small and Large results</a></p>"
        '<p><strong>Setup the lab environment:</strong><br>\n'
        '<a href="../lab-guide.html">Lab setup guide</a></p></div>'
    )


def lab_results_pointer(course: Path, lab: str) -> str:
    if not any(
        (course / "reference/lab-results" / lab / profile / "manifest.json").is_file()
        for profile in ("small", "large")
    ):
        return ""
    return '<p class="lab-results">Small and Large results are in the <a href="#course-downloads">course results ZIP</a>.</p>'
