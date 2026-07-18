"""Shared protected-state capture and fast verification for Soperator upgrades."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .runtime_config import to_plain_data

SOPERATOR_UPGRADE_SAFETY_SCHEMA = "nebius-cxcli-soperator-upgrade-safety/v1"
EXTERNAL_JAIL_OPEN_METRICS_HANDOFF_REVISION = 1
_PROTECTED_PVC_KEYS = ("jail", "controller-spool", "accounting")
_READONLY_KUBECTL_VERBS = frozenset({"api-resources", "exec", "get", "logs", "rollout", "version"})
_MUTATING_KUBECTL_VERBS = frozenset(
    {
        "annotate",
        "apply",
        "cordon",
        "create",
        "delete",
        "drain",
        "edit",
        "label",
        "patch",
        "replace",
        "rollout",
        "scale",
        "taint",
        "uncordon",
    }
)
_UNAVAILABLE_STATUSES = frozenset({"failed", "pending", "unknown"})
_BAD_WAITING_REASONS = frozenset(
    {
        "CrashLoopBackOff",
        "CreateContainerConfigError",
        "CreateContainerError",
        "ErrImagePull",
        "Error",
        "ImagePullBackOff",
        "InvalidImageName",
        "RunContainerError",
    }
)


class SafetyCommandResult(Protocol):
    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


SafetyCommandRunner = Callable[..., SafetyCommandResult]


@dataclass(frozen=True)
class ProtectedStateDelta:
    kind: str
    resource: str
    field: str
    before: Any
    after: Any
    classification: str
    approval_required: bool
    remediation: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "resource": self.resource,
            "field": self.field,
            "before": _summary_value(self.before),
            "after": _summary_value(self.after),
            "classification": self.classification,
            "approval_required": self.approval_required,
            "remediation": self.remediation,
        }


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


@dataclass(frozen=True)
class PostUpgradeVerificationResult:
    status: str
    passed: bool
    checks: tuple[Mapping[str, Any], ...]
    protected_state: ProtectedCustomerState
    comparison: Mapping[str, Any]
    command_audit: tuple[Mapping[str, Any], ...]
    heavy_validation_followups: tuple[Mapping[str, Any], ...]
    zero_downtime_eligibility: Mapping[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": SOPERATOR_UPGRADE_SAFETY_SCHEMA,
            "status": self.status,
            "passed": self.passed,
            "checks": [to_plain_data(item) for item in self.checks],
            "protected_customer_state": self.protected_state.as_payload(),
            "comparison": to_plain_data(self.comparison),
            "command_audit": [to_plain_data(item) for item in self.command_audit],
            "heavy_validation_followups": [
                to_plain_data(item) for item in self.heavy_validation_followups
            ],
            "zero_downtime_eligibility": to_plain_data(self.zero_downtime_eligibility),
        }


def protected_customer_state_from_payload(
    payload: Mapping[str, Any] | None,
) -> ProtectedCustomerState | None:
    if not isinstance(payload, Mapping):
        return None
    target_ref = str(payload.get("target_ref", "") or "").strip()
    namespace = str(payload.get("namespace", "") or "").strip()
    sections = payload.get("sections")
    if not target_ref or not namespace or not isinstance(sections, Mapping):
        return None
    warnings = tuple(str(item) for item in payload.get("warnings", []) or [] if str(item).strip())
    audit = tuple(
        dict(item) for item in payload.get("command_audit", []) or [] if isinstance(item, Mapping)
    )
    return ProtectedCustomerState(
        target_ref=target_ref,
        namespace=namespace,
        captured_at=str(payload.get("captured_at", "") or ""),
        sections=dict(sections),
        warnings=warnings,
        command_audit=audit,
        complete=bool(payload.get("complete", True)),
    )


def capture_protected_customer_state(
    *,
    command_runner: SafetyCommandRunner,
    target_ref: str,
    namespace: str = "soperator",
    kube_context: str | None = None,
    source_payload: Mapping[str, Any] | None = None,
    timeout_seconds: int = 120,
) -> ProtectedCustomerState:
    """Capture redacted customer state using read-only Kubernetes/Slurm probes."""

    namespace = namespace or "soperator"
    audit: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = True

    def _collect(
        section: str,
        args: Sequence[str],
        *,
        namespaced: bool = True,
        namespace_override: str | None = None,
        optional: bool = False,
        parser: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> Any:
        nonlocal complete
        command_namespace = namespace_override if namespace_override is not None else namespace
        command = _kubectl_args(
            args,
            namespace=command_namespace if namespaced else None,
            kube_context=kube_context,
        )
        result = _run_readonly(command_runner, command, timeout_seconds=timeout_seconds)
        audit.append(_command_audit(result, section=section))
        if result.returncode != 0:
            warning = _command_failure_summary(section, result)
            warnings.append(warning)
            if not optional:
                complete = False
            return {"available": False, "error": warning}
        payload = _json_object(result.stdout)
        if payload is None:
            warning = f"{section} capture returned non-JSON output"
            warnings.append(warning)
            if not optional:
                complete = False
            return {"available": False, "error": warning}
        if parser is None:
            return _sanitize_resource_list(payload)
        return parser(payload)

    pods = _collect("pods", ("get", "pods", "-o", "json"), parser=_sanitize_pods)
    pvcs = _collect("pvcs", ("get", "pvc", "-o", "json"), parser=_sanitize_pvcs)
    pvs = _collect("pvs", ("get", "pv", "-o", "json"), namespaced=False, parser=_sanitize_pvs)
    configmaps = _collect(
        "configmaps",
        ("get", "configmaps", "-o", "json"),
        parser=_sanitize_configmaps,
    )
    secrets = _collect("secrets", ("get", "secrets", "-o", "json"), parser=_sanitize_secrets)
    deployments = _collect(
        "deployments",
        ("get", "deployments", "-o", "json"),
        optional=True,
        parser=lambda payload: _sanitize_workloads(payload, workload_kind="Deployment"),
    )
    statefulsets = _collect(
        "statefulsets",
        ("get", "statefulsets", "-o", "json"),
        optional=True,
        parser=lambda payload: _sanitize_workloads(payload, workload_kind="StatefulSet"),
    )
    daemonsets = _collect(
        "daemonsets",
        ("get", "daemonsets", "-o", "json"),
        optional=True,
        parser=lambda payload: _sanitize_workloads(payload, workload_kind="DaemonSet"),
    )
    slurmclusters = _collect(
        "slurmclusters",
        ("get", "slurmclusters", "-o", "json"),
        parser=lambda payload: _sanitize_custom_resources(payload, kind="SlurmCluster"),
    )
    nodesets = _collect(
        "nodesets",
        ("get", "nodesets", "-o", "json"),
        parser=lambda payload: _sanitize_custom_resources(payload, kind="NodeSet"),
    )
    activechecks = _collect(
        "activechecks",
        ("get", "activechecks", "-o", "json"),
        optional=True,
        parser=_sanitize_activechecks,
    )
    helmreleases = _collect(
        "helmreleases",
        ("get", "helmreleases", "-o", "json"),
        namespaced=True,
        optional=True,
        parser=lambda payload: _sanitize_flux_resources(payload, kind="HelmRelease"),
    )
    flux_system_helmreleases = _collect(
        "flux-system.helmreleases",
        ("get", "helmreleases", "-o", "json"),
        namespaced=True,
        namespace_override="flux-system",
        optional=True,
        parser=lambda payload: _sanitize_flux_resources(payload, kind="HelmRelease"),
    )
    kustomizations = _collect(
        "kustomizations",
        ("get", "kustomizations", "-o", "json"),
        namespaced=True,
        optional=True,
        parser=lambda payload: _sanitize_flux_resources(payload, kind="Kustomization"),
    )
    flux_system_kustomizations = _collect(
        "flux-system.kustomizations",
        ("get", "kustomizations", "-o", "json"),
        namespaced=True,
        namespace_override="flux-system",
        optional=True,
        parser=lambda payload: _sanitize_flux_resources(payload, kind="Kustomization"),
    )
    nodes = _collect(
        "nodes",
        ("get", "nodes", "-o", "json"),
        namespaced=False,
        parser=_sanitize_nodes,
    )
    slurm_runtime = _capture_slurm_runtime(
        command_runner=command_runner,
        namespace=namespace,
        kube_context=kube_context,
        pods=pods,
        timeout_seconds=timeout_seconds,
        audit=audit,
        warnings=warnings,
    )

    sections: dict[str, Any] = {
        "pods": pods,
        "pvcs": pvcs,
        "pvs": pvs,
        "configmaps": configmaps,
        "secrets": secrets,
        "workloads": {
            "deployments": deployments,
            "statefulsets": statefulsets,
            "daemonsets": daemonsets,
        },
        "slurmclusters": slurmclusters,
        "nodesets": nodesets,
        "activechecks": activechecks,
        "flux": {
            "helmreleases": helmreleases,
            "flux_system_helmreleases": flux_system_helmreleases,
            "kustomizations": kustomizations,
            "flux_system_kustomizations": flux_system_kustomizations,
        },
        "nodes": nodes,
        "slurm_runtime": slurm_runtime,
    }
    if source_payload is not None:
        sections["source_payload"] = _sanitize_source_payload(source_payload, target_ref=target_ref)

    return ProtectedCustomerState(
        target_ref=target_ref,
        namespace=namespace,
        captured_at=_utc_now(),
        sections=sections,
        warnings=tuple(warnings),
        command_audit=tuple(audit),
        complete=complete,
    )


def compare_protected_customer_state(
    *,
    before: ProtectedCustomerState,
    after: ProtectedCustomerState,
) -> dict[str, Any]:
    deltas: list[ProtectedStateDelta] = []
    section_names = sorted(set(before.sections) | set(after.sections))
    for section in section_names:
        before_section = before.sections.get(section)
        after_section = after.sections.get(section)
        if _stable_hash(before_section) == _stable_hash(after_section):
            continue
        deltas.extend(_section_deltas(section, before_section, after_section))
    if not before.complete:
        deltas.append(
            ProtectedStateDelta(
                kind="protected-state",
                resource="before-capture",
                field="complete",
                before=False,
                after=after.complete,
                classification="blocked",
                approval_required=False,
                remediation="Rerun after the pre-upgrade protected-state capture succeeds.",
            )
        )
    if not after.complete:
        deltas.append(
            ProtectedStateDelta(
                kind="protected-state",
                resource="after-capture",
                field="complete",
                before=before.complete,
                after=False,
                classification="blocked",
                approval_required=False,
                remediation="Investigate the failed post-upgrade protected-state capture.",
            )
        )
    blocked = [delta for delta in deltas if delta.classification == "blocked"]
    approval = [delta for delta in deltas if delta.approval_required]
    return {
        "schema": SOPERATOR_UPGRADE_SAFETY_SCHEMA,
        "status": "matched" if not deltas else ("blocked" if blocked else "drift-detected"),
        "before_hash": before.content_hash,
        "after_hash": after.content_hash,
        "deltas": [delta.as_payload() for delta in deltas],
        "blocked_count": len(blocked),
        "approval_required_count": len(approval),
    }


def _classify_external_intentional_deltas(
    comparison: Mapping[str, Any],
    *,
    target_ref: str,
    namespace: str = "soperator",
    after_state: ProtectedCustomerState | None = None,
    open_metrics_handoff: Mapping[str, Any] | None = None,
    open_metrics_handoff_verified: bool = False,
    open_metrics_comparison_hashes_match: bool = False,
) -> dict[str, Any]:
    return _classify_intentional_deltas(
        comparison,
        predicate=lambda delta: (
            _is_external_intentional_migration_delta(
                delta,
                target_ref=target_ref,
            )
            or _is_external_accounting_config_successor_delta(
                delta,
                after_state=after_state,
                target_ref=target_ref,
                namespace=namespace,
            )
            or _is_external_restored_open_metrics_delta(
                delta,
                target_ref=target_ref,
                namespace=namespace,
                handoff=open_metrics_handoff,
                handoff_verified=open_metrics_handoff_verified,
                comparison_hashes_match=open_metrics_comparison_hashes_match,
            )
        ),
        remediation=(
            "Expected external migration ownership, chart takeover, node replacement, "
            "or checkpoint-verified OpenMetrics restoration drift."
        ),
    )


def _is_external_accounting_config_successor_delta(
    delta: Mapping[str, Any],
    *,
    after_state: ProtectedCustomerState | None,
    target_ref: str,
    namespace: str,
) -> bool:
    if after_state is None:
        return False
    field = str(delta.get("field", "") or "")
    if (
        str(delta.get("kind", "") or "") != "configmaps"
        or str(delta.get("resource", "") or "")
        != f"{namespace}/soperator-acct-db-config-default"
        or field != "data_sha256_by_key"
    ):
        return False
    configmaps = _items_by_name(after_state.sections.get("configmaps"))
    legacy = configmaps.get(f"{namespace}/soperator-acct-db-config-default")
    successor = configmaps.get(f"{namespace}/{target_ref}-acct-db-config-default")
    if not legacy or not successor:
        return False
    legacy_contract = legacy.get(field)
    successor_contract = successor.get(field)
    if bool(
        isinstance(legacy_contract, Mapping)
        and legacy_contract
        and legacy_contract == successor_contract
        and delta.get("after") == _summary_value(successor_contract)
    ):
        return True

    # The retired legacy MariaDB may reconcile its default ConfigMap while the
    # external handoff is completing.  That drift is harmless only when the
    # source database workload is gone and the live target successor retains
    # the exact protected pre-upgrade contract.
    workloads = after_state.sections.get("workloads")
    statefulsets = workloads.get("statefulsets") if isinstance(workloads, Mapping) else None
    statefulsets_by_name = _items_by_name(statefulsets)
    return bool(
        isinstance(successor_contract, Mapping)
        and successor_contract
        and delta.get("before") == _summary_value(successor_contract)
        and f"{namespace}/soperator-acct-db" not in statefulsets_by_name
        and f"{namespace}/{target_ref}-acct-db" in statefulsets_by_name
    )


def _is_external_restored_open_metrics_delta(
    delta: Mapping[str, Any],
    *,
    target_ref: str,
    namespace: str,
    handoff: Mapping[str, Any] | None,
    handoff_verified: bool,
    comparison_hashes_match: bool,
) -> bool:
    proof = handoff if isinstance(handoff, Mapping) else {}
    desired_enabled = proof.get("desired_enabled")
    observed_enabled = proof.get("observed_enabled")
    field = str(delta.get("field", "") or "")
    base_contract_matches = bool(
        handoff_verified
        and str(delta.get("kind", "") or "") == "slurmclusters"
        and str(delta.get("resource", "") or "") == f"{namespace}/{target_ref}"
        and proof.get("revision") == EXTERNAL_JAIL_OPEN_METRICS_HANDOFF_REVISION
        and str(proof.get("status", "") or "").strip() == "restored"
        and isinstance(desired_enabled, bool)
        and isinstance(observed_enabled, bool)
        and observed_enabled == desired_enabled
    )
    if not base_contract_matches:
        return False
    if field == "controller_open_metrics_enabled":
        # ProtectedStateDelta summaries stringify scalar values.  Normalize the
        # summary before binding this delta to the verified desired boolean.
        summarized_after = delta.get("after")
        if isinstance(summarized_after, bool):
            after_enabled = summarized_after
        elif summarized_after == "True":
            after_enabled = True
        elif summarized_after == "False":
            after_enabled = False
        else:
            return False
        return after_enabled is desired_enabled
    return bool(field in {"spec_hash", "spec_keys"} and comparison_hashes_match)


def _classify_managed_chart_upgrade_deltas(comparison: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(to_plain_data(comparison))
    raw_deltas = [item for item in payload.get("deltas", []) or [] if isinstance(item, Mapping)]
    chart_touched_resources = {
        _delta_resource_key(item) for item in raw_deltas if _is_managed_chart_metadata_delta(item)
    }
    managed_runtime_touched = any(
        _is_managed_chart_runtime_source_delta(item, chart_touched_resources) for item in raw_deltas
    )
    deltas: list[dict[str, Any]] = []
    for item in raw_deltas:
        delta = dict(item)
        if _is_managed_chart_upgrade_delta(
            delta,
            chart_touched_resources=chart_touched_resources,
            managed_runtime_touched=managed_runtime_touched,
        ):
            delta["classification"] = "intentional_upgrade"
            delta["approval_required"] = False
            delta["remediation"] = "Expected managed Soperator chart-owned state change."
        deltas.append(delta)
    blocked = [delta for delta in deltas if delta.get("classification") == "blocked"]
    approval = [delta for delta in deltas if bool(delta.get("approval_required"))]
    payload["deltas"] = deltas
    payload["blocked_count"] = len(blocked)
    payload["approval_required_count"] = len(approval)
    payload["status"] = (
        "matched"
        if not deltas
        else ("blocked" if blocked else ("drift-detected" if approval else "intentional-upgrade"))
    )
    return payload


def _classify_managed_node_template_upgrade_deltas(comparison: Mapping[str, Any]) -> dict[str, Any]:
    return _classify_intentional_deltas(
        comparison,
        predicate=_is_managed_node_template_upgrade_delta,
        remediation="Expected managed MK8s node-template replacement of Kubernetes node instances.",
    )


def _classify_intentional_deltas(
    comparison: Mapping[str, Any],
    *,
    predicate: Callable[[Mapping[str, Any]], bool],
    remediation: str,
) -> dict[str, Any]:
    payload = dict(to_plain_data(comparison))
    deltas: list[dict[str, Any]] = []
    for item in payload.get("deltas", []) or []:
        if not isinstance(item, Mapping):
            continue
        delta = dict(item)
        if predicate(delta):
            delta["classification"] = "intentional_upgrade"
            delta["approval_required"] = False
            delta["remediation"] = remediation
        deltas.append(delta)
    blocked = [delta for delta in deltas if delta.get("classification") == "blocked"]
    approval = [delta for delta in deltas if bool(delta.get("approval_required"))]
    payload["deltas"] = deltas
    payload["blocked_count"] = len(blocked)
    payload["approval_required_count"] = len(approval)
    payload["status"] = (
        "matched"
        if not deltas
        else ("blocked" if blocked else ("drift-detected" if approval else "intentional-upgrade"))
    )
    return payload


def _is_external_source_flux_cleanup_delta(delta: Mapping[str, Any]) -> bool:
    kind = str(delta.get("kind", "") or "")
    resource = str(delta.get("resource", "") or "")
    field = str(delta.get("field", "") or "")
    before = str(delta.get("before", "") or "")
    after = str(delta.get("after", "") or "")
    if kind == "flux.flux_system_helmreleases" and field == "presence":
        name = resource.rsplit("/", 1)[-1]
        return (
            before == "present"
            and after == "absent"
            and (name == "soperator-fluxcd" or name.startswith("flux-system-soperator-fluxcd"))
        )
    if kind == "flux.flux_system_kustomizations":
        name = resource.rsplit("/", 1)[-1]
        if name not in {"soperator-fluxcd", "flux-system"}:
            return False
        if field == "suspend":
            return before.lower() in {"false", "none", ""} and after.lower() == "true"
        if field == "spec_hash":
            return True
    return False


def _is_external_intentional_migration_delta(
    delta: Mapping[str, Any],
    *,
    target_ref: str,
) -> bool:
    if _is_external_source_flux_cleanup_delta(delta):
        return True
    kind = str(delta.get("kind", "") or "")
    resource = str(delta.get("resource", "") or "")
    field = str(delta.get("field", "") or "")
    before = str(delta.get("before", "") or "")
    after = str(delta.get("after", "") or "")
    name = resource.rsplit("/", 1)[-1]
    if kind == "nodes" and field == "presence":
        return (before, after) in {("present", "absent"), ("absent", "present")}
    if kind == "slurm_runtime":
        return field in {"home_mount", "slurm_config", "slurm_nodes"}
    if kind in {"configmaps", "secrets", "nodesets", "slurmclusters"}:
        return field == "presence" and _is_external_migration_owned_resource_name(
            name,
            target_ref=target_ref,
        )
    if kind in {"workloads.deployments", "workloads.daemonsets", "workloads.statefulsets"}:
        if (
            kind == "workloads.statefulsets"
            and name == "soperator-acct-db"
            and field == "presence"
            and before == "present"
            and after == "absent"
        ):
            return True
        return (
            name in _EXTERNAL_MIGRATION_WORKLOAD_NAMES or name.startswith(f"{target_ref}-")
        ) and field in {
            "presence",
            "labels",
            "annotation_sha256_by_key",
            "template_hash",
            "replicas",
        }
    return False


def _is_external_migration_owned_resource_name(name: str, *, target_ref: str) -> bool:
    if target_ref and (name == target_ref or name.startswith(f"{target_ref}-")):
        return True
    prefixes = (
        "sh.helm.release.v1.soperator.",
        "soperator-",
        "helm-soperator",
        "mariadb-",
        "sbatch-script-",
    )
    return name in _EXTERNAL_MIGRATION_OWNED_RESOURCE_NAMES or name.startswith(prefixes)


_EXTERNAL_MIGRATION_OWNED_RESOURCE_NAMES = frozenset(
    {
        "accounting",
        "accounting-mount",
        "controller-placeholder",
        "controller-spool-mount",
        "jail-mount",
        "rest",
        "sconfigcontroller",
        "soperator",
        "topology-node-labels",
        "webhook-server-cert",
        "worker",
    }
)


_EXTERNAL_MIGRATION_WORKLOAD_NAMES = frozenset(
    {
        "accounting",
        "accounting-mount",
        "controller-placeholder",
        "controller-spool-mount",
        "jail-mount",
        "rest",
        "sconfigcontroller",
        "soperator-manager",
        "soperator-mariadb-operator",
        "soperator-mariadb-operator-webhook",
    }
)


def _is_managed_node_template_upgrade_delta(delta: Mapping[str, Any]) -> bool:
    kind = str(delta.get("kind", "") or "")
    field = str(delta.get("field", "") or "")
    before = str(delta.get("before", "") or "")
    after = str(delta.get("after", "") or "")
    return (
        kind == "nodes"
        and field == "presence"
        and (before, after) in {("present", "absent"), ("absent", "present")}
    )


def _delta_resource_key(delta: Mapping[str, Any]) -> tuple[str, str]:
    return str(delta.get("kind", "") or ""), str(delta.get("resource", "") or "")


def _is_managed_chart_metadata_delta(delta: Mapping[str, Any]) -> bool:
    kind = str(delta.get("kind", "") or "")
    resource = str(delta.get("resource", "") or "")
    field = str(delta.get("field", "") or "")
    name = resource.rsplit("/", 1)[-1]
    if kind == "configmaps":
        return field in {"labels", "annotation_keys", "annotation_sha256_by_key"}
    if kind in {"nodesets", "slurmclusters"}:
        return field in {"labels", "annotation_keys", "annotation_sha256_by_key"}
    if kind in {"workloads.deployments", "workloads.daemonsets", "workloads.statefulsets"}:
        return name in _MANAGED_CHART_WORKLOAD_NAMES and field in {
            "labels",
            "annotation_keys",
            "annotation_sha256_by_key",
        }
    return False


def _is_managed_chart_runtime_source_delta(
    delta: Mapping[str, Any],
    chart_touched_resources: set[tuple[str, str]],
) -> bool:
    kind = str(delta.get("kind", "") or "")
    return (
        kind in {"nodesets", "slurmclusters"}
        and _delta_resource_key(delta) in chart_touched_resources
    )


_MANAGED_CHART_WORKLOAD_NAMES = frozenset(
    {
        "accounting",
        "controller-placeholder",
        "rest",
        "sconfigcontroller",
        "soperator-manager",
        "soperator-mariadb-operator",
        "soperator-mariadb-operator-webhook",
    }
)


def _is_managed_chart_upgrade_delta(
    delta: Mapping[str, Any],
    *,
    chart_touched_resources: set[tuple[str, str]],
    managed_runtime_touched: bool,
) -> bool:
    kind = str(delta.get("kind", "") or "")
    resource = str(delta.get("resource", "") or "")
    field = str(delta.get("field", "") or "")
    name = resource.rsplit("/", 1)[-1]
    if _is_managed_chart_metadata_delta(delta):
        return True
    if kind in {"nodesets", "slurmclusters"}:
        return _delta_resource_key(delta) in chart_touched_resources and field in {
            "spec_hash",
            "spec_hash_without_controller_open_metrics_enabled",
            "spec_keys",
        }
    if kind == "slurm_runtime":
        return field in {"slurm_config", "slurm_nodes"}
    if kind in {"workloads.deployments", "workloads.daemonsets", "workloads.statefulsets"}:
        return (
            _delta_resource_key(delta) in chart_touched_resources
            and name in _MANAGED_CHART_WORKLOAD_NAMES
            and field == "template_hash"
        )
    return False


def build_remediation_approval_plan(
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    deltas = [
        dict(item) for item in comparison.get("deltas", []) or [] if isinstance(item, Mapping)
    ]
    blocked = [item for item in deltas if item.get("classification") == "blocked"]
    remediation = [
        item
        for item in deltas
        if item.get("classification") == "remediation_required"
        or bool(item.get("approval_required"))
    ]
    return {
        "schema": SOPERATOR_UPGRADE_SAFETY_SCHEMA,
        "status": "blocked"
        if blocked
        else ("approval-required" if remediation else "not-required"),
        "blocked": blocked,
        "remediation_required": remediation,
        "requires_approval": bool(remediation),
    }


def _external_open_metrics_handoff_check(
    *,
    after_state: ProtectedCustomerState,
    target_ref: str,
    namespace: str,
    handoff: Mapping[str, Any] | None,
    required: bool,
) -> tuple[dict[str, Any], bool]:
    proof = handoff if isinstance(handoff, Mapping) else {}
    if not proof and not required:
        return (
            {
                "name": "external-open-metrics-handoff",
                "status": "skipped",
                "summary": "No external Jail Upgrade OpenMetrics handoff was recorded.",
            },
            False,
        )
    desired_enabled = proof.get("desired_enabled")
    observed_enabled = proof.get("observed_enabled")
    checkpointed_uid = str(proof.get("slurmcluster_uid", "") or "").strip()
    restored_at = str(proof.get("restored_at", "") or "").strip()
    readiness_verified_at = str(proof.get("post_restore_readiness_verified_at", "") or "").strip()
    expected_resource = f"{namespace}/{target_ref}"
    live_resource = _items_by_name(after_state.sections.get("slurmclusters")).get(expected_resource)
    failures: list[str] = []
    if not proof:
        failures.append("handoff proof is missing")
    if proof.get("revision") != EXTERNAL_JAIL_OPEN_METRICS_HANDOFF_REVISION:
        failures.append("handoff revision is not current")
    if str(proof.get("status", "") or "").strip() != "restored":
        failures.append("handoff status is not restored")
    if not isinstance(desired_enabled, bool):
        failures.append("desired value is not boolean")
    if not isinstance(observed_enabled, bool) or observed_enabled != desired_enabled:
        failures.append("checkpoint observed value does not match desired")
    if not checkpointed_uid:
        failures.append("checkpoint SlurmCluster UID is missing")
    if not restored_at:
        failures.append("restoration timestamp is missing")
    if not readiness_verified_at:
        failures.append("post-restore readiness timestamp is missing")
    if live_resource is None:
        failures.append(f"live SlurmCluster {expected_resource} is missing")
    else:
        live_uid = str(live_resource.get("resource_uid", "") or "").strip()
        live_enabled = live_resource.get("controller_open_metrics_enabled")
        if not live_uid or live_uid != checkpointed_uid:
            failures.append("live SlurmCluster UID does not match the checkpoint")
        if not isinstance(live_enabled, bool) or live_enabled != desired_enabled:
            failures.append("live OpenMetrics value does not match restored desired state")
    if failures:
        return (
            {
                "name": "external-open-metrics-handoff",
                "status": "failed",
                "summary": "External Jail Upgrade OpenMetrics proof failed: "
                + "; ".join(failures)
                + ".",
            },
            False,
        )
    return (
        {
            "name": "external-open-metrics-handoff",
            "status": "passed",
            "summary": (
                f"OpenMetrics restored value and SlurmCluster identity match {expected_resource}."
            ),
        },
        True,
    )


def _external_open_metrics_comparison_hashes_match(
    *,
    before_state: ProtectedCustomerState | None,
    after_state: ProtectedCustomerState,
    target_ref: str,
    namespace: str,
) -> bool:
    if before_state is None:
        return False
    resource = f"{namespace}/{target_ref}"
    before_item = _items_by_name(before_state.sections.get("slurmclusters")).get(resource)
    after_item = _items_by_name(after_state.sections.get("slurmclusters")).get(resource)
    if before_item is None or after_item is None:
        return False
    field = "spec_hash_without_controller_open_metrics_enabled"
    before_hash = str(before_item.get(field, "") or "").strip()
    after_hash = str(after_item.get(field, "") or "").strip()
    return bool(before_hash and after_hash and before_hash == after_hash)


def _external_open_metrics_value_changed(
    *,
    before_state: ProtectedCustomerState | None,
    after_state: ProtectedCustomerState,
    target_ref: str,
    namespace: str,
) -> bool:
    if before_state is None:
        return False
    resource = f"{namespace}/{target_ref}"
    before_item = _items_by_name(before_state.sections.get("slurmclusters")).get(resource)
    after_item = _items_by_name(after_state.sections.get("slurmclusters")).get(resource)
    if before_item is None or after_item is None:
        return False
    # Baselines captured before the split OpenMetrics fields existed must keep
    # their original whole-spec drift semantics.  A recorded handoff is still
    # checked independently, but the mere appearance of the new field must not
    # create a non-waivable proof requirement for older non-handoff upgrades.
    comparison_field = "spec_hash_without_controller_open_metrics_enabled"
    if comparison_field not in before_item:
        return False
    field = "controller_open_metrics_enabled"
    return _stable_hash(before_item.get(field)) != _stable_hash(after_item.get(field))


def run_post_upgrade_fast_verification(
    *,
    command_runner: SafetyCommandRunner,
    target_ref: str,
    namespace: str = "soperator",
    kube_context: str | None = None,
    before_state: ProtectedCustomerState | None = None,
    source_payload: Mapping[str, Any] | None = None,
    terraform_plan_command: Sequence[str] | None = None,
    external_cluster: bool = False,
    external_open_metrics_handoff: Mapping[str, Any] | None = None,
    managed_chart_upgrade: bool = False,
    managed_node_template_upgrade: bool = False,
    remediation_approved: bool = False,
    timeout_seconds: int = 120,
) -> PostUpgradeVerificationResult:
    after_state = capture_protected_customer_state(
        command_runner=command_runner,
        target_ref=target_ref,
        namespace=namespace,
        kube_context=kube_context,
        source_payload=source_payload,
        timeout_seconds=timeout_seconds,
    )
    checks: list[dict[str, Any]] = [
        _pod_phase_check(after_state),
        _pvc_check(before_state, after_state),
        _home_mount_check(before_state, after_state),
        _activechecks_check(before_state, after_state),
        _observability_check(after_state),
    ]
    open_metrics_handoff_verified = False
    open_metrics_comparison_hashes_match = False
    if external_cluster:
        open_metrics_handoff_required = _external_open_metrics_value_changed(
            before_state=before_state,
            after_state=after_state,
            target_ref=target_ref,
            namespace=namespace,
        )
        open_metrics_check, open_metrics_handoff_verified = _external_open_metrics_handoff_check(
            after_state=after_state,
            target_ref=target_ref,
            namespace=namespace,
            handoff=external_open_metrics_handoff,
            required=open_metrics_handoff_required,
        )
        checks.append(open_metrics_check)
        open_metrics_comparison_hashes_match = _external_open_metrics_comparison_hashes_match(
            before_state=before_state,
            after_state=after_state,
            target_ref=target_ref,
            namespace=namespace,
        )
    comparison: dict[str, Any] = {
        "schema": SOPERATOR_UPGRADE_SAFETY_SCHEMA,
        "status": "not-run",
        "before_hash": None,
        "after_hash": after_state.content_hash,
        "deltas": [],
        "blocked_count": 0,
        "approval_required_count": 0,
    }
    if before_state is not None:
        comparison = compare_protected_customer_state(before=before_state, after=after_state)
        if external_cluster:
            comparison = _classify_external_intentional_deltas(
                comparison,
                target_ref=target_ref,
                namespace=namespace,
                after_state=after_state,
                open_metrics_handoff=external_open_metrics_handoff,
                open_metrics_handoff_verified=open_metrics_handoff_verified,
                open_metrics_comparison_hashes_match=(open_metrics_comparison_hashes_match),
            )
        else:
            if managed_chart_upgrade:
                comparison = _classify_managed_chart_upgrade_deltas(comparison)
            if managed_node_template_upgrade:
                comparison = _classify_managed_node_template_upgrade_deltas(comparison)
        checks.append(
            _protected_comparison_check(
                comparison,
                remediation_approved=remediation_approved,
            )
        )
    else:
        checks.append(
            {
                "name": "protected-state-comparison",
                "status": "skipped",
                "summary": "No pre-upgrade protected-state baseline was available.",
            }
        )
    zero_downtime = _zero_downtime_eligibility(
        after_state=after_state,
        comparison=comparison,
    )
    checks.append(
        {
            "name": "zero-downtime-eligibility",
            "status": "passed" if zero_downtime.get("eligible") is True else "failed",
            "summary": str(zero_downtime.get("summary", "") or ""),
        }
    )
    failed = [check for check in checks if check.get("status") == "failed"]
    status = "failed" if failed else "passed"
    followups = _heavy_validation_followups(
        terraform_plan_command=terraform_plan_command,
        external_cluster=external_cluster,
    )
    return PostUpgradeVerificationResult(
        status=status,
        passed=not failed,
        checks=tuple(checks),
        protected_state=after_state,
        comparison=comparison,
        command_audit=after_state.command_audit,
        heavy_validation_followups=tuple(followups),
        zero_downtime_eligibility=zero_downtime,
    )


def stage_fast_verification_check(name: str, status: str, summary: str) -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
    }


def stage_fast_verification_failed(checks: Sequence[Mapping[str, Any]]) -> bool:
    return any(str(check.get("status", "") or "") == "failed" for check in checks)


def stage_fast_verification_status(checks: Sequence[Mapping[str, Any]]) -> str:
    if stage_fast_verification_failed(checks):
        return "failed"
    statuses = [
        str(check.get("status", "") or "").strip().lower().replace("_", "-")
        for check in checks
        if str(check.get("status", "") or "").strip()
    ]
    if statuses and all(status in {"skipped", "skip"} for status in statuses):
        return "skipped"
    return "passed"


def build_stage_fast_verification_payload(
    *,
    phase_id: str,
    status: str,
    summary: str,
    checks: Sequence[Mapping[str, Any]],
    verified_at: str | None = None,
) -> dict[str, Any]:
    passed = True if status == "passed" else False if status == "failed" else None
    return {
        "phase_id": phase_id,
        "status": status,
        "passed": passed,
        "summary": summary,
        "verified_at": verified_at or _utc_now(),
        "checks": [to_plain_data(dict(check)) for check in checks],
    }


def stage_fast_verification_report(
    phase_id: str,
    phase: Mapping[str, Any],
) -> dict[str, Any]:
    verification = phase.get("fast_verification")
    verification_map = verification if isinstance(verification, Mapping) else {}
    if verification_map:
        plain = to_plain_data(verification_map)
        payload = dict(plain) if isinstance(plain, Mapping) else {}
        payload.setdefault("phase_id", phase_id)
        payload.setdefault("status", "not_run")
        payload.setdefault("passed", None)
        payload.setdefault("summary", "No fast verification summary recorded.")
        payload.setdefault("checks", [])
        return payload
    return {
        "phase_id": phase_id,
        "status": "not_run",
        "passed": None,
        "summary": "No fast stage verification recorded.",
        "checks": [],
    }


def stage_fast_verification_markdown_lines(
    verifications: Sequence[Mapping[str, Any]],
    *,
    empty_message: str = "No executable upgrade stages were selected.",
) -> list[str]:
    lines = ["## Stage Fast Verification", ""]
    if verifications:
        for verification in verifications:
            phase_id = str(verification.get("phase_id", "") or "unknown")
            status = str(verification.get("status", "") or "not_run")
            summary = str(verification.get("summary", "") or "No summary recorded.")
            lines.append(f"- `{phase_id}`: `{_status_label(status)}` - {summary}")
    else:
        lines.append(f"- {empty_message}")
    lines.append("")
    return lines


def upgrade_safety_checkpoint_payload() -> dict[str, Any]:
    return {
        "schema": SOPERATOR_UPGRADE_SAFETY_SCHEMA,
        "protected_customer_state": {
            "before_hash": None,
            "after_hash": None,
            "deltas": [],
            "status": "not-run",
        },
        "remediation_approvals": [],
        "post_upgrade_verification": {
            "status": "not-run",
            "passed": False,
            "checks": [],
        },
        "fast_smoke": {
            "status": "not-run",
            "checks": [],
        },
        "heavy_validation_followups": [],
        "backup": {
            "status": "not-recorded",
        },
        "zero_downtime_eligibility": {
            "status": "not-evaluated",
            "eligible": False,
            "reasons": [],
        },
    }


def update_safety_payload_with_before(
    safety_payload: Mapping[str, Any] | None,
    before_state: ProtectedCustomerState,
    *,
    backup: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _safety_payload_copy(safety_payload)
    protected = dict(payload.get("protected_customer_state") or {})
    protected["before"] = before_state.as_payload()
    protected["before_hash"] = before_state.content_hash
    protected["status"] = "captured" if before_state.complete else "incomplete"
    payload["protected_customer_state"] = protected
    payload["backup"] = {
        "status": "available" if backup else "not-recorded",
        **(dict(backup) if isinstance(backup, Mapping) else {}),
    }
    return payload


def update_safety_payload_with_verification(
    safety_payload: Mapping[str, Any] | None,
    result: PostUpgradeVerificationResult,
    *,
    remediation_approved: bool = False,
) -> dict[str, Any]:
    payload = _safety_payload_copy(safety_payload)
    comparison = dict(result.comparison)
    protected = dict(payload.get("protected_customer_state") or {})
    protected["after"] = result.protected_state.as_payload()
    protected["after_hash"] = result.protected_state.content_hash
    protected["deltas"] = list(comparison.get("deltas", []) or [])
    protected["status"] = comparison.get("status") or result.status
    payload["protected_customer_state"] = protected
    payload["post_upgrade_verification"] = result.as_payload()
    payload["fast_smoke"] = {
        "status": result.status,
        "passed": result.passed,
        "checks": [to_plain_data(item) for item in result.checks],
    }
    payload["heavy_validation_followups"] = [
        to_plain_data(item) for item in result.heavy_validation_followups
    ]
    payload["zero_downtime_eligibility"] = to_plain_data(result.zero_downtime_eligibility)
    approval_plan = build_remediation_approval_plan(comparison)
    remediation_items = approval_plan.get("remediation_required", [])
    if isinstance(remediation_items, list):
        payload["remediation_approvals"] = [
            {
                **dict(item),
                "approved": remediation_approved,
            }
            for item in remediation_items
            if isinstance(item, Mapping)
        ]
    else:
        payload["remediation_approvals"] = []
    payload["remediation_approval"] = {
        "approved": remediation_approved,
        "required": bool(approval_plan.get("requires_approval")),
        "status": "approved"
        if remediation_approved and approval_plan.get("requires_approval")
        else str(approval_plan.get("status") or "not-required"),
    }
    return payload


def safety_report_markdown_lines(safety_payload: Mapping[str, Any] | None) -> list[str]:
    safety = _safety_payload_copy(safety_payload)
    protected = safety.get("protected_customer_state")
    protected_map = protected if isinstance(protected, Mapping) else {}
    verification = safety.get("post_upgrade_verification")
    verification_map = verification if isinstance(verification, Mapping) else {}
    checks = verification_map.get("checks") or safety.get("fast_smoke", {}).get("checks", [])
    lines = [
        "## Shared Upgrade Safety",
        "",
        f"- Before hash: `{protected_map.get('before_hash') or 'not-captured'}`",
        f"- After hash: `{protected_map.get('after_hash') or 'not-captured'}`",
        f"- Protected-state result: `{protected_map.get('status') or 'not-run'}`",
        f"- Fast verification: `{verification_map.get('status') or 'not-run'}`",
        "",
        "### Fast Checks",
        "",
    ]
    if isinstance(checks, Sequence) and not isinstance(checks, (str, bytes, bytearray)) and checks:
        for item in checks:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                f"- `{item.get('status') or 'not_run'}` {item.get('name') or 'check'}: "
                f"{item.get('summary') or 'No summary recorded.'}"
            )
    else:
        lines.append("- No fast checks recorded.")
    lines.extend(["", "### Remediation Deltas", ""])
    deltas = protected_map.get("deltas")
    if isinstance(deltas, list) and deltas:
        for item in deltas[:25]:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "- `"
                + str(item.get("classification") or "unknown")
                + "` "
                + str(item.get("resource") or "resource")
                + "."
                + str(item.get("field") or "field")
                + ": "
                + str(item.get("remediation") or "Review before accepting the new state.")
            )
        if len(deltas) > 25:
            lines.append(f"- {len(deltas) - 25} additional delta(s) are in the JSON report.")
    else:
        lines.append("- No protected-state deltas recorded.")
    lines.extend(["", "### Heavy Follow-Ups", ""])
    followups = safety.get("heavy_validation_followups")
    if isinstance(followups, list) and followups:
        for item in followups:
            if not isinstance(item, Mapping):
                continue
            command = item.get("command")
            suffix = f" Command: `{command}`" if command else ""
            lines.append(
                f"- `{item.get('status') or 'manual'}` {item.get('name') or 'follow-up'}: "
                f"{item.get('summary') or 'No summary recorded.'}{suffix}"
            )
    else:
        lines.append("- No heavy follow-up checks recorded.")
    return lines


def _status_label(status: str) -> str:
    normalized = status.strip().lower().replace("_", "-")
    if normalized in {"passed", "pass"}:
        return "PASS"
    if normalized in {"failed", "fail", "blocked"}:
        return "FAIL"
    if normalized in {"skipped", "skip"}:
        return "SKIP"
    if normalized in {"not-run", "not_run", "pending"}:
        return "PENDING"
    return status.upper() if status else "UNKNOWN"


def _safety_payload_copy(safety_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(safety_payload, Mapping):
        payload = dict(to_plain_data(safety_payload))
        payload.setdefault("schema", SOPERATOR_UPGRADE_SAFETY_SCHEMA)
        return payload
    return upgrade_safety_checkpoint_payload()


def _kubectl_args(
    args: Sequence[str],
    *,
    namespace: str | None,
    kube_context: str | None,
) -> tuple[str, ...]:
    command: list[str] = ["kubectl"]
    context = str(kube_context or "").strip()
    if context:
        command.extend(["--context", context])
    if namespace:
        command.extend(["-n", namespace])
    command.extend(str(arg) for arg in args)
    return tuple(command)


def _run_readonly(
    command_runner: SafetyCommandRunner,
    args: Sequence[str],
    *,
    timeout_seconds: int,
    input_text: str | None = None,
) -> SafetyCommandResult:
    if _is_mutating_command(args):
        raise RuntimeError(
            f"Refusing mutating command during Soperator safety verification: {shlex.join(args)}"
        )
    return command_runner(
        args,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        check=False,
    )


def _is_mutating_command(args: Sequence[str]) -> bool:
    tokens = tuple(str(item) for item in args)
    if not tokens:
        return False
    binary = tokens[0]
    if binary != "kubectl":
        return False
    verb = ""
    skip_next = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in {"--context", "-n", "--namespace", "--kubeconfig"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        verb = token
        break
    if verb == "rollout":
        subcommands = [token for token in tokens if not token.startswith("-")]
        return "restart" in subcommands or "undo" in subcommands
    return bool(verb and verb not in _READONLY_KUBECTL_VERBS and verb in _MUTATING_KUBECTL_VERBS)


def _command_audit(result: SafetyCommandResult, *, section: str) -> dict[str, Any]:
    return {
        "section": section,
        "command": shlex.join(str(arg) for arg in result.args),
        "returncode": int(result.returncode),
        "read_only": True,
        "stdout_sha256": _sha256_text(result.stdout),
        "stderr_summary": _truncate(result.stderr.strip(), 300),
    }


def _command_failure_summary(section: str, result: SafetyCommandResult) -> str:
    detail = _truncate((result.stderr or result.stdout or "").strip(), 300)
    return f"{section} capture failed with exit code {result.returncode}" + (
        f": {detail}" if detail else ""
    )


def _json_object(text: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _sanitize_resource_list(payload: Mapping[str, Any]) -> dict[str, Any]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        items = []
    normalized = [_resource_identity(item) for item in items if isinstance(item, Mapping)]
    return {
        "available": True,
        "count": len(normalized),
        "items": sorted(normalized, key=lambda item: str(item.get("name", ""))),
    }


def _metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _resource_identity(item: Mapping[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    metadata = _metadata(item)
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), Mapping) else {}
    annotations = (
        metadata.get("annotations") if isinstance(metadata.get("annotations"), Mapping) else {}
    )
    return {
        "kind": kind or str(item.get("kind", "") or ""),
        "namespace": str(metadata.get("namespace", "") or ""),
        "name": str(metadata.get("name", "") or ""),
        "labels": dict(sorted((str(k), str(v)) for k, v in labels.items())),
        "annotation_keys": sorted(str(key) for key in annotations),
        "annotation_sha256_by_key": {
            str(key): _safe_annotation_hash(str(key), value)
            for key, value in sorted(annotations.items())
        },
    }


def _sanitize_pods(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        metadata = _metadata(item)
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        owner_refs = metadata.get("ownerReferences")
        containers = status.get("containerStatuses")
        init_containers = status.get("initContainerStatuses")
        items.append(
            {
                **_resource_identity(item, kind="Pod"),
                "phase": str(status.get("phase", "") or ""),
                "owners": _owner_refs(owner_refs),
                "node_name": str(spec.get("nodeName", "") or ""),
                "restart_count": _restart_count(containers) + _restart_count(init_containers),
                "waiting_reasons": sorted(
                    {
                        reason
                        for reason in (
                            *_waiting_reasons(containers),
                            *_waiting_reasons(init_containers),
                        )
                        if reason
                    }
                ),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_pvcs(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        resources = spec.get("resources") if isinstance(spec.get("resources"), Mapping) else {}
        requests = (
            resources.get("requests") if isinstance(resources.get("requests"), Mapping) else {}
        )
        capacity = status.get("capacity") if isinstance(status.get("capacity"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind="PersistentVolumeClaim"),
                "phase": str(status.get("phase", "") or ""),
                "volume_name": str(spec.get("volumeName", "") or ""),
                "storage_class": str(spec.get("storageClassName", "") or ""),
                "access_modes": sorted(str(mode) for mode in spec.get("accessModes", []) or []),
                "request_storage": str(requests.get("storage", "") or ""),
                "capacity_storage": str(capacity.get("storage", "") or ""),
                "selector_hash": _stable_hash(spec.get("selector", {})),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_pvs(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        capacity = spec.get("capacity") if isinstance(spec.get("capacity"), Mapping) else {}
        claim = spec.get("claimRef") if isinstance(spec.get("claimRef"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind="PersistentVolume"),
                "phase": str(status.get("phase", "") or ""),
                "claim": {
                    "namespace": str(claim.get("namespace", "") or ""),
                    "name": str(claim.get("name", "") or ""),
                },
                "storage_class": str(spec.get("storageClassName", "") or ""),
                "capacity_storage": str(capacity.get("storage", "") or ""),
                "persistent_volume_reclaim_policy": str(
                    spec.get("persistentVolumeReclaimPolicy", "") or ""
                ),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_configmaps(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
        binary_data = item.get("binaryData") if isinstance(item.get("binaryData"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind="ConfigMap"),
                "data_keys": sorted(str(key) for key in data),
                "binary_data_keys": sorted(str(key) for key in binary_data),
                "data_sha256_by_key": {
                    str(key): _sha256_text(str(value)) for key, value in sorted(data.items())
                },
                "binary_data_sha256_by_key": {
                    str(key): _sha256_text(str(value)) for key, value in sorted(binary_data.items())
                },
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_secrets(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
        string_data = item.get("stringData") if isinstance(item.get("stringData"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind="Secret"),
                "type": str(item.get("type", "") or ""),
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
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_workloads(payload: Mapping[str, Any], *, workload_kind: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        template = spec.get("template") if isinstance(spec.get("template"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind=workload_kind),
                "replicas": spec.get("replicas"),
                "ready_replicas": status.get("readyReplicas"),
                "available_replicas": status.get("availableReplicas"),
                "template_hash": _stable_hash(template),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _slurmcluster_protected_spec(
    spec: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Any]:
    protected = to_plain_data(spec)
    if not isinstance(protected, dict):
        protected = dict(spec)
    slurm_nodes = protected.get("slurmNodes")
    if not isinstance(slurm_nodes, dict):
        return protected, None
    controller = slurm_nodes.get("controller")
    if not isinstance(controller, dict):
        return protected, None
    open_metrics = controller.get("openMetrics")
    if not isinstance(open_metrics, dict):
        return protected, None
    enabled = open_metrics.pop("enabled", None)
    if not open_metrics:
        controller.pop("openMetrics", None)
    if not controller:
        slurm_nodes.pop("controller", None)
    if not slurm_nodes:
        protected.pop("slurmNodes", None)
    return protected, enabled


def _sanitize_custom_resources(payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        comparison_spec: Mapping[str, Any] = spec
        open_metrics_enabled: Any = None
        if kind == "SlurmCluster":
            comparison_spec, open_metrics_enabled = _slurmcluster_protected_spec(spec)
        items.append(
            {
                **_resource_identity(item, kind=kind),
                **(
                    {"resource_uid": str(_metadata(item).get("uid", "") or "")}
                    if kind == "SlurmCluster"
                    else {}
                ),
                "spec_keys": sorted(str(key) for key in spec),
                "spec_hash": _stable_hash(_redact_secrets(spec)),
                **(
                    {
                        "controller_open_metrics_enabled": open_metrics_enabled,
                        "spec_hash_without_controller_open_metrics_enabled": _stable_hash(
                            _redact_secrets(comparison_spec)
                        ),
                    }
                    if kind == "SlurmCluster"
                    else {}
                ),
                "status_phase": str(status.get("phase", "") or status.get("state", "") or ""),
                "conditions": _conditions(status.get("conditions")),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_activechecks(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind="ActiveCheck"),
                "suspend": bool(spec.get("suspend") or spec.get("suspended")),
                "spec_hash": _stable_hash(spec),
                "status_phase": str(status.get("phase", "") or status.get("state", "") or ""),
                "conditions": _conditions(status.get("conditions")),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_flux_resources(payload: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        items.append(
            {
                **_resource_identity(item, kind=kind),
                "suspend": bool(spec.get("suspend")),
                "ready": _condition_status(status.get("conditions"), "Ready"),
                "spec_hash": _stable_hash(spec),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


def _sanitize_nodes(payload: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        metadata = _metadata(item)
        labels = metadata.get("labels") if isinstance(metadata.get("labels"), Mapping) else {}
        taints = spec.get("taints") if isinstance(spec.get("taints"), list) else []
        items.append(
            {
                "kind": "Node",
                "name": str(metadata.get("name", "") or ""),
                "labels": dict(sorted((str(key), str(value)) for key, value in labels.items())),
                "taints": to_plain_data(taints),
                "unschedulable": bool(spec.get("unschedulable")),
                "ready": _condition_status(status.get("conditions"), "Ready"),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": sorted(items, key=lambda item: str(item.get("name", ""))),
    }


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
        return {
            "available": False,
            "reason": "No running login pod was discovered for Slurm runtime capture.",
        }
    commands = {
        "slurm_config": "scontrol show config",
        "slurm_partitions": "scontrol show partition",
        "slurm_nodes": "scontrol show nodes",
        "accounting_qos": "sacctmgr -nP show qos format=name,priority,grptres,maxjobs,maxsubmit",
        "accounting_associations": "sacctmgr -nP show assoc format=cluster,account,user,partition,qos",
        "home_mount": "findmnt -T /home --json || findmnt /home --json || mount | grep ' on /home ' || true",
    }
    captured: dict[str, Any] = {"available": True, "login_pod": login_pod}
    for key, shell_command in commands.items():
        args = _kubectl_args(
            ("exec", login_pod, "-c", "sshd", "--", "bash", "-lc", shell_command),
            namespace=namespace,
            kube_context=kube_context,
        )
        result = _run_readonly(command_runner, args, timeout_seconds=timeout_seconds)
        audit.append(_command_audit(result, section=f"slurm_runtime.{key}"))
        if result.returncode != 0 and _kubectl_exec_container_missing(result, "sshd"):
            args = _kubectl_args(
                ("exec", login_pod, "--", "bash", "-lc", shell_command),
                namespace=namespace,
                kube_context=kube_context,
            )
            result = _run_readonly(command_runner, args, timeout_seconds=timeout_seconds)
            audit.append(_command_audit(result, section=f"slurm_runtime.{key}"))
        if result.returncode != 0:
            warning = _command_failure_summary(f"slurm_runtime.{key}", result)
            warnings.append(warning)
            captured[key] = {"available": False, "error": warning}
            continue
        captured[key] = {
            "available": True,
            "stdout_sha256": _sha256_text(result.stdout),
            "line_count": len([line for line in result.stdout.splitlines() if line.strip()]),
            "summary": _truncate(_normalize_whitespace(result.stdout), 300),
        }
        if key == "slurm_nodes":
            captured[key]["problem_nodes"] = [
                dict(item) for item in _slurm_problem_nodes_from_scontrol(result.stdout)
            ]
    return captured


def _kubectl_exec_container_missing(result: SafetyCommandResult, container: str) -> bool:
    stderr = str(result.stderr or "").strip().lower()
    normalized_container = container.strip().lower()
    return (
        normalized_container
        and normalized_container in stderr
        and (
            "container not found" in stderr
            or "container is not valid" in stderr
            or "container not found in pod" in stderr
        )
    )


def _slurm_problem_nodes_from_scontrol(output: str) -> tuple[Mapping[str, str], ...]:
    problems: list[dict[str, str]] = []
    current_node = ""
    for raw_line in output.splitlines():
        for token in str(raw_line or "").strip().split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if key == "NodeName":
                current_node = value
                continue
            if key != "State" or not current_node:
                continue
            state = value.strip()
            state_upper = state.upper()
            if (
                "*" in state
                or "NOT_RESPONDING" in state_upper
                or "DOWN" in state_upper
                or "DRAIN" in state_upper
                or "FAIL" in state_upper
            ):
                problems.append({"name": current_node, "state": state})
    return tuple(problems)


def _sanitize_source_payload(payload: Mapping[str, Any], *, target_ref: str) -> dict[str, Any]:
    plain = to_plain_data(payload)
    return {
        "target_ref": target_ref,
        "hash": _stable_hash(_redact_secrets(plain)),
    }


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(
                r"(password|secret|token|private[_-]?key|certificate|credential)", key_text, re.I
            ):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redact_secrets(item)
        return result
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _safe_annotation_hash(key: str, value: Any) -> str:
    key_text = str(key)
    if key_text == "kubectl.kubernetes.io/last-applied-configuration" or re.search(
        r"(password|secret|token|private[_-]?key|certificate|credential)",
        key_text,
        re.I,
    ):
        return _sha256_text("<redacted>")
    return _sha256_text(str(value))


def _section_deltas(section: str, before: Any, after: Any) -> list[ProtectedStateDelta]:
    if section == "workloads":
        return _nested_named_deltas(section, before, after)
    if section == "flux":
        return _nested_named_deltas(section, before, after)
    if section == "slurm_runtime":
        return _slurm_runtime_deltas(before, after)
    return _named_resource_deltas(section, before, after)


def _nested_named_deltas(section: str, before: Any, after: Any) -> list[ProtectedStateDelta]:
    before_map = before if isinstance(before, Mapping) else {}
    after_map = after if isinstance(after, Mapping) else {}
    deltas: list[ProtectedStateDelta] = []
    for child in sorted(set(before_map) | set(after_map)):
        deltas.extend(
            _named_resource_deltas(
                f"{section}.{child}", before_map.get(child), after_map.get(child)
            )
        )
    return deltas


def _named_resource_deltas(section: str, before: Any, after: Any) -> list[ProtectedStateDelta]:
    before_items = _items_by_name(before)
    after_items = _items_by_name(after)
    deltas: list[ProtectedStateDelta] = []
    if not before_items and not after_items:
        if _resource_list_without_items(before) and _resource_list_without_items(after):
            return []
        return [
            _delta(
                section=section,
                resource=section,
                field="hash",
                before=before,
                after=after,
            )
        ]
    for name in sorted(set(before_items) | set(after_items)):
        before_item = before_items.get(name)
        after_item = after_items.get(name)
        if before_item is None or after_item is None:
            deltas.append(
                _delta(
                    section=section,
                    resource=name,
                    field="presence",
                    before="present" if before_item is not None else "absent",
                    after="present" if after_item is not None else "absent",
                )
            )
            continue
        for field in sorted(set(before_item) | set(after_item)):
            if field in {"status_phase", "conditions", "ready_replicas", "available_replicas"}:
                continue
            if _stable_hash(before_item.get(field)) == _stable_hash(after_item.get(field)):
                continue
            deltas.append(
                _delta(
                    section=section,
                    resource=name,
                    field=str(field),
                    before=before_item.get(field),
                    after=after_item.get(field),
                )
            )
    return deltas


def _resource_list_without_items(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if "items" not in value and "available" not in value and "count" not in value:
        return False
    items = value.get("items")
    return not isinstance(items, list) or not items


def _slurm_runtime_deltas(before: Any, after: Any) -> list[ProtectedStateDelta]:
    before_map = before if isinstance(before, Mapping) else {}
    after_map = after if isinstance(after, Mapping) else {}
    fields = (
        "slurm_config",
        "slurm_partitions",
        "slurm_nodes",
        "accounting_qos",
        "accounting_associations",
        "home_mount",
    )
    deltas: list[ProtectedStateDelta] = []
    for field in fields:
        before_hash = _mapping_hash_value(before_map.get(field))
        after_hash = _mapping_hash_value(after_map.get(field))
        if before_hash == after_hash:
            continue
        deltas.append(
            _delta(
                section="slurm_runtime",
                resource="slurm-runtime",
                field=field,
                before=before_map.get(field),
                after=after_map.get(field),
            )
        )
    return deltas


def _mapping_hash_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("stdout_sha256") or _stable_hash(value))
    return _stable_hash(value)


def _delta(
    *,
    section: str,
    resource: str,
    field: str,
    before: Any,
    after: Any,
) -> ProtectedStateDelta:
    classification, approval_required, remediation = _classify_delta(
        section, resource, field, before, after
    )
    return ProtectedStateDelta(
        kind=section,
        resource=resource,
        field=field,
        before=before,
        after=after,
        classification=classification,
        approval_required=approval_required,
        remediation=remediation,
    )


def _classify_delta(
    section: str,
    resource: str,
    field: str,
    before: Any,
    after: Any,
) -> tuple[str, bool, str]:
    if section == "source_payload":
        return (
            "intentional_upgrade",
            False,
            "Generated config changed as part of the requested upgrade.",
        )
    if section.endswith("pvcs"):
        if field == "presence" and before == "present" and after == "absent":
            return (
                "blocked",
                False,
                "Protected PVC disappeared after upgrade; restore before continuing.",
            )
        if field in {"request_storage", "capacity_storage"} and _storage_quantity_gib(
            after
        ) < _storage_quantity_gib(before):
            return (
                "blocked",
                False,
                "Protected PVC storage shrank after upgrade; restore before continuing.",
            )
    if section == "slurm_runtime" and field in {
        "accounting_associations",
        "accounting_qos",
        "home_mount",
        "slurm_config",
        "slurm_nodes",
        "slurm_partitions",
    }:
        if (
            isinstance(before, Mapping)
            and before.get("available") is False
            and isinstance(after, Mapping)
            and after.get("available") is True
        ):
            return (
                "preserve",
                False,
                "Pre-upgrade Slurm runtime evidence was unavailable; post-upgrade capture succeeded, "
                "so no comparable baseline exists.",
            )
        return "remediation_required", True, "Review and approve this Slurm protected-state drift."
    if section in {"configmaps", "secrets", "nodesets", "slurmclusters"} or section.startswith(
        "flux"
    ):
        return (
            "remediation_required",
            True,
            "Review and approve this protected Kubernetes resource drift.",
        )
    if section.startswith("workloads"):
        return "remediation_required", True, "Review and approve this workload template drift."
    if section == "nodes":
        return "remediation_required", True, "Review node label, taint, or schedulability drift."
    return "preserve", False, "Recorded for audit; no approval required."


def _items_by_name(section: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(section, Mapping):
        return {}
    items = section.get("items")
    if not isinstance(items, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = _resource_name(item)
        if name:
            result[name] = item
    return result


def _resource_name(item: Mapping[str, Any]) -> str:
    namespace = str(item.get("namespace", "") or "")
    name = str(item.get("name", "") or "")
    return f"{namespace}/{name}" if namespace else name


def _pod_phase_check(state: ProtectedCustomerState) -> dict[str, Any]:
    pods = state.sections.get("pods")
    items = _items_by_name(pods)
    if not items:
        return {
            "name": "pods-running-or-completed",
            "status": "failed",
            "summary": "No Soperator namespace pods were discovered.",
        }
    failed: list[str] = []
    high_restarts: list[str] = []
    for name, pod in items.items():
        phase = str(pod.get("phase", "") or "").lower()
        reasons = {str(reason) for reason in pod.get("waiting_reasons", []) or []}
        if (
            phase in _UNAVAILABLE_STATUSES
            or phase not in {"running", "succeeded"}
            or reasons & _BAD_WAITING_REASONS
        ):
            failed.append(name)
        if int(pod.get("restart_count") or 0) >= 10:
            high_restarts.append(name)
    if failed:
        return {
            "name": "pods-running-or-completed",
            "status": "failed",
            "summary": "Pod health failed for: " + ", ".join(failed[:20]),
            "failed_pods": failed,
        }
    if high_restarts:
        return {
            "name": "pods-running-or-completed",
            "status": "failed",
            "summary": "High restart count after upgrade for: " + ", ".join(high_restarts[:20]),
            "failed_pods": high_restarts,
        }
    return {
        "name": "pods-running-or-completed",
        "status": "passed",
        "summary": f"{len(items)} pod(s) are Running or Succeeded.",
    }


def _pvc_check(
    before_state: ProtectedCustomerState | None,
    after_state: ProtectedCustomerState,
) -> dict[str, Any]:
    before_pvcs = _items_by_name(before_state.sections.get("pvcs")) if before_state else {}
    after_pvcs = _items_by_name(after_state.sections.get("pvcs"))
    expected_names = _protected_pvc_names(before_pvcs) or _protected_pvc_names(after_pvcs)
    if not expected_names:
        return {
            "name": "protected-pvcs-restored",
            "status": "skipped",
            "summary": "No jail/controller/accounting PVC baseline was discovered.",
        }
    missing: list[str] = []
    bad_phase: list[str] = []
    reduced: list[str] = []
    for name in expected_names:
        after = after_pvcs.get(name)
        if after is None:
            missing.append(name)
            continue
        if str(after.get("phase", "") or "") != "Bound":
            bad_phase.append(name)
        before = before_pvcs.get(name)
        if before and _storage_quantity_gib(after.get("request_storage")) < _storage_quantity_gib(
            before.get("request_storage")
        ):
            reduced.append(name)
    failures = []
    if missing:
        failures.append("missing: " + ", ".join(missing))
    if bad_phase:
        failures.append("not Bound: " + ", ".join(bad_phase))
    if reduced:
        failures.append("size reduced: " + ", ".join(reduced))
    if failures:
        return {
            "name": "protected-pvcs-restored",
            "status": "failed",
            "summary": "; ".join(failures),
        }
    return {
        "name": "protected-pvcs-restored",
        "status": "passed",
        "summary": f"{len(expected_names)} protected PVC(s) exist, are Bound, and did not shrink.",
    }


def _home_mount_check(
    before_state: ProtectedCustomerState | None,
    after_state: ProtectedCustomerState,
) -> dict[str, Any]:
    after_mount = _home_mount_hash(after_state)
    before_mount = _home_mount_hash(before_state) if before_state else ""
    if not before_mount:
        return {
            "name": "home-mounted",
            "status": "skipped",
            "summary": "No pre-upgrade /home mount evidence was captured.",
        }
    if not after_mount:
        return {
            "name": "home-mounted",
            "status": "failed",
            "summary": "Post-upgrade /home mount evidence is missing.",
        }
    if before_mount != after_mount:
        if _home_mount_is_shared_jail_home(after_state):
            return {
                "name": "home-mounted",
                "status": "passed",
                "summary": "/home is mounted from the expected shared jail home path.",
            }
        return {
            "name": "home-mounted",
            "status": "failed",
            "summary": "Post-upgrade /home mount evidence differs from the baseline.",
        }
    return {
        "name": "home-mounted",
        "status": "passed",
        "summary": "/home mount evidence matches the pre-upgrade baseline.",
    }


def _activechecks_check(
    before_state: ProtectedCustomerState | None,
    after_state: ProtectedCustomerState,
) -> dict[str, Any]:
    before_items = _items_by_name(before_state.sections.get("activechecks")) if before_state else {}
    after_items = _items_by_name(after_state.sections.get("activechecks"))
    if not before_items:
        return {
            "name": "activechecks-restored",
            "status": "skipped",
            "summary": "ActiveChecks were not enabled or not discoverable before upgrade.",
        }
    missing = sorted(name for name in before_items if name not in after_items)
    suspended = sorted(
        name
        for name, item in after_items.items()
        if bool(item.get("suspend")) and not bool(before_items.get(name, {}).get("suspend"))
    )
    failed = sorted(
        name
        for name, item in after_items.items()
        if str(item.get("status_phase", "") or "").lower() in {"failed", "error"}
        or any(
            str(condition.get("status", "") or "").lower() == "false"
            and str(condition.get("type", "") or "").lower() in {"ready", "succeeded", "completed"}
            for condition in item.get("conditions", []) or []
            if isinstance(condition, Mapping)
        )
    )
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if suspended:
        details.append("suspended: " + ", ".join(suspended))
    if failed:
        details.append("failed: " + ", ".join(failed))
    if details:
        return {
            "name": "activechecks-restored",
            "status": "failed",
            "summary": "; ".join(details),
        }
    return {
        "name": "activechecks-restored",
        "status": "passed",
        "summary": f"{len(before_items)} ActiveCheck resource(s) match the baseline suspend state.",
    }


def _observability_check(state: ProtectedCustomerState) -> dict[str, Any]:
    workload_text: list[str] = []
    workloads = state.sections.get("workloads")
    if isinstance(workloads, Mapping):
        for section in workloads.values():
            for item in _items_by_name(section).values():
                labels = item.get("labels") if isinstance(item.get("labels"), Mapping) else {}
                workload_text.extend(
                    [str(item.get("name", "") or ""), *[str(value) for value in labels.values()]]
                )
    observability_enabled = any(
        "observability" in item.lower() or "nebius-observability-agent" in item.lower()
        for item in workload_text
    )
    if not observability_enabled:
        return {
            "name": "observability-agent-health",
            "status": "skipped",
            "summary": "No observability-agent workload was discovered; backend ingestion remains a manual follow-up.",
        }
    return {
        "name": "observability-agent-health",
        "status": "passed",
        "summary": "Observability-agent workload evidence is present; backend ingestion still requires a read-endpoint query.",
    }


def _protected_comparison_check(
    comparison: Mapping[str, Any],
    *,
    remediation_approved: bool,
) -> dict[str, Any]:
    blocked_count = int(comparison.get("blocked_count") or 0)
    approval_count = int(comparison.get("approval_required_count") or 0)
    if blocked_count:
        return {
            "name": "protected-state-comparison",
            "status": "failed",
            "summary": f"{blocked_count} blocked protected-state delta(s) were detected.",
        }
    if approval_count:
        if not remediation_approved:
            return {
                "name": "protected-state-comparison",
                "status": "failed",
                "summary": (
                    f"{approval_count} protected-state delta(s) require remediation approval. "
                    "Review the report and rerun with --approve-remediation only if the "
                    "listed remediation-required deltas are acceptable."
                ),
            }
        return {
            "name": "protected-state-comparison",
            "status": "passed",
            "summary": f"{approval_count} protected-state delta(s) were remediation-approved.",
        }
    return {
        "name": "protected-state-comparison",
        "status": "passed",
        "summary": "Protected customer state matched the pre-upgrade baseline.",
    }


def _zero_downtime_eligibility(
    *,
    after_state: ProtectedCustomerState,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if int(comparison.get("blocked_count") or 0) > 0:
        reasons.append("blocked protected-state deltas exist")
    if not after_state.complete:
        reasons.append("post-upgrade protected-state capture is incomplete")
    nodes = _items_by_name(after_state.sections.get("nodes"))
    ready_count = sum(
        1 for item in nodes.values() if str(item.get("ready", "") or "").lower() == "true"
    )
    if nodes and ready_count == 0:
        reasons.append("no Ready Kubernetes nodes were captured")
    slurm_problem_nodes = _slurm_runtime_problem_nodes(after_state)
    if slurm_problem_nodes:
        sample = ", ".join(
            f"{item.get('name')}:{item.get('state')}" for item in slurm_problem_nodes[:6]
        )
        suffix = "" if len(slurm_problem_nodes) <= 6 else f" (+{len(slurm_problem_nodes) - 6} more)"
        reasons.append(f"Slurm worker nodes are not responsive: {sample}{suffix}")
    eligible = not reasons
    return {
        "status": "passed" if eligible else "blocked",
        "eligible": eligible,
        "summary": "Zero-downtime guardrails have no blocking evidence."
        if eligible
        else "Zero-downtime guardrail blocked: " + "; ".join(reasons),
        "reasons": reasons,
        "node_sample": {
            "captured": len(nodes),
            "ready": ready_count,
        },
        "slurm_problem_nodes": [to_plain_data(item) for item in slurm_problem_nodes],
    }


def _slurm_runtime_problem_nodes(state: ProtectedCustomerState) -> tuple[Mapping[str, str], ...]:
    runtime = _as_mapping(state.sections.get("slurm_runtime"))
    slurm_nodes = _as_mapping(runtime.get("slurm_nodes"))
    structured = slurm_nodes.get("problem_nodes")
    if isinstance(structured, Sequence) and not isinstance(structured, (str, bytes, bytearray)):
        items = tuple(dict(item) for item in structured if isinstance(item, Mapping))
        if items:
            return items
    summary = str(slurm_nodes.get("summary", "") or "")
    if summary:
        return _slurm_problem_nodes_from_scontrol(summary)
    return ()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _heavy_validation_followups(
    *,
    terraform_plan_command: Sequence[str] | None,
    external_cluster: bool,
) -> list[dict[str, Any]]:
    followups = [
        {
            "name": "metrics-backend-ingestion",
            "status": "manual",
            "summary": "Query Prometheus/Monitoring read endpoint for fresh Soperator and Slurm metrics.",
        },
        {
            "name": "logs-backend-ingestion",
            "status": "manual",
            "summary": "Query Loki/Logging read endpoint for fresh Soperator and Slurm logs.",
        },
        {
            "name": "slurm-job-recovery-audit",
            "status": "manual",
            "summary": "Review squeue and scontrol show job for customer job recovery.",
        },
        {
            "name": "full-slurm-acceptance",
            "status": "manual",
            "summary": "Run optional full Slurm smoke/benchmark suites when cluster policy allows it.",
        },
    ]
    if external_cluster:
        followups.append(
            {
                "name": "terraform-drift",
                "status": "not_applicable",
                "summary": "External Soperator cluster remains outside cxcli Terraform ownership.",
            }
        )
    elif terraform_plan_command:
        followups.append(
            {
                "name": "terraform-drift",
                "status": "manual",
                "summary": "Run the generated Terraform plan drift check outside the required fast gate.",
                "command": shlex.join(str(item) for item in terraform_plan_command),
            }
        )
    return followups


def _protected_pvc_names(pvcs: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    names: list[str] = []
    for name, item in pvcs.items():
        basename = name.rsplit("/", 1)[-1].lower()
        labels = item.get("labels") if isinstance(item.get("labels"), Mapping) else {}
        haystack = " ".join(
            [basename, *[str(key) for key in labels], *[str(value) for value in labels.values()]]
        )
        if any(key in haystack for key in _PROTECTED_PVC_KEYS):
            names.append(name)
    return tuple(sorted(names))


def _home_mount_hash(state: ProtectedCustomerState | None) -> str:
    if state is None:
        return ""
    runtime = state.sections.get("slurm_runtime")
    if not isinstance(runtime, Mapping):
        return ""
    home = runtime.get("home_mount")
    if not isinstance(home, Mapping) or not bool(home.get("available")):
        return ""
    return str(home.get("stdout_sha256") or "")


def _home_mount_is_shared_jail_home(state: ProtectedCustomerState | None) -> bool:
    if state is None:
        return False
    runtime = state.sections.get("slurm_runtime")
    if not isinstance(runtime, Mapping):
        return False
    home = runtime.get("home_mount")
    if not isinstance(home, Mapping) or not bool(home.get("available")):
        return False
    summary = str(home.get("summary") or "").lower()
    if "/mnt/jail/home" not in summary:
        return False
    return bool(
        "/shared/home" in summary
        or re.search(r'"source"\s*:\s*"jail\[/(?:shared/)?home\]"', summary)
    )


def _first_login_pod(pods: Mapping[str, Any]) -> str:
    for item in _items_by_name(pods).values():
        name = str(item.get("name", "") or "")
        phase = str(item.get("phase", "") or "").lower()
        labels = item.get("labels") if isinstance(item.get("labels"), Mapping) else {}
        label_text = " ".join(str(value).lower() for value in labels.values())
        if phase == "running" and ("login" in name.lower() or "login" in label_text):
            return name
    return ""


def _owner_refs(owner_refs: Any) -> list[dict[str, str]]:
    if not isinstance(owner_refs, list):
        return []
    result: list[dict[str, str]] = []
    for item in owner_refs:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "kind": str(item.get("kind", "") or ""),
                "name": str(item.get("name", "") or ""),
            }
        )
    return sorted(result, key=lambda item: (item["kind"], item["name"]))


def _restart_count(statuses: Any) -> int:
    if not isinstance(statuses, list):
        return 0
    total = 0
    for item in statuses:
        if not isinstance(item, Mapping):
            continue
        with suppress(Exception):
            total += int(item.get("restartCount") or 0)
    return total


def _waiting_reasons(statuses: Any) -> tuple[str, ...]:
    if not isinstance(statuses, list):
        return ()
    reasons: list[str] = []
    for item in statuses:
        if not isinstance(item, Mapping):
            continue
        state = item.get("state") if isinstance(item.get("state"), Mapping) else {}
        waiting = state.get("waiting") if isinstance(state.get("waiting"), Mapping) else {}
        reason = str(waiting.get("reason", "") or "").strip()
        if reason:
            reasons.append(reason)
    return tuple(reasons)


def _conditions(conditions: Any) -> list[dict[str, str]]:
    if not isinstance(conditions, list):
        return []
    result: list[dict[str, str]] = []
    for item in conditions:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "type": str(item.get("type", "") or ""),
                "status": str(item.get("status", "") or ""),
                "reason": str(item.get("reason", "") or ""),
            }
        )
    return sorted(result, key=lambda item: item["type"])


def _condition_status(conditions: Any, condition_type: str) -> str:
    for item in _conditions(conditions):
        if item["type"] == condition_type:
            return item["status"]
    return ""


def _stable_hash(value: Any) -> str:
    payload = json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_text(payload)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _storage_quantity_gib(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)([A-Za-z]*)$", text)
    if not match:
        return 0.0
    number = float(match.group(1))
    unit = match.group(2)
    multipliers = {
        "": 1 / (1024**3),
        "Ki": 1 / (1024**2),
        "Mi": 1 / 1024,
        "Gi": 1,
        "Ti": 1024,
        "Pi": 1024**2,
        "K": 1 / (1000**2),
        "M": 1 / 1000,
        "G": 1000**3 / 1024**3,
        "T": 1000**4 / 1024**3,
    }
    return number * multipliers.get(unit, 0.0)


def _summary_value(value: Any) -> Any:
    plain = to_plain_data(value)
    if isinstance(plain, Mapping):
        summary: dict[str, Any] = {}
        for key in (
            "kind",
            "namespace",
            "name",
            "phase",
            "status_phase",
            "storage_class",
            "request_storage",
            "capacity_storage",
            "suspend",
            "stdout_sha256",
            "line_count",
        ):
            if key in plain:
                summary[key] = plain[key]
        if summary:
            return summary
        return {"hash": _stable_hash(plain)}
    if isinstance(plain, list):
        return {"count": len(plain), "hash": _stable_hash(plain)}
    text = str(plain)
    return _truncate(text, 300)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _normalize_whitespace(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
