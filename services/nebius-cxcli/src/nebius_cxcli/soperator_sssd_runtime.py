"""Runtime-only SSSD configuration and optional directory CA delivery."""

from __future__ import annotations

import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any

from .runtime_config import to_plain_data
from .soperator_backup_runtime import _env_value, _soperator_row_target_ref, _target_env_names
from .soperator_runtime_objects import RuntimeObjects
from .soperator_values import read_soperator_input_text, soperator_rows, validate_object_name

SSSD_CONFIG_FILE_ENV = "NEBIUS_CXCLI_SOPERATOR_SSSD_CONFIG_FILE"
SSSD_LDAP_CA_FILE_ENV = "NEBIUS_CXCLI_SOPERATOR_SSSD_LDAP_CA_FILE"


@dataclass(frozen=True)
class SssdObject:
    kind: str
    namespace: str
    name: str
    key: str
    env_name: str


def sssd_objects(
    payload_or_config: Any, *, target_ref: str, namespaces: Mapping[str, str]
) -> tuple[SssdObject, ...]:
    result: set[SssdObject] = set()
    for row in soperator_rows(to_plain_data(payload_or_config)):
        if _soperator_row_target_ref(row) != target_ref:
            continue
        values = row.get("values", {})
        shared = values.get("sssd")
        consumers: list[tuple[str, Mapping]] = []
        if isinstance(shared, Mapping):
            consumers.append(("slurmCluster", shared))
            if values.get("nodesets"):
                consumers.append(("nodesets", shared))
        else:
            consumers.append(("slurmCluster", values.get("slurmNodes", {}).get("sssd", {})))
            for nodeset in values.get("nodesets", []):
                consumers.append(
                    ("nodesets", {**nodeset, "enabled": nodeset.get("sssd", {}).get("enabled")})
                )
        for role, config in consumers:
            if config.get("enabled") is not True:
                continue
            namespace = namespaces.get(role)
            if namespace is None:
                raise ValueError(f"SSSD has no rendered {role} consumer namespace")
            for field, kind, key, env in (
                ("sssdConfSecretRefName", "Secret", "sssd.conf", SSSD_CONFIG_FILE_ENV),
                ("sssdLdapCAConfigMapRefName", "ConfigMap", "ca.crt", SSSD_LDAP_CA_FILE_ENV),
            ):
                name = config.get(field)
                if not name and kind == "ConfigMap":
                    continue
                validate_object_name(name, f"sssd.{field}")
                result.add(SssdObject(kind, namespace, str(name), key, env))
    return tuple(sorted(result, key=lambda obj: (obj.namespace, obj.kind, obj.name)))


def _input_path(
    obj: SssdObject, target_ref: str, extra_env: Mapping[str, str] | None, *, prompt: bool
) -> str:
    path = _env_value(obj.env_name, target_ref=target_ref, extra_env=extra_env)
    if not path and prompt:
        path = getpass(f"Local file for SSSD {obj.kind} key {obj.key} (path hidden): ").strip()
    return path


def _file_material(
    obj: SssdObject, target_ref: str, extra_env: Mapping[str, str] | None, *, prompt: bool = False
) -> str:
    env_name = _target_env_names(obj.env_name, target_ref)[0]
    path = _input_path(obj, target_ref, extra_env, prompt=prompt)
    if not path:
        raise RuntimeError(
            f"Set {env_name} or precreate the referenced SSSD {obj.kind} with key {obj.key}"
        )
    try:
        material = read_soperator_input_text(Path(path))
        if not material.strip() or "\0" in material:
            raise ValueError
        if obj.kind == "ConfigMap":
            ssl.create_default_context(cadata=material)
    except (OSError, UnicodeError, ValueError, ssl.SSLError):
        raise RuntimeError(
            f"{env_name} must reference a readable nonempty regular file containing {'a PEM CA bundle' if obj.kind == 'ConfigMap' else 'SSSD configuration'} below 1 MiB"
        ) from None
    return material


def preflight_soperator_sssd_inputs(
    payload_or_config: Any, *, target_ref: str, prompt: bool = False
) -> dict[str, str]:
    runtime_env: dict[str, str] = {}
    for obj in sssd_objects(
        payload_or_config, target_ref=target_ref, namespaces={"slurmCluster": "", "nodesets": ""}
    ):
        runtime_env[_target_env_names(obj.env_name, target_ref)[0]] = _input_path(
            obj, target_ref, runtime_env, prompt=prompt
        )
        _file_material(obj, target_ref, runtime_env)
    return runtime_env


def ensure_soperator_sssd_runtime(
    payload_or_config: Any,
    *,
    target_ref: str,
    namespaces: Mapping[str, str],
    extra_env: Mapping[str, str] | None,
    assert_authority: Callable[[], object],
    emit: Callable[[str], None] | None = None,
    prompt: bool = False,
) -> None:
    objects = sssd_objects(payload_or_config, target_ref=target_ref, namespaces=namespaces)
    if not objects:
        return
    runtime = RuntimeObjects(extra_env=extra_env, assert_authority=assert_authority)
    # Validate every missing input before making the first runtime write.
    pending = [
        (obj, _file_material(obj, target_ref, extra_env, prompt=prompt))
        for obj in objects
        if not runtime.complete(obj.kind, obj.namespace, obj.name, (obj.key,))
    ]
    for obj, material in pending:
        runtime.create(obj.kind, obj.namespace, obj.name, {obj.key: material})
        if emit:
            emit(f"Created SSSD {obj.kind} `{obj.name}`.")
