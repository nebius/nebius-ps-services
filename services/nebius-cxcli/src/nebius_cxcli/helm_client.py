"""Helm chart metadata/values helpers with automatic source detection."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import kubernetes_process
from .soperator_upgrade_progress import sanitized_bounded_command_output

HELM_TIMEOUT_ENV = "NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS"
DEFAULT_HELM_COMMAND_TIMEOUT_SECONDS = 90
DEFAULT_HELM_PULL_TIMEOUT_SECONDS = 120
DEFAULT_GIT_CLONE_TIMEOUT_SECONDS = 120
_HELM_FETCH_ATTEMPTS = 3


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


def _transient_helm_fetch_reason(detail: str) -> str | None:
    # Classify sanitized diagnostics, never opaque signed URL/query content.
    normalized = detail.lower()
    if any(
        marker in normalized
        for marker in (
            "unauthorized",
            "forbidden",
            "authentication",
            "certificate",
            "x509",
            "not found",
            "digest",
            "invalid",
            "mismatch",
        )
    ):
        return None
    if re.search(
        r"\b(?:http(?:/[0-9.]+)?|status(?: code)?)\b[^\n]*?\b(?:401|403|404)\b",
        normalized,
    ):
        return None
    if "connection reset" in normalized:
        return "connection reset"
    if any(
        marker in normalized
        for marker in (
            "i/o timeout",
            "operation timed out",
            "timeout awaiting response",
            "tls handshake timeout",
        )
    ):
        return "network timeout"
    return None


def _run_helm_show(subcommand: str, ref: str, *, repo: str = "", version: str = "") -> str:
    command = ["helm", "show", subcommand, ref]
    if repo:
        command.extend(["--repo", repo])
    if version:
        command.extend(["--version", version])
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_HELM_COMMAND_TIMEOUT_SECONDS)
    reason: str | None
    for attempt in range(1, _HELM_FETCH_ATTEMPTS + 1):
        try:
            result = kubernetes_process.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            # Raise outside the handler so callers cannot expose raw timeout output
            # through either an explicit cause or an implicit exception context.
            reason = "network timeout"
        else:
            if result.returncode == 0:
                return result.stdout
            detail = sanitized_bounded_command_output(
                result.stderr or result.stdout or "unknown error"
            )
            reason = _transient_helm_fetch_reason(detail)
            if reason is None:
                raise RuntimeError(detail)
        if attempt == _HELM_FETCH_ATTEMPTS:
            raise RuntimeError(
                f"helm show {subcommand} failed after {attempt} attempts: {reason} "
                f"(per-attempt timeout: {timeout_seconds} seconds)"
            )
        time.sleep(2 ** (attempt - 1) + random.uniform(0.0, 0.25))
    raise AssertionError("unreachable Helm show retry state")


def _run_git_clone(git_url: str, ref: str) -> Path:
    git_binary = shutil.which("git")
    if not git_binary:
        raise RuntimeError(
            "git is required for Git tree Helm chart sources but was not found in PATH"
        )
    tmp_root = Path(tempfile.mkdtemp(prefix="nebius-cxcli-helm-git-"))
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_GIT_CLONE_TIMEOUT_SECONDS)
    try:
        result = kubernetes_process.run(
            [
                git_binary,
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                git_url,
                str(tmp_root / "repo"),
            ],
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
    timeout_seconds = _helm_timeout_seconds(default=DEFAULT_HELM_PULL_TIMEOUT_SECONDS)
    reason: str | None
    for attempt in range(1, _HELM_FETCH_ATTEMPTS + 1):
        tmp_root = Path(tempfile.mkdtemp(prefix="nebius-cxcli-helm-pull-"))
        keep_root = False
        try:
            command = ["helm", "pull", ref, "--untar", "--untardir", str(tmp_root)]
            if repo:
                command.extend(["--repo", repo])
            if version:
                command.extend(["--version", version])
            try:
                result = kubernetes_process.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                reason = "network timeout"
            else:
                if result.returncode == 0:
                    directories = [path for path in tmp_root.iterdir() if path.is_dir()]
                    if len(directories) != 1:
                        raise RuntimeError(
                            "helm pull did not materialize exactly one chart directory"
                        )
                    keep_root = True
                    return directories[0]
                detail = sanitized_bounded_command_output(
                    result.stderr or result.stdout or "unknown error"
                )
                reason = _transient_helm_fetch_reason(detail)
                if reason is None:
                    raise RuntimeError(detail)
        finally:
            # Partial extraction must never become the next attempt's input.
            # The successful directory is owned by _materialize_chart_dir.
            if not keep_root:
                shutil.rmtree(tmp_root, ignore_errors=True)
        if attempt == _HELM_FETCH_ATTEMPTS:
            raise RuntimeError(
                f"helm pull failed after {attempt} attempts: {reason} "
                f"(per-attempt timeout: {timeout_seconds} seconds)"
            )
        time.sleep(2 ** (attempt - 1) + random.uniform(0.0, 0.25))
    raise AssertionError("unreachable Helm pull retry state")


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
        result = kubernetes_process.run(
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
    from .compatibility_artifacts import frozen_chart, materialize_frozen_chart

    frozen = frozen_chart(reference)
    if frozen is not None:
        with materialize_frozen_chart(frozen) as directory:
            yield directory
        return
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


def chart_cli_contract_findings(
    *,
    chart_name: str,
    chart_repo: str,
    chart_version: str,
    expected_chart_name: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # Cache completed inspections, never exceptions caused by an unavailable source.
    try:
        return _chart_cli_contract_findings_cached(
            chart_name=chart_name,
            chart_repo=chart_repo,
            chart_version=chart_version,
            expected_chart_name=expected_chart_name,
        )
    except Exception as exc:
        detail = sanitized_bounded_command_output(str(exc))
        return (f"could not materialize chart source via helm: {detail}",), ()


@lru_cache(maxsize=64)
def _chart_cli_contract_findings_cached(
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

        if not chart_repo and not readme_file.exists():
            warnings.append(f"local chart is missing README.md in {chart_dir}")

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
                    issues.append(f"materialized chart Chart.yaml is missing name in {chart_dir}")
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

    return tuple(issues), tuple(warnings)


class HelmClient:
    """Python Helm client wrapper used by runtime validation/introspection."""

    def __init__(self) -> None:
        if not shutil.which("helm"):
            raise RuntimeError("helm not found in PATH")

    def show_chart(self, *, reference: HelmChartReference) -> dict[str, Any]:
        from .compatibility_artifacts import frozen_chart, materialize_frozen_chart

        frozen = frozen_chart(reference)
        if frozen is not None:
            with materialize_frozen_chart(frozen) as chart:
                payload = yaml.safe_load((chart / "Chart.yaml").read_text())
                if not isinstance(payload, dict):
                    raise ValueError("Frozen chart metadata must be a mapping")
                return payload
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
        from .compatibility_artifacts import frozen_chart, materialize_frozen_chart

        frozen = frozen_chart(reference)
        if frozen is not None:
            with materialize_frozen_chart(frozen) as chart:
                path = chart / "values.yaml"
                payload = yaml.safe_load(path.read_text()) if path.exists() else {}
                if not isinstance(payload, dict):
                    raise ValueError("Frozen chart values must be a mapping")
                return payload
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

        alias = f"cxcli-{hashlib.sha1(repo.encode('utf-8')).hexdigest()[:12]}"
        add_result = kubernetes_process.run(
            ["helm", "repo", "add", alias, repo],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if add_result.returncode != 0 and "already exists" not in (add_result.stderr or ""):
            return []

        search = kubernetes_process.run(
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
