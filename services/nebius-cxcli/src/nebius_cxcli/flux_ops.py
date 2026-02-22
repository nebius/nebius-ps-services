"""Flux bootstrap/reconcile helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .paths import InstancePaths


def _require_binary(name: str) -> None:
    if shutil.which(name):
        return
    raise RuntimeError(f"{name} is required but was not found in PATH")


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 300) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, timeout=timeout)


def _flux_installed() -> bool:
    result = subprocess.run(
        ["kubectl", "get", "namespace", "flux-system"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.returncode == 0


def _resolve_owner_repo() -> tuple[str, str]:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repository:
        raise RuntimeError(
            "GITHUB_REPOSITORY must be set as '<owner>/<repo>' for flux bootstrap github"
        )
    owner, repo = repository.split("/", maxsplit=1)
    return owner, repo


def ensure_flux(paths: InstancePaths) -> str:
    """Bootstrap Flux once, then reconcile idempotently on subsequent runs."""
    _require_binary("flux")
    _require_binary("kubectl")

    relative_flux_path = paths.flux_dir.relative_to(paths.repo_root).as_posix()

    if _flux_installed():
        _run(["flux", "reconcile", "source", "git", "flux-system"], timeout=300)
        _run(
            ["flux", "reconcile", "kustomization", "flux-system", "--with-source"],
            timeout=300,
        )
        return "reconciled"

    owner, repo = _resolve_owner_repo()
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    bootstrap_cmd = [
        "flux",
        "bootstrap",
        "github",
        "--owner",
        owner,
        "--repository",
        repo,
        "--branch",
        branch,
        "--path",
        relative_flux_path,
        "--token-auth",
    ]
    if os.environ.get("NEBIUS_CXCLI_FLUX_GITHUB_PERSONAL", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        bootstrap_cmd.append("--personal")

    _run(bootstrap_cmd, cwd=paths.repo_root, timeout=1800)
    _run(
        ["flux", "reconcile", "kustomization", "flux-system", "--with-source"],
        timeout=300,
    )
    return "bootstrapped"
