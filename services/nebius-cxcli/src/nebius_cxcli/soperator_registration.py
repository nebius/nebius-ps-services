"""Canonical Soperator registration and read-only cluster inventory."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .component_instances import normalize_component_token
from .deploy_targets import deploy_target_is_external_mk8s
from .runtime_config import to_plain_data
from .soperator_artifacts import SoperatorClusterArtifactIdentity
from .soperator_discovery import (
    write_soperator_discovery_bundle,
)
from .soperator_flux_graph import (
    SOPERATOR_GRAPH_LABEL,
    SOPERATOR_GRAPH_LABEL_VALUE,
    target_soperator_release_name,
)

SOPERATOR_REGISTRATION_SCHEMA = "nebius-cxcli.soperator-registration.v3"

SOPERATOR_REGISTRATION_STATE_SUPPORTED = "existing-soperator-supported"

SOPERATOR_NODESET_LABEL_KEYS = (
    "slurm.nebius.ai/nodeset-name",
    "slurm.nebius.ai/nodeset",
)

SOPERATOR_WORKER_ROLE_PREFIX = "worker"

SOPERATOR_CRD_RESOURCE_KINDS = (
    ("activechecks.slurm.nebius.ai", "activechecks"),
    ("jailedconfigs.slurm.nebius.ai", "jailedconfigs"),
    ("slurmclusters.slurm.nebius.ai", "slurmclusters"),
    ("nodeconfigurators.slurm.nebius.ai", "nodeconfigurators"),
    ("nodesetpowerstates.slurm.nebius.ai", "nodesetpowerstates"),
    ("nodesets.slurm.nebius.ai", "nodesets"),
)

SOPERATOR_COMPATIBLE_RELEASE_NAMES = frozenset({"soperator", "slurm-operator"})

SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES = frozenset({"soperator-controller"})

SOPERATOR_COMPATIBLE_CHART_IDENTITIES = frozenset({"soperator", "helm-soperator", "slurm-operator"})

GPU_STACK_HELM_DISCOVERY_NAMESPACES = ("nvidia-gpu-operator", "nvidia-network-operator")

_KUBE_RBAC_PROXY_V015_REGISTRY_ALIASES = frozenset(
    {
        "gcr.io/kubebuilder/kube-rbac-proxy:v0.15.0",
        "quay.io/brancz/kube-rbac-proxy:v0.15.0",
    }
)

_KRUISE_CONTROLLER_MUTATED_WEBHOOKS = frozenset(
    {
        "kruise-mutating-webhook-configuration",
        "kruise-validating-webhook-configuration",
    }
)

_KRUISE_STATEFULSET_CRD = "statefulsets.apps.kruise.io"

_FLUX_HELM_RELEASE_CRD = "helmreleases.helm.toolkit.fluxcd.io"

_FLUX_SOPERATOR_MAIN_RELEASE = target_soperator_release_name("soperator-fluxcd-soperator")


@dataclass(frozen=True)
class SoperatorProvenanceEvidence:
    method: str
    live_manifest_sha256: str
    rendered_manifest_sha256: str
    owned_object_graph_sha256: str


def _manifest_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical_cpu_quantity(value: object) -> object:
    token = str(value or "").strip()
    try:
        quantity = Decimal(token[:-1]) / 1000 if token.endswith("m") else Decimal(token)
    except (InvalidOperation, ValueError):
        return value
    rendered = format(quantity.normalize(), "f")
    return "0" if rendered in {"-0", ""} else rendered


def _normalized_manifest_object(
    value: Mapping[str, Any],
    *,
    strip_generated: bool = False,
) -> dict[str, Any]:
    normalized = copy.deepcopy(to_plain_data(dict(value)))
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        normalized["metadata"] = metadata
    for key in (
        "creationTimestamp",
        "deletionGracePeriodSeconds",
        "deletionTimestamp",
        "generation",
        "managedFields",
        "resourceVersion",
        "selfLink",
        "uid",
    ):
        metadata.pop(key, None)
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        for key in tuple(annotations):
            if str(key).startswith(("meta.helm.sh/", "deployment.kubernetes.io/revision")):
                annotations.pop(key, None)
        if not annotations:
            metadata.pop("annotations", None)
    labels = metadata.get("labels")
    if isinstance(labels, dict):
        labels.pop("helm.sh/chart", None)
        if strip_generated:
            for key in tuple(labels):
                if str(key).startswith("helm.toolkit.fluxcd.io/"):
                    labels.pop(key, None)
        if not labels:
            metadata.pop("labels", None)
    kind = str(normalized.get("kind") or "")
    spec = normalized.get("spec")
    spec_map = spec if isinstance(spec, dict) else {}
    template = spec_map.get("template")
    template_map = template if isinstance(template, dict) else {}
    pod_spec = template_map.get("spec")
    pod_spec_map = pod_spec if isinstance(pod_spec, dict) else {}
    if pod_spec_map.get("hostNetwork") is False:
        pod_spec_map.pop("hostNetwork", None)
    for container_key in ("containers", "initContainers"):
        containers = pod_spec_map.get(container_key)
        if not isinstance(containers, list):
            continue
        for container in containers:
            if not isinstance(container, dict):
                continue
            image = str(container.get("image") or "").strip()
            if image in _KUBE_RBAC_PROXY_V015_REGISTRY_ALIASES:
                container["image"] = "kube-rbac-proxy-registry-relocation:v0.15.0"
            resources = container.get("resources")
            resources_map = resources if isinstance(resources, dict) else {}
            for resource_key in ("limits", "requests"):
                resource_values = resources_map.get(resource_key)
                if isinstance(resource_values, dict) and "cpu" in resource_values:
                    resource_values["cpu"] = _canonical_cpu_quantity(resource_values["cpu"])
    if kind == "Service" and spec_map.get("type") == "ClusterIP":
        spec_map.pop("type", None)
    if strip_generated and kind == "Service":
        spec = normalized.get("spec")
        if isinstance(spec, dict):
            if spec.get("clusterIP") != "None":
                spec.pop("clusterIP", None)
            if spec.get("clusterIPs") != ["None"]:
                spec.pop("clusterIPs", None)
            spec.pop("ipFamilies", None)
            spec.pop("ipFamilyPolicy", None)
    if strip_generated and kind == "ServiceAccount":
        normalized.pop("secrets", None)
    if (
        strip_generated
        and kind == "Secret"
        and str(metadata.get("namespace") or "") == "kruise-system"
        and str(metadata.get("name") or "") == "kruise-webhook-certs"
    ):
        data = normalized.get("data")
        allowed_keys = {
            "ca-cert.pem",
            "ca-key.pem",
            "cert.pem",
            "key.pem",
            "tls.crt",
            "tls.key",
        }
        if isinstance(data, Mapping) and set(map(str, data)).issubset(allowed_keys):
            normalized.pop("data", None)
    normalized.pop("status", None)
    return normalized


def _controller_owned_webhook_template(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> Mapping[str, Any]:
    kind = str(expected.get("kind") or "")
    metadata = _mapping_value(expected.get("metadata"))
    name = str(metadata.get("name") or "")
    if (
        kind not in {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration"}
        or name not in _KRUISE_CONTROLLER_MUTATED_WEBHOOKS
    ):
        return observed
    expected_annotations = _mapping_value(metadata.get("annotations"))
    observed_metadata = _mapping_value(observed.get("metadata"))
    observed_annotations = _mapping_value(observed_metadata.get("annotations"))
    expected_template = str(expected_annotations.get("template") or "")
    observed_template = str(observed_annotations.get("template") or "")
    if expected_template or not observed_template:
        return observed
    parsed_template = yaml.safe_load(observed_template)
    expected_webhooks = expected.get("webhooks")
    active_webhooks = observed.get("webhooks")
    if (
        not isinstance(parsed_template, list)
        or not isinstance(expected_webhooks, list)
        or not isinstance(active_webhooks, list)
        or not active_webhooks
        or not _manifest_value_is_live_subset(expected_webhooks, parsed_template)
    ):
        raise RuntimeError(f"live Kruise webhook template differs for {kind}/{name}")
    template_by_name = {
        str(item.get("name") or ""): item
        for item in parsed_template
        if isinstance(item, Mapping) and str(item.get("name") or "")
    }
    if len(template_by_name) != len(parsed_template) or any(
        not isinstance(item, Mapping)
        or str(item.get("name") or "") not in template_by_name
        or not _manifest_value_is_live_subset(
            template_by_name[str(item.get("name") or "")],
            item,
        )
        for item in active_webhooks
    ):
        raise RuntimeError(
            f"active Kruise webhook state is not derived from its template for {kind}/{name}"
        )
    normalized = copy.deepcopy(dict(observed))
    normalized["webhooks"] = copy.deepcopy(parsed_template)
    normalized_metadata = normalized.get("metadata")
    normalized_metadata = normalized_metadata if isinstance(normalized_metadata, dict) else {}
    normalized["metadata"] = normalized_metadata
    normalized_annotations = normalized_metadata.get("annotations")
    normalized_annotations = (
        normalized_annotations if isinstance(normalized_annotations, dict) else {}
    )
    normalized_metadata["annotations"] = normalized_annotations
    normalized_annotations["template"] = ""
    return normalized


def _normalized_manifest_documents(
    text: str,
    *,
    strip_generated: bool = False,
) -> tuple[dict[str, Any], ...]:
    documents: list[dict[str, Any]] = []
    for raw in yaml.safe_load_all(text):
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("kind") or "") == "List" and isinstance(raw.get("items"), list):
            candidates = [item for item in raw["items"] if isinstance(item, Mapping)]
        else:
            candidates = [raw]
        for item in candidates:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            annotations = (
                metadata.get("annotations")
                if isinstance(metadata.get("annotations"), Mapping)
                else {}
            )
            if str(annotations.get("helm.sh/hook") or "").strip():
                continue
            documents.append(_normalized_manifest_object(item, strip_generated=strip_generated))
    return tuple(sorted(documents, key=_manifest_object_key))


def _manifest_object_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    return (
        str(value.get("apiVersion") or ""),
        str(value.get("kind") or ""),
        str(metadata.get("namespace") or ""),
        str(metadata.get("name") or ""),
    )


def _manifest_value_is_live_subset(expected: object, observed: object) -> bool:
    """Compare rendered fields while allowing only server-added live fields."""

    if isinstance(expected, Mapping):
        if not isinstance(observed, Mapping):
            return False
        return all(
            key in observed and _manifest_value_is_live_subset(value, observed[key])
            for key, value in expected.items()
        )
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        if not isinstance(observed, Sequence) or isinstance(observed, (str, bytes, bytearray)):
            return False
        return len(expected) == len(observed) and all(
            _manifest_value_is_live_subset(expected_item, observed_item)
            for expected_item, observed_item in zip(expected, observed, strict=True)
        )
    return expected == observed


def verify_soperator_manifest_equivalence(
    *,
    live_manifest: str,
    rendered_manifest: str,
    oci_digest: str = "",
) -> SoperatorProvenanceEvidence:
    live = _normalized_manifest_documents(live_manifest, strip_generated=True)
    rendered = _normalized_manifest_documents(rendered_manifest)
    live_by_key = {_manifest_object_key(item): item for item in live}
    rendered_by_key = {_manifest_object_key(item): item for item in rendered}
    if len(live_by_key) != len(live) or len(rendered_by_key) != len(rendered):
        raise RuntimeError("Soperator provenance has duplicate owned object identities")
    if live_by_key != rendered_by_key:
        missing = sorted(set(rendered_by_key) - set(live_by_key))
        unexpected = sorted(set(live_by_key) - set(rendered_by_key))
        drifted = sorted(
            key
            for key in set(live_by_key).intersection(rendered_by_key)
            if live_by_key[key] != rendered_by_key[key]
        )
        summary = "; ".join(
            item
            for item in (
                f"missing={missing}" if missing else "",
                f"unexpected={unexpected}" if unexpected else "",
                f"drifted={drifted}" if drifted else "",
            )
            if item
        )
        raise RuntimeError(
            "installed Soperator manifest is not equivalent to the verified official "
            f"render ({summary or 'object graph differs'})"
        )
    graph = tuple(live_by_key)
    return SoperatorProvenanceEvidence(
        method=(
            "official-oci-digest-and-render-equivalence"
            if str(oci_digest or "").strip()
            else "official-render-equivalence"
        ),
        live_manifest_sha256=_manifest_sha256(live),
        rendered_manifest_sha256=_manifest_sha256(rendered),
        owned_object_graph_sha256=_manifest_sha256(graph),
    )


def _materialize_soperator_chart_dependencies(
    *,
    chart_path: Path,
    staging_root: Path,
    run: Any,
) -> Path:
    """Stage exact source-declared dependencies without modifying verified source."""

    raw_metadata = yaml.safe_load((chart_path / "Chart.yaml").read_text(encoding="utf-8"))
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    raw_dependencies = metadata.get("dependencies")
    if raw_dependencies is None:
        return chart_path
    if not isinstance(raw_dependencies, list) or not raw_dependencies:
        raise RuntimeError("verified official Soperator chart has invalid dependencies")
    dependencies: list[tuple[str, str, str]] = []
    for raw_dependency in raw_dependencies:
        dependency = raw_dependency if isinstance(raw_dependency, Mapping) else {}
        name = str(dependency.get("name") or "").strip()
        version = str(dependency.get("version") or "").strip()
        repository = str(dependency.get("repository") or "").strip()
        if (
            not name
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or not repository.startswith("https://")
        ):
            raise RuntimeError(
                "verified official Soperator chart dependency must use an exact stable "
                "version and HTTPS repository"
            )
        dependencies.append((name, version, repository))

    staged = staging_root / "soperator"
    shutil.copytree(chart_path, staged)
    for path in staged.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    staged.chmod(0o700)
    repository_config = staging_root / "repositories.yaml"
    repository_cache = staging_root / "repository-cache"
    repository_cache.mkdir(mode=0o700)
    for index, repository in enumerate(dict.fromkeys(item[2] for item in dependencies), start=1):
        run(
            [
                "helm",
                "repo",
                "add",
                f"cxcli-soperator-provenance-{index}",
                repository,
                "--force-update",
                "--repository-config",
                str(repository_config),
                "--repository-cache",
                str(repository_cache),
            ]
        )
    run(
        [
            "helm",
            "dependency",
            "build",
            "--skip-refresh",
            "--repository-config",
            str(repository_config),
            "--repository-cache",
            str(repository_cache),
            str(staged),
        ]
    )
    lock_path = staged / "Chart.lock"
    raw_lock = yaml.safe_load(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {}
    lock = raw_lock if isinstance(raw_lock, Mapping) else {}
    locked_dependencies = lock.get("dependencies")
    locked_identities = {
        (
            str(item.get("name") or "").strip(),
            str(item.get("version") or "").strip(),
            str(item.get("repository") or "").strip(),
        )
        for item in locked_dependencies or ()
        if isinstance(item, Mapping)
    }
    if locked_identities != set(dependencies):
        raise RuntimeError(
            "materialized Soperator chart dependencies differ from verified source metadata"
        )
    for name, version, _repository in dependencies:
        packaged = staged / "charts" / f"{name}-{version}.tgz"
        unpacked = staged / "charts" / name / "Chart.yaml"
        if not packaged.is_file() and not unpacked.is_file():
            raise RuntimeError(
                f"materialized Soperator chart dependency is missing: {name}-{version}"
            )
    return staged


def verify_live_soperator_release_provenance(
    *,
    kube_context: str,
    release_name: str,
    namespace: str,
    storage_namespace: str,
    source_dir: Path,
    timeout: int = 120,
    extra_env: Mapping[str, str] | None = None,
    oci_digest: str = "",
) -> SoperatorProvenanceEvidence:
    chart_path = source_dir / "helm" / "soperator"
    if not (chart_path / "Chart.yaml").is_file():
        raise RuntimeError("verified official Soperator source has no helm/soperator chart")
    environment = None if extra_env is None else {**os.environ, **dict(extra_env)}

    def _run(args: list[str], *, input_text: str | None = None) -> str:
        result = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise RuntimeError(f"Soperator provenance command failed: {detail}")
        return result.stdout

    values = _run(
        [
            "helm",
            "--kube-context",
            kube_context,
            "get",
            "values",
            release_name,
            "--namespace",
            storage_namespace,
            "--all",
            "--output",
            "yaml",
        ]
    )
    live_manifest = _run(
        [
            "helm",
            "--kube-context",
            kube_context,
            "get",
            "manifest",
            release_name,
            "--namespace",
            storage_namespace,
        ]
    )
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-soperator-provenance-") as temp_value:
        render_chart_path = _materialize_soperator_chart_dependencies(
            chart_path=chart_path,
            staging_root=Path(temp_value),
            run=_run,
        )
        rendered_manifest = _run(
            [
                "helm",
                "template",
                release_name,
                str(render_chart_path),
                "--namespace",
                namespace,
                "--values",
                "-",
            ],
            input_text=values,
        )
    evidence = verify_soperator_manifest_equivalence(
        live_manifest=live_manifest,
        rendered_manifest=rendered_manifest,
        oci_digest=oci_digest,
    )
    live_identities: list[dict[str, str]] = []
    for document in _normalized_manifest_documents(live_manifest, strip_generated=True):
        metadata = document.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        annotations = metadata_map.get("annotations")
        annotations_map = annotations if isinstance(annotations, Mapping) else {}
        if str(annotations_map.get("helm.sh/hook") or "").strip():
            continue
        api_version = str(document.get("apiVersion") or "").strip()
        kind = str(document.get("kind") or "").strip()
        name = str(metadata_map.get("name") or "").strip()
        object_namespace = str(metadata_map.get("namespace") or namespace).strip()
        if not api_version or not kind or not name:
            raise RuntimeError("Soperator provenance contains an incomplete object identity")
        group = api_version.split("/", 1)[0] if "/" in api_version else ""
        resource = f"{kind}.{group}" if group else kind
        raw_live = _run(
            [
                "kubectl",
                "--context",
                kube_context,
                "--namespace",
                object_namespace,
                "get",
                resource,
                name,
                "--output",
                "json",
            ]
        )
        try:
            live_object = json.loads(raw_live)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"live Soperator object {kind}/{name} returned invalid JSON"
            ) from exc
        if not isinstance(live_object, Mapping):
            raise RuntimeError(f"live Soperator object {kind}/{name} is malformed")
        live_metadata = live_object.get("metadata")
        live_metadata_map = live_metadata if isinstance(live_metadata, Mapping) else {}
        live_identity = {
            "apiVersion": str(live_object.get("apiVersion") or "").strip(),
            "kind": str(live_object.get("kind") or "").strip(),
            "namespace": str(live_metadata_map.get("namespace") or object_namespace).strip(),
            "name": str(live_metadata_map.get("name") or "").strip(),
            "uid": str(live_metadata_map.get("uid") or "").strip(),
        }
        if (
            live_identity["apiVersion"] != api_version
            or live_identity["kind"] != kind
            or live_identity["namespace"] != object_namespace
            or live_identity["name"] != name
            or not live_identity["uid"]
        ):
            raise RuntimeError(f"live Soperator object identity differs for {kind}/{name}")
        normalized_live_object = _normalized_manifest_object(
            live_object,
            strip_generated=True,
        )
        normalized_live_object = _controller_owned_webhook_template(
            document,
            normalized_live_object,
        )
        if not _manifest_value_is_live_subset(document, normalized_live_object):
            raise RuntimeError(
                f"live object content differs from the official Soperator render for {kind}/{name}"
            )
        live_identities.append(live_identity)
    if not live_identities:
        raise RuntimeError("Soperator provenance has no persistent live Helm objects")
    return SoperatorProvenanceEvidence(
        method=evidence.method,
        live_manifest_sha256=evidence.live_manifest_sha256,
        rendered_manifest_sha256=evidence.rendered_manifest_sha256,
        owned_object_graph_sha256=_manifest_sha256(
            sorted(
                live_identities,
                key=lambda item: (
                    item["apiVersion"],
                    item["kind"],
                    item["namespace"],
                    item["name"],
                ),
            )
        ),
    )


def soperator_protected_storage_evidence(
    snapshot: Mapping[str, Any],
) -> tuple[str, tuple[dict[str, str], ...]]:
    raw_pvcs = snapshot.get("pvcs")
    raw_pvs = snapshot.get("pvs")
    pvcs = [item for item in raw_pvcs or () if isinstance(item, Mapping)]
    pvs = [item for item in raw_pvs or () if isinstance(item, Mapping)]
    bindings: list[dict[str, str]] = []
    for pvc in pvcs:
        metadata = pvc.get("metadata") if isinstance(pvc.get("metadata"), Mapping) else {}
        if str(metadata.get("namespace") or "") != "soperator":
            continue
        spec = pvc.get("spec") if isinstance(pvc.get("spec"), Mapping) else {}
        pvc_name = str(metadata.get("name") or "").strip()
        pvc_uid = str(metadata.get("uid") or "").strip()
        pv_name = str(spec.get("volumeName") or "").strip()
        pv = next(
            (
                item
                for item in pvs
                if str(
                    (item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}).get(
                        "name"
                    )
                    or ""
                ).strip()
                == pv_name
            ),
            None,
        )
        pv_spec = (
            pv.get("spec")
            if isinstance(pv, Mapping) and isinstance(pv.get("spec"), Mapping)
            else {}
        )
        pv_metadata = (
            pv.get("metadata")
            if isinstance(pv, Mapping) and isinstance(pv.get("metadata"), Mapping)
            else {}
        )
        pv_uid = str(pv_metadata.get("uid") or "").strip()
        csi = pv_spec.get("csi") if isinstance(pv_spec.get("csi"), Mapping) else {}
        filesystem_id = str(csi.get("volumeHandle") or "").strip()
        identity = normalize_component_token(f"{pvc_name}-{pv_name}").replace("-", "")
        mount_tag = next(
            (
                candidate
                for candidate in ("accounting", "controller-spool", "jail")
                if candidate.replace("-", "") in identity
            ),
            "",
        )
        if not pvc_name or not pvc_uid or not pv_name or not pv_uid:
            continue
        binding = {
            "namespace": "soperator",
            "pvc": pvc_name,
            "pvcUid": pvc_uid,
            "pv": pv_name,
            "pvUid": pv_uid,
        }
        if filesystem_id.startswith("computefilesystem-"):
            bindings.append(
                {
                    **binding,
                    "backingKind": "sfs-csi",
                    "filesystemId": filesystem_id,
                    "mountTag": mount_tag or normalize_component_token(pvc_name),
                }
            )
            continue
        if isinstance(pv_spec.get("local"), Mapping) and mount_tag:
            if str(pv_spec.get("persistentVolumeReclaimPolicy") or "").strip() != "Retain":
                raise RuntimeError(f"Soperator protected local SFS PV {pv_name!r} must use Retain")
            bindings.append(
                {
                    **binding,
                    "backingKind": "sfs-local",
                    "mountTag": mount_tag,
                }
            )
    if not bindings:
        raise RuntimeError(
            "Soperator onboarding requires at least one immutable protected PVC/PV/CSI "
            "backing-storage identity"
        )
    ordered = tuple(sorted(bindings, key=lambda item: (item["pvc"], item["pv"])))
    return _manifest_sha256(
        {
            "schema": "nebius-cxcli.soperator-protected-storage-evidence.v2",
            "bindings": ordered,
        }
    ), ordered


def _stable_json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)


def soperator_registration_fingerprint(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> str:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        payload = {}
    target = soperator_registration_target(payload, target_ref=target_ref)
    app_row = soperator_registration_app_row(payload, target_ref=target_ref)
    registration = (target or {}).get("soperator_registration", {})
    if not isinstance(registration, Mapping):
        registration = {}
    material = {
        "target_ref": normalize_component_token(target_ref),
        "target": {
            "access": str((target or {}).get("access", "") or "").strip(),
            "cluster_id": str((target or {}).get("cluster_id", "") or "").strip(),
            "inventory": (target or {}).get("inventory", {}),
            "kube_context": str((target or {}).get("kube_context", "") or "").strip(),
            "project_id": str((target or {}).get("project_id", "") or "").strip(),
            "registration": {
                "accepted": registration.get("accepted") is True,
                "collection_errors": list(registration.get("collection_errors", []) or []),
                "schema": str(registration.get("schema", "") or "").strip(),
                "source_archive_sha256": str(
                    registration.get("source_archive_sha256", "") or ""
                ).strip(),
                "source_capability_sha256": str(
                    registration.get("source_capability_sha256", "") or ""
                ).strip(),
                "source_commit": str(registration.get("source_commit", "") or "").strip(),
                "source_contract": str(registration.get("source_contract", "") or "").strip(),
                "source_manifest_sha256": str(
                    registration.get("source_manifest_sha256", "") or ""
                ).strip(),
                "live_manifest_sha256": str(
                    registration.get("live_manifest_sha256", "") or ""
                ).strip(),
                "rendered_manifest_sha256": str(
                    registration.get("rendered_manifest_sha256", "") or ""
                ).strip(),
                "owned_object_graph_sha256": str(
                    registration.get("owned_object_graph_sha256", "") or ""
                ).strip(),
                "protected_storage_receipt_sha256": str(
                    registration.get("protected_storage_receipt_sha256", "") or ""
                ).strip(),
                "provenance_method": str(registration.get("provenance_method", "") or "").strip(),
                "source_repository": str(registration.get("source_repository", "") or "").strip(),
                "source_tag": str(registration.get("source_tag", "") or "").strip(),
                "source_tree": str(registration.get("source_tree", "") or "").strip(),
                "source_version": str(registration.get("source_version", "") or "").strip(),
                "state": str(registration.get("state", "") or "").strip(),
            },
        },
        "soperator": {
            "instance_id": str((app_row or {}).get("instance_id", "") or ""),
            "namespace": str((app_row or {}).get("namespace", "") or ""),
            "release_name": str((app_row or {}).get("release-name", "") or ""),
        },
    }
    return hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()


def soperator_registration_target(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> Mapping[str, Any] | None:
    payload = to_plain_data(payload_or_config)
    deploy = payload.get("deploy") if isinstance(payload, Mapping) else None
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    normalized_target = normalize_component_token(target_ref)
    if not isinstance(targets, list) or not normalized_target:
        return None
    for row in targets:
        if not isinstance(row, Mapping):
            continue
        if normalize_component_token(row.get("instance_id")) == normalized_target:
            return row
    return None


def soperator_registration_app_row(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> Mapping[str, Any] | None:
    payload = to_plain_data(payload_or_config)
    apps = payload.get("apps") if isinstance(payload, Mapping) else None
    charts = apps.get("charts") if isinstance(apps, Mapping) else None
    normalized_target = normalize_component_token(target_ref)
    if not isinstance(charts, list) or not normalized_target:
        return None
    for row in charts:
        if not isinstance(row, Mapping) or row.get("id") != "soperator":
            continue
        if normalize_component_token(row.get("instance_id")) == normalized_target:
            return row
    return None


def soperator_registration_is_accepted(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> bool:
    target = soperator_registration_target(payload_or_config, target_ref=target_ref)
    if not isinstance(target, Mapping):
        return False
    registration = target.get("soperator_registration")
    if not isinstance(registration, Mapping):
        return False
    if registration.get("schema") != SOPERATOR_REGISTRATION_SCHEMA:
        return False
    if registration.get("accepted") is not True:
        return False
    if str(registration.get("state", "") or "").strip() != SOPERATOR_REGISTRATION_STATE_SUPPORTED:
        return False
    collection_errors = registration.get("collection_errors")
    if not isinstance(collection_errors, list) or collection_errors:
        return False
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(registration.get("source_version", ""))):
        return False
    if str(registration.get("source_contract", "") or "").strip() not in {
        "protected-data-plane-v1",
        "upstream-flux-v1",
    }:
        return False
    for key in (
        "source_archive_sha256",
        "source_capability_sha256",
        "source_manifest_sha256",
        "live_manifest_sha256",
        "rendered_manifest_sha256",
        "owned_object_graph_sha256",
        "protected_storage_receipt_sha256",
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(registration.get(key, "") or "")):
            return False
    for key in ("source_commit", "source_tree"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(registration.get(key, "") or "")):
            return False
    if str(registration.get("provenance_method") or "") not in {
        "official-render-equivalence",
        "official-oci-digest-and-render-equivalence",
    }:
        return False
    recorded = str(registration.get("analysis_fingerprint", "") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", recorded):
        return False
    return recorded == soperator_registration_fingerprint(payload_or_config, target_ref=target_ref)


def validate_soperator_registration(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> None:
    if soperator_registration_is_accepted(payload_or_config, target_ref=target_ref):
        return
    target = normalize_component_token(target_ref) or target_ref
    raise ValueError(
        f"apps:soperator target '{target}' has unsupported or invalid Soperator "
        "registration evidence."
    )


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _release_chart_identity(release: Mapping[str, Any]) -> str:
    explicit = str(release.get("chart_name", "") or "").strip()
    if explicit:
        return explicit.lower()
    chart = str(release.get("chart", "") or "").strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)-[0-9]+(?:[.+-].*)?$", chart)
    return (match.group(1) if match else chart).lower()


def _release_name(release: Mapping[str, Any]) -> str:
    return str(release.get("name", "") or "").strip().lower()


def _is_soperator_release_candidate(release: Mapping[str, Any]) -> bool:
    chart_identity = _release_chart_identity(release)
    if chart_identity:
        return chart_identity in SOPERATOR_COMPATIBLE_CHART_IDENTITIES
    release_name = _release_name(release)
    return (
        release_name in SOPERATOR_COMPATIBLE_RELEASE_NAMES
        or release_name in SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES
    )


def _flux_release_base_version(value: object) -> str:
    return str(value or "").strip().removeprefix("v").split("+", 1)[0]


def _flux_soperator_main_release(
    inventory: Mapping[str, Any],
    *,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project the exact cxcli Flux main owner into the Helm discovery shape."""

    graph_items = [
        item
        for item in _sequence_of_mappings(inventory.get("items"))
        if _mapping_value(_mapping_value(item.get("metadata")).get("labels")).get(
            SOPERATOR_GRAPH_LABEL
        )
        == SOPERATOR_GRAPH_LABEL_VALUE
    ]
    if not graph_items:
        return {}
    candidates = [
        item
        for item in graph_items
        if str(_mapping_value(item.get("metadata")).get("namespace") or "") == "flux-system"
        and str(_mapping_value(item.get("metadata")).get("name") or "")
        == _FLUX_SOPERATOR_MAIN_RELEASE
    ]
    if len(candidates) != 1:
        errors.append(
            {
                "command": "project Flux Soperator HelmRelease",
                "message": "release graph has no unique main workload",
            }
        )
        return {}

    item = candidates[0]
    metadata = _mapping_value(item.get("metadata"))
    labels = _mapping_value(metadata.get("labels"))
    spec = _mapping_value(item.get("spec"))
    status = _mapping_value(item.get("status"))
    release_name = str(spec.get("releaseName") or "").strip()
    target_namespace = str(spec.get("targetNamespace") or "").strip()
    graph_version = str(labels.get("app.kubernetes.io/version") or "").strip()
    if (
        release_name not in SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES
        or not target_namespace
        or not graph_version
    ):
        errors.append(
            {
                "command": "project Flux Soperator HelmRelease",
                "message": "main workload identity is incomplete",
            }
        )
        return {}

    ready_condition = next(
        (
            condition
            for condition in _sequence_of_mappings(status.get("conditions"))
            if str(condition.get("type") or "") == "Ready"
        ),
        {},
    )
    generation = str(metadata.get("generation") or "").strip()
    observed_generation = str(status.get("observedGeneration") or "").strip()
    generation_is_current = bool(generation) and observed_generation == generation
    ready_status = str(ready_condition.get("status") or "")
    history = _sequence_of_mappings(status.get("history"))
    latest = history[0] if history else {}
    chart_name = str(latest.get("chartName") or "").strip()
    chart_version = str(latest.get("chartVersion") or "").strip()
    app_version = str(latest.get("appVersion") or "").strip()
    release_status = str(latest.get("status") or "").strip()
    if generation_is_current and ready_status == "True":
        if (
            chart_name != "helm-soperator"
            or release_status != "deployed"
            or _flux_release_base_version(chart_version) != graph_version
            or _flux_release_base_version(app_version) != graph_version
        ):
            errors.append(
                {
                    "command": "project Flux Soperator HelmRelease",
                    "message": "ready main workload history differs from its release graph",
                }
            )
            return {}
        projected_status = "deployed"
    elif ready_status == "False":
        projected_status = "failed"
    elif not generation_is_current or ready_status == "Unknown":
        projected_status = "pending-upgrade"
    else:
        projected_status = "unknown"

    storage_namespace = str(
        status.get("storageNamespace")
        or spec.get("storageNamespace")
        or metadata.get("namespace")
        or ""
    ).strip()
    projected: dict[str, Any] = {
        "name": release_name,
        "namespace": target_namespace,
        "status": projected_status,
        "storage_namespace": storage_namespace,
        "source": "flux-helmrelease",
    }
    if chart_name:
        projected["chart_name"] = chart_name
    if chart_name and chart_version:
        projected["chart"] = f"{chart_name}-{chart_version}"
    if chart_version:
        projected["chart_version"] = chart_version
    if app_version:
        projected["app_version"] = app_version
    return projected


def _helm_release_storage_namespace(
    *,
    kube_context: str,
    release_name: str,
    namespace_names: Sequence[str],
    timeout: int,
    errors: list[dict[str, Any]] | None,
    extra_env: Mapping[str, str] | None,
) -> str:
    """Resolve Helm storage independently from the rendered target namespace."""

    storage_namespaces: list[str] = []
    release_filter = f"^{re.escape(release_name)}$"
    for namespace in sorted({str(item).strip() for item in namespace_names if str(item).strip()}):
        releases = _helm_json(
            [
                "helm",
                "--kube-context",
                kube_context,
                "list",
                "--namespace",
                namespace,
                "--filter",
                release_filter,
                "--output",
                "json",
            ],
            timeout,
            errors=errors,
            extra_env=extra_env,
        )
        if not isinstance(releases, list):
            continue
        if any(
            isinstance(item, Mapping)
            and str(item.get("name") or "").strip() == release_name
            and str(item.get("status") or "").strip().lower() == "deployed"
            for item in releases
        ):
            storage_namespaces.append(namespace)
    if len(storage_namespaces) == 1:
        return storage_namespaces[0]
    if errors is not None:
        detail = "none" if not storage_namespaces else ", ".join(sorted(storage_namespaces))
        errors.append(
            {
                "command": "helm list --namespace <candidate> --filter <release>",
                "message": (
                    "Soperator Helm storage namespace resolution requires exactly one "
                    f"deployed match; found {detail}."
                ),
            }
        )
    return ""


def soperator_registration_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    payload = to_plain_data(payload_or_config)
    deploy = payload.get("deploy") if isinstance(payload, Mapping) else None
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    external_refs = {
        normalize_component_token(row.get("instance_id"))
        for row in targets or ()
        if isinstance(row, Mapping) and deploy_target_is_external_mk8s(row)
    }
    apps = payload.get("apps") if isinstance(payload, Mapping) else None
    charts = apps.get("charts") if isinstance(apps, Mapping) else None
    if not isinstance(charts, list):
        return ()
    refs: list[str] = []
    seen: set[str] = set()
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        if row.get("id") != "soperator":
            continue
        target_ref = normalize_component_token(row.get("instance_id"))
        if target_ref in external_refs and target_ref not in seen:
            refs.append(target_ref)
            seen.add(target_ref)
    return tuple(refs)


def write_source_soperator_discovery_report(
    target_dir: Path,
    *,
    target_ref: str,
    snapshot: Mapping[str, Any],
    report: Mapping[str, Any],
    artifact_identity: SoperatorClusterArtifactIdentity | None = None,
    cluster_id: str = "",
    cluster_name: str = "",
    source_kind: str = "onboarded",
    command: Sequence[str] | None = None,
    namespace: str = "",
    release_name: str = "",
    kube_context: str = "",
    chart_values: Mapping[str, Any] | None = None,
    slurm_snapshot: Mapping[str, Any] | None = None,
    accounting_snapshot: Mapping[str, Any] | None = None,
    target_versions: Mapping[str, Any] | None = None,
    guidance_lines: Sequence[str] | None = None,
    output_dir: Path | None = None,
    redaction: str = "support",
) -> Path:
    return write_soperator_discovery_bundle(
        target_dir,
        target_ref=target_ref,
        snapshot=snapshot,
        report=report,
        source_kind=source_kind,
        command=command,
        artifact_identity=artifact_identity,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        namespace=namespace,
        release_name=release_name,
        kube_context=kube_context,
        chart_values=chart_values,
        slurm_snapshot=slurm_snapshot,
        accounting_snapshot=accounting_snapshot,
        target_versions=target_versions,
        guidance_lines=guidance_lines,
        output_dir=output_dir,
        redaction=redaction,
    )


def _soperator_jail_pvc_binding(
    soperator_resources: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    slurmclusters = tuple(
        item
        for item in soperator_resources
        if str(item.get("kind", "") or "").strip() == "SlurmCluster"
    )
    if not slurmclusters:
        return None

    bindings: set[tuple[str, str]] = set()
    unresolved: list[str] = []
    for slurmcluster in slurmclusters:
        metadata = (
            slurmcluster.get("metadata")
            if isinstance(slurmcluster.get("metadata"), Mapping)
            else {}
        )
        namespace = str(metadata.get("namespace", "") or "soperator").strip()
        cluster_name = str(metadata.get("name", "") or "<unnamed>").strip()
        spec = slurmcluster.get("spec") if isinstance(slurmcluster.get("spec"), Mapping) else {}
        volume_sources: dict[str, list[Mapping[str, Any]]] = {}
        for source in _sequence_of_mappings(spec.get("volumeSources")):
            source_name = str(source.get("name", "") or "").strip()
            if source_name:
                volume_sources.setdefault(source_name, []).append(source)

        referenced_source_names: set[str] = set()
        direct_claim_names: set[str] = set()
        slurm_nodes = spec.get("slurmNodes")
        if isinstance(slurm_nodes, Mapping):
            for role in slurm_nodes.values():
                if not isinstance(role, Mapping):
                    continue
                volumes = role.get("volumes")
                jail = volumes.get("jail") if isinstance(volumes, Mapping) else None
                if not isinstance(jail, Mapping):
                    continue
                persistent_volume_claim = jail.get("persistentVolumeClaim")
                claim_name = str(
                    persistent_volume_claim.get("claimName", "")
                    if isinstance(persistent_volume_claim, Mapping)
                    else ""
                ).strip()
                if claim_name:
                    direct_claim_names.add(claim_name)
                source_name = str(jail.get("volumeSourceName", "") or "").strip()
                if source_name:
                    referenced_source_names.add(source_name)

        if not referenced_source_names and not direct_claim_names:
            referenced_source_names.update(
                source_name
                for source_name in volume_sources
                if normalize_component_token(source_name) in {"jail", "jail-rootfs"}
            )

        bindings.update((namespace, claim_name) for claim_name in direct_claim_names)
        for source_name in sorted(referenced_source_names):
            source_matches = volume_sources.get(source_name, [])
            if len(source_matches) != 1:
                unresolved.append(
                    f"{namespace}/{cluster_name} volumeSourceName={source_name} "
                    f"resolved to {len(source_matches)} declarations"
                )
                continue
            persistent_volume_claim = source_matches[0].get("persistentVolumeClaim")
            claim_name = str(
                persistent_volume_claim.get("claimName", "")
                if isinstance(persistent_volume_claim, Mapping)
                else ""
            ).strip()
            if not claim_name:
                unresolved.append(
                    f"{namespace}/{cluster_name} volumeSourceName={source_name} has no PVC"
                )
                continue
            bindings.add((namespace, claim_name))

    if unresolved:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: unresolved SlurmCluster Jail "
            "volume reference(s): " + "; ".join(unresolved) + "."
        )
    if len(bindings) != 1:
        details = ", ".join(f"{namespace}/{name}" for namespace, name in sorted(bindings))
        if not details:
            details = "none"
        raise RuntimeError(
            "Soperator Jail identity resolution failed: expected exactly one discovered "
            f"Jail PVC binding, found {len(bindings)} ({details})."
        )
    return next(iter(bindings))


def _soperator_jail_filesystem_identity(
    *,
    soperator_resources: Sequence[Mapping[str, Any]],
    pvcs: Sequence[Mapping[str, Any]],
    pvs: Sequence[Mapping[str, Any]],
) -> str:
    binding = _soperator_jail_pvc_binding(soperator_resources)
    if binding is None:
        return ""
    namespace, claim_name = binding
    pvc_matches = [
        item
        for item in pvcs
        if str(
            item.get("metadata", {}).get("namespace", "")
            if isinstance(item.get("metadata"), Mapping)
            else ""
        ).strip()
        == namespace
        and str(
            item.get("metadata", {}).get("name", "")
            if isinstance(item.get("metadata"), Mapping)
            else ""
        ).strip()
        == claim_name
    ]
    if len(pvc_matches) != 1:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: discovered Jail PVC "
            f"{namespace}/{claim_name} resolved to {len(pvc_matches)} live PVC objects."
        )
    pvc = pvc_matches[0]
    pvc_status = pvc.get("status") if isinstance(pvc.get("status"), Mapping) else {}
    if str(pvc_status.get("phase", "") or "").strip() != "Bound":
        raise RuntimeError(
            "Soperator Jail identity resolution failed: discovered Jail PVC "
            f"{namespace}/{claim_name} is not Bound."
        )
    pvc_spec = pvc.get("spec") if isinstance(pvc.get("spec"), Mapping) else {}
    pv_name = str(pvc_spec.get("volumeName", "") or "").strip()
    if not pv_name:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: discovered Jail PVC "
            f"{namespace}/{claim_name} has no bound PV name."
        )
    pv_matches = [
        item
        for item in pvs
        if str(
            item.get("metadata", {}).get("name", "")
            if isinstance(item.get("metadata"), Mapping)
            else ""
        ).strip()
        == pv_name
    ]
    if len(pv_matches) != 1:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: bound PV "
            f"{pv_name} resolved to {len(pv_matches)} live PV objects."
        )
    pv = pv_matches[0]
    pv_status = pv.get("status") if isinstance(pv.get("status"), Mapping) else {}
    if str(pv_status.get("phase", "") or "").strip() != "Bound":
        raise RuntimeError(f"Soperator Jail identity resolution failed: PV {pv_name} is not Bound.")
    pv_spec = pv.get("spec") if isinstance(pv.get("spec"), Mapping) else {}
    claim_ref = pv_spec.get("claimRef") if isinstance(pv_spec.get("claimRef"), Mapping) else {}
    if (
        str(claim_ref.get("namespace", "") or "").strip() != namespace
        or str(claim_ref.get("name", "") or "").strip() != claim_name
    ):
        raise RuntimeError(
            "Soperator Jail identity resolution failed: PV "
            f"{pv_name} claimRef does not match {namespace}/{claim_name}."
        )
    pvc_metadata = pvc.get("metadata") if isinstance(pvc.get("metadata"), Mapping) else {}
    pvc_uid = str(pvc_metadata.get("uid", "") or "").strip()
    claim_uid = str(claim_ref.get("uid", "") or "").strip()
    if pvc_uid and claim_uid and pvc_uid != claim_uid:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: PV "
            f"{pv_name} claimRef UID does not match the discovered Jail PVC."
        )
    csi = pv_spec.get("csi") if isinstance(pv_spec.get("csi"), Mapping) else {}
    volume_handle = str(csi.get("volumeHandle", "") or "").strip()
    if not volume_handle:
        local = pv_spec.get("local") if isinstance(pv_spec.get("local"), Mapping) else {}
        local_path = str(local.get("path", "") or "").strip()
        if local_path:
            # Active/passive Jail slots are local-path PVs backed by a Nebius SFS
            # mounted on the node. The PV path identifies only the logical slot;
            # the immutable backing filesystem ID is resolved from the fresh
            # Nebius SDK node-group attachment inventory after snapshots merge.
            return ""
        raise RuntimeError(
            "Soperator Jail identity resolution failed: bound PV "
            f"{pv_name} has no CSI volumeHandle."
        )
    return volume_handle


def _soperator_slurmcluster_uid(
    soperator_resources: Sequence[Mapping[str, Any]],
) -> str:
    candidates: list[tuple[str, str, str]] = []
    for resource in soperator_resources:
        if str(resource.get("kind", "") or "").strip().lower() != "slurmcluster":
            continue
        metadata = resource.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidates.append(
            (
                str(metadata.get("namespace", "") or "soperator").strip(),
                str(metadata.get("name", "") or "<unnamed>").strip(),
                str(metadata.get("uid", "") or "").strip(),
            )
        )
    if not candidates:
        return ""
    if len(candidates) != 1:
        identities = ", ".join(
            f"{namespace}/{name} uid={uid or 'missing'}"
            for namespace, name, uid in sorted(candidates)
        )
        raise RuntimeError(
            "Soperator identity resolution failed: expected exactly one discovered "
            f"SlurmCluster identity, found {len(candidates)} ({identities})."
        )
    namespace, name, uid = candidates[0]
    if not uid:
        raise RuntimeError(
            "Soperator identity resolution failed: discovered SlurmCluster "
            f"{namespace}/{name} has no immutable UID."
        )
    return uid


def _soperator_snapshot_identity(
    *,
    soperator_resources: Sequence[Mapping[str, Any]],
    pvcs_payload: Mapping[str, Any],
    pvs_payload: Mapping[str, Any],
    collection_errors: list[dict[str, Any]],
    require_complete: bool,
) -> tuple[str, str]:
    """Resolve strict lifecycle identity or record one public-discovery gap."""

    try:
        slurmcluster_uid = _soperator_slurmcluster_uid(soperator_resources)
        jail_binding = _soperator_jail_pvc_binding(soperator_resources)
        if jail_binding is not None and not isinstance(pvcs_payload.get("items"), list):
            detail = _last_kubectl_inventory_error(collection_errors, resource="pvc")
            raise RuntimeError(
                "Soperator Jail identity resolution failed: live PVC inventory collection "
                "failed after 3 attempts; refusing to treat a failed read as an empty inventory."
                f"{detail}"
            )
        if jail_binding is not None and not isinstance(pvs_payload.get("items"), list):
            detail = _last_kubectl_inventory_error(collection_errors, resource="pv")
            raise RuntimeError(
                "Soperator Jail identity resolution failed: live PV inventory collection "
                "failed after 3 attempts; refusing to treat a failed read as an empty inventory."
                f"{detail}"
            )
        jail_filesystem_id = _soperator_jail_filesystem_identity(
            soperator_resources=soperator_resources,
            pvcs=_sequence_of_mappings(pvcs_payload.get("items")),
            pvs=_sequence_of_mappings(pvs_payload.get("items")),
        )
    except RuntimeError as exc:
        if require_complete:
            raise
        collection_errors.append(
            {
                "collector": "soperator-identity",
                "message": str(exc),
            }
        )
        return "", ""
    return slurmcluster_uid, jail_filesystem_id


def _merge_kubectl_list_payloads(
    *payloads: Mapping[str, Any],
) -> dict[str, Any]:
    items: list[Mapping[str, Any]] = []
    for payload in payloads:
        items.extend(_sequence_of_mappings(payload.get("items")))
    return {"apiVersion": "v1", "kind": "List", "items": items}


def collect_kubectl_soperator_snapshot(
    *,
    kube_context: str,
    timeout: int = 30,
    extra_env: Mapping[str, str] | None = None,
    include_cluster_inventory: bool = False,
    require_complete_identity: bool = True,
) -> dict[str, Any]:
    context = str(kube_context or "").strip()
    if not context:
        return {
            "node_groups": {},
            "helm_releases": [],
            "crds": [],
            "collection_lanes": [
                {"name": "kubernetes-access", "status": "failed", "item_count": None}
            ],
        }
    collection_errors: list[dict[str, Any]] = []
    nodes = _kubectl_json(
        ["kubectl", "--context", context, "get", "nodes", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    crds = _kubectl_json(
        ["kubectl", "--context", context, "get", "crd", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    namespaces = _kubectl_json(
        ["kubectl", "--context", context, "get", "namespace", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    namespace_names = (
        [
            str(item.get("metadata", {}).get("name", "")).strip()
            for item in namespaces.get("items", [])
            if isinstance(item, Mapping)
        ]
        if isinstance(namespaces, Mapping)
        else []
    )
    crd_names = (
        [
            str(item.get("metadata", {}).get("name", "")).strip()
            for item in crds.get("items", [])
            if isinstance(item, Mapping)
        ]
        if isinstance(crds, Mapping)
        else []
    )
    pvs = _kubectl_list_json_with_bounded_retry(
        ["kubectl", "--context", context, "get", "pv", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    pvcs = _kubectl_list_json_with_bounded_retry(
        ["kubectl", "--context", context, "get", "pvc", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    workloads: Mapping[str, Any] = {}
    required_workloads: Mapping[str, Any] = {}
    soperator_workloads_failed = False
    if "soperator" in namespace_names:
        soperator_workload_error_count = len(collection_errors)
        required_workloads = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                "deployments,statefulsets,daemonsets,pods,jobs,services,configmaps,secrets",
                "-n",
                "soperator",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        optional_workloads: Mapping[str, Any] = {}
        if _KRUISE_STATEFULSET_CRD in crd_names:
            optional_workloads = _kubectl_list_json_with_bounded_retry(
                [
                    "kubectl",
                    "--context",
                    context,
                    "get",
                    _KRUISE_STATEFULSET_CRD,
                    "-n",
                    "soperator",
                    "-o",
                    "json",
                ],
                timeout,
                errors=collection_errors,
                extra_env=extra_env,
            )
        workloads = _merge_kubectl_list_payloads(required_workloads, optional_workloads)
        soperator_workloads_failed = len(collection_errors) > soperator_workload_error_count
    cluster_workloads: Mapping[str, Any] = {}
    if include_cluster_inventory:
        required_cluster_workloads = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                "deployments,statefulsets,daemonsets,replicasets,pods,jobs,cronjobs,"
                "services,persistentvolumeclaims",
                "-A",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        optional_cluster_workloads: Mapping[str, Any] = {}
        if _KRUISE_STATEFULSET_CRD in crd_names:
            optional_cluster_workloads = _kubectl_list_json_with_bounded_retry(
                [
                    "kubectl",
                    "--context",
                    context,
                    "get",
                    _KRUISE_STATEFULSET_CRD,
                    "-A",
                    "-o",
                    "json",
                ],
                timeout,
                errors=None,
                extra_env=extra_env,
            )
        cluster_workloads = _merge_kubectl_list_payloads(
            required_cluster_workloads,
            optional_cluster_workloads,
        )
    soperator_resource_kinds = [
        resource_kind
        for crd_name, resource_kind in SOPERATOR_CRD_RESOURCE_KINDS
        if crd_name in set(crd_names)
    ]
    soperator_resources: Mapping[str, Any] = {}
    if soperator_resource_kinds:
        soperator_resources = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                ",".join(soperator_resource_kinds),
                "-A",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
    helm_error_count = len(collection_errors)
    all_helm_releases = _helm_json(
        ["helm", "--kube-context", context, "list", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    helm_collection_failed = len(collection_errors) > helm_error_count
    helm_releases = (
        [
            dict(release)
            for release in all_helm_releases
            if isinstance(release, Mapping) and _is_soperator_release_candidate(release)
        ]
        if isinstance(all_helm_releases, list)
        else []
    )
    for release in helm_releases:
        release_name = str(release.get("name") or "").strip()
        if not release_name:
            continue
        storage_namespace = _helm_release_storage_namespace(
            kube_context=context,
            release_name=release_name,
            namespace_names=namespace_names,
            timeout=timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        if storage_namespace:
            release["storage_namespace"] = storage_namespace
    if _FLUX_HELM_RELEASE_CRD in crd_names:
        flux_inventory = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                _FLUX_HELM_RELEASE_CRD,
                "-A",
                "-l",
                f"{SOPERATOR_GRAPH_LABEL}={SOPERATOR_GRAPH_LABEL_VALUE}",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        flux_release = _flux_soperator_main_release(
            flux_inventory,
            errors=collection_errors,
        )
        if flux_release:
            flux_name = str(flux_release.get("name") or "").strip()
            flux_namespace = str(flux_release.get("namespace") or "").strip()
            helm_releases = [
                flux_release,
                *(
                    release
                    for release in helm_releases
                    if str(release.get("name") or "").strip() != flux_name
                    or str(release.get("namespace") or "").strip() != flux_namespace
                ),
            ]
    gpu_stack_helm_releases = []
    gpu_helm_collection_failed = False
    gpu_helm_namespaces = [
        namespace
        for namespace in GPU_STACK_HELM_DISCOVERY_NAMESPACES
        if namespace in namespace_names
    ]
    for namespace in gpu_helm_namespaces:
        gpu_helm_error_count = len(collection_errors)
        namespace_releases = _helm_json(
            ["helm", "--kube-context", context, "list", "-n", namespace, "-o", "json"],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        gpu_helm_collection_failed = (
            gpu_helm_collection_failed or len(collection_errors) > gpu_helm_error_count
        )
        if isinstance(namespace_releases, list):
            gpu_stack_helm_releases.extend(namespace_releases)
    gpu_policy_kinds = [
        kind
        for crd_name, kind in (
            ("clusterpolicies.nvidia.com", "clusterpolicy"),
            ("nicclusterpolicies.mellanox.com", "nicclusterpolicy"),
        )
        if crd_name in crd_names
    ]
    gpu_policy_collection_failed = False
    gpu_stack_policies: Mapping[str, Any] = {}
    if gpu_policy_kinds:
        gpu_policy_error_count = len(collection_errors)
        gpu_stack_policies = _kubectl_json(
            [
                "kubectl",
                "--context",
                context,
                "get",
                ",".join(gpu_policy_kinds),
                "-A",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        gpu_policy_collection_failed = len(collection_errors) > gpu_policy_error_count
    component_namespaces = {
        str(_mapping_value(resource.get("metadata")).get("namespace") or "").strip()
        for resource in _sequence_of_mappings(soperator_resources.get("items"))
    }
    component_namespaces.update(
        str(release.get("namespace") or "").strip()
        for release in (*helm_releases, *gpu_stack_helm_releases)
        if isinstance(release, Mapping)
    )
    component_namespaces.update(
        str(_mapping_value(policy.get("metadata")).get("namespace") or "").strip()
        for policy in _sequence_of_mappings(gpu_stack_policies.get("items"))
    )
    component_namespaces.intersection_update(set(namespace_names))
    component_namespaces.discard("")
    additional_component_payloads: list[Mapping[str, Any]] = []
    component_workloads_failed = False
    for namespace in sorted(component_namespaces - {"soperator"}):
        component_error_count = len(collection_errors)
        payload = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                "deployments,statefulsets,daemonsets,pods,jobs,services,configmaps,secrets",
                "-n",
                namespace,
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        component_workloads_failed = (
            component_workloads_failed or len(collection_errors) > component_error_count
        )
        optional_payload: Mapping[str, Any] = {}
        if _KRUISE_STATEFULSET_CRD in crd_names:
            optional_payload = _kubectl_list_json_with_bounded_retry(
                [
                    "kubectl",
                    "--context",
                    context,
                    "get",
                    _KRUISE_STATEFULSET_CRD,
                    "-n",
                    namespace,
                    "-o",
                    "json",
                ],
                timeout,
                errors=collection_errors,
                extra_env=extra_env,
            )
            component_workloads_failed = (
                component_workloads_failed or len(collection_errors) > component_error_count
            )
        additional_component_payloads.append(
            _merge_kubectl_list_payloads(payload, optional_payload)
        )
    component_workloads = _merge_kubectl_list_payloads(*additional_component_payloads)
    all_component_workloads = _merge_kubectl_list_payloads(workloads, component_workloads)
    worker_topology_by_nodeset = _collect_worker_topology_by_nodeset(
        kube_context=context,
        workloads=all_component_workloads,
        timeout=timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    slurm_health = _collect_slurm_health_from_login(
        kube_context=context,
        workloads=all_component_workloads,
        timeout=timeout,
        extra_env=extra_env,
        errors=collection_errors,
    )
    node_groups: dict[str, dict[str, Any]] = {}
    for item in nodes.get("items", []) if isinstance(nodes, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        status = item.get("status")
        labels = metadata.get("labels") if isinstance(metadata, Mapping) else {}
        allocatable = status.get("allocatable") if isinstance(status, Mapping) else {}
        if not isinstance(labels, Mapping):
            labels = {}
        if not isinstance(allocatable, Mapping):
            allocatable = {}
        selector_key = ""
        selector_value = ""
        for candidate_key in (
            "nebius.com/node-group-id",
            "yandex.cloud/node-group-id",
            "nebius.com/node-group",
            "node.kubernetes.io/instance-type",
        ):
            candidate_value = str(labels.get(candidate_key) or "").strip()
            if candidate_value:
                selector_key = candidate_key
                selector_value = candidate_value
                break
        group_key = selector_value or "default"
        normalized = normalize_component_token(group_key) or "default"
        group = node_groups.setdefault(
            normalized,
            {
                "allocatable": {},
                "gpu": False,
                "node_count": 0,
                "labels": {},
                "nodes": [],
                "selector": {
                    "key": selector_key,
                    "operator": "In",
                    "values": [selector_value],
                }
                if selector_key and selector_value
                else {},
                "taints": [],
            },
        )
        group["node_count"] = int(group.get("node_count", 0)) + 1
        node_names = group.setdefault("nodes", [])
        node_name = str(metadata.get("name", "") if isinstance(metadata, Mapping) else "").strip()
        if isinstance(node_names, list) and node_name and node_name not in node_names:
            node_names.append(node_name)
        resources = group.setdefault("allocatable", {})
        if isinstance(resources, dict):
            for key, value in allocatable.items():
                resources[str(key)] = str(value)
        group["gpu"] = bool(group.get("gpu")) or any(
            str(key).startswith("nvidia.com/gpu") and str(value) not in {"0", ""}
            for key, value in allocatable.items()
        )
        taints = (
            item.get("spec", {}).get("taints", []) if isinstance(item.get("spec"), Mapping) else []
        )
        if isinstance(taints, list):
            existing_taints = group.setdefault("taints", [])
            if isinstance(existing_taints, list):
                for taint in taints:
                    if taint not in existing_taints:
                        existing_taints.append(taint)
        label_map = group.setdefault("labels", {})
        if isinstance(label_map, dict):
            for key, value in labels.items():
                text_key = str(key)
                if text_key.startswith(("nebius.com/", "slurm.nebius.ai/", "topology.nebius.com/")):
                    label_map.setdefault(text_key, str(value))
    kubernetes_uid = ""
    soperator_uid = ""
    for item in namespaces.get("items", []) if isinstance(namespaces, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        namespace_name = str(metadata.get("name", "") or "").strip()
        namespace_uid = str(metadata.get("uid", "") or "").strip()
        if namespace_name == "kube-system":
            kubernetes_uid = namespace_uid
        elif namespace_name == "soperator":
            soperator_uid = namespace_uid
    collected_soperator_resources = (
        soperator_resources.get("items", []) if isinstance(soperator_resources, Mapping) else []
    )
    collected_pvcs = pvcs.get("items", []) if isinstance(pvcs, Mapping) else []
    collected_pvs = pvs.get("items", []) if isinstance(pvs, Mapping) else []
    identity_resources = _sequence_of_mappings(collected_soperator_resources)
    slurmcluster_uid, jail_filesystem_id = _soperator_snapshot_identity(
        soperator_resources=identity_resources,
        pvcs_payload=pvcs,
        pvs_payload=pvs,
        collection_errors=collection_errors,
        require_complete=require_complete_identity,
    )
    result = {
        "node_groups": node_groups,
        "helm_releases": helm_releases if isinstance(helm_releases, list) else [],
        "crds": crd_names,
        "namespaces": namespace_names,
        "pvs": collected_pvs,
        "pvcs": collected_pvcs,
        "soperator_resources": collected_soperator_resources,
        "soperator_namespace_resources": _sanitize_namespace_resource_items(
            workloads.get("items", []) if isinstance(workloads, Mapping) else []
        ),
        "component_namespace_resources": _sanitize_namespace_resource_items(
            component_workloads.get("items", [])
        ),
        "cluster_namespace_resources": _sanitize_namespace_resource_items(
            cluster_workloads.get("items", []) if isinstance(cluster_workloads, Mapping) else []
        ),
        "kubernetes_nodes": _sanitize_kubernetes_node_items(
            nodes.get("items", []) if isinstance(nodes, Mapping) else []
        ),
        "slurm_health": slurm_health,
        "worker_topology_by_nodeset": worker_topology_by_nodeset,
        "gpu_stack": {
            "helm_releases": gpu_stack_helm_releases,
            "policies": (
                gpu_stack_policies.get("items", [])
                if isinstance(gpu_stack_policies, Mapping)
                else []
            ),
        },
        "cluster_identity": {
            "kubernetes_uid": kubernetes_uid,
            "soperator_uid": soperator_uid,
            "slurmcluster_uid": slurmcluster_uid,
            "jail_filesystem_id": jail_filesystem_id,
        },
        "collection_errors": collection_errors,
        "collection_lanes": [
            {
                "name": "kubernetes-nodes",
                "status": "succeeded" if isinstance(nodes.get("items"), list) else "failed",
                "item_count": len(nodes.get("items", []))
                if isinstance(nodes.get("items"), list)
                else None,
            },
            {
                "name": "kubernetes-crds",
                "status": "succeeded" if isinstance(crds.get("items"), list) else "failed",
                "item_count": len(crd_names) if isinstance(crds.get("items"), list) else None,
            },
            {
                "name": "kubernetes-namespaces",
                "status": "succeeded" if isinstance(namespaces.get("items"), list) else "failed",
                "item_count": len(namespace_names)
                if isinstance(namespaces.get("items"), list)
                else None,
            },
            {
                "name": "persistent-volumes",
                "status": "succeeded" if isinstance(pvs.get("items"), list) else "failed",
                "item_count": len(collected_pvs) if isinstance(pvs.get("items"), list) else None,
            },
            {
                "name": "persistent-volume-claims",
                "status": "succeeded" if isinstance(pvcs.get("items"), list) else "failed",
                "item_count": len(collected_pvcs) if isinstance(pvcs.get("items"), list) else None,
            },
            {
                "name": "soperator-resources",
                "status": (
                    "succeeded" if isinstance(soperator_resources.get("items"), list) else "failed"
                )
                if soperator_resource_kinds
                else "not-applicable",
                "item_count": len(collected_soperator_resources)
                if soperator_resource_kinds and isinstance(soperator_resources.get("items"), list)
                else 0
                if not soperator_resource_kinds
                else None,
            },
            {
                "name": "soperator-helm",
                "status": "failed" if helm_collection_failed else "succeeded",
                "item_count": len(helm_releases),
            },
            {
                "name": "soperator-workloads",
                "status": (
                    "failed"
                    if soperator_workloads_failed
                    else "succeeded"
                    if isinstance(required_workloads.get("items"), list)
                    else "failed"
                )
                if "soperator" in namespace_names
                else "not-applicable",
                "item_count": (
                    None
                    if soperator_workloads_failed
                    else len(_sequence_of_mappings(workloads.get("items")))
                    if isinstance(workloads.get("items"), list)
                    else None
                )
                if "soperator" in namespace_names
                else 0,
            },
            {
                "name": "component-workloads",
                "status": "failed"
                if component_workloads_failed
                else "succeeded"
                if additional_component_payloads
                else "not-applicable",
                "item_count": None
                if component_workloads_failed
                else len(_sequence_of_mappings(component_workloads.get("items"))),
            },
            {
                "name": "gpu-stack-helm",
                "status": "failed"
                if gpu_helm_collection_failed
                else "succeeded"
                if gpu_helm_namespaces
                else "not-applicable",
                "item_count": None if gpu_helm_collection_failed else len(gpu_stack_helm_releases),
            },
            {
                "name": "gpu-stack-policies",
                "status": "failed"
                if gpu_policy_collection_failed
                else "succeeded"
                if gpu_policy_kinds
                else "not-applicable",
                "item_count": None
                if gpu_policy_collection_failed
                else len(_sequence_of_mappings(gpu_stack_policies.get("items")))
                if gpu_policy_kinds
                else 0,
            },
            {
                "name": "slurm-health",
                "status": "succeeded"
                if slurm_health.get("checked") is True
                else "failed"
                if helm_releases or collected_soperator_resources
                else "not-applicable",
                "item_count": 1 if slurm_health.get("checked") is True else 0,
            },
        ],
    }
    return result


def _sanitize_kubernetes_node_items(items: Any) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in _sequence_of_mappings(items):
        metadata = _mapping_value(item.get("metadata"))
        spec = _mapping_value(item.get("spec"))
        status = _mapping_value(item.get("status"))
        sanitized.append(
            {
                "metadata": {
                    "name": metadata.get("name"),
                    "uid": metadata.get("uid"),
                    "labels": copy.deepcopy(to_plain_data(_mapping_value(metadata.get("labels")))),
                },
                "spec": {"unschedulable": spec.get("unschedulable") is True},
                "status": {
                    "conditions": [
                        dict(copy.deepcopy(to_plain_data(dict(condition))))
                        for condition in _sequence_of_mappings(status.get("conditions"))
                    ],
                    "nodeInfo": {
                        key: copy.deepcopy(to_plain_data(value))
                        for key, value in _mapping_value(status.get("nodeInfo")).items()
                        if key
                        in {
                            "containerRuntimeVersion",
                            "kernelVersion",
                            "kubeletVersion",
                            "osImage",
                        }
                    },
                },
            }
        )
    return sanitized


def _collect_slurm_health_from_login(
    *,
    kube_context: str,
    workloads: Mapping[str, Any],
    timeout: int,
    extra_env: Mapping[str, str] | None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    login_pods: list[tuple[str, str]] = []
    for item in _sequence_of_mappings(workloads.get("items")):
        if str(item.get("kind", "") or "").strip() != "Pod":
            continue
        metadata = _mapping_value(item.get("metadata"))
        status = _mapping_value(item.get("status"))
        name = str(metadata.get("name", "") or "").strip()
        namespace = str(metadata.get("namespace", "") or "soperator").strip()
        labels = _mapping_value(metadata.get("labels"))
        component = normalize_component_token(
            labels.get("app.kubernetes.io/component")
            or labels.get("slurm.nebius.ai/nodeset")
            or labels.get("slurm.nebius.ai/nodeset-name")
        )
        if (
            name
            and str(status.get("phase", "") or "").strip() == "Running"
            and (component == "login" or name.startswith("login-"))
        ):
            login_pods.append((namespace, name))
    if not login_pods:
        return {
            "checked": False,
            "healthy": False,
            "reason": "running login pod not found",
        }
    namespace, pod = sorted(login_pods)[0]
    command = [
        "kubectl",
        "--context",
        kube_context,
        "-n",
        namespace,
        "exec",
        pod,
        "--",
        "scontrol",
        "ping",
    ]
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        reason = (
            "scontrol ping collector timed out"
            if isinstance(exc, subprocess.TimeoutExpired)
            else "scontrol ping collector could not be executed"
        )
        if errors is not None:
            errors.append(
                {
                    "collector": "slurm-health",
                    "message": reason,
                }
            )
        return {
            "checked": False,
            "healthy": False,
            "pod": pod,
            "reason": reason,
        }
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip()
    )
    healthy = completed.returncode == 0 and bool(
        re.search(r"\bSlurmctld(?:\([^)]*\))?.*\bis\s+UP\b", output, re.IGNORECASE)
    )
    return {
        "checked": True,
        "healthy": healthy,
        "pod": pod,
        "namespace": namespace,
        "output": output[:1000],
        "reason": "" if healthy else "scontrol ping did not report Slurmctld UP",
    }


def _pvc_claim_names_from_pod_spec(pod_spec: Mapping[str, Any]) -> list[str]:
    volumes = pod_spec.get("volumes")
    if not isinstance(volumes, Sequence) or isinstance(volumes, (str, bytes, bytearray)):
        return []
    return sorted(
        {
            claim_name
            for volume in volumes
            if isinstance(volume, Mapping)
            and (
                claim_name := str(
                    _mapping_value(volume.get("persistentVolumeClaim")).get("claimName") or ""
                ).strip()
            )
        }
    )


def _sanitize_namespace_resource_items(items: Any) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return sanitized
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping_value(item.get("metadata"))
        row: dict[str, Any] = {
            "apiVersion": item.get("apiVersion"),
            "kind": item.get("kind"),
            "metadata": {
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "labels": _mapping_value(metadata.get("labels")),
            },
        }
        kind = str(item.get("kind", "") or "")
        if kind == "Secret":
            row["type"] = item.get("type")
            data = item.get("data")
            row["data_keys"] = sorted(str(key) for key in data) if isinstance(data, Mapping) else []
        elif kind in {"Deployment", "StatefulSet", "DaemonSet", "Pod"}:
            spec = _mapping_value(item.get("spec"))
            if kind in {"Deployment", "StatefulSet"}:
                row["spec"] = {"replicas": spec.get("replicas")}
            row["status"] = _mapping_value(item.get("status"))
            if kind == "Pod":
                pod_spec = spec
            else:
                template = _mapping_value(spec.get("template"))
                pod_spec = _mapping_value(template.get("spec"))
            if claim_names := _pvc_claim_names_from_pod_spec(pod_spec):
                row["pvc_claim_names"] = claim_names
        elif kind == "Job":
            row["metadata"]["uid"] = metadata.get("uid")
            row["metadata"]["creationTimestamp"] = metadata.get("creationTimestamp")
            row["status"] = _mapping_value(item.get("status"))
            spec = _mapping_value(item.get("spec"))
            template = _mapping_value(spec.get("template"))
            pod_spec = _mapping_value(template.get("spec"))
            containers = pod_spec.get("containers")
            if isinstance(containers, Sequence) and not isinstance(
                containers, (str, bytes, bytearray)
            ):
                row["containers"] = [
                    {
                        "name": container.get("name"),
                        "image": container.get("image"),
                    }
                    for container in containers
                    if isinstance(container, Mapping)
                ]
            else:
                row["containers"] = []
            if claim_names := _pvc_claim_names_from_pod_spec(pod_spec):
                row["pvc_claim_names"] = claim_names
        elif kind == "Service":
            spec = _mapping_value(item.get("spec"))
            row["spec"] = {
                "type": spec.get("type"),
                "ports": spec.get("ports") if isinstance(spec.get("ports"), list) else [],
                "selector": spec.get("selector")
                if isinstance(spec.get("selector"), Mapping)
                else {},
            }
        elif kind == "ConfigMap":
            data = item.get("data")
            row["data_keys"] = sorted(str(key) for key in data) if isinstance(data, Mapping) else []
        sanitized.append(row)
    return sanitized


def _subprocess_error_payload(command: Sequence[str], exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, subprocess.TimeoutExpired):
        message = "collector command timed out"
    elif isinstance(exc, subprocess.CalledProcessError):
        message = f"collector command failed with exit code {exc.returncode}"
    elif isinstance(exc, json.JSONDecodeError):
        message = "collector command returned invalid JSON"
    else:
        message = "collector command could not be executed"
    return {
        "command": " ".join(str(part) for part in command),
        "message": message,
    }


def _kubectl_json(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _kubectl_list_json_with_bounded_retry(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    """Retry a failed inventory read without treating an authoritative empty list as failure."""
    attempt_errors: list[dict[str, Any]] = []
    payload: Mapping[str, Any] = {}
    for _attempt in range(3):
        current_errors: list[dict[str, Any]] = []
        payload = _kubectl_json(
            command,
            timeout,
            errors=current_errors,
            extra_env=extra_env,
        )
        if isinstance(payload.get("items"), list):
            return payload
        attempt_errors.extend(current_errors)
    if errors is not None:
        errors.extend(attempt_errors)
    return payload


def _last_kubectl_inventory_error(errors: Sequence[Mapping[str, Any]], *, resource: str) -> str:
    marker = f" get {resource} "
    matching = [
        error for error in errors if marker in f" {str(error.get('command', '') or '').strip()} "
    ]
    if not matching:
        return ""
    message = " ".join(str(matching[-1].get("message", "") or "").split())
    return f" Last error: {message[:500]}" if message else ""


def _helm_json(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Any:
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return []
    try:
        return json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return []


def _kubectl_text(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return ""
    return completed.stdout or ""


def _lscpu_field_map(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    entries = payload.get("lscpu")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return result
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        field = str(item.get("field", "") or "").strip().rstrip(":").lower()
        data = str(item.get("data", "") or "").strip()
        if field and data:
            result[field] = data
    return result


def _positive_int(value: Any, *, fallback: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def parse_worker_lscpu_topology(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    fields = _lscpu_field_map(payload)
    topology = {
        "cpus": _positive_int(fields.get("cpu(s)"), fallback=0),
        "boards": 1,
        "sockets": _positive_int(fields.get("socket(s)"), fallback=0),
        "cores_per_socket": _positive_int(fields.get("core(s) per socket"), fallback=0),
        "threads_per_core": _positive_int(fields.get("thread(s) per core"), fallback=0),
    }
    if not all(
        _positive_int(topology.get(key), fallback=0)
        for key in ("cpus", "sockets", "cores_per_socket", "threads_per_core")
    ):
        return {}
    return topology


def _worker_pods_by_nodeset(workloads: Mapping[str, Any]) -> dict[str, str]:
    pods: dict[str, str] = {}
    for item in workloads.get("items", []) if isinstance(workloads, Mapping) else []:
        if not isinstance(item, Mapping) or str(item.get("kind", "") or "") != "Pod":
            continue
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        if str(status.get("phase", "") or "") != "Running":
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        labels = metadata.get("labels") if isinstance(metadata.get("labels"), Mapping) else {}
        pod_name = str(metadata.get("name", "") or "").strip()
        nodeset_name = ""
        for key in SOPERATOR_NODESET_LABEL_KEYS:
            candidate = normalize_component_token(labels.get(key))
            if candidate.startswith(SOPERATOR_WORKER_ROLE_PREFIX):
                nodeset_name = candidate
                break
        if pod_name and nodeset_name:
            pods.setdefault(nodeset_name, pod_name)
    return pods


def _collect_worker_topology_by_nodeset(
    *,
    kube_context: str,
    workloads: Mapping[str, Any],
    timeout: int,
    errors: list[dict[str, Any]],
    extra_env: Mapping[str, str] | None,
) -> dict[str, Any]:
    topology_by_nodeset: dict[str, Any] = {}
    pod_namespaces = {
        str(_mapping_value(item.get("metadata")).get("name") or "").strip(): str(
            _mapping_value(item.get("metadata")).get("namespace") or "soperator"
        ).strip()
        for item in _sequence_of_mappings(workloads.get("items"))
        if str(item.get("kind") or "") == "Pod"
    }
    for nodeset_name, pod_name in sorted(_worker_pods_by_nodeset(workloads).items()):
        stdout = _kubectl_text(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                pod_namespaces.get(pod_name, "soperator"),
                "exec",
                pod_name,
                "-c",
                "slurmd",
                "--",
                "lscpu",
                "-J",
            ],
            timeout,
            errors=errors,
            extra_env=extra_env,
        )
        topology = parse_worker_lscpu_topology(stdout)
        if topology:
            topology["source_pod"] = pod_name
            topology_by_nodeset[nodeset_name] = topology
    return topology_by_nodeset
