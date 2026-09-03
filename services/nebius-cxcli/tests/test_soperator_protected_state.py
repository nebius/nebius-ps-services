from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pytest

from nebius_cxcli.soperator_infrastructure_identity import (
    ProtectedStorageIdentity,
    SfsFilesystemIdentity,
    SfsProtectedStorageIdentity,
)
from nebius_cxcli.soperator_protected_data_plane import build_protected_data_plane_receipt
from nebius_cxcli.soperator_upgrade_safety import capture_protected_customer_state
from soperator_fixtures import sample_infrastructure_receipt


@dataclass(frozen=True)
class _Result:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str = ""


def test_protected_state_capture_is_read_only_and_redacts_secret_values() -> None:
    commands: list[tuple[str, ...]] = []

    def _runner(args, *, input_text, timeout_seconds: int, check: bool):
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        commands.append(command)
        if "secrets" in command:
            payload = {
                "items": [
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "namespace": "soperator",
                            "name": "mariadb-password",
                            "uid": "secret-uid",
                            "resourceVersion": "1",
                        },
                        "type": "Opaque",
                        "data": {"password": "raw-secret-value"},
                    }
                ]
            }
        else:
            payload = {"items": []}
        return _Result(command, 0, json.dumps(payload))

    state = capture_protected_customer_state(
        command_runner=_runner,
        target_ref="cluster-a",
        namespace="soperator",
        kube_context="ctx",
        source_payload={"password": "another-raw-secret"},
    )

    serialized = json.dumps(state.as_payload(), sort_keys=True)
    assert state.complete is False
    assert any("No running login pod" in warning for warning in state.warnings)
    assert "raw-secret-value" not in serialized
    assert "another-raw-secret" not in serialized
    assert "data_sha256_by_key" in serialized
    assert commands
    assert all(command[0] == "kubectl" for command in commands)
    assert all("get" in command or "exec" in command for command in commands)


def test_recovery_capture_reuses_only_admitted_home_identity_when_login_is_absent() -> None:
    def _runner(args, *, input_text, timeout_seconds: int, check: bool):
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        payload = {
            "items": [
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "namespace": "soperator",
                        "name": "mariadb-password",
                        "uid": "secret-uid",
                        "resourceVersion": "1",
                    },
                    "type": "Opaque",
                    "data": {"password": "redacted-by-capture"},
                }
            ]
            if "secrets" in command
            else []
        }
        return _Result(command, 0, json.dumps(payload))

    state = capture_protected_customer_state(
        command_runner=_runner,
        target_ref="cluster-a",
        namespace="soperator",
        kube_context="ctx",
        admitted_home_mount_sha256="sha256:" + "a" * 64,
    )

    assert state.complete is True
    assert state.sections["slurm_runtime"] == {
        "available": True,
        "source": "admitted-protected-data-plane",
        "home_mount": {
            "available": True,
            "stdout_sha256": "sha256:" + "a" * 64,
        },
    }
    assert any("No running login pod" in warning for warning in state.warnings)


def test_recovery_capture_rejects_invalid_admitted_home_identity() -> None:
    with pytest.raises(RuntimeError, match="home-mount identity is invalid"):
        capture_protected_customer_state(
            command_runner=lambda *_args, **_kwargs: None,
            target_ref="cluster-a",
            admitted_home_mount_sha256="not-a-digest",
        )


def test_real_protected_capture_builds_data_plane_receipt() -> None:
    def _metadata(name: str, uid: str, *, namespace: str = "soperator") -> dict[str, str]:
        return {
            "name": name,
            "namespace": namespace,
            "uid": uid,
            "resourceVersion": "10",
        }

    pvcs = []
    pvs = []
    for name, pv_name in (
        ("jail-pvc", "pv-jail"),
        ("controller-spool-pvc", "pv-spool"),
        ("storage-soperator-acct-db-0", "pv-accounting"),
    ):
        pvcs.append(
            {
                "metadata": _metadata(name, f"uid-{name}"),
                "spec": {"volumeName": pv_name},
                "status": {"phase": "Bound"},
            }
        )
        pvs.append(
            {
                "metadata": _metadata(pv_name, f"uid-{pv_name}", namespace=""),
                "spec": {
                    "claimRef": {"namespace": "soperator", "name": name},
                    "persistentVolumeReclaimPolicy": "Retain",
                },
                "status": {"phase": "Bound"},
            }
        )
    pvcs.append(
        {
            "metadata": _metadata("controller-spool-controller-0", "uid-dynamic-spool"),
            "spec": {"volumeName": "pv-dynamic-spool"},
            "status": {"phase": "Bound"},
        }
    )
    pvs.append(
        {
            "metadata": _metadata("pv-dynamic-spool", "uid-pv-dynamic-spool", namespace=""),
            "spec": {
                "claimRef": {
                    "namespace": "soperator",
                    "name": "controller-spool-controller-0",
                },
                "persistentVolumeReclaimPolicy": "Delete",
            },
            "status": {"phase": "Bound"},
        }
    )

    commands: list[tuple[str, ...]] = []

    def _runner(args, *, input_text, timeout_seconds: int, check: bool):
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        commands.append(command)
        joined = " ".join(command)
        if " get pods " in f" {joined} ":
            payload = {
                "items": [
                    {
                        "metadata": {
                            **_metadata("login-0", "uid-login"),
                            "labels": {"slurm.nebius.ai/role": "login"},
                        },
                        "status": {"phase": "Running"},
                    }
                ]
            }
        elif " get pvc " in f" {joined} ":
            payload = {"items": pvcs}
        elif " get pv " in f" {joined} ":
            payload = {"items": pvs}
        elif " get secrets " in f" {joined} ":
            payload = {
                "items": [
                    {
                        "metadata": _metadata("mariadb-password", "uid-secret"),
                        "type": "Opaque",
                        "data": {"password": "redacted-by-capture"},
                    }
                ]
            }
        elif " exec login-0 " in f" {joined} " and ("scontrol " in joined or "sacctmgr " in joined):
            return _Result(
                command,
                1,
                "",
                "scontrol: fatal: Could not establish a configuration source",
            )
        else:
            return _Result(command, 0, "captured-runtime-state\n")
        return _Result(command, 0, json.dumps(payload))

    state = capture_protected_customer_state(
        command_runner=_runner,
        target_ref="cluster-a",
        namespace="soperator",
        kube_context="ctx",
    )

    infrastructure = sample_infrastructure_receipt()
    assert infrastructure.storage.sfs is not None
    infrastructure = replace(
        infrastructure,
        storage=ProtectedStorageIdentity(
            kind="sfs",
            sfs=SfsProtectedStorageIdentity(
                filesystems=tuple(
                    sorted(
                        (
                            *(
                                replace(item, pvc_names=("jail-pvc",))
                                if item.role == "jail"
                                else item
                                for item in infrastructure.storage.sfs.filesystems
                            ),
                            SfsFilesystemIdentity(
                                role="controller-spool",
                                filesystem_id="filesystem-spool",
                                mount_tag="controller-spool",
                                node_group_ids=("nodes-controller",),
                                pv_names=("pv-spool",),
                                pvc_names=("controller-spool-pvc",),
                            ),
                            SfsFilesystemIdentity(
                                role="accounting",
                                filesystem_id="filesystem-accounting",
                                mount_tag="accounting",
                                node_group_ids=("nodes-accounting",),
                                pv_names=(),
                                pvc_names=(),
                            ),
                        ),
                        key=lambda item: item.role,
                    )
                )
            ),
        ),
    )
    receipt = build_protected_data_plane_receipt(
        state=state,
        target_ref="cluster-a",
        ownership="managed",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        infrastructure=infrastructure,
    )

    assert state.complete is True
    assert receipt.home_mount_sha256.startswith("sha256:")
    assert (
        next(item.pvc.name for item in receipt.volumes if item.role == "controller-spool")
        == "controller-spool-pvc"
    )
    assert any("controller-0" in command and "slurmctld" in command for command in commands)
