"""Read-only identities for the admitted live HelmRelease settings."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .deployment_state import digest


def observe_release_settings(
    cli: Any, *, kube_env: Mapping[str, str], identities: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for namespace, name in identities:
        if not namespace or not name or f"{namespace}/{name}" in result:
            raise RuntimeError("Deployment release settings inventory is ambiguous")
        response = cli._run_soperator_upgrade_kubectl(
            namespace,
            [
                "get",
                "helmrelease",
                name,
                "-o",
                "json",
                "--ignore-not-found=true",
                "--request-timeout=20s",
            ],
            kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
            extra_env=kube_env,
            timeout_seconds=60,
            check=True,
        )
        if not response.stdout.strip():
            result[f"{namespace}/{name}"] = {"missing": True}
            continue
        payload = json.loads(response.stdout)
        metadata, spec = payload.get("metadata", {}), payload.get("spec")
        if (
            not isinstance(spec, Mapping)
            or not metadata.get("uid")
            or metadata.get("name") != name
            or metadata.get("namespace") != namespace
        ):
            raise RuntimeError("Live release settings identity is incomplete")
        # Digests retain the complete owned settings without storing runtime values.
        result[f"{namespace}/{name}"] = {"uid": metadata["uid"], "specSha256": digest(spec)}
    if not result:
        raise RuntimeError("Deployment release settings inventory is empty")
    return result


def rendered_release_identities(
    flux_dir: Path, graph: Mapping[str, Any]
) -> tuple[tuple[str, str], ...]:
    from .flux_ops import FLUX_NAMESPACE

    # Graph namespaces are Helm *target* namespaces. The Flux objects live in
    # flux-system. Include rendered outer and ordinary releases as well.
    identities = {(FLUX_NAMESPACE, row["releaseName"]) for row in graph["releases"]}
    for path in sorted({*flux_dir.rglob("*.yaml"), *flux_dir.rglob("*.yml")}):
        for document in yaml.safe_load_all(path.read_text()):
            if isinstance(document, Mapping) and document.get("kind") == "HelmRelease":
                metadata = document.get("metadata", {})
                name, namespace = metadata.get("name"), metadata.get("namespace")
                if not name or not namespace:
                    raise RuntimeError("Rendered HelmRelease has incomplete identity")
                identities.add((namespace, name))
    return tuple(sorted(identities))


def target_documents(
    generation: Any,
    target_ref: str,
    *,
    ordinary_only: bool = False,
    protected_only: bool = False,
    documents_projection: Any = None,
    resource_paths: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read the immutable target's declared resources, including nested bundles."""
    import base64
    import posixpath

    rows = [
        row
        for row in generation.manifest.get("deploy", {}).get("targets", [])
        if row.get("target_ref") == target_ref
    ]
    if len(rows) != 1:
        raise RuntimeError("Desired target inventory is missing or ambiguous")
    root = str(rows[0].get("flux_dir") or "flux").rstrip("/")
    if ordinary_only:
        if root + "/ordinary/kustomization.yaml" in generation.files:
            root += "/ordinary"
        else:
            from .deployment_plan import soperator_target

            owner = soperator_target(generation.manifest.get("runtime_config", {}))
            if owner and owner[0] == target_ref:
                return {}
    files = generation.files
    result: dict[str, dict[str, Any]] = {}
    ordinary_origins: dict[str, bool] = {}
    visited = set()

    def read(name: str) -> list[Any]:
        if name not in files:
            raise RuntimeError("Desired target resource is missing from its generation")
        return list(yaml.safe_load_all(base64.b64decode(files[name], validate=True)))

    def visit(directory: str) -> None:
        if directory in visited:
            raise RuntimeError("Desired target contains a cyclic resource directory")
        visited.add(directory)
        manifests = read(directory + "/kustomization.yaml")
        if len(manifests) != 1 or not isinstance(manifests[0], Mapping):
            raise RuntimeError("Desired target kustomization is invalid")
        resources = list(manifests[0].get("resources", []))
        resources.extend(
            Path(name).name
            for name in files
            if posixpath.dirname(name) == directory
            and Path(name).name.startswith("post-flux-")
            and name.endswith(".yaml")
            and Path(name).name not in resources
        )
        for resource in resources:
            name = posixpath.normpath(directory + "/" + resource)
            if not name.startswith(root + "/"):
                raise RuntimeError("Desired target resource escapes its bundle")
            if name + "/kustomization.yaml" in files:
                visit(name)
                continue
            if resource_paths is not None:
                resource_paths.add(name)
            for doc in read(name):
                if not isinstance(doc, dict) or not doc.get("apiVersion") or not doc.get("kind"):
                    raise RuntimeError("Desired resource has incomplete identity")
                meta = doc.get("metadata", {})
                if not meta.get("name"):
                    raise RuntimeError("Desired resource has no name")
                group = doc["apiVersion"].split("/")[0] if "/" in doc["apiVersion"] else ""
                key = "/".join((group, doc["kind"], meta.get("namespace", ""), meta["name"]))
                is_ordinary = not ordinary_only and name.startswith(root + "/ordinary/")
                if key in result and result[key] != doc:
                    from .ordinary_apps import is_shared_protected_resource

                    if ordinary_origins[key] != is_ordinary:
                        ordinary_doc = doc if is_ordinary else result[key]
                        protected_doc = result[key] if is_ordinary else doc
                        if is_shared_protected_resource(ordinary_doc, protected_doc):
                            result[key] = protected_doc
                            ordinary_origins[key] = False
                            continue
                    raise RuntimeError("Desired resource identity is ambiguous")
                result[key] = doc
                ordinary_origins[key] = ordinary_origins.get(key, True) and is_ordinary

    if root + "/kustomization.yaml" not in files:
        raise RuntimeError("Desired target kustomization is missing from its generation")
    visit(root)
    ordinary = root + "/ordinary"
    if ordinary + "/kustomization.yaml" in files and ordinary not in visited:
        visit(ordinary)
    graphs = [
        doc
        for doc in result.values()
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
    ]
    if graphs:
        if len(graphs) != 1:
            raise RuntimeError("Desired Soperator graph is ambiguous")
        from .flux_ops import stable_soperator_documents

        graph = json.loads(graphs[0]["data"]["graph.json"])
        normalized = stable_soperator_documents(list(result.values()), graph["releases"])
        if documents_projection is not None:
            normalized = documents_projection(normalized, graph["releases"])
        result = dict(zip(result, normalized, strict=True))
    if protected_only:
        ordinary_documents = target_documents(generation, target_ref, ordinary_only=True)
        result = {key: doc for key, doc in result.items() if key not in ordinary_documents}
        if not result or not graphs:
            raise RuntimeError("Protected Soperator resource inventory is incomplete")
    return result


def _applied_values_match(desired: Any, live: Any) -> bool:
    """Keep values exact while accepting client-side apply's null map deletions."""
    if isinstance(desired, Mapping):
        if not isinstance(live, Mapping) or live.keys() - desired.keys():
            return False
        return all(
            (value is None if key not in live else _applied_values_match(value, live[key]))
            for key, value in desired.items()
        )
    # JSON merge patch replaces lists atomically: nulls inside lists are data.
    return desired == live


def _owned_matches(desired: Any, live: Any, previous: Any = None) -> bool:
    if isinstance(desired, Mapping):
        if not isinstance(live, Mapping):
            return False
        prior = previous if isinstance(previous, Mapping) else {}
        for key, value in desired.items():
            if key == "values":
                if not _applied_values_match(value, live.get(key, {})):
                    return False
            elif key not in live or not _owned_matches(value, live[key], prior.get(key)):
                return False
        return all(key in desired or key not in live for key in prior)
    if isinstance(desired, list):
        if not isinstance(live, list) or len(desired) != len(live):
            return False
        prior_list = previous if isinstance(previous, list) else []
        return all(
            _owned_matches(
                value, live[index], prior_list[index] if index < len(prior_list) else None
            )
            for index, value in enumerate(desired)
        )
    return desired == live


class DesiredStateNotConverged(RuntimeError):
    """An authoritative observation requires another admitted application attempt."""


def _soperator_values_keys(documents: Mapping[str, dict[str, Any]]) -> set[str]:
    graphs = [
        doc
        for doc in documents.values()
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "nebius-cxcli-soperator-release-graph"
    ]
    if not graphs:
        return set()
    from .flux_ops import _staged_soperator_outer_release

    graph = json.loads(graphs[0]["data"]["graph.json"])
    outer = _staged_soperator_outer_release(list(documents.values()), graph["releases"])
    return {
        key
        for key, doc in documents.items()
        if doc is outer
        or (
            doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "terraform-fluxcd-values"
        )
    }


def bind_soperator_observation_identity(documents, releases, *, target_ref, cluster_id):
    """Resolve renderer-owned identity placeholders from authenticated cluster evidence."""
    import copy

    from .flux_ops import _staged_soperator_outer_release

    result = copy.deepcopy(documents)
    outer = _staged_soperator_outer_release(result, releases)
    for document in result:
        is_values = (
            document.get("kind") == "ConfigMap"
            and document.get("metadata", {}).get("name") == "terraform-fluxcd-values"
        )
        if document is not outer and not is_values:
            continue
        values = (
            yaml.safe_load(document["data"]["values.yaml"])
            if is_values
            else document.get("spec", {}).get("values", {})
        )
        for path in (
            ("observability", "clusterId"),
            (
                "observability",
                "vmStack",
                "values",
                "vmagent",
                "spec",
                "externalLabels",
                "mk8s_cluster_id",
            ),
        ):
            parent = values
            for key in path[:-1]:
                parent = parent.get(key, {}) if isinstance(parent, dict) else {}
            if path[-1] not in parent:
                continue
            if parent[path[-1]] not in {target_ref, cluster_id}:
                raise RuntimeError(
                    "Rendered observability identity conflicts with accepted cluster"
                )
            parent[path[-1]] = cluster_id
        if is_values:
            document["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
    return result


def _canonical_node_filter_members(values: dict[str, Any]) -> None:
    """NodeSelector In/NotIn values are unordered; preserve all other owned fields."""
    override: Any = values
    for key in ("slurmCluster", "overrideValues"):
        if not isinstance(override, dict):
            return
        override = override.get(key)
    filters = override.get("k8sNodeFilters") if isinstance(override, dict) else None
    if not isinstance(filters, list):
        return
    for node_filter in filters:
        terms = node_filter
        for key in (
            "affinity",
            "nodeAffinity",
            "requiredDuringSchedulingIgnoredDuringExecution",
            "nodeSelectorTerms",
        ):
            terms = terms.get(key) if isinstance(terms, dict) else None
        if not isinstance(terms, list):
            continue
        for term in terms:
            expressions = term.get("matchExpressions") if isinstance(term, dict) else None
            if not isinstance(expressions, list):
                continue
            for expression in expressions:
                if not isinstance(expression, dict) or expression.get("operator") not in (
                    "In",
                    "NotIn",
                ):
                    continue
                members = expression.get("values")
                if isinstance(members, list) and all(isinstance(item, str) for item in members):
                    expression["values"] = sorted(members)


def _canonical_soperator_values(values: Any) -> Any:
    """Compare native selector and restored structured partition semantics."""
    import copy

    result = copy.deepcopy(values)
    if not isinstance(result, dict):
        return result
    _canonical_node_filter_members(result)
    partition: Any = result
    for key in ("slurmCluster", "overrideValues", "partitionConfiguration"):
        if not isinstance(partition, dict):
            return result
        partition = partition.get(key)
    if not isinstance(partition, dict) or partition.get("configType") != "structured":
        return result
    rows = partition.get("partitions")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("ambiguous structured partition configuration")
    custom = result["slurmCluster"]["overrideValues"].get("customSlurmConfig", "")
    if any(row.get("name") == "DEFAULT" for row in rows) or (
        isinstance(custom, str) and re.search(r"(?i)\bPartitionName\s*=\s*DEFAULT\b", custom)
    ):
        # Explicit Slurm inheritance can override the native defaults. Keep those
        # configurations exact instead of guessing each partition's effective values.
        return result
    for row in rows:
        text = row.get("config", "")
        if not isinstance(text, str):
            raise ValueError("ambiguous structured partition configuration")
        fields = {}
        for token in text.split():
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)=([^\s'\"#]+)", token)
            if match is None or match[1].lower() in fields:
                raise ValueError("ambiguous structured partition configuration")
            fields[match[1].lower()] = match[2]
        # Slurm 25.11 slurm.conf: omitted AllowGroups means ALL; State defaults UP.
        fields.setdefault("allowgroups", "ALL")
        fields.setdefault("state", "UP")
        row["config"] = fields
    return result


def _canonical_soperator_fields(fields: Mapping[str, Any], kind: str) -> dict[str, Any]:
    import copy

    result = copy.deepcopy(dict(fields))
    if kind == "HelmRelease":
        result.setdefault("spec", {}).setdefault("suspend", False)
    if kind == "HelmRelease" and "values" in result.get("spec", {}):
        result["spec"]["values"] = _canonical_soperator_values(result["spec"]["values"])
    elif kind == "ConfigMap" and "values.yaml" in result.get("data", {}):
        values = yaml.safe_load(result["data"]["values.yaml"])
        # Keep the entire values document exact, including added or removed keys.
        result["data"]["values.yaml"] = json.dumps(
            _canonical_soperator_values(values), sort_keys=True, separators=(",", ":")
        )
    return result


def _canonical_volume_fields(fields: Mapping[str, Any], kind: str) -> dict[str, Any]:
    import copy

    from .mk8s_gpu import _parse_kubernetes_quantity

    result = copy.deepcopy(dict(fields))
    paths = (
        [("spec", "capacity")]
        if kind == "PersistentVolume"
        else [("spec", "resources", "requests"), ("spec", "resources", "limits")]
    )
    for path in paths:
        row: Any = result
        for key in path:
            row = row.get(key) if isinstance(row, dict) else None
        if isinstance(row, dict) and "storage" in row:
            quantity = _parse_kubernetes_quantity(row["storage"])
            if quantity is not None:
                row["storage"] = quantity
    return result


def _canonical_workload_fields(fields: Mapping[str, Any], kind: str) -> dict[str, Any]:
    import copy

    result = copy.deepcopy(dict(fields))
    path = (
        ("spec",)
        if kind == "Pod"
        else ("spec", "jobTemplate", "spec", "template", "spec")
        if kind == "CronJob"
        else ("spec", "template", "spec")
    )
    pod: Any = result
    for key in path:
        pod = pod.get(key) if isinstance(pod, dict) else None
    if isinstance(pod, dict):
        for field in ("containers", "initContainers", "ephemeralContainers"):
            for container in pod.get(field, []):
                for variable in container.get("env", []):
                    if "valueFrom" not in variable:
                        variable.setdefault("value", "")
    return result


def verify_desired_target(
    cli: Any,
    *,
    generation: Any,
    target_ref: str,
    kube_env: Mapping[str, str],
    previous: Any = None,
    documents_projection: Any = None,
    protected_only: bool = False,
    resource_kinds: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Verify frozen owned fields and current readiness; persist digests only."""
    desired = target_documents(
        generation,
        target_ref,
        documents_projection=documents_projection,
        protected_only=protected_only,
    )
    if resource_kinds is not None:
        desired = {key: doc for key, doc in desired.items() if doc["kind"] in resource_kinds}
        if not desired:
            raise RuntimeError("Desired resource verification inventory is empty")
    soperator_values = _soperator_values_keys(desired)
    prior = (
        target_documents(previous, target_ref)
        if previous is not None
        and any(
            row.get("target_ref") == target_ref
            for row in previous.manifest.get("deploy", {}).get("targets", [])
        )
        else {}
    )
    evidence = {}
    for key, document in desired.items():
        meta = document["metadata"]
        namespace, name = meta.get("namespace", ""), meta["name"]
        group = document["apiVersion"].split("/")[0] if "/" in document["apiVersion"] else ""
        resource = document["kind"] + ("." + group if group else "")
        response = cli._run_soperator_upgrade_kubectl(
            namespace or "default",
            [
                "get",
                resource,
                name,
                "-o",
                "json",
                "--ignore-not-found=true",
                "--request-timeout=20s",
            ],
            kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
            extra_env=kube_env,
            timeout_seconds=60,
            check=True,
        )
        if not response.stdout.strip():
            raise DesiredStateNotConverged(f"Desired resource is absent: {key}")
        live = json.loads(response.stdout)
        metadata = live.get("metadata", {})
        if (
            not metadata.get("uid")
            or metadata.get("name") != name
            or metadata.get("namespace", "") != namespace
        ):
            raise RuntimeError("Desired resource live identity differs or is unavailable")
        if protected_only and (
            metadata.get("deletionTimestamp")
            or any(
                not _owned_matches(meta.get(field, {}), metadata.get(field, {}), {})
                for field in ("labels", "annotations")
            )
            or metadata.get("ownerReferences", []) != meta.get("ownerReferences", [])
        ):
            raise RuntimeError(f"Accepted resource ownership changed: {key}")
        fields = ("spec", "data", "binaryData", "type", "immutable", "rules", "roleRef", "subjects")
        import copy

        expected = {field: copy.deepcopy(document[field]) for field in fields if field in document}
        observed = {field: live[field] for field in fields if field in live}
        previous_fields = {
            field: prior[key][field] for field in fields if key in prior and field in prior[key]
        }
        # values is an entire cxcli-owned map, including a removed/empty map.
        if document["kind"] == "HelmRelease":
            expected.setdefault("spec", {}).setdefault("values", {})
        comparison: Sequence[Mapping[str, Any]] = (expected, observed, previous_fields)
        if key in soperator_values:
            comparison = tuple(
                _canonical_soperator_fields(value, document["kind"]) for value in comparison
            )
        elif document["apiVersion"] == "v1" and document["kind"] in {
            "PersistentVolume",
            "PersistentVolumeClaim",
        }:
            comparison = tuple(
                _canonical_volume_fields(value, document["kind"]) for value in comparison
            )
        elif (document["apiVersion"], document["kind"]) in {
            ("v1", "Pod"),
            ("apps/v1", "Deployment"),
            ("apps/v1", "DaemonSet"),
            ("apps/v1", "StatefulSet"),
            ("apps/v1", "ReplicaSet"),
            ("batch/v1", "Job"),
            ("batch/v1", "CronJob"),
        }:
            comparison = tuple(
                _canonical_workload_fields(value, document["kind"]) for value in comparison
            )
        if not _owned_matches(*comparison):
            raise DesiredStateNotConverged(f"Desired resource settings have not converged: {key}")
        if document["kind"] in {
            "HelmRelease",
            "HelmRepository",
            "OCIRepository",
            "GitRepository",
            "Kustomization",
        } and not (
            document["kind"] == "HelmRepository" and document.get("spec", {}).get("type") == "oci"
        ):
            status = live.get("status", {})
            conditions = status.get("conditions", [])
            ready: Mapping[str, Any] = next(
                (row for row in conditions if row.get("type") == "Ready"), {}
            )
            if (
                ready.get("status") != "True"
                or ready.get("observedGeneration", status.get("observedGeneration"))
                != metadata.get("generation")
                or not metadata.get("generation")
            ):
                raise DesiredStateNotConverged(
                    f"Desired resource readiness is stale or unavailable: {key}"
                )
        evidence[key] = {
            "uid": metadata["uid"],
            "desiredSha256": digest(expected),
            "observedSha256": digest(observed),
        }
    for key in prior.keys() - desired.keys():
        document = prior[key]
        meta = document["metadata"]
        group = document["apiVersion"].split("/")[0] if "/" in document["apiVersion"] else ""
        resource = document["kind"] + ("." + group if group else "")
        response = cli._run_soperator_upgrade_kubectl(
            meta.get("namespace") or "default",
            [
                "get",
                resource,
                meta["name"],
                "-o",
                "json",
                "--ignore-not-found=true",
                "--request-timeout=20s",
            ],
            kube_context=kube_env.get(cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV),
            extra_env=kube_env,
            timeout_seconds=60,
            check=True,
        )
        if response.stdout.strip():
            raise DesiredStateNotConverged(f"Removed owned resource remains live: {key}")
    return {"desiredBundle": digest(desired), "resources": evidence, "ready": True}
