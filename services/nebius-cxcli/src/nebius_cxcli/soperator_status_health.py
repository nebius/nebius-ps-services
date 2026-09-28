"""Pure, target-attributed health observations for the status command."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .soperator_status_checks import CheckHistory, project_check_history

HEALTH_STYLES = {
    "Healthy": "green",
    "Degraded": "yellow",
    "Unhealthy": "red",
    "Error": "bold red",
    "Unknown": "magenta",
    "Disabled": "dim white",
    "Scaled to zero": "cyan",
    "Not checked": "blue",
}
_IMPACTS = {
    "operator": "reconciliation may stall; running jobs may continue",
    "controller": "job submission and scheduling may be unavailable",
    "login": "interactive access and job submission through login nodes may fail",
    "nodeset": "available compute capacity is reduced",
    "accounting": "job accounting and accounting-dependent scheduling may be affected",
    "database": "accounting storage and job history may be unavailable",
    "rest": "REST clients may be unable to submit or inspect jobs",
    "exporter": "Slurm metrics may be missing",
    "sconfigcontroller": "Slurm configuration updates may stall",
    "node-configurator": "node preparation may fail",
    "placeholder": "controller placement or recovery may be delayed",
}


class StatusReadError(RuntimeError):
    """A fixed, safe status diagnostic that contains no raw transport output."""


def mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def records(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def safe_text(value: object, limit: int = 240) -> str:
    text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(value))
    return " ".join(text.split())[:limit]


def metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return mapping(item.get("metadata"))


def helm_owned(item: Mapping[str, Any], *, name: str, namespace: str) -> bool:
    """Helm ownership records the release target namespace, not its storage namespace."""
    annotations = mapping(metadata(item).get("annotations"))
    return bool(name and namespace) and (
        metadata(item).get("namespace") == namespace
        and annotations.get("meta.helm.sh/release-name") == name
        and annotations.get("meta.helm.sh/release-namespace") == namespace
    )


def owned_by(item: Mapping[str, Any], owner: Mapping[str, Any]) -> bool:
    """Require immutable controller ownership within the same namespace/API group."""
    meta, parent = metadata(item), metadata(owner)
    return (
        bool(parent.get("uid"))
        and meta.get("namespace") == parent.get("namespace")
        and len(
            [ref for ref in records(meta.get("ownerReferences")) if ref.get("controller") is True]
        )
        == 1
        and any(
            ref.get("uid") == parent["uid"]
            and ref.get("kind") == owner.get("kind")
            and ref.get("apiVersion", "").split("/")[0]
            == str(owner.get("apiVersion", "")).split("/")[0]
            and ref.get("controller") is True
            for ref in records(meta.get("ownerReferences"))
        )
    )


def cluster_workload(item: Mapping[str, Any], cluster: Mapping[str, Any], component: str) -> bool:
    labels = mapping(metadata(item).get("labels"))
    return (
        metadata(item).get("namespace") == metadata(cluster).get("namespace")
        and labels.get("app.kubernetes.io/name") == "slurmcluster"
        and labels.get("app.kubernetes.io/instance") == metadata(cluster).get("name")
        and labels.get("app.kubernetes.io/component") == component
        and owned_by(item, cluster)
    )


@dataclass(frozen=True)
class ComponentHealth:
    component: str
    state: str
    ready: int | None = None
    expected: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class StatusHealthReport:
    components: tuple[ComponentHealth, ...]
    issues: tuple[str, ...] = ()
    history: CheckHistory = CheckHistory()

    @property
    def overall(self) -> str:
        states = {row.state for row in self.components}
        for state in ("Error", "Unhealthy", "Degraded", "Unknown"):
            if state in states:
                return state
        if self.issues or "Healthy" not in states:
            return "Unknown"
        return "Healthy"

    @property
    def summary(self) -> str:
        counts = Counter(row.state for row in self.components)
        parts = [
            f"{counts[state]} {state.lower()}"
            for state in ("Error", "Unhealthy", "Degraded", "Unknown")
            if counts[state]
        ]
        if self.issues:
            parts.append("partial report")
        return "; ".join(parts)


def _integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _problem(row: ComponentHealth, state: str, reason: str, role: str) -> ComponentHealth:
    return replace(row, state=state, detail=f"{reason}; {_IMPACTS[role]}.")


def _pod_ready(pod: Mapping[str, Any]) -> bool:
    return not metadata(pod).get("deletionTimestamp") and any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in records(mapping(pod.get("status")).get("conditions"))
    )


def workload_health(
    name: str,
    role: str,
    candidates: list[Mapping[str, Any]],
    *,
    expected: int | None,
    enabled: bool = True,
    collection_failed: bool = False,
    resources: list[Mapping[str, Any]] | None = None,
) -> ComponentHealth:
    row = ComponentHealth(name, "Unknown", expected=expected)
    if not enabled:
        return replace(row, state="Disabled", detail="Disabled in configuration.")
    if collection_failed:
        return replace(
            row, detail="Workload query failed; component availability could not be verified."
        )
    if len(candidates) != 1:
        if not candidates and expected == 0:
            return replace(
                row, state="Scaled to zero", ready=0, detail="Configured with zero capacity."
            )
        if not candidates and expected is not None:
            return _problem(
                replace(row, ready=0), "Unhealthy", "Expected workload is missing", role
            )
        return replace(
            row,
            detail="Workload ownership is missing or ambiguous; availability could not be verified.",
        )
    item = candidates[0]
    meta, spec, status = metadata(item), mapping(item.get("spec")), mapping(item.get("status"))
    desired = (
        _integer(status.get("desiredNumberScheduled"))
        if item.get("kind") == "DaemonSet"
        else _integer(spec.get("replicas"))
    )
    ready = _integer(
        status.get("numberReady" if item.get("kind") == "DaemonSet" else "readyReplicas", 0)
    )
    row = replace(row, ready=ready, expected=expected if expected is not None else desired)
    if meta.get("deletionTimestamp"):
        return _problem(row, "Degraded", "Workload is terminating", role)
    if not meta.get("generation") or status.get("observedGeneration") != meta.get("generation"):
        return replace(
            row, detail="Workload has not observed its current generation; readiness is unverified."
        )
    if desired is None or ready is None or row.expected is None:
        return replace(row, detail="Readiness counts are unavailable.")
    if desired != row.expected:
        return _problem(row, "Degraded", "Workload size differs from configured capacity", role)
    if row.expected == 0:
        return replace(row, state="Scaled to zero", detail="Configured with zero capacity.")
    if ready < row.expected:
        return _problem(
            row,
            "Unhealthy" if ready == 0 else "Degraded",
            f"{row.expected - ready} replicas not ready",
            role,
        )
    # Pod ownership adds actual readiness evidence, excluding old Job pods and terminating replicas.
    if resources is not None:
        owners = [item] + [
            r for r in resources if r.get("kind") == "ReplicaSet" and owned_by(r, item)
        ]
        pods = [
            r
            for r in resources
            if r.get("kind") == "Pod" and any(owned_by(r, owner) for owner in owners)
        ]
        ready_pods = sum(_pod_ready(pod) for pod in pods)
        if ready_pods < row.expected:
            return _problem(
                replace(row, ready=ready_pods),
                "Unhealthy" if not ready_pods else "Degraded",
                "Owned pods are not ready",
                role,
            )
    return replace(row, state="Healthy")


def _conditions_unhealthy(item: Mapping[str, Any], condition_type: str) -> bool:
    return any(
        c.get("type") == condition_type
        and c.get("status") == "False"
        and c.get("observedGeneration") in (None, 0, metadata(item).get("generation"))
        for c in records(mapping(item.get("status")).get("conditions"))
    )


def maintenance_active(cluster: Mapping[str, Any]) -> bool:
    value = mapping(cluster.get("spec")).get("maintenance")
    if value not in (
        None,
        "none",
        "skipPopulateJail",
        "downscale",
        "downscaleAndDeletePopulateJail",
        "downscaleAndOverwritePopulateJail",
    ):
        raise ValueError("Unrecognized Slurm maintenance state")
    return value not in (None, "none", "skipPopulateJail")


def active_worker_ordinals(
    nodeset: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> list[int] | None:
    spec = mapping(nodeset.get("spec"))
    desired = _integer(spec.get("replicas"))
    if desired is None:
        return None
    if spec.get("ephemeralNodes") is not True:
        return list(range(desired))
    power = [
        p
        for p in records(snapshot.get("soperator_resources"))
        if p.get("kind") == "NodeSetPowerState"
        and owned_by(p, nodeset)
        and mapping(p.get("spec")).get("nodeSetRef") == metadata(nodeset).get("name")
    ]
    if len(power) != 1:
        return None
    ordinals = mapping(power[0].get("spec")).get("activeNodes")
    if (
        not isinstance(ordinals, list)
        or any(type(i) is not int or i < 0 or i >= desired for i in ordinals)
        or len(set(ordinals)) != len(ordinals)
    ):
        return None
    return ordinals


def _worker_runtime(
    row: ComponentHealth, nodeset: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> ComponentHealth:
    if row.state in {"Unknown", "Scaled to zero"}:
        return row
    runtime = mapping(snapshot.get("slurm_nodes"))
    if runtime.get("state") != "succeeded":
        if row.state in {"Degraded", "Unhealthy"}:
            return row
        return replace(
            row,
            state="Unknown",
            detail="Slurm node query failed; registration and availability could not be verified.",
        )
    name = str(metadata(nodeset).get("name"))
    ordinals = active_worker_ordinals(nodeset, snapshot)
    if ordinals is None:
        return replace(
            row,
            state="Unknown",
            detail="Active NodeSet capacity is unavailable; Slurm capacity could not be verified.",
        )
    nodes = mapping(runtime.get("nodes"))
    missing = 0
    bad: Counter[str] = Counter()
    unknown = False
    for ordinal in ordinals:
        state = str(mapping(nodes.get(f"{name}-{ordinal}")).get("State", ""))
        if not state:
            missing += 1
            continue
        flags = set(re.split(r"[+~#*$%@!]+", state.upper()))
        if (
            flags
            & {
                "DOWN",
                "DRAIN",
                "DRAINED",
                "DRAINING",
                "FAIL",
                "FAILING",
                "NO_RESPOND",
                "INVALID_REG",
                "MAINT",
                "MAINTENANCE",
                "POWERING_UP",
                "POWERING_DOWN",
                "POWERED_DOWN",
                "FUTURE",
            }
            or "*" in state
        ):
            bad[safe_text(state.lower(), 50)] += 1
        elif not flags <= {
            "IDLE",
            "ALLOCATED",
            "MIXED",
            "COMPLETING",
            "RESERVED",
            "PLANNED",
            "CLOUD",
        }:
            unknown = True
    if missing or bad:
        reasons = ([f"{missing} missing from Slurm"] if missing else []) + [
            f"{count} {state}" for state, count in sorted(bad.items())
        ]
        reason = ", ".join(reasons)
        if row.detail:
            reason = row.detail + " " + reason
        return _problem(
            row,
            "Unhealthy"
            if row.state == "Unhealthy" or missing + sum(bad.values()) == len(ordinals)
            else "Degraded",
            reason,
            "nodeset",
        )
    if unknown and row.state == "Healthy":
        return replace(
            row,
            state="Unknown",
            detail="Unrecognized active Slurm node state; availability could not be verified.",
        )
    return row


def project_status_health(snapshot: Mapping[str, Any]) -> StatusHealthReport:
    """Project one validated target snapshot without performing any I/O."""
    resources = records(snapshot.get("workloads"))
    custom = records(snapshot.get("soperator_resources"))
    clusters = [r for r in custom if r.get("kind") == "SlurmCluster"]
    issues = tuple(safe_text(x) for x in snapshot.get("status_issues", []))
    if len(clusters) != 1:
        return StatusHealthReport(
            (
                ComponentHealth(
                    "Slurm cluster",
                    "Unknown",
                    detail="Cluster inventory is unavailable; component health could not be verified.",
                ),
            ),
            issues,
        )
    cluster = clusters[0]
    spec = mapping(cluster.get("spec"))
    slurm = mapping(spec.get("slurmNodes"))
    maintenance = maintenance_active(cluster)
    failed = set(snapshot.get("failed_namespaces", []))
    namespace = metadata(cluster).get("namespace")
    rows: list[ComponentHealth] = []
    if metadata(cluster).get("deletionTimestamp"):
        rows.append(
            ComponentHealth(
                "Slurm cluster",
                "Degraded",
                detail="Slurm cluster is terminating; services and scheduling may become unavailable.",
            )
        )
    release = mapping(snapshot.get("status_release"))
    operator_candidates = [
        r
        for r in resources
        if r.get("kind") == "Deployment"
        and mapping(metadata(r).get("labels")).get("control-plane") == "controller-manager"
        and helm_owned(
            r, name=str(release.get("name") or ""), namespace=str(release.get("namespace") or "")
        )
    ]
    rows.append(
        workload_health(
            "Soperator operator",
            "operator",
            operator_candidates,
            expected=None,
            collection_failed=release.get("namespace") in failed,
            resources=resources,
        )
    )
    if release.get("status") != "deployed" and rows[-1].state == "Healthy":
        rows[-1] = _problem(
            rows[-1],
            "Unhealthy" if release.get("status") == "failed" else "Unknown",
            "Installed release is not deployed",
            "operator",
        )
    definitions = (
        ("controller", "Slurm controllers", "StatefulSet", "ControllersAvailable"),
        ("login", "Login", "StatefulSet", "LoginAvailable"),
        ("accounting", "Accounting", "Deployment", "AccountingAvailable"),
        ("rest", "Slurm REST", "Deployment", ""),
        ("exporter", "Slurm exporter", "Deployment", ""),
        ("sconfigcontroller", "SConfig controller", "Deployment", "SConfigControllerAvailable"),
    )
    for role, title, kind, condition in definitions:
        config = (
            mapping(mapping(spec.get("sConfigController")).get("node"))
            if role == "sconfigcontroller"
            else mapping(slurm.get(role))
        )
        enabled = (
            config.get("enabled", False) is True
            if role in {"accounting", "rest", "exporter"}
            else True
        )
        candidates = [
            r
            for r in resources
            if r.get("kind") == kind
            and cluster_workload(r, cluster, role)
            and (
                role != "controller"
                or mapping(metadata(r).get("labels")).get("slurm.nebius.ai/controller-type")
                == "main"
            )
        ]
        desired = (
            1
            if role in {"controller", "accounting", "exporter"} and config
            else _integer(config.get("size"))
        )
        if maintenance:
            desired = 0
        row = workload_health(
            title,
            role,
            candidates,
            expected=desired,
            enabled=enabled,
            collection_failed=namespace in failed,
            resources=resources,
        )
        if row.state == "Healthy" and condition and _conditions_unhealthy(cluster, condition):
            row = _problem(row, "Degraded", f"{condition} is false", role)
        if role == "controller" and row.state in {"Healthy", "Degraded"}:
            ping = mapping(snapshot.get("slurm_ping"))
            if ping.get("state") != "succeeded":
                if row.state == "Healthy":
                    row = replace(
                        row,
                        state="Unknown",
                        detail="Controller query failed; Slurm responsiveness could not be verified.",
                    )
            else:
                up, total = int(ping.get("up", 0)), int(ping.get("total", 0))
                if up < max(row.expected or 0, total):
                    row = _problem(
                        row,
                        "Unhealthy" if up == 0 else "Degraded",
                        f"{up}/{max(row.expected or 0, total)} controllers responding",
                        "controller",
                    )
        if not config:
            row = replace(
                row,
                state="Unknown",
                detail="Component configuration is missing; availability could not be verified.",
            )
        rows.append(row)
    if maintenance:
        rows.append(
            ComponentHealth(
                "Slurm scheduling",
                "Degraded",
                detail="Declared maintenance downscales Slurm; job submission and scheduling are unavailable.",
            )
        )
    # Worker identity comes from the cluster annotation and immutable NodeSet owner.
    nodesets = [
        r
        for r in custom
        if r.get("kind") == "NodeSet"
        and metadata(r).get("namespace") == namespace
        and mapping(metadata(r).get("annotations")).get("slurm.nebius.ai/parental-cluster-ref")
        == metadata(cluster).get("name")
    ]
    for nodeset in sorted(nodesets, key=lambda n: str(metadata(n).get("name"))):
        name = str(metadata(nodeset).get("name"))
        candidates = [
            r
            for r in resources
            if r.get("kind") == "StatefulSet"
            and owned_by(r, nodeset)
            and mapping(metadata(r).get("labels")).get("slurm.nebius.ai/nodeset") == name
            and mapping(metadata(r).get("labels")).get("slurm.nebius.ai/worker") == "true"
        ]
        ordinals = [] if maintenance else active_worker_ordinals(nodeset, snapshot)
        row = workload_health(
            f"Workers: {safe_text(name, 80)}",
            "nodeset",
            candidates,
            expected=len(ordinals) if ordinals is not None else None,
            collection_failed=namespace in failed,
            resources=resources,
        )
        if ordinals is None:
            row = replace(
                row,
                state="Unknown",
                detail="Active NodeSet capacity is unavailable; worker readiness could not be verified.",
            )
        if metadata(nodeset).get("deletionTimestamp"):
            row = _problem(row, "Degraded", "NodeSet is terminating", "nodeset")
        rows.append(_worker_runtime(row, nodeset, snapshot))
    if not nodesets:
        rows.append(
            ComponentHealth(
                "Workers",
                "Unknown",
                detail="No attributed NodeSets; worker capacity could not be verified.",
            )
        )
    accounting = mapping(slurm.get("accounting"))
    database = mapping(accounting.get("mariadbOperator"))
    if accounting.get("enabled") is True and database.get("enabled") is True:
        databases = [r for r in custom if r.get("kind") == "MariaDB" and owned_by(r, cluster)]
        row = ComponentHealth(
            "Accounting database", "Unknown", detail="Database readiness could not be verified."
        )
        if (
            maintenance
            and len(databases) == 1
            and mapping(databases[0].get("spec")).get("replicas") == 0
        ):
            row = replace(
                row, state="Scaled to zero", detail="Downscaled for declared maintenance."
            )
        elif len(databases) == 1 and metadata(databases[0]).get("deletionTimestamp"):
            row = _problem(row, "Degraded", "Database is terminating", "database")
        elif len(databases) == 1:
            conditions = records(mapping(databases[0].get("status")).get("conditions"))
            ready_condition = next((c for c in conditions if c.get("type") == "Ready"), {})
            if ready_condition.get("observedGeneration") in (
                None,
                0,
                metadata(databases[0]).get("generation"),
            ):
                if ready_condition.get("status") == "True":
                    row = replace(row, state="Healthy", detail="")
                elif ready_condition.get("status") == "False":
                    row = _problem(
                        row, "Unhealthy", "Database Ready condition is false", "database"
                    )
        rows.append(row)
    for resource in resources:
        if (
            resource.get("kind") == "DaemonSet"
            and cluster_workload(resource, cluster, "controller")
            and mapping(metadata(resource).get("labels")).get("slurm.nebius.ai/controller-type")
            == "placeholder"
        ):
            rows.append(
                workload_health(
                    "Controller placement",
                    "placeholder",
                    [resource],
                    expected=None,
                    resources=resources,
                )
            )
    configurators = [r for r in custom if r.get("kind") == "NodeConfigurator"]
    for owner in records(snapshot.get("expected_configurator_releases")):
        attributed = [
            r
            for r in configurators
            if helm_owned(
                r, name=str(owner.get("name") or ""), namespace=str(owner.get("namespace") or "")
            )
        ]
        if len(attributed) != 1:
            rows.append(
                ComponentHealth(
                    f"Node configuration: {safe_text(owner.get('name'), 60)}",
                    "Unknown",
                    detail="Expected NodeConfigurator is missing or ambiguous; node preparation could not be verified.",
                )
            )
            configurators = [r for r in configurators if r not in attributed]
    for configurator in sorted(
        configurators,
        key=lambda r: str(metadata(r).get("name")),
    ):
        candidates = [
            r for r in resources if r.get("kind") == "DaemonSet" and owned_by(r, configurator)
        ]
        rows.append(
            workload_health(
                f"Node configuration: {safe_text(metadata(configurator).get('name'), 60)}",
                "node-configurator",
                candidates,
                expected=None,
                collection_failed=metadata(configurator).get("namespace") in failed,
                resources=resources,
            )
        )
    checks = [
        r
        for r in custom
        if r.get("kind") == "ActiveCheck"
        and metadata(r).get("namespace") == namespace
        and mapping(r.get("spec")).get("slurmClusterRefName") == metadata(cluster).get("name")
    ]
    history = project_check_history(mapping(snapshot.get("status_check_history")), checks)
    return StatusHealthReport(tuple(rows), issues, history)
