"""Flux rendering orchestration (generic Helm release model)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .component_defaults import (
    resolve_component_defaults,
    set_component_path,
    shared_default_conflicts,
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
from .components import component_entries
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


def _namespace_doc(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name},
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
                if entry is not None and entry.defaults:
                    chart_node = resolve_component_defaults(
                        payload=payload,
                        component_node=chart_node,
                        entry=entry,
                        preserve_existing_literal=True,
                        preserve_existing_shared=False,
                    )
                if entry is not None:
                    conflicts = shared_default_conflicts(raw_chart, entry)
                    if conflicts:
                        target_path, source_path = conflicts[0]
                        raise ValueError(
                            f"apps chart '{entry_id}' field '{target_path}' is managed by shared default "
                            f"'{source_path}' and must not be set explicitly"
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
                chart_name = entry_id
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
                    }
                )
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
    if timeout:
        spec["timeout"] = timeout
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": release_name, "namespace": namespace},
        "spec": spec,
    }


@dataclass
class _FluxRenderState:
    paths: ProjectPaths
    files: list[Path] = field(default_factory=list)
    resources: list[str] = field(default_factory=lambda: ["./helm-repositories.yaml"])
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
            ),
        )


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

    state = _FluxRenderState(paths=paths)
    _render_flux_app_helm_releases(
        state,
        release_specs=_configured_app_release_specs(
            config,
            component_output_values=component_output_values,
        ),
    )

    repos_path = paths.flux_dir / "helm-repositories.yaml"
    _ensure_parent(repos_path)
    if state.repositories:
        repo_yaml = (
            "\n---\n".join(
                yaml.safe_dump(doc, sort_keys=False).strip() for doc in state.repositories
            )
            + "\n"
        )
    else:
        repo_yaml = "# No Helm repositories are enabled for this project\n"
    repos_path.write_text(repo_yaml, encoding="utf-8")
    state.files.append(repos_path)

    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": state.resources,
    }
    kustomization_path = paths.flux_dir / "kustomization.yaml"
    _write_text(kustomization_path, yaml.safe_dump(kustomization_doc, sort_keys=False))
    state.files.append(kustomization_path)

    return state.files


__all__ = ["render_flux"]
