from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from nebius_cxcli.soperator_install_lease import (
    SoperatorInstallLocalLock,
    SoperatorInstallRemoteLease,
)
from nebius_cxcli.terraform_backend import TerraformBackendSettings


def _settings() -> TerraformBackendSettings:
    return TerraformBackendSettings(
        project_id="project-1",
        client_name="client-1",
        region_id="eu-north1",
        bucket="state-bucket",
        key="terraform.tfstate",
        endpoint="https://storage.example.invalid",
    )


class _InMemoryLease(SoperatorInstallRemoteLease):
    def __init__(self, *args, object_state=None, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.object_state = object_state
        self._version = 1

    def _aws(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = list(args)
        operation = command[0]
        if operation == "put-object":
            if_none_match = "--if-none-match" in command
            if_match = command[command.index("--if-match") + 1] if "--if-match" in command else ""
            if if_none_match and self.object_state is not None:
                return subprocess.CompletedProcess(command, 1, "", "PreconditionFailed 412")
            if if_match and (self.object_state is None or self.object_state["etag"] != if_match):
                return subprocess.CompletedProcess(command, 1, "", "PreconditionFailed 412")
            metadata_value = command[command.index("--metadata") + 1]
            metadata = dict(item.split("=", 1) for item in metadata_value.split(","))
            self._version += 1
            etag = f"etag-{self._version}"
            self.object_state = {"etag": etag, "metadata": metadata}
            return subprocess.CompletedProcess(command, 0, f'{{"ETag": "{etag}"}}', "")
        if operation == "head-object":
            if self.object_state is None:
                return subprocess.CompletedProcess(command, 1, "", "NotFound 404")
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    '{"ETag": "'
                    + self.object_state["etag"]
                    + '", "Metadata": '
                    + json.dumps(self.object_state["metadata"])
                    + "}"
                ),
                "",
            )
        if operation == "delete-object":
            if_match = command[command.index("--if-match") + 1]
            if self.object_state is None or self.object_state["etag"] != if_match:
                return subprocess.CompletedProcess(command, 1, "", "PreconditionFailed 412")
            self.object_state = None
            return subprocess.CompletedProcess(command, 0, "{}", "")
        raise AssertionError(command)


def test_remote_install_lease_acquires_renews_and_releases() -> None:
    lease = _InMemoryLease(
        settings=_settings(),
        target_ref="mk8s",
        operation_id="operation-a",
        ttl_seconds=60,
        renew_interval_seconds=59,
    )

    with lease:
        first_etag = lease._etag
        lease.assert_held()
        lease.bind_cluster_identity(
            cluster_id="mk8scluster-1",
            kubernetes_uid="kube-system-uid-1",
        )
        lease.assert_held()
        assert "clusteridhash" in lease.object_state["metadata"]
        lease.renew()
        assert lease._etag != first_etag

    assert lease.object_state is None


def test_remote_install_lease_rejects_active_owner() -> None:
    lease = _InMemoryLease(
        settings=_settings(),
        target_ref="mk8s",
        operation_id="operation-b",
        ttl_seconds=60,
        renew_interval_seconds=59,
        object_state={
            "etag": "etag-existing",
            "metadata": {
                "operationid": "operation-a",
                "holder": "other",
                "expiresat": str(int(time.time()) + 60),
            },
        },
    )

    with pytest.raises(RuntimeError, match="Another workstation owns"):
        lease.__enter__()


def test_remote_install_lease_takes_over_only_expired_etag() -> None:
    lease = _InMemoryLease(
        settings=_settings(),
        target_ref="mk8s",
        operation_id="operation-b",
        ttl_seconds=60,
        renew_interval_seconds=59,
        object_state={
            "etag": "etag-expired",
            "metadata": {
                "operationid": "operation-a",
                "holder": "other",
                "expiresat": str(int(time.time()) - 1),
            },
        },
    )

    with lease:
        lease.assert_held()
        assert lease.object_state["metadata"]["operationid"] == "operation-b"


@pytest.mark.parametrize("expires_at", (None, "not-an-epoch"))
def test_remote_install_lease_rejects_ambiguous_expiry(expires_at: str | None) -> None:
    metadata = {
        "operationid": "operation-a",
        "holder": "other",
    }
    if expires_at is not None:
        metadata["expiresat"] = expires_at
    lease = _InMemoryLease(
        settings=_settings(),
        target_ref="mk8s",
        operation_id="operation-b",
        ttl_seconds=60,
        renew_interval_seconds=59,
        object_state={"etag": "etag-existing", "metadata": metadata},
    )

    with pytest.raises(RuntimeError, match="invalid expiry metadata"):
        lease._acquire()

    assert lease.object_state == {"etag": "etag-existing", "metadata": metadata}


def test_remote_install_lease_rejects_expiry_while_still_owned() -> None:
    lease = _InMemoryLease(
        settings=_settings(),
        target_ref="mk8s",
        operation_id="operation-a",
        ttl_seconds=60,
        renew_interval_seconds=59,
    )

    with lease:
        lease.object_state["metadata"]["expiresat"] = str(int(time.time()) - 1)
        with pytest.raises(RuntimeError, match="expired"):
            lease.assert_held()


def test_local_install_lock_is_fail_fast_and_reusable(tmp_path: Path) -> None:
    path = tmp_path / "install.lock"

    with SoperatorInstallLocalLock(path):
        lock_inode = path.stat().st_ino
        with (
            pytest.raises(RuntimeError, match="Another Soperator install"),
            SoperatorInstallLocalLock(path),
        ):
            pass

    assert path.exists()
    with SoperatorInstallLocalLock(path):
        assert path.stat().st_ino == lock_inode


def test_local_install_lock_rejects_links_without_truncating_target(tmp_path: Path) -> None:
    protected = tmp_path / "protected.txt"
    protected.write_text("preserve me\n", encoding="utf-8")
    symlink = tmp_path / "symlink.lock"
    symlink.symlink_to(protected)

    with (
        pytest.raises(RuntimeError, match="not a safe regular file"),
        SoperatorInstallLocalLock(symlink),
    ):
        pass
    assert protected.read_text(encoding="utf-8") == "preserve me\n"

    hardlink = tmp_path / "hardlink.lock"
    hardlink.hardlink_to(protected)
    with (
        pytest.raises(RuntimeError, match="not a single-link regular file"),
        SoperatorInstallLocalLock(hardlink),
    ):
        pass
    assert protected.read_text(encoding="utf-8") == "preserve me\n"
