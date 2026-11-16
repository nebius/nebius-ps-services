"""Merging unactivated source must leave ordinary publication available."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_dormant_source_does_not_enroll_the_repository():
    assert not (ROOT / ".github/ai-automation.json").exists()
    policy = json.loads((ROOT / ".github/merge-policy.json").read_text())
    assert not policy.get("automation_required", False)
    installed = {path.name for path in (ROOT / ".github/workflows").glob("github-ai-*.yml")}
    assert installed == {"github-ai-automation-ci.yml"}


def test_ordinary_publication_has_no_controller_prerequisite():
    for filename in ("skills-merge-pr.yml", "merge-completion.yml"):
        content = (ROOT / ".github/workflows" / filename).read_text()
        assert "AI_AUTOMATION_STATE_" not in content
        assert "environment: github-ai-automation" not in content
        assert "uv run --locked --project github-ai-automation" not in content
    for filename, prefix in (
        ("vpngw-release.yml", "nebius-vpngw-v*"),
        ("nebius-cxcli-release.yml", "nebius-cxcli-v*"),
    ):
        value = yaml.load(
            (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
        )
        assert prefix in value["on"]["push"]["tags"]
        assert "workflow_call" not in value["on"]
        assert "gh release create" in str(value)


def test_prepared_enrollment_stays_outside_installed_workflows():
    deployment = ROOT / "github-ai-automation/deployment"
    enrollment = json.loads((deployment / "ai-automation.json.template").read_text())
    policy = json.loads((deployment / "merge-policy.json.template").read_text())
    assert policy["automation_required"] is True
    for workflow in enrollment["workflows"].values():
        assert (deployment / "workflows" / (workflow + ".template")).is_file()
    for filename in ("vpngw-release.yml", "nebius-cxcli-release.yml"):
        value = yaml.load(
            (deployment / "workflows" / (filename + ".template")).read_text(),
            Loader=yaml.BaseLoader,
        )
        assert set(value["on"]) == {"workflow_call"}
        assert value["permissions"] == {"contents": "read"}
