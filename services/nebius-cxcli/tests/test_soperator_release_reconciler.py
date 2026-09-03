from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.soperator_failures import (
    SoperatorMainWorkloadIdentity,
    SoperatorMainWorkloadTerminalError,
    SoperatorSafetyPauseError,
)
from nebius_cxcli.soperator_operation import (
    SoperatorOperationSpec,
    soperator_sha256,
    soperator_stage_plan_sha256,
)
from nebius_cxcli.soperator_release_artifacts import SoperatorArtifactReceipt
from nebius_cxcli.soperator_release_reconciler import (
    SOPERATOR_RECONCILE_RECEIPT_SCHEMA,
    SoperatorReconcileCallbacks,
    SoperatorReconcileExecutionPolicy,
    SoperatorReconcileRepairLineage,
    reconcile_soperator_release,
    resolve_soperator_reconcile_strategy,
    soperator_reconcile_stage_plan_sha256,
)
from nebius_cxcli.soperator_release_source import (
    SOPERATOR_SOURCE_CACHE_SCHEMA,
    SoperatorSourceReceipt,
)
from nebius_cxcli.soperator_strategy import SoperatorStrategyPlan
from soperator_fixtures import sample_snapshot


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=tmp_path / "generated" / "flux",
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph"},
        "data": {
            "graph.json": json.dumps(
                {
                    "schema": "nebius-cxcli.soperator-release-graph.v1",
                    "release": "4.1.7",
                    "sourceCommit": "a" * 40,
                    "sourceTree": "b" * 40,
                    "sourceManifestSha256": "sha256:" + "c" * 64,
                    "releases": [
                        {
                            "releaseName": "soperator",
                            "namespace": "soperator-system",
                            "owner": "upstream",
                            "stage": 1,
                            "sourceKind": "OCIRepository",
                            "sourceName": "soperator",
                            "revision": "sha256:" + "d" * 64,
                            "dependencies": [],
                        }
                    ],
                }
            )
        },
    }
    (paths.flux_dir / "soperator-release-graph.yaml").write_text(
        yaml.safe_dump(graph), encoding="utf-8"
    )
    return paths


def _source(snapshot) -> SoperatorSourceReceipt:
    return SoperatorSourceReceipt(
        schema=SOPERATOR_SOURCE_CACHE_SCHEMA,
        release=snapshot.release,
        commit=snapshot.commit,
        tree=snapshot.tree,
        archive_sha256=snapshot.archive_sha256,
        manifest_sha256=snapshot.source_manifest_sha256,
        source_dir="/not-persisted",
    )


def _artifacts(snapshot) -> SoperatorArtifactReceipt:
    return SoperatorArtifactReceipt(
        release=snapshot.release,
        source_manifest_sha256=snapshot.source_manifest_sha256,
        chart_package_sha256=tuple(
            sorted(
                [chart.package_sha256 for chart in snapshot.charts.values()]
                + [chart.package_sha256 for chart in snapshot.third_party_charts.values()]
            )
        ),
        umbrella_render_sha256="sha256:" + "8" * 64,
    )


def _spec(snapshot, strategy: SoperatorStrategyPlan, paths: ProjectPaths) -> SoperatorOperationSpec:
    return SoperatorOperationSpec(
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy.strategy.value,
        current_release=strategy.source_release,
        target_release=snapshot.release,
        source_contract=strategy.source_contract,
        target_contract=strategy.target_contract,
        source_capability_sha256=(
            snapshot.capability_sha256
            if strategy.strategy.value == "noop"
            else "sha256:" + "6" * 64
        ),
        target_capability_sha256=snapshot.capability_sha256,
        stage_plan_sha256=soperator_reconcile_stage_plan_sha256(
            strategy=strategy.strategy.value,
            rendered_graph_sha256=soperator_stage_plan_sha256(paths),
        ),
        release_snapshot_sha256=snapshot.snapshot_sha256,
        target_jail_image=snapshot.populate_jail_image,
        target_jail_image_source="upstream-default",
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
        infrastructure_plan_sha256="sha256:" + "1" * 64,
        desired_values_sha256="sha256:" + "2" * 64,
        adapter_sha256="sha256:" + "3" * 64,
        protected_state_sha256="sha256:" + "4" * 64,
        scheduling_sha256="sha256:" + "5" * 64,
        admission_sha256="sha256:" + "6" * 64,
    )


def _callbacks(calls: list[str]) -> SoperatorReconcileCallbacks:
    def action(name: str):
        return lambda: calls.append(name)

    def verify(name: str):
        return lambda _evidence: calls.append(name)

    return SoperatorReconcileCallbacks(
        assert_authority=action("authority"),
        resolve_sources=action("sources"),
        establish_storage=action("storage"),
        apply_desired_state=action("apply"),
        wait_flux=action("flux"),
        apply_post_flux=action("post-flux"),
        wait_pre_restore_product=action("pre-product"),
        restore_infrastructure=action("restore"),
        wait_infrastructure=action("infrastructure"),
        wait_restored_product=action("restored-product"),
        release_requeued_jobs=action("requeued"),
        wait_requeued_product=action("post-requeue"),
        release_held_jobs=action("held"),
        wait_final_product=action("final-product"),
        capture_protected_state=action("protected-capture"),
        classify_rootfs=action("rootfs-classification"),
        enforce_retention=action("retention"),
        prepare_passive_rootfs=action("passive-rootfs"),
        quiesce_legacy_owners=action("quiesce-source"),
        verify_single_writer=action("single-writer"),
        adopt_protected_state=action("protected-adoption"),
        verify_protected_state=action("protected-verification"),
        verify_rootfs_consumers=action("rootfs-consumers"),
        retire_legacy_owners=action("retire-source"),
        rollback_before_frontier=action("rollback-source"),
        completed_postconditions={
            "establish-boot-storage-barrier": verify("verify-storage"),
            "enforce-protected-volume-retention": verify("verify-retention"),
            "populate-passive-jail-rootfs": verify("verify-passive-rootfs"),
            "quiesce-legacy-release-owners": verify("verify-quiesce"),
            "apply-declarative-release": verify("verify-apply"),
            "apply-post-flux-manifests": verify("verify-post-flux"),
            "restore-infrastructure-and-scheduling-preimages": verify("verify-restore"),
            "retire-legacy-release-owners": verify("verify-retire"),
            "release-requeued-running-jobs": verify("verify-requeued"),
            "release-other-operation-held-jobs": verify("verify-held"),
        },
        interrupted_recovery={
            "populate-passive-jail-rootfs": verify("recover-passive-rootfs"),
            "restore-infrastructure-and-scheduling-preimages": verify("recover-restore"),
            "retire-legacy-release-owners": verify("recover-retire"),
            "release-requeued-running-jobs": verify("recover-requeued"),
            "release-other-operation-held-jobs": verify("recover-held"),
        },
    )


def _run(tmp_path: Path, *, current: str | None = "4.1.6"):
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release=current,
        target_release=snapshot.release,
        source_contract="upstream-flux-v1" if current else None,
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        callbacks=_callbacks(calls),
        operation_spec=_spec(snapshot, strategy, paths),
    )
    return path, calls, snapshot, strategy


def test_reconciler_persists_complete_capability_operation(tmp_path: Path) -> None:
    path, calls, snapshot, strategy = _run(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == SOPERATOR_RECONCILE_RECEIPT_SCHEMA
    assert payload["status"] == "complete"
    assert payload["release"]["snapshotSha256"] == snapshot.snapshot_sha256
    assert payload["strategy"]["strategy"] == strategy.strategy.value
    assert "apply" in calls
    assert calls.index("restored-product") < calls.index("requeued")
    assert calls.index("held") < calls.index("final-product")
    assert calls[-1] == "final-product"
    assert all("telemetry" not in transition["phase"] for transition in payload["transitions"])
    assert "/not-persisted" not in path.read_text(encoding="utf-8")


def test_completed_mutations_are_verified_without_reexecution(tmp_path: Path) -> None:
    path, first_calls, _snapshot, _strategy = _run(tmp_path)
    assert first_calls.count("apply") == 1

    _path, second_calls, _snapshot, _strategy = _run(tmp_path)

    assert _path == path
    assert "apply" not in second_calls
    assert "restore" not in second_calls
    assert "verify-apply" in second_calls
    assert "verify-restore" in second_calls
    assert "flux" in second_calls
    assert second_calls[-1] == "final-product"


def test_upgrade_stage_plans_have_no_observability_transitions(tmp_path: Path) -> None:
    path, _calls, snapshot, _strategy = _run(tmp_path)
    full_payload = json.loads(path.read_text(encoding="utf-8"))
    full_phases = [transition["phase"] for transition in full_payload["transitions"]]
    assert full_phases[-1] == "wait-final-product-readiness"
    assert all("telemetry" not in phase and "observability" not in phase for phase in full_phases)

    noop_path, _calls, _snapshot, _noop_strategy = _run(
        tmp_path / "noop",
        current=snapshot.release,
    )
    noop_payload = json.loads(noop_path.read_text(encoding="utf-8"))
    noop_transitions = [
        (transition["phase"], transition["mode"]) for transition in noop_payload["transitions"]
    ]
    assert noop_transitions == [
        ("reconcile-sources-and-wait-flux-graph", "reconcile-forward"),
        ("wait-infrastructure-convergence", "observe"),
        ("wait-final-product-readiness", "observe"),
    ]


def test_noop_reconciles_sources_without_reapplying_release(tmp_path: Path) -> None:
    _path, calls, snapshot, noop_strategy = _run(tmp_path, current="4.1.7")
    assert "apply" not in calls
    assert "restore" not in calls
    assert calls.count("authority") == 3
    assert calls[-1] == "final-product"
    in_place = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    paths = _paths(tmp_path / "plans")
    assert (
        _spec(snapshot, noop_strategy, paths).stage_plan_sha256
        != _spec(snapshot, in_place, paths).stage_plan_sha256
    )


def test_protected_data_plane_orders_rootfs_and_single_writer_before_retirement(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []

    receipt_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        callbacks=_callbacks(calls),
        operation_spec=_spec(snapshot, strategy, paths),
    )

    assert calls.index("protected-capture") < calls.index("passive-rootfs")
    assert calls.index("rootfs-classification") < calls.index("passive-rootfs")
    assert calls.index("rootfs-classification") < calls.index("quiesce-source")
    assert calls.index("single-writer") < calls.index("retire-source")
    assert calls.index("retire-source") < calls.index("requeued")
    assert calls.index("held") < calls.index("final-product")
    assert calls[-1] == "final-product"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["irreversibleFrontier"]["phase"] == "apply-declarative-release"


def test_protected_apply_failure_requires_forward_recovery(tmp_path: Path) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    callbacks = _callbacks(calls)

    def fail_apply() -> None:
        calls.append("apply-failed")
        raise RuntimeError("target apply failed")

    with pytest.raises(RuntimeError, match="target apply failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(callbacks, apply_desired_state=fail_apply),
            operation_spec=_spec(snapshot, strategy, paths),
        )

    assert "rollback-source" not in calls
    receipts = tuple(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    assert len(receipts) == 1
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "recovery-required"
    assert payload["irreversibleIntent"]["phase"] == "apply-declarative-release"


def test_repair_successor_imports_completed_prefix_and_starts_at_apply(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)
    predecessor_calls: list[str] = []
    predecessor_callbacks = _callbacks(predecessor_calls)

    def interrupt_apply() -> None:
        predecessor_calls.append("apply-interrupted")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                predecessor_callbacks,
                apply_desired_state=interrupt_apply,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    assert predecessor["transitions"][-1]["phase"] == "apply-declarative-release"
    assert predecessor["transitions"][-1]["status"] == "running"

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason="victoria-metrics-install-retry-v1",
        ),
    )

    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1
    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert successor["repairLineage"]["resumePhase"] == "apply-declarative-release"
    assert successor["repairLineage"]["predecessorOperationId"] == predecessor["operationId"]
    imported = successor["transitions"][:7]
    assert all(item["status"] == "complete" for item in imported)
    assert all("repairPredecessor" in item for item in imported)


def test_repair_successor_reapplies_from_authenticated_running_flux_wait(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)

    def interrupt_wait_flux() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                _callbacks([]),
                wait_flux=interrupt_wait_flux,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    assert predecessor["status"] == "running"
    assert predecessor["transitions"][-1]["phase"] == "wait-flux-graph"
    assert predecessor["transitions"][-1]["status"] == "running"
    assert predecessor["irreversibleFrontier"]["phase"] == "apply-declarative-release"

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=replace(
            _artifacts(snapshot),
            umbrella_render_sha256="sha256:" + "7" * 64,
        ),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason="controller-spool-receipt-writer-v1",
        ),
    )

    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert successor["repairLineage"]["predecessorFrontier"] == "running-wait-flux-graph"
    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1


def test_repair_successor_rejects_corrupt_running_flux_wait_frontier(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)

    def interrupt_wait_flux() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(_callbacks([]), wait_flux=interrupt_wait_flux),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor["irreversibleFrontier"]["transitionId"] = "corrupt"
    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )

    with pytest.raises(ValueError, match="running Flux-wait frontier evidence"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=replace(
                _artifacts(snapshot),
                umbrella_render_sha256="sha256:" + "7" * 64,
            ),
            callbacks=_callbacks([]),
            operation_spec=successor_spec,
            repair_lineage=SoperatorReconcileRepairLineage(
                predecessor_receipt=predecessor,
                previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
                resume_phase="apply-declarative-release",
                reason="controller-spool-receipt-writer-v1",
            ),
        )


def test_repair_successor_reapplies_after_failed_protected_adoption(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)
    predecessor_calls: list[str] = []
    predecessor_callbacks = _callbacks(predecessor_calls)

    def fail_adoption() -> None:
        predecessor_calls.append("protected-adoption-failed")
        raise RuntimeError("protected adoption failed")

    with pytest.raises(RuntimeError, match="protected adoption failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                predecessor_callbacks,
                adopt_protected_state=fail_adoption,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    assert predecessor["status"] == "recovery-required"
    assert predecessor["transitions"][-1]["phase"] == "adopt-protected-data-plane"
    assert predecessor["transitions"][-1]["status"] == "failed"

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_artifacts = replace(
        _artifacts(snapshot),
        umbrella_render_sha256="sha256:" + "7" * 64,
    )
    with pytest.raises(ValueError, match="reason is not an admitted render repair"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=successor_artifacts,
            callbacks=_callbacks([]),
            operation_spec=successor_spec,
            repair_lineage=SoperatorReconcileRepairLineage(
                predecessor_receipt=predecessor,
                previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
                resume_phase="apply-declarative-release",
                reason="untrusted-render-repair-v1",
            ),
        )
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=successor_artifacts,
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason="controller-storage-render-contract-v2",
        ),
    )

    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1
    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert (
        successor["repairLineage"]["predecessorFrontier"] == "failed-protected-adoption-after-apply"
    )
    assert (
        successor["repairLineage"]["predecessorArtifactReceiptSha256"]
        != successor["repairLineage"]["replacementArtifactReceiptSha256"]
    )
    assert (
        successor["repairLineage"]["predecessorUmbrellaRenderSha256"]
        != successor["repairLineage"]["replacementUmbrellaRenderSha256"]
    )


@pytest.mark.parametrize(
    "repair_reason",
    [
        "registered-runtime-shape-repair-v1",
        "registered-operator-capability-repair-v1",
        "registered-static-partition-repair-v1",
        "registered-static-worker-rollout-v1",
        "registered-partition-output-sentinel-repair-v1",
    ],
)
def test_repair_successor_reapplies_after_interrupted_failed_protected_adoption(
    tmp_path: Path,
    repair_reason: str,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)

    def fail_adoption() -> None:
        raise RuntimeError("protected adoption failed")

    with pytest.raises(RuntimeError, match="protected adoption failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                _callbacks([]),
                adopt_protected_state=fail_adoption,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    predecessor["status"] = "running"

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=replace(
            _artifacts(snapshot),
            umbrella_render_sha256="sha256:" + "7" * 64,
        ),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason=repair_reason,
        ),
    )

    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert (
        successor["repairLineage"]["predecessorFrontier"] == "failed-protected-adoption-after-apply"
    )
    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1


@pytest.mark.parametrize(
    "repair_reason",
    (
        "registered-scheduling-runtime-repair-v1",
        "registered-static-partition-repair-v1",
    ),
)
def test_static_partition_repair_reapplies_after_failed_preimage_restore(
    tmp_path: Path,
    repair_reason: str,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)

    def fail_restore() -> None:
        raise RuntimeError("restore precondition failed before mutation")

    with pytest.raises(RuntimeError, match="restore precondition failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                _callbacks([]),
                restore_infrastructure=fail_restore,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    assert predecessor["status"] == "recovery-required"
    assert predecessor["transitions"][-1]["phase"] == (
        "restore-infrastructure-and-scheduling-preimages"
    )
    assert predecessor["transitions"][-1].get("intentSha256") is None

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=replace(
            _artifacts(snapshot),
            umbrella_render_sha256="sha256:" + "7" * 64,
        ),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason=repair_reason,
        ),
    )

    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert successor["repairLineage"]["predecessorFrontier"] == (
        "failed-preimage-restore-after-apply"
    )
    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1


def test_static_worker_rollout_reapplies_after_failed_pre_restore_readiness(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)

    def fail_readiness() -> None:
        raise RuntimeError("static workers did not register")

    with pytest.raises(RuntimeError, match="static workers did not register"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                _callbacks([]),
                wait_pre_restore_product=fail_readiness,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    assert predecessor["status"] == "recovery-required"
    assert predecessor["transitions"][-1]["phase"] == ("wait-pre-restore-product-readiness")

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=replace(
            _artifacts(snapshot),
            umbrella_render_sha256="sha256:" + "7" * 64,
        ),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason="registered-static-worker-rollout-v1",
        ),
    )

    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert successor["repairLineage"]["predecessorFrontier"] == (
        "failed-pre-restore-readiness-after-apply"
    )
    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1


def test_repair_successor_reapplies_after_failed_declarative_release(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)
    predecessor_calls: list[str] = []

    def fail_apply() -> None:
        predecessor_calls.append("apply-failed")
        raise RuntimeError("declarative release failed")

    with pytest.raises(RuntimeError, match="declarative release failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                _callbacks(predecessor_calls),
                apply_desired_state=fail_apply,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    assert predecessor["status"] == "recovery-required"
    assert predecessor["transitions"][-1]["phase"] == "apply-declarative-release"
    assert predecessor["transitions"][-1]["status"] == "failed"

    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )
    successor_calls: list[str] = []
    successor_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=replace(
            _artifacts(snapshot),
            umbrella_render_sha256="sha256:" + "7" * 64,
        ),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason="controller-storage-render-contract-v2",
        ),
    )

    successor = json.loads(successor_path.read_text(encoding="utf-8"))
    assert successor["status"] == "complete"
    assert (
        successor["repairLineage"]["predecessorFrontier"]
        == "failed-declarative-release-after-quiescence"
    )
    assert "passive-rootfs" not in successor_calls
    assert "verify-passive-rootfs" in successor_calls
    assert successor_calls.count("apply") == 1


def test_repair_successor_rebuilds_only_an_authenticated_preapply_replay(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    predecessor_spec = _spec(snapshot, strategy, paths)
    predecessor_callbacks = _callbacks([])

    def interrupt_apply() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                predecessor_callbacks,
                apply_desired_state=interrupt_apply,
            ),
            operation_spec=predecessor_spec,
        )
    predecessor_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    successor_spec = replace(
        predecessor_spec,
        intervention_generation=1,
        admission_sha256="sha256:" + "9" * 64,
    )

    def interrupt_population() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(
                _callbacks([]),
                prepare_passive_rootfs=interrupt_population,
            ),
            operation_spec=successor_spec,
        )
    successor_path = next(
        path
        for path in paths.reports_dir.glob("soperator-release-reconcile-*.json")
        if path != predecessor_path
    )
    replay = json.loads(successor_path.read_text(encoding="utf-8"))
    assert replay["transitions"][-1]["phase"] == "populate-passive-jail-rootfs"
    assert replay["transitions"][-1]["status"] == "running"
    discarded_sha256 = soperator_sha256(replay)
    successor_calls: list[str] = []

    tampered = copy.deepcopy(replay)
    tampered["transitions"][-1]["id"] = "0" * 64
    successor_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="frontier changed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=_callbacks([]),
            operation_spec=successor_spec,
            repair_lineage=SoperatorReconcileRepairLineage(
                predecessor_receipt=predecessor,
                previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
                resume_phase="apply-declarative-release",
                reason="victoria-metrics-install-retry-v1",
                discarded_replay_receipt_sha256=soperator_sha256(tampered),
            ),
        )
    successor_path.write_text(json.dumps(replay), encoding="utf-8")

    repaired_path = reconcile_soperator_release(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        callbacks=_callbacks(successor_calls),
        operation_spec=successor_spec,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(predecessor_spec)),
            resume_phase="apply-declarative-release",
            reason="victoria-metrics-install-retry-v1",
            discarded_replay_receipt_sha256=discarded_sha256,
        ),
    )

    assert repaired_path == successor_path
    assert "passive-rootfs" not in successor_calls
    repaired = json.loads(repaired_path.read_text(encoding="utf-8"))
    assert repaired["repairLineage"]["discardedReplayReceiptSha256"] == discarded_sha256


def test_protected_failure_before_target_apply_restores_source_ownership(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    callbacks = _callbacks(calls)

    def fail_quiesce() -> None:
        calls.append("quiesce-failed")
        raise RuntimeError("source quiesce failed")

    with pytest.raises(RuntimeError, match="source quiesce failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(callbacks, quiesce_legacy_owners=fail_quiesce),
            operation_spec=_spec(snapshot, strategy, paths),
        )

    assert calls[-1] == "rollback-source"
    receipts = tuple(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "failed-rolled-back"


def test_protected_retirement_failure_is_forward_recovery_only(tmp_path: Path) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="1.22.0",
        target_release=snapshot.release,
        source_contract="protected-data-plane-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    callbacks = _callbacks(calls)

    def fail_retirement() -> None:
        calls.append("retirement-failed")
        raise RuntimeError("source retirement failed")

    with pytest.raises(RuntimeError, match="source retirement failed"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(callbacks, retire_legacy_owners=fail_retirement),
            operation_spec=_spec(snapshot, strategy, paths),
        )

    assert "rollback-source" not in calls
    receipts = tuple(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "recovery-required"
    assert payload["irreversibleFrontier"]["phase"] == "apply-declarative-release"


def test_snapshot_mismatch_fails_before_callback(tmp_path: Path) -> None:
    snapshot = sample_snapshot()
    strategy = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    paths = _paths(tmp_path)
    with pytest.raises(ValueError, match="source receipt"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=replace(_source(snapshot), commit="c" * 40),
            artifacts=_artifacts(snapshot),
            callbacks=_callbacks(calls),
            operation_spec=_spec(snapshot, strategy, paths),
        )
    assert calls == []


def test_stage_plan_mismatch_fails_before_receipt_or_callback(tmp_path: Path) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    with pytest.raises(ValueError, match="operation spec"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=_callbacks(calls),
            operation_spec=replace(
                _spec(snapshot, strategy, paths),
                stage_plan_sha256="sha256:" + "0" * 64,
            ),
        )
    assert calls == []
    assert not paths.reports_dir.exists()


def test_legacy_telemetry_stage_plan_fails_closed_without_rewriting_history(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    paths.reports_dir.mkdir(parents=True)
    incomplete_path = paths.reports_dir / "legacy-telemetry-incomplete.json"
    completed_path = paths.reports_dir / "legacy-telemetry-complete.json"
    incomplete_bytes = (
        b'{"status":"recovery-required","phase":'
        b'"wait-final-authoritative-telemetry"}\n'
    )
    completed_bytes = (
        b'{"status":"complete","phase":"wait-final-authoritative-telemetry"}\n'
    )
    incomplete_path.write_bytes(incomplete_bytes)
    completed_path.write_bytes(completed_bytes)
    legacy_stage_plan_sha256 = soperator_sha256(
        {
            "renderedGraphSha256": soperator_stage_plan_sha256(paths),
            "legacyTelemetryPhases": [
                "wait-pre-resume-authoritative-telemetry",
                "wait-final-authoritative-telemetry",
            ],
        }
    )
    calls: list[str] = []

    with pytest.raises(ValueError, match="operation spec"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=_callbacks(calls),
            operation_spec=replace(
                _spec(snapshot, strategy, paths),
                stage_plan_sha256=legacy_stage_plan_sha256,
            ),
        )

    assert calls == []
    assert incomplete_path.read_bytes() == incomplete_bytes
    assert completed_path.read_bytes() == completed_bytes


def test_noop_source_capability_mismatch_fails_before_callback(tmp_path: Path) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release=snapshot.release,
        target_release=snapshot.release,
        source_contract=snapshot.capability_contract,
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    with pytest.raises(ValueError, match="operation spec"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=_callbacks(calls),
            operation_spec=replace(
                _spec(snapshot, strategy, paths),
                source_capability_sha256="sha256:" + "0" * 64,
            ),
        )
    assert calls == []


def test_unknown_capability_transition_fails_closed() -> None:
    with pytest.raises(ValueError, match="no reviewed"):
        resolve_soperator_reconcile_strategy(
            current_release="3.0.0",
            target_release="4.1.7",
            source_contract="unknown-v1",
            target_contract="upstream-flux-v1",
        )


def test_forward_policy_runs_one_attempt_for_the_outer_supervisor_without_rollback(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    callbacks = _callbacks(calls)

    def apply_with_transient_failure() -> None:
        calls.append("apply-attempt")
        raise RuntimeError("transient apply failure")

    sleeps: list[float] = []
    events: list[str] = []
    with pytest.raises(RuntimeError, match="transient apply failure"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(callbacks, apply_desired_state=apply_with_transient_failure),
            operation_spec=_spec(snapshot, strategy, paths),
            emit=events.append,
            execution_policy=SoperatorReconcileExecutionPolicy(
                forward_until_complete=True,
                sleep=sleeps.append,
            ),
        )

    assert calls.count("apply-attempt") == 1
    assert "rollback-source" not in calls
    assert sleeps == []


def test_forward_policy_propagates_safety_pause_to_outer_supervisor(tmp_path: Path) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    callbacks = _callbacks(calls)

    def resolve_with_temporary_authority_gap() -> None:
        calls.append("sources-attempt")
        raise SoperatorSafetyPauseError("authority unavailable")

    events: list[str] = []
    with pytest.raises(SoperatorSafetyPauseError, match="authority unavailable"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(callbacks, resolve_sources=resolve_with_temporary_authority_gap),
            operation_spec=_spec(snapshot, strategy, paths),
            emit=events.append,
            execution_policy=SoperatorReconcileExecutionPolicy(forward_until_complete=True),
        )

    assert calls.count("sources-attempt") == 1


def test_forward_supervisor_stops_only_for_typed_main_component_failure(
    tmp_path: Path,
) -> None:
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
    )
    calls: list[str] = []
    callbacks = _callbacks(calls)

    def fail_main_component() -> None:
        calls.append("main-component-terminal")
        raise SoperatorMainWorkloadTerminalError(
            "frozen main workload failed",
            identity=SoperatorMainWorkloadIdentity(
                api_version="helm.toolkit.fluxcd.io/v2",
                kind="HelmRelease",
                namespace="flux-system",
                name="cxcli-soperator-fluxcd-slurm-cluster",
                source_kind="OCIRepository",
                source_name="soperator-upstream-umbrella",
                source_revision="sha256:" + "1" * 64,
                uid="main-uid",
                generation=2,
                observed_generation=2,
            ),
        )

    with pytest.raises(SoperatorMainWorkloadTerminalError, match="frozen main workload"):
        reconcile_soperator_release(
            paths=paths,
            target_ref="cluster-a",
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(callbacks, apply_desired_state=fail_main_component),
            operation_spec=_spec(snapshot, strategy, paths),
            execution_policy=SoperatorReconcileExecutionPolicy(
                forward_until_complete=True,
                retry_initial_seconds=0,
                retry_max_seconds=0,
                sleep=lambda _seconds: pytest.fail("terminal failure must not retry"),
            ),
        )

    receipt_path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert calls.count("main-component-terminal") == 1
    assert payload["status"] == "recovery-required"
