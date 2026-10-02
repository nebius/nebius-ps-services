"""Make course authoring modules available to repository tests."""

import sys
from pathlib import Path
import re

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


@pytest.fixture
def authored_figure_placeholders():
    """Isolate narrative tests; publication tests verify the real figure assets."""
    def registrations(lesson):
        return {
            field: [
                {"title": title, "path": path, "markup": "<figure></figure>"}
                for title, path in re.findall(r"^!\[([^]]+)\]\(([^)]+)\)$", value, re.M)
            ]
            for field, value in lesson.items()
            if field != "title"
        }

    return registrations
