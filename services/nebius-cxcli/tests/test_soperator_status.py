from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.soperator_destroy import (
    build_soperator_destroy_receipt,
    write_soperator_destroy_receipt,
)
from nebius_cxcli.soperator_full_stack_upgrade import (
    CampaignSegmentResult,
    FrozenCompatibilityRow,
    FrozenNodeGroupTarget,
    build_campaign_intent,
    campaign_receipt_path,
    create_or_resume_campaign,
    record_campaign_supervisor_state,
    run_campaign,
)
from nebius_cxcli.soperator_status import (
    SoperatorLiveStatusContext,
    _observability_receipt_path,
    _reconcile_classification,
    _reconcile_phase,
    _required_classification,
    read_soperator_completed_upgrade_evidence,
    read_soperator_operation_status,
    run_soperator_observability_verification,
)
from soperator_fixtures import sample_infrastructure_receipt


def _paths(tmp_path: Path) -> ProjectPaths:
    reports = tmp_path / "generated" / "reports"
    reports.mkdir(parents=True)
    config = tmp_path / "config.yaml"
    config.write_text("version: v1\n", encoding="utf-8")
    return ProjectPaths(
        config_path=config,
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=tmp_path / "generated" / "flux",
        reports_dir=reports,
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def _write_private(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _campaign_intent():
    return build_campaign_intent(
        target_ref="cluster-a",
        ownership="managed",
        backend="terraform",
        backend_authority_sha256="sha256:" + "b" * 64,
        provider_api_authorized=False,
        source_config_sha256="sha256:" + "a" * 64,
        source_project_snapshot_sha256="sha256:" + "c" * 64,
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        requested_release_selector="latest",
        source_release="4.1.7",
        target_release="4.2.0",
        target_jail_cuda_version="13.0",
        requested_kubernetes_selector="latest",
        source_kubernetes_version="1.33",
        supported_kubernetes_versions=("1.33", "1.34"),
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        node_group_strategy="zero-surge",
        strategy_max_surge_count=None,
        drain_timeout="30m",
        zero_size_gpu_validation="require-capacity",
        job_policy="wait-to-finish",
        cancel_job_ids=(),
        requeue_job_ids=(),
        job_wait_timeout="0s",
        job_refresh_interval="30s",
        node_groups=(
            FrozenNodeGroupTarget(
                key="worker",
                provider_name="worker",
                provider_id="nodegroup-a",
                platform="gpu-l40s",
                source_version="1.33",
                source_os="ubuntu22.04",
                source_drivers_preset="cuda12.8",
                target_version="1.34",
                target_os="ubuntu24.04",
                target_drivers_preset="cuda13.0",
                gpu=True,
            ),
        ),
        compatibility_rows=(
            FrozenCompatibilityRow(
                group_key="worker",
                kubernetes_version="1.34",
                platform="gpu-l40s",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
        ),
    )


def test_observability_receipt_filename_hashes_target_identity(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    receipt_path = _observability_receipt_path(
        paths=paths,
        target_ref="customer-cluster-a",
        verification_id="verification-1",
    )

    assert "customer-cluster-a" not in receipt_path.name
    assert receipt_path.name.startswith("soperator-observability-")
    assert receipt_path.name.endswith("-verification-1.json")


def test_observability_verification_records_missing_identity_without_running_commands(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    result = run_soperator_observability_verification(
        paths=paths,
        target_ref="cluster-a",
        project_id="",
        release="4.2.0",
        live_context=SoperatorLiveStatusContext(
            snapshot={},
            kube_context="ctx",
            extra_env={},
            cluster_id="",
        ),
        interactive=False,
        command_runner=lambda *_args, **_kwargs: pytest.fail("must not run kubectl"),
    )

    assert result.receipt.status == "failed"
    assert result.receipt.failure_code == "evidence-missing"
    assert result.receipt_path.is_file()


def test_status_phase_and_classification_helpers_fail_closed() -> None:
    assert _reconcile_phase({"supervisor": {"phase": "wait-ready"}}) == "wait-ready"
    assert _reconcile_phase({"irreversibleIntent": {"phase": "apply-release"}}) == ("apply-release")
    assert (
        _reconcile_phase(
            {
                "transitions": [
                    "invalid",
                    {"phase": "complete-phase", "status": "complete"},
                    {"phase": "failed-phase", "status": "failed"},
                ]
            }
        )
        == "failed-phase"
    )
    assert (
        _reconcile_phase({"transitions": [{"phase": "complete-phase", "status": "complete"}]})
        == "complete-phase"
    )
    assert _reconcile_phase({}) == "operation-admission"
    assert (
        _reconcile_classification(
            {"supervisor": {"disposition": "provider-secret"}},
            status="failed",
        )
        == "operation-error"
    )
    assert (
        _reconcile_classification(
            {"transitions": ["invalid", {"failureType": "operation-error"}]},
            status="failed",
        )
        == "operation-error"
    )
    assert _reconcile_classification({}, status="retrying") == "retrying"
    assert _reconcile_classification({}, status="complete") == ""
    with pytest.raises(RuntimeError, match="safe failure classification"):
        _required_classification("ProviderSpecificError", operation="destroy")


def test_status_reports_failed_destroy_without_modifying_receipt(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    infrastructure = sample_infrastructure_receipt()
    receipt = build_soperator_destroy_receipt(
        target_ref="cluster-a",
        ownership="managed",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        destroy_inventory=("mk8s:mk8scluster-a",),
        preserve_inventory=("sfs:filesystem-jail",),
        protected_storage_sha256=infrastructure.receipt_sha256,
        infrastructure_receipt=infrastructure.as_payload(),
        config_sha256="sha256:" + "a" * 64,
        post_cleanup_config_sha256="sha256:" + "b" * 64,
    )
    receipt_path = paths.reports_dir / "soperator-destroy-cluster-a.json"
    write_soperator_destroy_receipt(receipt_path, receipt)
    raw = json.loads(receipt_path.read_text(encoding="utf-8"))
    raw.update(
        {
            "status": "failed",
            "failure_classification": "runtime-error",
            "checkpoints": ["approved", "storage_verified_before_cleanup"],
        }
    )
    _write_private(receipt_path, raw)
    before = (receipt_path.read_bytes(), receipt_path.stat().st_mtime_ns)

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert (status.operation, status.status, status.phase) == (
        "destroy",
        "failed",
        "storage_verified_before_cleanup",
    )
    assert status.classification == "runtime-error"
    assert "soperator destroy" in status.resume_command
    assert (receipt_path.read_bytes(), receipt_path.stat().st_mtime_ns) == before


def test_status_can_exclude_destroy_to_detect_a_foreign_active_operation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    infrastructure = sample_infrastructure_receipt()
    receipt = build_soperator_destroy_receipt(
        target_ref="cluster-a",
        ownership="managed",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        destroy_inventory=("mk8s:mk8scluster-a",),
        preserve_inventory=("sfs:filesystem-jail",),
        protected_storage_sha256=infrastructure.receipt_sha256,
        infrastructure_receipt=infrastructure.as_payload(),
        config_sha256="sha256:" + "a" * 64,
        post_cleanup_config_sha256="sha256:" + "b" * 64,
    )
    write_soperator_destroy_receipt(
        paths.reports_dir / "soperator-destroy-cluster-a.json",
        receipt,
    )
    _write_private(
        paths.reports_dir / "soperator-release-intent-cluster-a.json",
        {
            "schema": "nebius-cxcli.soperator-release-intent.v3",
            "status": "active",
            "target_ref": "cluster-a",
            "requested_selector": "4.1.7",
        },
    )

    status = read_soperator_operation_status(
        paths=paths,
        target_ref="cluster-a",
        include_destroy=False,
    )

    assert status is not None
    assert status.operation == "upgrade"


def test_status_reports_upgrade_safety_pause_and_frozen_resume(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    intent_path = paths.reports_dir / "soperator-release-intent-cluster-a.json"
    _write_private(
        intent_path,
        {
            "schema": "nebius-cxcli.soperator-release-intent.v3",
            "status": "active",
            "target_ref": "cluster-a",
            "requested_selector": "4.1.7",
            "operation_spec_sha256": "sha256:" + "a" * 64,
        },
    )
    reconcile_path = paths.reports_dir / "soperator-release-reconcile-cluster-a-operation.json"
    _write_private(
        reconcile_path,
        {
            "schema": "nebius-cxcli.soperator-reconcile-receipt.v6",
            "target": {"ref": "cluster-a"},
            "status": "safety-paused",
            "supervisor": {
                "phase": "wait-flux-graph",
                "disposition": "safety-paused",
            },
            "transitions": [],
        },
    )

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert (status.operation, status.status, status.phase) == (
        "upgrade",
        "safety-paused",
        "wait-flux-graph",
    )
    assert status.classification == "safety-paused"
    assert "--to-release 4.1.7 --execute --approve" in status.resume_command


def test_status_projects_upgrade_transition_failure_classification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    reconcile_path = paths.reports_dir / "soperator-release-reconcile-cluster-a-operation.json"
    _write_private(
        reconcile_path,
        {
            "schema": "nebius-cxcli.soperator-reconcile-receipt.v6",
            "target": {"ref": "cluster-a"},
            "status": "failed",
            "transitions": [
                {
                    "phase": "wait-product-ready",
                    "status": "failed",
                    "failureType": "operation-error",
                }
            ],
        },
    )
    before = reconcile_path.read_bytes()

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert status.classification == "operation-error"
    assert reconcile_path.read_bytes() == before


@pytest.mark.parametrize(
    ("source", "value"),
    (
        ("supervisor", "future-disposition"),
        ("supervisor", "CredentialError token=abc123"),
        ("transition", "CredentialError token=abc123"),
    ),
)
def test_status_does_not_reflect_unknown_upgrade_classification(
    tmp_path: Path,
    source: str,
    value: str,
) -> None:
    paths = _paths(tmp_path)
    reconcile_path = paths.reports_dir / "soperator-release-reconcile-cluster-a-operation.json"
    payload: dict[str, object] = {
        "schema": "nebius-cxcli.soperator-reconcile-receipt.v6",
        "target": {"ref": "cluster-a"},
        "status": "recovery-required",
        "transitions": [],
    }
    if source == "supervisor":
        payload["supervisor"] = {
            "phase": "wait-product-ready",
            "disposition": value,
        }
    else:
        payload["transitions"] = [
            {
                "phase": "wait-product-ready",
                "status": "failed",
                "failureType": value,
            }
        ]
    _write_private(reconcile_path, payload)

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert status.classification == "operation-error"
    assert "abc123" not in status.classification


def test_status_ignores_complete_operations_and_reports_saved_install(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_private(
        paths.reports_dir / "soperator-release-intent-cluster-a.json",
        {
            "schema": "nebius-cxcli.soperator-release-intent.v3",
            "status": "complete",
            "target_ref": "cluster-a",
        },
    )
    install_path = paths.reports_dir / "soperator-install-plan.json"
    _write_private(
        install_path,
        {
            "schema": "nebius-cxcli.soperator-install-plan.v1",
            "status": "planned",
            "target": {"ref": "cluster-a"},
        },
    )

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert (status.operation, status.status, status.phase) == (
        "install",
        "planned",
        "saved-plan",
    )
    assert status.resume_command.endswith("--resume --dry-run")


def test_status_normalizes_failed_install_exception_type(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    install_path = paths.reports_dir / "soperator-install-plan.json"
    _write_private(
        install_path,
        {
            "schema": "nebius-cxcli.soperator-install-plan.v1",
            "status": "failed",
            "failureType": "ProviderSpecificCredentialError",
            "target": {"ref": "cluster-a"},
        },
    )
    before = install_path.read_bytes()

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert status.classification == "operation-error"
    assert "ProviderSpecificCredentialError" not in status.detail
    assert install_path.read_bytes() == before


def test_status_never_treats_observability_receipt_as_an_operation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    receipt_path = paths.reports_dir / "soperator-observability-cluster-a-verification.json"
    _write_private(
        receipt_path,
        {
            "schema": "nebius-cxcli.soperator-observability-verification.v1",
            "status": "unavailable",
            "failure_code": "authorization-denied",
            "target_ref_sha256": "sha256:" + "a" * 64,
        },
    )
    before = receipt_path.read_bytes()

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is None
    assert receipt_path.read_bytes() == before


def test_status_projects_parent_full_stack_campaign_before_child_receipts(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    intent = _campaign_intent()
    receipt_path = campaign_receipt_path(paths.project_dir, target_ref=intent.target_ref)
    create_or_resume_campaign(path=receipt_path, intent=intent)
    record_campaign_supervisor_state(
        path=receipt_path,
        intent=intent,
        state="retrying",
        attempt=3,
        disposition="retrying",
        current_segment="mk8s-hop:1.34",
        maintenance_state="active",
        failure_type="RuntimeError",
    )
    _write_private(
        paths.reports_dir / "soperator-release-intent-cluster-a.json",
        {
            "schema": "nebius-cxcli.soperator-release-intent.v3",
            "status": "active",
            "target_ref": "cluster-a",
            "requested_selector": "4.2.0",
        },
    )

    status = read_soperator_operation_status(paths=paths, target_ref="cluster-a")

    assert status is not None
    assert (status.operation, status.status, status.phase) == (
        "upgrade",
        "retrying",
        "mk8s-hop:1.34",
    )
    assert status.receipt_path == receipt_path
    assert status.classification == "retrying"
    assert "--to-release latest" in status.resume_command
    assert "--to-k8s-version latest" in status.resume_command
    assert "Backend: managed/terraform" in status.detail
    assert "worker@1.34=ubuntu24.04/cuda13.0" in status.detail


def test_status_keeps_completed_upgrade_evidence_without_active_operation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    intent = _campaign_intent()
    receipt_path = campaign_receipt_path(paths.project_dir, target_ref=intent.target_ref)

    def _segment_executor(name: str):
        if name == "final-readiness":
            return lambda: CampaignSegmentResult(
                evidence={
                    "gpuRuntimeEvidence": {"worker": "passed"},
                    "gpuRuntimeValidationReports": {
                        "worker": {
                            "reportFile": "/reports/worker.json",
                            "selectedNodeCount": 1,
                            "passedNodeCount": 1,
                        }
                    },
                }
            )
        return lambda: CampaignSegmentResult(evidence={"segment": "complete"})

    segment_executors = {name: _segment_executor(name) for name in intent.segments}
    run_campaign(
        path=receipt_path,
        intent=intent,
        segment_executors=segment_executors,
        enter_maintenance=lambda _record, _evidence: {"partitionCount": 1},
        restore_maintenance=lambda _record, _evidence: {"partitionCount": 1},
        assert_fence=lambda: None,
    )

    assert read_soperator_operation_status(paths=paths, target_ref="cluster-a") is None
    evidence = read_soperator_completed_upgrade_evidence(
        paths=paths,
        target_ref="cluster-a",
    )

    assert evidence is not None
    assert evidence.backend == "terraform"
    assert evidence.provider_compatibility == ("worker@1.34=ubuntu24.04/cuda13.0",)
    assert evidence.gpu_runtime == ("worker=passed (nodes 1/1; report /reports/worker.json)",)

    observed_running = []

    def _fail_final_revalidation() -> CampaignSegmentResult:
        observed_running.append(
            read_soperator_operation_status(paths=paths, target_ref="cluster-a")
        )
        raise RuntimeError("injected final revalidation failure")

    replay_executors = {
        name: (lambda: CampaignSegmentResult(evidence={"unused": True})) for name in intent.segments
    }
    replay_executors["final-readiness"] = _fail_final_revalidation
    with pytest.raises(RuntimeError, match="final revalidation failure"):
        run_campaign(
            path=receipt_path,
            intent=intent,
            segment_executors=replay_executors,
            enter_maintenance=lambda _record, _evidence: pytest.fail(
                "final revalidation must not reopen maintenance"
            ),
            restore_maintenance=lambda _record, _evidence: pytest.fail(
                "final revalidation must not restore maintenance"
            ),
            assert_fence=lambda: None,
        )

    assert len(observed_running) == 1
    assert observed_running[0] is not None
    assert observed_running[0].status == "running"
    retrying = read_soperator_operation_status(paths=paths, target_ref="cluster-a")
    assert retrying is not None
    assert retrying.status == "retrying"
    assert "last-known-good evidence is retained" in retrying.detail
    retained = read_soperator_completed_upgrade_evidence(
        paths=paths,
        target_ref="cluster-a",
    )
    assert retained is not None
    assert retained.gpu_runtime == evidence.gpu_runtime

    record_campaign_supervisor_state(
        path=receipt_path,
        intent=intent,
        state="terminal-failed",
        attempt=1,
        disposition="terminal-failed",
        current_segment="final-readiness",
        maintenance_state="restored",
        failure_type="RuntimeError",
    )
    terminal = read_soperator_operation_status(paths=paths, target_ref="cluster-a")
    assert terminal is not None
    assert terminal.status == "terminal-failed"

    replay_executors["final-readiness"] = lambda: CampaignSegmentResult(
        evidence={
            "gpuRuntimeEvidence": {"worker": "passed"},
            "gpuRuntimeValidationReports": {
                "worker": {
                    "reportFile": "/reports/worker-new.json",
                    "selectedNodeCount": 2,
                    "passedNodeCount": 2,
                }
            },
        }
    )
    run_campaign(
        path=receipt_path,
        intent=intent,
        segment_executors=replay_executors,
        enter_maintenance=lambda _record, _evidence: pytest.fail(
            "final revalidation must not reopen maintenance"
        ),
        restore_maintenance=lambda _record, _evidence: pytest.fail(
            "final revalidation must not restore maintenance"
        ),
        assert_fence=lambda: None,
    )

    assert read_soperator_operation_status(paths=paths, target_ref="cluster-a") is None
    refreshed = read_soperator_completed_upgrade_evidence(
        paths=paths,
        target_ref="cluster-a",
    )
    assert refreshed is not None
    assert refreshed.gpu_runtime == ("worker=passed (nodes 2/2; report /reports/worker-new.json)",)


def test_status_does_not_project_node_group_migration_receipts(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    migration_path = (
        paths.project_dir
        / ".nebius-cxcli"
        / "node-group-migrations-v1"
        / "cluster-a"
        / "worker"
        / "receipt.json"
    )
    migration_path.parent.mkdir(parents=True)
    _write_private(migration_path, {"schema": "deliberately-invalid-if-read"})

    assert read_soperator_operation_status(paths=paths, target_ref="cluster-a") is None
