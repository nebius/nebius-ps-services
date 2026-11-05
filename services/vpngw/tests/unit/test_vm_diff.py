from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy.vm_diff import ChangeType, VMDiffAnalyzer, VMSpec


def _vm_spec(**overrides) -> VMSpec:
    data = {
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "cores": None,
        "memory_gb": None,
        "disk_boot_image": "ubuntu24.04-driverless",
        "disk_type": "NETWORK_SSD",
        "disk_gb": 100,
        "disk_block_bytes": 4096,
        "num_nics": 1,
    }
    data.update(overrides)
    return VMSpec(**data)


def test_compare_marks_missing_vm_as_safe_creation() -> None:
    diff = VMDiffAnalyzer().compare(_vm_spec(), None)

    assert diff.change_type == ChangeType.SAFE
    assert diff.differences == ["VM does not exist (will create)"]
    assert not diff.requires_recreation()


def test_compare_requires_replacement_for_disk_growth() -> None:
    desired = _vm_spec(disk_gb=200, num_nics=2)
    actual = _vm_spec(disk_gb=100, num_nics=1)

    diff = VMDiffAnalyzer().compare(desired, actual)

    assert diff.change_type == ChangeType.DESTRUCTIVE
    assert "disk_gb: 100 → 200" in diff.differences
    assert "num_nics: 1 → 2 (expanding - safe)" in diff.differences
    assert diff.destructive_fields == ["disk_gb (100GiB → 200GiB)"]


def test_compare_treats_shrinks_and_disk_type_changes_as_destructive() -> None:
    desired = _vm_spec(disk_type="NETWORK_HDD", disk_gb=50, num_nics=1)
    actual = _vm_spec(disk_type="NETWORK_SSD", disk_gb=100, num_nics=2)

    diff = VMDiffAnalyzer().compare(desired, actual)

    assert diff.change_type == ChangeType.DESTRUCTIVE
    assert "disk_type (NETWORK_SSD → NETWORK_HDD)" in diff.destructive_fields
    assert "disk_gb (100GiB → 50GiB)" in diff.destructive_fields
    assert "num_nics shrink (2 → 1)" in diff.destructive_fields


def test_from_live_vm_uses_actual_disk_status_and_image_family() -> None:
    vm_obj = SimpleNamespace(
        spec=SimpleNamespace(
            resources=SimpleNamespace(platform="cpu-e2", preset=None, cores=8, memory_gb=32),
            network_interfaces=[SimpleNamespace(), SimpleNamespace()],
        )
    )
    disk_obj = SimpleNamespace(
        spec=SimpleNamespace(
            type="NETWORK_SSD",
            size_gibibytes=1,
        ),
        status=SimpleNamespace(
            size_bytes=250 * (1 << 30), block_size_bytes=8192, source_image_id="computeimage-123"
        ),
    )

    spec = VMSpec.from_live_vm(vm_obj, disk_obj, image_family="ubuntu24.04-driverless")

    assert spec.platform == "cpu-e2"
    assert spec.cores == 8
    assert spec.memory_gb == 32
    assert spec.disk_boot_image == "ubuntu24.04-driverless"
    assert spec.source_image_id == "computeimage-123"
    assert spec.disk_gb == 250
    assert spec.disk_block_bytes == 8192
    assert spec.num_nics == 2


def test_sparse_live_metadata_cannot_fabricate_a_matching_default():
    with pytest.raises(RuntimeError, match="incomplete"):
        VMSpec.from_live_vm(
            SimpleNamespace(), SimpleNamespace(), image_family="ubuntu24.04-driverless"
        )


def test_family_comparison_ignores_age_but_not_a_different_actual_family():
    analyzer = VMDiffAnalyzer()
    actual = _vm_spec(source_image_id="computeimage-older")
    assert not analyzer.compare(_vm_spec(), actual).has_changes()
    changed_family = _vm_spec(
        disk_boot_image="different-family", source_image_id="computeimage-other"
    )
    assert analyzer.compare(_vm_spec(), changed_family).requires_recreation()
    assert analyzer.compare(
        _vm_spec(source_image_id="computeimage-new"), actual
    ).requires_recreation()
