"""Flux rendering orchestration (generic Helm release model)."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .component_defaults import (
    resolve_component_defaults,
    set_component_path,
)
from .component_sources import component_input_binding_ref
from .component_wiring import (
    _UNRESOLVED,
    component_entry_lookup,
    component_output_ref,
    input_binding_conflicts,
    output_lookup,
    resolve_component_output_value,
    resolve_input_binding_source,
)
from .components import component_entries, component_entry_chart_name
from .deploy_targets import (
    app_chart_target_ref,
    enabled_cluster_target_refs,
    flux_target_dir,
)
from .mk8s_gpu import (
    mk8s_gpu_flux_release_dependencies,
    mk8s_gpu_flux_release_post_render_patches,
)
from .mysterybox_eso import (
    MYSTERYBOX_ESO_MANAGED_LABEL,
    MYSTERYBOX_ESO_MANAGED_VALUE,
    mysterybox_eso_extra_objects_for_target,
)
from .paths import ProjectPaths
from .runtime_config import to_plain_data


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _helm_repository_doc(name: str, url: str, *, repo_type: str = "default") -> dict[str, Any]:
    spec: dict[str, Any] = {"interval": "30m", "url": url}
    if repo_type == "oci":
        spec["type"] = "oci"
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": spec,
    }


def _git_repository_doc(name: str, url: str, ref: str) -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "GitRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {"interval": "5m", "url": url, "ref": {"branch": ref}},
    }


def _multi_doc_yaml(docs: list[dict[str, Any]]) -> str:
    return "\n---\n".join(yaml.safe_dump(doc, sort_keys=False).strip() for doc in docs) + "\n"


def _namespace_doc(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name},
    }


def _configmap_doc(
    *,
    name: str,
    namespace: str,
    data: dict[str, str],
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": data,
    }


def _github_tree_ref(repo: str) -> tuple[str, str, str] | None:
    token = repo.strip().rstrip("/")
    if not token:
        return None
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$", token)
    if not match:
        return None
    owner = match.group(1).strip()
    repository = match.group(2).strip()
    branch = match.group(3).strip()
    chart_path = match.group(4).strip()
    if not owner or not repository or not branch or not chart_path:
        return None
    return f"https://github.com/{owner}/{repository}.git", branch, chart_path


def _is_oci_repo(token: str) -> bool:
    return token.strip().lower().startswith("oci://")


def _oci_repo_url(chart_repo: str, chart_name: str) -> str:
    repo = chart_repo.strip().rstrip("/")
    if not repo:
        return repo
    chart = chart_name.strip().strip("/")
    if not chart:
        return repo
    repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip().lower()
    if repo_tail != chart.lower():
        return repo
    parent = repo.rsplit("/", maxsplit=1)[0].rstrip("/")
    return parent or repo


def _resolve_flux_chart_source(*, chart_name: str, chart_repo: str) -> dict[str, str]:
    if chart_repo:
        github_tree = _github_tree_ref(chart_repo)
        if github_tree is None:
            if _is_oci_repo(chart_repo):
                return {
                    "kind": "helm_repository",
                    "chart_ref": chart_name,
                    "repo_url": _oci_repo_url(chart_repo, chart_name),
                    "repo_type": "oci",
                }
            return {
                "kind": "helm_repository",
                "chart_ref": chart_name,
                "repo_url": chart_repo,
                "repo_type": "default",
            }
        git_url, git_ref, chart_path = github_tree
        return {
            "kind": "git_repository",
            "chart_ref": chart_path,
            "repo_url": git_url,
            "repo_ref": git_ref,
        }

    github_tree = _github_tree_ref(chart_name)
    if github_tree is not None:
        git_url, git_ref, chart_path = github_tree
        return {
            "kind": "git_repository",
            "chart_ref": chart_path,
            "repo_url": git_url,
            "repo_ref": git_ref,
        }

    raise ValueError(
        "chart.repo is required for Helm repository charts, "
        "or use a GitHub tree URL as chart.repo/chart.name for standalone chart sources"
    )


def _runtime_app_chart_name(
    *,
    chart_node: dict[str, Any],
    entry_id: str,
    configured_chart_name: str | None,
) -> str:
    repo = str(chart_node.get("repo", "")).strip().rstrip("/")
    if repo.startswith("oci://") and "/" in repo:
        repo_tail = repo.rsplit("/", maxsplit=1)[-1].strip()
        if configured_chart_name and repo_tail.lower() == configured_chart_name.lower():
            return repo_tail
        if repo_tail and not configured_chart_name:
            return repo_tail
    if configured_chart_name:
        return configured_chart_name
    return entry_id


def _file_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in value)


def _configured_app_release_specs(
    config: Any,
    *,
    component_output_values: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return []

    specs: list[dict[str, Any]] = []
    resolved_component_outputs = component_output_values or {}
    entry_by_id = {entry.id: entry for entry in component_entries("apps")}
    all_entry_by_id = component_entry_lookup()
    cluster_target_refs = set(enabled_cluster_target_refs(payload))
    apps_node = payload.get("apps")
    if isinstance(apps_node, dict):
        dynamic_charts = apps_node.get("charts")
        if isinstance(dynamic_charts, list):
            for raw_chart in dynamic_charts:
                if not isinstance(raw_chart, dict):
                    continue
                entry_id = str(raw_chart.get("id", "")).strip().lower()
                if not entry_id or not bool(raw_chart.get("enabled", False)):
                    continue
                entry = entry_by_id.get(entry_id)
                chart_node = dict(raw_chart)
                instance_id = str(chart_node.get("instance_id", entry_id)).strip() or entry_id
                target_ref = app_chart_target_ref(chart_node)
                if cluster_target_refs:
                    if not target_ref and instance_id in cluster_target_refs:
                        target_ref = instance_id
                    if not target_ref:
                        raise ValueError(
                            f"apps.charts[{instance_id}].instance_id must reference one of the enabled cluster targets"
                        )
                    if target_ref not in cluster_target_refs:
                        available = ", ".join(sorted(cluster_target_refs))
                        raise ValueError(
                            f"apps.charts[{instance_id}].instance_id must reference one of the enabled cluster targets: {available}"
                        )
                else:
                    target_ref = ""
                if entry is not None and entry.defaults:
                    chart_node = resolve_component_defaults(
                        payload=payload,
                        component_node=chart_node,
                        entry=entry,
                        preserve_existing_literal=True,
                        preserve_existing_shared=False,
                        include_shared=False,
                    )

                if entry is not None and entry.input_bindings:
                    conflicts = input_binding_conflicts(chart_node, entry)
                    if conflicts:
                        target_path, source_ref = conflicts[0]
                        raise ValueError(
                            f"apps chart '{entry_id}' field '{target_path}' is managed by component input "
                            f"binding '{source_ref}' and must not be set explicitly"
                        )
                    for binding in entry.input_bindings:
                        declared_source_ref = component_input_binding_ref(binding)
                        source_entry = all_entry_by_id.get(binding.source_component_id)
                        if source_entry is None:
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' references "
                                f"unknown component '{binding.source_component_id}'"
                            )
                        source_output = output_lookup(source_entry).get(binding.source_output_name)
                        if source_output is None:
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' references "
                                f"undeclared output '{declared_source_ref}'"
                            )
                        _resolved_source_entry, resolved_source_row, source_instance_id = (
                            resolve_input_binding_source(payload, binding=binding)
                        )
                        source_ref = (
                            component_output_ref(source_instance_id, binding.source_output_name)
                            if source_instance_id
                            else declared_source_ref
                        )

                        static_value = resolve_component_output_value(
                            payload,
                            component_id=binding.source_component_id,
                            output_name=binding.source_output_name,
                            instance_id=source_instance_id or binding.source_instance_id,
                        )
                        if static_value is not _UNRESOLVED:
                            set_component_path(chart_node, binding.target_path, static_value)
                            continue

                        if source_output.kind != "terraform_output":
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' could not "
                                f"resolve output '{source_ref}' from the current config payload"
                            )
                        if source_ref not in resolved_component_outputs:
                            raise ValueError(
                                f"apps chart '{entry_id}' input binding '{binding.target_path}' requires "
                                f"Terraform-derived output '{source_ref}', but that value is not available yet. "
                                "Run `deploy`, or rerun `render` after Terraform state exists."
                            )
                        set_component_path(
                            chart_node,
                            binding.target_path,
                            resolved_component_outputs[source_ref],
                        )

                values_node = chart_node.get("values", {})
                if values_node is None:
                    values_node = {}
                if not isinstance(values_node, dict):
                    raise ValueError(
                        f"apps.charts[{entry_id}].values must be a mapping for enabled chart '{entry_id}'"
                    )

                chart_repo = str(chart_node.get("repo", "")).strip()
                chart_name = _runtime_app_chart_name(
                    chart_node=chart_node,
                    entry_id=entry_id,
                    configured_chart_name=component_entry_chart_name(entry),
                )
                chart_version = str(chart_node.get("version", "")).strip()
                if not chart_repo:
                    raise ValueError(
                        f"apps.charts[{entry_id}].repo is required for enabled chart '{entry_id}'"
                    )
                source = _resolve_flux_chart_source(chart_name=chart_name, chart_repo=chart_repo)
                source_kind = source["kind"]
                chart_ref = source["chart_ref"]
                source_url = source["repo_url"]
                source_ref = source.get("repo_ref", "")
                source_repo_type = source.get("repo_type", "default")
                if source_kind == "helm_repository" and not chart_version:
                    raise ValueError(
                        f"apps.charts[{entry_id}].version is required for enabled chart '{entry_id}'"
                    )

                namespace = str(chart_node.get("namespace", "")).strip()
                if not namespace:
                    raise ValueError(
                        f"apps.charts[{entry_id}].namespace is required for enabled chart '{entry_id}'"
                    )
                release_name = str(chart_node.get("release-name", entry_id)).strip() or entry_id
                repo_name_default = f"helm-{_file_slug(entry_id)}"
                repo_name = repo_name_default
                interval = "5m"
                scope = str(chart_node.get("group", "")).strip().lower() or "workloads"
                chart_values = values_node
                release_timeout = (
                    str(entry.default_release_timeout or "").strip() if entry is not None else ""
                )

                specs.append(
                    {
                        "scope": scope,
                        "entry_id": entry_id,
                        "instance_id": instance_id,
                        "target_ref": target_ref,
                        "release_name": release_name,
                        "namespace": namespace,
                        "source_name": repo_name,
                        "source_kind": source_kind,
                        "source_url": source_url,
                        "source_ref": source_ref,
                        "source_repo_type": source_repo_type,
                        "chart_ref": chart_ref,
                        "chart_version": chart_version if source_kind == "helm_repository" else "",
                        "interval": interval,
                        "timeout": release_timeout,
                        "values": chart_values,
                        "depends_on": [],
                        "install_after": tuple(entry.default_release_install_after or ())
                        if entry is not None
                        else (),
                    }
                )
    dependency_map = mk8s_gpu_flux_release_dependencies(
        payload,
        release_entry_ids={
            str(item.get("entry_id", "")).strip()
            for item in specs
            if str(item.get("entry_id", "")).strip()
        },
    )
    specs_by_target_and_entry_id: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in specs:
        entry_id = str(item.get("entry_id", "")).strip()
        if not entry_id:
            continue
        key = (str(item.get("target_ref", "")).strip(), entry_id)
        specs_by_target_and_entry_id.setdefault(key, []).append(item)
    post_render_patch_map = mk8s_gpu_flux_release_post_render_patches(
        payload,
        release_entry_ids={
            str(item.get("entry_id", "")).strip()
            for item in specs
            if str(item.get("entry_id", "")).strip()
        },
    )
    for release in specs:
        entry_id = str(release.get("entry_id", "")).strip()
        if not entry_id:
            continue
        target_ref = str(release.get("target_ref", "")).strip()
        dependency_ids = tuple(
            dict.fromkeys(
                tuple(release.get("install_after") or ()) + tuple(dependency_map.get(entry_id, ()))
            )
        )
        depends_on: list[dict[str, str]] = []
        for dependency_id in dependency_ids:
            for dependency in specs_by_target_and_entry_id.get((target_ref, dependency_id), ()):
                depends_on.append(
                    {
                        "name": str(dependency["release_name"]),
                        "namespace": str(dependency["namespace"]),
                    }
                )
        if depends_on:
            release["depends_on"] = depends_on
        patches = post_render_patch_map.get((target_ref, entry_id))
        if patches:
            release["post_render_patches"] = [dict(item) for item in patches]
    return specs


def _helm_release_doc(
    *,
    release_name: str,
    namespace: str,
    source_name: str,
    source_kind: str,
    chart_ref: str,
    chart_version: str | None,
    interval: str,
    timeout: str | None,
    values: dict[str, Any],
    depends_on: list[dict[str, str]] | None = None,
    post_render_patches: list[dict[str, Any]] | None = None,
    disable_wait: bool = False,
) -> dict[str, Any]:
    chart_spec: dict[str, Any] = {
        "chart": chart_ref,
        "sourceRef": {
            "kind": source_kind,
            "name": source_name,
            "namespace": "flux-system",
        },
    }
    if chart_version:
        chart_spec["version"] = chart_version
    spec: dict[str, Any] = {
        "interval": interval,
        "chart": {"spec": chart_spec},
        "install": {"createNamespace": True},
        "values": values,
    }
    if disable_wait:
        spec["install"]["disableWait"] = True
        spec["upgrade"] = {"disableWait": True}
    if timeout:
        spec["timeout"] = timeout
    if depends_on:
        spec["dependsOn"] = depends_on
    if post_render_patches:
        spec["postRenderers"] = [
            {
                "kustomize": {
                    "patches": [
                        {
                            "target": dict(item.get("target", {}))
                            if isinstance(item.get("target"), dict)
                            else {},
                            "patch": str(item.get("patch", "")).strip(),
                        }
                        for item in post_render_patches
                        if str(item.get("patch", "")).strip()
                    ]
                }
            }
        ]
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": release_name, "namespace": namespace},
        "spec": spec,
    }


def _release_has_managed_mysterybox_external_secret(values: Mapping[str, Any]) -> bool:
    extra_objects = values.get("extraObjects")
    if not isinstance(extra_objects, list):
        return False
    for item in extra_objects:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "ExternalSecret":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        labels = metadata.get("labels")
        if not isinstance(labels, dict):
            continue
        if labels.get(MYSTERYBOX_ESO_MANAGED_LABEL) == MYSTERYBOX_ESO_MANAGED_VALUE:
            return True
    return False


def _pretty_dashboard_json(raw_json: str) -> str:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return raw_json.rstrip() + "\n"
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _dashboard_file_name(dashboard_name: str) -> str:
    return f"{_file_slug(dashboard_name)}.json"


def _target_dashboard_dir_name(target_ref: str) -> str:
    return _file_slug(target_ref) if target_ref else "current-cluster"


def _externalize_dashboard_json_values(
    state: _FluxRenderState,
    *,
    release: dict[str, Any],
) -> None:
    values = release.get("values")
    if not isinstance(values, dict):
        return
    dashboards = values.get("dashboards")
    if not isinstance(dashboards, dict):
        return

    release_name = str(release["release_name"])
    namespace = str(release["namespace"])
    target_ref = str(release.get("target_ref", "")).strip()
    target_dir = _target_dashboard_dir_name(target_ref)
    next_dashboards = copy.deepcopy(dashboards)
    dashboards_config_maps = copy.deepcopy(values.get("dashboardsConfigMaps") or {})
    if not isinstance(dashboards_config_maps, dict):
        dashboards_config_maps = {}

    for folder, folder_dashboards in list(dashboards.items()):
        if not isinstance(folder_dashboards, dict):
            continue
        folder_key = str(folder)
        configmap_data: dict[str, str] = {}
        next_folder = copy.deepcopy(next_dashboards.get(folder, {}))
        if not isinstance(next_folder, dict):
            next_folder = {}
        for dashboard_name, dashboard_spec in list(folder_dashboards.items()):
            if not isinstance(dashboard_spec, dict) or "json" not in dashboard_spec:
                continue
            dashboard_json = _pretty_dashboard_json(str(dashboard_spec.get("json") or ""))
            file_name = _dashboard_file_name(str(dashboard_name))
            dashboard_path = (
                state.paths.generated_dir
                / "grafana_dashboards"
                / target_dir
                / folder_key
                / file_name
            )
            _write_text(dashboard_path, dashboard_json)
            state.files.append(dashboard_path)
            configmap_data[file_name] = dashboard_json
            next_folder.pop(dashboard_name, None)

        if not configmap_data:
            continue
        configmap_name = _file_slug(f"{release_name}-{folder_key}-dashboards")
        dashboards_config_maps[folder_key] = configmap_name
        configmap_file = Path(f"configmap-{_file_slug(release_name)}-{_file_slug(folder_key)}-dashboards.yaml")
        state.write_doc(
            configmap_file,
            _configmap_doc(
                name=configmap_name,
                namespace=namespace,
                data=configmap_data,
            ),
        )
        if next_folder:
            next_dashboards[folder] = next_folder
        else:
            next_dashboards.pop(folder, None)

    if dashboards_config_maps:
        values["dashboardsConfigMaps"] = dashboards_config_maps
    if next_dashboards:
        values["dashboards"] = next_dashboards
    else:
        values.pop("dashboards", None)


@dataclass
class _FluxRenderState:
    paths: ProjectPaths
    files: list[Path] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    repositories: list[dict[str, Any]] = field(default_factory=list)
    seen_repo_names: set[str] = field(default_factory=set)
    seen_namespaces: set[str] = field(default_factory=set)

    def add_repository_doc(self, *, repo_name: str, doc: dict[str, Any]) -> None:
        if repo_name in self.seen_repo_names:
            return
        self.repositories.append(doc)
        self.seen_repo_names.add(repo_name)

    def ensure_namespace(self, namespace: str) -> None:
        normalized = namespace.strip()
        if not normalized or normalized in self.seen_namespaces:
            return
        namespace_file = Path(f"namespace-{_file_slug(normalized)}.yaml")
        absolute_file = self.paths.flux_dir / namespace_file
        _write_text(absolute_file, yaml.safe_dump(_namespace_doc(normalized), sort_keys=False))
        self.files.append(absolute_file)
        self.resources.append(f"./{namespace_file.as_posix()}")
        self.seen_namespaces.add(normalized)

    def write_doc(self, relative_file: Path, doc: dict[str, Any]) -> None:
        absolute_file = self.paths.flux_dir / relative_file
        _write_text(absolute_file, yaml.safe_dump(doc, sort_keys=False))
        self.files.append(absolute_file)
        self.resources.append(f"./{relative_file.as_posix()}")

    def write_post_flux_docs(self, relative_file: Path, docs: list[dict[str, Any]]) -> None:
        absolute_file = self.paths.flux_dir / relative_file
        if not docs:
            absolute_file.unlink(missing_ok=True)
            return
        _write_text(absolute_file, _multi_doc_yaml(docs))
        self.files.append(absolute_file)


def _finalize_flux_render_state(state: _FluxRenderState) -> None:
    repos_path = state.paths.flux_dir / "helm-repositories.yaml"
    if state.repositories:
        _ensure_parent(repos_path)
        repo_yaml = (
            "\n---\n".join(
                yaml.safe_dump(doc, sort_keys=False).strip() for doc in state.repositories
            )
            + "\n"
        )
        repos_path.write_text(repo_yaml, encoding="utf-8")
        state.files.append(repos_path)
        state.resources.insert(0, "./helm-repositories.yaml")
    else:
        repos_path.unlink(missing_ok=True)

    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": state.resources,
    }
    kustomization_path = state.paths.flux_dir / "kustomization.yaml"
    _write_text(kustomization_path, yaml.safe_dump(kustomization_doc, sort_keys=False))
    state.files.append(kustomization_path)


def _render_flux_app_helm_releases(
    state: _FluxRenderState, *, release_specs: list[dict[str, Any]]
) -> None:
    for release in release_specs:
        scope = release["scope"]
        release_name = release["release_name"]
        namespace = release["namespace"]
        source_name = release["source_name"]
        source_kind = release["source_kind"]
        source_url = release["source_url"]
        source_ref = release.get("source_ref", "")
        source_repo_type = release.get("source_repo_type", "default")
        if source_kind == "helm_repository":
            state.add_repository_doc(
                repo_name=source_name,
                doc=_helm_repository_doc(source_name, source_url, repo_type=source_repo_type),
            )
            flux_source_kind = "HelmRepository"
        else:
            state.add_repository_doc(
                repo_name=source_name,
                doc=_git_repository_doc(source_name, source_url, source_ref or "main"),
            )
            flux_source_kind = "GitRepository"
        state.ensure_namespace(namespace)
        _externalize_dashboard_json_values(state, release=release)
        release_file_name = f"helmrelease-{_file_slug(scope)}-{_file_slug(release_name)}.yaml"
        state.write_doc(
            Path(release_file_name),
            _helm_release_doc(
                release_name=release_name,
                namespace=namespace,
                source_name=source_name,
                source_kind=flux_source_kind,
                chart_ref=release["chart_ref"],
                chart_version=release["chart_version"],
                interval=release["interval"],
                timeout=release["timeout"] or None,
                values=release["values"],
                depends_on=release.get("depends_on") or None,
                post_render_patches=release.get("post_render_patches") or None,
                disable_wait=_release_has_managed_mysterybox_external_secret(release["values"]),
            ),
        )


def _render_post_flux_mysterybox_eso_resources(
    state: _FluxRenderState,
    *,
    config: Any,
    target_ref: str,
    component_output_values: dict[str, Any] | None,
) -> None:
    objects = mysterybox_eso_extra_objects_for_target(
        config,
        target_ref=target_ref,
        component_output_values=component_output_values or {},
    )
    state.write_post_flux_docs(Path("post-flux-mysterybox-eso.yaml"), objects)


def render_flux(
    config: Any,
    paths: ProjectPaths,
    *,
    component_output_values: dict[str, Any] | None = None,
) -> list[Path]:
    legacy_dirs = [
        path
        for path in (paths.flux_dir / "apps", paths.flux_dir / "sources")
        if path.exists() and path.is_dir()
    ]
    if legacy_dirs:
        locations = ", ".join(str(path) for path in legacy_dirs)
        raise ValueError(
            "Unsupported legacy Flux layout detected. "
            "Use only flat files under generated/flux and remove: "
            f"{locations}"
        )

    release_specs = _configured_app_release_specs(
        config,
        component_output_values=component_output_values,
    )
    cluster_target_refs = enabled_cluster_target_refs(config)
    if cluster_target_refs:
        written: list[Path] = []
        release_specs_by_target: dict[str, list[dict[str, Any]]] = {
            target_ref: [] for target_ref in cluster_target_refs
        }
        for item in release_specs:
            release_specs_by_target.setdefault(str(item.get("target_ref", "")).strip(), []).append(
                item
            )
        for target_ref in cluster_target_refs:
            target_paths = replace(paths, flux_dir=flux_target_dir(paths, target_ref))
            state = _FluxRenderState(paths=target_paths)
            _render_flux_app_helm_releases(
                state,
                release_specs=release_specs_by_target.get(target_ref, []),
            )
            _render_post_flux_mysterybox_eso_resources(
                state,
                config=config,
                target_ref=target_ref,
                component_output_values=component_output_values,
            )
            _finalize_flux_render_state(state)
            written.extend(state.files)
        return written

    state = _FluxRenderState(paths=paths)
    _render_flux_app_helm_releases(state, release_specs=release_specs)
    _render_post_flux_mysterybox_eso_resources(
        state,
        config=config,
        target_ref="",
        component_output_values=component_output_values,
    )
    _finalize_flux_render_state(state)
    return state.files


__all__ = ["render_flux"]
