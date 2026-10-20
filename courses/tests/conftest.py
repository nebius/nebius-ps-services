"""Make course authoring modules available to repository tests."""

import sys
from pathlib import Path
import re

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


@pytest.fixture
def monitoring_settings():
    from course_bootstrap.handoff import render

    def configured(workspace="learner"):
        return render(workspace, {
            "cluster_identity": {"kubernetes_uid": "fixture-cluster"},
            "vmagent_uid": "fixture-agent", "vmsingle_uid": "fixture-metrics",
            "service_uid": "fixture-service", "pushgateway_service_uid": "fixture-push",
            "metrics_url": "http://127.0.0.1:8428", "pushgateway_url": "http://127.0.0.1:9091",
        })

    return configured


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
