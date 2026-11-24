"""Explicit Git and standalone filesystem identities for spec publication."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from .contracts import (
    CONFIG_RELATIVE_PATH,
    ProjectSpecError,
    _read_file,
    _run_git,
    canonical_digest,
    digest,
    git_environment,
    load_project_config,
    project_identity,
    stable_json,
)


@dataclass(frozen=True)
class PublicationTarget:
    kind: str
    project: Path
    storage_root: Path
    scope: str
    head: str | None
    basis: str

    def check(self):
        if resolve_target(self.project) != self:
            raise ProjectSpecError(
                "CONCURRENT_MODIFICATION", "publication target or policy changed"
            )


def resolve_target(project_root: Path) -> PublicationTarget:
    project = Path(os.path.abspath(project_root.expanduser()))
    if project != project.resolve() or not project.is_dir():
        raise ProjectSpecError(
            "PROJECT_SCOPE_REQUIRED", "target must be a real directory without symlinks"
        )
    metadata = project.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise ProjectSpecError(
            "UNSAFE_SPEC", "target directory ownership or mode is unsafe"
        )
    environment = git_environment(project)
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=20,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectSpecError(
            "ENVIRONMENT_BLOCKER", "Git classification failed"
        ) from error
    if result.returncode == 0:
        project, storage_root, scope, head = project_identity(project)
        kind = "git"
        common = _run_git(
            project, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
    else:
        markers = any(
            (parent / ".git").exists()
            or (parent / ".git").is_symlink()
            or ((parent / "HEAD").exists() and (parent / "objects").exists())
            for parent in (project, *project.parents)
        )
        if (
            result.returncode != 128
            or "not a git repository" not in result.stderr
            or markers
        ):
            raise ProjectSpecError(
                "PROJECT_SCOPE_REQUIRED",
                "Git target classification is ambiguous or invalid",
            )
        kind, storage_root, scope, head, common = "filesystem", project, ".", None, None
    for name in (".codex", "docs"):
        directory = project / name
        if directory.is_symlink() or directory.exists() and not directory.is_dir():
            raise ProjectSpecError(
                "UNSAFE_SPEC", "target policy/docs directory must be real"
            )
    policy = load_project_config(project, require_git=kind == "git")
    path = project / CONFIG_RELATIVE_PATH
    policy_hash = (
        digest(_read_file(path, "project spec policy", max_bytes=4096)[0])
        if path.exists()
        else digest(stable_json(policy))
    )
    basis = canonical_digest(
        {
            "kind": kind,
            "root": str(project),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "policy": policy_hash,
            "head": head,
            "common": common,
        }
    )
    return PublicationTarget(kind, project, storage_root, scope, head, basis)


def inspect_filesystem(target: PublicationTarget):
    """Filesystem receipts never have Git/workflow receipt identity."""
    from .contracts import inspect_pair_bytes
    from .transaction import spec_pair_lock

    with spec_pair_lock(target.project, target.storage_root, shared=True):
        target.check()
        result = {
            "schema": "maintain-project-specs.filesystem-inspection.v1",
            "target_kind": target.kind,
            "filesystem_basis": target.basis,
            "project_root": str(target.project),
        }
        if load_project_config(target.project, require_git=False)["mode"] == "disabled":
            return {**result, "status": "disabled"}
        raw = {}
        for kind in ("requirements", "design"):
            path = target.project / "docs" / f"{kind}.md"
            raw[kind] = (
                _read_file(path, kind)[0]
                if path.exists() or path.is_symlink()
                else None
            )
            result[kind] = {
                "path": f"docs/{kind}.md",
                "sha256": digest(raw[kind]) if raw[kind] is not None else "absent",
            }
        if any(value is None for value in raw.values()):
            result.update(status="pending", pending=["missing canonical document"])
        else:
            pair = inspect_pair_bytes(raw["requirements"], raw["design"])
            result.update(status=pair["status"], pending=pair["pending"])
        target.check()
        return result
