from __future__ import annotations

from pathlib import Path

import yaml

_SETUP_UV_ACTION = "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
_SETUP_UV_INPUTS = {
    "version": "0.12.9",
    "enable-cache": "true",
    "cache-dependency-glob": "services/nebius-cxcli/uv.lock",
}


def _workflow_path(name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".github" / "workflows" / name


def _workflow(name: str) -> dict[str, object]:
    workflow_path = _workflow_path(name)
    loaded = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def _service_file(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")


def _named_step(steps: list[object], name: str) -> dict[str, object]:
    step = next(step for step in steps if isinstance(step, dict) and step.get("name") == name)
    assert isinstance(step, dict)
    return step


def _uses_step(steps: list[object], action: str) -> dict[str, object]:
    step = next(step for step in steps if isinstance(step, dict) and step.get("uses") == action)
    assert isinstance(step, dict)
    return step


def _assert_pinned_uv(steps: list[object], *, condition: str | None = None) -> None:
    setup_python = _uses_step(steps, "actions/setup-python@v7")
    setup_uv = _uses_step(steps, _SETUP_UV_ACTION)
    assert setup_uv["with"] == _SETUP_UV_INPUTS
    if condition is None:
        assert "if" not in setup_uv
    else:
        assert setup_uv["if"] == condition
    assert steps.index(setup_python) < steps.index(setup_uv)


def test_nebius_cxcli_ci_workflow_tracks_platform_modules_and_parses() -> None:
    workflow = _workflow("nebius-cxcli-ci.yml")

    assert workflow["permissions"] == {"contents": "read"}

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
        ".github/helm-chart-publish.json",
        "services/nebius-cxcli/**",
        "services/nccl-test/**",
        "helm-charts/nccl-test/**",
        "platform-infra/modules/**",
        ".github/workflows/nebius-cxcli-ci.yml",
        ".github/workflows/nebius-cxcli-release.yml",
    }
    assert set(pr_paths) == expected_paths
    assert set(push_paths) == expected_paths
    assert all("soperator" not in path for path in pr_paths)
    assert all("soperator" not in path for path in push_paths)

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"python-compatibility", "verify", "wheel-compatibility"}
    compatibility = jobs["python-compatibility"]
    assert isinstance(compatibility, dict)
    strategy = compatibility["strategy"]
    assert isinstance(strategy, dict)
    matrix = strategy["matrix"]
    assert isinstance(matrix, dict)
    assert matrix["python-version"] == ["3.12", "3.13", "3.14"]
    compatibility_steps = compatibility["steps"]
    assert isinstance(compatibility_steps, list)
    compatibility_checkout = _uses_step(compatibility_steps, "actions/checkout@v7")
    assert compatibility_checkout["with"] == {"fetch-depth": "0"}
    _assert_pinned_uv(compatibility_steps)
    _uses_step(compatibility_steps, "azure/setup-helm@v5")
    assert "make ci-python" in "\n".join(str(step) for step in compatibility_steps)

    verify = jobs["verify"]
    assert isinstance(verify, dict)
    steps = verify["steps"]
    assert isinstance(steps, list)
    verify_checkout = _uses_step(steps, "actions/checkout@v7")
    assert verify_checkout["with"] == {"fetch-depth": "0"}
    _assert_pinned_uv(steps)
    _uses_step(steps, "azure/setup-helm@v5")
    serialized_steps = "\n".join(str(step) for step in steps)
    assert "Validate active component sources catalog" in serialized_steps
    assert "python -m nebius_cxcli validate-sources component_sources.yaml" in serialized_steps
    assert "Verify bundled component sources are packaged in wheel" in serialized_steps
    assert "python -m nebius_cxcli.release_catalog verify-wheel-bundle" in serialized_steps
    assert "make ci-quality verify-wheel-cli" in serialized_steps
    upload_step = _uses_step(steps, "actions/upload-artifact@v7")
    assert upload_step["with"] == {
        "name": "nebius-cxcli-wheel",
        "path": "services/nebius-cxcli/dist/*.whl",
        "if-no-files-found": "error",
    }
    verify_env = verify.get("env")
    assert isinstance(verify_env, dict)
    assert "pull_request.base.sha" in str(verify_env.get("DIFF_BASE"))
    assert "github.event.before" in str(verify_env.get("DIFF_BASE"))
    validate_step = _named_step(steps, "Validate active component sources catalog")
    env = validate_step.get("env")
    assert isinstance(env, dict)
    assert env.get("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE") == "local"

    wheel_compatibility = jobs["wheel-compatibility"]
    assert isinstance(wheel_compatibility, dict)
    assert wheel_compatibility["needs"] == "verify"
    wheel_strategy = wheel_compatibility["strategy"]
    assert isinstance(wheel_strategy, dict)
    wheel_matrix = wheel_strategy["matrix"]
    assert isinstance(wheel_matrix, dict)
    assert wheel_matrix["python-version"] == ["3.12", "3.13", "3.14"]
    wheel_steps = wheel_compatibility["steps"]
    assert isinstance(wheel_steps, list)
    wheel_checkout = _uses_step(wheel_steps, "actions/checkout@v7")
    assert wheel_checkout["with"] == {"fetch-depth": "0"}
    _assert_pinned_uv(wheel_steps)
    serialized_wheel_steps = "\n".join(str(step) for step in wheel_steps)
    download_step = _uses_step(wheel_steps, "actions/download-artifact@v7")
    assert download_step["with"] == {
        "name": "nebius-cxcli-wheel",
        "path": "services/nebius-cxcli/dist",
    }
    assert "make verify-wheel-cli-dist" in serialized_wheel_steps

    workflow_text = _workflow_path("nebius-cxcli-ci.yml").read_text(encoding="utf-8")
    assert ".venv/bin/python" not in workflow_text
    assert "python -m pip" not in workflow_text
    assert workflow_text.count("uv run --locked --no-sync --no-python-downloads") == 4

    makefile = _service_file("Makefile")
    assert "$(MAKE) -j2 check verify-wheel-cli" in makefile
    assert "scripts/verify_wheel_cli.py" in makefile
    assert "tests/fixtures/cli_contract.json" in makefile


def test_nebius_cxcli_release_workflow_parses() -> None:
    workflow = _workflow("nebius-cxcli-release.yml")

    assert workflow["name"] == "nebius-cxcli-release-publish"
    assert workflow["permissions"] == {"contents": "write"}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"release"}
    release = jobs["release"]
    assert isinstance(release, dict)
    steps = release["steps"]
    assert isinstance(steps, list)
    checkout_step = _uses_step(steps, "actions/checkout@v7")
    assert checkout_step["with"] == {"fetch-depth": "0"}
    release_condition = "steps.state.outputs.release_exists == 'false'"
    _assert_pinned_uv(steps, condition=release_condition)
    helm_step = _uses_step(steps, "azure/setup-helm@v5")
    assert helm_step["if"] == release_condition
    serialized_steps = "\n".join(str(step) for step in steps)
    assert "Validate active component sources catalog" in serialized_steps
    assert "python -m nebius_cxcli validate-sources component_sources.yaml" in serialized_steps
    assert "python -m nebius_cxcli.release_catalog verify-wheel \\" in serialized_steps
    validate_step = _named_step(steps, "Validate active component sources catalog")
    env = validate_step.get("env")
    assert isinstance(env, dict)
    assert env.get("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE") == "portable"
    assert "make ci-quality verify-wheel-cli" in serialized_steps
    assert "make all" not in serialized_steps
    verify_step = _named_step(steps, "Verify service with local make contract")
    verify_env = verify_step["env"]
    assert isinstance(verify_env, dict)
    assert verify_env["DIFF_BASE"] == "${{ steps.revision.outputs.commit }}^"
    manifest_upload = _uses_step(steps, "actions/upload-artifact@v7")
    manifest_with = manifest_upload["with"]
    assert isinstance(manifest_with, dict)
    assert manifest_with["if-no-files-found"] == "error"

    workflow_text = _workflow_path("nebius-cxcli-release.yml").read_text(encoding="utf-8")
    assert ".venv/bin/python" not in workflow_text
    assert "python -m pip" not in workflow_text
    assert workflow_text.count("uv run --locked --no-sync --no-python-downloads") == 5
