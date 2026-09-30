"""Bounded status observations; no registration, mutations or diagnostic jobs."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from . import kubernetes_process
from .soperator_flux_graph import target_soperator_release_name
from .soperator_registration import (
    SOPERATOR_GRAPH_LABEL,
    SOPERATOR_GRAPH_LABEL_VALUE,
    _flux_soperator_main_release,
    _helm_json,
    _is_soperator_release_candidate,
    _kubectl_json,
)
from .soperator_slurm_fields import slurm_fields
from .soperator_status_health import (
    StatusReadError,
    helm_owned,
    maintenance_active,
    mapping,
    metadata,
    owned_by,
    records,
)

_INVENTORY_TIMEOUT = 30
_SLURM_TIMEOUT = 10
_CUSTOM_RESOURCES = {
    "nodesets.slurm.nebius.ai": "NodeSet",
    "nodesetpowerstates.slurm.nebius.ai": "NodeSetPowerState",
    "activechecks.slurm.nebius.ai": "ActiveCheck",
    "nodeconfigurators.slurm.nebius.ai": "NodeConfigurator",
    "mariadbs.k8s.mariadb.com": "MariaDB",
}


def read_status_identity(
    *, kube_context: str, extra_env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Read only kube-system identity; callers validate it before further collection."""
    errors: list[dict[str, Any]] = []
    namespace = _kubectl_json(
        ["kubectl", "--context", kube_context, "get", "namespace", "kube-system", "-o", "json"],
        _INVENTORY_TIMEOUT,
        errors=errors,
        extra_env=extra_env,
    )
    uid = metadata(namespace).get("uid")
    if errors or not isinstance(uid, str) or not uid:
        raise StatusReadError("Status could not verify the cluster identity")
    return {"cluster_identity": {"kubernetes_uid": uid}}


def _slurm_query(
    context: str,
    pod: Mapping[str, Any],
    command: list[str],
    env: Mapping[str, str] | None,
) -> tuple[str | None, str]:
    containers = records(mapping(pod.get("spec")).get("containers"))
    # Login has one product container. Select it explicitly; never run an arbitrary sidecar.
    login = [c for c in containers if c.get("name") == "sshd"]
    if len(login) != 1:
        return None, "Login container could not be identified"
    argv = [
        "kubectl",
        "--context",
        context,
        "-n",
        str(metadata(pod)["namespace"]),
        "exec",
        str(metadata(pod)["name"]),
        "-c",
        "sshd",
        "--",
        *command,
    ]
    try:
        result = kubernetes_process.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=_SLURM_TIMEOUT,
            env=None if env is None else {**os.environ, **env},
        )
    except subprocess.TimeoutExpired:
        return None, "Slurm query timed out"
    except OSError:
        return None, "Slurm query could not be executed"
    if result.returncode != 0:
        return None, "Slurm query failed or access was denied"
    return result.stdout, ""


def collect_status_snapshot(
    *,
    kube_context: str,
    identity: Mapping[str, Any],
    extra_env: Mapping[str, str] | None = None,
    expected_slurmcluster_uid: str = "",
    progress: Callable[[str], None] = lambda _message: None,
) -> dict[str, Any]:
    """Collect independent lanes after the caller has verified cluster identity."""
    if not mapping(identity.get("cluster_identity")).get("kubernetes_uid"):
        raise StatusReadError("Status requires a validated cluster identity")
    issues: list[str] = []
    failed_namespaces: list[str] = []
    check_history = {"state": "unavailable", "detail": "Cluster attribution is unavailable"}

    def read(
        kind: str, namespace: str | None = None, *, selector: str = "", history: bool = False
    ) -> Mapping[str, Any]:
        errors: list[dict[str, Any]] = []
        argv = ["kubectl", "--context", kube_context, "get", kind]
        if namespace:
            argv += ["-n", namespace]
        elif kind != "crd":
            argv += ["-A"]
        if selector:
            argv += ["-l", selector]
        argv += ["-o", "json"]
        result = _kubectl_json(argv, _INVENTORY_TIMEOUT, errors=errors, extra_env=extra_env)
        if errors or not isinstance(result.get("items"), list):
            if history:
                check_history.update(
                    state="unavailable",
                    detail="Check history query failed or returned invalid inventory",
                )
            else:
                issues.append(f"{kind.split(',')[0]} query failed or returned invalid inventory")
            return {}
        return result

    progress("Reading Soperator resource definitions")
    crd_payload = read("crd")
    crds = {str(metadata(r).get("name")) for r in records(crd_payload.get("items"))}
    progress("Reading the registered Slurm cluster")
    cluster_payload = read("slurmclusters.slurm.nebius.ai")
    clusters = records(cluster_payload.get("items"))
    if expected_slurmcluster_uid:
        clusters = [c for c in clusters if metadata(c).get("uid") == expected_slurmcluster_uid]
        if cluster_payload and not clusters:
            raise StatusReadError("Live Slurm cluster identity differs from registered identity")
    if len(clusters) > 1:
        raise StatusReadError("Status found ambiguous Slurm cluster identities")
    cluster = clusters[0] if clusters else {}
    if cluster and (not metadata(cluster).get("uid") or not metadata(cluster).get("namespace")):
        raise StatusReadError("Status found an incomplete Slurm cluster identity")
    namespace = str(metadata(cluster).get("namespace") or "")
    custom: list[Mapping[str, Any]] = list(clusters)

    progress("Reading the installed Soperator release")
    release: dict[str, Any] = {}
    graph: list[Mapping[str, Any]] = []
    if any(name == "helmreleases.helm.toolkit.fluxcd.io" for name in crds):
        flux = read(
            "helmreleases.helm.toolkit.fluxcd.io",
            selector=f"{SOPERATOR_GRAPH_LABEL}={SOPERATOR_GRAPH_LABEL_VALUE}",
        )
        graph = records(flux.get("items"))
        errors: list[dict[str, Any]] = []
        release = _flux_soperator_main_release(flux, errors=errors)
        if errors:
            raise StatusReadError("Live Soperator release identity or release graph conflicts")
    # Direct upstream installations have no cxcli Flux graph; Helm remains their authority.
    if not graph and not any("helmreleases" in error for error in issues):
        errors = []
        releases = _helm_json(
            ["helm", "--kube-context", kube_context, "list", "-A", "-o", "json"],
            _INVENTORY_TIMEOUT,
            errors=errors,
            extra_env=extra_env,
        )
        candidates = [dict(r) for r in records(releases) if _is_soperator_release_candidate(r)]
        if errors:
            issues.append("Installed release query failed")
        elif len(candidates) > 1:
            raise StatusReadError("Status found ambiguous installed Soperator releases")
        elif candidates:
            release = candidates[0]
            chart = str(release.get("chart", ""))
            match = re.search(r"-(\d+\.\d+\.\d+.*)$", chart)
            if match:
                release["chart_version"] = match.group(1)
    if not release:
        issues.append("Installed Soperator release could not be verified")

    graph_owners = {
        (
            str(mapping(g.get("spec")).get("releaseName") or metadata(g).get("name") or ""),
            str(
                mapping(g.get("spec")).get("targetNamespace") or metadata(g).get("namespace") or ""
            ),
        )
        for g in graph
    }
    graph_owners.add((str(release.get("name") or ""), str(release.get("namespace") or "")))
    expected_configurators = [
        {
            "name": str(mapping(g.get("spec")).get("releaseName") or ""),
            "namespace": str(
                mapping(g.get("spec")).get("targetNamespace") or metadata(g).get("namespace") or ""
            ),
        }
        for g in graph
        if metadata(g).get("namespace") == "flux-system"
        and metadata(g).get("name")
        == target_soperator_release_name("soperator-fluxcd-nodeconfigurator")
    ]
    if any(not r["name"] or not r["namespace"] for r in expected_configurators):
        issues.append("NodeConfigurator release identity is incomplete")
    if namespace:
        if not crd_payload:
            check_history.update(detail="ActiveCheck API discovery is unavailable")
        elif "activechecks.slurm.nebius.ai" not in crds:
            check_history.update(state="not_installed", detail="ActiveCheck API is not installed")
    progress("Reading Slurm component configuration")
    for resource in _CUSTOM_RESOURCES:
        if resource not in crds or not namespace:
            continue
        # NodeConfigurator belongs to the shared release graph and may have its own namespace.
        selected_namespace = None if resource.startswith("nodeconfigurators.") else namespace
        is_history = resource.startswith("activechecks.")
        payload = read(resource, selected_namespace, history=is_history)
        if is_history and payload:
            check_history.update(state="collected", detail="")
        items = records(payload.get("items"))
        if resource.startswith("nodeconfigurators."):
            items = [
                r
                for r in items
                if any(helm_owned(r, name=name, namespace=ns) for name, ns in graph_owners)
            ]
        custom.extend(items)

    workloads: list[Mapping[str, Any]] = []
    namespaces = {namespace, str(release.get("namespace") or "")} | {
        str(metadata(r).get("namespace") or "")
        for r in custom
        if r.get("kind") == "NodeConfigurator"
    }
    for ns in sorted(namespaces - {""}):
        progress("Reading component workload readiness")
        kinds = "deployments.apps,statefulsets.apps,daemonsets.apps,replicasets.apps,pods"
        if any(name == "statefulsets.apps.kruise.io" for name in crds):
            kinds += ",statefulsets.apps.kruise.io"
        payload = read(kinds, ns)
        if not payload:
            failed_namespaces.append(ns)
        workloads.extend(records(payload.get("items")))

    ping: dict[str, Any] = {"state": "failed"}
    nodes: dict[str, Any] = {"state": "failed"}
    login_workloads = [
        w
        for w in workloads
        if w.get("kind") == "StatefulSet"
        and owned_by(w, cluster)
        and mapping(metadata(w).get("labels")).get("app.kubernetes.io/component") == "login"
    ]
    login_pods = [
        p
        for p in workloads
        if p.get("kind") == "Pod"
        and not metadata(p).get("deletionTimestamp")
        and any(owned_by(p, w) for w in login_workloads)
        and any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in records(mapping(p.get("status")).get("conditions"))
        )
    ]
    if cluster and maintenance_active(cluster):
        ping = {"state": "not-applicable"}
        nodes = {"state": "not-applicable"}
    elif login_pods:
        pod = sorted(login_pods, key=lambda p: str(metadata(p).get("name")))[0]
        progress("Checking Slurm controller responses")
        output, error = _slurm_query(kube_context, pod, ["scontrol", "ping"], extra_env)
        if output is not None:
            states = re.findall(
                r"Slurmctld(?:\([^)]*\))?.*?\bis\s+(UP|DOWN)\b", output, re.IGNORECASE
            )
            if states:
                ping = {
                    "state": "succeeded",
                    "up": sum(s.upper() == "UP" for s in states),
                    "total": len(states),
                }
            else:
                error = "Controller response was not recognized"
        if error:
            issues.append(error)
        progress("Reading Slurm worker states")
        output, error = _slurm_query(
            kube_context, pod, ["scontrol", "show", "nodes", "-o"], extra_env
        )
        if output is not None:
            parsed = [slurm_fields(line) for line in output.splitlines() if line.strip()]
            names = [p.get("NodeName") for p in parsed]
            if (
                parsed
                and all(names)
                and all(p.get("State") for p in parsed)
                and len(set(names)) == len(names)
            ):
                nodes = {
                    "state": "succeeded",
                    "nodes": {p["NodeName"]: {"State": p["State"]} for p in parsed},
                }
            else:
                error = "Slurm node inventory was invalid or ambiguous"
        if error:
            issues.append(error)
    else:
        issues.append("No attributed Ready login pod for Slurm queries")
    return {
        **identity,
        "soperator_resources": custom,
        "workloads": workloads,
        "component_namespace_resources": workloads,
        "soperator_namespace_resources": [],
        "helm_releases": [release] if release else [],
        "status_release": release,
        "status_issues": issues,
        "status_check_history": check_history,
        "expected_configurator_releases": expected_configurators,
        "collection_errors": [],
        "failed_namespaces": failed_namespaces,
        "slurm_ping": ping,
        "slurm_nodes": nodes,
    }
