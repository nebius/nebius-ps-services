from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from nebius_vpngw.agent.vm_ha.mtls import (
    ManagedMTLSError,
    ManagedMTLSStore,
    MTLSOperationKind,
    MTLSReceipt,
)
from nebius_vpngw.vm_ha_tls import (
    VM_HA_MTLS_NOT_AFTER,
    VM_HA_MTLS_NOT_BEFORE,
    generate_vm_ha_managed_identity,
    validate_vm_ha_managed_certificate,
)


def _operation(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _stores(tmp_path: Path) -> tuple[ManagedMTLSStore, ManagedMTLSStore]:
    return (
        ManagedMTLSStore(tmp_path / "member-a"),
        ManagedMTLSStore(tmp_path / "member-b"),
    )


def _prepare_pair(
    first: ManagedMTLSStore,
    second: ManagedMTLSStore,
    *,
    operation_id: str,
    operation_kind: MTLSOperationKind,
    epoch: int,
) -> tuple[object, object]:
    first_receipt = first.prepare_identity(
        operation_id=operation_id,
        operation_kind=operation_kind,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        compute_id="compute-a",
        target_epoch=epoch,
    )
    second_receipt = second.prepare_identity(
        operation_id=operation_id,
        operation_kind=operation_kind,
        cluster_id="gateway-cluster",
        node_id="gateway-b",
        compute_id="compute-b",
        target_epoch=epoch,
    )
    first.stage_peer_leaf(
        operation_id=operation_id,
        peer_node_id="gateway-b",
        peer_compute_id="compute-b",
        peer_epoch=epoch,
        certificate_pem=second_receipt.certificate_pem,
    )
    second.stage_peer_leaf(
        operation_id=operation_id,
        peer_node_id="gateway-a",
        peer_compute_id="compute-a",
        peer_epoch=epoch,
        certificate_pem=first_receipt.certificate_pem,
    )
    return first_receipt, second_receipt


def _bootstrap_pair(
    first: ManagedMTLSStore,
    second: ManagedMTLSStore,
) -> tuple[object, object]:
    operation_id = _operation("bootstrap")
    receipts = _prepare_pair(
        first,
        second,
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        epoch=1,
    )
    assert first.expand_peer_trust(operation_id) is None
    assert second.expand_peer_trust(operation_id) is None
    first.activate_identity(operation_id)
    second.activate_identity(operation_id)
    first_receipt, second_receipt = receipts
    first.record_fresh_observation(
        operation_id,
        local_certificate_fingerprint=first_receipt.certificate_fingerprint,
        peer_certificate_fingerprint=second_receipt.certificate_fingerprint,
        local_epoch=1,
        peer_epoch=1,
        observation_id=_operation("bootstrap-first-observation"),
    )
    second.record_fresh_observation(
        operation_id,
        local_certificate_fingerprint=second_receipt.certificate_fingerprint,
        peer_certificate_fingerprint=first_receipt.certificate_fingerprint,
        local_epoch=1,
        peer_epoch=1,
        observation_id=_operation("bootstrap-second-observation"),
    )
    first.commit_transaction(operation_id)
    second.commit_transaction(operation_id)
    first.prune_obsolete(operation_id)
    second.prune_obsolete(operation_id)
    return receipts


def test_managed_identity_has_fixed_non_expiring_leaf_profile() -> None:
    identity = generate_vm_ha_managed_identity("gateway-a")
    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
    private_key = serialization.load_pem_private_key(identity.private_key_pem, None)

    assert isinstance(private_key, ec.EllipticCurvePrivateKey)
    assert isinstance(private_key.curve, ec.SECP256R1)
    assert certificate.not_valid_before_utc == VM_HA_MTLS_NOT_BEFORE
    assert certificate.not_valid_after_utc == VM_HA_MTLS_NOT_AFTER
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert (
        validate_vm_ha_managed_certificate(
            "gateway-a",
            identity.certificate_pem,
            private_key_pem=identity.private_key_pem,
            now=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ).certificate_fingerprint
        == identity.certificate_fingerprint
    )


def test_managed_identities_use_distinct_random_keypairs() -> None:
    first = generate_vm_ha_managed_identity("gateway-a")
    second = generate_vm_ha_managed_identity("gateway-b")

    assert first.spki_fingerprint != second.spki_fingerprint
    assert first.private_key_pem != second.private_key_pem


def test_prepare_is_idempotent_and_receipt_is_public_only(tmp_path: Path) -> None:
    store = ManagedMTLSStore(tmp_path / "member-a")
    operation_id = _operation("prepare")

    first = store.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        compute_id="compute-a",
        target_epoch=1,
    )
    second = store.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        compute_id="compute-a",
        target_epoch=1,
    )

    assert first == second
    encoded = json.dumps(first.to_dict()).lower()
    assert "private" not in encoded
    assert "begin private key" not in encoded
    assert not hasattr(first, "private_key_pem")
    key_paths = tuple((tmp_path / "member-a" / "identities").glob("*/private-key.pem"))
    assert len(key_paths) == 1
    assert stat.S_IMODE(key_paths[0].stat().st_mode) == 0o600
    assert "BEGIN PRIVATE KEY" in key_paths[0].read_text()


def test_bootstrap_and_rotation_keep_overlap_until_verified(tmp_path: Path) -> None:
    first, second = _stores(tmp_path)
    old_first, old_second = _bootstrap_pair(first, second)
    operation_id = _operation("rotation")
    for store, node_id in ((first, "gateway-a"), (second, "gateway-b")):
        store.install_inhibition(
            operation_id=operation_id,
            cluster_id="gateway-cluster",
            node_id=node_id,
            generation_id="a" * 64,
        )
    new_first, new_second = _prepare_pair(
        first,
        second,
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.ROTATION,
        epoch=2,
    )

    first_expanded = first.expand_peer_trust(operation_id)
    second_expanded = second.expand_peer_trust(operation_id)
    assert first_expanded is not None and second_expanded is not None
    assert {peer.certificate_fingerprint for peer in first_expanded.peers} == {
        old_second.certificate_fingerprint,
        new_second.certificate_fingerprint,
    }
    assert {peer.certificate_fingerprint for peer in second_expanded.peers} == {
        old_first.certificate_fingerprint,
        new_first.certificate_fingerprint,
    }

    second.activate_identity(operation_id)
    first.activate_identity(operation_id)
    with pytest.raises(ManagedMTLSError, match="lacks fresh verification"):
        first.commit_transaction(operation_id)

    for store, local, peer in (
        (first, new_first, new_second),
        (second, new_second, new_first),
    ):
        for index in range(3):
            store.record_fresh_observation(
                operation_id,
                local_certificate_fingerprint=local.certificate_fingerprint,
                peer_certificate_fingerprint=peer.certificate_fingerprint,
                local_epoch=2,
                peer_epoch=2,
                observation_id=_operation(f"{local.node_id}-observation-{index}"),
            )
        store.commit_transaction(operation_id)
        snapshot = store.prune_obsolete(operation_id)
        assert snapshot.epoch == 2
        assert snapshot.certificate_fingerprint == local.certificate_fingerprint
        assert tuple(item.certificate_fingerprint for item in snapshot.peers) == (
            peer.certificate_fingerprint,
        )
        status = store.status()
        assert status.state == "transitioning"
        assert status.operation_id == operation_id
        assert status.phase == "pruned"
        assert status.target_epoch == 2
        store.release_inhibition(operation_id)
        assert store.status().state == "healthy"


def test_inhibition_is_exact_idempotent_and_read_only_status_creates_nothing(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing"
    assert ManagedMTLSStore(missing_root, create=False).status().state == "missing"
    assert not missing_root.exists()

    store = ManagedMTLSStore(tmp_path / "member-a")
    operation_id = _operation("inhibition")
    payload = store.install_inhibition(
        operation_id=operation_id,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        generation_id="a" * 64,
    )
    assert (
        store.install_inhibition(
            operation_id=operation_id,
            cluster_id="gateway-cluster",
            node_id="gateway-a",
            generation_id="a" * 64,
        )
        == payload
    )
    with pytest.raises(ManagedMTLSError, match="inhibition conflicts"):
        store.install_inhibition(
            operation_id=_operation("foreign-inhibition"),
            cluster_id="gateway-cluster",
            node_id="gateway-a",
            generation_id="a" * 64,
        )
    with pytest.raises(ManagedMTLSError, match="another operation"):
        store.release_inhibition(_operation("foreign-release"))
    assert store.inhibition_operation_id() == operation_id
    assert store.release_inhibition(operation_id) == {
        "released": True,
        "operation_id": operation_id,
    }
    assert store.release_inhibition(operation_id) == {
        "released": True,
        "operation_id": operation_id,
    }


def test_public_receipt_round_trip_validates_certificate_and_digest(tmp_path: Path) -> None:
    store = ManagedMTLSStore(tmp_path / "member-a")
    receipt = store.prepare_identity(
        operation_id=_operation("receipt"),
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        compute_id="compute-a",
        target_epoch=1,
    )

    assert MTLSReceipt.from_mapping(receipt.to_dict()) == receipt
    tampered = receipt.to_dict()
    tampered["certificate_fingerprint"] = "0" * 64
    with pytest.raises(ManagedMTLSError, match="digest mismatch"):
        MTLSReceipt.from_mapping(tampered)


def test_rotation_observations_must_be_distinct(tmp_path: Path) -> None:
    first, second = _stores(tmp_path)
    _bootstrap_pair(first, second)
    operation_id = _operation("unique-observations")
    first_receipt, second_receipt = _prepare_pair(
        first,
        second,
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.ROTATION,
        epoch=2,
    )
    first.expand_peer_trust(operation_id)
    second.expand_peer_trust(operation_id)
    first.activate_identity(operation_id)
    second.activate_identity(operation_id)

    observation_id = _operation("one-observation")
    for _ in range(3):
        assert (
            first.record_fresh_observation(
                operation_id,
                local_certificate_fingerprint=first_receipt.certificate_fingerprint,
                peer_certificate_fingerprint=second_receipt.certificate_fingerprint,
                local_epoch=2,
                peer_epoch=2,
                observation_id=observation_id,
            )
            == 1
        )
    with pytest.raises(ManagedMTLSError, match="lacks fresh verification"):
        first.commit_transaction(operation_id)


def test_replacement_preserves_survivor_identity_and_prunes_old_peer(tmp_path: Path) -> None:
    survivor, former = _stores(tmp_path)
    survivor_receipt, former_receipt = _bootstrap_pair(survivor, former)
    operation_id = _operation("replacement")
    replacement = ManagedMTLSStore(tmp_path / "replacement")
    replacement_receipt = replacement.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.REPLACEMENT,
        cluster_id="gateway-cluster",
        node_id="gateway-b",
        compute_id="compute-b-new",
        target_epoch=2,
        peer_epoch=1,
    )
    survivor_public = survivor.prepare_peer_replacement(
        operation_id=operation_id,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        compute_id="compute-a",
        target_peer_epoch=2,
    )
    assert survivor_public.certificate_fingerprint == survivor_receipt.certificate_fingerprint
    survivor.stage_peer_receipt(operation_id, replacement_receipt)
    replacement.stage_peer_receipt(operation_id, survivor_public)
    survivor.expand_peer_trust(operation_id)
    replacement.expand_peer_trust(operation_id)
    survivor.activate_identity(operation_id)
    replacement.activate_identity(operation_id)
    survivor.record_fresh_observation(
        operation_id,
        local_certificate_fingerprint=survivor_receipt.certificate_fingerprint,
        peer_certificate_fingerprint=replacement_receipt.certificate_fingerprint,
        local_epoch=1,
        peer_epoch=2,
        observation_id=_operation("survivor-observed-replacement"),
    )
    replacement.record_fresh_observation(
        operation_id,
        local_certificate_fingerprint=replacement_receipt.certificate_fingerprint,
        peer_certificate_fingerprint=survivor_receipt.certificate_fingerprint,
        local_epoch=2,
        peer_epoch=1,
        observation_id=_operation("replacement-observed-survivor"),
    )
    survivor.commit_transaction(operation_id)
    replacement.commit_transaction(operation_id)

    survivor_snapshot = survivor.prune_obsolete(operation_id)
    replacement_snapshot = replacement.prune_obsolete(operation_id)
    assert survivor_snapshot.certificate_fingerprint == survivor_receipt.certificate_fingerprint
    assert survivor_snapshot.spki_fingerprint == survivor_receipt.spki_fingerprint
    assert survivor_snapshot.peers[0].certificate_fingerprint == (
        replacement_receipt.certificate_fingerprint
    )
    assert former_receipt.certificate_fingerprint not in {
        peer.certificate_fingerprint for peer in survivor_snapshot.peers
    }
    assert replacement_snapshot.peers[0].certificate_fingerprint == (
        survivor_receipt.certificate_fingerprint
    )


def test_rotation_can_rollback_only_before_local_switch(tmp_path: Path) -> None:
    first, second = _stores(tmp_path)
    _bootstrap_pair(first, second)
    operation_id = _operation("rollback")
    _prepare_pair(
        first,
        second,
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.ROTATION,
        epoch=2,
    )
    first.expand_peer_trust(operation_id)
    first.rollback_before_switch(operation_id)
    assert first.status().state == "healthy"

    second.expand_peer_trust(operation_id)
    second.activate_identity(operation_id)
    with pytest.raises(ManagedMTLSError, match="roll forward"):
        second.rollback_before_switch(operation_id)


def test_snapshot_rejects_private_key_permission_drift(tmp_path: Path) -> None:
    first, second = _stores(tmp_path)
    _bootstrap_pair(first, second)
    key_path = first.snapshot().private_key_path
    key_path.chmod(0o640)

    with pytest.raises(ManagedMTLSError, match="permissions"):
        first.snapshot()


def test_store_rejects_symlink_state_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ManagedMTLSError, match="directory permissions"):
        ManagedMTLSStore(linked)


def test_peer_certificate_with_wrong_node_identity_is_rejected(tmp_path: Path) -> None:
    store = ManagedMTLSStore(tmp_path / "member-a")
    operation_id = _operation("wrong-peer")
    store.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="gateway-cluster",
        node_id="gateway-a",
        compute_id="compute-a",
        target_epoch=1,
    )
    wrong = generate_vm_ha_managed_identity("gateway-c")

    with pytest.raises(ValueError, match="gateway-b is invalid"):
        store.stage_peer_leaf(
            operation_id=operation_id,
            peer_node_id="gateway-b",
            peer_compute_id="compute-b",
            peer_epoch=1,
            certificate_pem=wrong.certificate_pem,
        )


def test_state_files_are_owner_only(tmp_path: Path) -> None:
    first, second = _stores(tmp_path)
    _bootstrap_pair(first, second)

    for root, directories, files in os.walk(tmp_path):
        for name in directories:
            assert stat.S_IMODE((Path(root) / name).stat().st_mode) == 0o700
        for name in files:
            assert stat.S_IMODE((Path(root) / name).stat().st_mode) == 0o600
