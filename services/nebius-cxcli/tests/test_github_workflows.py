from __future__ import annotations

from pathlib import Path

import yaml


def _workflow(name: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    workflow_path = repo_root / ".github" / "workflows" / name
    loaded = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_nebius_cxcli_ci_workflow_tracks_platform_modules_and_parses() -> None:
    workflow = _workflow("nebius-cxcli-ci.yml")

    on = workflow["on"]
    assert isinstance(on, dict)
    pull_request = on["pull_request"]
    push = on["push"]
    assert isinstance(pull_request, dict)
    assert isinstance(push, dict)

    pr_paths = pull_request["paths"]
    push_paths = push["paths"]
    assert isinstance(pr_paths, list)
    assert isinstance(push_paths, list)

    expected_paths = {
        "services/nebius-cxcli/**",
        "platform-infra/modules/**",
        ".github/workflows/nebius-cxcli-ci.yml",
        ".github/workflows/nebius-cxcli-release.yml",
    }
    assert expected_paths.issubset(set(pr_paths))
    assert expected_paths.issubset(set(push_paths))

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    verify = jobs["verify"]
    assert isinstance(verify, dict)
    steps = verify["steps"]
    assert isinstance(steps, list)
    serialized_steps = "\n".join(str(step) for step in steps)
    assert "Validate active component sources catalog" in serialized_steps
    assert ".venv/bin/python -m nebius_cxcli validate-sources component_sources.yaml" in serialized_steps


def test_nebius_cxcli_release_workflow_parses() -> None:
    workflow = _workflow("nebius-cxcli-release.yml")

    assert workflow["name"] == "nebius-cxcli-release-publish"
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert "release" in jobs
    release = jobs["release"]
    assert isinstance(release, dict)
    steps = release["steps"]
    assert isinstance(steps, list)
    serialized_steps = "\n".join(str(step) for step in steps)
    assert "Validate active component sources catalog" in serialized_steps
    assert ".venv/bin/python -m nebius_cxcli validate-sources component_sources.yaml" in serialized_steps
