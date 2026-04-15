"""Helm chart metadata/values helpers with automatic source detection."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

HELM_TIMEOUT_ENV = "NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS"
DEFAULT_HELM_COMMAND_TIMEOUT_SECONDS = 90
DEFAULT_HELM_PULL_TIMEOUT_SECONDS = 120
DEFAULT_GIT_CLONE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class HelmChartReference:
    chart_name: str
    chart_repo: str
    chart_version: str


def _is_oci_ref(token: str) -> bool:
    return token.strip().lower().startswith("oci://")


def _is_http_repo(token: str) -> bool:
    normalized = token.strip().lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


def _github_tree_ref(repo: str) -> tuple[str, str, str] | None:
    # Example: https://github.com/org/repo/tree/main/charts/n8n
    token = repo.strip().rstrip("/")
    marker = "github.com/"
    if marker not in token or "/tree/" not in token:
        return None
    try:
        tail = token.split(marker, maxsplit=1)[1]
        owner_repo, tree_tail = tail.split("/tree/", maxsplit=1)
        owner, repo_name = owner_repo.split("/", maxsplit=1)
        ref, chart_path = tree_tail.split("/", maxsplit=1)
    except ValueError:
        return None
    if not owner or not repo_name or not ref or not chart_path:
        return None
    git_url = f"https://github.com/{owner}/{repo_name}.git"
    return git_url, ref, chart_path


def _repo_has_index(repo: str) -> bool:
    candidate = repo.strip().rstrip("/")
    if not candidate or not _is_http_repo(candidate):
        return False
    index_url = f"{candidate}/index.yaml"
    try:
        with urllib.request.urlopen(index_url, timeout=8) as response:
            payload = yaml.safe_load(response.read().decode("utf-8")) or {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("entries"), dict)


def _helm_timeout_seconds(*, default: int) -> int:
    raw = os.environ.get(HELM_TIMEOUT_ENV, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{HELM_TIMEOUT_ENV} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{HELM_TIMEOUT_ENV} must be a positive integer")
    return value


def _run_helm_show(subcommand: str, ref: str, *, repo: str = "", version: str = "") -> str:
    command = ["helm", "show", subcommand, ref]
    if repo:
        command.extend(["--repo", repo])
    if version:
        command.extend(["--version", version])
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_HELM_COMMAND_TIMEOUT_SECONDS)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"helm show {subcommand} timed out after {timeout_seconds} seconds for '{ref}'. "
            f"Set {HELM_TIMEOUT_ENV} to a larger value for slow chart registries."
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(stderr)
    return result.stdout


def _run_git_clone(git_url: str, ref: str) -> Path:
    tmp_root = Path(tempfile.mkdtemp(prefix="nebius-cxcli-helm-git-"))
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_GIT_CLONE_TIMEOUT_SECONDS)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, git_url, str(tmp_root / "repo")],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(
            f"git clone timed out after {timeout_seconds} seconds for '{git_url}' ref '{ref}'. "
            f"Set {HELM_TIMEOUT_ENV} to a larger value for slow chart sources."
        ) from exc
    if result.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git clone error"
        raise RuntimeError(stderr)
    return tmp_root / "repo"


def _run_helm_pull(ref: str, *, repo: str = "", version: str = "") -> Path:
    tmp_root = Path(tempfile.mkdtemp(prefix="nebius-cxcli-helm-pull-"))
    command = ["helm", "pull", ref, "--untar", "--untardir", str(tmp_root)]
    if repo:
        command.extend(["--repo", repo])
    if version:
        command.extend(["--version", version])
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_HELM_PULL_TIMEOUT_SECONDS)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(
            f"helm pull timed out after {timeout_seconds} seconds for '{ref}'. "
            f"Set {HELM_TIMEOUT_ENV} to a larger value for slow chart registries."
        ) from exc
    if result.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(stderr)
    directories = [path for path in tmp_root.iterdir() if path.is_dir()]
    if len(directories) != 1:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"helm pull did not materialize exactly one chart directory for '{ref}'")
    return directories[0]


def _run_helm_template(
    *,
    release_name: str,
    chart_path: Path,
    namespace: str,
    values_file: Path | None,
) -> str:
    command = ["helm", "template", release_name, str(chart_path)]
    if namespace:
        command.extend(["--namespace", namespace])
    if values_file is not None:
        command.extend(["-f", str(values_file)])
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_HELM_COMMAND_TIMEOUT_SECONDS)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"helm template timed out after {timeout_seconds} seconds for '{chart_path}'. "
            f"Set {HELM_TIMEOUT_ENV} to a larger value for slow chart sources."
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(stderr)
    return result.stdout


def _resolve_show_ref(reference: HelmChartReference) -> tuple[str, str, str, Path | None]:
    chart_name = reference.chart_name.strip()
    chart_repo = reference.chart_repo.strip().rstrip("/")
    chart_version = reference.chart_version.strip()

    if _is_oci_ref(chart_repo) or _is_oci_ref(chart_name):
        oci_repo = chart_repo
        oci_name = chart_name
        if _is_oci_ref(oci_name):
            return oci_name.rstrip("/"), "", chart_version, None
        if not oci_repo:
            raise RuntimeError("OCI chart reference is incomplete: chart repo is required")
        if not oci_name:
            return oci_repo, "", chart_version, None
        repo_tail = oci_repo.rsplit("/", maxsplit=1)[-1].strip().lower()
        if repo_tail == oci_name.lower():
            return oci_repo, "", chart_version, None
        return f"{oci_repo}/{oci_name}", "", chart_version, None

    github_tree = _github_tree_ref(chart_repo)
    if github_tree is not None:
        git_url, git_ref, chart_path = github_tree
        checkout = _run_git_clone(git_url, git_ref)
        local_path = (checkout / chart_path).resolve()
        if not local_path.exists() or not local_path.is_dir():
            shutil.rmtree(checkout.parent, ignore_errors=True)
            raise RuntimeError(f"Chart path not found in git source: {chart_path}")
        return str(local_path), "", "", checkout.parent

    github_tree = _github_tree_ref(chart_name)
    if github_tree is not None:
        git_url, git_ref, chart_path = github_tree
        checkout = _run_git_clone(git_url, git_ref)
        local_path = (checkout / chart_path).resolve()
        if not local_path.exists() or not local_path.is_dir():
            shutil.rmtree(checkout.parent, ignore_errors=True)
            raise RuntimeError(f"Chart path not found in git source: {chart_path}")
        return str(local_path), "", "", checkout.parent

    if chart_repo and _is_http_repo(chart_repo):
        if _repo_has_index(chart_repo):
            return chart_name, chart_repo, chart_version, None
        raise RuntimeError(
            f"HTTP chart repository '{chart_repo}' is missing '/index.yaml'. "
            "Use a Helm repo base URL that serves index.yaml, or switch to an OCI reference (oci://...)."
        )

    if chart_repo and chart_name:
        return f"{chart_repo}/{chart_name}", "", chart_version, None
    if chart_name:
        return chart_name, "", chart_version, None
    raise RuntimeError("Chart reference is incomplete: chart name is required")


@contextlib.contextmanager
def _materialize_chart_dir(reference: HelmChartReference):
    cleanup_roots: list[Path] = []
    show_ref, repo, version, cleanup_dir = _resolve_show_ref(reference)
    if cleanup_dir is not None:
        cleanup_roots.append(cleanup_dir)

    local_candidate = Path(show_ref)
    if not repo and not version and local_candidate.exists() and local_candidate.is_dir():
        try:
            yield local_candidate
        finally:
            for root in reversed(cleanup_roots):
                shutil.rmtree(root, ignore_errors=True)
        return

    chart_dir = _run_helm_pull(show_ref, repo=repo, version=version)
    cleanup_roots.append(chart_dir.parent)
    try:
        yield chart_dir
    finally:
        for root in reversed(cleanup_roots):
            shutil.rmtree(root, ignore_errors=True)


def render_chart_template_documents(
    *,
    chart_name: str,
    chart_repo: str,
    chart_version: str,
    release_name: str,
    namespace: str,
    values: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    reference = HelmChartReference(
        chart_name=chart_name,
        chart_repo=chart_repo,
        chart_version=chart_version,
    )
    temp_values_dir: Path | None = None
    values_file: Path | None = None
    try:
        if values:
            temp_values_dir = Path(tempfile.mkdtemp(prefix="nebius-cxcli-helm-values-"))
            values_file = temp_values_dir / "values.yaml"
            values_file.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        with _materialize_chart_dir(reference) as chart_dir:
            rendered = _run_helm_template(
                release_name=release_name,
                chart_path=chart_dir,
                namespace=namespace,
                values_file=values_file,
            )
        documents: list[dict[str, Any]] = []
        for item in yaml.safe_load_all(rendered):
            if item is None:
                continue
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"helm template rendered a non-object document for release '{release_name}'"
                )
            documents.append(item)
        return documents
    finally:
        if temp_values_dir is not None:
            shutil.rmtree(temp_values_dir, ignore_errors=True)


@lru_cache(maxsize=64)
def chart_cli_contract_findings(
    *,
    chart_name: str,
    chart_repo: str,
    chart_version: str,
    expected_chart_name: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    issues: list[str] = []
    warnings: list[str] = []
    reference = HelmChartReference(
        chart_name=chart_name,
        chart_repo=chart_repo,
        chart_version=chart_version,
    )
    try:
        with _materialize_chart_dir(reference) as chart_dir:
            chart_yaml = chart_dir / "Chart.yaml"
            values_yaml = chart_dir / "values.yaml"
            templates_dir = chart_dir / "templates"
            readme_file = chart_dir / "README.md"

            if not chart_yaml.exists():
                issues.append(f"materialized chart is missing Chart.yaml in {chart_dir}")
            if not values_yaml.exists():
                issues.append(f"materialized chart is missing values.yaml in {chart_dir}")
            if not templates_dir.exists():
                issues.append(f"materialized chart is missing templates/ in {chart_dir}")
            elif not templates_dir.is_dir():
                issues.append(
                    f"materialized chart templates exists but is not a directory in {chart_dir}"
                )
            else:
                template_files = [
                    path
                    for path in templates_dir.rglob("*")
                    if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".tpl", ".txt"}
                ]
                if not template_files:
                    issues.append(
                        f"materialized chart templates/ has no renderable template files in {chart_dir}"
                    )

            if not readme_file.exists():
                warnings.append(f"materialized chart is missing README.md in {chart_dir}")

            if chart_yaml.exists():
                payload = yaml.safe_load(chart_yaml.read_text(encoding="utf-8")) or {}
                if not isinstance(payload, dict):
                    issues.append(f"materialized chart Chart.yaml is not a mapping in {chart_dir}")
                else:
                    api_version = str(payload.get("apiVersion", "")).strip()
                    resolved_name = str(payload.get("name", "")).strip()
                    resolved_version = str(payload.get("version", "")).strip()
                    if not api_version:
                        issues.append(
                            f"materialized chart Chart.yaml is missing apiVersion in {chart_dir}"
                        )
                    elif api_version != "v2":
                        warnings.append(
                            f"materialized chart Chart.yaml uses apiVersion '{api_version}' instead of canonical Helm v2 format in {chart_dir}"
                        )
                    if not resolved_name:
                        issues.append(
                            f"materialized chart Chart.yaml is missing name in {chart_dir}"
                        )
                    elif resolved_name != (expected_chart_name or chart_name):
                        issues.append(
                            "materialized chart name "
                            f"'{resolved_name}' does not match configured chart name "
                            f"'{expected_chart_name or chart_name}'"
                        )
                    if not resolved_version:
                        issues.append(
                            f"materialized chart Chart.yaml is missing version in {chart_dir}"
                        )
    except Exception as exc:
        return (f"could not materialize chart source via helm: {exc}",), ()

    return tuple(issues), tuple(warnings)


class HelmClient:
    """Python Helm client wrapper used by runtime validation/introspection."""

    def __init__(self) -> None:
        if not shutil.which("helm"):
            raise RuntimeError("helm not found in PATH")

    def show_chart(self, *, reference: HelmChartReference) -> dict[str, Any]:
        cleanup_dir: Path | None = None
        try:
            show_ref, repo, version, cleanup_dir = _resolve_show_ref(reference)
            output = _run_helm_show("chart", show_ref, repo=repo, version=version)
            payload = yaml.safe_load(output) or {}
            if not isinstance(payload, dict):
                raise RuntimeError("chart metadata is not a mapping")
            return payload
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    def show_values(self, *, reference: HelmChartReference) -> dict[str, Any]:
        cleanup_dir: Path | None = None
        try:
            show_ref, repo, version, cleanup_dir = _resolve_show_ref(reference)
            output = _run_helm_show("values", show_ref, repo=repo, version=version)
            payload = yaml.safe_load(output) or {}
            if not isinstance(payload, dict):
                return {}
            return payload
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    def search_repo(self, *, chart_name: str, chart_repo: str) -> list[dict[str, Any]]:
        repo = chart_repo.strip().rstrip("/")
        if not repo or _is_oci_ref(repo) or not _repo_has_index(repo):
            return []

        alias = f"cxcli-{abs(hash(repo))}"
        add_result = subprocess.run(
            ["helm", "repo", "add", alias, repo],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if add_result.returncode != 0 and "already exists" not in (add_result.stderr or ""):
            return []

        search = subprocess.run(
            ["helm", "search", "repo", f"{alias}/{chart_name}", "--versions", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if search.returncode != 0:
            return []
        try:
            payload = json.loads(search.stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
