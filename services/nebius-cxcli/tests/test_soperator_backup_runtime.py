from __future__ import annotations

import pytest

import nebius_cxcli.soperator_backup_runtime as backup_runtime


def _backup_payload() -> dict[str, object]:
    return {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "values": {
                        "soperator-backup-config": {
                            "enabled": True,
                            "secret": {
                                "name": "jail-backup",
                                "keys": {
                                    "accessKeyID": "aws-access-key-id",
                                    "secretAccessKey": "aws-access-secret-key",
                                    "backupPassword": "backup-password",
                                },
                            },
                        }
                    },
                },
            ]
        }
    }


def test_soperator_backup_specs_reject_inline_secret_values() -> None:
    payload = _backup_payload()
    chart = payload["apps"]["charts"][0]  # type: ignore[index]
    chart["values"]["soperator-backup-config"]["secret"]["stringData"] = {  # type: ignore[index]
        "aws-access-secret-key": "do-not-store"
    }

    with pytest.raises(RuntimeError, match="stringData"):
        backup_runtime.soperator_backup_release_specs(payload, target_ref="cluster1")


def test_soperator_backup_specs_resolve_target_from_soperator_instance_id() -> None:
    payload = _backup_payload()
    chart = payload["apps"]["charts"][0]  # type: ignore[index]
    chart.pop("target_ref", None)

    specs = backup_runtime.soperator_backup_release_specs(payload, target_ref="cluster1")

    assert len(specs) == 1
    assert specs[0].target_ref == "cluster1"


class _Objects:
    created: list[tuple] = []
    present = False

    def __init__(self, **kwargs):
        self.authority = kwargs["assert_authority"]

    def complete(self, *args):
        return self.present

    def create(self, *args):
        self.authority()
        self.created.append(args)


def test_ensure_backup_creates_in_consumer_namespace(monkeypatch):
    _Objects.created = []
    _Objects.present = False
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(backup_runtime, "RuntimeObjects", _Objects)
    payload = _backup_payload()
    payload["apps"]["charts"][0]["namespace"] = "flux-system"
    backup_runtime.ensure_soperator_backup_runtime_secrets(
        payload,
        namespace="consumer",
        target_ref="cluster1",
        assert_authority=lambda: None,
        extra_env={
            "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID_CLUSTER1": "access-key-id",
            "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY_CLUSTER1": "secret-key",
            "NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD_CLUSTER1": "repo-password",
        },
    )
    assert _Objects.created == [
        (
            "Secret",
            "consumer",
            "jail-backup",
            {
                "aws-access-key-id": "access-key-id",
                "aws-access-secret-key": "secret-key",
                "backup-password": "repo-password",
            },
        )
    ]


def test_ensure_backup_requires_target_specific_env_for_target_ref(monkeypatch):
    _Objects.present = False
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(backup_runtime, "RuntimeObjects", _Objects)
    with pytest.raises(RuntimeError, match="AWS_ACCESS_KEY_ID_CLUSTER1"):
        backup_runtime.ensure_soperator_backup_runtime_secrets(
            _backup_payload(),
            namespace="consumer",
            target_ref="cluster1",
            assert_authority=lambda: None,
            extra_env={backup_runtime.BACKUP_ACCESS_KEY_ID_ENV: "global-only"},
        )


def test_ensure_backup_reuses_complete_secret_without_inputs(monkeypatch):
    _Objects.present = True
    _Objects.created = []
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(backup_runtime, "RuntimeObjects", _Objects)
    monkeypatch.setattr(backup_runtime, "getpass", lambda *args: pytest.fail("unexpected prompt"))
    backup_runtime.ensure_soperator_backup_runtime_secrets(
        _backup_payload(),
        namespace="consumer",
        target_ref="cluster1",
        assert_authority=lambda: None,
        extra_env={},
        prompt=True,
    )
    assert _Objects.created == []


def test_ensure_backup_requires_kubectl(monkeypatch):
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="kubectl is required"):
        backup_runtime.ensure_soperator_backup_runtime_secrets(
            _backup_payload(),
            namespace="consumer",
            target_ref="cluster1",
            assert_authority=lambda: None,
            extra_env={},
        )


def test_soperator_backup_specs_reference_runtime_secret() -> None:
    payload = _backup_payload()

    specs = backup_runtime.soperator_backup_release_specs(payload, target_ref="cluster1")

    assert len(specs) == 1
    assert specs[0].secret_name == "jail-backup"
