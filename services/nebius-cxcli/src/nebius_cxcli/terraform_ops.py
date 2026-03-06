"""Terraform command wrappers used by the CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _require_terraform() -> None:
    if shutil.which("terraform"):
        return
    raise RuntimeError("terraform is required but was not found in PATH")


def _run(
    cmd: list[str], *, cwd: Path, timeout: int, extra_env: dict[str, str] | None = None
) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    subprocess.run(cmd, cwd=cwd, check=True, timeout=timeout, env=env)


def terraform_init(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
    """Run terraform init in the rendered infra directory."""
    _require_terraform()
    if not infra_dir.exists():
        raise RuntimeError(f"Rendered infra directory does not exist: {infra_dir}")
    _run(["terraform", "init", "-input=false"], cwd=infra_dir, timeout=300, extra_env=extra_env)


def terraform_plan(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
    """Run terraform init + plan in the rendered infra directory."""
    terraform_init(infra_dir, extra_env=extra_env)
    _run(
        ["terraform", "plan", "-input=false", "-lock-timeout=5m"],
        cwd=infra_dir,
        timeout=1800,
        extra_env=extra_env,
    )


def terraform_apply(infra_dir: Path, *, extra_env: dict[str, str] | None = None) -> None:
    """Run terraform init + apply in the rendered infra directory."""
    terraform_init(infra_dir, extra_env=extra_env)
    _run(
        ["terraform", "apply", "-input=false", "-auto-approve", "-lock-timeout=5m"],
        cwd=infra_dir,
        timeout=7200,
        extra_env=extra_env,
    )
