"""Deploy-time Soperator jail backup secret bootstrap."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from getpass import getpass
from typing import Any

from .component_defaults import read_component_path
from .component_instances import component_instance_id, component_type_id
from .deploy_targets import app_chart_target_ref
from .runtime_config import to_plain_data
from .soperator_runtime_objects import RuntimeObjects

SOPERATOR_COMPONENT_ID = "soperator"
SOPERATOR_BACKUP_VALUES_KEY = "soperator-backup-config"
BACKUP_ACCESS_KEY_ID_ENV = "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID"
BACKUP_SECRET_ACCESS_KEY_ENV = "NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY"
BACKUP_REPOSITORY_PASSWORD_ENV = "NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD"
KUBE_CONTEXT_ENV = "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT"


@dataclass(frozen=True)
class SoperatorBackupSpec:
    target_ref: str
    namespace: str
    secret_name: str
    access_key_id_key: str
    secret_access_key_key: str
    backup_password_key: str


def _as_payload(value: Any) -> dict[str, Any]:
    payload = to_plain_data(value)
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _active_backup_rows(payload_or_config: Any) -> tuple[dict[str, Any], ...]:
    payload = _as_payload(payload_or_config)
    rows = _mapping(payload.get("apps")).get("charts")
    if not isinstance(rows, list):
        return ()
    return tuple(
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and bool(row.get("enabled", False))
        and component_type_id(row) == SOPERATOR_COMPONENT_ID
        and read_component_path(row, f"values.{SOPERATOR_BACKUP_VALUES_KEY}.enabled") is True
    )


def _soperator_row_target_ref(row: Mapping[str, Any]) -> str:
    target_ref = app_chart_target_ref(row)
    if target_ref:
        return target_ref
    instance_id = component_instance_id(row)
    if instance_id and instance_id != SOPERATOR_COMPONENT_ID:
        return instance_id
    return ""


def _reject_inline_secrets(values: Mapping[str, Any]) -> None:
    secret = _mapping(values.get("secret"))
    bucket = _mapping(values.get("bucket"))
    forbidden_paths = (
        ("values.secret.data", secret.get("data")),
        ("values.secret.stringData", secret.get("stringData")),
        ("values.bucket.accessKeyID", bucket.get("accessKeyID")),
        ("values.bucket.secretAccessKey", bucket.get("secretAccessKey")),
        ("values.bucket.backupPassword", bucket.get("backupPassword")),
    )
    for path, value in forbidden_paths:
        if value not in (None, ""):
            raise RuntimeError(
                "apps.charts[] soperator values must not contain "
                f"values.{SOPERATOR_BACKUP_VALUES_KEY}.{path.removeprefix('values.')}. "
                "Store backup credentials in the runtime Kubernetes Secret referenced by "
                f"values.{SOPERATOR_BACKUP_VALUES_KEY}.secret.name and "
                f"values.{SOPERATOR_BACKUP_VALUES_KEY}.secret.keys.*."
            )


def soperator_backup_release_specs(
    payload_or_config: Any,
    *,
    target_ref: str = "",
    namespace: str = "",
) -> tuple[SoperatorBackupSpec, ...]:
    normalized_target_ref = str(target_ref or "").strip().lower()
    active_rows: list[tuple[dict[str, Any], str]] = []
    for row in _active_backup_rows(payload_or_config):
        row_target_ref = _soperator_row_target_ref(row)
        if normalized_target_ref and row_target_ref != normalized_target_ref:
            continue
        if not normalized_target_ref and row_target_ref:
            continue
        active_rows.append((row, row_target_ref))
    specs: list[SoperatorBackupSpec] = []
    for row, row_target_ref in active_rows:
        values = _mapping(row.get("values"))
        backup_values = _mapping(values.get(SOPERATOR_BACKUP_VALUES_KEY))
        _reject_inline_secrets(backup_values)
        secret = _mapping(backup_values.get("secret"))
        secret_keys = _mapping(secret.get("keys"))
        specs.append(
            SoperatorBackupSpec(
                target_ref=row_target_ref,
                namespace=namespace,
                secret_name=str(secret.get("name") or "jail-backup").strip() or "jail-backup",
                access_key_id_key=str(
                    secret_keys.get("accessKeyID") or "aws-access-key-id"
                ).strip(),
                secret_access_key_key=str(
                    secret_keys.get("secretAccessKey") or "aws-access-secret-key"
                ).strip(),
                backup_password_key=str(
                    secret_keys.get("backupPassword") or "backup-password"
                ).strip(),
            )
        )
    return tuple(specs)


def soperator_backup_enabled_for_target(
    payload_or_config: Any,
    *,
    target_ref: str = "",
) -> bool:
    return bool(soperator_backup_release_specs(payload_or_config, target_ref=target_ref))


def _kubectl_env(extra_env: Mapping[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return env


def _target_env_names(base_name: str, target_ref: str) -> tuple[str, ...]:
    suffix = re.sub(r"[^A-Z0-9]+", "_", str(target_ref or "").upper()).strip("_")
    if suffix:
        return (f"{base_name}_{suffix}",)
    return (base_name,)


def _env_value(
    base_name: str,
    *,
    target_ref: str,
    extra_env: Mapping[str, str] | None,
) -> str:
    merged = _kubectl_env(extra_env)
    for name in _target_env_names(base_name, target_ref):
        value = str(merged.get(name) or "").strip()
        if value:
            return value
    return ""


def _secret_material(
    spec: SoperatorBackupSpec,
    *,
    extra_env: Mapping[str, str] | None,
    prompt: bool,
) -> dict[str, str]:
    values = {
        spec.access_key_id_key: _env_value(
            BACKUP_ACCESS_KEY_ID_ENV,
            target_ref=spec.target_ref,
            extra_env=extra_env,
        ),
        spec.secret_access_key_key: _env_value(
            BACKUP_SECRET_ACCESS_KEY_ENV,
            target_ref=spec.target_ref,
            extra_env=extra_env,
        ),
        spec.backup_password_key: _env_value(
            BACKUP_REPOSITORY_PASSWORD_ENV,
            target_ref=spec.target_ref,
            extra_env=extra_env,
        ),
    }
    if prompt:
        if not values[spec.access_key_id_key]:
            values[spec.access_key_id_key] = getpass(
                f"Soperator backup access key ID for {spec.namespace}/{spec.secret_name}: "
            ).strip()
        if not values[spec.secret_access_key_key]:
            values[spec.secret_access_key_key] = getpass(
                f"Soperator backup secret access key for {spec.namespace}/{spec.secret_name}: "
            ).strip()
        if not values[spec.backup_password_key]:
            values[spec.backup_password_key] = getpass(
                f"Soperator backup repository password for {spec.namespace}/{spec.secret_name}: "
            ).strip()
    missing = [key for key, value in values.items() if not value]
    if missing:
        access_key_env = _target_env_names(BACKUP_ACCESS_KEY_ID_ENV, spec.target_ref)[0]
        secret_key_env = _target_env_names(BACKUP_SECRET_ACCESS_KEY_ENV, spec.target_ref)[0]
        password_env = _target_env_names(BACKUP_REPOSITORY_PASSWORD_ENV, spec.target_ref)[0]
        raise RuntimeError(
            f"Soperator backup Secret {spec.namespace}/{spec.secret_name} is missing. "
            "Set "
            f"{access_key_env}, {secret_key_env}, and {password_env}; rerun interactively; or precreate "
            f"the Kubernetes Secret with keys: {', '.join(sorted(missing))}."
        )
    return values


def ensure_soperator_backup_runtime_secrets(
    payload_or_config: Any,
    *,
    namespace: str,
    assert_authority: Callable[[], object],
    extra_env: Mapping[str, str] | None,
    target_ref: str = "",
    prompt: bool = False,
    emit: Callable[[str], None] | None = None,
) -> None:
    """Create absent Secrets in the rendered backup consumer's namespace."""
    specs = soperator_backup_release_specs(
        payload_or_config, target_ref=target_ref, namespace=namespace
    )
    if not specs:
        return
    if not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required to deploy Soperator jail backup runtime Secret")
    runtime = RuntimeObjects(extra_env=extra_env, assert_authority=assert_authority)
    for spec in specs:
        required_keys = (
            spec.access_key_id_key,
            spec.secret_access_key_key,
            spec.backup_password_key,
        )
        if runtime.complete("Secret", spec.namespace, spec.secret_name, required_keys):
            continue
        material = _secret_material(spec, extra_env=extra_env, prompt=prompt)
        runtime.create("Secret", spec.namespace, spec.secret_name, material)
        if callable(emit):
            emit(f"Created Soperator backup Secret `{spec.secret_name}`.")


def preflight_soperator_backup_inputs(
    payload_or_config: Any, *, target_ref: str, prompt: bool
) -> None:
    """Check fresh-cluster input availability before Terraform, without persisting it."""
    for spec in soperator_backup_release_specs(payload_or_config, target_ref=target_ref):
        # Interactive credentials are collected once, at the runtime boundary.
        if not prompt:
            _secret_material(spec, extra_env=None, prompt=False)


__all__ = [
    "BACKUP_ACCESS_KEY_ID_ENV",
    "BACKUP_REPOSITORY_PASSWORD_ENV",
    "BACKUP_SECRET_ACCESS_KEY_ENV",
    "SOPERATOR_BACKUP_VALUES_KEY",
    "SoperatorBackupSpec",
    "ensure_soperator_backup_runtime_secrets",
    "soperator_backup_enabled_for_target",
    "soperator_backup_release_specs",
]
