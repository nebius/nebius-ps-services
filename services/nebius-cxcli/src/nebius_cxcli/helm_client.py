"""Helm chart metadata/values helpers with automatic source detection."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class HelmChartReference:
    chart_name: str
    chart_repo: str
    chart_version: str


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
    if not candidate:
        return False
    index_url = f"{candidate}/index.yaml"
    try:
        with urllib.request.urlopen(index_url, timeout=8) as response:
            payload = yaml.safe_load(response.read().decode("utf-8")) or {}
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("entries"), dict)


def _run_helm_show(subcommand: str, ref: str, *, repo: str = "", version: str = "") -> str:
    command = ["helm", "show", subcommand, ref]
    if repo:
        command.extend(["--repo", repo])
    if version:
        command.extend(["--version", version])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(stderr)
    return result.stdout


def _run_git_clone(git_url: str, ref: str) -> Path:
    tmp_root = Path(tempfile.mkdtemp(prefix="nebius-cxcli-helm-git-"))
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, git_url, str(tmp_root / "repo")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git clone error"
        raise RuntimeError(stderr)
    return tmp_root / "repo"


def _resolve_show_ref(reference: HelmChartReference) -> tuple[str, str, str, Path | None]:
    chart_name = reference.chart_name.strip()
    chart_repo = reference.chart_repo.strip().rstrip("/")
    chart_version = reference.chart_version.strip()

    if chart_repo and _repo_has_index(chart_repo):
        return chart_name, chart_repo, chart_version, None

    github_tree = _github_tree_ref(chart_repo)
    if github_tree is not None:
        git_url, git_ref, chart_path = github_tree
        checkout = _run_git_clone(git_url, git_ref)
        local_path = (checkout / chart_path).resolve()
        if not local_path.exists() or not local_path.is_dir():
            shutil.rmtree(checkout.parent, ignore_errors=True)
            raise RuntimeError(f"Chart path not found in git source: {chart_path}")
        return str(local_path), "", "", checkout.parent

    if chart_repo and chart_name:
        return f"{chart_repo}/{chart_name}", "", chart_version, None
    if chart_name:
        return chart_name, "", chart_version, None
    raise RuntimeError("Chart reference is incomplete: chart name is required")


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
        if not repo or not _repo_has_index(repo):
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
