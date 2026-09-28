from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest

from destroy_fakes import receipt
from nebius_cxcli.destroy_generation import (
    freeze_publication,
    freeze_terraform,
    publish,
    remove_sfs_entries,
    validate_final_state,
    validate_reconciliation,
    verify_publication,
)
from nebius_cxcli.render import project_generation_plan_fingerprints


def change(address, kind, identifier, action="delete"):
    return {
        "mode": "managed",
        "type": kind,
        "address": address,
        "change": {"actions": [action], "before": {"id": identifier}},
    }


def plan():
    cluster = change(
        "module.cluster_a.nebius_mk8s_v1_cluster.this", "nebius_mk8s_v1_cluster", "mk8scluster-a"
    )
    account = change(
        'module.cluster_a.nebius_iam_v1_service_account.node_group["worker"]',
        "nebius_iam_v1_service_account",
        "account-a",
    )
    return {
        "prior_state": {"values": {}},
        "planned_values": {
            "outputs": {"remaining_fs": {"value": "fs-preserved"}},
            "root_module": {"resources": []},
        },
        "resource_changes": [cluster, account],
    }


def freeze(value):
    return freeze_terraform(
        value,
        inventory=receipt().approved["inventory"],
        delete_sfs=False,
        module_names=["cluster_a"],
        managed_cluster=True,
        ancillary_addresses=frozenset(
            f'nebius_iam_v1_{kind}.cluster_a_soperator_observability["{role}"]'
            for kind in ("group", "group_membership", "access_permit")
            for role in ("system", "controller", "login", "worker", "accounting")
        ),
    )


def refresh(row):
    observed = copy.deepcopy(row)
    before = {**row["change"]["before"], "status": {"members_count": 0}}
    observed["change"] = {
        "actions": ["update"],
        "before": before,
        "after": {**before, "status": {"members_count": 1}},
        "after_unknown": {},
    }
    return observed


@pytest.mark.parametrize("kind", ["cluster", "account", "iam"])
def test_preview_accepts_refreshed_metadata_on_exact_planned_deletions(kind):
    initial = plan()
    if kind == "iam":
        initial["resource_changes"].append(
            change(
                'nebius_iam_v1_group.cluster_a_soperator_observability["worker"]',
                "nebius_iam_v1_group",
                "group-a",
            )
        )
    selected = initial["resource_changes"][0 if kind == "cluster" else -1]
    initial["resource_drift"] = [refresh(selected)]
    frozen = freeze(initial)
    assert frozen["deletes"][selected["address"]]["id"] == selected["change"]["before"]["id"]


def test_reconciliation_accepts_sdk_absence_and_ancillary_refresh():
    initial = plan()
    frozen = freeze(initial)
    post = copy.deepcopy(initial)
    sdk = post["resource_changes"].pop(0)
    sdk["change"]["after"] = None
    post["resource_drift"] = [sdk, refresh(post["resource_changes"][0])]
    validate_reconciliation(post, frozen)


def test_delete_sfs_preview_with_thirty_refreshed_target_resources():
    initial = plan()
    initial["resource_changes"][1] = change(
        'module.cluster_a.nebius_compute_v1_gpu_cluster.this["fabric"]',
        "nebius_compute_v1_gpu_cluster",
        "gpu-a",
    )
    for role in ("system", "controller", "login", "worker", "accounting"):
        initial["resource_changes"].append(
            change(
                f'module.cluster_a.nebius_mk8s_v1_node_group.this["{role}"]',
                "nebius_mk8s_v1_node_group",
                f"group-{role}",
            )
        )
        initial["resource_changes"].append(
            change(
                f'module.cluster_a.nebius_iam_v1_service_account.node_group["{role}"]',
                "nebius_iam_v1_service_account",
                f"account-{role}",
            )
        )
        for kind in ("group", "group_membership", "access_permit"):
            resource_type = f"nebius_iam_v1_{kind}"
            initial["resource_changes"].append(
                change(
                    f'{resource_type}.cluster_a_soperator_observability["{role}"]',
                    resource_type,
                    f"{kind}-{role}",
                )
            )
    for key in ("jail", "spool", "accounting"):
        initial["resource_changes"].append(
            change(
                f'module.storage.nebius_compute_v1_filesystem.this["{key}"]',
                "nebius_compute_v1_filesystem",
                f"fs-{key}",
            )
        )
    initial["resource_drift"] = [refresh(row) for row in initial["resource_changes"]]
    inventory = {
        **receipt().approved["inventory"],
        "node_group_ids": [
            f"group-{role}" for role in ("system", "controller", "login", "worker", "accounting")
        ],
        "filesystem_ids": ["fs-jail", "fs-spool", "fs-accounting"],
        "gpu_cluster_ids": ["gpu-a"],
    }
    frozen = freeze_terraform(
        initial,
        inventory=inventory,
        delete_sfs=True,
        module_names=["cluster_a"],
        managed_cluster=True,
        ancillary_addresses=frozenset(
            f'nebius_iam_v1_{kind}.cluster_a_soperator_observability["{role}"]'
            for kind in ("group", "group_membership", "access_permit")
            for role in ("system", "controller", "login", "worker", "accounting")
        ),
    )
    assert len(frozen["deletes"]) == 30
    assert sum(row["sdk"] for row in frozen["deletes"].values()) == 10


@pytest.mark.parametrize("stage", ["preview", "reconcile"])
@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_address",
        "foreign_type",
        "foreign_before_id",
        "changed_after_id",
        "missing_after_id",
        "unknown_after_id",
        "absent_after",
        "retained",
        "data_mode",
        "deposed",
        "moved",
        "planned_create",
        "planned_update",
        "planned_replace",
    ],
)
def test_refreshed_drift_never_widens_deletion_authority(stage, mutation):
    initial = plan()
    frozen = freeze(initial)
    candidate = copy.deepcopy(initial)
    if stage == "reconcile":
        candidate["resource_changes"].pop(0)
    row = candidate["resource_changes"][-1]
    drift = refresh(row)
    candidate["resource_drift"] = [drift]
    if mutation == "foreign_address":
        drift["address"] = "module.other.resource.this"
    elif mutation == "foreign_type":
        drift["type"] = "nebius_compute_v1_disk"
    elif mutation == "foreign_before_id":
        drift["change"]["before"]["id"] = "other-id"
    elif mutation == "changed_after_id":
        drift["change"]["after"]["id"] = "other-id"
    elif mutation == "missing_after_id":
        del drift["change"]["after"]["id"]
    elif mutation == "unknown_after_id":
        drift["change"]["after_unknown"] = {"id": True}
    elif mutation == "absent_after":
        drift["change"]["after"] = None
    elif mutation == "retained":
        row["change"]["actions"] = ["no-op"]
    elif mutation == "data_mode":
        drift["mode"] = "data"
    elif mutation == "deposed":
        drift["deposed"] = "old-object"
    elif mutation == "moved":
        drift["previous_address"] = "module.other.resource.this"
    else:
        row["change"]["actions"] = {
            "planned_create": ["create"],
            "planned_update": ["update"],
            "planned_replace": ["delete", "create"],
        }[mutation]
    with pytest.raises(RuntimeError):
        if stage == "preview":
            freeze(candidate)
        else:
            validate_reconciliation(candidate, frozen)


@pytest.mark.parametrize("mutation", ["type", "mode", "after", "unknown_id"])
def test_reconciliation_rejects_inconsistent_disappearance(mutation):
    initial = plan()
    frozen = freeze(initial)
    sdk = initial["resource_changes"].pop(0)
    sdk["change"]["after"] = None
    initial["resource_drift"] = [sdk]
    if mutation == "type":
        sdk["type"] = "nebius_compute_v1_disk"
    elif mutation == "mode":
        sdk["mode"] = "data"
    elif mutation == "after":
        sdk["change"]["after"] = {"id": "mk8scluster-a"}
    else:
        sdk["change"]["before"]["id"] = None
    with pytest.raises(RuntimeError):
        validate_reconciliation(initial, frozen)


def test_reconciliation_only_applies_exact_ancillary_deletes_and_expected_output_projection():
    original = plan()
    frozen = freeze(original)
    post = copy.deepcopy(original)
    sdk = post["resource_changes"].pop(0)
    post["resource_drift"] = [sdk]
    validate_reconciliation(post, frozen)
    validate_final_state(post["planned_values"], frozen)
    with pytest.raises(RuntimeError, match="ancillary"):
        validate_reconciliation(original, frozen)


@pytest.mark.parametrize(
    "mutation",
    [
        "create",
        "update",
        "foreign_id",
        "foreign_address",
        "unrelated_drift",
        "changed_outputs",
        "extra_resource",
    ],
)
def test_reconciliation_rejects_widened_or_changed_scope(mutation):
    original = plan()
    frozen = freeze(original)
    post = copy.deepcopy(original)
    post["resource_changes"].pop(0)
    row = post["resource_changes"][0]
    if mutation in {"create", "update"}:
        row["change"]["actions"] = [mutation]
    elif mutation == "foreign_id":
        row["change"]["before"]["id"] = "other-gpu"
    elif mutation == "foreign_address":
        row["address"] = "module.other.gpu.this"
    elif mutation == "unrelated_drift":
        post["resource_drift"] = [change("other", "nebius_compute_v1_disk", "data-disk")]
    elif mutation == "changed_outputs":
        post["planned_values"]["outputs"]["remaining_fs"]["value"] = "changed"
    else:
        post["planned_values"]["root_module"]["resources"] = [
            {"address": "unexpected", "mode": "managed", "values": {"id": "new"}}
        ]
    with pytest.raises(RuntimeError):
        validate_reconciliation(post, frozen)


@pytest.mark.parametrize(
    "kind,identifier",
    [
        ("nebius_compute_v1_disk", "disk"),
        ("nebius_compute_v1_filesystem", "filesystem-a"),
        ("nebius_mk8s_v1_cluster", "other-cluster"),
    ],
)
def test_initial_plan_rejects_preserved_storage_and_wrong_cloud_ids(kind, identifier):
    initial = plan()
    initial["resource_changes"].append(change("module.cluster_a.extra", kind, identifier))
    with pytest.raises(RuntimeError):
        freeze(initial)


def test_publication_frozen_bytes_survive_restart_and_reject_local_edits(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(b"old")
    writes, removals = {path: b"new"}, ()
    preimages = {path: "sha256:" + hashlib.sha256(b"old").hexdigest()}
    identity, before = project_generation_plan_fingerprints(
        project_dir=tmp_path, writes=writes, removals=removals, expected_preimages=preimages
    )
    frozen = freeze_publication(
        SimpleNamespace(
            writes=writes,
            removals=removals,
            expected_preimages=preimages,
            sha256=identity,
            preimage_sha256=before,
        ),
        tmp_path,
    )
    verify_publication(frozen, tmp_path)
    path.write_bytes(b"operator edit")
    with pytest.raises(RuntimeError, match="changed"):
        publish(frozen, tmp_path)
    path.write_bytes(b"old")
    publish(frozen, tmp_path)
    publish(frozen, tmp_path)
    assert path.read_bytes() == b"new"


@pytest.mark.parametrize("remaining", [False, True])
def test_sfs_exact_entry_removal_never_activates_single_mode_default(remaining):
    entries = {"jail": {"existing_id": "filesystem-a"}}
    if remaining:
        entries["keep"] = {"existing_id": "keep-fs"}
    payload = {
        "infra": {
            "components": [
                {"id": "sfs", "instance_id": "storage", "inputs": {"filesystems": entries}}
            ]
        }
    }
    result = remove_sfs_entries(
        payload, filesystem_ids={"filesystem-a"}, module_sources=[], state_values={}
    )
    rows = result["infra"]["components"]
    if remaining:
        assert rows[0]["inputs"]["filesystems"] == {"keep": {"existing_id": "keep-fs"}}
    else:
        assert rows == []


def test_remaining_configuration_cannot_reference_deleted_sfs():
    with pytest.raises(RuntimeError, match="still referenced"):
        remove_sfs_entries(
            {"other_consumer": "filesystem-a"},
            filesystem_ids={"filesystem-a"},
            module_sources=[],
            state_values={},
        )


def test_publication_recovers_exact_committed_generation_after_crash(tmp_path):
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    config = tmp_path / "config.yaml"
    generated = tmp_path / "generated/main.tf"
    generated.parent.mkdir()
    config.write_bytes(b"old")
    generated.write_bytes(b"old tf")
    writes = {config: b"new", generated: b"new tf"}
    preimages = {path: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in writes}
    identity, before = project_generation_plan_fingerprints(
        project_dir=tmp_path, writes=writes, removals=(), expected_preimages=preimages
    )
    frozen = freeze_publication(
        SimpleNamespace(
            writes=writes,
            removals=(),
            expected_preimages=preimages,
            sha256=identity,
            preimage_sha256=before,
        ),
        tmp_path,
    )

    def crash(name):
        if name == "after-commit":
            raise OSError("interrupted publication")

    with pytest.raises(OSError):
        ProjectBundleTransaction(tmp_path, failpoint=crash).commit(
            writes, expected_preimages=preimages, generation_sha256=identity
        )
    publish(frozen, tmp_path)
    assert config.read_bytes() == b"new" and generated.read_bytes() == b"new tf"


def test_backend_publication_restores_missing_generated_cache_without_overwriting_source(tmp_path):
    config, generated = tmp_path / "config.yaml", tmp_path / "generated/main.tf"
    generated.parent.mkdir()
    config.write_bytes(b"old")
    generated.write_bytes(b"old tf")
    writes = {config: b"new", generated: b"new tf"}
    preimages = {path: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in writes}
    identity, before = project_generation_plan_fingerprints(
        project_dir=tmp_path, writes=writes, removals=(), expected_preimages=preimages
    )
    frozen = freeze_publication(
        SimpleNamespace(
            writes=writes,
            removals=(),
            expected_preimages=preimages,
            sha256=identity,
            preimage_sha256=before,
        ),
        tmp_path,
    )
    generated.unlink()
    verify_publication(frozen, tmp_path, allow_missing_generated=True)
    publish(frozen, tmp_path)
    assert generated.read_bytes() == b"new tf"
    config.write_bytes(b"operator edit")
    with pytest.raises(RuntimeError, match="changed"):
        publish(frozen, tmp_path)


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("indirect", [False, True])
def test_imported_sfs_pruning_rejects_remaining_consumers(single, indirect):
    inputs = (
        {"existing_id": "filesystem-a"}
        if single
        else {"filesystems": {"data": {"existing_id": "filesystem-a"}}}
    )
    consumer = (
        "storage.filesystem_ids"
        if indirect
        else {"source_component": "sfs", "source_instance": "storage"}
    )
    with pytest.raises(RuntimeError, match="references"):
        remove_sfs_entries(
            {
                "infra": {
                    "components": [{"id": "sfs", "instance_id": "storage", "inputs": inputs}]
                },
                "remaining": consumer,
            },
            filesystem_ids={"filesystem-a"},
            module_sources=[],
            state_values={},
        )


def test_sfs_pruning_keeps_an_unselected_key_reference():
    payload = {
        "infra": {
            "components": [
                {
                    "id": "sfs",
                    "instance_id": "storage",
                    "inputs": {
                        "filesystems": {
                            "data": {"existing_id": "filesystem-a"},
                            "other": {"existing_id": "filesystem-other"},
                        }
                    },
                }
            ]
        },
        "remaining": {"source_component": "sfs", "source_instance": "storage", "keys": ["other"]},
    }
    result = remove_sfs_entries(
        payload, filesystem_ids={"filesystem-a"}, module_sources=[], state_values={}
    )
    assert result["remaining"] == payload["remaining"]


def test_managed_nfs_binding_uses_original_terraform_outputs(monkeypatch):
    from nebius_cxcli import nfs_csi
    from nebius_cxcli.component_sources import component_output_root_name
    from nebius_cxcli.destroy_generation import vm_nfs_config

    monkeypatch.setattr(nfs_csi, "nfs_instance_id_for_target", lambda *a, **kw: "nfs-a")
    outputs = {
        component_output_root_name("nfs-a", key): {"value": value}
        for key, value in [("server_ip", "10.0.0.8"), ("export_path", "/data")]
    }
    assert vm_nfs_config({}, {}, target_ref="cluster-a", state_values={"outputs": outputs}) == {
        "enabled": True,
        "server": "10.0.0.8",
        "path": "/data",
    }
    with pytest.raises(RuntimeError, match="server address"):
        vm_nfs_config({}, {}, target_ref="cluster-a", state_values={})
    assert vm_nfs_config(
        {}, {"externalNfs": {"enabled": False}}, target_ref="cluster-a", state_values={}
    ) == {"enabled": False}
