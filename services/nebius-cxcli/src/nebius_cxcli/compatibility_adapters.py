"""Hard compatibility evidence collected from native tools and provider APIs."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from . import kubernetes_process
from .compatibility_matrix import digest
from .oci_image import is_immutable_oci_image_reference


class _RenderedChartLoader(yaml.SafeLoader):
    """Read native chart manifests without tagging bare CRD enum '=' values."""

    yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:value"]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def _rendered_chart_documents(rendered: str) -> list[Any]:
    documents = list(yaml.load_all(rendered, Loader=_RenderedChartLoader))
    _validate_container_inputs(documents)
    return documents


def _validate_container_inputs(documents: list[Any]) -> None:
    """Reject malformed SHA-256 images and duplicate native Pod environment keys."""
    pending = list(documents)
    while pending:
        document = pending.pop()
        if not isinstance(document, Mapping):
            continue
        identity = (document.get("apiVersion"), document.get("kind"))
        spec = document.get("spec", {})
        if identity == ("v1", "List"):
            pending.extend(document.get("items", []))
            continue
        if identity == ("v1", "Pod"):
            pod = spec
        elif identity in {
            ("apps/v1", "Deployment"),
            ("apps/v1", "StatefulSet"),
            ("apps/v1", "DaemonSet"),
            ("apps/v1", "ReplicaSet"),
            ("v1", "ReplicationController"),
            ("batch/v1", "Job"),
        }:
            pod = spec.get("template", {}).get("spec", {})
        elif identity == ("batch/v1", "CronJob"):
            pod = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
        else:
            continue
        for field in ("containers", "initContainers", "ephemeralContainers"):
            for container in pod.get(field) or []:
                image = container.get("image")
                if (
                    isinstance(image, str)
                    and "@sha256:" in image
                    and not is_immutable_oci_image_reference(image)
                ):
                    metadata = document.get("metadata", {})
                    raise ValueError(
                        f"Rendered {document['kind']} "
                        f"{metadata.get('namespace', 'default')}/{metadata.get('name', '')} "
                        f"container {container.get('name', '')} has malformed SHA-256 image digest"
                    )
                names: set[str] = set()
                for variable in container.get("env") or []:
                    name = variable["name"]
                    if name in names:
                        metadata = document.get("metadata", {})
                        raise ValueError(
                            f"Rendered {document['kind']} "
                            f"{metadata.get('namespace', 'default')}/{metadata.get('name', '')} "
                            f"container {container.get('name', '')} has duplicate env name {name}"
                        )
                    names.add(name)


def receipt(
    subject: Mapping, evaluator: str, inputs: Any, *, outcome: str = "pass", reason: str
) -> dict:
    return {
        "subject_sha256": digest(subject),
        "evaluator": evaluator,
        "input_sha256": digest(inputs),
        "outcome": outcome,
        "reason": reason,
    }


def helm_constraint(metadata: Mapping, kubernetes: str, *, helm: str = "helm") -> dict[str, Any]:
    """Use Helm's own SemVer implementation, including OR/hyphen/prerelease rules."""
    constraint = metadata.get("kubeVersion")
    # Native Helm exposes an omitted optional Chart.KubeVersion as an empty string.
    if constraint is None or constraint == "":
        return {"outcome": "not_declared", "constraint": None}
    if not isinstance(constraint, str) or not constraint.strip() or not kubernetes:
        raise ValueError("Helm Kubernetes constraint or target version is missing/malformed")
    with tempfile.TemporaryDirectory(prefix="cxcli-kube-constraint-") as directory:
        root = Path(directory)
        (root / "Chart.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "v2",
                    "name": "compatibility",
                    "version": "0.1.0",
                    "kubeVersion": constraint,
                }
            )
        )
        result = kubernetes_process.run(
            [helm, "template", "compatibility", str(root), "--kube-version", kubernetes],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            # Do not expose chart values or arbitrary tool output in a report.
            raise ValueError(
                f"Helm rejected kubeVersion {constraint!r} for Kubernetes {kubernetes}"
            )
    version = kubernetes_process.run(
        [helm, "version", "--short"], capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()
    return {"outcome": "pass", "constraint": constraint, "kubernetes": kubernetes, "helm": version}


def chart_evidence(subject: Mapping, row: Mapping) -> dict[str, Any]:
    from .compatibility_artifacts import materialize_frozen_chart, resolve_chart_input, tree_digest
    from .components import component_entry_chart_name, component_lookup
    from .flux_render import _local_chart_path_from_entry, _runtime_app_chart_name
    from .helm_client import HelmChartReference

    entry = component_lookup("apps").get(subject["component_id"])
    if entry is None:
        raise ValueError("Selected chart has no canonical catalog entry")
    local = _local_chart_path_from_entry(entry)
    reference = HelmChartReference(
        chart_name=_runtime_app_chart_name(
            chart_node=dict(row),
            entry_id=entry.id,
            configured_chart_name=component_entry_chart_name(entry),
            local_chart_path=local,
        ),
        chart_repo=str(row.get("repo") or ("" if local else subject["source"])),
        chart_version=str(subject["chart_version"]),
    )
    chart_input = resolve_chart_input(reference)
    with materialize_frozen_chart(chart_input) as directory:
        metadata = yaml.safe_load((directory / "Chart.yaml").read_text())
        if (
            not isinstance(metadata, Mapping)
            or not metadata.get("version")
            or (
                subject["chart_version"]
                and str(metadata.get("version", "")).removeprefix("v")
                != str(subject["chart_version"]).removeprefix("v")
            )
        ):
            raise ValueError("Chart compatibility artifact version differs from selected version")
        constraint = helm_constraint(
            metadata, str(subject.get("kubernetes_version") or subject["kubernetes_minor"])
        )
        values_file = directory.parent / "compatibility-values.yaml"
        values_file.write_text(yaml.safe_dump(dict(row.get("values") or {})))
        values_file.chmod(0o600)
        command = [
            "helm",
            "template",
            str(row.get("release-name") or row["id"]),
            str(directory),
            "--namespace",
            str(row.get("namespace") or "default"),
            "--values",
            str(values_file),
        ]
        if subject.get("kubernetes_version") or subject.get("kubernetes_minor"):
            command.extend(
                [
                    "--kube-version",
                    str(subject.get("kubernetes_version") or subject["kubernetes_minor"]),
                ]
            )
        rendered = kubernetes_process.run(command, capture_output=True, text=True, timeout=120)
        if rendered.returncode:
            raise ValueError(
                "Selected chart or an enabled dependency rejected its effective values/Kubernetes"
            )
        dependencies = enabled_chart_constraints(
            directory,
            values_file,
            str(subject.get("kubernetes_version") or subject["kubernetes_minor"]),
        )
        documents = _rendered_chart_documents(rendered.stdout)
        images = rendered_image_references(documents)
        evidence = {
            "artifact_sha256": tree_digest(chart_input),
            "oci_digest": chart_input["oci_digest"],
            "metadata": dict(metadata),
            "constraint": constraint,
            "enabled_dependencies": dependencies,
            "rendered_images": sorted(images),
            "effective_values_sha256": digest(dict(row.get("values") or {})),
            "helm": kubernetes_process.run(
                ["helm", "version", "--short"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            ).stdout.strip(),
        }

        # Keep the exact selected distribution identity; appVersion is evidence,
        # never an automatic mapping to the vendor's upstream distribution.
        return {
            **receipt(
                subject,
                "exact_artifact_kube_version",
                evidence,
                outcome=constraint["outcome"],
                reason="Exact chart metadata checked with native Helm",
            ),
            "artifact": evidence,
            "chart_input": chart_input,
        }


def rendered_image_references(documents) -> list[str]:
    images = set()

    def collect_images(value):
        if isinstance(value, Mapping):
            repository, name = value.get("repository"), value.get("image")
            tag = value.get("version") or value.get("tag")
            if isinstance(repository, str) and isinstance(tag, str):
                image = repository.rstrip("/")
                if isinstance(name, str) and name and "/" not in name:
                    image += "/" + name
                if "/" in image:
                    images.add(image + ":" + tag)
            for key, child in value.items():
                if key == "image" and isinstance(child, str) and "/" in child:
                    images.add(child)
                collect_images(child)
        elif isinstance(value, list):
            for child in value:
                collect_images(child)

    collect_images(documents)
    return sorted(images)


def enabled_chart_constraints(directory: Path, values_file: Path, kubernetes: str) -> list[dict]:
    """Let native Helm resolve conditions, tags, aliases and nested values.

    Helm checks the root chart's kubeVersion, but not the enabled children's.
    A private inspection template exposes only their metadata; it is removed
    before the artifact is used, and never changes the deployable chart.
    """
    marker = "cxcli_compatibility_" + uuid.uuid4().hex
    template = directory / "templates" / (marker + ".yaml")
    template.parent.mkdir(exist_ok=True)
    template.write_text(
        '{{- define "' + marker + '" -}}\n'
        "{{- range .Subcharts }}\n---\n"
        '{{ dict "name" .Chart.Name "version" .Chart.Version "kubeVersion" .Chart.KubeVersion | toJson }}\n'
        '{{ include "' + marker + '" . }}\n{{- end }}\n{{- end }}\n'
        '{{ include "' + marker + '" . }}\n---\n{}\n'
    )
    try:
        command = [
            "helm",
            "template",
            "compatibility",
            str(directory),
            "--values",
            str(values_file),
            "--show-only",
            "templates/" + template.name,
        ]
        if kubernetes:
            command.extend(["--kube-version", kubernetes])
        result = kubernetes_process.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Helm --show-only rejects an empty template when no dependencies
            # exist, so emit a sentinel below and always get a document.
            raise ValueError("Cannot resolve enabled chart dependencies with native Helm")
        return [
            {"metadata": dict(metadata), "constraint": helm_constraint(metadata, kubernetes)}
            for metadata in yaml.safe_load_all(result.stdout)
            if isinstance(metadata, Mapping) and metadata.get("name")
        ]
    finally:
        template.unlink()


def terraform_evidence(subject: Mapping, directory: Path) -> dict:
    inputs: dict[str, Any] = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and (
            path.name.endswith((".tf", ".tf.json", ".tfvars", ".tfvars.json"))
            or path.name == ".terraform.lock.hcl"
        ):
            if path.is_symlink():
                raise ValueError("Terraform compatibility input contains a symlink")
            inputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if ".terraform.lock.hcl" not in inputs:
        raise ValueError("Resolved Terraform provider lock is required for compatibility admission")
    modules_file = directory / ".terraform/modules/modules.json"
    if modules_file.is_file():
        modules = json.loads(modules_file.read_text())
        for module in modules.get("Modules", []):
            if not module.get("Key"):
                continue
            root = directory / module["Dir"]
            files = {}
            for path in sorted(root.rglob("*")):
                if ".git" in path.relative_to(root).parts:
                    continue
                if path.is_symlink():
                    raise ValueError("Resolved Terraform module contains a symlink")
                if path.is_file():
                    files[path.relative_to(root).as_posix()] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
            if not files:
                raise ValueError("Resolved Terraform module has no verifiable source files")
            inputs["module:" + module["Key"]] = {
                "source": module.get("Source"),
                "version": module.get("Version"),
                "files": files,
            }
    tool = kubernetes_process.run(
        ["terraform", "version", "-json"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    tool_identity = json.loads(tool.stdout)
    inputs["tool"] = {
        key: tool_identity[key] for key in ("terraform_version", "platform", "provider_selections")
    }
    # The caller invokes this only after the exact tree's native init/validate.
    return receipt(
        subject,
        "resolved_terraform_constraints",
        inputs,
        reason="Native Terraform validation passed for these resolved locked inputs",
    )


def soperator_evidence(subject: Mapping, paths: Any) -> tuple[list[dict], list[dict]]:
    from .deploy_targets import flux_target_dir
    from .soperator_release import load_soperator_release_snapshot, soperator_release_snapshot_path
    from .soperator_release_artifacts import (
        _chart_file_map,
        render_soperator_consumers,
        verify_soperator_release_artifacts,
    )
    from .soperator_release_resolver import frozen_soperator_release_from_snapshot
    from .soperator_release_source import default_soperator_source_cache_root

    snapshot = load_soperator_release_snapshot(
        soperator_release_snapshot_path(paths.reports_dir, subject["instance_id"])
    )
    if snapshot.release != subject["release"]:
        raise ValueError("Soperator compatibility snapshot differs from selected release")
    frozen = frozen_soperator_release_from_snapshot(snapshot)
    candidates = list(
        flux_target_dir(paths, subject["instance_id"]).rglob(
            "configmap-terraform-fluxcd-values.yaml"
        )
    )
    if len(candidates) != 1:
        raise ValueError("Soperator compatibility requires exact rendered umbrella values")
    cm = yaml.safe_load(candidates[0].read_text())
    values = yaml.safe_load(cm["data"]["values.yaml"])
    from .soperator_adapter import load_soperator_adapter_documents

    adapter_docs = load_soperator_adapter_documents(flux_target_dir(paths, subject["instance_id"]))
    artifact = verify_soperator_release_artifacts(
        snapshot, frozen.source, values=values, adapter_documents=adapter_docs
    )
    consumers = render_soperator_consumers(
        snapshot, frozen.source, values, adapter_documents=adapter_docs
    )
    enabled = {doc["metadata"]["name"] for doc in consumers}
    child_specs = {doc["metadata"]["name"]: doc.get("spec", {}) for doc in consumers}
    child_values = {
        doc["metadata"]["name"]: doc.get("spec", {}).get("values", {}) for doc in consumers
    }
    evidence = [
        receipt(
            subject,
            "frozen_soperator_release_contract",
            asdict(artifact),
            reason="Source, package, child values and release contract verified",
        )
    ]
    children = []
    constraints = [
        {
            "helm": kubernetes_process.run(
                ["helm", "version", "--short"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            ).stdout.strip()
        }
    ]
    cache = default_soperator_source_cache_root() / "charts"
    umbrella_package = cache / (snapshot.umbrella.package_sha256.removeprefix("sha256:") + ".tgz")
    umbrella_metadata = yaml.safe_load(_chart_file_map(umbrella_package)["Chart.yaml"])
    constraints.append(
        helm_constraint(
            umbrella_metadata, subject.get("kubernetes_version") or subject["kubernetes_minor"]
        )
    )
    for node in snapshot.release_graph:
        if node.release_name not in enabled:
            continue
        chart = (snapshot.charts if node.owner == "upstream" else snapshot.third_party_charts)[
            node.chart_key
        ]
        package = cache / (chart.package_sha256.removeprefix("sha256:") + ".tgz")
        if (
            package.is_symlink()
            or "sha256:" + hashlib.sha256(package.read_bytes()).hexdigest() != chart.package_sha256
        ):
            raise ValueError("Soperator chart compatibility package integrity changed")
        metadata = yaml.safe_load(_chart_file_map(package)["Chart.yaml"])
        result = helm_constraint(
            metadata, subject.get("kubernetes_version") or subject["kubernetes_minor"]
        )
        with tempfile.TemporaryDirectory(prefix="cxcli-child-constraints-") as directory:
            root = Path(directory) / "chart"
            root.mkdir()
            for name, data in _chart_file_map(package).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            values_file = Path(directory) / "values.yaml"
            values_file.write_text(yaml.safe_dump(child_values[node.release_name]))
            values_file.chmod(0o600)
            dependencies = enabled_chart_constraints(
                root, values_file, subject.get("kubernetes_version") or subject["kubernetes_minor"]
            )
            rendered = kubernetes_process.run(
                [
                    "helm",
                    "template",
                    str(child_specs[node.release_name].get("releaseName") or node.release_name),
                    str(root),
                    "--namespace",
                    str(child_specs[node.release_name].get("targetNamespace") or "flux-system"),
                    "--values",
                    str(values_file),
                    "--kube-version",
                    subject.get("kubernetes_version") or subject["kubernetes_minor"],
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if rendered.returncode:
                raise ValueError("Bundled chart rejected its effective values/Kubernetes")
            images = rendered_image_references(_rendered_chart_documents(rendered.stdout))

        child = {
            "instance_id": subject["instance_id"] + "/" + node.release_name,
            "component_id": node.chart_key,
            "kind": "helm",
            "owner": "soperator",
            "distribution": "soperator-bundled:" + node.chart_key,
            "chart_version": chart.version,
            "chart_app_version": metadata.get("appVersion"),
            "package_sha256": chart.package_sha256,
            "parent_sha256": digest(subject),
            "kubernetes_minor": subject["kubernetes_minor"],
            "required_adapters": ["exact_artifact_kube_version"],
        }
        children.append(child)
        evidence.append(
            {
                **receipt(
                    child,
                    "exact_artifact_kube_version",
                    {"package": chart.package_sha256, **result},
                    outcome=result["outcome"],
                    reason="Bundled dependency checked against target Kubernetes with native Helm",
                ),
                "artifact": {
                    "metadata": dict(metadata),
                    "package_sha256": chart.package_sha256,
                    "enabled_dependencies": dependencies,
                },
            }
        )
        for image in images:
            operand = {
                "instance_id": child["instance_id"] + "/image/" + digest(image)[7:23],
                "component_id": child["component_id"] + "/operand",
                "kind": "operand",
                "owner": "soperator",
                "distribution": "unknown",
                "image_reference": image,
                "parent_sha256": digest(child),
                "kubernetes_minor": child["kubernetes_minor"],
                "required_adapters": ["artifact_bound_operand_constraints"],
            }
            children.append(operand)
            evidence.append(
                receipt(
                    operand,
                    "artifact_bound_operand_constraints",
                    {"package": chart.package_sha256, "image": image},
                    outcome="not_declared",
                    reason="Enabled image selected by frozen upstream child render; no additional artifact-declared operand constraint",
                )
            )
        constraints.append({"package": chart.package_sha256, **result})
    evidence.append(
        {
            **receipt(
                subject,
                "exact_artifact_kube_version",
                constraints,
                reason="All enabled release children passed declared Kubernetes constraints",
            ),
            "artifact": {
                "metadata": dict(umbrella_metadata),
                "package_sha256": snapshot.umbrella.package_sha256,
            },
        }
    )
    return children, evidence


def provider_evidence(subject: Mapping, config: Mapping) -> list[dict]:
    from nebius.api.nebius.mk8s.v1 import (
        ClusterServiceClient,
        GetNodeGroupCompatibilityMatrixRequest,
        ListClusterControlPlaneVersionsRequest,
        NodeGroupServiceClient,
    )

    from .component_instances import component_instance_id
    from .mk8s_node_groups import iter_node_groups
    from .mk8s_upgrade import compatibility_choices_from_response, parse_k8s_version
    from .provider_options import _provider_request_kwargs
    from .sdk_auth import init_nebius_sdk

    row = next(
        row
        for row in config["infra"]["components"]
        if component_instance_id(row) == subject["instance_id"] and row["id"] == "mk8s"
    )
    inputs = row.get("inputs", {})
    project = (
        inputs.get("cluster", {}).get("parent_id") or config["client_info"]["nebius"]["project_id"]
    )
    sdk = init_nebius_sdk(parent_id=project, context="Compatibility admission")
    request_options: dict[str, Any] = dict(_provider_request_kwargs())
    try:
        response = (
            ClusterServiceClient(sdk)
            .list_control_plane_versions(
                ListClusterControlPlaneVersionsRequest(), **request_options
            )
            .wait()
        )
        versions = [str(item.version) for item in response.items]
        minor = parse_k8s_version(subject["kubernetes_minor"]).minor_text
        if minor not in {parse_k8s_version(version).minor_text for version in versions}:
            raise ValueError(f"Nebius does not admit Kubernetes {minor}")
        rows = []
        for group in iter_node_groups(inputs):
            if not group.os or not group.platform:
                raise ValueError(
                    f"Node group {group.key} requires an explicit OS and platform for compatibility admission"
                )
            group_version = str(inputs["node_groups"][group.key].get("version") or minor)
            parsed = parse_k8s_version(group_version)
            target = parse_k8s_version(minor)
            if (
                parsed.major != target.major
                or parsed.minor > target.minor
                or target.minor - parsed.minor > 3
            ):
                raise ValueError("Kubernetes node/control-plane version skew is unsupported")
            group_response = (
                NodeGroupServiceClient(sdk)
                .get_compatibility_matrix(
                    GetNodeGroupCompatibilityMatrixRequest(
                        cluster_kubernetes_version=group_version, platform=group.platform
                    ),
                    **request_options,
                )
                .wait()
            )
            choices = compatibility_choices_from_response(group_response, platform=group.platform)
            preset = group.gpu_stack_preset if group.gpu_stack_source == "nebius_image" else ""
            matches = [
                choice
                for choice in choices
                if choice.platform == group.platform
                and choice.os == group.os
                and choice.drivers_preset == preset
            ]
            if not matches:
                raise ValueError(
                    f"Nebius node group compatibility rejected {group.key} at {group_version}"
                )
            rows.append(
                {
                    "group": group.key,
                    "version": group_version,
                    "matches": [asdict(choice) for choice in matches],
                }
            )
        return [
            receipt(
                subject,
                "nebius_cluster_version_api",
                {"versions": versions, "selected": minor},
                reason="Provider control-plane admission passed",
            ),
            {
                **receipt(
                    subject,
                    "nebius_node_group_compatibility_api",
                    rows,
                    reason="Each selected node template matched current provider compatibility",
                ),
                "node_groups": rows,
            },
        ]
    finally:
        sdk.sync_close()
