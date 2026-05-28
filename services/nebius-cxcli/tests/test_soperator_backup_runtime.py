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


def test_ensure_backup_creates_runtime_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, object]] = []
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(backup_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        backup_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: False,
    )
    monkeypatch.setattr(
        backup_runtime,
        "_apply_secret",
        lambda *, namespace, name, string_data, extra_env: applied.append(
            {"namespace": namespace, "name": name, "string_data": dict(string_data)}
        ),
    )

    backup_runtime.ensure_soperator_backup_runtime_secrets(
        _backup_payload(),
        target_ref="cluster1",
        extra_env={
            "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID_CLUSTER1": "access-key-id",
            "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY_CLUSTER1": "secret-key",
            "NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD_CLUSTER1": "repo-password",
        },
    )

    assert applied == [
        {
            "namespace": "soperator",
            "name": "jail-backup",
            "string_data": {
                "aws-access-key-id": "access-key-id",
                "aws-access-secret-key": "secret-key",
                "backup-password": "repo-password",
            },
        }
    ]


def test_ensure_backup_requires_target_specific_env_for_target_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(backup_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        backup_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: False,
    )
    monkeypatch.setattr(
        backup_runtime,
        "_apply_secret",
        lambda **_kwargs: pytest.fail("bare backup env vars must not be accepted"),
    )

    with pytest.raises(RuntimeError, match="AWS_ACCESS_KEY_ID_CLUSTER1"):
        backup_runtime.ensure_soperator_backup_runtime_secrets(
            _backup_payload(),
            target_ref="cluster1",
            extra_env={
                "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID": "access-key-id",
                "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY": "secret-key",
                "NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD": "repo-password",
            },
        )


def test_ensure_backup_skips_existing_runtime_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[dict[str, object]] = []
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: "/usr/bin/kubectl")
    monkeypatch.setattr(backup_runtime, "_ensure_namespace", lambda _namespace, *, extra_env: None)
    monkeypatch.setattr(
        backup_runtime,
        "_secret_has_keys",
        lambda *, namespace, name, keys, extra_env: True,
    )
    monkeypatch.setattr(
        backup_runtime,
        "_apply_secret",
        lambda *, namespace, name, string_data, extra_env: applied.append(
            {"namespace": namespace, "name": name, "string_data": dict(string_data)}
        ),
    )

    backup_runtime.ensure_soperator_backup_runtime_secrets(
        _backup_payload(),
        target_ref="cluster1",
        extra_env={},
    )

    assert applied == []


def test_ensure_backup_requires_kubectl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_runtime.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="kubectl is required"):
        backup_runtime.ensure_soperator_backup_runtime_secrets(
            _backup_payload(),
            target_ref="cluster1",
            extra_env={},
        )


def test_soperator_backup_specs_use_chart_owned_k8up_dependency() -> None:
    payload = _backup_payload()

    specs = backup_runtime.soperator_backup_release_specs(payload, target_ref="cluster1")

    assert len(specs) == 1
    assert specs[0].release_name == "soperator-jail-backup"
