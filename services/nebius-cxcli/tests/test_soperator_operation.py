from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.soperator_failures import (
    SoperatorMainWorkloadIdentity,
    SoperatorSafetyPauseError,
)
from nebius_cxcli.soperator_operation import (
    SOPERATOR_OPERATION_ANCHOR_SCHEMA,
    SoperatorOperationAnchor,
    begin_soperator_release_intent,
    bind_soperator_release_intent_operation,
    build_soperator_operation_spec,
    complete_soperator_release_intent,
    load_active_soperator_release_intent,
    load_local_active_soperator_release_intent,
    soperator_operation_anchor_status,
)
from nebius_cxcli.soperator_operation_lock import SoperatorLeaseAuthority
from soperator_fixtures import sample_snapshot


def _lease_authority() -> SoperatorLeaseAuthority:
    return SoperatorLeaseAuthority(
        lease_name="nebius-cxcli-soperator-a",
        lease_uid="lease-uid-a",
        holder_identity_sha256="sha256:" + "f" * 64,
        fencing_epoch=7,
        operation_fingerprint="sha256:" + "e" * 64,
    )


def _paths(tmp_path: Path) -> ProjectPaths:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True)
    values = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values"},
        "data": {"values.yaml": "clusterName: soperator\n"},
    }
    (flux_dir / "configmap-terraform-fluxcd-values.yaml").write_text(
        yaml.safe_dump(values), encoding="utf-8"
    )
    (flux_dir / "soperator-nebius-adapter.yaml").write_text(
        yaml.safe_dump({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "a"}}),
        encoding="utf-8",
    )
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
                    "readiness": {
                        "storage": [
                            {
                                "kind": "PersistentVolumeClaim",
                                "namespace": "soperator",
                                "name": "controller-spool-controller-0",
                            }
                        ]
                    },
                }
            )
        },
    }
    (flux_dir / "soperator-release-graph.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    return ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=flux_dir,
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def _spec(tmp_path: Path):
    return build_soperator_operation_spec(
        paths=_paths(tmp_path),
        target_ref="cluster-a",
        ownership="managed",
        strategy="install",
        current_release="",
        target_release="4.1.7",
        source_contract="absent",
        target_contract="upstream-flux-v1",
        source_capability_sha256="sha256:" + "7" * 64,
        target_capability_sha256="sha256:" + "8" * 64,
        release_snapshot_sha256="sha256:" + "9" * 64,
        target_jail_image="registry.example.invalid/populate-jail@sha256:" + "a" * 64,
        target_jail_image_source="upstream-default",
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
        infrastructure_plan_sha256="sha256:" + "1" * 64,
        scheduling_evidence={"actions": []},
    )


def test_operation_spec_binds_every_mutable_boundary(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    assert spec.target_ref == "cluster-a"
    assert spec.nebius_cluster_id == "mk8scluster-a"
    assert spec.kubernetes_uid == "kube-system-uid"
    assert spec.infrastructure_plan_sha256 == "sha256:" + "1" * 64
    for digest in (
        spec.desired_values_sha256,
        spec.adapter_sha256,
        spec.protected_state_sha256,
        spec.scheduling_sha256,
        spec.admission_sha256,
        spec.source_capability_sha256,
        spec.target_capability_sha256,
        spec.stage_plan_sha256,
    ):
        assert digest.startswith("sha256:")
        assert len(digest) == 71


def test_operation_spec_rejects_missing_infrastructure_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="infrastructure plan SHA-256"):
        build_soperator_operation_spec(
            paths=_paths(tmp_path),
            target_ref="cluster-a",
            ownership="managed",
            strategy="install",
            current_release="",
            target_release="4.1.7",
            source_contract="absent",
            target_contract="upstream-flux-v1",
            source_capability_sha256="sha256:" + "7" * 64,
            target_capability_sha256="sha256:" + "8" * 64,
            release_snapshot_sha256="sha256:" + "9" * 64,
            target_jail_image="registry.example.invalid/populate-jail@sha256:" + "a" * 64,
            target_jail_image_source="upstream-default",
            nebius_cluster_id="mk8scluster-a",
            kubernetes_uid="kube-system-uid",
            infrastructure_plan_sha256="",
            scheduling_evidence={},
        )


def test_completed_operation_anchor_is_reactivated_for_safe_reobservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=_spec(tmp_path),
        lease_authority=_lease_authority(),
    )
    calls: list[tuple[str, ...]] = []

    def _kubectl(*args: str, input_text: str | None = None):
        del input_text
        calls.append(args)
        if args[0] == "get":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "metadata": {"resourceVersion": "12"},
                        "data": anchor._expected_data(status="complete"),
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(anchor, "_kubectl", _kubectl)

    anchor.establish()

    patch_call = next(args for args in calls if args[0] == "patch")
    patch = json.loads(patch_call[-1])
    assert patch[-1] == {
        "op": "replace",
        "path": "/data/status",
        "value": "active",
    }


def test_interrupted_operation_anchor_rotates_to_strictly_newer_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_authority = _lease_authority()
    new_authority = replace(
        old_authority,
        lease_uid="lease-uid-b",
        holder_identity_sha256="sha256:" + "1" * 64,
        fencing_epoch=old_authority.fencing_epoch + 1,
    )
    operation_spec = _spec(tmp_path)
    old_anchor = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=operation_spec,
        lease_authority=old_authority,
    )
    resumed_anchor = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=operation_spec,
        lease_authority=new_authority,
    )
    calls: list[tuple[str, ...]] = []

    def _kubectl(*args: str, input_text: str | None = None):
        del input_text
        calls.append(args)
        if args[0] == "get":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "metadata": {"resourceVersion": "12"},
                        "data": old_anchor._expected_data(status="active"),
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(resumed_anchor, "_kubectl", _kubectl)

    resumed_anchor.establish()

    patch_call = next(args for args in calls if args[0] == "patch")
    patch = json.loads(patch_call[-1])
    replacements = {
        item.get("path"): item.get("value") for item in patch if item["op"] == "replace"
    }
    assert (
        replacements.items()
        >= {
            "/data/leaseUid": "lease-uid-b",
            "/data/holderIdentitySha256": "sha256:" + "1" * 64,
            "/data/fencingEpoch": str(old_authority.fencing_epoch + 1),
        }.items()
    )


def test_operation_anchor_completion_is_cas_fenced_by_current_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=_spec(tmp_path),
        lease_authority=_lease_authority(),
    )
    calls: list[tuple[str, ...]] = []

    def _kubectl(*args: str, input_text: str | None = None):
        del input_text
        calls.append(args)
        if args[0] == "get":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "metadata": {"resourceVersion": "12"},
                        "data": anchor._expected_data(status="active"),
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(anchor, "_kubectl", _kubectl)

    anchor.complete()

    patch_call = next(args for args in calls if args[0] == "patch")
    patch = json.loads(patch_call[-1])
    tests = {item["path"]: item["value"] for item in patch if item["op"] == "test"}
    assert tests["/metadata/resourceVersion"] == "12"
    assert tests["/data/leaseUid"] == _lease_authority().lease_uid
    assert tests["/data/fencingEpoch"] == str(_lease_authority().fencing_epoch)
    assert patch[-1] == {
        "op": "replace",
        "path": "/data/status",
        "value": "complete",
    }


def test_operation_anchor_freezes_and_loads_exact_main_workload_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=_spec(tmp_path),
        lease_authority=_lease_authority(),
    )
    identity = SoperatorMainWorkloadIdentity(
        api_version="helm.toolkit.fluxcd.io/v2",
        kind="HelmRelease",
        namespace="flux-system",
        name="soperator-main",
        source_kind="OCIRepository",
        source_name="soperator-upstream-umbrella",
        source_revision="sha256:" + "1" * 64,
        uid="main-release-uid",
        generation=4,
        observed_generation=4,
    )
    state = {
        "metadata": {"resourceVersion": "12"},
        "data": anchor._expected_data(status="active"),
    }

    def _kubectl(*args: str, input_text: str | None = None):
        del input_text
        if args[0] == "get":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(state), stderr="")
        if args[0] == "patch":
            for operation in json.loads(args[-1]):
                if operation["op"] in {"add", "replace"} and operation["path"].startswith("/data/"):
                    state["data"][operation["path"].removeprefix("/data/")] = operation["value"]
            state["metadata"]["resourceVersion"] = "13"
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(anchor, "_kubectl", _kubectl)

    assert anchor.freeze_main_workload_authority(identity) == identity
    assert anchor.freeze_main_workload_authority(identity) == identity
    advanced = replace(identity, generation=5, observed_generation=5)
    assert anchor.freeze_main_workload_authority(advanced) == advanced
    assert anchor.load_main_workload_authority() == advanced
    with pytest.raises(SoperatorSafetyPauseError, match="conflicts"):
        anchor.freeze_main_workload_authority(identity)
    with pytest.raises(SoperatorSafetyPauseError, match="conflicts"):
        anchor.freeze_main_workload_authority(replace(advanced, uid="replacement-main-uid"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_capability_sha256", "sha256:" + "0" * 64),
        ("target_capability_sha256", "sha256:" + "0" * 64),
        ("stage_plan_sha256", "sha256:" + "0" * 64),
        (
            "target_jail_image",
            "registry.example.invalid/populate-jail@sha256:" + "0" * 64,
        ),
        ("target_jail_image_source", "unsupported-source"),
    ],
)
def test_authoritative_inputs_change_operation_identity(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    spec = _spec(tmp_path)
    changed = replace(spec, **{field: value})

    original = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=spec,
        lease_authority=_lease_authority(),
    )
    modified = SoperatorOperationAnchor(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_spec=changed,
        lease_authority=_lease_authority(),
    )

    assert original.operation_id != modified.operation_id


def test_release_intent_reuses_exact_snapshot_and_fails_closed_on_selector_drift(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    snapshot = sample_snapshot()
    begin_soperator_release_intent(
        paths=paths,
        target_ref="cluster-a",
        requested_selector="latest",
        ownership="managed",
        strategy="in-place",
        source_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
        source_capability_sha256="sha256:" + "6" * 64,
        target_capability_sha256=snapshot.capability_sha256,
        target_jail_image=snapshot.populate_jail_image,
        target_jail_image_source="upstream-default",
        snapshot=snapshot,
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
        infrastructure_receipt_sha256="sha256:" + "7" * 64,
    )

    recovered = load_active_soperator_release_intent(
        paths=paths,
        target_ref="cluster-a",
        requested_selector="latest",
        ownership="managed",
        live_release="4.1.6",
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
    )

    assert recovered is not None
    intent, recovered_snapshot = recovered
    assert intent.target_release == "4.1.7"
    assert recovered_snapshot == snapshot
    assert load_local_active_soperator_release_intent(
        paths=paths,
        target_ref="cluster-a",
    ) == (intent, snapshot)
    with pytest.raises(RuntimeError, match="different selector"):
        load_active_soperator_release_intent(
            paths=paths,
            target_ref="cluster-a",
            requested_selector="4.1.7",
            ownership="managed",
            live_release="4.1.6",
            nebius_cluster_id="mk8scluster-a",
            kubernetes_uid="kube-system-uid",
        )


def test_completed_release_intent_is_not_recovered(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    snapshot = sample_snapshot()
    begin_soperator_release_intent(
        paths=paths,
        target_ref="cluster-a",
        requested_selector="latest",
        ownership="managed",
        strategy="in-place",
        source_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
        source_capability_sha256="sha256:" + "6" * 64,
        target_capability_sha256=snapshot.capability_sha256,
        target_jail_image=snapshot.populate_jail_image,
        target_jail_image_source="upstream-default",
        snapshot=snapshot,
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
        infrastructure_receipt_sha256="sha256:" + "7" * 64,
    )
    bind_soperator_release_intent_operation(
        paths=paths,
        target_ref="cluster-a",
        operation_spec_sha256="sha256:" + "5" * 64,
    )
    complete_soperator_release_intent(paths=paths, target_ref="cluster-a")

    assert (
        load_active_soperator_release_intent(
            paths=paths,
            target_ref="cluster-a",
            requested_selector="latest",
            ownership="managed",
            live_release="4.1.7",
            nebius_cluster_id="mk8scluster-a",
            kubernetes_uid="kube-system-uid",
        )
        is None
    )
    assert (
        load_local_active_soperator_release_intent(
            paths=paths,
            target_ref="cluster-a",
        )
        is None
    )


def test_bound_release_intent_recognizes_completed_cluster_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    snapshot = sample_snapshot()
    begin_soperator_release_intent(
        paths=paths,
        target_ref="cluster-a",
        requested_selector="latest",
        ownership="managed",
        strategy="in-place",
        source_release="4.1.6",
        target_release=snapshot.release,
        source_contract="upstream-flux-v1",
        target_contract=snapshot.capability_contract,
        source_capability_sha256="sha256:" + "6" * 64,
        target_capability_sha256=snapshot.capability_sha256,
        target_jail_image=snapshot.populate_jail_image,
        target_jail_image_source="upstream-default",
        snapshot=snapshot,
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
        infrastructure_receipt_sha256="sha256:" + "7" * 64,
    )
    operation_id = "sha256:" + "5" * 64
    bind_soperator_release_intent_operation(
        paths=paths,
        target_ref="cluster-a",
        operation_spec_sha256=operation_id,
    )
    intent, _snapshot = load_active_soperator_release_intent(
        paths=paths,
        target_ref="cluster-a",
        requested_selector="latest",
        ownership="managed",
        live_release="4.1.7",
        nebius_cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
    ) or pytest.fail("expected active intent")
    anchor_data = {
        "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
        "operationId": operation_id,
        "operationSpecSha256": operation_id,
        "clusterId": "mk8scluster-a",
        "kubernetesUid": "kube-system-uid",
        "targetRef": "cluster-a",
        "ownership": "managed",
        "strategy": "in-place",
        "currentRelease": "4.1.6",
        "sourceContract": "upstream-flux-v1",
        "targetContract": snapshot.capability_contract,
        "sourceCapabilitySha256": "sha256:" + "6" * 64,
        "targetCapabilitySha256": snapshot.capability_sha256,
        "releaseSnapshotSha256": snapshot.snapshot_sha256,
        "infrastructureReceiptSha256": "sha256:" + "7" * 64,
        "targetJailImage": snapshot.populate_jail_image,
        "targetJailImageSource": "upstream-default",
        "targetRelease": snapshot.release,
        "status": "complete",
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"data": anchor_data}), stderr=""
        ),
    )

    assert (
        soperator_operation_anchor_status(
            kube_context="ctx",
            cluster_id="mk8scluster-a",
            intent=intent,
        )
        == "complete"
    )
