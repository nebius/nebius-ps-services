from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import replace

import pytest

from nebius_cxcli.soperator_protected_data_plane import (
    bind_protected_job_authority,
    bind_protected_workload_identity,
    build_protected_data_plane_receipt,
    parse_rootfs_inventory_log,
    passive_rootfs_recycle_resume_policy,
    protected_data_plane_receipt_from_payload,
    protected_job_pod_identity,
    protected_workload_identity,
    readmit_unbound_protected_data_plane_baseline,
    resolve_admitted_protected_data_plane_baseline,
    retention_patch_contract,
    rootfs_cleanup_job_manifest,
    rootfs_inventory_evidence_sha256,
    rootfs_inventory_job_manifest,
    validate_passive_rootfs_preflight_evidence,
    verify_passive_rootfs_consumers,
    verify_passive_rootfs_precondition,
    verify_protected_data_plane_handoff,
    verify_rootfs_persistent_consumers,
)
from nebius_cxcli.soperator_upgrade_safety import ProtectedCustomerState
from soperator_fixtures import sample_infrastructure_receipt


def _resource(kind: str, name: str, uid: str, **extra: object) -> dict[str, object]:
    return {
        "kind": kind,
        "namespace": "" if kind == "PersistentVolume" else "soperator",
        "name": name,
        "uid": uid,
        "resource_version": "10",
        "labels": {},
        **extra,
    }


def _state() -> ProtectedCustomerState:
    pvc_rows = [
        ("jail-pvc", "pv-jail"),
        ("controller-spool-controller-0", "pv-spool"),
        ("storage-soperator-acct-db-0", "pv-accounting"),
    ]
    pvcs = [
        _resource(
            "PersistentVolumeClaim",
            name,
            f"uid-{name}",
            phase="Bound",
            volume_name=pv,
        )
        for name, pv in pvc_rows
    ]
    pvs = [
        _resource(
            "PersistentVolume",
            pv,
            f"uid-{pv}",
            claim={"namespace": "soperator", "name": pvc},
            persistent_volume_reclaim_policy="Delete" if pvc == "jail-pvc" else "Retain",
        )
        for pvc, pv in pvc_rows
    ]
    secrets = [
        _resource("Secret", "mariadb-password", "uid-secret", type="Opaque"),
        _resource(
            "Secret",
            "soperator-slurmdbd-configs",
            "uid-generated-config",
            type="Opaque",
        ),
        _resource(
            "Secret",
            "sh.helm.release.v1.soperator.v1",
            "uid-helm",
            type="helm.sh/release.v1",
        ),
    ]
    return ProtectedCustomerState(
        target_ref="cluster-a",
        namespace="soperator",
        captured_at="2026-01-01T00:00:00Z",
        sections={
            "pvcs": {"available": True, "items": pvcs},
            "pvs": {"available": True, "items": pvs},
            "secrets": {"available": True, "items": secrets},
            "slurm_runtime": {
                "home_mount": {
                    "available": True,
                    "stdout_sha256": "sha256:" + "a" * 64,
                }
            },
        },
    )


def _receipt(state: ProtectedCustomerState | None = None):
    infrastructure = sample_infrastructure_receipt()
    assert infrastructure.storage.sfs is not None
    infrastructure = replace(
        infrastructure,
        storage=replace(
            infrastructure.storage,
            sfs=replace(
                infrastructure.storage.sfs,
                filesystems=tuple(
                    replace(item, pvc_names=("jail-pvc",)) if item.role == "jail" else item
                    for item in infrastructure.storage.sfs.filesystems
                ),
            ),
        ),
    )
    return build_protected_data_plane_receipt(
        state=state or _state(),
        target_ref="cluster-a",
        ownership="managed",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        infrastructure=infrastructure,
    )


def test_receipt_binds_all_protected_volumes_and_safe_secret_identity() -> None:
    receipt = _receipt()

    assert {item.role for item in receipt.volumes} == {
        "jail",
        "controller-spool",
        "accounting",
    }
    assert [item.name for item in receipt.protected_objects] == ["mariadb-password"]
    patches = retention_patch_contract(receipt)
    assert len(patches) == 1
    assert patches[0]["pvName"] == "pv-jail"
    operations = patches[0]["operations"]
    assert operations[0] == {
        "op": "test",
        "path": "/metadata/uid",
        "value": "uid-pv-jail",
    }
    assert operations[-1]["value"] == "Retain"


def test_protected_data_plane_payload_round_trips_nested_infrastructure_receipt() -> None:
    receipt = _receipt()

    payload = json.loads(json.dumps(receipt.as_payload()))
    assert protected_data_plane_receipt_from_payload(payload) == receipt
    historical = json.loads(json.dumps(receipt.as_payload()))
    infrastructure = historical["infrastructure"]
    assert isinstance(infrastructure, dict)
    infrastructure.pop("receipt_sha256")
    assert protected_data_plane_receipt_from_payload(historical) == receipt


def test_admitted_baseline_recovery_allows_semantically_safe_observation_churn() -> None:
    admitted = _receipt()
    first_volume = admitted.volumes[0]
    observed = replace(
        admitted,
        state_sha256="sha256:" + "b" * 64,
        volumes=(
            replace(
                first_volume,
                pvc=replace(first_volume.pvc, resource_version="11"),
                pv=replace(first_volume.pv, resource_version="12"),
            ),
            *admitted.volumes[1:],
        ),
    )
    historical_journal = json.loads(json.dumps(admitted.as_payload()))
    infrastructure = historical_journal["infrastructure"]
    assert isinstance(infrastructure, dict)
    infrastructure.pop("receipt_sha256")

    baseline, normalize = resolve_admitted_protected_data_plane_baseline(
        admitted_receipt_sha256=admitted.receipt_sha256,
        journal_receipt_payload=historical_journal,
        observed_receipt=observed,
    )

    assert baseline == admitted
    assert baseline.receipt_sha256 != observed.receipt_sha256
    assert normalize is True


def test_admitted_baseline_payload_can_seed_a_missing_journal() -> None:
    admitted = _receipt()
    observed = replace(admitted, state_sha256="sha256:" + "b" * 64)

    baseline, normalize = resolve_admitted_protected_data_plane_baseline(
        admitted_receipt_sha256=admitted.receipt_sha256,
        admitted_receipt_payload=admitted.as_payload(),
        observed_receipt=observed,
    )

    assert baseline == admitted
    assert normalize is False


def test_admitted_baseline_rejects_a_journal_outside_the_frozen_digest() -> None:
    admitted = _receipt()
    different = replace(admitted, state_sha256="sha256:" + "b" * 64)

    with pytest.raises(RuntimeError, match="journal differs from the admitted preimage"):
        resolve_admitted_protected_data_plane_baseline(
            admitted_receipt_sha256=admitted.receipt_sha256,
            journal_receipt_payload=different.as_payload(),
            observed_receipt=admitted,
        )


def test_unbound_baseline_readmission_requires_no_scheduling_mutation() -> None:
    original_admission = _receipt()
    journal_baseline = replace(
        original_admission,
        state_sha256="sha256:" + "b" * 64,
    )
    observed = replace(
        journal_baseline,
        state_sha256="sha256:" + "c" * 64,
    )

    recovered = readmit_unbound_protected_data_plane_baseline(
        admitted_receipt_sha256=original_admission.receipt_sha256,
        journal_receipt_payload=journal_baseline.as_payload(),
        observed_receipt=observed,
        frozen_infrastructure=original_admission.infrastructure,
        operation_spec_sha256="",
        scheduling_actions=(),
    )

    assert recovered == journal_baseline
    with pytest.raises(RuntimeError, match="scheduling mutations"):
        readmit_unbound_protected_data_plane_baseline(
            admitted_receipt_sha256=original_admission.receipt_sha256,
            journal_receipt_payload=journal_baseline.as_payload(),
            observed_receipt=observed,
            frozen_infrastructure=original_admission.infrastructure,
            operation_spec_sha256="",
            scheduling_actions=({"action": "partitions-paused"},),
        )
    with pytest.raises(RuntimeError, match="bound operation"):
        readmit_unbound_protected_data_plane_baseline(
            admitted_receipt_sha256=original_admission.receipt_sha256,
            journal_receipt_payload=journal_baseline.as_payload(),
            observed_receipt=observed,
            frozen_infrastructure=original_admission.infrastructure,
            operation_spec_sha256="sha256:" + "d" * 64,
            scheduling_actions=(),
        )


def test_rootfs_job_rejects_malformed_digest_reference() -> None:
    with pytest.raises(ValueError, match="digest-addressed"):
        rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image=("registry.example.invalid/rootfs@sha256:" + "a" * 64 + "-suffix"),
            pvc_name="jail-rootfs-slot-b-pvc",
        )


def test_job_pod_identity_allows_only_controller_and_standard_pod_defaults() -> None:
    operation_id = "sha256:" + "b" * 64
    requested = bind_protected_job_authority(
        rootfs_inventory_job_manifest(
            namespace="soperator",
            name="cxcli-rootfs-inventory",
            image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
            pvc_name="jail-rootfs-slot-b-pvc",
        ),
        operation_id=operation_id,
        fence_epoch=1,
        pvc_uid="pvc-uid-a",
    )
    job = copy.deepcopy(requested)
    assert requested["metadata"]["labels"]["nebius-cxcli/operation-id"] == "b" * 63
    assert (
        requested["spec"]["template"]["metadata"]["labels"]["nebius-cxcli/operation-id"] == "b" * 63
    )
    job["metadata"]["uid"] = "job-uid"
    job["spec"]["selector"] = {
        "matchLabels": {"batch.kubernetes.io/controller-uid": "controller-uid"}
    }
    job_labels = job["spec"]["template"]["metadata"]["labels"]
    job_labels.update(
        {
            "batch.kubernetes.io/controller-uid": "controller-uid",
            "batch.kubernetes.io/job-name": "cxcli-rootfs-inventory",
        }
    )
    job["spec"]["template"]["metadata"]["creationTimestamp"] = None
    requested_identity = protected_workload_identity(requested)
    admitted_identity = protected_workload_identity(job)
    job = bind_protected_workload_identity(
        job,
        requested_workload_sha256=requested_identity.workload_sha256,
        admitted_workload_sha256=admitted_identity.workload_sha256,
    )
    pod = {
        "metadata": {
            "uid": "pod-uid",
            "labels": copy.deepcopy(job_labels),
            "ownerReferences": [
                {
                    "kind": "Job",
                    "uid": "job-uid",
                    "controller": True,
                }
            ],
        },
        "spec": copy.deepcopy(job["spec"]["template"]["spec"]),
    }
    pod["spec"].update(
        {
            "enableServiceLinks": True,
            "nodeName": "worker-0",
            "preemptionPolicy": "PreemptLowerPriority",
            "priority": 0,
            "serviceAccount": "default",
            "serviceAccountName": "default",
            "tolerations": [
                {
                    "key": "node.kubernetes.io/not-ready",
                    "operator": "Exists",
                    "effect": "NoExecute",
                    "tolerationSeconds": 300,
                },
                {
                    "key": "node.kubernetes.io/unreachable",
                    "operator": "Exists",
                    "effect": "NoExecute",
                    "tolerationSeconds": 300,
                },
            ],
        }
    )

    assert protected_job_pod_identity(job=job, pod=pod).startswith("sha256:")

    for field, value in (
        ("enableServiceLinks", False),
        ("preemptionPolicy", "Never"),
        ("priority", 1),
    ):
        changed = copy.deepcopy(pod)
        changed["spec"][field] = value
        with pytest.raises(RuntimeError, match=rf"changed workload identity at .*spec\.{field}"):
            protected_job_pod_identity(job=job, pod=changed)

    pod["spec"]["containers"].append(
        {"name": "unexpected-sidecar", "image": "example.invalid/sidecar:latest"}
    )
    with pytest.raises(RuntimeError, match="changed workload identity"):
        protected_job_pod_identity(job=job, pod=pod)


def test_handoff_requires_same_cluster_volume_and_secret_identities() -> None:
    before = _receipt()
    after = replace(
        before,
        volumes=tuple(replace(item, reclaim_policy="Retain") for item in before.volumes),
    )

    result = verify_protected_data_plane_handoff(before=before, after=after)

    assert result["status"] == "verified"


def test_handoff_allows_only_explicit_home_mount_transport_transition() -> None:
    before = _receipt()
    after = replace(
        before,
        home_mount_sha256="sha256:" + "0" * 64,
        volumes=tuple(replace(item, reclaim_policy="Retain") for item in before.volumes),
    )

    with pytest.raises(RuntimeError, match="home_mount_sha256"):
        verify_protected_data_plane_handoff(before=before, after=after)

    result = verify_protected_data_plane_handoff(
        before=before,
        after=after,
        allow_home_mount_transport_transition=True,
    )

    assert result["status"] == "verified"
    assert result["homeMountTransport"] == "persistent-pvc-consumer-proof-required"


def test_bound_baseline_recovery_preserves_admission_while_allowing_home_transport() -> None:
    before = _receipt()
    observed = replace(
        before,
        home_mount_sha256="sha256:" + "0" * 64,
        volumes=tuple(replace(item, reclaim_policy="Retain") for item in before.volumes),
    )

    baseline, normalize = resolve_admitted_protected_data_plane_baseline(
        admitted_receipt_sha256=before.receipt_sha256,
        admitted_receipt_payload=before.as_payload(),
        observed_receipt=observed,
        allow_home_mount_transport_transition=True,
    )

    assert baseline == before
    assert baseline.home_mount_sha256 == before.home_mount_sha256
    assert normalize is False


def test_handoff_allows_target_added_secrets_but_preserves_existing_content() -> None:
    before = _receipt()
    added = replace(
        before.protected_objects[0],
        name="target-generated-secret",
        uid="target-generated-uid",
    )
    after = replace(
        before,
        volumes=tuple(replace(item, reclaim_policy="Retain") for item in before.volumes),
        protected_objects=(*before.protected_objects, added),
    )

    assert verify_protected_data_plane_handoff(before=before, after=after)["status"] == "verified"

    changed = replace(
        after,
        protected_objects=(
            replace(before.protected_objects[0], content_sha256="sha256:" + "0" * 64),
            added,
        ),
    )
    with pytest.raises(RuntimeError, match="Secret identities"):
        verify_protected_data_plane_handoff(before=before, after=changed)


def test_handoff_projects_out_generated_slurmdbd_config_from_older_receipt() -> None:
    before = _receipt()
    generated_config = replace(
        before.protected_objects[0],
        name="soperator-slurmdbd-configs",
        uid="generated-config-uid",
    )
    before = replace(
        before,
        protected_objects=(*before.protected_objects, generated_config),
    )
    after = replace(
        before,
        volumes=tuple(replace(item, reclaim_policy="Retain") for item in before.volumes),
        protected_objects=(
            before.protected_objects[0],
            replace(generated_config, content_sha256="sha256:" + "0" * 64),
        ),
    )

    assert verify_protected_data_plane_handoff(before=before, after=after)["status"] == "verified"


def test_handoff_rejects_recreated_pvc() -> None:
    before = _receipt()
    first = before.volumes[0]
    after = replace(
        before,
        volumes=(
            replace(first, pvc=replace(first.pvc, uid="replacement"), reclaim_policy="Retain"),
            *before.volumes[1:],
        ),
    )

    with pytest.raises(RuntimeError, match="PV/PVC identity"):
        verify_protected_data_plane_handoff(before=before, after=after)


def test_receipt_uses_exact_infrastructure_binding_when_role_names_are_ambiguous() -> None:
    state = _state()
    sections = dict(state.sections)
    pvcs = dict(sections["pvcs"])
    pvcs["items"] = [
        *pvcs["items"],
        _resource(
            "PersistentVolumeClaim",
            "jail-secondary-pvc",
            "uid-secondary",
            phase="Bound",
            volume_name="pv-secondary",
        ),
    ]
    sections["pvcs"] = pvcs

    receipt = _receipt(replace(state, sections=sections))

    assert next(item.pvc.name for item in receipt.volumes if item.role == "jail") == "jail-pvc"


def test_receipt_uses_frozen_volume_when_target_bindings_share_the_jail_sfs() -> None:
    baseline = _receipt()
    state = _state()
    sections = dict(state.sections)
    pvcs = dict(sections["pvcs"])
    pvcs["items"] = [
        *pvcs["items"],
        _resource(
            "PersistentVolumeClaim",
            "jail-rootfs-slot-a-pvc",
            "uid-slot-a-pvc",
            phase="Bound",
            volume_name="jail-rootfs-slot-a-pv",
        ),
    ]
    sections["pvcs"] = pvcs
    pvs = dict(sections["pvs"])
    pvs["items"] = [
        *pvs["items"],
        _resource(
            "PersistentVolume",
            "jail-rootfs-slot-a-pv",
            "uid-slot-a-pv",
            claim={"namespace": "soperator", "name": "jail-rootfs-slot-a-pvc"},
            persistent_volume_reclaim_policy="Retain",
        ),
    ]
    sections["pvs"] = pvs
    assert baseline.infrastructure.storage.sfs is not None
    expanded = replace(
        baseline.infrastructure.storage.sfs.filesystems[-1],
        pv_names=("jail-pv", "jail-rootfs-slot-a-pv"),
        pvc_names=("jail-pvc", "jail-rootfs-slot-a-pvc"),
    )
    infrastructure = replace(
        baseline.infrastructure,
        storage=replace(
            baseline.infrastructure.storage,
            sfs=replace(
                baseline.infrastructure.storage.sfs,
                filesystems=(
                    *baseline.infrastructure.storage.sfs.filesystems[:-1],
                    expanded,
                ),
            ),
        ),
    )

    receipt = build_protected_data_plane_receipt(
        state=replace(state, sections=sections),
        target_ref="cluster-a",
        ownership="managed",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        infrastructure=infrastructure,
        expected_volumes=baseline.volumes,
    )

    assert next(item.pvc.name for item in receipt.volumes if item.role == "jail") == "jail-pvc"


def test_receipt_rejects_missing_exact_infrastructure_binding() -> None:
    state = _state()
    sections = dict(state.sections)
    pvcs = dict(sections["pvcs"])
    pvcs["items"] = [item for item in pvcs["items"] if item["name"] != "jail-pvc"]
    sections["pvcs"] = pvcs

    with pytest.raises(RuntimeError, match="jail PVC identity is ambiguous"):
        _receipt(replace(state, sections=sections))


def test_rootfs_inventory_jobs_are_digest_bound_and_read_only_by_default() -> None:
    image = "registry.example.invalid/rootfs@sha256:" + "a" * 64
    inventory = rootfs_inventory_job_manifest(
        namespace="soperator",
        name="inventory-a",
        image=image,
        pvc_name="jail-pvc",
    )
    cleanup = rootfs_cleanup_job_manifest(
        namespace="soperator",
        name="cleanup-a",
        image=image,
        pvc_name="slot-b-pvc",
        expected_inventory_sha256="sha256:" + "d" * 64,
    )

    inventory_mount = inventory["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]
    cleanup_mount = cleanup["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]
    inventory_command = inventory["spec"]["template"]["spec"]["containers"][0]["command"]
    cleanup_command = cleanup["spec"]["template"]["spec"]["containers"][0]["command"]
    assert inventory_mount["readOnly"] is True
    assert cleanup_mount["readOnly"] is False
    assert inventory_command[:2] == ["/bin/sh", "-c"]
    assert cleanup_command[:2] == ["/bin/sh", "-c"]
    assert "pipefail" not in inventory_command[2]
    assert "< <(" not in inventory_command[2]
    assert "read -r -d" not in inventory_command[2]
    assert "base64 -w0" not in inventory_command[2]
    assert "! -path './.nebius-cxcli'" in inventory_command[2]
    assert "! -path './.nebius-cxcli/*'" in inventory_command[2]
    assert "! -path './.nebius-cxcli'" in cleanup_command[2]
    assert "! -path './.nebius-cxcli/*'" in cleanup_command[2]
    assert "find . -xdev -mindepth 1" in inventory_command[2]
    assert "-exec sh -c" in inventory_command[2]
    assert "passive rootfs inventory changed before cleanup" in cleanup_command[2]
    assert "sha256:" + "d" * 64 in cleanup_command[2]


def test_passive_recycle_resume_never_creates_cleanup_after_inventory_drift() -> None:
    pre_clean = "sha256:" + "a" * 64
    pre_clean_inventory = "sha256:" + "c" * 64
    changed = "sha256:" + "b" * 64

    initial = passive_rootfs_recycle_resume_policy(
        stage=None,
        current_manifest_sha256=pre_clean,
        current_inventory_sha256=pre_clean_inventory,
    )
    unchanged_resume = passive_rootfs_recycle_resume_policy(
        stage={
            "status": "intent",
            "intent": {
                "preCleanManifestSha256": pre_clean,
                "preCleanInventorySha256": pre_clean_inventory,
            },
        },
        current_manifest_sha256=pre_clean,
        current_inventory_sha256=pre_clean_inventory,
    )
    drifted_resume = passive_rootfs_recycle_resume_policy(
        stage={
            "status": "intent",
            "intent": {
                "preCleanManifestSha256": pre_clean,
                "preCleanInventorySha256": pre_clean_inventory,
            },
        },
        current_manifest_sha256=changed,
        current_inventory_sha256=changed,
    )
    completed = passive_rootfs_recycle_resume_policy(
        stage={
            "status": "complete",
            "intent": {
                "preCleanManifestSha256": pre_clean,
                "preCleanInventorySha256": pre_clean_inventory,
            },
        },
        current_manifest_sha256=changed,
        current_inventory_sha256=changed,
    )

    assert initial.pre_clean_manifest_sha256 == pre_clean
    assert initial.pre_clean_inventory_sha256 == pre_clean_inventory
    assert initial.allow_job_create is True
    assert unchanged_resume.allow_job_create is True
    assert drifted_resume.allow_job_create is False
    assert completed.allow_job_create is False


def test_passive_recycle_resume_rejects_incomplete_journal_intent() -> None:
    with pytest.raises(RuntimeError, match="pre-clean manifest digest"):
        passive_rootfs_recycle_resume_policy(
            stage={"status": "intent", "intent": {}},
            current_manifest_sha256="sha256:" + "a" * 64,
            current_inventory_sha256="sha256:" + "b" * 64,
        )


def test_inventory_evidence_digest_matches_the_sorted_job_line_stream() -> None:
    output = "".join(
        (
            f"f\tsha256:{'b' * 64}\tsha256:{'c' * 64}\taG9tZS9maWxlX2I=\n",
            f"d\tsha256:{'d' * 64}\tsha256:{'e' * 64}\taG9tZQ==\n",
        )
    )
    manifest = parse_rootfs_inventory_log(
        image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
        output=output,
    )
    expected = (
        "sha256:"
        + hashlib.sha256("".join(sorted(output.splitlines(keepends=True))).encode()).hexdigest()
    )

    assert rootfs_inventory_evidence_sha256(manifest) == expected


def test_rootfs_inventory_log_contains_no_file_contents() -> None:
    path = base64.b64encode(b"home/customer/result.txt").decode()
    output = f"f\tsha256:{'b' * 64}\tsha256:{'c' * 64}\t{path}\n"
    manifest = parse_rootfs_inventory_log(
        image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
        output=output,
    )

    assert manifest.entries[0].path == "/home/customer/result.txt"
    assert manifest.entries[0].digest == "sha256:" + "b" * 64
    assert manifest.entries[0].metadata_digest == "sha256:" + "c" * 64


def _inventory_line(kind: str, path: str, digest: str, metadata: str) -> str:
    encoded = base64.b64encode(path.encode()).decode()
    return f"{kind}\tsha256:{digest * 64}\tsha256:{metadata * 64}\t{encoded}\n"


def _persistent_consumer_inventory(*, worker_home_pvc: str = "jail-home-pvc") -> dict:
    sources = [
        {
            "name": "jail-home",
            "persistentVolumeClaim": {"claimName": "jail-home-pvc"},
        },
        {
            "name": "jail-data",
            "persistentVolumeClaim": {"claimName": "jail-data-pvc"},
        },
    ]
    cluster_mounts = [
        {"mountPath": "/home", "volumeSourceName": "jail-home"},
        {"mountPath": "/data", "volumeSourceName": "jail-data"},
    ]
    worker_mounts = [
        {
            "mountPath": "/home",
            "volumeSource": {"persistentVolumeClaim": {"claimName": worker_home_pvc}},
        },
        {
            "mountPath": "/data",
            "volumeSource": {"persistentVolumeClaim": {"claimName": "jail-data-pvc"}},
        },
    ]
    return {
        "items": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "cluster"},
                "spec": {
                    "volumeSources": sources,
                    "slurmNodes": {
                        "controller": {"volumes": {"jail": {}}},
                        "login": {"volumes": {"jail": {}, "jailSubMounts": cluster_mounts}},
                    },
                },
            },
            {
                "kind": "NodeSet",
                "metadata": {"name": "worker"},
                "spec": {"slurmd": {"volumes": {"jail": {}, "jailSubMounts": worker_mounts}}},
            },
        ]
    }


def test_rootfs_persistent_consumers_require_exact_path_to_pvc_mapping() -> None:
    receipt = verify_rootfs_persistent_consumers(
        soperator_resources=_persistent_consumer_inventory(),
        expected_mounts={"/home": "jail-home-pvc", "/data": "jail-data-pvc"},
    )

    assert receipt == {
        "persistentConsumerStatus": "exact",
        "persistentConsumerCount": 2,
        "persistentMountCount": 2,
    }


def test_rootfs_persistent_consumers_reject_one_wrong_worker_pvc() -> None:
    with pytest.raises(RuntimeError, match="path-to-PVC mapping"):
        verify_rootfs_persistent_consumers(
            soperator_resources=_persistent_consumer_inventory(worker_home_pvc="wrong-home-pvc"),
            expected_mounts={"/home": "jail-home-pvc", "/data": "jail-data-pvc"},
        )


def test_passive_rootfs_precondition_requires_empty_unconsumed_slot() -> None:
    image = "registry.example.invalid/rootfs@sha256:" + "a" * 64
    manifest = parse_rootfs_inventory_log(image=image, output="")

    result = verify_passive_rootfs_precondition(
        pvc_name="jail-rootfs-slot-b-pvc",
        manifest=manifest,
        pods={"items": []},
        soperator_resources={"items": []},
    )

    assert result["status"] == "empty-and-unconsumed"
    assert result["consumerCount"] == 0
    assert result["filesystemBootstrap"] == "none"
    assert result["controlMetadataPolicy"] == "reserved-nebius-cxcli-subtree"


def test_passive_rootfs_resume_rejects_completed_stage_without_exact_evidence() -> None:
    with pytest.raises(RuntimeError, match="does not prove"):
        validate_passive_rootfs_preflight_evidence({})

    evidence = validate_passive_rootfs_preflight_evidence(
        {
            "status": "empty-and-unconsumed",
            "manifestSha256": "sha256:" + "a" * 64,
            "filesystemBootstrap": "none",
            "controlMetadataPolicy": "reserved-nebius-cxcli-subtree",
            "consumerStatus": "unconsumed",
            "consumerCount": 0,
            "jobUid": "job-a",
            "admittedWorkloadSha256": "sha256:" + "b" * 64,
        }
    )
    assert evidence["jobUid"] == "job-a"


def test_passive_rootfs_precondition_rejects_existing_content() -> None:
    path = base64.b64encode(b"etc/customer.conf").decode()
    manifest = parse_rootfs_inventory_log(
        image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
        output=f"f\tsha256:{'b' * 64}\tsha256:{'c' * 64}\t{path}\n",
    )

    with pytest.raises(RuntimeError, match="not empty"):
        verify_passive_rootfs_precondition(
            pvc_name="jail-rootfs-slot-b-pvc",
            manifest=manifest,
            pods={"items": []},
            soperator_resources={"items": []},
        )


def test_passive_rootfs_precondition_accepts_only_empty_filesystem_lost_found() -> None:
    path = base64.b64encode(b"lost+found").decode()
    manifest = parse_rootfs_inventory_log(
        image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
        output=f"d\tsha256:{'b' * 64}\tsha256:{'c' * 64}\t{path}\n",
    )

    result = verify_passive_rootfs_precondition(
        pvc_name="jail-rootfs-slot-b-pvc",
        manifest=manifest,
        pods={"items": []},
        soperator_resources={"items": []},
    )

    assert result["status"] == "empty-and-unconsumed"
    assert result["filesystemBootstrap"] == "empty-lost-found-directory"
    assert result["manifestSha256"] == manifest.manifest_sha256


@pytest.mark.parametrize(
    ("entries", "expected_count"),
    [
        ([("f", "lost+found")], 1),
        ([("l", "lost+found")], 1),
        ([("d", "lost+found"), ("f", "lost+found/customer")], 2),
        ([("d", "lost+found"), ("f", "customer")], 2),
    ],
)
def test_passive_rootfs_precondition_rejects_nonempty_lost_found_shapes(
    entries: list[tuple[str, str]], expected_count: int
) -> None:
    output = "".join(
        f"{kind}\tsha256:{'b' * 64}\tsha256:{'c' * 64}\t"
        f"{base64.b64encode(path.encode()).decode()}\n"
        for kind, path in entries
    )
    manifest = parse_rootfs_inventory_log(
        image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
        output=output,
    )

    with pytest.raises(RuntimeError, match=rf"{expected_count} unexpected entries"):
        verify_passive_rootfs_precondition(
            pvc_name="jail-rootfs-slot-b-pvc",
            manifest=manifest,
            pods={"items": []},
            soperator_resources={"items": []},
        )


def test_passive_rootfs_precondition_rejects_pod_or_cr_consumers() -> None:
    manifest = parse_rootfs_inventory_log(
        image="registry.example.invalid/rootfs@sha256:" + "a" * 64,
        output="",
    )
    pods = {
        "items": [
            {
                "kind": "Pod",
                "metadata": {"namespace": "soperator", "name": "worker-0"},
                "spec": {
                    "volumes": [{"persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}}]
                },
            }
        ]
    }
    resources = {
        "items": [
            {
                "kind": "NodeSet",
                "metadata": {"namespace": "soperator", "name": "worker"},
                "spec": {
                    "slurmd": {
                        "volumes": {
                            "jail": {
                                "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}
                            }
                        }
                    }
                },
            }
        ]
    }

    with pytest.raises(RuntimeError, match="Pod:soperator/worker-0"):
        verify_passive_rootfs_precondition(
            pvc_name="jail-rootfs-slot-b-pvc",
            manifest=manifest,
            pods=pods,
            soperator_resources=resources,
        )


def test_fresh_consumer_census_allows_only_the_exact_operation_jobs() -> None:
    operation_id = "sha256:" + "a" * 64
    pod = {
        "kind": "Pod",
        "metadata": {
            "namespace": "soperator",
            "name": "cxcli-rootfs-inventory",
            "labels": {"nebius-cxcli/operation-id": "a" * 63},
        },
        "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}}]},
    }

    result = verify_passive_rootfs_consumers(
        pvc_name="jail-rootfs-slot-b-pvc",
        pods={"items": [pod]},
        soperator_resources={"items": []},
        allowed_operation_id=operation_id,
    )
    assert result == {"consumerStatus": "unconsumed", "consumerCount": 0}
    precondition = verify_passive_rootfs_precondition(
        pvc_name="jail-rootfs-slot-b-pvc",
        manifest=parse_rootfs_inventory_log(
            image="registry.example.invalid/rootfs@sha256:" + "b" * 64,
            output="",
        ),
        pods={"items": [pod]},
        soperator_resources={"items": []},
        allowed_operation_id=operation_id,
    )
    assert precondition["status"] == "empty-and-unconsumed"

    pod["metadata"]["labels"]["nebius-cxcli/operation-id"] = "foreign"
    with pytest.raises(RuntimeError, match="cxcli-rootfs-inventory"):
        verify_passive_rootfs_consumers(
            pvc_name="jail-rootfs-slot-b-pvc",
            pods={"items": [pod]},
            soperator_resources={"items": []},
            allowed_operation_id=operation_id,
        )


@pytest.mark.parametrize("phase", ["Succeeded", "Failed"])
def test_fresh_consumer_census_ignores_terminal_foreign_pods(phase: str) -> None:
    pod = {
        "kind": "Pod",
        "metadata": {
            "namespace": "soperator",
            "name": "preserved-populate-evidence",
            "labels": {"nebius-cxcli/operation-id": "superseded"},
        },
        "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}}]},
        "status": {"phase": phase},
    }

    result = verify_passive_rootfs_consumers(
        pvc_name="jail-rootfs-slot-b-pvc",
        pods={"items": [pod]},
        soperator_resources={"items": []},
        allowed_operation_id="sha256:" + "a" * 64,
    )

    assert result == {"consumerStatus": "unconsumed", "consumerCount": 0}


@pytest.mark.parametrize("phase", ["Pending", "Running", "Unknown", ""])
def test_fresh_consumer_census_rejects_nonterminal_foreign_pods(phase: str) -> None:
    pod = {
        "kind": "Pod",
        "metadata": {
            "namespace": "soperator",
            "name": "live-foreign-consumer",
            "labels": {"nebius-cxcli/operation-id": "foreign"},
        },
        "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}}]},
        "status": {"phase": phase},
    }

    with pytest.raises(RuntimeError, match="live-foreign-consumer"):
        verify_passive_rootfs_consumers(
            pvc_name="jail-rootfs-slot-b-pvc",
            pods={"items": [pod]},
            soperator_resources={"items": []},
            allowed_operation_id="sha256:" + "a" * 64,
        )
