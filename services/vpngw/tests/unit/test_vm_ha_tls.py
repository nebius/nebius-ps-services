from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore, MTLSOperationKind
from nebius_vpngw.agent.vm_ha.transport import MutualTLSConfig
from nebius_vpngw.vm_ha_tls import (
    VM_HA_MTLS_NOT_AFTER,
    generate_vm_ha_managed_identity,
    validate_vm_ha_managed_certificate,
)


def _pair(tmp_path: Path) -> tuple[ManagedMTLSStore, ManagedMTLSStore]:
    operation_id = hashlib.sha256(b"tls-test-bootstrap").hexdigest()
    first = ManagedMTLSStore(tmp_path / "node-a")
    second = ManagedMTLSStore(tmp_path / "node-b")
    first_receipt = first.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="cluster-a",
        node_id="node-a",
        compute_id="compute-a",
        target_epoch=1,
    )
    second_receipt = second.prepare_identity(
        operation_id=operation_id,
        operation_kind=MTLSOperationKind.BOOTSTRAP,
        cluster_id="cluster-a",
        node_id="node-b",
        compute_id="compute-b",
        target_epoch=1,
    )
    first.stage_peer_leaf(
        operation_id=operation_id,
        peer_node_id="node-b",
        peer_compute_id="compute-b",
        peer_epoch=1,
        certificate_pem=second_receipt.certificate_pem,
    )
    second.stage_peer_leaf(
        operation_id=operation_id,
        peer_node_id="node-a",
        peer_compute_id="compute-a",
        peer_epoch=1,
        certificate_pem=first_receipt.certificate_pem,
    )
    first.activate_identity(operation_id)
    second.activate_identity(operation_id)
    return first, second


def _transfer(source: ssl.MemoryBIO, destination: ssl.MemoryBIO) -> bool:
    changed = False
    while payload := source.read():
        destination.write(payload)
        changed = True
    return changed


def _handshake(client_context: ssl.SSLContext, server_context: ssl.SSLContext) -> None:
    client_in = ssl.MemoryBIO()
    client_out = ssl.MemoryBIO()
    server_in = ssl.MemoryBIO()
    server_out = ssl.MemoryBIO()
    client = client_context.wrap_bio(
        client_in,
        client_out,
        server_side=False,
        server_hostname="node-b",
    )
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    client_done = False
    server_done = False
    for _ in range(256):
        if not client_done:
            try:
                client.do_handshake()
                client_done = True
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                pass
        _transfer(client_out, server_in)
        if not server_done:
            try:
                server.do_handshake()
                server_done = True
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                pass
        _transfer(server_out, client_in)
        if client_done and server_done:
            return
    raise AssertionError("managed mTLS handshake did not converge")


def test_direct_pinned_self_signed_leaves_complete_mutual_tls(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    client, first_snapshot = MutualTLSConfig(first.snapshot, "node-b").client_context()
    server, second_snapshot = MutualTLSConfig(second.snapshot, "node-a").server_context()

    _handshake(client, server)

    assert (
        first_snapshot.peers[0].certificate_fingerprint == second_snapshot.certificate_fingerprint
    )
    assert (
        second_snapshot.peers[0].certificate_fingerprint == first_snapshot.certificate_fingerprint
    )


def test_managed_certificate_is_fixed_to_year_9999_and_validates_without_ca() -> None:
    identity = generate_vm_ha_managed_identity("node-a")

    validated = validate_vm_ha_managed_certificate("node-a", identity.certificate_pem)

    assert validated.certificate_fingerprint == identity.certificate_fingerprint
    assert VM_HA_MTLS_NOT_AFTER.year == 9999


def test_managed_certificate_rejects_wrong_node_and_wrong_private_key() -> None:
    identity = generate_vm_ha_managed_identity("node-a")
    other = generate_vm_ha_managed_identity("node-b")

    with pytest.raises(ValueError, match="node-b is invalid"):
        validate_vm_ha_managed_certificate("node-b", identity.certificate_pem)
    with pytest.raises(ValueError, match="node-a is invalid"):
        validate_vm_ha_managed_certificate(
            "node-a",
            identity.certificate_pem,
            private_key_pem=other.private_key_pem,
        )


def test_managed_private_key_is_unencrypted_pkcs8() -> None:
    identity = generate_vm_ha_managed_identity("node-a")

    assert identity.private_key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert b"ENCRYPTED" not in identity.private_key_pem
