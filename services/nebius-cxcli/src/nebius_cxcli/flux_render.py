"""Flux rendering orchestration (generic Helm release model)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import InstancePaths
from .runtime_config import to_plain_data


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _helm_repository_doc(name: str, url: str) -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {"interval": "30m", "url": url},
    }


def _git_repository_doc(name: str, url: str, ref: str) -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "GitRepository",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {"interval": "5m", "url": url, "ref": {"branch": ref}},
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


def _resolve_flux_chart_source(*, chart_name: str, chart_repo: str) -> dict[str, str]:
    if chart_repo:
        github_tree = _github_tree_ref(chart_repo)
        if github_tree is None:
            return {
                "kind": "helm_repository",
                "chart_ref": chart_name,
                "repo_url": chart_repo,
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


def _configured_app_release_specs(config: Any) -> list[dict[str, Any]]:
    payload = to_plain_data(config)
    if not isinstance(payload, dict):
        return []

    specs: list[dict[str, Any]] = []
    apps_node = payload.get("apps")
    if isinstance(apps_node, dict):
        dynamic_releases = apps_node.get("releases")
        if isinstance(dynamic_releases, list):
            for raw_release in dynamic_releases:
                if not isinstance(raw_release, dict):
                    continue
                entry_id = str(raw_release.get("id", "")).strip().lower()
                if not entry_id or not bool(raw_release.get("enabled", False)):
                    continue

                values_node = raw_release.get("values", {})
                if values_node is None:
                    values_node = {}
                if not isinstance(values_node, dict):
                    raise ValueError(
                        f"apps.releases[{entry_id}].values must be a mapping for enabled release '{entry_id}'"
                    )

                chart_node = values_node.get("chart")
                if not isinstance(chart_node, dict):
                    raise ValueError(
                        f"apps.releases[{entry_id}].values.chart must be a mapping for enabled release '{entry_id}'"
                    )
                chart_repo = str(chart_node.get("repo", "")).strip()
                chart_name = str(chart_node.get("name", "")).strip()
                chart_version = str(chart_node.get("version", "")).strip()
                if not chart_name:
                    raise ValueError(
                        f"apps.releases[{entry_id}].values.chart.name is required for enabled release '{entry_id}'"
                    )
                source = _resolve_flux_chart_source(chart_name=chart_name, chart_repo=chart_repo)
                source_kind = source["kind"]
                chart_ref = source["chart_ref"]
                source_url = source["repo_url"]
                source_ref = source.get("repo_ref", "")
                if source_kind == "helm_repository" and not chart_version:
                    raise ValueError(
                        f"apps.releases[{entry_id}].values.chart.version is required for Helm repository release '{entry_id}'"
                    )

                namespace = str(values_node.get("namespace", "")).strip()
                if not namespace:
                    raise ValueError(
                        f"apps.releases[{entry_id}].values.namespace is required for enabled release '{entry_id}'"
                    )
                release_name = str(values_node.get("release_name") or entry_id).strip() or entry_id
                repo_name_default = f"helm-{_file_slug(entry_id)}"
                repo_name = str(values_node.get("repo_name") or repo_name_default).strip()
                interval = str(values_node.get("interval") or "5m").strip() or "5m"
                scope = str(raw_release.get("section", "")).strip().lower() or "workloads"
                chart_values = values_node.get("values", {})
                if chart_values is None:
                    chart_values = {}
                if not isinstance(chart_values, dict):
                    raise ValueError(
                        f"apps.releases[{entry_id}].values.values must be a mapping for enabled release '{entry_id}'"
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
                        "chart_ref": chart_ref,
                        "chart_version": chart_version if source_kind == "helm_repository" else "",
                        "interval": interval,
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
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": release_name, "namespace": namespace},
        "spec": {
            "interval": interval,
            "chart": {"spec": chart_spec},
            "values": values,
        },
    }


@dataclass
class _FluxRenderState:
    paths: InstancePaths
    files: list[Path] = field(default_factory=list)
    resources: list[str] = field(default_factory=lambda: ["./sources/helm-repositories.yaml"])
    repositories: list[dict[str, Any]] = field(default_factory=list)
    seen_repo_names: set[str] = field(default_factory=set)

    def add_repository_doc(self, *, repo_name: str, doc: dict[str, Any]) -> None:
        if repo_name in self.seen_repo_names:
            return
        self.repositories.append(doc)
        self.seen_repo_names.add(repo_name)

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
        if source_kind == "helm_repository":
            state.add_repository_doc(
                repo_name=source_name,
                doc=_helm_repository_doc(source_name, source_url),
            )
            flux_source_kind = "HelmRepository"
        else:
            state.add_repository_doc(
                repo_name=source_name,
                doc=_git_repository_doc(source_name, source_url, source_ref or "main"),
            )
            flux_source_kind = "GitRepository"
        state.write_doc(
            Path("apps") / scope / f"{release_name}-helmrelease.yaml",
            _helm_release_doc(
                release_name=release_name,
                namespace=namespace,
                source_name=source_name,
                source_kind=flux_source_kind,
                chart_ref=release["chart_ref"],
                chart_version=release["chart_version"],
                interval=release["interval"],
                values=release["values"],
            ),
        )


def render_flux(config: Any, paths: InstancePaths) -> list[Path]:
    state = _FluxRenderState(paths=paths)
    _render_flux_app_helm_releases(state, release_specs=_configured_app_release_specs(config))

    repos_path = paths.flux_dir / "sources/helm-repositories.yaml"
    _ensure_parent(repos_path)
    if state.repositories:
        repo_yaml = (
            "\n---\n".join(yaml.safe_dump(doc, sort_keys=False).strip() for doc in state.repositories)
            + "\n"
        )
    else:
        repo_yaml = "# No Helm repositories are enabled for this instance\n"
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
