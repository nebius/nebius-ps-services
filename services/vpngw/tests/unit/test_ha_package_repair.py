from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nebius_vpngw import ha_repair
from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint
from nebius_vpngw.deploy.ordinary_handoff import streamed_source


@pytest.fixture
def evidence_guest(tmp_path, monkeypatch):
    def write(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    boot = tmp_path / "boot"
    boot.write_text("12345678-1234-1234-1234-123456789012")
    forwarding = tmp_path / "forwarding"
    forwarding.write_text("0\n")
    monkeypatch.setattr(ha_repair, "BOOT", boot)
    monkeypatch.setattr(ha_repair, "FORWARDING", forwarding)
    expected = dict(
        cluster_id="cluster",
        node_id="node-a",
        compute_id="compute-a",
        generation_id="a" * 64,
        operation_id="b" * 64,
    )
    write(
        "apply.lock",
        dict(
            schema="nebius-vpngw/vm-ha-apply-lock-v2",
            apply_locked=True,
            **{key: value for key, value in expected.items() if key != "compute_id"},
        ),
    )
    write(
        "guard.json",
        dict(
            schema=ha_repair.STATUS_SCHEMA,
            guard_boot_id=boot.read_text(),
            data_plane_mode="blocked",
            installed_at=1,
        ),
    )
    write("controller-checkpoint.json", controller_checkpoint_to_dict(ControllerCheckpoint()))
    active = dict(
        schema="nebius-vpngw/vm-ha-mtls-active-v1",
        cluster_id="cluster",
        node_id="node-a",
        compute_id="compute-a",
        epoch=1,
        certificate_fingerprint="c" * 64,
        spki_fingerprint="d" * 64,
        peers=[],
        operation_id="e" * 64,
    )
    write("mtls/active.json", active)
    write(
        "mtls/transactions/" + "e" * 64 + ".json",
        dict(
            schema="nebius-vpngw/vm-ha-mtls-transaction-v1",
            operation_id="e" * 64,
            operation_kind="bootstrap",
            phase="local-active",
            cluster_id="cluster",
            node_id="node-a",
            compute_id="compute-a",
            target_epoch=1,
            peer_target_epoch=1,
            preserve_local=False,
        ),
    )
    return SimpleNamespace(
        root=tmp_path,
        write=write,
        expected=expected,
        boot=boot,
        forwarding=forwarding,
        active=active,
    )


def test_repair_uses_raw_canonical_records(evidence_guest):
    fixture = evidence_guest
    value = ha_repair.evidence(fixture.expected, state_dir=fixture.root)
    assert value["forwarding"] is False
    assert value["lock"]["generation_id"] == fixture.expected["generation_id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "NOT_A_STATE"),
        ("suspect_since", {}),
        ("suspect_since", float("inf")),
        ("ownership_incarnation", -1),
        ("ownership_continuity_invalidated", True),
        ("established_ownership_context", {"malformed": True}),
        ("pending_action", {"boot_id": "previous boot", "operation_id": "pending"}),
    ],
)
def test_repair_rejects_malformed_or_stale_raw_checkpoint(evidence_guest, field, value):
    fixture = evidence_guest
    checkpoint = controller_checkpoint_to_dict(ControllerCheckpoint())
    checkpoint[field] = value
    fixture.write("controller-checkpoint.json", checkpoint)
    # A projected status would erase stale-boot pending intent. It is not read.
    fixture.write("status.json", {"pending_operation_id": None, "data_plane_mode": "passive"})
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        ha_repair.evidence(fixture.expected, state_dir=fixture.root)


@pytest.mark.parametrize(
    "damage",
    [
        "generation",
        "operation",
        "boot",
        "forwarding",
        "effect",
        "active-fields",
        "missing-transaction",
        "permissions",
        "hardlink",
    ],
)
def test_repair_rejects_foreign_missing_or_unsafe_evidence(evidence_guest, damage):
    fixture = evidence_guest
    if damage in {"generation", "operation"}:
        fixture.expected[damage + "_id"] = "f" * 64
    elif damage == "boot":
        fixture.boot.write_text("another-boot")
    elif damage == "forwarding":
        fixture.forwarding.write_text("1")
    elif damage == "effect":
        fixture.write("accepted-cloud-operation.json", {"status": "complete"})
    elif damage == "active-fields":
        fixture.active.pop("peers")
        fixture.write("mtls/active.json", fixture.active)
    elif damage == "missing-transaction":
        (fixture.root / "mtls/transactions" / ("e" * 64 + ".json")).unlink()
    elif damage == "permissions":
        (fixture.root / "mtls/active.json").chmod(0o644)
    else:
        os.link(fixture.root / "mtls/active.json", fixture.root / "another-link")
    with pytest.raises((OSError, RuntimeError, ValueError)):
        ha_repair.evidence(fixture.expected, state_dir=fixture.root)


def test_missing_checkpoint_requires_proven_unstarted_owner(evidence_guest):
    fixture = evidence_guest
    (fixture.root / "controller-checkpoint.json").unlink()
    with pytest.raises(RuntimeError, match="checkpoint is missing"):
        ha_repair.evidence(fixture.expected, state_dir=fixture.root)
    assert (
        ha_repair.evidence(fixture.expected, state_dir=fixture.root, unstarted=True)[
            "checkpoint_schema"
        ]
        is None
    )
    fixture.write("status.json", {})
    with pytest.raises(RuntimeError, match="checkpoint is missing"):
        ha_repair.evidence(fixture.expected, state_dir=fixture.root, unstarted=True)


def test_streamed_repair_loads_without_installed_product_or_crypto(evidence_guest):
    fixture = evidence_guest
    blocked = """
import sys
class BlockInstalled:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'nebius_vpngw', 'cryptography', 'cffi'}:
            raise ImportError('damaged installation')
sys.meta_path.insert(0, BlockInstalled())
"""
    code = (
        blocked
        + streamed_source()
        + f"""
from pathlib import Path
import _vpngw_ha_repair as repair
repair.BOOT=Path({str(fixture.boot)!r})
repair.FORWARDING=Path({str(fixture.forwarding)!r})
assert repair.evidence({fixture.expected!r}, state_dir=Path({str(fixture.root)!r}))['forwarding'] is False
print('canonical evidence verified without installed imports')
"""
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B"], input=code, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "canonical evidence verified" in result.stdout


def test_pruned_mtls_remains_admissible_with_no_pending_transaction(evidence_guest):
    fixture = evidence_guest
    fixture.active["operation_id"] = None
    fixture.write("mtls/active.json", fixture.active)
    transaction = fixture.root / "mtls/transactions" / ("e" * 64 + ".json")
    record = json.loads(transaction.read_text())
    record["phase"] = "pruned"
    fixture.write(str(transaction.relative_to(fixture.root)), record)
    assert ha_repair.evidence(fixture.expected, state_dir=fixture.root)["transactions"] == {}
    record["phase"] = "local-active"
    fixture.write(str(transaction.relative_to(fixture.root)), record)
    with pytest.raises(RuntimeError, match="mTLS transaction"):
        ha_repair.evidence(fixture.expected, state_dir=fixture.root)
