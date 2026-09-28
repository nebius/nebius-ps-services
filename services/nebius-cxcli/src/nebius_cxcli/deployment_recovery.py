"""Portable execution-cache checkpoints for existing lifecycle suboperations.

Only render inputs and named, support-safe journals belong here. Runtime
credentials, kubeconfigs, Terraform state/binaries and arbitrary files do not.
"""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .deployment_state import _read_regular, _safe_relative, canonical_json

_CHECKPOINT: ContextVar[Callable[[], None] | None] = ContextVar(
    "deployment_checkpoint", default=None
)


@contextmanager
def execution_checkpoint(callback: Callable[[], None]) -> Iterator[None]:
    token = _CHECKPOINT.set(callback)
    try:
        yield
    finally:
        _CHECKPOINT.reset(token)


def checkpoint_execution() -> None:
    callback = _CHECKPOINT.get()
    if callback is not None:
        # Prevent recursive publication when a checkpoint implementation uses the
        # same safe file-writing primitives as an embedded lifecycle engine.
        token = _CHECKPOINT.set(None)
        try:
            callback()
        finally:
            _CHECKPOINT.reset(token)


def _project_cache_file(relative: Path) -> bool:
    parts = relative.parts
    if parts == ("config.yaml",):
        return True
    if not parts:
        return False
    if parts[0] == "generated":
        sub = parts[1:]
        if sub == ("nebius-cxcli-manifest.json",):
            return True
        if len(sub) == 2 and sub[0] == "infra":
            return (
                relative.suffix == ".tf"
                or relative.name.endswith(".tf.json")
                or relative.name in {"terraform.auto.tfvars.json", ".terraform.lock.hcl"}
            )
        if len(sub) >= 2 and sub[0] == "flux":
            return relative.suffix in {".yaml", ".yml", ".json"}
        if len(sub) >= 2 and sub[0] == "grafana_dashboards":
            return relative.suffix == ".json"
        if sub in {
            ("reports", "deployment-applications.json"),
            ("reports", "compatibility-admission.json"),
            ("reports", "ordinary-apps-baseline.json"),
        }:
            return True
        if len(sub) == 2 and sub[0] == "reports":
            if re.fullmatch(
                r"soperator-install-(?:render|checks|rest|login|collector|gpu-maintenance|"
                r"runtime|storage|userns|docker|docker-storage|topology|cpu-mask|observability)"
                r"-repair-[A-Za-z0-9_.-]+\.json",
                relative.name,
            ):
                return True
            return relative.suffix == ".json" and relative.name.startswith(
                (
                    "soperator-release-",
                    "soperator-recovery-",
                    "soperator-upgrade-",
                    "soperator-slurm-",
                    "soperator-protected-",
                    "soperator-checks-",
                    "soperator-fast-readiness-",
                    "soperator-campaign-",
                )
            )
    if parts[0] == ".nebius-cxcli":
        sub = parts[1:]
        if sub == ("project-bundle-transaction.json",):
            return True
        if len(sub) >= 3 and sub[0] == "project-bundle-generations":
            return bool(
                re.fullmatch(r"[a-f0-9-]{32,64}", sub[1])
                and _project_cache_file(Path(*sub[2:]))
                and sub[2] != ".nebius-cxcli"
            )
        return len(sub) == 3 and sub[0] == "soperator-upgrades" and sub[-1] == "campaign.json"
    return False


def _cache_file(relative: Path) -> bool:
    # Execution paths always use <tenant>/<project>/... beneath a private root.
    return len(relative.parts) >= 3 and _project_cache_file(Path(*relative.parts[2:]))


def _validate_journal_bytes(relative: Path, data: bytes) -> None:
    if relative.name == "deployment-applications.json":
        import json

        from .deployment_applications import validate_application_journal

        validate_application_journal(json.loads(data))
    elif relative.name == "soperator-campaign-application-generations.json":
        import json

        from .deployment_jail_state import validate_application_generations

        validate_application_generations(json.loads(data))
    elif relative.name == "soperator-campaign-dependencies.json":
        import json

        from .deployment_dependencies import validate_dependency_journal

        validate_dependency_journal(json.loads(data))


def capture_execution_cache(root: Path) -> dict[str, str]:
    from .project_bundle_transaction import ProjectBundleTransaction

    staged_files: set[Path] = set()
    for journal in root.glob("*/*/.nebius-cxcli/project-bundle-transaction.json"):
        if any(parent.is_symlink() for parent in journal.parents if parent.is_relative_to(root)):
            raise RuntimeError("Execution cache has a symlinked parent")
        project_dir = journal.parent.parent
        transaction = ProjectBundleTransaction(project_dir)
        for staged in transaction.recovery_staged_files():
            relative = project_dir.relative_to(root) / staged.relative_to(transaction.project_dir)
            if not _cache_file(relative):
                raise RuntimeError("Pending project generation contains a forbidden recovery file")
            staged_files.add(relative)
    cache: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not _cache_file(relative) or path.is_dir():
            continue
        if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
            raise RuntimeError("Execution cache has a symlinked parent")
        if (
            relative.parts[2:4] == (".nebius-cxcli", "project-bundle-generations")
            and relative not in staged_files
        ):
            continue
        data = _read_regular(path)
        _validate_journal_bytes(relative, data)
        cache[relative.as_posix()] = base64.b64encode(data).decode("ascii")
    if len(canonical_json(cache)) > 64 * 1024 * 1024:
        raise RuntimeError("Execution recovery cache exceeds its size limit")
    return cache


def restore_execution_cache(root: Path, cache: Mapping[str, Any]) -> None:
    """Overlay an authenticated remote cache onto a newly created private directory."""
    if len(canonical_json(cache)) > 64 * 1024 * 1024:
        raise RuntimeError("Execution recovery cache exceeds its size limit")
    decoded: dict[Path, bytes] = {}
    for name, encoded in cache.items():
        relative = _safe_relative(name)
        if not _cache_file(relative) or not isinstance(encoded, str):
            raise RuntimeError("Remote execution cache contains a forbidden file")
        data = base64.b64decode(encoded, validate=True)
        _validate_journal_bytes(relative, data)
        decoded[root / relative] = data
    # Validate the complete archive before publishing any file.
    for path, data in decoded.items():
        if path.exists() and path.stat(follow_symlinks=False).st_nlink != 1:
            raise RuntimeError("Execution cache restore encountered a hard link")
        if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
            raise RuntimeError("Execution cache restore encountered a symlink")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        path.chmod(0o600)


_PREVIEW: ContextVar[bool] = ContextVar("deployment_preview", default=False)


@contextmanager
def deployment_preview(enabled: bool) -> Iterator[None]:
    token = _PREVIEW.set(enabled)
    try:
        yield
    finally:
        _PREVIEW.reset(token)


def is_deployment_preview() -> bool:
    return _PREVIEW.get()
