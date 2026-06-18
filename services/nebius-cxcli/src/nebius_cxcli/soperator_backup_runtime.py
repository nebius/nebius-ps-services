"""Deploy-time Soperator jail backup secret bootstrap."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from getpass import getpass
from typing import Any

import yaml

from .component_defaults import read_component_path
from .component_instances import component_instance_id, component_type_id
from .deploy_targets import app_chart_target_ref
from .runtime_config import to_plain_data

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
    release_name: str
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
                namespace=str(row.get("namespace") or "soperator").strip() or "soperator",
                release_name=str(
                    backup_values.get("fullnameOverride") or "soperator-jail-backup"
                ).strip()
                or "soperator-jail-backup",
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


def _target_kube_context(extra_env: Mapping[str, str] | None) -> str:
    explicit_context = str((extra_env or {}).get(KUBE_CONTEXT_ENV) or "").strip()
    if explicit_context:
        return explicit_context
    env_context = str(os.environ.get(KUBE_CONTEXT_ENV) or "").strip()
    if env_context:
        return env_context
    kubeconfig_value = str(
        (extra_env or {}).get("KUBECONFIG") or os.environ.get("KUBECONFIG") or ""
    )
    kubeconfig_paths = (
        tuple(item for item in kubeconfig_value.split(os.pathsep) if item)
        if kubeconfig_value
        else (os.path.expanduser("~/.kube/config"),)
    )
    for kubeconfig_path in kubeconfig_paths:
        try:
            with open(kubeconfig_path, encoding="utf-8") as handle:
                payload = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):
            continue
        context = str(_mapping(payload).get("current-context") or "").strip()
        if context:
            return context
    return ""


def _kubectl_command(
    args: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None,
) -> list[str]:
    command = ["kubectl"]
    context_name = _target_kube_context(extra_env)
    if context_name:
        command.extend(["--context", context_name])
    command.extend(str(arg) for arg in args)
    return command


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _run_kubectl(
    args: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    command = _kubectl_command(args, extra_env=extra_env)
    completed = subprocess.run(
        command,
        env=_kubectl_env(extra_env),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = _first_non_empty_line(completed.stderr or completed.stdout or "")
        raise RuntimeError(f"{' '.join(command)} failed: {detail or completed.returncode}")
    return completed


def _apply_manifest(
    manifest: Mapping[str, Any],
    *,
    extra_env: Mapping[str, str] | None,
) -> None:
    rendered = yaml.safe_dump(dict(manifest), sort_keys=False)
    _run_kubectl(["apply", "-f", "-"], extra_env=extra_env, input_text=rendered)


def _ensure_namespace(namespace: str, *, extra_env: Mapping[str, str] | None) -> None:
    _apply_manifest(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace},
        },
        extra_env=extra_env,
    )


def _secret_has_keys(
    *,
    namespace: str,
    name: str,
    keys: Sequence[str],
    extra_env: Mapping[str, str] | None,
) -> bool:
    command = _kubectl_command(
        ["-n", namespace, "get", "secret", name, "-o", "json"],
        extra_env=extra_env,
    )
    completed = subprocess.run(
        command,
        env=_kubectl_env(extra_env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        if _kubectl_not_found_error(completed):
            return False
        message = _first_non_empty_line(completed.stderr or completed.stdout or "")
        raise RuntimeError(f"{' '.join(command)} failed: {message or completed.returncode}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(command)} returned invalid JSON") from exc
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    return all(str(key) in data for key in keys)


def _kubectl_not_found_error(completed: subprocess.CompletedProcess[str]) -> bool:
    detail = completed.stderr or completed.stdout or ""
    for candidate in (completed.stdout, completed.stderr):
        try:
            payload = json.loads(candidate or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and str(payload.get("reason", "") or "") == "NotFound":
            return True
    normalized = detail.lower()
    return "error from server (notfound)" in normalized or '"reason":"notfound"' in normalized


def _apply_secret(
    *,
    namespace: str,
    name: str,
    string_data: Mapping[str, str],
    extra_env: Mapping[str, str] | None,
) -> None:
    _apply_manifest(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace},
            "stringData": dict(string_data),
        },
        extra_env=extra_env,
    )


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
    extra_env: Mapping[str, str] | None,
    target_ref: str = "",
    prompt: bool = False,
    emit: Callable[[str], None] | None = None,
) -> None:
    """Create runtime-only Kubernetes Secrets required by Soperator jail backups."""
    specs = soperator_backup_release_specs(payload_or_config, target_ref=target_ref)
    if specs and not shutil.which("kubectl"):
        raise RuntimeError("kubectl is required to deploy Soperator jail backup runtime Secret")
    for spec in specs:
        _ensure_namespace(spec.namespace, extra_env=extra_env)
        required_keys = (
            spec.access_key_id_key,
            spec.secret_access_key_key,
            spec.backup_password_key,
        )
        if _secret_has_keys(
            namespace=spec.namespace,
            name=spec.secret_name,
            keys=required_keys,
            extra_env=extra_env,
        ):
            continue
        _apply_secret(
            namespace=spec.namespace,
            name=spec.secret_name,
            string_data=_secret_material(spec, extra_env=extra_env, prompt=prompt),
            extra_env=extra_env,
        )
        if callable(emit):
            emit(f"Created Soperator backup Secret `{spec.secret_name}`.")


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
