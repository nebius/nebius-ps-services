"""Soperator migration execution checkpoints and guarded preflight."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data
from .soperator_onboarding import (
    ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
    analyze_soperator_onboarding_snapshot,
    normalize_soperator_release_version,
    soperator_onboarding_target,
)

SOPERATOR_MIGRATION_EXECUTION_SCHEMA = "nebius-cxcli-soperator-migration-execution/v1"
SOPERATOR_MIGRATION_CHECKPOINT_DIR = ".nebius-cxcli/soperator-migrations"
_MUTATING_PHASE_IDS = frozenset(
    {
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    }
)
_ORDERED_EXECUTE_PHASE_IDS = (
    "discovery-and-plan",
    "customer-approval",
    "create-aligned-sfs",
    "online-bulk-data-sync",
    "rolling-compute-migration",
    "final-control-plane-cutover",
    "validation-and-rollback-hold",
    "retire-old-resources",
)
_SUPPORTED_EXECUTE_PHASE_IDS = frozenset(_ORDERED_EXECUTE_PHASE_IDS)
_SOPERATOR_STORAGE_KEYS = ("jail", "controller-spool", "accounting")
_SOPERATOR_SERVICE_ROLES = ("system", "controller", "login", "accounting")
_SOPERATOR_COMPUTE_ROLES = (*_SOPERATOR_SERVICE_ROLES, "worker")
_SOPERATOR_ROLE_STORAGE_KEYS: Mapping[str, tuple[str, ...]] = {
    "system": ("jail",),
    "controller": ("jail", "controller-spool"),
    "login": ("jail",),
    "accounting": ("jail", "accounting"),
    "worker": ("jail",),
}
_SOPERATOR_ROLE_SOURCE_KIND: Mapping[str, str] = {
    "system": "cpu",
    "controller": "cpu",
    "login": "cpu",
    "accounting": "cpu",
    "worker": "gpu",
}
_SOPERATOR_STORAGE_DEFAULTS: Mapping[str, Mapping[str, Any]] = {
    "jail": {
        "size_gib": 1024,
        "block_size_kib": 4,
        "mount_tag": "jail",
        "forbid_deletion": False,
        "type": "NETWORK_SSD",
    },
    "controller-spool": {
        "size_gib": 128,
        "block_size_kib": 4,
        "mount_tag": "controller-spool",
        "forbid_deletion": False,
        "type": "NETWORK_SSD",
    },
    "accounting": {
        "size_gib": 128,
        "block_size_kib": 4,
        "mount_tag": "accounting",
        "forbid_deletion": False,
        "type": "NETWORK_SSD",
    },
}
_SOPERATOR_NAMESPACE = "soperator"
_ROLLING_COMPUTE_VALUES_REVISION = 5
_TARGET_SLURM_PLUGIN_DIR = "/usr/lib/x86_64-linux-gnu/slurm"
_HELM_OWNERSHIP_CONFLICT_RE = re.compile(
    r'(?P<kind>[A-Za-z][A-Za-z0-9.]*)\s+"(?P<name>[^"]+)"\s+in namespace '
    r'"(?P<namespace>[^"]*)"\s+exists and cannot be imported into the current release',
    re.DOTALL,
)
_KUBECTL_RESOURCE_BY_KIND = {
    "ClusterRole": "clusterrole",
    "ClusterRoleBinding": "clusterrolebinding",
    "CustomResourceDefinition": "customresourcedefinition",
    "MutatingWebhookConfiguration": "mutatingwebhookconfiguration",
    "PriorityClass": "priorityclass",
    "ValidatingWebhookConfiguration": "validatingwebhookconfiguration",
}


class SoperatorMigrationPhaseBlocked(RuntimeError):
    """Checkpointed migration phase blocked before an unsafe mutation."""


@dataclass(frozen=True)
class SoperatorMigrationCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SoperatorMigrationCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        """Run an external command for a live migration phase."""


@dataclass(frozen=True)
class SoperatorAlignedFilesystemSpec:
    key: str
    name: str
    size_gib: int
    block_size_kib: int
    mount_tag: str
    forbid_deletion: bool
    filesystem_type: str


@dataclass(frozen=True)
class SoperatorMigrationExecutionResult:
    checkpoint_path: Path
    completed_phases: tuple[str, ...]
    blocked_phase: str
    blocked_reason: str
    live_source_version: str
    target_version: str
    mutation_performed: bool
    lines: tuple[str, ...]


def soperator_migration_checkpoint_path(config_path: Path, target_ref: str) -> Path:
    normalized = normalize_component_token(target_ref) or "mk8s"
    return (
        config_path.parent
        / SOPERATOR_MIGRATION_CHECKPOINT_DIR
        / normalized
        / "checkpoint.json"
    )


def soperator_migration_lock_path(config_path: Path, target_ref: str) -> Path:
    return soperator_migration_checkpoint_path(config_path, target_ref).with_suffix(".lock")


class SoperatorMigrationExecutionLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> SoperatorMigrationExecutionLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Soperator migration is already running for this target or left a lock: {self.path}. "
                "Remove the lock only after verifying no matching migration process is active."
            ) from exc
        payload = {
            "pid": os.getpid(),
            "created_at": _utc_now(),
        }
        os.write(self._fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with suppress(FileNotFoundError):
            self.path.unlink()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _command_text(args: Sequence[str]) -> str:
    return " ".join(str(item) for item in args)


def _default_command_runner(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> SoperatorMigrationCommandResult:
    completed = subprocess.run(
        list(args),
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    result = SoperatorMigrationCommandResult(
        args=tuple(str(item) for item in args),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{_command_text(args)} failed: {detail}")
    return result


def _json_from_command(
    command_runner: SoperatorMigrationCommandRunner,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> Mapping[str, Any]:
    result = command_runner(
        args,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        check=check,
    )
    if result.returncode != 0 and not check:
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(args)} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{_command_text(args)} returned a non-object JSON payload")
    return payload


def _append_event(checkpoint: dict[str, Any], event: str, **details: Any) -> None:
    events = checkpoint.setdefault("events", [])
    if not isinstance(events, list):
        return
    item: dict[str, Any] = {"at": _utc_now(), "event": event}
    for key, value in details.items():
        if value not in (None, "", (), [], {}):
            item[key] = to_plain_data(value)
    events.append(item)


def _ordered_phase_list(phases: set[str], planned_phases: Sequence[str]) -> list[str]:
    planned_order = [phase for phase in planned_phases if phase in phases]
    remaining = sorted(phases - set(planned_order))
    return [*planned_order, *remaining]


def _phase_state(checkpoint: dict[str, Any], phase_id: str) -> dict[str, Any]:
    state = checkpoint.setdefault("phase_state", {})
    if not isinstance(state, dict):
        raise RuntimeError("Soperator migration checkpoint phase_state must be a mapping.")
    phase = state.setdefault(phase_id, {})
    if not isinstance(phase, dict):
        raise RuntimeError(f"Soperator migration checkpoint phase_state.{phase_id} must be a mapping.")
    return phase


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _onboarding_actions(onboarding: Mapping[str, Any]) -> set[str]:
    return {
        str(action or "").strip()
        for action in onboarding.get("actions", []) or []
        if str(action or "").strip()
    }


def _target_payload(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    target = soperator_onboarding_target(payload, target_ref=target_ref)
    if not isinstance(target, Mapping):
        raise RuntimeError(f"Soperator target '{target_ref}' was not found in config.yaml.")
    return target


def _target_onboarding(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    target = _target_payload(payload, target_ref)
    onboarding = target.get("soperator_onboarding")
    if not isinstance(onboarding, Mapping):
        raise RuntimeError(
            f"Soperator target '{target_ref}' is missing deploy.targets[].soperator_onboarding."
        )
    return onboarding


def _target_kube_context(payload: Mapping[str, Any], target_ref: str) -> str:
    target = _target_payload(payload, target_ref)
    context = str((_mapping(target)).get("kube_context", "") or "").strip()
    if not context:
        raise RuntimeError(
            f"Soperator migration execute requires deploy.targets[].kube_context for "
            f"target '{target_ref}'. Rerun onboarding with --kube-context or select a "
            "Nebius MK8s target interactively."
        )
    return context


def _target_cluster_id(payload: Mapping[str, Any], target_ref: str) -> str:
    target = _target_payload(payload, target_ref)
    return str(target.get("cluster_id", "") or "").strip()


def _nebius_project_id(payload: Mapping[str, Any]) -> str:
    client_info = _mapping(payload.get("client_info"))
    nebius = _mapping(client_info.get("nebius"))
    project_id = str(nebius.get("project_id", "") or "").strip()
    if not project_id:
        raise RuntimeError("Soperator migration execute requires client_info.nebius.project_id.")
    return project_id


def _target_soperator_values(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    apps = _mapping(payload.get("apps"))
    charts = apps.get("charts")
    if not isinstance(charts, Sequence) or isinstance(charts, (str, bytes, bytearray)):
        return {}
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("id", "") or "").strip() != "soperator":
            continue
        instance_id = normalize_component_token(row.get("instance_id"))
        if instance_id != target_ref:
            continue
        return _mapping(row.get("values"))
    return {}


def _target_soperator_chart_path() -> Path:
    override = str(os.environ.get("NEBIUS_CXCLI_SOPERATOR_CHART_PATH", "") or "").strip()
    if override:
        return Path(override).expanduser()
    # services/nebius-cxcli/src/nebius_cxcli/soperator_migration.py -> repo root
    return Path(__file__).resolve().parents[4] / "helm-charts" / "soperator"


def _string_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _target_role_mapping(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, tuple[str, ...]]:
    values = _target_soperator_values(payload, target_ref)
    raw_mapping = _mapping(values.get("nodeGroupMapping"))
    result: dict[str, tuple[str, ...]] = {}
    for role in (*_SOPERATOR_SERVICE_ROLES, "worker"):
        result[role] = tuple(
            dict.fromkeys(
                normalize_component_token(item)
                for item in _string_sequence(raw_mapping.get(role))
                if normalize_component_token(item)
            )
        )
    return result


def _approved_role_attachment_keys(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
) -> Mapping[str, tuple[str, ...]]:
    role_mapping = _target_role_mapping(payload, target_ref)
    result: dict[str, list[str]] = {}
    worker_groups = {
        group
        for group in (normalize_component_token(item) for item in worker_node_groups)
        if group
    }
    for group in worker_groups:
        result.setdefault(group, []).append("jail")
    for role in _SOPERATOR_SERVICE_ROLES:
        for group in role_mapping.get(role, ()):
            if group in worker_groups:
                continue
            keys = result.setdefault(group, [])
            if "jail" not in keys:
                keys.append("jail")
            if role in {"system", "controller", "login"} and "controller-spool" not in keys:
                keys.append("controller-spool")
            if role == "accounting" and "accounting" not in keys:
                keys.append("accounting")
    return {group: tuple(keys) for group, keys in result.items() if keys}


def _source_report_payload(source_report: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    snapshot = _mapping(source_report.get("snapshot"))
    report = _mapping(source_report.get("report"))
    if not snapshot or not report:
        raise RuntimeError("Soperator source discovery report is missing snapshot or report data.")
    return snapshot, report


_VOLATILE_KUBERNETES_CONTRACT_KEYS = frozenset(
    {
        "creationTimestamp",
        "deletionGracePeriodSeconds",
        "deletionTimestamp",
        "finalizers",
        "generation",
        "managedFields",
        "observedGeneration",
        "ownerReferences",
        "resourceVersion",
        "selfLink",
        "status",
        "uid",
    }
)
_KUBERNETES_CONTRACT_METADATA_KEYS = frozenset({"labels", "name", "namespace"})
_HELM_RELEASE_CONTRACT_KEYS = ("name", "namespace", "chart", "app_version")
_NODE_GROUP_CONTRACT_KEYS = ("gpu", "node_count", "labels", "selector", "taints")
_SOPERATOR_DEFAULTED_SPEC_PATHS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "NodeSet": (
        ("spec", "initialNumberEphemeralNodes"),
        ("spec", "sssdDebugLevel"),
    ),
    "SlurmCluster": (
        ("spec", "clusterType"),
        ("spec", "plugStackConfig", "pyxis", "importerPath"),
        ("spec", "slurmNodes", "controller", "openMetrics"),
        ("spec", "slurmNodes", "controller", "sssdDebugLevel"),
        ("spec", "slurmNodes", "login", "sssdDebugLevel"),
    ),
}


def _strip_volatile_kubernetes_contract(value: Any) -> Any:
    plain = to_plain_data(value)
    if isinstance(plain, Mapping):
        result: dict[str, Any] = {}
        for key, item in plain.items():
            text_key = str(key)
            if text_key in _VOLATILE_KUBERNETES_CONTRACT_KEYS:
                continue
            result[text_key] = _strip_volatile_kubernetes_contract(item)
        return result
    if isinstance(plain, Sequence) and not isinstance(plain, (str, bytes, bytearray)):
        return [_strip_volatile_kubernetes_contract(item) for item in plain]
    return plain


def _resource_contract_metadata(metadata: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw = _mapping(metadata)
    for key in sorted(_KUBERNETES_CONTRACT_METADATA_KEYS):
        if key in raw:
            result[key] = _strip_volatile_kubernetes_contract(raw.get(key))
    return result


def _kubernetes_resource_contract(resource: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("apiVersion", "kind"):
        value = str(resource.get(key, "") or "").strip()
        if value:
            result[key] = value
    metadata = _resource_contract_metadata(resource.get("metadata"))
    if metadata:
        result["metadata"] = metadata
    spec = resource.get("spec")
    if isinstance(spec, Mapping):
        result["spec"] = _strip_volatile_kubernetes_contract(spec)
    _normalize_soperator_resource_contract(result)
    return result


def _drop_contract_path(value: dict[str, Any], path: tuple[str, ...]) -> None:
    cursor: Any = value
    for key in path[:-1]:
        if not isinstance(cursor, dict):
            return
        cursor = cursor.get(key)
    if isinstance(cursor, dict):
        cursor.pop(path[-1], None)


def _normalize_soperator_resource_contract(resource: dict[str, Any]) -> None:
    kind = str(resource.get("kind", "") or "").strip()
    for path in _SOPERATOR_DEFAULTED_SPEC_PATHS.get(kind, ()):
        _drop_contract_path(resource, path)


def _kubernetes_resource_contracts(value: Any) -> list[dict[str, Any]]:
    resources = [
        _kubernetes_resource_contract(item)
        for item in _sequence_of_mappings(value)
    ]
    return sorted(
        resources,
        key=lambda item: (
            str(_mapping(item.get("metadata")).get("namespace", "") or ""),
            str(_mapping(item.get("metadata")).get("name", "") or ""),
            str(item.get("apiVersion", "") or ""),
            str(item.get("kind", "") or ""),
            _stable_json(item),
        ),
    )


def _helm_release_contracts(value: Any) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    for item in _sequence_of_mappings(value):
        release: dict[str, Any] = {}
        for key in _HELM_RELEASE_CONTRACT_KEYS:
            release_value = str(item.get(key, "") or "").strip()
            if release_value:
                release[key] = release_value
        if release:
            releases.append(release)
    return sorted(releases, key=_stable_json)


def _node_group_contracts(
    value: Any,
    *,
    ignored_node_groups: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    raw_groups = value if isinstance(value, Mapping) else {}
    for raw_name, raw_group in raw_groups.items():
        name = normalize_component_token(raw_name)
        group = _mapping(raw_group)
        if not name or name in ignored_node_groups:
            continue
        contract: dict[str, Any] = {}
        for key in _NODE_GROUP_CONTRACT_KEYS:
            if key in group:
                contract[key] = _strip_volatile_kubernetes_contract(group.get(key))
        allocatable = _mapping(group.get("allocatable"))
        accelerator_resources = {
            str(key): str(item)
            for key, item in sorted(allocatable.items())
            if str(key).startswith(("nvidia.com/", "rdma/"))
        }
        if accelerator_resources:
            contract["accelerator_allocatable"] = accelerator_resources
        groups[name] = contract
    return dict(sorted(groups.items()))


def _execution_source_contract(
    snapshot: Mapping[str, Any],
    *,
    ignored_node_groups: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return the source material that must stay stable before live mutation."""
    return {
        "collection_errors": _strip_volatile_kubernetes_contract(
            snapshot.get("collection_errors", [])
        ),
        "crds": sorted(str(item).strip() for item in snapshot.get("crds", []) or [] if str(item).strip()),
        "helm_releases": _helm_release_contracts(snapshot.get("helm_releases")),
        "namespaces": sorted(
            str(item).strip() for item in snapshot.get("namespaces", []) or [] if str(item).strip()
        ),
        "node_groups": _node_group_contracts(
            snapshot.get("node_groups"),
            ignored_node_groups=ignored_node_groups,
        ),
        "pvcs": _kubernetes_resource_contracts(snapshot.get("pvcs")),
        "pvs": _kubernetes_resource_contracts(snapshot.get("pvs")),
        "soperator_resources": _kubernetes_resource_contracts(
            snapshot.get("soperator_resources")
        ),
        "storage": _strip_volatile_kubernetes_contract(_mapping(snapshot.get("storage"))),
    }


def _expected_source_version(
    *,
    onboarding: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    for value in (onboarding.get("source_version"), report.get("source_version")):
        normalized = normalize_soperator_release_version(str(value or ""))
        if normalized:
            return normalized
    return ""


def _phase_ids(report: Mapping[str, Any]) -> tuple[str, ...]:
    phases: list[str] = []
    for phase in _sequence_of_mappings(report.get("migration_plan")):
        phase_id = str(phase.get("id", "") or "").strip()
        if phase_id:
            phases.append(phase_id)
    return tuple(phases)


def _normalize_worker_node_groups(worker_node_groups: Sequence[str]) -> tuple[str, ...]:
    groups: list[str] = []
    for raw_value in worker_node_groups:
        for item in str(raw_value or "").split(","):
            normalized = normalize_component_token(item)
            if normalized:
                groups.append(normalized)
    return tuple(dict.fromkeys(groups))


def _source_node_group_inventory(source_report: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = _mapping(source_report.get("snapshot"))
    node_groups = snapshot.get("node_groups")
    return node_groups if isinstance(node_groups, Mapping) else {}


def _validate_worker_node_groups(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> tuple[str, ...]:
    normalized_groups = _normalize_worker_node_groups(worker_node_groups)
    if not normalized_groups:
        raise RuntimeError(
            "Soperator compute migration requires --worker-node-groups with the existing "
            "source node group names that should remain worker NodeSets during migration."
        )
    inventory = _source_node_group_inventory(source_report)
    available = {normalize_component_token(name) for name in inventory}
    missing = tuple(group for group in normalized_groups if group not in available)
    if missing:
        raise RuntimeError(
            "Soperator compute migration worker node group(s) were not found in source "
            "discovery inventory: "
            + ", ".join(missing)
            + ". Available groups: "
            + ", ".join(sorted(group for group in available if group))
        )
    return normalized_groups


def _positive_int(value: Any, *, fallback: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _bool_value(value: Any, *, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _nebius_filesystem_type(value: Any) -> str:
    text = str(value or "NETWORK_SSD").strip().lower().replace("-", "_")
    allowed = {"network_ssd", "network_hdd", "weka", "vast"}
    return text if text in allowed else "network_ssd"


def _aligned_filesystem_specs(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[SoperatorAlignedFilesystemSpec, ...]:
    values = _target_soperator_values(payload, target_ref)
    configured = _mapping(_mapping(values.get("sfs")).get("filesystems"))
    specs: list[SoperatorAlignedFilesystemSpec] = []
    for key in _SOPERATOR_STORAGE_KEYS:
        defaults = _SOPERATOR_STORAGE_DEFAULTS[key]
        configured_spec = _mapping(configured.get(key))
        name_template = str(configured_spec.get("name") or f"{target_ref}-{key}")
        name = name_template.replace("{target}", target_ref)
        specs.append(
            SoperatorAlignedFilesystemSpec(
                key=key,
                name=name,
                size_gib=_positive_int(
                    configured_spec.get("size_gib", configured_spec.get("size_gibibytes")),
                    fallback=int(defaults["size_gib"]),
                ),
                block_size_kib=_positive_int(
                    configured_spec.get("block_size_kib"),
                    fallback=int(defaults["block_size_kib"]),
                ),
                mount_tag=str(configured_spec.get("mount_tag") or defaults["mount_tag"]),
                forbid_deletion=_bool_value(
                    configured_spec.get("forbid_deletion"),
                    fallback=bool(defaults["forbid_deletion"]),
                ),
                filesystem_type=_nebius_filesystem_type(
                    configured_spec.get("type", configured_spec.get("filesystem_type", defaults["type"]))
                ),
            )
        )
    return tuple(specs)


def _filesystem_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _filesystem_id(payload: Mapping[str, Any]) -> str:
    return str(_filesystem_metadata(payload).get("id", "") or "").strip()


def _filesystem_spec(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = payload.get("spec")
    return spec if isinstance(spec, Mapping) else {}


def _validate_existing_filesystem(spec: SoperatorAlignedFilesystemSpec, payload: Mapping[str, Any]) -> None:
    live_spec = _filesystem_spec(payload)
    mismatches: list[str] = []
    live_size = _positive_int(live_spec.get("size_gibibytes"), fallback=spec.size_gib)
    if live_size != spec.size_gib:
        mismatches.append(f"size_gibibytes={live_size} expected {spec.size_gib}")
    live_block = _positive_int(live_spec.get("block_size_bytes"), fallback=spec.block_size_kib * 1024)
    if live_block != spec.block_size_kib * 1024:
        mismatches.append(f"block_size_bytes={live_block} expected {spec.block_size_kib * 1024}")
    live_type = _nebius_filesystem_type(live_spec.get("type"))
    if live_type != spec.filesystem_type:
        mismatches.append(f"type={live_type} expected {spec.filesystem_type}")
    if mismatches:
        raise SoperatorMigrationPhaseBlocked(
            f"existing aligned SFS filesystem '{spec.name}' is incompatible: "
            + "; ".join(mismatches)
            + ". Rename or fix the existing filesystem before resuming."
        )


def _get_filesystem_by_name(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    project_id: str,
    name: str,
) -> Mapping[str, Any]:
    result = command_runner(
        [
            "nebius",
            "compute",
            "filesystem",
            "get-by-name",
            "--parent-id",
            project_id,
            "--name",
            name,
            "--format",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"nebius compute filesystem get-by-name returned invalid JSON: {exc}") from exc
    return parsed if isinstance(parsed, Mapping) else {}


def _create_filesystem(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    project_id: str,
    spec: SoperatorAlignedFilesystemSpec,
) -> Mapping[str, Any]:
    args = [
        "nebius",
        "compute",
        "filesystem",
        "create",
        "--parent-id",
        project_id,
        "--name",
        spec.name,
        "--type",
        spec.filesystem_type,
        "--size-gibibytes",
        str(spec.size_gib),
        "--block-size-bytes",
        str(spec.block_size_kib * 1024),
        "--format",
        "json",
        "--timeout",
        "30m",
    ]
    if spec.forbid_deletion:
        args.insert(-4, "--forbid-deletion")
    return _json_from_command(command_runner, args, timeout_seconds=1800)


def _source_group_node_group_id(source_group: Mapping[str, Any]) -> str:
    labels = _mapping(source_group.get("labels"))
    for key in ("nebius.com/node-group-id", "yandex.cloud/node-group-id"):
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return str(source_group.get("node_group_id", "") or source_group.get("id", "") or "").strip()


def _node_group_payload_by_id(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
) -> Mapping[str, Any]:
    return _json_from_command(
        command_runner,
        [
            "nebius",
            "mk8s",
            "node-group",
            "get",
            node_group_id,
            "--format",
            "json",
        ],
        timeout_seconds=120,
    )


def _node_group_template_filesystems(node_group: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = _mapping(_mapping(node_group.get("spec")).get("template"))
    filesystems = template.get("filesystems")
    if not isinstance(filesystems, Sequence) or isinstance(filesystems, (str, bytes, bytearray)):
        return []
    items: list[dict[str, Any]] = []
    for item in filesystems:
        if isinstance(item, Mapping):
            items.append(dict(to_plain_data(item)))
    return items


def _filesystem_attachment(spec: SoperatorAlignedFilesystemSpec, filesystem_id: str) -> dict[str, Any]:
    return {
        "attach_mode": "READ_WRITE",
        "existing_filesystem": {"id": filesystem_id},
        "mount_tag": spec.mount_tag,
    }


def _merge_filesystem_attachments(
    existing: Sequence[Mapping[str, Any]],
    desired: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(to_plain_data(item)) for item in existing]
    seen_mounts = {
        str(item.get("mount_tag", "") or "").strip()
        for item in merged
        if str(item.get("mount_tag", "") or "").strip()
    }
    seen_ids = {
        str(_mapping(item.get("existing_filesystem")).get("id", "") or "").strip()
        for item in merged
        if str(_mapping(item.get("existing_filesystem")).get("id", "") or "").strip()
    }
    for item in desired:
        mount_tag = str(item.get("mount_tag", "") or "").strip()
        filesystem_id = str(_mapping(item.get("existing_filesystem")).get("id", "") or "").strip()
        if (mount_tag and mount_tag in seen_mounts) or (filesystem_id and filesystem_id in seen_ids):
            continue
        merged.append(dict(to_plain_data(item)))
        if mount_tag:
            seen_mounts.add(mount_tag)
        if filesystem_id:
            seen_ids.add(filesystem_id)
    return merged


def _attach_filesystems_to_source_node_groups(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    source_report: Mapping[str, Any],
    attachment_keys_by_group: Mapping[str, Sequence[str]],
    filesystem_ids_by_key: Mapping[str, str],
    specs_by_key: Mapping[str, SoperatorAlignedFilesystemSpec],
) -> tuple[bool, list[dict[str, Any]]]:
    inventory = _source_node_group_inventory(source_report)
    attachments: list[dict[str, Any]] = []
    mutation_performed = False
    for raw_group_name, raw_group in sorted(inventory.items()):
        if not isinstance(raw_group, Mapping):
            continue
        group_name = normalize_component_token(raw_group_name)
        if not group_name:
            continue
        desired_keys = tuple(
            key
            for key in attachment_keys_by_group.get(group_name, ())
            if key in filesystem_ids_by_key and key in specs_by_key
        )
        if not desired_keys:
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhaseBlocked(
                "create-aligned-sfs requires Nebius node group ids in the onboarding "
                f"inventory before it can attach SFS to source group '{group_name}'. "
                "Rerun `nebius-cxcli soperator onboard` against a Nebius MK8s target."
            )
        desired = [
            _filesystem_attachment(specs_by_key[key], filesystem_ids_by_key[key])
            for key in desired_keys
        ]
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        existing = _node_group_template_filesystems(node_group)
        merged = _merge_filesystem_attachments(existing, desired)
        updated = len(merged) != len(existing)
        if updated:
            command_runner(
                [
                    "nebius",
                    "mk8s",
                    "node-group",
                    "update",
                    node_group_id,
                    "--template-filesystems",
                    json.dumps(merged, sort_keys=True),
                    "--format",
                    "json",
                    "--timeout",
                    "45m",
                ],
                timeout_seconds=2700,
            )
            mutation_performed = True
        attachments.append(
            {
                "source_group": group_name,
                "node_group_id": node_group_id,
                "filesystem_keys": list(desired_keys),
                "updated": updated,
            }
        )
    return mutation_performed, attachments


def _snapshot_storage(source_report: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = _mapping(source_report.get("snapshot"))
    return _mapping(snapshot.get("storage"))


def _snapshot_pvc_names(snapshot: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    pvcs = snapshot.get("pvcs")
    if not isinstance(pvcs, Sequence) or isinstance(pvcs, (str, bytes, bytearray)):
        return names
    for pvc in pvcs:
        if not isinstance(pvc, Mapping):
            continue
        metadata = _mapping(pvc.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if name:
            names.add(name)
    return names


def _source_pvc_name_for_storage_key(source_report: Mapping[str, Any], key: str) -> str:
    storage = _snapshot_storage(source_report)
    item = _mapping(storage.get(key))
    source = str(item.get("source", "") or "").strip()
    if source.startswith("pvc/"):
        return source.removeprefix("pvc/").strip()
    pvc = str(item.get("pvc", "") or item.get("claimName", "") or "").strip()
    return pvc


def _target_pvc_name_for_storage_key(payload: Mapping[str, Any], target_ref: str, key: str) -> str:
    values = _target_soperator_values(payload, target_ref)
    if key == "jail":
        nodesets = values.get("nodesets")
        if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
            for nodeset in nodesets:
                if not isinstance(nodeset, Mapping):
                    continue
                volumes = _mapping(_mapping(nodeset.get("slurmd")).get("volumes"))
                claim_name = str(
                    _mapping(
                        _mapping(volumes.get("jail")).get("persistentVolumeClaim")
                    ).get("claimName", "")
                    or ""
                ).strip()
                if claim_name:
                    return claim_name
    defaults = {
        "jail": "jail-pvc",
        "controller-spool": "controller-spool-pvc",
        "accounting": "accounting-pvc",
    }
    return defaults[key]


def _copy_job_manifest(
    *,
    key: str,
    source_pvc: str,
    target_pvc: str,
) -> dict[str, Any]:
    normalized = normalize_component_token(key) or key.replace("_", "-")
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": f"cxcli-soperator-sync-{normalized}",
            "namespace": _SOPERATOR_NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "nebius-cxcli.io/soperator-migration": "true",
                "nebius-cxcli.io/storage-key": key,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "copy",
                            "image": "ubuntu:24.04",
                            "command": [
                                "/bin/sh",
                                "-ceu",
                                "cd /old && tar --xattrs --acls --numeric-owner -cpf - . "
                                "| tar --xattrs --acls --numeric-owner -xpf - -C /new",
                            ],
                            "volumeMounts": [
                                {"name": "old", "mountPath": "/old", "readOnly": True},
                                {"name": "new", "mountPath": "/new"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "old", "persistentVolumeClaim": {"claimName": source_pvc}},
                        {"name": "new", "persistentVolumeClaim": {"claimName": target_pvc}},
                    ],
                }
            },
        },
    }


def _kubectl_apply_objects(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    objects: Sequence[Mapping[str, Any]],
    timeout_seconds: int = 300,
) -> None:
    payload = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [to_plain_data(item) for item in objects],
    }
    command_runner(
        ["kubectl", "--context", kube_context, "apply", "-f", "-"],
        input_text=json.dumps(payload, sort_keys=True),
        timeout_seconds=timeout_seconds,
    )


def _kubectl_wait(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    namespace: str,
    resource: str,
    condition: str,
    timeout: str,
    timeout_seconds: int,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "wait",
            f"--for={condition}",
            resource,
            f"--timeout={timeout}",
        ],
        timeout_seconds=timeout_seconds,
    )


def _kubectl_rollout_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    namespace: str,
    resource: str,
    timeout: str = "10m",
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "rollout",
            "status",
            resource,
            f"--timeout={timeout}",
        ],
        timeout_seconds=900,
    )


def _has_soperator_custom_resources(snapshot: Mapping[str, Any]) -> bool:
    resources = snapshot.get("soperator_resources")
    return isinstance(resources, Sequence) and not isinstance(resources, (str, bytes, bytearray)) and any(
        isinstance(item, Mapping) for item in resources
    )


def _nodes_for_worker_groups(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> tuple[str, ...]:
    inventory = _source_node_group_inventory(source_report)
    requested = {normalize_component_token(group) for group in worker_node_groups}
    nodes: list[str] = []
    for group_name, raw_group in inventory.items():
        normalized = normalize_component_token(group_name)
        if normalized not in requested or not isinstance(raw_group, Mapping):
            continue
        raw_nodes = raw_group.get("nodes")
        if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes, bytearray)):
            nodes.extend(str(node).strip() for node in raw_nodes if str(node).strip())
    return tuple(dict.fromkeys(nodes))


def _nodes_for_source_groups(
    *,
    source_report: Mapping[str, Any],
    source_groups: Sequence[str],
) -> tuple[str, ...]:
    inventory = _source_node_group_inventory(source_report)
    requested = {normalize_component_token(group) for group in source_groups}
    nodes: list[str] = []
    for group_name, raw_group in inventory.items():
        normalized = normalize_component_token(group_name)
        if normalized not in requested or not isinstance(raw_group, Mapping):
            continue
        raw_nodes = raw_group.get("nodes")
        if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes, bytearray)):
            nodes.extend(str(node).strip() for node in raw_nodes if str(node).strip())
    return tuple(dict.fromkeys(nodes))


def _node_group_id(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return str(metadata.get("id", "") or payload.get("id", "") or "").strip()


def _node_group_name(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return str(metadata.get("name", "") or payload.get("name", "") or "").strip()


def _node_group_parent_id(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return str(metadata.get("parent_id", "") or metadata.get("parentId", "") or "").strip()


def _node_group_fixed_count(payload: Mapping[str, Any]) -> int:
    spec = _mapping(payload.get("spec"))
    return _positive_int(spec.get("fixed_node_count", spec.get("fixedNodeCount")), fallback=1)


def _target_worker_replicas(payload: Mapping[str, Any], target_ref: str, source_count: int) -> int:
    override = str(os.environ.get("NEBIUS_CXCLI_SOPERATOR_TARGET_WORKER_COUNT", "") or "").strip()
    if override:
        return _positive_int(override, fallback=source_count)
    values = _target_soperator_values(payload, target_ref)
    nodesets = values.get("nodesets")
    if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
        for nodeset in nodesets:
            if not isinstance(nodeset, Mapping):
                continue
            name = normalize_component_token(nodeset.get("name"))
            if name in {"", "worker"}:
                return _positive_int(nodeset.get("replicas"), fallback=source_count)
    return source_count


def _source_compute_group_names(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> Mapping[str, str]:
    inventory = _source_node_group_inventory(source_report)
    worker_set = {
        group
        for group in (normalize_component_token(item) for item in worker_node_groups)
        if group
    }
    worker_group = next((group for group in worker_node_groups if group in inventory), "")
    if not worker_group:
        raise SoperatorMigrationPhaseBlocked(
            "rolling-compute-migration requires at least one approved source worker node group."
        )

    role_mapping = _target_role_mapping(payload, target_ref)
    cpu_candidates: list[str] = []
    for role in _SOPERATOR_SERVICE_ROLES:
        for group in role_mapping.get(role, ()):
            if group and group in inventory and group not in worker_set:
                cpu_candidates.append(group)
    if not cpu_candidates:
        for group_name, raw_group in inventory.items():
            normalized = normalize_component_token(group_name)
            if not normalized or normalized in worker_set or not isinstance(raw_group, Mapping):
                continue
            if not _bool_value(raw_group.get("gpu"), fallback=False):
                cpu_candidates.append(normalized)
    cpu_group = next((group for group in dict.fromkeys(cpu_candidates) if group in inventory), "")
    if not cpu_group:
        raise SoperatorMigrationPhaseBlocked(
            "rolling-compute-migration could not identify a non-GPU source node group "
            "to clone for Soperator system/controller/login/accounting roles. Rerun "
            "onboarding with explicit compute role mapping."
        )
    return {"cpu": cpu_group, "gpu": worker_group}


def _json_value_from_command(
    command_runner: SoperatorMigrationCommandRunner,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> Any:
    result = command_runner(
        args,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        check=check,
    )
    if result.returncode != 0 and not check:
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(args)} returned invalid JSON: {exc}") from exc


def _list_node_groups(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    cluster_id: str,
) -> tuple[Mapping[str, Any], ...]:
    parsed = _json_value_from_command(
        command_runner,
        [
            "nebius",
            "mk8s",
            "node-group",
            "list",
            "--parent-id",
            cluster_id,
            "--format",
            "json",
            "--all",
        ],
        timeout_seconds=180,
    )
    if isinstance(parsed, Mapping):
        items = parsed.get("items", parsed.get("node_groups", parsed.get("nodeGroups", [])))
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            return tuple(item for item in items if isinstance(item, Mapping))
        if _node_group_id(parsed):
            return (parsed,)
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
        return tuple(item for item in parsed if isinstance(item, Mapping))
    return ()


def _find_node_group_by_name(
    node_groups: Sequence[Mapping[str, Any]],
    name: str,
) -> Mapping[str, Any]:
    for node_group in node_groups:
        if _node_group_name(node_group) == name:
            return node_group
    return {}


def _lower_nebius_enums(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _lower_nebius_enums(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_lower_nebius_enums(item) for item in value]
    return value


def _source_node_group_spec_for_role(
    *,
    role: str,
    source_groups_by_kind: Mapping[str, str],
    inventory: Mapping[str, Any],
) -> Mapping[str, Any]:
    source_kind = _SOPERATOR_ROLE_SOURCE_KIND[role]
    source_group = source_groups_by_kind[source_kind]
    raw_group = inventory.get(source_group)
    if not isinstance(raw_group, Mapping):
        raise SoperatorMigrationPhaseBlocked(
            f"rolling-compute-migration could not find source node group '{source_group}'."
        )
    return raw_group


def _role_filesystem_attachments(
    *,
    role: str,
    checkpoint: Mapping[str, Any],
    specs_by_key: Mapping[str, SoperatorAlignedFilesystemSpec],
) -> list[dict[str, Any]]:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("create-aligned-sfs"))
    raw_filesystems = _mapping(phase.get("filesystems"))
    attachments: list[dict[str, Any]] = []
    for key in _SOPERATOR_ROLE_STORAGE_KEYS[role]:
        filesystem = _mapping(raw_filesystems.get(key))
        filesystem_id = str(filesystem.get("id", "") or "").strip()
        spec = specs_by_key.get(key)
        if not filesystem_id or spec is None:
            continue
        attachments.append(_filesystem_attachment(spec, filesystem_id))
    return attachments


def _source_slurmcluster_names(
    source_report: Mapping[str, Any],
    *,
    target_ref: str,
) -> tuple[str, ...]:
    snapshot = _mapping(source_report.get("snapshot"))
    names: list[str] = []
    for resource in _sequence_of_mappings(snapshot.get("soperator_resources")):
        if str(resource.get("kind", "") or "").strip() != "SlurmCluster":
            continue
        metadata = _mapping(resource.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if name and name != target_ref:
            names.append(name)
    return tuple(dict.fromkeys(names))


def _target_node_group_name(target_ref: str, role: str) -> str:
    return normalize_component_token(f"{target_ref}-{role}") or f"{target_ref}-{role}"


def _role_node_group_taints(role: str, source_template: Mapping[str, Any]) -> list[dict[str, Any]]:
    if role in {"controller", "login", "accounting"}:
        return [
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "value": role,
                "effect": "NO_SCHEDULE",
            }
        ]
    if role == "worker":
        existing = source_template.get("taints")
        taints: list[dict[str, Any]] = []
        if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray)):
            for item in existing:
                if isinstance(item, Mapping):
                    taints.append(dict(to_plain_data(_lower_nebius_enums(item))))
        if not any(str(item.get("key", "")) == "nvidia.com/gpu" for item in taints):
            taints.append(
                {
                    "key": "nvidia.com/gpu",
                    "value": "true",
                    "effect": "NO_SCHEDULE",
                }
            )
        return taints
    return []


def _role_node_group_template(
    *,
    role: str,
    source_node_group: Mapping[str, Any],
    filesystems: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    source_template = copy.deepcopy(_mapping(_mapping(source_node_group.get("spec")).get("template")))
    if not source_template:
        raise SoperatorMigrationPhaseBlocked(
            f"rolling-compute-migration could not clone a Nebius node template for role '{role}'."
        )
    template = dict(to_plain_data(_lower_nebius_enums(source_template)))
    metadata = dict(_mapping(template.get("metadata")))
    labels = dict(_mapping(metadata.get("labels")))
    source_node_group_label = str(labels.get("nebius.com/node-group", "") or "").strip()
    labels["nebius.com/node-group"] = source_node_group_label or _SOPERATOR_ROLE_SOURCE_KIND[role]
    labels["slurm.nebius.ai/nodeset-name"] = role
    if role != "worker":
        labels["nebius.com/gpu"] = "false"
    metadata["labels"] = labels
    template["metadata"] = metadata
    template["filesystems"] = [dict(to_plain_data(item)) for item in filesystems]
    template["taints"] = _role_node_group_taints(role, template)
    return template


def _path_text(value: Mapping[str, Any], path: Sequence[str]) -> str:
    cursor: Any = value
    for key in path:
        if not isinstance(cursor, Mapping):
            return ""
        cursor = cursor.get(key)
    if isinstance(cursor, Mapping):
        return str(cursor.get("name", "") or "").strip()
    return str(cursor or "").strip()


def _normalized_text(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _filesystem_identity_set(filesystems: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for filesystem in filesystems:
        mount_tag = str(filesystem.get("mount_tag", "") or "").strip()
        filesystem_id = str(_mapping(filesystem.get("existing_filesystem")).get("id", "") or "").strip()
        if mount_tag or filesystem_id:
            identities.add((mount_tag, filesystem_id))
    return identities


def _validate_reused_target_node_group(
    *,
    role: str,
    target_name: str,
    node_group: Mapping[str, Any],
    expected_count: int,
    expected_template: Mapping[str, Any],
) -> None:
    mismatches: list[str] = []
    actual_count = _node_group_fixed_count(node_group)
    if actual_count != expected_count:
        mismatches.append(f"fixed_node_count={actual_count} expected {expected_count}")

    spec = _mapping(node_group.get("spec"))
    template = _mapping(spec.get("template"))
    labels = _mapping(_mapping(template.get("metadata")).get("labels"))
    role_label = str(labels.get("slurm.nebius.ai/nodeset-name", "") or "").strip()
    if role_label != role:
        mismatches.append(
            f"missing slurm.nebius.ai/nodeset-name={role} template label"
        )

    for path in (
        ("resources", "platform"),
        ("resources", "preset"),
        ("os",),
    ):
        actual = _normalized_text(_path_text(template, path))
        expected = _normalized_text(_path_text(expected_template, path))
        if expected and actual and actual != expected:
            mismatches.append(
                f"template.{'.'.join(path)}={actual} expected {expected}"
            )

    expected_filesystems = _filesystem_identity_set(
        _sequence_of_mappings(expected_template.get("filesystems"))
    )
    if expected_filesystems:
        actual_filesystems = _filesystem_identity_set(
            _sequence_of_mappings(template.get("filesystems"))
        )
        missing_filesystems = expected_filesystems - actual_filesystems
        if missing_filesystems:
            formatted = ", ".join(
                f"{mount_tag or '?'}:{filesystem_id or '?'}"
                for mount_tag, filesystem_id in sorted(missing_filesystems)
            )
            mismatches.append(f"missing filesystem attachment(s): {formatted}")

    if role in {"controller", "login", "accounting"}:
        taints = _sequence_of_mappings(template.get("taints"))
        if not any(
            str(taint.get("key", "") or "").strip() == "slurm.nebius.ai/nodeset-name"
            and str(taint.get("value", "") or "").strip() == role
            for taint in taints
        ):
            mismatches.append(
                f"missing slurm.nebius.ai/nodeset-name={role} NoSchedule taint"
            )
    if role == "worker":
        taints = _sequence_of_mappings(template.get("taints"))
        if not any(str(taint.get("key", "") or "").strip() == "nvidia.com/gpu" for taint in taints):
            mismatches.append("missing nvidia.com/gpu worker taint")

    if mismatches:
        raise SoperatorMigrationPhaseBlocked(
            f"existing target node group '{target_name}' is incompatible with "
            f"Soperator role '{role}': "
            + "; ".join(mismatches)
            + ". Rename or fix the existing node group before resuming."
        )


def _create_or_reuse_target_node_groups(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    target_groups = phase.setdefault("target_node_groups", {})
    if not isinstance(target_groups, dict):
        raise RuntimeError("Soperator migration checkpoint rolling-compute-migration.target_node_groups must be a mapping.")
    old_groups = phase.setdefault("old_node_groups", {})
    if not isinstance(old_groups, dict):
        raise RuntimeError("Soperator migration checkpoint rolling-compute-migration.old_node_groups must be a mapping.")

    inventory = _source_node_group_inventory(source_report)
    source_groups_by_kind = _source_compute_group_names(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    source_payloads: dict[str, Mapping[str, Any]] = {}
    for kind, group_name in source_groups_by_kind.items():
        raw_group = inventory.get(group_name)
        if not isinstance(raw_group, Mapping):
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhaseBlocked(
                f"rolling-compute-migration requires Nebius node group id for source group '{group_name}'."
            )
        source_payloads[kind] = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        old_groups[group_name] = {"id": node_group_id, "kind": kind}

    cluster_id = _target_cluster_id(payload, target_ref)
    if not cluster_id:
        cluster_id = next(
            (
                _node_group_parent_id(source_payload)
                for source_payload in source_payloads.values()
                if _node_group_parent_id(source_payload)
            ),
            "",
        )
    if not cluster_id:
        raise SoperatorMigrationPhaseBlocked(
            "rolling-compute-migration could not resolve the Nebius MK8s cluster id "
            "from config or source node-group metadata."
        )

    specs_by_key = {
        spec.key: spec
        for spec in _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    }
    live_node_groups = _list_node_groups(command_runner=command_runner, cluster_id=cluster_id)
    mutation_performed = False
    lines: list[str] = []
    for role in _SOPERATOR_COMPUTE_ROLES:
        target_name = _target_node_group_name(target_ref, role)
        existing = _find_node_group_by_name(live_node_groups, target_name)
        source_inventory_group = _source_node_group_spec_for_role(
            role=role,
            source_groups_by_kind=source_groups_by_kind,
            inventory=inventory,
        )
        source_kind = _SOPERATOR_ROLE_SOURCE_KIND[role]
        source_node_group = source_payloads[source_kind]
        source_count = _positive_int(source_inventory_group.get("node_count"), fallback=1)
        target_count = (
            _target_worker_replicas(payload, target_ref, source_count)
            if role == "worker"
            else 1
        )
        filesystems = _role_filesystem_attachments(
            role=role,
            checkpoint=checkpoint,
            specs_by_key=specs_by_key,
        )
        if not filesystems:
            filesystems = [
                item
                for item in _node_group_template_filesystems(source_node_group)
                if str(item.get("mount_tag", "") or "").strip() in _SOPERATOR_ROLE_STORAGE_KEYS[role]
            ]
        expected_template = _role_node_group_template(
            role=role,
            source_node_group=source_node_group,
            filesystems=filesystems,
        )
        if existing:
            node_group_id = _node_group_id(existing)
            if not node_group_id:
                raise RuntimeError(f"existing target node group '{target_name}' has no id.")
            existing_payload = _node_group_payload_by_id(
                command_runner=command_runner,
                node_group_id=node_group_id,
            )
            _validate_reused_target_node_group(
                role=role,
                target_name=target_name,
                node_group=existing_payload or existing,
                expected_count=target_count,
                expected_template=expected_template,
            )
            target_groups[role] = {
                "id": node_group_id,
                "name": target_name,
                "fixed_node_count": target_count,
                "created": False,
            }
            lines.append(f"Target node group {role}: reused {target_name} ({node_group_id}).")
            continue

        create_payload = {
            "metadata": {"parent_id": cluster_id, "name": target_name},
            "spec": {
                "version": str(_mapping(source_node_group.get("spec")).get("version", "") or ""),
                "fixed_node_count": target_count,
                "template": expected_template,
            },
        }
        created = _json_from_command(
            command_runner,
            [
                "nebius",
                "mk8s",
                "node-group",
                "create",
                json.dumps(create_payload, sort_keys=True),
                "--format",
                "json",
                "--timeout",
                "60m",
            ],
            timeout_seconds=3900,
        )
        node_group_id = _node_group_id(created)
        if not node_group_id:
            node_group_id = _node_group_id(_find_node_group_by_name(
                _list_node_groups(command_runner=command_runner, cluster_id=cluster_id),
                target_name,
            ))
        if not node_group_id:
            raise RuntimeError(f"target node group '{target_name}' did not return an id.")
        target_groups[role] = {
            "id": node_group_id,
            "name": target_name,
            "fixed_node_count": target_count,
            "created": True,
        }
        mutation_performed = True
        lines.append(f"Target node group {role}: created {target_name} ({node_group_id}).")
    phase["cluster_id"] = cluster_id
    phase["source_groups"] = dict(source_groups_by_kind)
    return mutation_performed, lines


def _login_pod_name(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> str:
    parsed = _json_value_from_command(
        command_runner,
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "pods",
            "-o",
            "json",
        ],
        timeout_seconds=120,
    )
    items = parsed.get("items", []) if isinstance(parsed, Mapping) else []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return ""
    best_name = ""
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        labels = _mapping(metadata.get("labels"))
        label_text = " ".join(str(value) for value in labels.values())
        phase = str(_mapping(item.get("status")).get("phase", "") or "")
        if name and phase == "Running" and ("login" in name or "login" in label_text):
            return name
        if name and not best_name and ("login" in name or "login" in label_text):
            best_name = name
    return best_name


def _kubectl_exec_login(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    args: Sequence[str],
    check: bool = True,
    timeout_seconds: int = 300,
) -> SoperatorMigrationCommandResult:
    pod = _login_pod_name(command_runner=command_runner, kube_context=kube_context)
    if not pod:
        return SoperatorMigrationCommandResult(tuple(args), 1, "", "login pod not found")
    return command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "exec",
            pod,
            "--",
            *args,
        ],
        check=check,
        timeout_seconds=timeout_seconds,
    )


def _ensure_slurm_quiet(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> list[str]:
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("squeue", "-h"),
        check=False,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        raise SoperatorMigrationPhaseBlocked(
            "rolling-compute-migration could not inspect Slurm jobs from a login pod: "
            + (result.stderr.strip() or "login pod not found")
            + ". Ensure the source Slurm data plane is healthy before executing."
        )
    queued = result.stdout.strip()
    if queued:
        raise SoperatorMigrationPhaseBlocked(
            "rolling-compute-migration requires an empty Slurm queue before compute cutover. "
            "Running or pending jobs were returned by `squeue -h`."
        )
    drain = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "update", "PartitionName=ALL", "State=DRAIN"),
        check=False,
        timeout_seconds=120,
    )
    if drain.returncode != 0:
        return ["Slurm quiet window verified; partition drain command was not supported by the source release."]
    return ["Slurm quiet window verified: no jobs in queue and partitions set to DRAIN."]


def _uncordon_or_drain_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    nodes: Sequence[str],
    action: str,
) -> None:
    for node in nodes:
        if action == "cordon":
            _run_kubectl_node_action(
                command_runner=command_runner,
                node=node,
                args=["kubectl", "--context", kube_context, "cordon", node],
                timeout_seconds=300,
            )
        elif action == "drain":
            _run_kubectl_node_action(
                command_runner=command_runner,
                node=node,
                args=[
                    "kubectl",
                    "--context",
                    kube_context,
                    "drain",
                    node,
                    "--ignore-daemonsets",
                    "--delete-emptydir-data",
                    "--timeout=20m",
                ],
                timeout_seconds=1500,
            )
        elif action == "uncordon":
            _run_kubectl_node_action(
                command_runner=command_runner,
                node=node,
                args=["kubectl", "--context", kube_context, "uncordon", node],
                timeout_seconds=300,
            )


def _run_kubectl_node_action(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node: str,
    args: Sequence[str],
    timeout_seconds: int,
) -> None:
    result = command_runner(args, timeout_seconds=timeout_seconds, check=False)
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").lower()
    if "notfound" in detail or "not found" in detail:
        return
    raise RuntimeError(f"{_command_text(args)} failed: {result.stderr.strip() or result.stdout.strip()}")


def _ensure_soperator_chart_dependencies(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    chart_path: Path,
) -> None:
    if not (chart_path / "Chart.yaml").exists():
        raise SoperatorMigrationPhaseBlocked(
            f"target Soperator chart path does not exist: {chart_path}. "
            "Set NEBIUS_CXCLI_SOPERATOR_CHART_PATH or run from the repository checkout."
        )
    command_runner(
        ["helm", "dependency", "build", str(chart_path)],
        timeout_seconds=600,
    )


def _apply_soperator_crds(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    chart_path: Path,
) -> None:
    crd_dir = chart_path / "crds"
    if not crd_dir.exists():
        return
    for crd_file in sorted(crd_dir.glob("*.yaml")):
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "apply",
                "--server-side",
                "-f",
                str(crd_file),
            ],
            timeout_seconds=1200,
        )


def _target_role_mapping_values() -> Mapping[str, list[str]]:
    return {role: [role] for role in _SOPERATOR_COMPUTE_ROLES}


def _role_match_expression(role: str) -> dict[str, Any]:
    return {
        "key": "slurm.nebius.ai/nodeset-name",
        "operator": "In",
        "values": [role],
    }


def _role_node_selector_term(role: str) -> dict[str, Any]:
    return {
        "matchExpressions": [_role_match_expression(role)]
    }


def _role_toleration(role: str) -> dict[str, str]:
    return {
        "key": "slurm.nebius.ai/nodeset-name",
        "operator": "Equal",
        "value": role,
        "effect": "NoSchedule",
    }


def _target_k8s_node_filters() -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = [
        {
            "name": "no-gpu",
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [_role_node_selector_term("system")]
                    }
                }
            },
        }
    ]
    for role in _SOPERATOR_SERVICE_ROLES:
        item: dict[str, Any] = {
            "name": role,
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [_role_node_selector_term(role)]
                    }
                }
            },
        }
        if role != "system":
            item["tolerations"] = [_role_toleration(role)]
        filters.append(item)
    return filters


def _patch_target_values_for_compute(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    checkpoint: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    values = dict(copy.deepcopy(to_plain_data(_target_soperator_values(payload, target_ref))))
    values.setdefault("nameOverride", "helm-soperator")
    _patch_target_slurm_runtime(values)
    _preserve_live_storage_sizes(values, live_snapshot=live_snapshot)
    values["nodeGroupMapping"] = _target_role_mapping_values()
    values["k8sNodeFilters"] = _target_k8s_node_filters()
    _patch_storage_mount_tolerations(values)
    if not _has_live_storage_pvs(live_snapshot):
        _patch_storage_role_affinity(values)
    _patch_accounting_mariadb_storage(values, live_snapshot=live_snapshot)
    target_groups = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration")).get(
        "target_node_groups"
    )
    if isinstance(target_groups, Mapping):
        worker = _mapping(target_groups.get("worker"))
        worker_count = _positive_int(worker.get("fixed_node_count"), fallback=0)
    else:
        worker_count = 0
    nodesets = values.get("nodesets")
    if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
        patched_nodesets: list[Any] = []
        for nodeset in nodesets:
            if isinstance(nodeset, Mapping) and normalize_component_token(nodeset.get("name")) in {"", "worker"}:
                item = dict(copy.deepcopy(to_plain_data(nodeset)))
                if worker_count:
                    item["replicas"] = worker_count
                    item["nodeSelector"] = {"slurm.nebius.ai/nodeset-name": "worker"}
                    item["tolerations"] = [
                        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
                    ]
                _strip_nodeset_image_override(item, "slurmd")
                _strip_nodeset_image_override(item, "munge")
                patched_nodesets.append(item)
            else:
                patched_nodesets.append(copy.deepcopy(to_plain_data(nodeset)))
        values["nodesets"] = patched_nodesets
    return values


def _patch_target_slurm_runtime(values: dict[str, Any]) -> None:
    raw_custom = str(values.get("customSlurmConfig") or "")
    lines = [
        line
        for line in raw_custom.splitlines()
        if not re.match(r"^\s*PluginDir\s*=", line)
    ]
    lines.append(f"PluginDir={_TARGET_SLURM_PLUGIN_DIR}")
    values["customSlurmConfig"] = "\n".join(line for line in lines if line.strip())

    plug_stack = values.setdefault("plugStackConfig", {})
    if not isinstance(plug_stack, dict):
        return
    pyxis = plug_stack.setdefault("pyxis", {})
    if not isinstance(pyxis, dict):
        return
    pyxis["required"] = False
    pyxis["importerPath"] = ""


def _strip_nodeset_image_override(nodeset: dict[str, Any], component: str) -> None:
    raw_component = nodeset.get(component)
    if not isinstance(raw_component, dict):
        return
    raw_component.pop("image", None)


def _patch_storage_role_affinity(values: dict[str, Any]) -> None:
    storage = values.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    for role, key in (
        ("controller", "controllerSpool"),
        ("accounting", "accounting"),
    ):
        item = storage.setdefault(key, {})
        if not isinstance(item, dict):
            continue
        item["matchExpressions"] = [_role_match_expression(role)]
        item["tolerations"] = [_role_toleration(role)]
    jail = storage.setdefault("jail", {})
    if isinstance(jail, dict):
        jail["matchExpressions"] = [
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "operator": "Exists",
            }
        ]
        jail["tolerations"] = _jail_storage_tolerations()


def _patch_storage_mount_tolerations(values: dict[str, Any]) -> None:
    storage = values.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    jail = storage.setdefault("jail", {})
    if isinstance(jail, dict):
        jail["tolerations"] = _jail_storage_tolerations()
    controller_spool = storage.setdefault("controllerSpool", {})
    if isinstance(controller_spool, dict):
        controller_spool["tolerations"] = [_role_toleration("controller")]
    accounting = storage.setdefault("accounting", {})
    if isinstance(accounting, dict):
        accounting["tolerations"] = [_role_toleration("accounting")]


def _jail_storage_tolerations() -> list[dict[str, str]]:
    return [
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},
        *[_role_toleration(role) for role in ("controller", "login", "accounting")],
    ]


def _has_live_storage_pvs(snapshot: Mapping[str, Any]) -> bool:
    names = {
        str(_mapping(item.get("metadata")).get("name", "") or "").strip()
        for item in _sequence_of_mappings(snapshot.get("pvs"))
    }
    return bool(names & {"jail-pv", "controller-spool-pv", "accounting-pv"})


def _patch_accounting_mariadb_storage(
    values: dict[str, Any],
    *,
    live_snapshot: Mapping[str, Any],
) -> None:
    live_size = _pvc_live_size(live_snapshot, "accounting-pvc") or _pv_live_size(
        live_snapshot,
        "accounting-pv",
    )
    if not live_size:
        live_size = "128Gi"
    slurm_nodes = values.setdefault("slurmNodes", {})
    if not isinstance(slurm_nodes, dict):
        return
    accounting = slurm_nodes.setdefault("accounting", {})
    if not isinstance(accounting, dict):
        return
    mariadb = accounting.setdefault("mariadbOperator", {})
    if not isinstance(mariadb, dict):
        return
    storage = mariadb.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    storage["size"] = live_size
    storage["storageClassName"] = "slurm-local-pv"
    storage["volumeClaimTemplate"] = {
        "accessModes": ["ReadWriteMany"],
        "resources": {"requests": {"storage": live_size}},
        "storageClassName": "slurm-local-pv",
    }


def _pvc_live_size(snapshot: Mapping[str, Any], pvc_name: str) -> str:
    for pvc in _sequence_of_mappings(snapshot.get("pvcs")):
        metadata = _mapping(pvc.get("metadata"))
        if str(metadata.get("name", "") or "").strip() != pvc_name:
            continue
        status_size = str(
            _mapping(_mapping(pvc.get("status")).get("capacity")).get("storage", "") or ""
        ).strip()
        if status_size:
            return status_size
        request_size = str(
            _mapping(_mapping(_mapping(pvc.get("spec")).get("resources")).get("requests")).get(
                "storage", ""
            )
            or ""
        ).strip()
        if request_size:
            return request_size
    return ""


def _pv_live_size(snapshot: Mapping[str, Any], pv_name: str) -> str:
    for pv in _sequence_of_mappings(snapshot.get("pvs")):
        metadata = _mapping(pv.get("metadata"))
        if str(metadata.get("name", "") or "").strip() != pv_name:
            continue
        return str(_mapping(_mapping(pv.get("spec")).get("capacity")).get("storage", "") or "").strip()
    return ""


def _preserve_live_storage_sizes(values: dict[str, Any], *, live_snapshot: Mapping[str, Any]) -> None:
    volume = values.setdefault("volume", {})
    if not isinstance(volume, dict):
        return
    for value_key, pvc_name in (
        ("jail", "jail-pvc"),
        ("controllerSpool", "controller-spool-pvc"),
        ("accounting", "accounting-pvc"),
    ):
        live_size = _pvc_live_size(live_snapshot, pvc_name)
        if not live_size:
            continue
        item = volume.setdefault(value_key, {})
        if isinstance(item, dict):
            item["size"] = live_size


def _helm_upgrade_target_soperator(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
    wait: bool = True,
) -> None:
    chart_path = _target_soperator_chart_path()
    _ensure_soperator_chart_dependencies(command_runner=command_runner, chart_path=chart_path)
    _apply_soperator_crds(
        command_runner=command_runner,
        kube_context=kube_context,
        chart_path=chart_path,
    )
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-soperator-chart-") as temp_dir:
        staged_chart_path = Path(temp_dir) / chart_path.name
        shutil.copytree(
            chart_path,
            staged_chart_path,
            ignore=shutil.ignore_patterns("crds"),
        )
        command = [
            "helm",
            "--kube-context",
            kube_context,
            "upgrade",
            "--install",
            "soperator",
            str(staged_chart_path),
            "-n",
            _SOPERATOR_NAMESPACE,
            "--create-namespace",
            "--skip-crds",
            "--force-conflicts",
            "-f",
            "-",
        ]
        if wait:
            command.extend(["--wait", "--timeout", "45m"])
        values_text = json.dumps(to_plain_data(values), sort_keys=True)
        adopted: set[tuple[str, str, str]] = set()
        pending_operation_cleared = False
        while True:
            try:
                command_runner(command, input_text=values_text, timeout_seconds=3000)
                return
            except RuntimeError as exc:
                if (
                    not pending_operation_cleared
                    and "another operation" in str(exc).lower()
                    and _clear_pending_helm_release_operation(
                        command_runner=command_runner,
                        kube_context=kube_context,
                    )
                ):
                    pending_operation_cleared = True
                    continue
                adopted_key = _adopt_helm_ownership_conflict(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    error_text=str(exc),
                    already_adopted=adopted,
                )
                if adopted_key is None:
                    raise
                adopted.add(adopted_key)


def _clear_pending_helm_release_operation(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> bool:
    result = command_runner(
        [
            "helm",
            "--kube-context",
            kube_context,
            "history",
            "soperator",
            "-n",
            _SOPERATOR_NAMESPACE,
            "--max",
            "20",
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, list):
        return False
    latest_revision = 0
    latest_status = ""
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        revision = _positive_int(item.get("revision"), fallback=0)
        if revision <= latest_revision:
            continue
        latest_revision = revision
        latest_status = str(item.get("status", "") or "").strip().lower()
    if latest_revision <= 0 or not latest_status.startswith("pending-"):
        return False
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "delete",
            "secret",
            f"sh.helm.release.v1.soperator.v{latest_revision}",
            "--ignore-not-found",
        ],
        timeout_seconds=300,
    )
    return True


def _target_worker_nodeset_names(values: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    nodesets = values.get("nodesets")
    if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
        for item in nodesets:
            if not isinstance(item, Mapping):
                continue
            name = normalize_component_token(item.get("name")) or "worker"
            if name:
                names.append(name)
    return tuple(dict.fromkeys(names or ["worker"]))


def _recreate_target_worker_statefulsets(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
) -> None:
    for name in _target_worker_nodeset_names(values):
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "statefulset.apps.kruise.io",
                name,
                "--ignore-not-found",
                "--cascade=foreground",
                "--wait=true",
                "--timeout=10m",
            ],
            timeout_seconds=720,
            check=False,
        )


def _delete_pending_accounting_pvcs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
) -> None:
    result = _json_from_command(
        command_runner,
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "pvc",
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    prefix = f"storage-{target_ref}-acct-db-"
    stale_statefulset_deleted = False
    for item in _sequence_of_mappings(result.get("items")):
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        phase = str(_mapping(item.get("status")).get("phase", "") or "").strip()
        if not name.startswith(prefix) or phase != "Pending":
            continue
        access_modes = {str(mode).strip() for mode in _mapping(item.get("spec")).get("accessModes", [])}
        if "ReadWriteMany" not in access_modes and not stale_statefulset_deleted:
            command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    _SOPERATOR_NAMESPACE,
                    "delete",
                    "statefulset.apps",
                    f"{target_ref}-acct-db",
                    "--ignore-not-found",
                    "--cascade=foreground",
                    "--wait=true",
                    "--timeout=5m",
                ],
                timeout_seconds=420,
            )
            stale_statefulset_deleted = True
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "pvc",
                name,
                "--ignore-not-found",
            ],
            timeout_seconds=300,
        )


def _reconcile_target_node_storage_labels(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> None:
    for role in _SOPERATOR_COMPUTE_ROLES:
        storage_group = _SOPERATOR_ROLE_SOURCE_KIND[role]
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "label",
                "nodes",
                "-l",
                f"slurm.nebius.ai/nodeset-name={role}",
                f"nebius.com/node-group={storage_group}",
                "--overwrite",
            ],
            timeout_seconds=300,
            check=False,
        )
        if result.returncode != 0:
            detail = f"{result.stderr}\n{result.stdout}".lower()
            if "no resources found" not in detail and "not found" not in detail:
                raise RuntimeError(
                    f"{_command_text(result.args)} failed: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )


def _adopt_helm_ownership_conflict(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    error_text: str,
    already_adopted: set[tuple[str, str, str]],
) -> tuple[str, str, str] | None:
    if "invalid ownership metadata" not in error_text:
        return None
    match = _HELM_OWNERSHIP_CONFLICT_RE.search(error_text)
    if not match:
        return None
    kind = str(match.group("kind") or "").strip()
    name = str(match.group("name") or "").strip()
    namespace = str(match.group("namespace") or "").strip()
    if not kind or not name:
        return None
    adopted_key = (kind, namespace, name)
    if adopted_key in already_adopted:
        return None
    resource_type = _KUBECTL_RESOURCE_BY_KIND.get(kind, kind)
    resource_ref = f"{resource_type}/{name}"
    namespace_args = ["-n", namespace] if namespace else []
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            *namespace_args,
            "label",
            resource_ref,
            "app.kubernetes.io/managed-by=Helm",
            "--overwrite",
        ],
        timeout_seconds=120,
    )
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            *namespace_args,
            "annotate",
            resource_ref,
            "meta.helm.sh/release-name=soperator",
            f"meta.helm.sh/release-namespace={_SOPERATOR_NAMESPACE}",
            "--overwrite",
        ],
        timeout_seconds=120,
    )
    return adopted_key


def _resume_slurm_partitions(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> None:
    _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "update", "PartitionName=ALL", "State=UP"),
        check=False,
        timeout_seconds=120,
    )


def _scale_node_group(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
    count: int,
) -> None:
    result = command_runner(
        [
            "nebius",
            "mk8s",
            "node-group",
            "update",
            node_group_id,
            "--fixed-node-count",
            str(count),
            "--format",
            "json",
            "--timeout",
            "45m",
        ],
        timeout_seconds=3000,
        check=False,
    )
    if result.returncode != 0 and not _command_not_found(result):
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _delete_node_group(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
) -> None:
    result = command_runner(
        [
            "nebius",
            "mk8s",
            "node-group",
            "delete",
            node_group_id,
            "--timeout",
            "45m",
        ],
        timeout_seconds=3000,
        check=False,
    )
    if result.returncode != 0 and not _command_not_found(result):
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _command_not_found(result: SoperatorMigrationCommandResult) -> bool:
    detail = f"{result.stderr}\n{result.stdout}".lower()
    return "notfound" in detail or "not found" in detail or "resource not found" in detail


def _ensure_live_nodes_ready(snapshot: Mapping[str, Any]) -> None:
    groups = _mapping(snapshot.get("node_groups"))
    if not groups:
        raise RuntimeError("Soperator migration validation found no Kubernetes node groups.")
    empty = [
        str(name)
        for name, group in groups.items()
        if isinstance(group, Mapping) and int(group.get("node_count", 0) or 0) <= 0
    ]
    if empty:
        raise RuntimeError("Soperator migration validation found empty node groups: " + ", ".join(empty))


def _execute_create_aligned_sfs_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    project_id = _nebius_project_id(payload)
    specs = _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    specs_by_key = {spec.key: spec for spec in specs}
    phase = _phase_state(checkpoint, "create-aligned-sfs")
    filesystems = phase.setdefault("filesystems", {})
    if not isinstance(filesystems, dict):
        raise RuntimeError("Soperator migration checkpoint create-aligned-sfs.filesystems must be a mapping.")
    mutation_performed = False
    lines: list[str] = []
    filesystem_ids_by_key: dict[str, str] = {}
    for spec in specs:
        existing = _get_filesystem_by_name(
            command_runner=command_runner,
            project_id=project_id,
            name=spec.name,
        )
        created = False
        filesystem = existing
        if not _filesystem_id(filesystem):
            filesystem = _create_filesystem(
                command_runner=command_runner,
                project_id=project_id,
                spec=spec,
            )
            created = True
            mutation_performed = True
        else:
            _validate_existing_filesystem(spec, filesystem)
        filesystem_id = _filesystem_id(filesystem)
        if not filesystem_id:
            raise RuntimeError(
                f"Aligned SFS filesystem '{spec.name}' did not return a filesystem id."
            )
        filesystem_ids_by_key[spec.key] = filesystem_id
        filesystems[spec.key] = {
            "id": filesystem_id,
            "name": spec.name,
            "mount_tag": spec.mount_tag,
            "size_gib": spec.size_gib,
            "block_size_kib": spec.block_size_kib,
            "type": spec.filesystem_type,
            "created": created,
        }
        lines.append(
            f"Aligned SFS {spec.key}: {'created' if created else 'reused'} {spec.name} ({filesystem_id})"
        )
    attached, attachments = _attach_filesystems_to_source_node_groups(
        command_runner=command_runner,
        source_report=source_report,
        attachment_keys_by_group=_approved_role_attachment_keys(
            payload=payload,
            target_ref=target_ref,
            worker_node_groups=worker_node_groups,
        ),
        filesystem_ids_by_key=filesystem_ids_by_key,
        specs_by_key=specs_by_key,
    )
    mutation_performed = mutation_performed or attached
    phase["node_group_attachments"] = attachments
    lines.append(
        "Aligned SFS node-group attachments: "
        + ", ".join(
            f"{item['source_group']}={'updated' if item['updated'] else 'already-attached'}"
            for item in attachments
        )
    )
    return mutation_performed, lines


def _execute_online_bulk_data_sync_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "online-bulk-data-sync")
    jobs = phase.setdefault("jobs", {})
    if not isinstance(jobs, dict):
        raise RuntimeError("Soperator migration checkpoint online-bulk-data-sync.jobs must be a mapping.")
    storage = _snapshot_storage(source_report)
    if not storage:
        phase["skipped_reason"] = "source discovery did not detect old Soperator storage"
        return False, ["Data sync skipped: no old Soperator storage was detected."]
    mutation_performed = False
    lines: list[str] = []
    manifests: list[dict[str, Any]] = []
    live_pvcs = _snapshot_pvc_names(live_snapshot)
    missing_pvcs: list[str] = []
    for key in _SOPERATOR_STORAGE_KEYS:
        source_pvc = _source_pvc_name_for_storage_key(source_report, key)
        target_pvc = _target_pvc_name_for_storage_key(payload, target_ref, key)
        if not source_pvc:
            continue
        for pvc_name, role in ((source_pvc, "source"), (target_pvc, "target")):
            if pvc_name not in live_pvcs:
                missing_pvcs.append(f"{role}:{key}:{pvc_name}")
        if source_pvc == target_pvc:
            jobs[key] = {"source_pvc": source_pvc, "target_pvc": target_pvc, "skipped": True}
            lines.append(f"Data sync {key}: skipped because source and target PVC are {source_pvc}.")
            continue
        manifests.append(
            _copy_job_manifest(key=key, source_pvc=source_pvc, target_pvc=target_pvc)
        )
        jobs[key] = {"source_pvc": source_pvc, "target_pvc": target_pvc}
    if missing_pvcs:
        phase["missing_pvcs"] = missing_pvcs
        raise SoperatorMigrationPhaseBlocked(
            "online-bulk-data-sync requires existing source and target PVCs before copy Jobs run. "
            "Missing PVCs: "
            + ", ".join(missing_pvcs)
            + "."
        )
    if not manifests:
        phase["skipped_reason"] = "no source PVC to target PVC copy pairs were detected"
        return False, lines or ["Data sync skipped: no PVC copy pairs were detected."]
    _kubectl_apply_objects(
        command_runner=command_runner,
        kube_context=kube_context,
        objects=manifests,
        timeout_seconds=300,
    )
    mutation_performed = True
    for manifest in manifests:
        name = str(_mapping(manifest.get("metadata")).get("name", "") or "").strip()
        if not name:
            continue
        _kubectl_wait(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            resource=f"job/{name}",
            condition="condition=complete",
            timeout="60m",
            timeout_seconds=3900,
        )
        lines.append(f"Data sync job completed: {name}")
    return mutation_performed, lines


def _execute_rolling_compute_migration_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint_writer: Callable[[], None] | None = None,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    nodes = _nodes_for_worker_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    phase["worker_nodes"] = list(nodes)
    if not _has_soperator_custom_resources(live_snapshot):
        phase["skipped_reason"] = "no Soperator Slurm custom resources were detected"
        return False, [
            "Compute migration skipped: no Slurm custom resources were detected on the source cluster."
        ]
    mutation_performed, lines = _create_or_reuse_target_node_groups(
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
        command_runner=command_runner,
    )
    if checkpoint_writer is not None:
        checkpoint_writer()
    quiet_lines = _ensure_slurm_quiet(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    values = _patch_target_values_for_compute(
        payload=payload,
        target_ref=target_ref,
        checkpoint=checkpoint,
        live_snapshot=live_snapshot,
    )
    _delete_pending_accounting_pvcs(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
    )
    _reconcile_target_node_storage_labels(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    try:
        _helm_upgrade_target_soperator(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
            wait=False,
        )
        _recreate_target_worker_statefulsets(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
        )
    except Exception:
        _resume_slurm_partitions(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        raise
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="15m",
    )
    if nodes:
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=nodes,
            action="cordon",
        )
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=nodes,
            action="drain",
        )
        phase["drained_worker_nodes"] = list(nodes)
    _resume_slurm_partitions(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    mutation_performed = True
    phase["target_values_revision"] = _ROLLING_COMPUTE_VALUES_REVISION
    phase["target_values_applied_at"] = _utc_now()
    phase["slurm_quiet_window"] = "verified"
    return mutation_performed, [
        *lines,
        *quiet_lines,
        "Target Soperator chart values applied to aligned compute groups.",
        "Old worker nodes cordoned and drained after target rollout.",
    ]


def _rolling_compute_values_revision(checkpoint: Mapping[str, Any]) -> int:
    rolling = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    try:
        return int(rolling.get("target_values_revision", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _reapply_stale_rolling_compute_values(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    if _rolling_compute_values_revision(checkpoint) >= _ROLLING_COMPUTE_VALUES_REVISION:
        return False, []
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    values = _patch_target_values_for_compute(
        payload=payload,
        target_ref=target_ref,
        checkpoint=checkpoint,
        live_snapshot=live_snapshot,
    )
    _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    _delete_pending_accounting_pvcs(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
    )
    _reconcile_target_node_storage_labels(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    _helm_upgrade_target_soperator(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
        wait=False,
    )
    _recreate_target_worker_statefulsets(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
    )
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="15m",
    )
    phase["target_values_revision"] = _ROLLING_COMPUTE_VALUES_REVISION
    phase["target_values_reapplied_at"] = _utc_now()
    return True, ["Target Soperator chart values reapplied for the current compute migration contract."]


def _reconcile_completed_compute_cutover(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    lines: list[str] = []
    mutation_performed = False
    if _rolling_compute_values_revision(checkpoint) < _ROLLING_COMPUTE_VALUES_REVISION:
        phase_mutation, phase_lines = _reapply_stale_rolling_compute_values(
            checkpoint=checkpoint,
            payload=payload,
            source_report=source_report,
            live_snapshot=live_snapshot,
            target_ref=target_ref,
            kube_context=kube_context,
            command_runner=command_runner,
        )
        mutation_performed = mutation_performed or phase_mutation
        lines.extend(phase_lines)
    source_cleanup = _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    if source_cleanup:
        mutation_performed = True
        lines.append("Conflicting source Slurm resources removed for target cutover.")
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    if not phase.get("controller_spool_clustername_cleared_at"):
        _clear_controller_spool_clustername(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        phase["controller_spool_clustername_cleared_at"] = _utc_now()
        mutation_performed = True
        lines.append("Controller spool Slurm cluster-name guard cleared for target cutover.")
    if source_cleanup or mutation_performed:
        _wait_for_target_slurmcluster_available(
            command_runner=command_runner,
            kube_context=kube_context,
            target_ref=target_ref,
            timeout_seconds=900,
        )
    return mutation_performed, lines


def _clear_controller_spool_clustername(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> None:
    job_name = "cxcli-soperator-clear-clustername"
    _kubectl_apply_objects(
        command_runner=command_runner,
        kube_context=kube_context,
        objects=[
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": job_name,
                    "namespace": _SOPERATOR_NAMESPACE,
                    "labels": {
                        "app.kubernetes.io/managed-by": "nebius-cxcli",
                        "nebius-cxcli.io/soperator-migration": "true",
                    },
                },
                "spec": {
                    "backoffLimit": 0,
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "nodeSelector": {"slurm.nebius.ai/nodeset-name": "controller"},
                            "tolerations": [_role_toleration("controller")],
                            "containers": [
                                {
                                    "name": "clear",
                                    "image": "ubuntu:24.04",
                                    "command": [
                                        "/bin/sh",
                                        "-ceu",
                                        "rm -f /controller-spool/clustername",
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "controller-spool",
                                            "mountPath": "/controller-spool",
                                        }
                                    ],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "controller-spool",
                                    "persistentVolumeClaim": {
                                        "claimName": "controller-spool-pvc"
                                    },
                                }
                            ],
                        }
                    },
                },
            }
        ],
        timeout_seconds=300,
    )
    _kubectl_wait(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource=f"job/{job_name}",
        condition="condition=complete",
        timeout="10m",
        timeout_seconds=720,
    )
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "delete",
            "pod",
            "controller-0",
            "--ignore-not-found",
        ],
        timeout_seconds=300,
    )


def _delete_conflicting_source_slurm_resources(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    source_report: Mapping[str, Any],
    target_ref: str,
) -> bool:
    source_names = set(_source_slurmcluster_names(source_report, target_ref=target_ref))
    if not source_names:
        return False
    result = _json_from_command(
        command_runner,
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "slurmclusters",
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    mutation_performed = False
    for item in _sequence_of_mappings(result.get("items")):
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if not name or name == target_ref or name not in source_names:
            continue
        mutation_performed = True
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "slurmcluster",
                name,
                "--ignore-not-found",
                "--wait=false",
            ],
            timeout_seconds=300,
        )
    for source_name in sorted(source_names):
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "deployment.apps,statefulset.apps,daemonset.apps,service",
                "-l",
                f"app.kubernetes.io/name=slurmcluster,app.kubernetes.io/instance={source_name}",
                "--ignore-not-found",
                "--wait=true",
                "--timeout=5m",
            ],
            timeout_seconds=420,
            check=False,
        )
    return mutation_performed


def _wait_for_target_slurmcluster_available(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_phase = ""
    while True:
        result = _json_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "slurmcluster",
                target_ref,
                "-o",
                "json",
            ],
            timeout_seconds=120,
            check=False,
        )
        if not _mapping(result.get("metadata")):
            last_phase = "missing"
        else:
            status = _mapping(result.get("status"))
            last_phase = str(status.get("phase", "") or "").strip()
            if last_phase == "Available":
                return
        if time.monotonic() >= deadline:
            raise SoperatorMigrationPhaseBlocked(
                f"target SlurmCluster '{target_ref}' did not become Available "
                f"within {timeout_seconds}s; last phase: {last_phase or 'unknown'}."
            )
        time.sleep(10)


def _resource_output_contains(names: Sequence[str], kind: str, name: str) -> bool:
    kind_lower = kind.lower()
    return any(
        kind_lower in item.lower() and item.rsplit("/", 1)[-1] == name
        for item in names
    )


def _expected_cutover_nodesets(checkpoint: Mapping[str, Any]) -> tuple[str, ...]:
    rolling = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    target_groups = _mapping(rolling.get("target_node_groups"))
    if "worker" in target_groups:
        return ("worker",)
    return ()


def _execute_final_cutover_phase(
    *,
    checkpoint: dict[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "final-control-plane-cutover")
    if not _has_soperator_custom_resources(live_snapshot):
        phase["skipped_reason"] = "no Soperator Slurm custom resources were detected"
        _kubectl_rollout_status(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            resource="deployment/soperator-manager",
            timeout="10m",
        )
        return False, [
            "Final cutover skipped: no Slurm custom resources were detected; Soperator manager rollout is healthy."
        ]
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="15m",
    )
    _wait_for_target_slurmcluster_available(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
        timeout_seconds=900,
    )
    resources = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "slurmclusters,nodesets",
            "-o",
            "name",
        ],
        timeout_seconds=300,
    )
    names = tuple(line.strip() for line in resources.stdout.splitlines() if line.strip())
    if not _resource_output_contains(names, "slurmcluster", target_ref):
        raise SoperatorMigrationPhaseBlocked(
            f"final-control-plane-cutover expected target SlurmCluster '{target_ref}' "
            "after target chart apply, but it was not found."
        )
    missing_nodesets = [
        name
        for name in _expected_cutover_nodesets(checkpoint)
        if not _resource_output_contains(names, "nodeset", name)
    ]
    if missing_nodesets:
        raise SoperatorMigrationPhaseBlocked(
            "final-control-plane-cutover expected target NodeSet resources after "
            "target chart apply, but these were not found: "
            + ", ".join(missing_nodesets)
            + "."
        )
    phase["validated_resources"] = list(names)
    phase["cutover_at"] = _utc_now()
    return False, ["Final cutover validated: target Slurm custom resources are present."]


def _execute_validation_hold_phase(
    *,
    checkpoint: dict[str, Any],
    live_snapshot: Mapping[str, Any],
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "validation-and-rollback-hold")
    _ensure_live_nodes_ready(live_snapshot)
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="10m",
    )
    phase["validated_at"] = _utc_now()
    return False, ["Validation hold passed: nodes are present and Soperator manager rollout is healthy."]


def _execute_retire_old_resources_phase(
    *,
    checkpoint: dict[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "retire-old-resources")
    rolling = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    old_groups_raw = _mapping(rolling.get("old_node_groups"))
    old_group_names = tuple(str(name) for name in old_groups_raw if str(name).strip())
    if not old_group_names:
        if _snapshot_storage(source_report):
            raise SoperatorMigrationPhaseBlocked(
                "retire-old-resources requires manual confirmation after validating old storage "
                "and old compute references are no longer active."
            )
        if _has_soperator_custom_resources(live_snapshot):
            raise SoperatorMigrationPhaseBlocked(
                "retire-old-resources requires completed compute migration state before "
                "automatic old-resource retirement."
            )
        phase["skipped_reason"] = "no old storage or Slurm resources were detected"
        return False, ["Retire old resources skipped: no old storage or Slurm resources were detected."]

    old_nodes = _nodes_for_source_groups(
        source_report=source_report,
        source_groups=old_group_names,
    )
    if old_nodes:
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=old_nodes,
            action="cordon",
        )
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=old_nodes,
            action="drain",
        )
    retired: list[dict[str, str]] = []
    for group_name, item in old_groups_raw.items():
        node_group_id = str(_mapping(item).get("id", "") or "").strip()
        if not node_group_id:
            continue
        _scale_node_group(
            command_runner=command_runner,
            node_group_id=node_group_id,
            count=0,
        )
        _delete_node_group(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        retired.append({"source_group": str(group_name), "node_group_id": node_group_id})
    if not retired:
        raise SoperatorMigrationPhaseBlocked(
            "retire-old-resources found old compute groups in the checkpoint, but no "
            "Nebius node group ids were recorded."
        )
    phase["retired_node_groups"] = retired
    if _snapshot_storage(source_report):
        phase["storage_retirement"] = "held"
    return True, [
        "Retired old compute node groups: "
        + ", ".join(f"{item['source_group']} ({item['node_group_id']})" for item in retired)
    ]


def _load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Soperator migration checkpoint is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Soperator migration checkpoint must be a JSON object: {path}")
    if payload.get("schema") != SOPERATOR_MIGRATION_EXECUTION_SCHEMA:
        raise RuntimeError(f"Unsupported Soperator migration checkpoint schema in {path}.")
    return payload


def _write_checkpoint(path: Path, checkpoint: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_plain_data(checkpoint), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _checkpoint_for_run(
    *,
    existing: Mapping[str, Any] | None,
    target_ref: str,
    source_report_fingerprint: str,
    source_version: str,
    target_version: str,
    phase_ids: Sequence[str],
) -> dict[str, Any]:
    if existing is not None:
        if str(existing.get("target_ref", "") or "") != target_ref:
            raise RuntimeError("Soperator migration checkpoint belongs to a different target.")
        if str(existing.get("source_report_fingerprint", "") or "") != source_report_fingerprint:
            raise RuntimeError(
                "Soperator migration checkpoint is stale because the source discovery report changed. "
                "Review the new report and remove the old checkpoint before executing."
            )
        completed = {
            str(phase or "").strip()
            for phase in existing.get("completed_phases", []) or []
            if str(phase or "").strip()
        }
        unsupported_completed = sorted(completed - _SUPPORTED_EXECUTE_PHASE_IDS)
        if unsupported_completed:
            raise RuntimeError(
                "Soperator migration checkpoint contains completed phase(s) that this "
                "executor cannot resume safely: "
                + ", ".join(unsupported_completed)
                + ". Review or remove the checkpoint before executing."
            )
        checkpoint = dict(existing)
    else:
        checkpoint = {
            "schema": SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "target_ref": target_ref,
            "source_report_fingerprint": source_report_fingerprint,
            "source_version": source_version,
            "target_version": target_version,
            "created_at": _utc_now(),
            "completed_phases": [],
            "events": [],
        }
    checkpoint["updated_at"] = _utc_now()
    checkpoint["planned_phases"] = list(phase_ids)
    return checkpoint


def _checkpoint_has_mutating_progress(checkpoint: Mapping[str, Any] | None) -> bool:
    phase_state = _mapping((checkpoint or {}).get("phase_state"))
    for phase_id in _MUTATING_PHASE_IDS:
        state = phase_state.get(phase_id)
        if isinstance(state, Mapping) and bool(state):
            return True
    return False


def _target_resume_versions(target_version: str) -> set[str]:
    versions: set[str] = set()
    normalized = normalize_soperator_release_version(target_version)
    if normalized:
        versions.add(normalized)
    for marker in ("-ps.", "+"):
        base = str(target_version or "").split(marker, 1)[0]
        normalized_base = normalize_soperator_release_version(base)
        if normalized_base:
            versions.add(normalized_base)
    return versions


def execute_soperator_migration(
    *,
    config_path: Path,
    target_ref: str,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    snapshot_collector: Callable[..., Mapping[str, Any]],
    approved: bool = False,
    worker_node_groups: Sequence[str] = (),
    command_runner: SoperatorMigrationCommandRunner | None = None,
) -> SoperatorMigrationExecutionResult:
    """Run checkpointed live Soperator migration phases."""

    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("Soperator migration execute requires a target ref.")
    with SoperatorMigrationExecutionLock(
        soperator_migration_lock_path(config_path, normalized_target)
    ):
        return _execute_soperator_migration_unlocked(
            config_path=config_path,
            target_ref=normalized_target,
            payload=payload,
            source_report=source_report,
            snapshot_collector=snapshot_collector,
            approved=approved,
            worker_node_groups=worker_node_groups,
            command_runner=command_runner,
        )


def _execute_soperator_migration_unlocked(
    *,
    config_path: Path,
    target_ref: str,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    snapshot_collector: Callable[..., Mapping[str, Any]],
    approved: bool = False,
    worker_node_groups: Sequence[str] = (),
    command_runner: SoperatorMigrationCommandRunner | None = None,
) -> SoperatorMigrationExecutionResult:
    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("Soperator migration execute requires a target ref.")
    active_command_runner = command_runner or _default_command_runner
    onboarding = _target_onboarding(payload, normalized_target)
    source_snapshot, report = _source_report_payload(source_report)
    source_report_fingerprint = _fingerprint(source_report)
    expected_source_version = _expected_source_version(onboarding=onboarding, report=report)
    source_analysis_fingerprint = str(report.get("fingerprint", "") or "").strip()
    if not source_analysis_fingerprint:
        raise RuntimeError(
            "Soperator source discovery report is missing its analysis fingerprint. "
            "Rerun `nebius-cxcli soperator onboard` before executing migration."
        )
    expected_source_contract = _execution_source_contract(source_snapshot)
    expected_source_contract_fingerprint = _fingerprint(expected_source_contract)
    target_version = str(onboarding.get("target_version", "") or report.get("target_version", "") or "")
    phase_ids = _phase_ids(report)
    if not phase_ids:
        phase_ids = ("discovery-and-plan",)
    actions = _onboarding_actions(onboarding)
    requires_compute_executor = ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in actions

    kube_context = _target_kube_context(payload, normalized_target)
    checkpoint_path = soperator_migration_checkpoint_path(config_path, normalized_target)
    existing_checkpoint = _load_checkpoint(checkpoint_path)
    existing_completed = {
        str(phase or "").strip()
        for phase in (existing_checkpoint or {}).get("completed_phases", []) or []
        if str(phase or "").strip()
    }
    mutating_progress_started = bool(existing_completed & _MUTATING_PHASE_IDS) or (
        _checkpoint_has_mutating_progress(existing_checkpoint)
    )
    strict_source_fingerprint = not mutating_progress_started
    live_snapshot = snapshot_collector(kube_context=kube_context)
    live_report = analyze_soperator_onboarding_snapshot(
        live_snapshot,
        target_ref=normalized_target,
        pinned_chart_version=target_version,
        pinned_app_version=target_version,
    )
    live_source_version = normalize_soperator_release_version(live_report.source_version)
    allowed_source_versions = {expected_source_version} if expected_source_version else set()
    if mutating_progress_started:
        allowed_source_versions.update(_target_resume_versions(target_version))
    if expected_source_version and live_source_version not in allowed_source_versions:
        raise RuntimeError(
            "Live Soperator source version changed since onboarding discovery: "
            f"expected {', '.join(sorted(allowed_source_versions))}, "
            f"found {live_source_version or 'not detected'}. "
            "Rerun `nebius-cxcli soperator onboard` before executing migration."
        )
    if not live_source_version:
        raise RuntimeError(
            "Live Soperator source release was not detected. Rerun onboarding after installing "
            "the source Soperator release."
        )
    expected_node_groups = set(_mapping(expected_source_contract.get("node_groups")))
    ignored_live_target_node_groups = frozenset(
        role for role in _SOPERATOR_COMPUTE_ROLES if role not in expected_node_groups
    )
    live_source_contract_fingerprint = _fingerprint(
        _execution_source_contract(
            live_snapshot,
            ignored_node_groups=ignored_live_target_node_groups,
        )
    )
    if (
        strict_source_fingerprint
        and live_source_contract_fingerprint != expected_source_contract_fingerprint
    ):
        raise RuntimeError(
            "Live Soperator source discovery changed since onboarding: "
            f"expected stable contract fingerprint {expected_source_contract_fingerprint}, "
            f"found {live_source_contract_fingerprint}. "
            "Rerun `nebius-cxcli soperator onboard` before executing migration."
        )

    checkpoint = _checkpoint_for_run(
        existing=existing_checkpoint,
        target_ref=normalized_target,
        source_report_fingerprint=source_report_fingerprint,
        source_version=expected_source_version or live_source_version,
        target_version=target_version,
        phase_ids=phase_ids,
    )
    completed_phases = set(
        str(phase or "").strip()
        for phase in checkpoint.get("completed_phases", []) or []
        if str(phase or "").strip()
    )
    completed_phases.add("discovery-and-plan")
    existing_approval = (
        "customer-approval" in completed_phases
        or bool(str(checkpoint.get("customer_approved_at", "") or "").strip())
    )
    effective_approval = approved or existing_approval
    _append_event(
        checkpoint,
        "execute-preflight-completed",
        live_source_contract_fingerprint=live_source_contract_fingerprint,
        live_source_version=live_source_version,
        source_analysis_fingerprint=source_analysis_fingerprint,
        source_contract_fingerprint=expected_source_contract_fingerprint,
        strict_source_fingerprint=strict_source_fingerprint,
    )

    approved_worker_groups: tuple[str, ...] = ()
    if effective_approval:
        if requires_compute_executor:
            raw_worker_groups = (
                worker_node_groups
                if worker_node_groups
                else tuple(str(group or "") for group in checkpoint.get("worker_node_groups", []) or [])
            )
            approved_worker_groups = _validate_worker_node_groups(
                source_report=source_report,
                worker_node_groups=raw_worker_groups,
            )
            checkpoint["worker_node_groups"] = list(approved_worker_groups)
        completed_phases.add("customer-approval")
        if "customer_approved_at" not in checkpoint:
            checkpoint["customer_approved_at"] = _utc_now()
        if approved and not existing_approval:
            _append_event(
                checkpoint,
                "customer-approval-recorded",
                worker_node_groups=approved_worker_groups,
            )

    mutation_performed = False
    phase_lines: list[str] = []
    blocked_phase = ""
    blocked_reason = ""

    def _checkpoint_progress() -> None:
        checkpoint["completed_phases"] = _ordered_phase_list(completed_phases, phase_ids)
        checkpoint["updated_at"] = _utc_now()
        _write_checkpoint(checkpoint_path, checkpoint)

    if not effective_approval:
        blocked_phase = "customer-approval"
        blocked_reason = (
            "live preflight completed and checkpointed; customer approval is required "
            "before mutating phases."
        )
    else:
        if "rolling-compute-migration" in completed_phases and _has_soperator_custom_resources(
            live_snapshot
        ):
            phase_mutation, lines = _reconcile_completed_compute_cutover(
                checkpoint=checkpoint,
                payload=payload,
                source_report=source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            )
            if phase_mutation or lines:
                mutation_performed = mutation_performed or phase_mutation
                phase_lines.extend(
                    [f"rolling-compute-migration: {line}" for line in lines]
                )
                _append_event(
                    checkpoint,
                    "execute-phase-reconciled",
                    phase="rolling-compute-migration",
                    mutation_performed=phase_mutation,
                )
                _checkpoint_progress()
                live_snapshot = snapshot_collector(kube_context=kube_context)
        phase_handlers: Mapping[str, Callable[[], tuple[bool, list[str]]]] = {
            "create-aligned-sfs": lambda: _execute_create_aligned_sfs_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=source_report,
                target_ref=normalized_target,
                worker_node_groups=approved_worker_groups,
                command_runner=active_command_runner,
            ),
            "online-bulk-data-sync": lambda: _execute_online_bulk_data_sync_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            "rolling-compute-migration": lambda: _execute_rolling_compute_migration_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                worker_node_groups=approved_worker_groups,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
            ),
            "final-control-plane-cutover": lambda: _execute_final_cutover_phase(
                checkpoint=checkpoint,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            "validation-and-rollback-hold": lambda: _execute_validation_hold_phase(
                checkpoint=checkpoint,
                live_snapshot=live_snapshot,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            "retire-old-resources": lambda: _execute_retire_old_resources_phase(
                checkpoint=checkpoint,
                source_report=source_report,
                live_snapshot=live_snapshot,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
        }
        for phase_id in phase_ids:
            if phase_id in {"discovery-and-plan", "customer-approval"}:
                continue
            if phase_id in completed_phases:
                continue
            handler = phase_handlers.get(phase_id)
            if handler is None:
                blocked_phase = phase_id
                blocked_reason = f"unsupported Soperator migration phase '{phase_id}'."
                break
            try:
                phase_mutation, lines = handler()
            except SoperatorMigrationPhaseBlocked as exc:
                blocked_phase = phase_id
                blocked_reason = str(exc)
                break
            mutation_performed = mutation_performed or phase_mutation
            completed_phases.add(phase_id)
            _append_event(
                checkpoint,
                "execute-phase-completed",
                phase=phase_id,
                mutation_performed=phase_mutation,
            )
            phase_lines.extend([f"{phase_id}: {line}" for line in lines])
            _checkpoint_progress()
            if phase_id in {
                "create-aligned-sfs",
                "online-bulk-data-sync",
                "rolling-compute-migration",
                "final-control-plane-cutover",
            }:
                live_snapshot = snapshot_collector(kube_context=kube_context)
                _append_event(
                    checkpoint,
                    "execute-live-snapshot-refreshed",
                    after_phase=phase_id,
                )
                _checkpoint_progress()

    if blocked_phase:
        checkpoint["blocked_phase"] = blocked_phase
        checkpoint["blocked_reason"] = blocked_reason
        _append_event(checkpoint, "execute-blocked", blocked_phase=blocked_phase)
    else:
        checkpoint["blocked_phase"] = "none"
        checkpoint["blocked_reason"] = ""
        _append_event(checkpoint, "execute-completed")
    _checkpoint_progress()

    lines = [
        f"Execute preflight checkpoint: {checkpoint_path}",
        f"Live source version verified: {live_source_version}",
        "Completed execute phases: " + ", ".join(_ordered_phase_list(completed_phases, phase_ids)),
    ]
    if approved_worker_groups:
        lines.insert(3, "Approved worker node groups: " + ", ".join(approved_worker_groups))
    lines.extend(phase_lines)
    lines.extend(
        [
            f"Blocked phase: {checkpoint['blocked_phase']}",
            f"Blocked reason: {blocked_reason or 'none'}",
            "Mutation performed: " + ("yes." if mutation_performed else "no."),
        ]
    )
    return SoperatorMigrationExecutionResult(
        checkpoint_path=checkpoint_path,
        completed_phases=tuple(_ordered_phase_list(completed_phases, phase_ids)),
        blocked_phase=str(checkpoint["blocked_phase"]),
        blocked_reason=blocked_reason,
        live_source_version=live_source_version,
        target_version=target_version,
        mutation_performed=mutation_performed,
        lines=tuple(lines),
    )
