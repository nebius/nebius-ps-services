from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.deployment_state import digest
from nebius_cxcli.soperator_campaign_handoff import completed_release_handoff
from nebius_cxcli.soperator_jail_protection import freeze_jail_protection, rootfs_storage_authority
from nebius_cxcli.soperator_operation import SOPERATOR_RELEASE_INTENT_SCHEMA, SoperatorReleaseIntent
from nebius_cxcli.soperator_receipt_io import write_owner_only_json
from nebius_cxcli.soperator_rootfs_transition import plan_soperator_rootfs_transition
from test_deployment_campaign import paths
from test_deployment_jail_state import bundle, initial
from test_soperator_upstream_adapter import render_soperator_adapter_documents


def fixture(tmp_path, *, protected):
    local = paths(tmp_path)
    values = initial()
    switched, transition = plan_soperator_rootfs_transition(
        values, target_ref="cluster", layout="managed", legacy_pvc_resolver=lambda: "legacy"
    )
    intent = SimpleNamespace(
        target_ref="cluster",
        cluster_id="cluster-id",
        kubernetes_uid="cluster-uid",
        ownership="managed",
        target_release="4.1.8",
        checks_release_snapshot_sha256=digest("snapshot"),
        jail_protection=freeze_jail_protection(values),
    )
    config_transition = SimpleNamespace(
        status="applied",
        project_generation_sha256=digest("project"),
        evidence_sha256=digest("config-transition"),
    )
    store = SimpleNamespace(get=lambda _: config_transition)
    _, adapter = render_soperator_adapter_documents(switched)
    preflight = cli._build_soperator_rootfs_admission(
        target_image="registry.example.invalid/jail@" + digest("image"),
        persistent_paths=tuple(row["mountPath"] for row in values["jailPersistentMounts"]),
        live_pvc_name=transition["livePvcName"],
        live_pvc_uid="old-pvc-uid",
        target_pvc_name=transition["targetPvcName"],
        target_slot=transition["desiredActiveSlot"],
        target_storage_class_name="slurm-local-pv",
        target_provisioner="kubernetes.io/no-provisioner",
        target_capacity="2Ti",
        storage_authority=rootfs_storage_authority(adapter, transition["desiredActiveSlot"]),
        directory_identities=[],
        assert_authority=None,
    )
    admission = {
        "targetRef": "cluster",
        "targetRelease": "4.1.8",
        "clusterId": "cluster-id",
        "kubernetesUid": "cluster-uid",
        "releaseSnapshotSha256": intent.checks_release_snapshot_sha256,
        "projectGenerationSha256": config_transition.project_generation_sha256,
        "rootfsTransition": transition if protected else {"mode": "not-required"},
        "rootfsPreflight": preflight.as_payload() if protected else {"mode": "not-required"},
    }
    spec = {
        "intervention_generation": 0,
        "current_release": "4.1.7",
        "target_release": "4.1.8",
        "infrastructure_plan_sha256": digest("infra"),
        "admission_sha256": digest(admission),
    }
    operation = digest(spec)
    child = SoperatorReleaseIntent(
        schema=SOPERATOR_RELEASE_INTENT_SCHEMA,
        status="complete",
        target_ref="cluster",
        requested_selector="4.1.8",
        ownership="managed",
        strategy="protected-data-plane" if protected else "in-place",
        source_release="4.1.7",
        target_release="4.1.8",
        source_contract="protected-data-plane-v1" if protected else "upstream-flux-v1",
        target_contract="upstream-flux-v1",
        source_capability_sha256=digest("source"),
        target_capability_sha256=digest("target"),
        release_snapshot_sha256=intent.checks_release_snapshot_sha256,
        target_jail_image=preflight.target_image,
        target_jail_image_source="upstream-default",
        nebius_cluster_id="cluster-id",
        kubernetes_uid="cluster-uid",
        infrastructure_receipt_sha256=digest("infra"),
        operation_spec_sha256=operation,
    )
    write_owner_only_json(
        local.reports_dir / "soperator-release-intent-cluster.json", asdict(child)
    )
    write_owner_only_json(cli._soperator_upgrade_admission_path(local, "cluster"), admission)
    receipt = {"status": "complete", "target": {"ref": "cluster"}, "operation": {"spec": spec}}
    write_owner_only_json(local.reports_dir / "soperator-release-reconcile-child.json", receipt)
    payload = bundle(switched if protected else values).manifest["runtime_config"]
    payload["client_info"]["notifications"] = {}
    local.config_path.write_text(yaml.safe_dump(payload))
    if protected:
        expected = cli._soperator_rootfs_admission_stage_evidence(preflight)
        materialization = {
            "schema": "nebius-cxcli.soperator-rootfs-materialization.v1",
            "image": preflight.target_image,
            "slot": preflight.target_slot,
            "pvcName": preflight.target_pvc_name,
            "pvcUid": "new-pvc-uid",
            "pvUid": "new-pv-uid",
            "admissionReceiptSha256": preflight.receipt_sha256,
            "manifestSha256": digest("manifest"),
            "entryCount": 1,
            "populateJobUid": "populate-uid",
            "populateWorkloadSha256": digest("populate"),
            "inventoryJobUid": "inventory-uid",
            "inventoryWorkloadSha256": digest("inventory"),
        }
        materialization["receiptSha256"] = digest(materialization)
        journal = {
            "schema": "nebius-cxcli.soperator-recovery-journal.v4",
            "status": "complete",
            "operationId": operation,
            "clusterId": "cluster-id",
            "kubernetesUid": "cluster-uid",
            "sourceRelease": "4.1.7",
            "targetRelease": "4.1.8",
            "targetJailImage": preflight.target_image,
            "infrastructureReceiptSha256": digest("infra"),
            "rootfsClassificationSha256": preflight.receipt_sha256,
            "stages": {
                "rootfs-admission-decision": {
                    "status": "complete",
                    "intent": expected,
                    "evidence": expected,
                },
                "rootfs-passive-target-identity": {
                    "status": "complete",
                    "intent": {
                        "pvcName": preflight.target_pvc_name,
                        "targetSlot": preflight.target_slot,
                    },
                    "evidence": {"pvcUid": "new-pvc-uid", "pvUid": "new-pv-uid"},
                },
                "rootfs-passive-target-preflight": {
                    "status": "complete",
                    "intent": {
                        "pvcUid": "new-pvc-uid",
                        "admissionReceiptSha256": preflight.receipt_sha256,
                        "targetImage": preflight.target_image,
                    },
                    "evidence": {
                        "status": "empty-and-unconsumed",
                        "consumerStatus": "unconsumed",
                        "consumerCount": 0,
                        "filesystemBootstrap": "none",
                        "controlMetadataPolicy": "reserved-nebius-cxcli-subtree",
                        "manifestSha256": digest("empty"),
                        "jobUid": "preflight-job",
                        "admittedWorkloadSha256": digest("preflight"),
                    },
                },
                "rootfs-passive-target-populate": {
                    "status": "complete",
                    "intent": {"workloadSha256": digest("populate")},
                    "evidence": {
                        "jobUid": "populate-uid",
                        "admittedWorkloadSha256": digest("populate"),
                    },
                },
                "rootfs-passive-target-inventory": {
                    "status": "complete",
                    "intent": {"workloadSha256": digest("inventory")},
                    "evidence": {
                        "jobUid": "inventory-uid",
                        "admittedWorkloadSha256": digest("inventory"),
                        "manifestSha256": digest("manifest"),
                        "materialization": materialization,
                    },
                },
            },
        }
        write_owner_only_json(
            local.reports_dir / f"soperator-recovery-{operation.removeprefix('sha256:')[:20]}.json",
            journal,
        )
    return local, intent, store, child, admission


@pytest.mark.parametrize("protected", [False, True])
def test_completed_child_handoff_reuses_exact_output_without_reexecution(tmp_path, protected):
    local, intent, store, child, _ = fixture(tmp_path, protected=protected)
    first = completed_release_handoff(
        cli,
        paths=local,
        target={"target_ref": "cluster", "flux_dir": str(local.flux_dir / "targets/cluster")},
        intent=intent,
        config_store=store,
    )
    repeated = completed_release_handoff(
        cli,
        paths=local,
        target={"target_ref": "cluster", "flux_dir": str(local.flux_dir / "targets/cluster")},
        intent=intent,
        config_store=store,
    )
    assert first == repeated
    assert first["operationSpecSha256"] == child.operation_spec_sha256
    assert ("materialization" in first) is protected
    if protected:
        assert first["materialization"]["targetPvcUid"] == "new-pvc-uid"
    else:
        assert first["jailProtection"] == intent.jail_protection


def test_handoff_rejects_missing_materialization_and_changed_child_admission(tmp_path):
    local, intent, store, child, admission = fixture(tmp_path, protected=True)
    target = {"target_ref": "cluster", "flux_dir": str(local.flux_dir / "targets/cluster")}
    journal = (
        local.reports_dir
        / f"soperator-recovery-{child.operation_spec_sha256.removeprefix('sha256:')[:20]}.json"
    )
    journal.unlink()
    with pytest.raises(RuntimeError, match="owner-only"):
        completed_release_handoff(
            cli, paths=local, target=target, intent=intent, config_store=store
        )
    admission["rootfsTransition"]["targetPvcName"] = "redirect"
    write_owner_only_json(cli._soperator_upgrade_admission_path(local, "cluster"), admission)
    with pytest.raises(RuntimeError, match="not bound"):
        completed_release_handoff(
            cli, paths=local, target=target, intent=intent, config_store=store
        )


def test_unfinished_child_does_not_claim_handoff(tmp_path):
    local, intent, store, child, _ = fixture(tmp_path, protected=False)
    write_owner_only_json(
        local.reports_dir / "soperator-release-intent-cluster.json",
        asdict(replace(child, status="active")),
    )
    assert (
        completed_release_handoff(
            cli,
            paths=local,
            target={"target_ref": "cluster", "flux_dir": str(local.flux_dir / "targets/cluster")},
            intent=intent,
            config_store=store,
        )
        is None
    )
