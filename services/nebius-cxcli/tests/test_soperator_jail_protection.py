from __future__ import annotations

import copy

import pytest

from nebius_cxcli.soperator_jail_mounts import apply_jail_persistent_mount_values
from nebius_cxcli.soperator_jail_protection import (
    apply_frozen_jail_protection,
    assert_disposable_generation,
    assert_protection_extension,
    freeze_jail_protection,
    observed_directory_bindings,
    prepare_disposable_passive_generation,
    protect_jail_directories,
    retained_rootfs_generations,
    slot_generation,
    verify_target_volume,
)
from nebius_cxcli.soperator_rootfs_transition import (
    plan_soperator_rootfs_transition,
    recover_soperator_rootfs_transition,
)


@pytest.mark.parametrize("layout", ["managed", "external"])
def test_new_folder_keeps_same_backing_across_three_upgrades(layout, tmp_path):
    values = apply_jail_persistent_mount_values(
        {}, target_ref="cluster", layout=layout, legacy_active_source=False
    )
    original = slot_generation(values, "slot-a")
    values = protect_jail_directories(
        values, paths=["/datasets"], layout=layout, target_ref="cluster"
    )
    source_dir = tmp_path / original["localPath"].lstrip("/") / "datasets"
    source_dir.mkdir(parents=True)
    marker = source_dir / "checkpoint"
    marker.write_bytes(b"customer data")
    before = marker.stat()
    frozen = freeze_jail_protection(values)
    assert apply_frozen_jail_protection({}, frozen) == values

    targets = []
    for _ in range(3):
        switched, transition = plan_soperator_rootfs_transition(
            values,
            target_ref="cluster",
            layout=layout,
            legacy_pvc_resolver=lambda: pytest.fail(
                "slot-backed upgrade must not discover legacy storage"
            ),
        )
        generation = slot_generation(switched, switched["jailRootfs"]["activeSlot"])
        assert_disposable_generation(switched, generation)
        assert generation["localPath"] != original["localPath"]
        targets.append(generation)
        # Simulate official population only in the selected disposable generation.
        target = tmp_path / generation["localPath"].lstrip("/")
        target.mkdir(parents=True, exist_ok=True)
        (target / "release").write_text("next")
        assert switched["jailPersistentMounts"] == values["jailPersistentMounts"]
        values = apply_jail_persistent_mount_values(
            switched, target_ref="cluster", layout=layout, legacy_active_source=False
        )
        assert marker.read_bytes() == b"customer data"
        assert marker.stat().st_ino == before.st_ino
        assert marker.stat().st_mode == before.st_mode
        assert retained_rootfs_generations(values) == [original]
    assert targets[1]["pvcName"] != original["pvcName"]
    assert targets[0] == targets[2]


def test_existing_mount_volume_names_and_custom_rootfs_backing_are_preserved():
    values = apply_jail_persistent_mount_values({}, target_ref="cluster", layout="managed")
    values["jailRootfs"]["store"]["rootfsPath"] = "/mnt/jail-store/generations"
    for slot, row in values["jailRootfs"]["slots"].items():
        row["localPath"] = f"/mnt/jail-store/generations/{slot}"
    values["jailPersistentMounts"][0].update(
        name="existing-home", pvName="existing-home-pv", pvcName="existing-home-pvc"
    )
    previous = copy.deepcopy(values["jailPersistentMounts"])
    selected = protect_jail_directories(
        values, paths=["/workspace"], layout="managed", target_ref="cluster"
    )
    assert selected["jailPersistentMounts"][: len(previous)] == previous
    assert (
        selected["jailPersistentMounts"][-1]["localPath"]
        == "/mnt/jail-store/generations/slot-a/workspace"
    )
    assert_protection_extension(values, selected)
    for _ in range(2):
        selected, _ = plan_soperator_rootfs_transition(
            selected, target_ref="cluster", layout="managed", legacy_pvc_resolver=lambda: "unused"
        )
        assert selected["jailPersistentMounts"][: len(previous)] == previous
    desired = copy.deepcopy(selected)
    desired["jailPersistentMounts"][0]["pvcName"] = "replacement-home-pvc"
    with pytest.raises(ValueError, match="remove or redirect"):
        assert_protection_extension(selected, desired)


@pytest.mark.parametrize("key", ["name", "pvName", "pvcName"])
def test_implicit_existing_storage_identity_cannot_be_redirected(key):
    source = apply_jail_persistent_mount_values({}, target_ref="cluster", layout="managed")
    desired = copy.deepcopy(source)
    desired["jailPersistentMounts"][0][key] = "replacement-home"
    with pytest.raises(ValueError, match="remove or redirect"):
        assert_protection_extension(source, desired)


def test_allocation_is_deterministic_and_resume_does_not_allocate_again():
    values = apply_jail_persistent_mount_values({}, target_ref="cluster", layout="managed")
    values = protect_jail_directories(
        values, paths=["/data2"], layout="managed", target_ref="cluster"
    )
    values, _ = plan_soperator_rootfs_transition(
        values, target_ref="cluster", layout="managed", legacy_pvc_resolver=lambda: "unused"
    )
    first, allocated = prepare_disposable_passive_generation(values)
    assert allocated
    assert prepare_disposable_passive_generation(values) == (first, True)
    resumed, allocated = prepare_disposable_passive_generation(first)
    assert not allocated
    assert resumed == first
    # Removing the optional selection never releases the physical generation.
    first["jailPersistentMounts"] = [
        r for r in first["jailPersistentMounts"] if r["mountPath"] != "/data2"
    ]
    assert retained_rootfs_generations(first)


@pytest.mark.parametrize("path", ["/", "/usr", "/proc/data", "/home/nested", "/a/../b"])
def test_invalid_selection_never_changes_source(path):
    values = apply_jail_persistent_mount_values({}, target_ref="cluster", layout="managed")
    before = copy.deepcopy(values)
    with pytest.raises(ValueError):
        protect_jail_directories(values, paths=[path], layout="managed", target_ref="cluster")
    assert values == before


@pytest.mark.parametrize("layout", ["managed", "external"])
def test_recovery_reconstructs_retained_slot_allocation(layout):
    values = apply_jail_persistent_mount_values(
        {}, target_ref="cluster", layout=layout, legacy_active_source=False
    )
    values = protect_jail_directories(
        values, paths=["/datasets"], layout=layout, target_ref="cluster"
    )
    values, _ = plan_soperator_rootfs_transition(
        values, target_ref="cluster", layout=layout, legacy_pvc_resolver=lambda: "unused"
    )
    frozen = freeze_jail_protection(values)
    activated, transition = plan_soperator_rootfs_transition(
        values, target_ref="cluster", layout=layout, legacy_pvc_resolver=lambda: "unused"
    )
    assert not transition["recycleInactiveSlot"]
    # The release child reapplies the campaign's frozen, pre-transition intent.
    restored = apply_frozen_jail_protection(activated, frozen)
    recovered = recover_soperator_rootfs_transition(
        restored, transition, target_ref="cluster", layout=layout
    )
    assert recovered == activated
    assert (
        recover_soperator_rootfs_transition(
            activated, transition, target_ref="cluster", layout=layout
        )
        == activated
    )
    with pytest.raises(RuntimeError, match="transition changed"):
        recover_soperator_rootfs_transition(
            restored,
            {**transition, "targetPvcName": "different-pvc"},
            target_ref="cluster",
            layout=layout,
        )


def test_empty_selection_keeps_custom_backing_and_retention():
    values = apply_jail_persistent_mount_values({}, target_ref="cluster", layout="managed")
    selected = protect_jail_directories(
        values, paths=["/datasets"], layout="managed", target_ref="cluster"
    )
    assert (
        protect_jail_directories(selected, paths=[], layout="managed", target_ref="cluster")
        == selected
    )
    bad = copy.deepcopy(selected)
    bad["jailRootfs"]["retainedGenerations"] = []
    with pytest.raises(ValueError, match="must not overlap"):
        freeze_jail_protection(bad)


@pytest.mark.parametrize("field", ["localPath", "pvName", "pvcName", "volumeSourceName"])
def test_generation_alias_is_rejected_even_when_original_is_retained(field):
    values = protect_jail_directories(
        {}, paths=["/workspace"], layout="managed", target_ref="cluster"
    )
    original = slot_generation(values, "slot-a")
    values["jailRootfs"]["slots"]["slot-b"][field] = original[field]
    with pytest.raises(ValueError, match="identities overlap"):
        freeze_jail_protection(values)


def test_upgrade_cannot_remove_or_redirect_accepted_protection():
    initial = apply_jail_persistent_mount_values({}, layout="managed", target_ref="cluster")
    source = protect_jail_directories(
        initial, paths=["/workspace"], layout="managed", target_ref="cluster"
    )
    desired = protect_jail_directories(
        source, paths=["/datasets"], layout="managed", target_ref="cluster"
    )
    assert_protection_extension(source, desired)
    desired["jailPersistentMounts"] = [
        row for row in desired["jailPersistentMounts"] if row["mountPath"] != "/workspace"
    ]
    with pytest.raises(ValueError, match="remove or redirect"):
        assert_protection_extension(source, desired)
    desired["jailPersistentMounts"] = source["jailPersistentMounts"]
    desired["jailRootfs"]["slots"]["slot-a"]["localPath"] = "/mnt/jail-store/rootfs/replacement"
    with pytest.raises(ValueError, match="identities overlap"):
        freeze_jail_protection(desired)


def test_live_directory_binding_rejects_a_same_named_directory_on_different_backing():
    pod = {
        "spec": {
            "containers": [
                {"name": "sshd", "volumeMounts": [{"name": "jail", "mountPath": "/mnt/jail"}]}
            ],
            "volumes": [{"name": "jail", "persistentVolumeClaim": {"claimName": "source"}}],
        }
    }
    values = {
        "jailPersistentMounts": [{"mountPath": "/datasets", "localPath": "/store/old/datasets"}]
    }
    pvc = {"metadata": {"uid": "source-uid"}, "spec": {"volumeName": "source-pv"}}
    pv = {"metadata": {"uid": "pv-uid"}, "spec": {"local": {"path": "/store/old"}}}
    assert observed_directory_bindings(
        values, pod=pod, read_pvc=lambda _: pvc, read_pv=lambda _: pv
    ) == ("sshd", [{"mountPath": "/datasets", "localPath": "/store/old/datasets"}])
    pv["spec"]["local"]["path"] = "/store/empty"
    with pytest.raises(ValueError, match="live backing"):
        observed_directory_bindings(values, pod=pod, read_pvc=lambda _: pvc, read_pv=lambda _: pv)


def test_target_pvc_cannot_alias_retained_storage():
    from nebius_cxcli.soperator_jail_protection import rootfs_storage_authority

    target = {
        "volume_name": "slot-b",
        "pv_name": "slot-b-pv",
        "pvc_name": "slot-b-pvc",
        "local_path": "/store/b",
    }
    authority = rootfs_storage_authority(
        {
            "filesystemId": "filesystem",
            "deviceTag": "jail",
            "mountPath": "/store",
            "slots": {"slot-b": target},
            "persistentMounts": [],
            "retainedGenerations": [],
        },
        "slot-b",
    )
    pvc = {
        "metadata": {"name": "slot-b-pvc", "namespace": "soperator", "uid": "pvc-uid"},
        "spec": {"volumeName": "slot-b-pv"},
    }
    pv = {
        "metadata": {"name": "slot-b-pv", "uid": "pv-uid"},
        "spec": {
            "local": {"path": "/store/b"},
            "persistentVolumeReclaimPolicy": "Retain",
            "claimRef": {"name": "slot-b-pvc", "namespace": "soperator", "uid": "pvc-uid"},
        },
    }
    assert verify_target_volume(authority, pvc=pvc, pv=pv) == "pv-uid"
    pv["spec"]["local"]["path"] = "/store/customer"
    with pytest.raises(ValueError, match="physical backing"):
        verify_target_volume(authority, pvc=pvc, pv=pv)
