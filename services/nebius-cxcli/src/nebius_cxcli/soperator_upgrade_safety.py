"""Redacted, read-only protected-state capture for Soperator upgrades."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .runtime_config import to_plain_data

SOPERATOR_UPGRADE_SAFETY_SCHEMA = "nebius-cxcli-soperator-upgrade-safety/v1"
_SLURM_CONFIG_CAPTURE_BANNER = re.compile(r"\AConfiguration data as of [^\r\n]*(?:\r?\n|$)")
_SLURM_CONFIGURATION_FAILURE_MARKERS = (
    "could not establish a configuration source",
    "dns srv lookup failed",
    "resolve_ctls_from_dns_srv",
    "failed to fetch config",
)
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|credential|password|private.?key|secret|token)",
    re.IGNORECASE,
)


class SafetyCommandResult(Protocol):
    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


SafetyCommandRunner = Callable[..., SafetyCommandResult]


@dataclass(frozen=True)
class ProtectedCustomerState:
    target_ref: str
    namespace: str
    captured_at: str
    sections: Mapping[str, Any]
    warnings: tuple[str, ...] = ()
    command_audit: tuple[Mapping[str, Any], ...] = ()
    complete: bool = True

    @property
    def content_hash(self) -> str:
        return _stable_hash(
            {
                "target_ref": self.target_ref,
                "namespace": self.namespace,
                "sections": self.sections,
                "complete": self.complete,
            }
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": SOPERATOR_UPGRADE_SAFETY_SCHEMA,
            "target_ref": self.target_ref,
            "namespace": self.namespace,
            "captured_at": self.captured_at,
            "complete": self.complete,
            "hash": self.content_hash,
            "sections": to_plain_data(self.sections),
            "warnings": list(self.warnings),
            "command_audit": [to_plain_data(item) for item in self.command_audit],
        }


def capture_protected_customer_state(
    *,
    command_runner: SafetyCommandRunner,
    target_ref: str,
    namespace: str = "soperator",
    kube_context: str | None = None,
    source_payload: Mapping[str, Any] | None = None,
    admitted_home_mount_sha256: str = "",
    timeout_seconds: int = 120,
) -> ProtectedCustomerState:
    """Capture only bounded identity and hash evidence through read-only probes."""

    normalized_namespace = str(namespace or "soperator").strip()
    admitted_home_mount = str(admitted_home_mount_sha256 or "").strip()
    if admitted_home_mount and not re.fullmatch(r"sha256:[0-9a-f]{64}", admitted_home_mount):
        raise RuntimeError("admitted Soperator home-mount identity is invalid")
    audit: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = True

    def capture(
        section: str,
        resource: str,
        *,
        cluster_scoped: bool = False,
        sanitizer: Callable[[Mapping[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        nonlocal complete
        command = _kubectl_args(
            ("get", resource, "-o", "json"),
            namespace=None if cluster_scoped else normalized_namespace,
            kube_context=kube_context,
        )
        result = _run_readonly(
            command_runner,
            command,
            timeout_seconds=timeout_seconds,
        )
        audit.append(_command_audit(result, command=command, section=section))
        if result.returncode != 0:
            complete = False
            warning = _command_failure_summary(section, result)
            warnings.append(warning)
            return {"available": False, "error": warning}
        payload = _json_object(result.stdout)
        if payload is None:
            complete = False
            warning = f"{section} capture returned non-JSON output"
            warnings.append(warning)
            return {"available": False, "error": warning}
        return sanitizer(payload)

    pods = capture("pods", "pods", sanitizer=_sanitize_pods)
    pvcs = capture("pvcs", "pvc", sanitizer=_sanitize_pvcs)
    pvs = capture("pvs", "pv", cluster_scoped=True, sanitizer=_sanitize_pvs)
    secrets = capture("secrets", "secrets", sanitizer=_sanitize_secrets)
    slurm_runtime = _capture_slurm_runtime(
        command_runner=command_runner,
        namespace=normalized_namespace,
        kube_context=kube_context,
        pods=pods,
        timeout_seconds=timeout_seconds,
        audit=audit,
        warnings=warnings,
    )
    if not slurm_runtime.get("available"):
        if admitted_home_mount:
            slurm_runtime = {
                "available": True,
                "source": "admitted-protected-data-plane",
                "home_mount": {
                    "available": True,
                    "stdout_sha256": admitted_home_mount,
                },
            }
        else:
            complete = False

    sections: dict[str, Any] = {
        "pods": pods,
        "pvcs": pvcs,
        "pvs": pvs,
        "secrets": secrets,
        "slurm_runtime": slurm_runtime,
    }
    if source_payload is not None:
        sections["source_payload"] = {
            "target_ref": str(target_ref or "").strip(),
            "hash": _stable_hash(_redact_secrets(to_plain_data(source_payload))),
        }
    return ProtectedCustomerState(
        target_ref=str(target_ref or "").strip(),
        namespace=normalized_namespace,
        captured_at=_utc_now(),
        sections=sections,
        warnings=tuple(warnings),
        command_audit=tuple(audit),
        complete=complete,
    )


def _kubectl_args(
    args: Sequence[str],
    *,
    namespace: str | None,
    kube_context: str | None,
) -> tuple[str, ...]:
    command = ["kubectl"]
    if str(kube_context or "").strip():
        command.extend(("--context", str(kube_context).strip()))
    if namespace:
        command.extend(("-n", namespace))
    command.extend(str(arg) for arg in args)
    return tuple(command)


def _run_readonly(
    command_runner: SafetyCommandRunner,
    args: Sequence[str],
    *,
    timeout_seconds: int,
) -> SafetyCommandResult:
    verb = _kubectl_verb(args)
    if verb not in {"get", "exec"}:
        raise RuntimeError(
            f"Refusing mutating command during Soperator state capture: {shlex.join(args)}"
        )
    return command_runner(
        args,
        input_text=None,
        timeout_seconds=timeout_seconds,
        check=False,
    )


def _kubectl_verb(args: Sequence[str]) -> str:
    tokens = tuple(str(item) for item in args)
    skip_next = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in {"--context", "-n", "--namespace", "--kubeconfig"}:
            skip_next = True
            continue
        if not token.startswith("-"):
            return token
    return ""


def _capture_slurm_runtime(
    *,
    command_runner: SafetyCommandRunner,
    namespace: str,
    kube_context: str | None,
    pods: Mapping[str, Any],
    timeout_seconds: int,
    audit: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    login_pod = _first_login_pod(pods)
    if not login_pod:
        warning = "No running login pod was discovered for Slurm runtime capture."
        warnings.append(warning)
        return {"available": False, "reason": warning}
    commands = {
        "slurm_config": "scontrol show config",
        "slurm_partitions": "scontrol show partition",
        "slurm_nodes": "scontrol show nodes",
        "accounting_qos": "sacctmgr -nP show qos format=name,priority,grptres,maxjobs,maxsubmit",
        "accounting_associations": (
            "sacctmgr -nP show assoc format=cluster,account,user,partition,qos"
        ),
        "home_mount": (
            "findmnt -T /home --json || findmnt /home --json || mount | grep ' on /home ' || true"
        ),
    }
    captured: dict[str, Any] = {"available": True, "login_pod": login_pod}
    for key, shell_command in commands.items():
        login_command = _kubectl_args(
            (
                "exec",
                login_pod,
                "-c",
                "sshd",
                "--",
                "chroot",
                "/mnt/jail",
                "bash",
                "-lc",
                shell_command,
            ),
            namespace=namespace,
            kube_context=kube_context,
        )
        result = _run_readonly(
            command_runner,
            login_command,
            timeout_seconds=timeout_seconds,
        )
        audit.append(
            _command_audit(
                result,
                command=login_command,
                section=f"slurm_runtime.{key}.login",
            )
        )
        if _needs_controller_slurm_fallback(result):
            controller_command = _kubectl_args(
                (
                    "exec",
                    "controller-0",
                    "-c",
                    "slurmctld",
                    "--",
                    "bash",
                    "-lc",
                    shell_command,
                ),
                namespace=namespace,
                kube_context=kube_context,
            )
            result = _run_readonly(
                command_runner,
                controller_command,
                timeout_seconds=timeout_seconds,
            )
            audit.append(
                _command_audit(
                    result,
                    command=controller_command,
                    section=f"slurm_runtime.{key}.controller",
                )
            )
        if result.returncode != 0:
            warning = _command_failure_summary(f"slurm_runtime.{key}", result)
            warnings.append(warning)
            captured[key] = {"available": False, "error": warning}
            captured["available"] = False
            continue
        protected_stdout = (
            _SLURM_CONFIG_CAPTURE_BANNER.sub("", result.stdout, count=1)
            if key == "slurm_config"
            else result.stdout
        )
        captured[key] = {
            "available": True,
            "stdout_sha256": _sha256_text(protected_stdout),
            "line_count": sum(1 for line in protected_stdout.splitlines() if line.strip()),
        }
    return captured


def _needs_controller_slurm_fallback(result: SafetyCommandResult) -> bool:
    if result.returncode == 0:
        return False
    detail = f"{result.stderr}\n{result.stdout}".lower()
    return any(marker in detail for marker in _SLURM_CONFIGURATION_FAILURE_MARKERS)


def _sanitize_pods(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw in _items(payload):
        metadata = _metadata(raw)
        spec = raw.get("spec") if isinstance(raw.get("spec"), Mapping) else {}
        status = raw.get("status") if isinstance(raw.get("status"), Mapping) else {}
        items.append(
            {
                **_resource_identity(raw, kind="Pod"),
                "phase": str(status.get("phase") or ""),
                "node_name": str(spec.get("nodeName") or ""),
                "labels": _string_mapping(metadata.get("labels")),
            }
        )
    return _resource_list(items)


def _sanitize_pvcs(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw in _items(payload):
        spec = raw.get("spec") if isinstance(raw.get("spec"), Mapping) else {}
        status = raw.get("status") if isinstance(raw.get("status"), Mapping) else {}
        resources = spec.get("resources") if isinstance(spec.get("resources"), Mapping) else {}
        requests = (
            resources.get("requests") if isinstance(resources.get("requests"), Mapping) else {}
        )
        capacity = status.get("capacity") if isinstance(status.get("capacity"), Mapping) else {}
        items.append(
            {
                **_resource_identity(raw, kind="PersistentVolumeClaim"),
                "phase": str(status.get("phase") or ""),
                "volume_name": str(spec.get("volumeName") or ""),
                "storage_class": str(spec.get("storageClassName") or ""),
                "access_modes": sorted(str(item) for item in spec.get("accessModes", []) or []),
                "request_storage": str(requests.get("storage") or ""),
                "capacity_storage": str(capacity.get("storage") or ""),
                "selector_hash": _stable_hash(spec.get("selector", {})),
            }
        )
    return _resource_list(items)


def _sanitize_pvs(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw in _items(payload):
        spec = raw.get("spec") if isinstance(raw.get("spec"), Mapping) else {}
        status = raw.get("status") if isinstance(raw.get("status"), Mapping) else {}
        claim = spec.get("claimRef") if isinstance(spec.get("claimRef"), Mapping) else {}
        capacity = spec.get("capacity") if isinstance(spec.get("capacity"), Mapping) else {}
        items.append(
            {
                **_resource_identity(raw, kind="PersistentVolume"),
                "phase": str(status.get("phase") or ""),
                "claim": {
                    "namespace": str(claim.get("namespace") or ""),
                    "name": str(claim.get("name") or ""),
                },
                "storage_class": str(spec.get("storageClassName") or ""),
                "capacity_storage": str(capacity.get("storage") or ""),
                "persistent_volume_reclaim_policy": str(
                    spec.get("persistentVolumeReclaimPolicy") or ""
                ),
            }
        )
    return _resource_list(items)


def _sanitize_secrets(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw in _items(payload):
        data = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
        string_data = raw.get("stringData") if isinstance(raw.get("stringData"), Mapping) else {}
        items.append(
            {
                **_resource_identity(raw, kind="Secret"),
                "type": str(raw.get("type") or ""),
                "data_keys": sorted(str(key) for key in data),
                "string_data_keys": sorted(str(key) for key in string_data),
                "data_sha256_by_key": {
                    str(key): _sha256_text(str(value)) for key, value in sorted(data.items())
                },
                "string_data_sha256_by_key": {
                    str(key): _sha256_text(str(value)) for key, value in sorted(string_data.items())
                },
            }
        )
    return _resource_list(items)


def _items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = payload.get("items")
    return tuple(item for item in raw if isinstance(item, Mapping)) if isinstance(raw, list) else ()


def _resource_list(items: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(items, key=lambda item: (str(item.get("namespace")), str(item.get("name"))))
    return {"available": True, "count": len(ordered), "items": ordered}


def _metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _resource_identity(item: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    metadata = _metadata(item)
    return {
        "kind": kind,
        "namespace": str(metadata.get("namespace") or ""),
        "name": str(metadata.get("name") or ""),
        "uid": str(metadata.get("uid") or ""),
        "resource_version": str(metadata.get("resourceVersion") or ""),
        "labels": _string_mapping(metadata.get("labels")),
    }


def _first_login_pod(pods: Mapping[str, Any]) -> str:
    raw_items = pods.get("items")
    if not isinstance(raw_items, list):
        return ""
    candidates = []
    for item in raw_items:
        if not isinstance(item, Mapping) or str(item.get("phase") or "") != "Running":
            continue
        name = str(item.get("name") or "")
        labels = item.get("labels") if isinstance(item.get("labels"), Mapping) else {}
        role = " ".join(str(value) for value in labels.values()).lower()
        if "login" in name.lower() or "login" in role:
            candidates.append(name)
    return sorted(candidates)[0] if candidates else ""


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                {"redacted_sha256": _stable_hash(item)}
                if _SENSITIVE_KEY.search(str(key))
                else _redact_secrets(item)
            )
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _string_mapping(value: Any) -> dict[str, str]:
    return (
        dict(sorted((str(key), str(item)) for key, item in value.items()))
        if isinstance(value, Mapping)
        else {}
    )


def _json_object(value: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _command_audit(
    result: SafetyCommandResult,
    *,
    command: Sequence[str],
    section: str,
) -> dict[str, Any]:
    return {
        "section": section,
        "command": shlex.join(str(arg) for arg in command),
        "returncode": int(result.returncode),
        "read_only": True,
        "stdout_sha256": _sha256_text(result.stdout),
        "stderr_summary": str(result.stderr or "").strip()[:300],
    }


def _command_failure_summary(section: str, result: SafetyCommandResult) -> str:
    detail = str(result.stderr or result.stdout or "").strip()[:300]
    return f"{section} capture failed with exit code {result.returncode}" + (
        f": {detail}" if detail else ""
    )


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "SOPERATOR_UPGRADE_SAFETY_SCHEMA",
    "ProtectedCustomerState",
    "capture_protected_customer_state",
]
