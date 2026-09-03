from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_operation_lock import (
    SoperatorLeaseAuthority,
    SoperatorOperationLease,
    SoperatorOperationLocalLock,
)


def test_local_operation_lock_is_owner_only_and_reusable(tmp_path: Path) -> None:
    path = tmp_path / "operation.lock"

    with SoperatorOperationLocalLock(path):
        assert path.stat().st_mode & 0o777 == 0o600
        with (
            pytest.raises(RuntimeError, match="already running"),
            SoperatorOperationLocalLock(path),
        ):
            pass

    assert path.exists()
    with SoperatorOperationLocalLock(path):
        assert path.stat().st_mode & 0o777 == 0o600


def test_local_operation_lock_does_not_follow_stale_pid_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text('{"pid": 999999999}\n', encoding="utf-8")
    path = tmp_path / "operation.lock"
    path.symlink_to(victim)

    with (
        pytest.raises(RuntimeError, match="not a safe regular file"),
        SoperatorOperationLocalLock(path),
    ):
        pass

    assert path.is_symlink()
    assert victim.read_text(encoding="utf-8") == '{"pid": 999999999}\n'


def test_local_operation_lock_exit_preserves_a_successor_lock(tmp_path: Path) -> None:
    path = tmp_path / "operation.lock"
    first = SoperatorOperationLocalLock(path)
    second = SoperatorOperationLocalLock(path)

    first.__enter__()
    path.unlink()
    second.__enter__()
    first.__exit__(None, None, None)

    assert path.exists()
    with (
        pytest.raises(RuntimeError, match="already running"),
        SoperatorOperationLocalLock(path),
    ):
        pass
    second.__exit__(None, None, None)


def test_cluster_lease_uses_only_canonical_operation_identity() -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
    )

    manifest = lease._manifest()  # noqa: SLF001
    annotations = manifest["metadata"]["annotations"]
    assert annotations == {
        "nebius-cxcli/operation-fingerprint": "sha256:" + "a" * 64,
        "nebius-cxcli/cluster-id": "mk8scluster-a",
    }
    assert manifest["spec"]["holderIdentity"] == lease.holder_identity


def test_cluster_lease_renewal_is_uid_and_holder_fenced() -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
    )
    lease._authority = SoperatorLeaseAuthority(  # noqa: SLF001
        lease_name=lease.name,
        lease_uid="lease-uid-a",
        holder_identity_sha256=lease._holder_sha256(lease.holder_identity),  # noqa: SLF001
        fencing_epoch=7,
        operation_fingerprint=lease.operation_fingerprint,
    )

    operations = lease._renew_operations()  # noqa: SLF001

    assert operations[0] == {
        "op": "test",
        "path": "/metadata/uid",
        "value": "lease-uid-a",
    }
    assert operations[1] == {
        "op": "test",
        "path": "/spec/holderIdentity",
        "value": lease.holder_identity,
    }


def test_expired_takeover_retry_keeps_quiescence_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
    )
    reads = iter(
        (
            {
                "metadata": {"resourceVersion": "1"},
                "spec": {
                    "holderIdentity": "foreign-holder",
                    "leaseDurationSeconds": 1,
                    "renewTime": "1970-01-01T00:00:00Z",
                },
            },
            {
                "metadata": {"resourceVersion": "2"},
                "spec": {
                    "holderIdentity": lease.holder_identity,
                    "leaseDurationSeconds": 120,
                    "renewTime": "2099-01-01T00:00:00Z",
                },
            },
        )
    )
    monkeypatch.setattr(lease, "_read", lambda: next(reads))
    monkeypatch.setattr(
        lease,
        "_kubectl",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"metadata":{"uid":"lease-uid-a"}}',
            stderr="",
        ),
    )

    def _allocate(*, lease_uid: str) -> None:
        lease._authority = SoperatorLeaseAuthority(  # noqa: SLF001
            lease_name=lease.name,
            lease_uid=lease_uid,
            holder_identity_sha256=lease._holder_sha256(lease.holder_identity),  # noqa: SLF001
            fencing_epoch=7,
            operation_fingerprint=lease.operation_fingerprint,
        )

    monkeypatch.setattr(lease, "_allocate_fencing_epoch", _allocate)
    quiescence_attempts = 0

    def _wait_for_quiescence() -> None:
        nonlocal quiescence_attempts
        quiescence_attempts += 1
        if quiescence_attempts == 1:
            raise RuntimeError("recovery-required: prior writer remains active")

    monkeypatch.setattr(lease, "_wait_for_prior_writers_to_quiesce", _wait_for_quiescence)

    with pytest.raises(RuntimeError, match="prior writer remains active"):
        lease._acquire()  # noqa: SLF001
    lease._acquire()  # noqa: SLF001

    assert quiescence_attempts == 2


def test_cluster_lease_rejects_malformed_foreign_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        lease,
        "_read",
        lambda: {
            "metadata": {"resourceVersion": "1"},
            "spec": {
                "holderIdentity": "foreign-holder",
                "leaseDurationSeconds": "invalid",
                "renewTime": "not-a-timestamp",
            },
        },
    )
    replacements: list[object] = []
    monkeypatch.setattr(
        lease,
        "_kubectl",
        lambda *_args, **_kwargs: (
            replacements.append((_args, _kwargs))
            or SimpleNamespace(
                returncode=0,
                stdout='{"metadata":{"uid":"lease-uid-a"}}',
                stderr="",
            )
        ),
    )
    monkeypatch.setattr(lease, "_allocate_fencing_epoch", lambda **_kwargs: None)

    with pytest.raises(RuntimeError, match="expiry.*malformed"):
        lease._acquire()  # noqa: SLF001

    assert replacements == []


def test_expired_takeover_renews_lease_while_waiting_for_quiescence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress: list[str] = []
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
        emit=progress.append,
    )
    lease._authority = SoperatorLeaseAuthority(  # noqa: SLF001
        lease_name=lease.name,
        lease_uid="lease-uid-a",
        holder_identity_sha256=lease._holder_sha256(lease.holder_identity),  # noqa: SLF001
        fencing_epoch=7,
        operation_fingerprint=lease.operation_fingerprint,
    )
    active = iter((True, True, False))
    renewals: list[object] = []
    monkeypatch.setattr(lease, "_prior_writers_active", lambda: next(active))
    monkeypatch.setattr(lease, "_patch", renewals.append)
    monkeypatch.setattr("nebius_cxcli.soperator_operation_lock.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("nebius_cxcli.soperator_operation_lock.time.sleep", lambda _value: None)

    lease._wait_for_prior_writers_to_quiesce()  # noqa: SLF001

    assert len(renewals) == 3
    for operations in renewals:
        assert operations[:2] == lease._renew_operations()[:2]  # noqa: SLF001
    assert progress[0] == "Verifying that prior Soperator writers have stopped"
    assert progress[-1] == "Prior Soperator writers are quiescent"


def test_cluster_lease_progress_callback_is_presentation_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
        emit=lambda _message: (_ for _ in ()).throw(OSError("closed progress stream")),
    )
    lease._authority = SoperatorLeaseAuthority(  # noqa: SLF001
        lease_name=lease.name,
        lease_uid="lease-uid-a",
        holder_identity_sha256=lease._holder_sha256(lease.holder_identity),  # noqa: SLF001
        fencing_epoch=7,
        operation_fingerprint=lease.operation_fingerprint,
    )
    monkeypatch.setattr(lease, "_prior_writers_active", lambda: False)
    monkeypatch.setattr(lease, "_patch", lambda _operations: None)

    lease._wait_for_prior_writers_to_quiesce()  # noqa: SLF001


def test_transient_renewal_failure_reproves_and_restarts_renewal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
    )
    authority = SoperatorLeaseAuthority(
        lease_name=lease.name,
        lease_uid="lease-uid-a",
        holder_identity_sha256=lease._holder_sha256(lease.holder_identity),  # noqa: SLF001
        fencing_epoch=7,
        operation_fingerprint=lease.operation_fingerprint,
    )
    lease._authority = authority  # noqa: SLF001
    lease._renew_error = "Lost the Soperator operation Lease: temporary API outage"  # noqa: SLF001
    lease._stop.set()  # noqa: SLF001
    monkeypatch.setattr(lease, "_patch", lambda _operations: None)
    monkeypatch.setattr(
        lease,
        "_read_resource",
        lambda _kind, _name: {
            "data": {
                "epoch": "7",
                "leaseUid": "lease-uid-a",
                "holderIdentitySha256": authority.holder_identity_sha256,
                "operationFingerprint": lease.operation_fingerprint,
            }
        },
    )

    assert lease.assert_held() == authority
    assert lease._renew_error == ""  # noqa: SLF001
    assert not lease._stop.is_set()  # noqa: SLF001
    assert lease._thread is not None and lease._thread.is_alive()  # noqa: SLF001

    lease.__exit__(None, None, None)


def test_cluster_lease_rejects_lost_fencing_configmap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = SoperatorOperationLease(
        kube_context="ctx",
        cluster_id="mk8scluster-a",
        operation_fingerprint="sha256:" + "a" * 64,
    )
    lease._authority = SoperatorLeaseAuthority(  # noqa: SLF001
        lease_name=lease.name,
        lease_uid="lease-uid-a",
        holder_identity_sha256=lease._holder_sha256(lease.holder_identity),  # noqa: SLF001
        fencing_epoch=7,
        operation_fingerprint=lease.operation_fingerprint,
    )
    monkeypatch.setattr(lease, "_patch", lambda _operations: None)
    monkeypatch.setattr(
        lease,
        "_read_resource",
        lambda _kind, _name: {
            "data": {
                "epoch": "8",
                "leaseUid": "foreign",
                "holderIdentitySha256": "foreign",
                "operationFingerprint": "foreign",
            }
        },
    )

    with pytest.raises(RuntimeError, match="Lost the Soperator fencing authority"):
        lease.assert_held()
