from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh"
LOCK = REPO_ROOT / "helm-charts/soperator/upstream-soperator.lock.yaml"
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
EVIDENCE_PRODUCER_WORKFLOW = ".github/workflows/nebius-cxcli-ci.yml"
SOURCE_UPSTREAM_RELEASE = "1.22.3"
SOURCE_CHART_VERSION = "1.22.3"
SOURCE_TARBALL_SHA256 = (
    "41504a06e0867abfc6d626f05e31c6304e8f6ead7383739f6717c4f7145293fc"
)
SOURCE_CONTRACT_FINGERPRINT = (
    "9253ffb3f7893bdb1fa52593773a56838cf3ffc5a1a2df63a816bf0827ee54d4"
)
SOURCE_RUNTIME_IMAGES_SHA256 = (
    "c3151dc5430e1d12ceb1685f42cb497ecf1960cee5d84d5e46bd1c827b43fe3c"
)
SOURCE_PROFILE_FILE = (
    "services/nebius-cxcli/src/nebius_cxcli/soperator_migration_profiles.yaml"
)


def _write_live_upgrade_evidence(
    path: Path,
    *,
    target_release: str,
    producer_workflow: str = EVIDENCE_PRODUCER_WORKFLOW,
    target_chart_version: str | None = None,
) -> None:
    completed_at = datetime.now(UTC) - timedelta(minutes=5)
    expires_at = completed_at + timedelta(days=7)
    path.write_text(
        json.dumps(
            {
                "schema": "nebius-cxcli-soperator-disposable-upgrade-evidence/v1",
                "status": "passed",
                "disposable": True,
                "producer": {
                    "repository": "nebius/nebius-ps-services",
                    "workflow": producer_workflow,
                    "run_id": 123,
                    "run_attempt": 1,
                    "head_sha": "d" * 40,
                },
                "source": {
                    "upstream_release": SOURCE_UPSTREAM_RELEASE,
                    "chart_version": SOURCE_CHART_VERSION,
                    "tarball_sha256": SOURCE_TARBALL_SHA256,
                    "contract_fingerprint": SOURCE_CONTRACT_FINGERPRINT,
                    "runtime_images_sha256": SOURCE_RUNTIME_IMAGES_SHA256,
                },
                "target": {
                    "upstream_release": target_release,
                    "chart_version": target_chart_version or f"{target_release}-ps.1",
                    "upstream_commit": "7f1c4e817cab67e7b5d563bc11db2bff8c661189",
                    "candidate_manifest_sha256": "e" * 64,
                },
                "campaign": {
                    "schema": "nebius-cxcli-ext-soperator-upgrade-campaign/v4",
                    "status": "complete",
                    "pending_phase": "none",
                    "id": "campaign-test",
                    "fingerprint": "a" * 64,
                },
                "acceptance": {
                    "running_jobs": {
                        "preserved": True,
                        "same_job_ids": True,
                        "same_allocations": True,
                        "restarts_zero": True,
                        "exit_code_zero": True,
                    },
                    "tui_actions": {"all_terminal": True, "indeterminate": 0},
                    "ssh": {
                        "forced_disconnects": 0,
                        "login_endpoint_continuous": True,
                        "voluntary_handoff": True,
                    },
                    "controller": {
                        "slurmctld_processes": 1,
                        "slurmctld_hosts": 1,
                        "controller_replicas": 1,
                        "bridge_resources_absent": True,
                    },
                    "jail_gpu": {"passed": True},
                },
                "artifacts": {
                    "report_sha256": "b" * 64,
                    "checkpoint_sha256": "c" * 64,
                },
                "completed_at": completed_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )


def _run_sourced_script_function(
    *arguments: str,
    env_overrides: dict[str, str] | None = None,
    script_path: Path = SCRIPT,
) -> subprocess.CompletedProcess[str]:
    command = """
source <(sed '$d' "$1")
shift
"$@"
"""
    env = {**os.environ, "NO_COLOR": "1"}
    env.update(env_overrides or {})
    return subprocess.run(
        ["bash", "-c", command, "bash", str(script_path), *arguments],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _write_lock(
    path: Path,
    *,
    release: str,
    producer_workflow: str | None = None,
) -> None:
    workflow_value = "null" if producer_workflow is None else json.dumps(producer_workflow)
    path.write_text(
        "\n".join(
            [
                'repository: "https://github.com/nebius/soperator"',
                f'release: "{release}"',
                f'tag: "{release}"',
                'commit: "deadbeef"',
                "conformance:",
                "  live_upgrade_evidence:",
                "    producer_repository: nebius/nebius-ps-services",
                f"    producer_workflow: {workflow_value}",
                "    trusted_ref: refs/heads/main",
                "    trusted_event: workflow_dispatch",
                "    artifact_name: soperator-disposable-upgrade-evidence",
                "    required_source:",
                f"      upstream_release: {SOURCE_UPSTREAM_RELEASE}",
                f"      chart_version: {SOURCE_CHART_VERSION}",
                f"      tarball_sha256: {SOURCE_TARBALL_SHA256}",
                f"      contract_fingerprint: {SOURCE_CONTRACT_FINGERPRINT}",
                f"      runtime_images_sha256: {SOURCE_RUNTIME_IMAGES_SHA256}",
                f"      profile_file: {SOURCE_PROFILE_FILE}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_fake_curl(
    path: Path,
    *,
    page_one_releases: list[dict[str, object]],
    page_two_releases: list[dict[str, object]] | None = None,
) -> None:
    page_one_json = json.dumps(page_one_releases)
    page_two_json = json.dumps(page_two_releases or [])
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
url="${{@: -1}}"
case "${{url}}" in
  *"/releases?per_page=100&page=1")
    cat <<'JSON'
{page_one_json}
JSON
    ;;
  *"/releases?per_page=100&page=2")
    cat <<'JSON'
{page_two_json}
JSON
    ;;
  *"/releases?per_page=100&page=3")
    printf '[]\\n'
    ;;
  *)
    printf 'unexpected curl URL: %s\\n' "${{url}}" >&2
    exit 22
    ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_check_latest(tmp_path: Path, *, locked_release: str) -> subprocess.CompletedProcess[str]:
    return _run_check_latest_with_release_pages(
        tmp_path,
        locked_release=locked_release,
        page_one_releases=[
            {"tag_name": "3.0.7", "draft": False, "prerelease": False},
            {"tag_name": "4.0.2", "draft": False, "prerelease": False},
        ],
    )


def _run_check_latest_with_release_pages(
    tmp_path: Path,
    *,
    locked_release: str,
    page_one_releases: list[dict[str, object]],
    page_two_releases: list[dict[str, object]] | None = None,
) -> subprocess.CompletedProcess[str]:
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_lock(lock_file, release=locked_release)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_curl(
        bin_dir / "curl",
        page_one_releases=page_one_releases,
        page_two_releases=page_two_releases,
    )
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return subprocess.run(
        [str(SCRIPT), "--lock-file", str(lock_file), "--check-latest"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_check_latest_uses_highest_semver_release_not_latest_badge(tmp_path: Path) -> None:
    result = _run_check_latest(tmp_path, locked_release="4.0.2")

    assert result.returncode == 0, result.stderr
    assert "Pinned Soperator release '4.0.2' is the highest GitHub SemVer release." in (
        result.stdout
    )


def test_check_latest_reports_lock_behind_highest_semver_release(tmp_path: Path) -> None:
    result = _run_check_latest(tmp_path, locked_release="3.0.7")

    assert result.returncode == 1
    assert "Highest Soperator release is '4.0.2', but lock is pinned to '3.0.7'." in (
        result.stderr
    )


def test_check_latest_continues_after_prerelease_only_page(tmp_path: Path) -> None:
    result = _run_check_latest_with_release_pages(
        tmp_path,
        locked_release="4.0.2",
        page_one_releases=[
            {"tag_name": "5.0.0-rc.1", "draft": False, "prerelease": True},
            {"tag_name": "6.0.0", "draft": True, "prerelease": False},
        ],
        page_two_releases=[
            {"tag_name": "4.0.2", "draft": False, "prerelease": False},
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "Pinned Soperator release '4.0.2' is the highest GitHub SemVer release." in (
        result.stdout
    )


def test_real_pin_promotion_requires_disposable_live_evidence(tmp_path: Path) -> None:
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_lock(lock_file, release="4.0.2")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_curl(
        bin_dir / "curl",
        page_one_releases=[
            {"tag_name": "4.1.3", "draft": False, "prerelease": False},
        ],
    )
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(SCRIPT), "--lock-file", str(lock_file), "--latest", "--sync"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "require --live-upgrade-evidence PATH" in result.stderr
    assert "dirty working tree" not in result.stderr


def test_ci_preview_no_branch_requires_sync() -> None:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [str(SCRIPT), "--ci-preview-no-branch"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "--ci-preview-no-branch can only be used with --sync in disposable CI previews."
        in result.stderr
    )


def test_ci_preview_keeps_checkout_unchanged_when_staged_candidate_changes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    chart = repository / "helm-charts/soperator"
    (chart / "scripts").mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: soperator\nversion: 4.0.2-ps.4\n",
        encoding="utf-8",
    )
    values_file = chart / "values.yaml"
    values_file.write_text("candidate: original\n", encoding="utf-8")
    (chart / "upstream-soperator.lock.yaml").write_text(
        "\n".join(
            [
                'repository: "unused"',
                'release: "4.0.2"',
                'tag: "4.0.2"',
                f'commit: "{"a" * 40}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "fixture"],
        check=True,
    )
    before = values_file.read_bytes()

    harness = r'''
set -euo pipefail
source <(sed '$d' "$1")
fixture_root="$2"
script_dir() { printf '%s\n' "${fixture_root}/helm-charts/soperator/scripts"; }
repo_root() { printf '%s\n' "${fixture_root}"; }
require_cmd() { :; }
require_yq_v4() { :; }
require_docker_buildx() { :; }
require_helm_unittest() { :; }
resolve_tag_commit() { printf '%s\n' 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; }
fetch_release() { mkdir -p "$3/source"; }
verify_imports() {
  printf 'candidate: staged-only\n' >"$2/helm-charts/soperator/values.yaml"
}
sync_chart_dependencies_and_validate() { :; }
promote_staged_sync() {
  touch "${fixture_root}/promotion-was-called"
  return 97
}
main --sync --ci-preview-no-branch --scope all --report
'''
    result = subprocess.run(
        ["bash", "-c", harness, "bash", str(SCRIPT), str(repository)],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert values_file.read_bytes() == before
    assert not (repository / "promotion-was-called").exists()
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--short"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert status.stdout == ""
    assert "no candidate files are promoted to the checkout" in result.stdout
    assert "modified   helm-charts/soperator/values.yaml" in result.stdout


def test_image_scope_main_exits_without_all_scope_validation_workspace() -> None:
    harness = r'''
set -euo pipefail
source <(sed '$d' "$1")
fixture_root="$2"
script_dir() { printf '%s\n' "${fixture_root}/helm-charts/soperator/scripts"; }
repo_root() { printf '%s\n' "${fixture_root}"; }
require_cmd() { :; }
require_docker_buildx() { :; }
resolve_tag_commit() { printf '%s\n' '7f1c4e817cab67e7b5d563bc11db2bff8c661189'; }
fetch_release() { mkdir -p "$3/source"; }
verify_imports() { :; }
main --scope images --report
'''
    result = subprocess.run(
        ["bash", "-c", harness, "bash", str(SCRIPT), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Upstream import verification completed for scope 'images'." in result.stdout


@pytest.mark.parametrize(
    "flag",
    ["--accept-review-baseline", "--accept-image-baseline"],
)
def test_baseline_acceptance_requires_sync(flag: str) -> None:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [str(SCRIPT), flag],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert f"{flag} is a write operation and must be used with --sync." in result.stderr


def test_live_upgrade_evidence_requires_sync(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    _write_live_upgrade_evidence(evidence, target_release="4.0.2")

    result = subprocess.run(
        [str(SCRIPT), "--live-upgrade-evidence", str(evidence)],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "--live-upgrade-evidence can only be used with --sync." in result.stderr


@pytest.mark.parametrize(
    "flag",
    ["--accept-review-baseline", "--accept-image-baseline"],
)
def test_baseline_acceptance_requires_disposable_live_evidence(flag: str) -> None:
    result = subprocess.run(
        [str(SCRIPT), "--sync", flag],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "require --live-upgrade-evidence PATH" in result.stderr
    assert "dirty working tree" not in result.stderr


def test_live_upgrade_evidence_must_match_target_release(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_live_upgrade_evidence(evidence, target_release="4.1.3")
    _write_lock(
        lock_file,
        release="4.0.2",
        producer_workflow=EVIDENCE_PRODUCER_WORKFLOW,
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--lock-file",
            str(lock_file),
            "--sync",
            "--accept-review-baseline",
            "--live-upgrade-evidence",
            str(evidence),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "evidence.target.upstream_release must be \"4.0.2\"" in result.stderr
    assert "Invalid disposable live upgrade evidence" in result.stderr


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        (
            "chart_version",
            "1.22.2",
            'evidence.source.chart_version must be "1.22.3"',
        ),
        (
            "runtime_images_sha256",
            "0" * 64,
            "evidence.source.runtime_images_sha256 must be",
        ),
    ],
)
def test_live_upgrade_evidence_must_match_locked_source_contract(
    tmp_path: Path,
    field: str,
    value: str,
    expected_error: str,
) -> None:
    evidence = tmp_path / "evidence.json"
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["source"][field] = value
    evidence.write_text(json.dumps(document), encoding="utf-8")
    _write_lock(
        lock_file,
        release="4.0.2",
        producer_workflow=EVIDENCE_PRODUCER_WORKFLOW,
    )

    result = _run_sourced_script_function(
        "validate_live_upgrade_evidence",
        str(evidence),
        "4.0.2",
        "4.0.2-ps.4",
        "nebius/nebius-ps-services",
        EVIDENCE_PRODUCER_WORKFLOW,
        str(REPO_ROOT),
        str(lock_file),
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_live_evidence_fixture_references_existing_non_verifier_workflow() -> None:
    assert (REPO_ROOT / EVIDENCE_PRODUCER_WORKFLOW).is_file()
    assert EVIDENCE_PRODUCER_WORKFLOW != (
        ".github/workflows/soperator-upstream-verifier.yml"
    )


def test_upstream_verifier_cannot_produce_its_own_live_evidence(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        producer_workflow=".github/workflows/soperator-upstream-verifier.yml",
    )
    _write_lock(
        lock_file,
        release="4.0.2",
        producer_workflow=".github/workflows/soperator-upstream-verifier.yml",
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--lock-file",
            str(lock_file),
            "--sync",
            "--accept-review-baseline",
            "--live-upgrade-evidence",
            str(evidence),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "cannot produce its own upgrade evidence" in result.stderr


def test_live_upgrade_evidence_must_match_exact_target_chart(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.1",
    )
    _write_lock(
        lock_file,
        release="4.0.2",
        producer_workflow=EVIDENCE_PRODUCER_WORKFLOW,
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--lock-file",
            str(lock_file),
            "--sync",
            "--accept-image-baseline",
            "--live-upgrade-evidence",
            str(evidence),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "evidence.target.chart_version must be \"4.0.2-ps.4\"" in result.stderr


def test_live_upgrade_evidence_rejects_non_allowlisted_producer(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
        producer_workflow=".github/workflows/helm-chart-publish.yml",
    )
    _write_lock(
        lock_file,
        release="4.0.2",
        producer_workflow=EVIDENCE_PRODUCER_WORKFLOW,
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--lock-file",
            str(lock_file),
            "--sync",
            "--accept-image-baseline",
            "--live-upgrade-evidence",
            str(evidence),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "evidence.producer.workflow must be" in result.stderr
    assert EVIDENCE_PRODUCER_WORKFLOW in result.stderr


def test_production_lock_has_no_trusted_evidence_producer(tmp_path: Path) -> None:
    lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    evidence_contract = lock["conformance"]["live_upgrade_evidence"]
    assert evidence_contract == {
        "producer_repository": "nebius/nebius-ps-services",
        "producer_workflow": None,
        "trusted_ref": "refs/heads/main",
        "trusted_event": "workflow_dispatch",
        "artifact_name": "soperator-disposable-upgrade-evidence",
        "required_source": {
            "upstream_release": SOURCE_UPSTREAM_RELEASE,
            "chart_version": SOURCE_CHART_VERSION,
            "tarball_sha256": SOURCE_TARBALL_SHA256,
            "contract_fingerprint": SOURCE_CONTRACT_FINGERPRINT,
            "runtime_images_sha256": SOURCE_RUNTIME_IMAGES_SHA256,
            "profile_file": SOURCE_PROFILE_FILE,
        },
    }

    evidence = tmp_path / "evidence.json"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--sync",
            "--accept-image-baseline",
            "--live-upgrade-evidence",
            str(evidence),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "No trusted disposable-campaign evidence producer is configured" in (
        result.stderr
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("expired", "evidence has expired"),
        (
            "indeterminate",
            "evidence.acceptance.tui_actions.indeterminate must be 0",
        ),
    ],
)
def test_live_upgrade_evidence_rejects_failed_acceptance_proof(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    evidence = tmp_path / "evidence.json"
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    if mutation == "expired":
        document["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    else:
        document["acceptance"]["tui_actions"]["indeterminate"] = 1
    evidence.write_text(json.dumps(document), encoding="utf-8")
    _write_lock(
        lock_file,
        release="4.0.2",
        producer_workflow=EVIDENCE_PRODUCER_WORKFLOW,
    )

    result = subprocess.run(
        [
            str(SCRIPT),
            "--lock-file",
            str(lock_file),
            "--sync",
            "--accept-image-baseline",
            "--live-upgrade-evidence",
            str(evidence),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_live_upgrade_evidence_commit_binding_rejects_same_version_other_commit(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
    )

    result = _run_sourced_script_function(
        "verify_live_upgrade_evidence_commit",
        str(evidence),
        "f" * 40,
    )

    assert result.returncode == 1
    assert "does not match resolved commit" in result.stderr


def test_candidate_manifest_changes_with_runtime_platform_digest(tmp_path: Path) -> None:
    candidate_root = tmp_path / "candidate"
    shutil.copytree(REPO_ROOT / "helm-charts", candidate_root / "helm-charts")
    candidate_lock = candidate_root / "helm-charts/soperator/upstream-soperator.lock.yaml"
    lock = yaml.safe_load(candidate_lock.read_text(encoding="utf-8"))
    original = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(candidate_root),
        str(candidate_lock),
    )
    lock["imports"]["runtime_images"][0]["platform_digests"]["linux/amd64"] = (
        "sha256:" + "0" * 64
    )
    candidate_lock.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    changed = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(candidate_root),
        str(candidate_lock),
    )

    assert original.returncode == 0, original.stderr
    assert changed.returncode == 0, changed.stderr
    assert re.fullmatch(r"[0-9a-f]{64}\n?", original.stdout)
    assert original.stdout != changed.stdout


def test_candidate_manifest_changes_with_runtime_command_contract(tmp_path: Path) -> None:
    candidate_root = tmp_path / "candidate"
    shutil.copytree(REPO_ROOT / "helm-charts", candidate_root / "helm-charts")
    candidate_lock = candidate_root / "helm-charts/soperator/upstream-soperator.lock.yaml"
    lock = yaml.safe_load(candidate_lock.read_text(encoding="utf-8"))
    original = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(candidate_root),
        str(candidate_lock),
    )
    login_image = next(
        image
        for image in lock["imports"]["runtime_images"]
        if image["name"] == "Slurm login SSH"
    )
    login_image["required_commands"].remove("ssh-keyscan")
    candidate_lock.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    changed = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(candidate_root),
        str(candidate_lock),
    )

    assert original.returncode == 0, original.stderr
    assert changed.returncode == 0, changed.stderr
    assert original.stdout != changed.stdout


@pytest.mark.parametrize(
    "relative_path",
    [
        "helm-charts/soperator/templates/slurm-cluster/slurm-cluster-cr.yaml",
        "helm-charts/soperator/values.yaml",
        "helm-charts/soperator/slurm_scripts/prolog.sh",
        "helm-charts/soperator-activechecks/scripts/gpu-fryer.sh",
    ],
)
def test_candidate_manifest_rejects_any_changed_chart_runtime_file(
    tmp_path: Path,
    relative_path: str,
) -> None:
    candidate_root = tmp_path / "candidate"
    shutil.copytree(REPO_ROOT / "helm-charts", candidate_root / "helm-charts")
    candidate_lock = candidate_root / "helm-charts/soperator/upstream-soperator.lock.yaml"
    evidence = tmp_path / "evidence.json"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
    )
    original = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(candidate_root),
        str(candidate_lock),
    )
    assert original.returncode == 0, original.stderr
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["target"]["candidate_manifest_sha256"] = original.stdout.strip()
    evidence.write_text(json.dumps(document), encoding="utf-8")

    changed_path = candidate_root / relative_path
    changed_path.write_bytes(changed_path.read_bytes() + b"\n# candidate drift\n")
    result = _run_sourced_script_function(
        "verify_live_upgrade_candidate_manifest",
        str(evidence),
        str(candidate_root),
        str(candidate_lock),
    )

    assert result.returncode == 1
    assert "candidate manifest does not match" in result.stderr


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is not installed")
def test_candidate_manifest_is_stable_across_independent_dependency_builds(
    tmp_path: Path,
) -> None:
    def create_candidate(root: Path) -> Path:
        parent = root / "helm-charts/soperator"
        child = root / "helm-charts/child"
        (parent / "templates").mkdir(parents=True)
        (child / "templates").mkdir(parents=True)
        (parent / "Chart.yaml").write_text(
            "\n".join(
                [
                    "apiVersion: v2",
                    "name: soperator",
                    "version: 4.0.2-ps.4",
                    "dependencies:",
                    "  - name: child",
                    "    version: 1.0.0",
                    "    repository: file://../child",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (parent / "values.yaml").write_text("enabled: true\n", encoding="utf-8")
        (parent / "templates/configmap.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: parent\n",
            encoding="utf-8",
        )
        (child / "Chart.yaml").write_text(
            "apiVersion: v2\nname: child\nversion: 1.0.0\n",
            encoding="utf-8",
        )
        (child / "values.yaml").write_text("value: child\n", encoding="utf-8")
        (child / "templates/configmap.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: child\n",
            encoding="utf-8",
        )
        lock = parent / "upstream-soperator.lock.yaml"
        lock.write_text(
            "\n".join(
                [
                    'release: "4.0.2"',
                    f'commit: "{"a" * 40}"',
                    "exact_sync_targets: []",
                    "imports:",
                    "  runtime_images: []",
                    "  chart_versions: []",
                    "  images: []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["helm", "dependency", "build", str(parent)],
            text=True,
            capture_output=True,
            check=True,
        )
        return lock

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_lock = create_candidate(first_root)
    second_lock = create_candidate(second_root)
    first_archive = next((first_root / "helm-charts/soperator/charts").glob("*.tgz"))
    second_archive = next((second_root / "helm-charts/soperator/charts").glob("*.tgz"))
    if first_archive.read_bytes() == second_archive.read_bytes():
        changed = bytearray(second_archive.read_bytes())
        changed[4:8] = (1).to_bytes(4, byteorder="little")
        second_archive.write_bytes(changed)
    assert first_archive.read_bytes() != second_archive.read_bytes()

    first = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(first_root),
        str(first_lock),
    )
    second = _run_sourced_script_function(
        "candidate_manifest_sha256",
        str(second_root),
        str(second_lock),
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_candidate_manifest_rejects_evidence_from_different_runtime_bits(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    _write_live_upgrade_evidence(
        evidence,
        target_release="4.0.2",
        target_chart_version="4.0.2-ps.4",
    )

    result = _run_sourced_script_function(
        "verify_live_upgrade_candidate_manifest",
        str(evidence),
        str(REPO_ROOT),
        str(LOCK),
    )

    assert result.returncode == 1
    assert "candidate manifest does not match" in result.stderr


def test_runtime_image_lock_pins_reference_index_and_platform_digests() -> None:
    lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))

    assert lock["release"] == "4.0.2"
    runtime_images = lock["imports"]["runtime_images"]
    assert runtime_images

    for image in runtime_images:
        assert image["reference"].strip() == image["reference"]
        assert "@" not in image["reference"]
        assert SHA256_DIGEST.fullmatch(image["digest"])

        required_platforms = image["required_platforms"]
        platform_digests = image["platform_digests"]
        assert required_platforms
        assert set(platform_digests) == set(required_platforms)
        assert all(
            SHA256_DIGEST.fullmatch(digest)
            for digest in platform_digests.values()
        )

    # This multi-platform index proves the platform digest is a distinct lock
    # boundary rather than an alias for the image-list digest.
    populate_jail = next(
        image for image in runtime_images if image["name"] == "PopulateJail"
    )
    assert (
        populate_jail["platform_digests"]["linux/amd64"]
        != populate_jail["digest"]
    )

    login_image = next(
        image for image in runtime_images if image["name"] == "Slurm login SSH"
    )
    assert login_image["reference"] == (
        "cr.eu-north1.nebius.cloud/soperator/login_sshd:4.0.2-slurm25.11.3"
    )
    assert login_image["required_commands"] == [
        "awk",
        "cat",
        "readlink",
        "sha256sum",
        "sort",
        "ssh-keygen",
        "ssh-keyscan",
        "sshd",
        "tr",
    ]


def _write_runtime_command_verifier_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    fixture_root = tmp_path / "fixture"
    chart_dir = fixture_root / "helm-charts/soperator"
    chart_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: soperator\nversion: 4.0.2-ps.1\nappVersion: 4.0.2\n",
        encoding="utf-8",
    )
    (chart_dir / "values.yaml").write_text(
        "images:\n  sshd: registry.example/soperator/login_sshd:4.0.2\n",
        encoding="utf-8",
    )
    index_digest = "sha256:" + "a" * 64
    platform_digest = "sha256:" + "b" * 64
    lock_file = chart_dir / "upstream-soperator.lock.yaml"
    lock_file.write_text(
        yaml.safe_dump(
            {
                "release": "4.0.2",
                "exact_sync_targets": ["fixture-scripts"],
                "local_owned_paths": ["helm-charts/soperator/values.yaml"],
                "imports": {
                    "scripts": [
                        {
                            "name": "fixture scripts",
                            "scope": "scripts",
                            "local_path": "fixture-scripts",
                        }
                    ],
                    "crds": [],
                    "chart_versions": [],
                    "images": [],
                    "runtime_images": [
                        {
                            "name": "Slurm login SSH",
                            "local_file": "helm-charts/soperator/values.yaml",
                            "image_path": ["images", "sshd"],
                            "reference": (
                                "registry.example/soperator/login_sshd:4.0.2"
                            ),
                            "digest": index_digest,
                            "required_platforms": ["linux/amd64"],
                            "platform_digests": {
                                "linux/amd64": platform_digest,
                            },
                            "required_commands": [
                                "awk",
                                "cat",
                                "readlink",
                                "sha256sum",
                                "sort",
                                "ssh-keygen",
                                "ssh-keyscan",
                                "sshd",
                                "tr",
                            ],
                        }
                    ],
                    "review": [],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    upstream_root = tmp_path / "upstream"
    upstream_root.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
if [[ "$1" == "buildx" && "$2" == "imagetools" && "$3" == "inspect" ]]; then
  cat <<'JSON'
{{"digest":"{index_digest}","manifests":[{{"digest":"{platform_digest}","platform":{{"os":"linux","architecture":"amd64"}}}}]}}
JSON
  exit 0
fi
if [[ "$1" == "run" ]]; then
  for ((argument_index = 1; argument_index <= $#; argument_index += 1)); do
    if [[ "${{!argument_index}}" == "--cidfile" ]]; then
      cidfile_index=$((argument_index + 1))
      printf '%064d\n' 0 >"${{!cidfile_index}}"
    fi
  done
  if [[ "${{FAKE_HANG_RUNTIME:-0}}" == "1" ]]; then
    printf 'PRIVATE-RUNTIME-OUTPUT\n' >&2
    sleep 60
  fi
  if [[ "${{FAKE_FLOOD_RUNTIME:-0}}" == "1" ]]; then
    for ((output_index = 0; output_index < 20000; output_index += 1)); do
      printf 'PRIVATE-RUNTIME-OUTPUT-%08d\n' "$output_index" >&2
    done
    exit 93
  fi
  if [[ -n "${{FAKE_MISSING_COMMAND:-}}" && " $* " == *" $FAKE_MISSING_COMMAND "* ]]; then
    printf 'missing required command: %s\\n' "$FAKE_MISSING_COMMAND" >&2
    exit 42
  fi
  exit 0
fi
if [[ "$1" == "rm" && "$2" == "--force" ]]; then
  exit 0
fi
printf 'unexpected docker command\\n' >&2
exit 97
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    return fixture_root, lock_file, upstream_root, docker_log


def _run_runtime_command_verifier_fixture(
    tmp_path: Path,
    *,
    missing_command: str = "",
    remove_locked_command: str = "",
    locked_commands: object | None = None,
    hang_runtime: bool = False,
    flood_runtime: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fixture_root, lock_file, upstream_root, docker_log = (
        _write_runtime_command_verifier_fixture(tmp_path)
    )
    if remove_locked_command:
        lock = yaml.safe_load(lock_file.read_text(encoding="utf-8"))
        lock["imports"]["runtime_images"][0]["required_commands"].remove(
            remove_locked_command
        )
        lock_file.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    if locked_commands is not None:
        lock = yaml.safe_load(lock_file.read_text(encoding="utf-8"))
        lock["imports"]["runtime_images"][0]["required_commands"] = locked_commands
        lock_file.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    script_path = SCRIPT
    if hang_runtime:
        script_path = tmp_path / "verify-upstream-soperator-sync.sh"
        script_text = SCRIPT.read_text(encoding="utf-8")
        timeout_marker = "RUNTIME_COMMAND_TIMEOUT_SECONDS = 120"
        assert script_text.count(timeout_marker) == 1
        script_path.write_text(
            script_text.replace(timeout_marker, "RUNTIME_COMMAND_TIMEOUT_SECONDS = 1"),
            encoding="utf-8",
        )
    result = _run_sourced_script_function(
        "verify_imports",
        str(lock_file),
        str(fixture_root),
        str(upstream_root),
        "images",
        "0",
        "1",
        "4.0.2",
        "a" * 40,
        "0",
        "0",
        env_overrides={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(docker_log),
            "FAKE_MISSING_COMMAND": missing_command,
            "FAKE_HANG_RUNTIME": "1" if hang_runtime else "0",
            "FAKE_FLOOD_RUNTIME": "1" if flood_runtime else "0",
        },
        script_path=script_path,
    )
    return result, docker_log


def test_runtime_command_verifier_runs_immutable_platform_image(tmp_path: Path) -> None:
    result, docker_log = _run_runtime_command_verifier_fixture(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (
        "commands=awk,cat,readlink,sha256sum,sort,ssh-keygen,ssh-keyscan,sshd,tr"
        in result.stdout
    )
    docker_calls = docker_log.read_text(encoding="utf-8")
    assert (
        "registry.example/soperator/login_sshd@sha256:" + "b" * 64
    ) in docker_calls
    assert "--platform linux/amd64" in docker_calls
    assert "--network none --read-only --cap-drop ALL" in docker_calls
    assert "--security-opt no-new-privileges" in docker_calls
    assert "--pids-limit 64 --memory 128m --cpus 1" in docker_calls
    assert "ssh-keyscan" in docker_calls


def test_runtime_command_verifier_rejects_missing_pinned_image_command(
    tmp_path: Path,
) -> None:
    result, _ = _run_runtime_command_verifier_fixture(
        tmp_path,
        missing_command="ssh-keyscan",
    )

    assert result.returncode == 1
    assert "immutable runtime command check failed" in result.stderr
    assert "missing required command: ssh-keyscan" in result.stderr


def test_runtime_command_verifier_rejects_weakened_login_contract(
    tmp_path: Path,
) -> None:
    result, _ = _run_runtime_command_verifier_fixture(
        tmp_path,
        remove_locked_command="ssh-keyscan",
    )

    assert result.returncode == 1
    assert "runtime command contract" in result.stderr
    assert "must exactly match the cxcli login-session probe contract" in result.stderr


@pytest.mark.parametrize(
    ("locked_commands", "expected_error"),
    [
        ("ssh-keyscan", "required_commands must be an array"),
        ([], "required_commands must not be empty"),
        (["sshd", "sshd"], "required_commands contains duplicate commands"),
        (["sshd", "awk"], "required_commands must use canonical sorted order"),
        (["ssh-keyscan;touch"], "required_commands contains invalid command"),
    ],
)
def test_runtime_command_verifier_rejects_invalid_command_contract_shape(
    tmp_path: Path,
    locked_commands: object,
    expected_error: str,
) -> None:
    result, _ = _run_runtime_command_verifier_fixture(
        tmp_path,
        locked_commands=locked_commands,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_runtime_command_verifier_times_out_and_forces_redacted_cleanup(
    tmp_path: Path,
) -> None:
    result, docker_log = _run_runtime_command_verifier_fixture(
        tmp_path,
        hang_runtime=True,
    )

    assert result.returncode == 1
    assert "immutable runtime command check timed out" in result.stderr
    assert "forced container cleanup completed" in result.stderr
    assert "command output redacted" in result.stderr
    assert "PRIVATE-RUNTIME-OUTPUT" not in result.stderr
    assert "rm --force" in docker_log.read_text(encoding="utf-8")


def test_runtime_command_verifier_caps_and_redacts_flooded_output(
    tmp_path: Path,
) -> None:
    result, _ = _run_runtime_command_verifier_fixture(
        tmp_path,
        flood_runtime=True,
    )

    assert result.returncode == 1
    assert "probe exited 93; command output redacted" in result.stderr
    assert "PRIVATE-RUNTIME-OUTPUT" not in result.stderr


def test_cxcli_conformance_is_rendered_and_singleton() -> None:
    lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    contract = lock["conformance"]["cxcli_values"]

    assert contract["render_template"] == (
        "templates/slurm-cluster/slurm-cluster-cr.yaml"
    )
    assert ["spec", "slurmNodes", "controller"] in contract[
        "rendered_required_paths"
    ]
    forbidden = {
        "ha",
        "highAvailability",
        "replicas",
        "replicaCount",
        "size",
    }
    assert set(contract["forbidden_controller_fields"]) == forbidden

    values = yaml.safe_load(
        (REPO_ROOT / contract["values_file"]).read_text(encoding="utf-8")
    )
    schema = json.loads(
        (REPO_ROOT / contract["schema_file"]).read_text(encoding="utf-8")
    )
    assert forbidden.isdisjoint(values["slurmNodes"]["controller"])
    assert forbidden.isdisjoint(
        schema["properties"]["slurmNodes"]["properties"]["controller"][
            "properties"
        ]
    )

    script = SCRIPT.read_text(encoding="utf-8")
    assert "helm template cxcli-contract" in script
    assert "helm unittest --strict --with-subchart=false" in script
    assert "Rendered cxcli singleton controller contract passed." in script
    assert "no candidate files are promoted to the checkout" in script
