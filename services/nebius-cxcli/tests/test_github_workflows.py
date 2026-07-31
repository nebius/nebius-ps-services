from __future__ import annotations

from pathlib import Path

import yaml


def _workflow_path(name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".github" / "workflows" / name


def _workflow(name: str) -> dict[str, object]:
    workflow_path = _workflow_path(name)
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
    assert (
        ".venv/bin/python -m nebius_cxcli validate-sources component_sources.yaml"
        in serialized_steps
    )
    assert "Verify bundled component sources are packaged in wheel" in serialized_steps
    assert (
        ".venv/bin/python -m nebius_cxcli.release_catalog verify-wheel-bundle" in serialized_steps
    )
    validate_step = next(
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Validate active component sources catalog"
    )
    assert isinstance(validate_step, dict)
    env = validate_step.get("env")
    assert isinstance(env, dict)
    assert env.get("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE") == "local"


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
    assert (
        ".venv/bin/python -m nebius_cxcli validate-sources component_sources.yaml"
        in serialized_steps
    )
    assert ".venv/bin/python -m nebius_cxcli.release_catalog verify-wheel \\" in serialized_steps
    validate_step = next(
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Validate active component sources catalog"
    )
    assert isinstance(validate_step, dict)
    env = validate_step.get("env")
    assert isinstance(env, dict)
    assert env.get("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE") == "portable"


def test_soperator_upstream_verifier_is_read_only_and_manual_sync_only() -> None:
    workflow_path = _workflow_path("soperator-upstream-verifier.yml")
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = _workflow("soperator-upstream-verifier.yml")

    assert workflow["name"] == "soperator-upstream-verifier"
    assert workflow["permissions"] == {"contents": "read"}

    on = workflow["on"]
    assert isinstance(on, dict)
    pull_request = on["pull_request"]
    push = on["push"]
    workflow_dispatch = on["workflow_dispatch"]
    assert isinstance(pull_request, dict)
    assert isinstance(push, dict)
    assert isinstance(workflow_dispatch, dict)
    dispatch_inputs = workflow_dispatch["inputs"]
    assert isinstance(dispatch_inputs, dict)
    assert set(dispatch_inputs) == {
        "accept_review_baseline",
        "accept_image_baseline",
        "live_upgrade_evidence_run_id",
        "live_upgrade_evidence_artifact",
    }
    for input_name in ("accept_review_baseline", "accept_image_baseline"):
        input_contract = dispatch_inputs[input_name]
        assert isinstance(input_contract, dict)
        assert input_contract["required"] == "false"
        assert input_contract["default"] == "false"
        assert input_contract["type"] == "boolean"
    evidence_run_input = dispatch_inputs["live_upgrade_evidence_run_id"]
    assert evidence_run_input["required"] == "false"
    assert evidence_run_input["default"] == ""
    assert evidence_run_input["type"] == "string"
    evidence_artifact_input = dispatch_inputs["live_upgrade_evidence_artifact"]
    assert evidence_artifact_input["required"] == "false"
    assert evidence_artifact_input["default"] == "soperator-disposable-upgrade-evidence"
    assert evidence_artifact_input["type"] == "string"
    expected_paths = {
        ".github/workflows/soperator-upstream-verifier.yml",
        "helm-charts/soperator/**",
        "helm-charts/soperator-activechecks/**",
        "helm-charts/soperator-backup-config/**",
        "helm-charts/soperator-checks/**",
        "helm-charts/soperator-dcgm-exporter/**",
        "helm-charts/soperator-notifier/**",
    }
    assert expected_paths.issubset(set(pull_request["paths"]))
    assert expected_paths.issubset(set(push["paths"]))

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {
        "soperator-upstream-imports-verify",
        "soperator-upstream-release-check",
    }

    release_check = jobs["soperator-upstream-release-check"]
    assert isinstance(release_check, dict)
    assert release_check["permissions"] == {
        "actions": "read",
        "attestations": "read",
        "contents": "read",
    }
    steps = release_check["steps"]
    assert isinstance(steps, list)
    serialized_steps = "\n".join(str(step) for step in steps)
    assert "verify-upstream-soperator-sync.sh --check-latest" in serialized_steps
    assert "helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh" in serialized_steps
    assert "--latest" in serialized_steps
    assert "--sync" in serialized_steps
    assert "--report" in serialized_steps
    assert "--ci-preview-no-branch" in serialized_steps
    assert "ACCEPT_REVIEW_BASELINE" in serialized_steps
    assert "ACCEPT_IMAGE_BASELINE" in serialized_steps
    assert "--accept-review-baseline" in serialized_steps
    assert "--accept-image-baseline" in serialized_steps
    assert "actions/download-artifact@v8" in serialized_steps
    assert "github-token" in serialized_steps
    assert "run-id" in serialized_steps
    assert "gh api" in serialized_steps
    assert "gh attestation verify" in serialized_steps
    assert "--signer-workflow" in serialized_steps
    assert "--deny-self-hosted-runners" in serialized_steps
    assert "head_repository" in serialized_steps
    assert "producer run_attempt differs" in serialized_steps
    assert "producer head_sha differs" in serialized_steps
    assert "source event differs from the locked producer event" in serialized_steps
    assert "source ref differs from the locked producer ref" in serialized_steps
    assert "soperator-disposable-upgrade-evidence.json" in serialized_steps
    assert "cannot produce its own upgrade evidence" in serialized_steps
    assert "File.file?(producer[\"workflow\"])" in serialized_steps
    assert "--live-upgrade-evidence" in serialized_steps
    assert "helm-unittest/helm-unittest --version v1.1.1" in serialized_steps
    assert "v3.15.4" in serialized_steps
    assert "missing_review_baseline" in serialized_steps
    assert "missing_image_baseline" in serialized_steps
    assert "GITHUB_STEP_SUMMARY" in serialized_steps
    assert "Manual Soperator sync needed" in serialized_steps
    assert 'GITHUB_EVENT_NAME}" == "schedule"' in serialized_steps
    assert "Manual dispatch preview completed without failing the run" in serialized_steps
    assert "exit 1" in serialized_steps

    prohibited_fragments = {
        "contents: write",
        "pull-requests: write",
        "automation/soperator-upstream-sync",
        ".github/workflows/soperator-upstream-sync.yml",
        "gh pr",
        "git push",
        "git commit",
        "git add",
        "git switch",
        "sync-soperator-",
    }
    for fragment in prohibited_fragments:
        assert fragment not in workflow_text
