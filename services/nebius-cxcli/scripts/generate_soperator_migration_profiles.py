#!/usr/bin/env python3
"""Refresh committed Soperator migration profile history from GitHub releases."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

RELEASES_URL = "https://api.github.com/repos/nebius/soperator/releases"
RELEASES_PER_PAGE = 100
RELEASE_TARBALL_URL = "https://github.com/nebius/soperator/archive/refs/tags/{tag}.tar.gz"
GENERATOR_SCOPE = "chart-tarball-crd-template-image-and-slurm-contract-fingerprints"
MAIN_CHART_IDENTITIES = frozenset({"soperator", "helm-soperator", "slurm-operator"})
SLURM_CONTRACT_KEYWORDS = (
    "activecheck",
    "jailedconfig",
    "munge",
    "nodeconfigurator",
    "nodeset",
    "sconfig",
    "slurm",
)
SLURM_CONTRACT_MARKERS = (
    "slurm.nebius.ai",
    "slurmcluster",
    "nodeset",
    "nodeconfigurator",
    "jailedconfig",
    "activecheck",
)
IMAGE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_./:-])"
    r"((?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/)+"
    r"[A-Za-z0-9._/-]+:[A-Za-z0-9._+:-]+)"
)


def _normalize_version(tag: str) -> str:
    return str(tag or "").strip().removeprefix("v")


def _generation(version: str) -> str:
    major = int(version.split(".", 1)[0])
    if major <= 1:
        return "legacy-v1"
    return f"v{major}"


def _migration_class(generation: str) -> str:
    return "adopt-or-install" if generation == "v4" else "storage-and-layout-migration"


def _profile_id(generation: str) -> str:
    return f"{generation}-to-target"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash_json(value: Any) -> str:
    return _sha256_bytes(_stable_json(value).encode("utf-8"))


def _fetch_releases() -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"{RELEASES_URL}?per_page={RELEASES_PER_PAGE}&page={page}"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, list):
            raise RuntimeError("GitHub releases API returned a non-list payload")
        rows = [row for row in payload if isinstance(row, dict)]
        releases.extend(rows)
        if len(rows) < RELEASES_PER_PAGE:
            break
        page += 1
    return releases


def _fetch_url_bytes(url: str, *, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def _release_tarball_url(tag: str) -> str:
    quoted = urllib.parse.quote(tag, safe="")
    return RELEASE_TARBALL_URL.format(tag=quoted)


def _safe_extract_tarball(tarball: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        archive.extractall(destination, filter="data")


def _extracted_repo_root(destination: Path) -> Path:
    children = [child for child in destination.iterdir() if child.is_dir()]
    if len(children) == 1:
        return children[0]
    if (destination / "helm").is_dir():
        return destination
    raise RuntimeError("Soperator release tarball did not extract to a single repository root")


def _discover_chart_dirs(repo_root: Path) -> list[Path]:
    helm_root = repo_root / "helm"
    if not helm_root.is_dir():
        return []
    return sorted(
        path
        for path in helm_root.iterdir()
        if path.is_dir() and (path / "Chart.yaml").is_file()
    )


def _load_yaml_file(path: Path) -> Mapping[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _chart_name_from_metadata(chart_dir: Path) -> str:
    metadata = _load_yaml_file(chart_dir / "Chart.yaml")
    return str(metadata.get("name", "") or "").strip()


def _component_id(chart_dir: Path, chart_name: str) -> str:
    normalized_name = chart_name.strip().lower()
    if normalized_name in {"helm-soperator", "soperator"}:
        return "soperator"
    if normalized_name == "slurm-operator":
        return "slurm-operator"
    return chart_dir.name


def _iter_chart_file_paths(chart_dir: Path) -> tuple[Path, ...]:
    paths = [
        path.relative_to(chart_dir)
        for path in chart_dir.rglob("*")
        if path.is_file()
    ]
    return tuple(sorted(paths, key=lambda item: item.as_posix()))


def _fingerprint_files(
    root: Path,
    relative_paths: Iterable[Path],
    *,
    include_files: bool = False,
) -> dict[str, Any]:
    digest = hashlib.sha256()
    files = tuple(sorted(set(relative_paths), key=lambda item: item.as_posix()))
    for relative_path in files:
        data = (root / relative_path).read_bytes()
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    result: dict[str, Any] = {
        "sha256": digest.hexdigest(),
        "file_count": len(files),
    }
    if include_files:
        result["files"] = [path.as_posix() for path in files]
    return result


def _fingerprint_strings(values: Iterable[str], *, include_values: bool = False) -> dict[str, Any]:
    items = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    result: dict[str, Any] = {
        "sha256": _hash_json(items),
        "count": len(items),
    }
    if include_values:
        result["values"] = list(items)
    return result


def _looks_like_image_repo(value: str) -> bool:
    text = str(value or "").strip()
    if not text or "{{" in text or "}}" in text:
        return False
    return "/" in text and (
        "." in text.split("/", 1)[0]
        or ":" in text.split("/", 1)[0]
        or text.startswith(("cr.", "docker.io/", "ghcr.io/", "nvcr.io/"))
    )


def _collect_images_from_yaml(value: Any, refs: set[str]) -> None:
    if isinstance(value, Mapping):
        repository = value.get("repository")
        tag = value.get("tag")
        if isinstance(repository, str) and _looks_like_image_repo(repository):
            if isinstance(tag, str) and tag.strip() and "{{" not in tag:
                refs.add(f"{repository.strip()}:{tag.strip()}")
            else:
                refs.add(repository.strip())
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in {"image", "imagename", "containerimage"} and isinstance(child, str):
                image = child.strip()
                if _looks_like_image_repo(image) or ("/" in image and ":" in image):
                    refs.add(image)
            _collect_images_from_yaml(child, refs)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _collect_images_from_yaml(child, refs)


def _image_refs_for_chart(chart_dir: Path, relative_paths: Sequence[Path]) -> tuple[str, ...]:
    refs: set[str] = set()
    for yaml_name in ("values.yaml", "values.yml"):
        yaml_path = chart_dir / yaml_name
        if yaml_path.exists():
            _collect_images_from_yaml(_load_yaml_file(yaml_path), refs)
    for relative_path in relative_paths:
        suffix = relative_path.suffix.lower()
        if suffix not in {".yaml", ".yml", ".tpl", ".txt"}:
            continue
        text = (chart_dir / relative_path).read_text(encoding="utf-8", errors="ignore")
        for match in IMAGE_REF_RE.finditer(text):
            refs.add(match.group(1).strip("'\""))
    return tuple(sorted(refs))


def _is_slurm_contract_file(chart_dir: Path, relative_path: Path) -> bool:
    lower_path = relative_path.as_posix().lower()
    if any(keyword in lower_path for keyword in SLURM_CONTRACT_KEYWORDS):
        return True
    suffix = relative_path.suffix.lower()
    if suffix not in {".yaml", ".yml", ".tpl", ".txt", ".conf", ".sh"}:
        return False
    text = (chart_dir / relative_path).read_text(encoding="utf-8", errors="ignore").lower()
    return any(marker in text for marker in SLURM_CONTRACT_MARKERS)


def _chart_contract(repo_root: Path, chart_dir: Path) -> dict[str, Any]:
    metadata = _load_yaml_file(chart_dir / "Chart.yaml")
    chart_name = str(metadata.get("name", "") or "").strip()
    chart_version = str(metadata.get("version", "") or "").strip()
    app_version = str(metadata.get("appVersion", "") or metadata.get("app_version", "") or "").strip()
    file_paths = _iter_chart_file_paths(chart_dir)
    crd_paths = tuple(
        path
        for path in file_paths
        if path.parts and (path.parts[0] == "crds" or "crd" in path.name.lower())
    )
    template_paths = tuple(path for path in file_paths if path.parts and path.parts[0] == "templates")
    values_paths = tuple(
        path
        for path in file_paths
        if path.as_posix() in {"Chart.yaml", "Chart.lock", "values.yaml", "values.yml"}
    )
    slurm_paths = tuple(path for path in file_paths if _is_slurm_contract_file(chart_dir, path))
    image_refs = _image_refs_for_chart(chart_dir, file_paths)
    contract = {
        "id": _component_id(chart_dir, chart_name),
        "chart_path": chart_dir.relative_to(repo_root).as_posix(),
        "chart_name": chart_name,
        "chart_version": chart_version,
        "app_version": app_version,
        "chart_archive": _fingerprint_files(chart_dir, file_paths),
        "crds": _fingerprint_files(chart_dir, crd_paths, include_files=True),
        "templates": _fingerprint_files(chart_dir, template_paths, include_files=True),
        "values": _fingerprint_files(chart_dir, values_paths, include_files=True),
        "images": _fingerprint_strings(image_refs, include_values=True),
        "slurm_contract": _fingerprint_files(chart_dir, slurm_paths, include_files=True),
    }
    contract["contract_fingerprint"] = _hash_json(
        {
            "chart_archive": contract["chart_archive"]["sha256"],
            "crds": contract["crds"]["sha256"],
            "templates": contract["templates"]["sha256"],
            "values": contract["values"]["sha256"],
            "images": contract["images"]["sha256"],
            "slurm_contract": contract["slurm_contract"]["sha256"],
        }
    )
    return contract


def _main_chart_contract(components: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for component in components:
        chart_name = str(component.get("chart_name", "") or "").strip().lower()
        chart_path = str(component.get("chart_path", "") or "").strip().lower()
        component_id = str(component.get("id", "") or "").strip().lower()
        if chart_name in MAIN_CHART_IDENTITIES or component_id == "soperator":
            return component
        if chart_path in {"helm/soperator", "helm/slurm-operator"}:
            return component
    return components[0] if components else {}


def _release_contract_from_tarball(tarball: bytes) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="soperator-profile-") as tmp:
        destination = Path(tmp)
        _safe_extract_tarball(tarball, destination)
        repo_root = _extracted_repo_root(destination)
        chart_dirs = _discover_chart_dirs(repo_root)
        components = [_chart_contract(repo_root, chart_dir) for chart_dir in chart_dirs]
    main_chart = dict(_main_chart_contract(components))
    component_summaries = [
        {
            "id": component.get("id"),
            "chart_path": component.get("chart_path"),
            "contract_fingerprint": component.get("contract_fingerprint"),
            "chart_archive": component.get("chart_archive", {}).get("sha256")
            if isinstance(component.get("chart_archive"), Mapping)
            else "",
            "crds": component.get("crds", {}).get("sha256")
            if isinstance(component.get("crds"), Mapping)
            else "",
            "templates": component.get("templates", {}).get("sha256")
            if isinstance(component.get("templates"), Mapping)
            else "",
            "images": component.get("images", {}).get("sha256")
            if isinstance(component.get("images"), Mapping)
            else "",
            "slurm_contract": component.get("slurm_contract", {}).get("sha256")
            if isinstance(component.get("slurm_contract"), Mapping)
            else "",
        }
        for component in components
    ]
    return {
        "source_tarball_sha256": _sha256_bytes(tarball),
        "main_chart": main_chart,
        "component_contracts": components,
        "contract_fingerprint": _hash_json(component_summaries),
    }


def _release_contract(release: Mapping[str, Any]) -> dict[str, Any]:
    tag = str(release.get("tag_name", "") or "").strip()
    if not tag:
        raise RuntimeError("Cannot fetch a Soperator release tarball without tag_name")
    return _release_contract_from_tarball(_fetch_url_bytes(_release_tarball_url(tag)))


def _version_aliases(
    release_version: str,
    upstream_tag: str,
    main_chart: Mapping[str, Any],
) -> list[str]:
    candidates = {
        release_version,
        upstream_tag,
        str(main_chart.get("chart_version", "") or ""),
        str(main_chart.get("app_version", "") or ""),
    }
    aliases: list[str] = []
    seen: set[str] = set()
    for candidate in sorted(candidates):
        normalized = _normalize_version(candidate)
        if not normalized or normalized == release_version or normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(normalized)
    return aliases


def _release_contract_for_row(
    release_contracts: Mapping[str, Mapping[str, Any]],
    *,
    tag: str,
    version: str,
) -> Mapping[str, Any]:
    return (
        release_contracts.get(tag)
        or release_contracts.get(version)
        or release_contracts.get(_normalize_version(tag))
        or {}
    )


def _node_label_layout(*, source_role_label_keys: Sequence[str], action: str) -> dict[str, Any]:
    return {
        "source_role_label_keys": list(source_role_label_keys),
        "target_role_label_key": "slurm.nebius.ai/nodeset-name",
        "target_role_label_values": [
            "system",
            "controller",
            "login",
            "accounting",
            "worker",
        ],
        "worker_prefix": "worker",
        "source_workload_label_key": "slurm.nebius.ai/workload",
        "jail_label_key": "slurm.nebius.ai/jail",
        "action": action,
    }


def _source_controller_quiesce_contract(
    *,
    include_slurm_operator: bool,
) -> dict[str, Any]:
    admission_webhooks: list[dict[str, str]] = [
        {
            "kind": "MutatingWebhookConfiguration",
            "name": "soperator-controller-mutating-webhook-configuration",
        },
        {
            "kind": "ValidatingWebhookConfiguration",
            "name": "soperator-controller-validating-webhook-configuration",
        },
    ]
    deployments: list[dict[str, str]] = [
        {
            "namespace": "soperator-system",
            "name": "soperator-controller-manager",
            "release_name": "soperator-controller",
            "chart_prefix": "helm-soperator",
        },
    ]
    if include_slurm_operator:
        admission_webhooks.extend(
            [
                {
                    "kind": "MutatingWebhookConfiguration",
                    "name": "slurm-operator-mutating-webhook-configuration",
                },
                {
                    "kind": "ValidatingWebhookConfiguration",
                    "name": "slurm-operator-validating-webhook-configuration",
                },
            ]
        )
        deployments.append(
            {
                "namespace": "soperator",
                "release_name": "slurm-operator",
                "chart_prefix": "slurm-operator",
            }
        )
    return {
        "source_controller_quiesce": {
            "required_before_target_compute_reconcile": True,
            "admission_webhooks": admission_webhooks,
            "deployments": deployments,
        }
    }


def _support_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "legacy-before-1-22-not-validated",
            "status": "not_validated",
            "source_version_range": "<1.22.0",
            "message": (
                "Soperator source versions older than 1.22.x are not validated by "
                "cxcli for this upgrade path. Run a smoke test or rerun with "
                "--allow-unsupported-soperator-upgrade-path for an explicit testing override."
            ),
            "references": [
                "https://github.com/nebius/soperator/releases",
            ],
        },
        {
            "id": "k8s-before-1-33-soperator-1-22-plus-supported",
            "status": "supported",
            "source_version_range": ">=1.22.0",
            "target_version_range": ">=1.22.0,<5.0.0",
            "target_k8s_max": "1.33",
            "message": (
                "Soperator 1.22.0 through 4.x targeting Kubernetes versions "
                "before 1.33 matches the committed cxcli upgrade-path policy. "
                "Kubernetes minor-hop validation still applies."
            ),
            "references": [
                "https://docs.nebius.com/kubernetes/versions",
            ],
        },
        {
            "id": "k8s-1-33-requires-soperator-1-23",
            "status": "unsupported",
            "target_k8s_min": "1.33",
            "target_version_range": "<1.23.0",
            "message": (
                "Kubernetes 1.33+ requires Soperator 1.23.0 or newer for this "
                "legacy path to avoid worker procMount/hostUsers admission failures."
            ),
            "references": [
                "https://github.com/nebius/soperator/issues/1446",
                "https://github.com/nebius/soperator/pull/1482",
                "https://kubernetes.io/blog/2025/04/25/userns-enabled-by-default/",
            ],
        },
        {
            "id": "k8s-1-33-procmount-control-warning",
            "status": "supported_with_warning",
            "target_k8s_min": "1.33",
            "target_version_range": ">=1.23.0,<2.0.0",
            "message": (
                "Soperator 1.23.x is allowed on Kubernetes 1.33+, but prefer "
                "Soperator 2.0.0 or newer when NFS, idmap, or explicit procMount "
                "control matters."
            ),
            "references": [
                "https://github.com/nebius/soperator/issues/1510",
                "https://github.com/nebius/soperator/pull/1512",
            ],
        },
        {
            "id": "k8s-1-33-activechecks-warning",
            "status": "supported_with_warning",
            "target_k8s_min": "1.33",
            "target_version_range": ">=2.0.0,<4.0.0",
            "message": (
                "Soperator 2.x/3.x is allowed on Kubernetes 1.33+, but prefer "
                "Soperator 4.0.0 or newer when ActiveChecks behavior matters."
            ),
            "references": [
                "https://github.com/nebius/soperator/pull/1980",
                "https://github.com/nebius/soperator/pull/2364",
            ],
        },
        {
            "id": "k8s-1-33-soperator-4-supported",
            "status": "supported",
            "target_k8s_min": "1.33",
            "target_version_range": ">=4.0.0,<5.0.0",
            "message": (
                "Soperator 4.x targeting Kubernetes 1.33+ matches the committed "
                "cxcli upgrade-path policy, including ActiveChecks hostUsers handling."
            ),
            "references": [
                "https://github.com/nebius/soperator/pull/1980",
                "https://github.com/nebius/soperator/pull/2364",
            ],
        },
    ]


def _profile_payload(
    releases: list[dict[str, Any]],
    *,
    release_contracts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    release_contracts = release_contracts or {}
    rows: list[dict[str, Any]] = []
    for release in reversed(releases):
        tag = str(release.get("tag_name", "") or "").strip()
        version = _normalize_version(tag)
        if not version:
            continue
        generation = _generation(version)
        contract = _release_contract_for_row(release_contracts, tag=tag, version=version)
        main_chart = contract.get("main_chart") if isinstance(contract, Mapping) else None
        if not isinstance(main_chart, Mapping):
            main_chart = {}
        row: dict[str, Any] = {
            "version": version,
            "upstream_tag": tag,
            "published_at": str(release.get("published_at", "") or "")[:10],
            "generation": generation,
            "profile_id": _profile_id(generation),
            "migration_class": _migration_class(generation),
        }
        aliases = _version_aliases(version, tag, main_chart)
        if aliases:
            row["version_aliases"] = aliases
        if main_chart:
            for source_key, row_key in (
                ("chart_path", "chart_path"),
                ("chart_name", "chart_name"),
                ("chart_version", "chart_version"),
                ("app_version", "app_version"),
            ):
                value = str(main_chart.get(source_key, "") or "").strip()
                if value:
                    row[row_key] = value
        if contract:
            for key in ("source_tarball_sha256", "contract_fingerprint", "component_contracts"):
                value = contract.get(key)
                if value:
                    row[key] = value
        rows.append(row)
    return {
        "schema": "nebius-cxcli-soperator-migration-profiles/v1",
        "source": "https://github.com/nebius/soperator/releases",
        "generated_from": "github-releases-api-and-release-tarballs",
        "target_policy": "component_sources.yaml pinned soperator chart version",
        "generator_scope": GENERATOR_SCOPE,
        "support_rules": _support_rules(),
        "profile_groups": {
            "legacy-v1-to-target": {
                "title": "Legacy v1 Soperator to pinned target",
                "requires_aligned_sfs": True,
                "migration_class": "storage-and-layout-migration",
                "compatibility_axes": {
                    "compute_layout": "replace-and-roll",
                    "storage_layout": "create-aligned-sfs-and-migrate",
                    "node_label_layout": _node_label_layout(
                        source_role_label_keys=(
                            "slurm.nebius.ai/nodeset",
                            "slurm.nebius.ai/nodeset-name",
                        ),
                        action="normalize-target-node-labels",
                    ),
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                    ],
                },
                "execution_contract": _source_controller_quiesce_contract(
                    include_slurm_operator=True
                ),
            },
            "v2-to-target": {
                "title": "Soperator 2.x to pinned target",
                "requires_aligned_sfs": True,
                "migration_class": "storage-and-layout-migration",
                "compatibility_axes": {
                    "compute_layout": "replace-and-roll",
                    "storage_layout": "create-aligned-sfs-and-migrate",
                    "node_label_layout": _node_label_layout(
                        source_role_label_keys=(
                            "slurm.nebius.ai/nodeset",
                            "slurm.nebius.ai/nodeset-name",
                        ),
                        action="normalize-target-node-labels",
                    ),
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                    ],
                },
                "execution_contract": _source_controller_quiesce_contract(
                    include_slurm_operator=False
                ),
            },
            "v3-to-target": {
                "title": "Soperator 3.x to pinned target",
                "requires_aligned_sfs": True,
                "migration_class": "storage-and-layout-migration",
                "compatibility_axes": {
                    "compute_layout": "replace-and-roll",
                    "storage_layout": "create-aligned-sfs-and-migrate",
                    "node_label_layout": _node_label_layout(
                        source_role_label_keys=(
                            "slurm.nebius.ai/nodeset",
                            "slurm.nebius.ai/nodeset-name",
                        ),
                        action="normalize-target-node-labels",
                    ),
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                    ],
                },
                "execution_contract": _source_controller_quiesce_contract(
                    include_slurm_operator=False
                ),
            },
            "v4-to-target": {
                "title": "Soperator 4.x to pinned target",
                "requires_aligned_sfs": False,
                "migration_class": "adopt-or-install",
                "compatibility_axes": {
                    "compute_layout": "adopt-or-reconcile",
                    "storage_layout": "adopt-existing-or-create-if-missing",
                    "node_label_layout": _node_label_layout(
                        source_role_label_keys=("slurm.nebius.ai/nodeset-name",),
                        action="adopt-or-reconcile-target-node-labels",
                    ),
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "NodeSetPowerState",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                        "JailedConfig",
                    ],
                },
            },
        },
        "releases": rows,
    }


def _fetch_release_contracts(releases: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    contracts: dict[str, Mapping[str, Any]] = {}
    for index, release in enumerate(releases, start=1):
        tag = str(release.get("tag_name", "") or "").strip()
        if not tag:
            continue
        print(f"Fetching Soperator release tarball {index}/{len(releases)}: {tag}", file=sys.stderr)
        contracts[tag] = _release_contract(release)
    return contracts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/nebius_cxcli/soperator_migration_profiles.yaml"),
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Refresh only release metadata and compatibility axes; skip tarball fingerprints.",
    )
    args = parser.parse_args()
    releases = _fetch_releases()
    release_contracts = {} if args.metadata_only else _fetch_release_contracts(releases)
    payload = _profile_payload(releases, release_contracts=release_contracts)
    args.output.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(payload['releases'])} Soperator migration profile records to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
