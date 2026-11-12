"""Completed handover history must not strand a later exact fenced apply."""

import hashlib
import json

import pytest
from tests.unit.test_vm_ha_restoration import _completed

from nebius_vpngw import ha_activation as guest


def write_private(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.fixture
def completed_apply(tmp_path, monkeypatch):
    store = _completed(tmp_path)
    store.receipt_path.chmod(0o600)
    config, boot, forwarding = (tmp_path / name for name in ("config", "boot", "forwarding"))
    config.write_text("exact approved configuration")
    boot.write_text("current-boot")
    forwarding.write_text("0")
    guard = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "guard_boot_id": "current-boot",
        "node_id": "node-a",
        "data_plane_mode": "passive",
    }
    lock = {
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
        "apply_locked": True,
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "operation_id": "b" * 64,
    }
    for name, value in [("guard.json", guard), ("apply.lock", lock)]:
        write_private(tmp_path / name, json.dumps(value).encode())
    monkeypatch.setattr(guest, "STATE", tmp_path)
    monkeypatch.setattr(guest, "CONFIG", config)
    monkeypatch.setattr(guest, "BOOT", boot)
    monkeypatch.setattr(guest, "FORWARDING", forwarding)
    return store, {"lock": lock, "configuration": hashlib.sha256(config.read_bytes()).hexdigest()}


def test_exact_completed_receipt_allows_activation_without_retiring_history(completed_apply):
    store, request = completed_apply
    before = store.path.read_bytes()
    assert guest.require_authority(request) == "current-boot"
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("field", ["cluster_id", "node_id", "generation_id"])
def test_completed_history_cannot_authorize_another_binding(completed_apply, field):
    store, request = completed_apply
    request["lock"][field] = "c" * 64 if field == "generation_id" else "foreign"
    write_private(store.path.parent / "apply.lock", json.dumps(request["lock"]).encode())
    if field == "node_id":
        guard = json.loads((store.path.parent / "guard.json").read_text())
        guard["node_id"] = "foreign"
        write_private(store.path.parent / "guard.json", json.dumps(guard).encode())
    with pytest.raises(RuntimeError, match="foreign restoration"):
        guest.require_authority(request)


def test_completed_restoration_still_requires_durable_receipt(completed_apply):
    store, request = completed_apply
    store.receipt_path.unlink()
    with pytest.raises(OSError):
        guest.require_authority(request)


def test_completed_restoration_rejects_changed_receipt(completed_apply):
    store, request = completed_apply
    receipt = json.loads(store.receipt_path.read_text())
    receipt["ownership_epoch"] = "foreign-epoch"
    write_private(store.receipt_path, json.dumps(receipt).encode())
    with pytest.raises(RuntimeError):
        guest.require_authority(request)


def test_completed_restoration_does_not_bypass_current_forwarding_fence(completed_apply):
    _, request = completed_apply
    guest.FORWARDING.write_text("1")
    with pytest.raises(RuntimeError, match="forwarding fence"):
        guest.require_authority(request)


@pytest.mark.parametrize("phase", ["running", "awaiting-standby", "blocked"])
def test_noncompleted_restoration_remains_blocking(completed_apply, phase):
    store, request = completed_apply
    value = json.loads(store.path.read_text())
    value["phase"] = phase
    if phase == "blocked":
        value["blocked_reason"] = "standby-readiness-timeout"
    write_private(store.path, json.dumps(value).encode())
    with pytest.raises(RuntimeError):
        guest.require_authority(request)


def test_completed_record_symlink_is_not_trusted(completed_apply):
    store, request = completed_apply
    saved = store.path.with_suffix(".saved")
    store.path.rename(saved)
    store.path.symlink_to(saved)
    with pytest.raises(OSError):
        guest.require_authority(request)
