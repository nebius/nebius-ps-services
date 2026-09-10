"""Explicit Soperator input ownership, independent of the generic catalog."""

from __future__ import annotations

import copy
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

EXPLICIT_VALUES_FIELD = "values-explicit-paths"
SSSD_REFERENCE_FIELDS = ("sssdConfSecretRefName", "sssdLdapCAConfigMapRefName")
# These are ownership boundaries, not product defaults. Their owners derive them
# from the selected release and infrastructure, never from an advanced values file.
_PROTECTED_ROOTS = frozenset(
    {
        "images",
        "clusterName",
        "clusterType",
        "k8sNodeFilters",
        "nodesets",
        "sfs",
        "volume",
        "volumeSources",
        "populateJail",
        "storage",
        "storageClass",
        "externalNfs",
        "jailRootfs",
        "jailPersistentMounts",
        "gpuDriverJail",
        "nameOverride",
        "fullnameOverride",
        "uninstallCleanup",
        "qosConfiguration",
        "schedulingConfig",
    }
)
_CHILD_ROUTES = {
    "controllerManager": "operator",
    "serviceMonitor": "operator",
    "customContainer": "nodeConfigurator",
    "hostNetwork": "nodeConfigurator",
    "rebooter": "nodeConfigurator",
    "priorityClasses": "nodesets",
    "soperator-checks": "checks",
    "soperator-activechecks": "activeChecks",
    "soperator-backup-config": "backupConfig",
    "soperator-notifier": "notifier",
    "soperator-dcgm-exporter": "dcgmExporter",
}
_HELPERS = frozenset({"sssd", "partitionProfile", "topologyProfile"})
_DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_MAX_INPUT_BYTES = 1_048_576


class _InputLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _InputLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("Soperator values require unique string mapping keys")
        result[key] = loader.construct_object(value_node)
    return result


_InputLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_soperator_input_text(path: Path) -> str:
    """Read a bounded regular input file without waiting for a FIFO/device writer."""
    descriptor = os.open(path.expanduser(), os.O_RDONLY | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_INPUT_BYTES:
            raise ValueError("Soperator input must be a regular file below 1 MiB")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_INPUT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("Soperator input must be below 1 MiB")
    return raw.decode("utf-8")


def read_soperator_values_file(path: Path) -> dict[str, Any]:
    try:
        raw = read_soperator_input_text(path)
        value = yaml.load(raw, Loader=_InputLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        # YAML diagnostics can contain the input, including accidentally supplied credentials.
        raise ValueError("Cannot read --values-file as one valid YAML mapping") from None
    if not isinstance(value, dict):
        raise ValueError(
            "--values-file must contain a values-only mapping without a values wrapper"
        )
    validate_input_values(value)
    return value


def value_pointer(path: Sequence[str | int]) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _segments(pointer: str) -> tuple[str, ...]:
    if (
        not isinstance(pointer, str)
        or not pointer.startswith("/")
        or re.search(r"~(?![01])", pointer)
    ):
        raise ValueError("Invalid Soperator explicit-value path")
    return tuple(p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/"))


def _leaf_paths(value: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    paths: list[str] = []
    for key, child in value.items():
        if not isinstance(key, str):
            raise ValueError("Soperator values require string keys")
        path = (*prefix, key)
        if isinstance(child, Mapping) and child:
            paths.extend(_leaf_paths(child, path))
        else:
            paths.append(value_pointer(path))
    return paths


def _put(root: dict[str, Any], segments: Sequence[str], value: Any) -> None:
    current = root
    for segment in segments[:-1]:
        if not isinstance(current.get(segment), dict):
            current[segment] = {}
        current = current[segment]
    current[segments[-1]] = copy.deepcopy(value)


def explicit_values(row: Mapping[str, Any]) -> dict[str, Any]:
    paths = row.get(EXPLICIT_VALUES_FIELD, [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError("Soperator values-explicit-paths must be a list of JSON Pointers")
    result: dict[str, Any] = {}
    for pointer in paths:
        segments = _segments(pointer)
        value: Any = row.get("values", {})
        for segment in segments:
            if not isinstance(value, Mapping) or segment not in value:
                raise ValueError(f"Soperator explicit value is missing at {pointer}")
            value = value[segment]
        _put(result, segments, value)
    return result


def mark_explicit_value(row: dict[str, Any], path: Sequence[str | int]) -> None:
    # Lists are owned atomically; an index never becomes a fragile persisted pointer.
    atomic = tuple(path[: next((i for i, p in enumerate(path) if isinstance(p, int)), len(path))])
    if not atomic:
        return
    pointer = value_pointer(atomic)
    paths = set(row.get(EXPLICIT_VALUES_FIELD, []))
    if not any(pointer == old or pointer.startswith(old + "/") for old in paths):
        paths = {old for old in paths if not old.startswith(pointer + "/")}
        paths.add(pointer)
    row[EXPLICIT_VALUES_FIELD] = sorted(paths)


def merge_values(target: dict[str, Any], supplied: Mapping[str, Any]) -> None:
    for key, value in supplied.items():
        if isinstance(value, Mapping) and value and isinstance(target.get(key), dict):
            merge_values(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def seed_soperator_values(payload: dict[str, Any], supplied: Mapping[str, Any]) -> None:
    rows = soperator_rows(payload)
    if len(rows) != 1:
        raise ValueError("Soperator values input requires exactly one enabled Soperator row")
    row = rows[0]
    merge_values(row.setdefault("values", {}), supplied)
    for pointer in _leaf_paths(supplied):
        mark_explicit_value(row, _segments(pointer))


def soperator_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    apps = payload.get("apps", {})
    if not isinstance(apps, Mapping):
        return []
    return [
        row
        for row in apps.get("charts", [])
        if isinstance(row, dict) and row.get("id") == "soperator" and row.get("enabled", False)
    ]


def validate_input_values(values: Mapping[str, Any]) -> None:
    _validate_tree(values)
    if "values" in values:
        raise ValueError("--values-file must not contain a values wrapper")
    for key in values:
        if key in _PROTECTED_ROOTS:
            raise ValueError(f"Soperator values.{key} is owned by the release or infrastructure")
    slurm_nodes = values.get("slurmNodes", {})
    if "slurmNodes" in values:
        if not isinstance(slurm_nodes, Mapping) or not slurm_nodes:
            raise ValueError(
                "Soperator slurmNodes cannot replace protected infrastructure mappings"
            )
        for role, settings in slurm_nodes.items():
            if not isinstance(settings, Mapping) or not settings:
                raise ValueError(
                    f"Soperator slurmNodes.{role} cannot replace protected infrastructure mappings"
                )
    if "slurmConfig" in values and (
        not isinstance(values["slurmConfig"], Mapping) or not values["slurmConfig"]
    ):
        raise ValueError(
            "Soperator slurmConfig cannot replace the protected ephemeral policy mapping"
        )
    child_sssd = slurm_nodes.get("sssd", {}) if isinstance(slurm_nodes, Mapping) else {}
    if isinstance(child_sssd, Mapping) and set(child_sssd) & {"enabled", *SSSD_REFERENCE_FIELDS}:
        raise ValueError("Configure SSSD enablement and runtime references through values.sssd")
    for pointer in _leaf_paths(values):
        parts = _segments(pointer)
        if parts[:3] == ("controllerManager", "manager", "env") and (
            len(parts) == 3
            or parts[3]
            in {"isApparmorCrdInstalled", "isMariadbCrdInstalled", "isPrometheusCrdInstalled"}
        ):
            raise ValueError(f"Soperator {pointer} is owned by the dependency graph")
        if any(
            part
            in {
                "image",
                "images",
                "imageTag",
                "imageRepository",
                "k8sNodeFilterName",
                "nameOverride",
                "fullnameOverride",
            }
            for part in parts
        ):
            raise ValueError(f"Soperator {pointer} is owned by the release or infrastructure")
        if any(
            p
            in {
                "data",
                "stringData",
                "webhookUrl",
                "accessKeyID",
                "secretAccessKey",
                "backupPassword",
            }
            for p in parts
        ) and not (len(parts) >= 3 and parts[-3:-1] == ("secret", "keys")):
            raise ValueError(f"Soperator {pointer} cannot contain inline credentials")
        if parts[0] == "slurmNodes" and any(
            p in {"size", "k8sNodeFilterName", "volumes"} for p in parts[1:]
        ):
            raise ValueError(f"Soperator {pointer} is owned by infrastructure")
        if parts == ("slurmConfig", "suspendTime"):
            raise ValueError(f"Soperator {pointer} is owned by the worker ephemeral policy")
    for key in _CHILD_ROUTES:
        if not key.startswith("soperator-") or key not in values:
            continue
        settings = values[key]
        if not isinstance(settings, Mapping):
            raise ValueError(f"Soperator values.{key} must be a mapping")
        if "enabled" in settings and not isinstance(settings["enabled"], bool):
            raise ValueError(f"Soperator values.{key}.enabled must be a boolean")
        if (
            key in {"soperator-checks", "soperator-activechecks"}
            and settings.get("enabled") is False
        ):
            raise ValueError(f"The required {key} component cannot be disabled")
    sssd = values.get("sssd")
    if sssd is not None:
        if not isinstance(sssd, Mapping) or set(sssd) - {"enabled", *SSSD_REFERENCE_FIELDS}:
            raise ValueError("Soperator values.sssd has unsupported fields")
        if "enabled" in sssd and not isinstance(sssd["enabled"], bool):
            raise ValueError("Soperator values.sssd.enabled must be a boolean")
        for name in SSSD_REFERENCE_FIELDS:
            if name in sssd:
                validate_object_name(sssd[name], f"sssd.{name}", optional=True)
        slurm_nodes = values.get("slurmNodes", {})
        child = slurm_nodes.get("sssd", {}) if isinstance(slurm_nodes, Mapping) else {}
        if isinstance(child, Mapping) and any(k in sssd and sssd[k] != v for k, v in child.items()):
            raise ValueError("Shared SSSD configuration conflicts with slurmNodes.sssd")
    for helper in ("partitionProfile", "topologyProfile"):
        if helper in values and not isinstance(values[helper], str):
            raise ValueError(f"Soperator values.{helper} must be a string")


def validate_object_name(value: Any, label: str, *, optional: bool = False) -> None:
    if optional and value == "":
        return
    if (
        not isinstance(value, str)
        or len(value) > 253
        or any(not _DNS_NAME.fullmatch(part) or len(part) > 63 for part in value.split("."))
    ):
        raise ValueError(f"Soperator {label} must be a Kubernetes object name")


def _validate_tree(
    value: Any,
    *,
    ancestors: tuple[int, ...] = (),
    path: tuple[str, ...] = (),
    budget: list[int] | None = None,
) -> None:
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Soperator values exceed the expanded structure limit")
    if len(ancestors) > 64 or id(value) in ancestors:
        raise ValueError(
            "Soperator values cannot contain recursive or excessively nested structures"
        )
    if isinstance(value, (dict, list)):
        if isinstance(value, dict) and any(not isinstance(key, str) for key in value):
            raise ValueError("Soperator values require string mapping keys")
        children = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in children:
            if key in {
                "data",
                "stringData",
                "webhookUrl",
                "password",
                "secretAccessKey",
                "accessKeyID",
                "backupPassword",
            } and path[-2:] != ("secret", "keys"):
                raise ValueError(
                    "Soperator values cannot contain inline credentials; use runtime references"
                )
            if key in {"image", "images", "imageTag", "imageRepository"}:
                raise ValueError("Soperator images are owned by the frozen release")
            _validate_tree(
                child, ancestors=(*ancestors, id(value)), path=(*path, str(key)), budget=budget
            )
    elif value is not None and type(value) not in {str, int, float, bool}:
        raise ValueError("Soperator values must contain YAML scalar, mapping, and list values")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Soperator values cannot contain non-finite numbers")


def validate_schedule(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"Soperator {label} must be a schedule string")
    if re.fullmatch(r"@(annually|yearly|monthly|weekly|daily|hourly)(-random)?", value):
        return
    fields = value.split()
    bounds = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    valid = len(fields) == 5
    for field, (low, high) in zip(fields, bounds, strict=False):
        for item in field.split(","):
            part, sep, step = item.partition("/")
            if sep and (not step.isdigit() or not 0 < int(step) <= high + 1):
                valid = False
            if part == "*":
                continue
            endpoints = part.split("-")
            if (
                len(endpoints) > 2
                or any(not p.isdigit() or not low <= int(p) <= high for p in endpoints)
                or len(endpoints) == 2
                and int(endpoints[0]) > int(endpoints[1])
            ):
                valid = False
    if not valid:
        raise ValueError(f"Soperator {label} must be a supported five-field cron or named schedule")


def validate_feature_values(values: Mapping[str, Any]) -> None:
    backup = values.get("soperator-backup-config", {})
    if not isinstance(backup, Mapping):
        raise ValueError("Soperator backup configuration must be a mapping")
    if "enabled" in backup and not isinstance(backup["enabled"], bool):
        raise ValueError("Soperator backup enabled must be a boolean")
    secret = backup.get("secret", {})
    if not isinstance(secret, Mapping) or set(secret) - {"name", "keys"}:
        raise ValueError("Soperator backup secret accepts only name and keys references")
    if "name" in secret:
        validate_object_name(secret["name"], "backup secret.name")
    keys = secret.get("keys", {})
    if not isinstance(keys, Mapping) or set(keys) - {
        "accessKeyID",
        "secretAccessKey",
        "backupPassword",
    }:
        raise ValueError("Soperator backup secret.keys has unsupported fields")
    if any(
        not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", key) or len(key) > 253
        for key in keys.values()
    ) or len(set(keys.values())) != len(keys):
        raise ValueError("Soperator backup Secret keys must be distinct nonempty key names")
    if backup.get("enabled") is True:
        bucket = backup.get("bucket", {})
        if (
            not isinstance(bucket, Mapping)
            or not isinstance(bucket.get("name"), str)
            or not bucket["name"].strip()
        ):
            raise ValueError("Soperator backups require bucket.name")
        endpoint = bucket.get("endpoint")
        try:
            parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
            valid = (
                parsed is not None
                and parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "Soperator backups require bucket.endpoint as an HTTP(S) URL without credentials"
            )
        for key in ("backup", "prune"):
            section = backup.get(key, {})
            if not isinstance(section, Mapping):
                raise ValueError(f"Soperator backup {key} must be a mapping")
            if "schedule" in section:
                validate_schedule(section["schedule"], f"backup {key}.schedule")
        retention = backup.get("prune", {}).get("retention", {})
        if not isinstance(retention, Mapping):
            raise ValueError("Soperator backup prune.retention must be a mapping")
        if "keepDaily" in retention:
            daily = retention["keepDaily"]
            if type(daily) is not int or daily <= 0:
                raise ValueError("Soperator backup prune.retention.keepDaily must be positive")
    sssd = values.get("sssd", {})
    if not isinstance(sssd, Mapping) or set(sssd) - {"enabled", *SSSD_REFERENCE_FIELDS}:
        raise ValueError("Soperator shared SSSD configuration has unsupported fields")
    if "enabled" in sssd and not isinstance(sssd["enabled"], bool):
        raise ValueError("Soperator SSSD enabled must be a boolean")
    for name in SSSD_REFERENCE_FIELDS:
        if name in sssd:
            validate_object_name(sssd[name], f"sssd.{name}", optional=True)
    if isinstance(sssd, Mapping) and sssd.get("enabled") is True:
        validate_object_name(sssd.get("sssdConfSecretRefName"), "sssd.sssdConfSecretRefName")


def validate_frozen_input(values: Mapping[str, Any], frozen: Any) -> None:
    """Check routing against exact source defaults; Helm owns schema validation.

    A chart defaults document is deliberately not treated as a complete schema.
    Nested/open upstream maps are checked by the frozen chart's own contract.
    """
    validate_input_values(values)
    root = Path(frozen.source.source_dir)
    defaults: dict[str, Mapping[str, Any]] = {}
    for role in {"slurmCluster", *_CHILD_ROUTES.values()}:
        chart = frozen.snapshot.charts.get(role)
        if chart is None:
            continue
        raw = yaml.safe_load((root / chart.source_path / "values.yaml").read_text())
        defaults[role] = raw if isinstance(raw, Mapping) else {}
    for key, value in values.items():
        if key in _HELPERS:
            continue
        role = _CHILD_ROUTES.get(key, "slurmCluster")
        wrapper = key in _CHILD_ROUTES and key.startswith("soperator-")
        if role not in defaults or (not wrapper and key not in defaults[role]):
            raise ValueError(f"Unsupported Soperator values.{key} for the frozen release")
        if wrapper:
            if not isinstance(value, Mapping):
                raise ValueError(f"Soperator values.{key} must be a mapping")
            # enabled is a cxcli/umbrella selector, not a child values key.
            unknown = set(value) - set(defaults[role]) - {"enabled"}
            if unknown:
                raise ValueError(
                    f"Unsupported Soperator values.{key} field(s): " + ", ".join(sorted(unknown))
                )
            if role == "backupConfig":
                validate_frozen_backup_values(value, defaults[role])


def validate_frozen_backup_values(values: Mapping[str, Any], defaults: Mapping[str, Any]) -> None:
    """Reject fields in fixed template-owned maps; retention/security maps are open."""
    closed = {(), ("bucket",), ("backup",), ("prune",), ("secret",), ("secret", "keys")}

    def validate(
        current: Mapping[str, Any], source: Mapping[str, Any], path: tuple[str, ...]
    ) -> None:
        for key, value in current.items():
            if not path and key == "enabled":
                continue
            if key not in source:
                raise ValueError(f"Unsupported Soperator backup field {'.'.join((*path, key))}")
            child_path = (*path, key)
            expected = source[key]
            if child_path in closed:
                if not isinstance(value, Mapping) or not isinstance(expected, Mapping):
                    raise ValueError(f"Soperator backup {'.'.join(child_path)} must be a mapping")
                validate(value, expected, child_path)
            elif type(expected) in {bool, int, str} and type(value) is not type(expected):
                raise ValueError(f"Soperator backup {'.'.join(child_path)} has an invalid type")

    validate(values, defaults, ())


def apply_frozen_feature_defaults(payload: dict[str, Any], frozen: Any) -> None:
    chart = frozen.snapshot.charts.get("backupConfig")
    if chart is None:
        return
    defaults = yaml.safe_load(
        (Path(frozen.source.source_dir) / chart.source_path / "values.yaml").read_text()
    )
    for row in soperator_rows(payload):
        backup = row.setdefault("values", {}).setdefault("soperator-backup-config", {})
        for section, leaf in (
            ("backup", "schedule"),
            ("prune", "schedule"),
            ("prune", "retention"),
        ):
            source = defaults.get(section, {})
            if leaf in source:
                backup.setdefault(section, {}).setdefault(leaf, copy.deepcopy(source[leaf]))
