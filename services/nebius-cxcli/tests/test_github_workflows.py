from __future__ import annotations

from pathlib import Path

import yaml

_SETUP_UV_ACTION = "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"
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


def _assert_grafana_api_qualification(steps: list[object], *, condition: str | None = None) -> None:
    step = _named_step(steps, "Qualify pinned Grafana charts, API and PostgreSQL persistence")
    assert "continue-on-error" not in step
    if condition is None:
        assert "if" not in step
    else:
        assert step["if"] == condition
    run = step["run"]
    assert isinstance(run, str)
    assert "set -euo pipefail" in run.splitlines()
    for script in ("verify_grafana_postgres.py", "verify_grafana_persistence.py"):
        assert (
            f"uv run --locked --no-sync --no-python-downloads python scripts/{script}"
            in run.splitlines()
        )

    assert (
        "uv run --locked --no-sync --no-python-downloads python scripts/verify_grafana_api.py"
        in run.splitlines()
    )


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
    assert set(jobs) == {
        "merge-inputs",
        "python-compatibility",
        "verify",
        "wheel-compatibility",
        "result-evidence",
    }
    expected_checkout = {
        "ref": "${{ github.sha }}",
        "persist-credentials": "false",
        "fetch-depth": "0",
    }
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
    assert compatibility_checkout["with"] == expected_checkout
    _assert_pinned_uv(compatibility_steps)
    _uses_step(compatibility_steps, "azure/setup-helm@v5")
    assert "make ci-python" in "\n".join(str(step) for step in compatibility_steps)

    verify = jobs["verify"]
    assert isinstance(verify, dict)
    steps = verify["steps"]
    assert isinstance(steps, list)
    verify_checkout = _uses_step(steps, "actions/checkout@v7")
    assert verify_checkout["with"] == expected_checkout
    _assert_pinned_uv(steps)
    _assert_grafana_api_qualification(steps)
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
    assert set(wheel_compatibility["needs"]) == {"merge-inputs", "verify"}
    wheel_strategy = wheel_compatibility["strategy"]
    assert isinstance(wheel_strategy, dict)
    wheel_matrix = wheel_strategy["matrix"]
    assert isinstance(wheel_matrix, dict)
    assert wheel_matrix["python-version"] == ["3.12", "3.13", "3.14"]
    wheel_steps = wheel_compatibility["steps"]
    assert isinstance(wheel_steps, list)
    wheel_checkout = _uses_step(wheel_steps, "actions/checkout@v7")
    assert wheel_checkout["with"] == expected_checkout
    _assert_pinned_uv(wheel_steps)
    serialized_wheel_steps = "\n".join(str(step) for step in wheel_steps)
    download_step = _uses_step(wheel_steps, "actions/download-artifact@v8")
    assert download_step["with"] == {
        "name": "nebius-cxcli-wheel",
        "path": "services/nebius-cxcli/dist",
    }
    assert "make verify-wheel-cli-dist" in serialized_wheel_steps

    for job in (compatibility, verify, wheel_compatibility):
        assert job["permissions"] == {
            "contents": "read",
            "pull-requests": "read",
            "actions": "read",
        }
        job_steps = job["steps"]
        checkout = _uses_step(job_steps, "actions/checkout@v7")
        result_checkout = _named_step(job_steps, "Check out the authoritative merged result")
        identity = _named_step(job_steps, "Verify exact checkout identity")
        setup = _uses_step(job_steps, "actions/setup-python@v7")
        assert result_checkout["if"] == "needs.merge-inputs.outputs.post_merge == 'true'"
        assert "ci-checkout --workflow nebius-cxcli-ci.yml" in result_checkout["run"]
        assert job_steps.index(checkout) < job_steps.index(result_checkout)
        assert job_steps.index(result_checkout) < job_steps.index(identity) < job_steps.index(setup)

    for job_steps, step_name in (
        (compatibility_steps, "Run full offline suite"),
        (steps, "Verify service with local make contract"),
    ):
        assert _named_step(job_steps, step_name)["env"]["RELEASE_REF"] == (
            "${{ needs.merge-inputs.outputs.checkout_sha }}"
        )

    workflow_text = _workflow_path("nebius-cxcli-ci.yml").read_text(encoding="utf-8")
    assert ".venv/bin/python" not in workflow_text
    assert "python -m pip" not in workflow_text
    assert workflow_text.count("uv run --locked --no-sync --no-python-downloads") == 7

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
    _assert_grafana_api_qualification(steps, condition=release_condition)
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
    assert workflow_text.count("uv run --locked --no-sync --no-python-downloads") == 8


def test_release_history_check_preserves_tip_and_historical_tags(tmp_path):
    import os
    import subprocess

    def git(*args, cwd, input=None, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=input,
            text=True,
            capture_output=True,
            check=check,
            env={
                **os.environ,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            },
        )

    remote = tmp_path / "remote.git"
    git("init", "--bare", "--initial-branch=main", str(remote), cwd=tmp_path)
    tree = git("mktree", cwd=remote, input="").stdout.strip()
    base = git("commit-tree", tree, "-m", "base", cwd=remote).stdout.strip()
    old = git("commit-tree", tree, "-p", base, "-m", "older tag", cwd=remote).stdout.strip()
    tip = git("commit-tree", tree, "-p", old, "-m", "tip tag", cwd=remote).stdout.strip()
    git("update-ref", "refs/heads/main", tip, cwd=remote)
    workflow = _workflow("nebius-cxcli-release.yml")
    steps = workflow["jobs"]["release"]["steps"]
    script = _named_step(steps, "Ensure tag commit belongs to main history")["run"]
    for tag, revision in [("older", old), ("tip", tip)]:
        checkout = tmp_path / tag
        git("clone", "--quiet", remote.as_uri(), str(checkout), cwd=tmp_path)
        result = subprocess.run(
            ["bash", "-c", script.replace("${{ steps.revision.outputs.commit }}", revision)],
            cwd=checkout,
            env={**os.environ, "MAIN_BRANCH": "main"},
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert (
            git("rev-parse", "--verify", revision + "^", cwd=checkout, check=False).returncode == 0
        )
