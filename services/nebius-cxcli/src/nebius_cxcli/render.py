"""Render orchestration for Terraform and Flux artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .component_sources import SourceProfile
from .flux_render import render_flux
from .infra_render import render_terraform_artifacts
from .mk8s_gpu import materialize_mk8s_gpu_app_values, prune_inactive_mk8s_gpu_app_rows
from .mysterybox_eso import materialize_mysterybox_eso_app_values
from .observability import (
    materialize_observability_app_values,
    materialize_observability_infra_values,
)
from .paths import ProjectPaths
from .project_bundle_transaction import normalize_project_bundle_target
from .soperator_child_charts import materialize_soperator_child_chart_values
from .soperator_config_materialization import (
    _materialize_soperator_component_defaults,
    _materialize_soperator_render_only_values,
)


@dataclass(frozen=True)
class RenderResult:
    files_written: list[Path]


@dataclass(frozen=True)
class ProjectGenerationPlan:
    """Complete render-owned postimage for one project transaction."""

    writes: dict[Path, bytes]
    removals: tuple[Path, ...]
    sha256: str
    expected_preimages: dict[Path, str]
    preimage_sha256: str


_LIFECYCLE_REPORT_FILENAMES = frozenset(
    {
        "deploy-report.md",
        "soperator-install-plan.json",
        "soperator-release-reconcile.json",
        "soperator-upgrade-report.md",
        "soperator-upgrade-report.json",
        "migrate-node-group-report.md",
        "migrate-node-group-report.json",
        "upgrade-node-template-report.md",
        "upgrade-node-template-report.json",
    }
)
_LIFECYCLE_REPORT_GLOBS = (
    "acceptance-benchmark-report*.json",
    "acceptance-smoke-report*.json",
    "cluster-inventory-report*.json",
    "deploy-smoke-report*.json",
    "deploy-gpu-stack-readiness-report*.json",
    "deploy-gpu-visibility-report*.json",
    "soperator-destroy-*.json",
    "soperator-protected-data-plane-*.json",
    "soperator-protected-source-ownership-*.json",
    "soperator-recovery-*.json",
    "soperator-release-intent-*.json",
    "soperator-release-reconcile-*.json",
    "soperator-release-snapshot-*.json",
    "soperator-observability-*.json",
    "soperator-slurm-actions-*.json",
    "soperator-upgrade-admission-*.json",
)
_LIFECYCLE_REPORT_DIRNAMES = frozenset({".locks", "soperator-clusters", "soperator-discovery"})
_REPORT_JSON_REF_RE = re.compile(r"`([^`/\\]+\.json)`")


def reset_generated_bundle(paths: ProjectPaths) -> None:
    """Remove the previously generated bundle completely."""
    if paths.generated_dir.exists():
        shutil.rmtree(paths.generated_dir)


def staged_generated_paths(paths: ProjectPaths) -> ProjectPaths:
    """Create a sibling staging directory for a transactional generated/ replacement."""
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=".generated-staging-",
            dir=paths.project_dir,
        )
    ).resolve()
    return replace(
        paths,
        generated_dir=staging_dir,
        infra_dir=staging_dir / "infra",
        flux_dir=staging_dir / "flux",
        reports_dir=staging_dir / "reports",
    )


def _lifecycle_report_artifact_names(reports_dir: Path) -> tuple[str, ...]:
    if not reports_dir.is_dir():
        return ()
    names = {name for name in _LIFECYCLE_REPORT_FILENAMES if (reports_dir / name).is_file()}
    for pattern in _LIFECYCLE_REPORT_GLOBS:
        names.update(path.name for path in reports_dir.glob(pattern) if path.is_file())
    for report_name in tuple(names):
        if not report_name.endswith(".md"):
            continue
        with suppress(OSError, UnicodeDecodeError):
            report_text = (reports_dir / report_name).read_text(encoding="utf-8")
            for match in _REPORT_JSON_REF_RE.finditer(report_text):
                names.add(match.group(1))
    return tuple(sorted(names))


def _lifecycle_report_artifact_dirnames(reports_dir: Path) -> tuple[str, ...]:
    if not reports_dir.is_dir():
        return ()
    return tuple(
        sorted(name for name in _LIFECYCLE_REPORT_DIRNAMES if (reports_dir / name).is_dir())
    )


def _lifecycle_report_artifact_paths(reports_dir: Path) -> tuple[Path, ...]:
    paths = {reports_dir / name for name in _lifecycle_report_artifact_names(reports_dir)}
    for dirname in _lifecycle_report_artifact_dirnames(reports_dir):
        paths.update(path for path in (reports_dir / dirname).rglob("*") if path.is_file())
    return tuple(sorted(path for path in paths if path.is_file()))


def render_replaceable_generated_files(paths: ProjectPaths) -> tuple[Path, ...]:
    """Return existing generated files that render will replace or remove."""
    if not paths.generated_dir.exists():
        return ()
    preserved_reports = set(_lifecycle_report_artifact_paths(paths.reports_dir))
    return tuple(
        sorted(
            path
            for path in paths.generated_dir.rglob("*")
            if path.is_file() and path not in preserved_reports
        )
    )


def _project_generation_snapshot_digest(
    *,
    project_dir: Path,
    files: dict[Path, bytes],
) -> str:
    payload = [
        {
            "path": path.relative_to(project_dir).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in sorted(
            files.items(),
            key=lambda item: item[0].relative_to(project_dir).as_posix(),
        )
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def project_generation_snapshot_sha256(paths: ProjectPaths) -> str:
    """Hash the exact config plus render-owned generated-file postimage."""

    files: dict[Path, bytes] = {}
    for path in (paths.config_path, *render_replaceable_generated_files(paths)):
        canonical = path.resolve()
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(f"project generation target is missing: {path}") from exc
        if path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
            raise RuntimeError(f"project generation target is unsafe: {path}")
        files[canonical] = path.read_bytes()
    return _project_generation_snapshot_digest(
        project_dir=paths.project_dir.resolve(),
        files=files,
    )


def project_generation_plan_postimage_sha256(
    *,
    project_dir: Path,
    plan: ProjectGenerationPlan,
) -> str:
    """Hash the exact config plus generated postimage represented by a plan."""

    return _project_generation_snapshot_digest(
        project_dir=project_dir.resolve(),
        files={path.resolve(): bytes(content) for path, content in plan.writes.items()},
    )


def build_project_generation_plan(
    *,
    final_paths: ProjectPaths,
    staged_paths: ProjectPaths,
    config_path: Path,
    config_content: bytes | str,
) -> ProjectGenerationPlan:
    """Build exact writes and tombstones without targeting lifecycle reports."""

    encoded_config = (
        config_content.encode("utf-8") if isinstance(config_content, str) else bytes(config_content)
    )
    canonical_config_path = normalize_project_bundle_target(
        final_paths.project_dir,
        config_path,
    )
    writes: dict[Path, bytes] = {canonical_config_path: encoded_config}
    staged_destinations: set[Path] = set()
    for staged in render_replaceable_generated_files(staged_paths):
        if staged.is_symlink():
            raise RuntimeError(f"staged generated file is an unsafe symlink: {staged}")
        relative = staged.relative_to(staged_paths.generated_dir)
        destination = final_paths.generated_dir / relative
        staged_destinations.add(destination)
        writes[destination] = staged.read_bytes()

    current_render_owned = set(render_replaceable_generated_files(final_paths))
    removals = tuple(sorted(current_render_owned - staged_destinations))
    expected_preimages = {
        path: _project_generation_target_preimage(path) for path in (*writes, *removals)
    }
    sha256, preimage_sha256 = project_generation_plan_fingerprints(
        project_dir=final_paths.project_dir,
        writes=writes,
        removals=removals,
        expected_preimages=expected_preimages,
    )
    return ProjectGenerationPlan(
        writes=writes,
        removals=removals,
        sha256=sha256,
        expected_preimages=expected_preimages,
        preimage_sha256=preimage_sha256,
    )


def _project_generation_target_preimage(path: Path) -> str:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "absent"
    if path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
        raise RuntimeError(f"canonical generated target is unsafe: {path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def project_generation_plan_fingerprints(
    *,
    project_dir: Path,
    writes: dict[Path, bytes],
    removals: tuple[Path, ...],
    expected_preimages: dict[Path, str],
) -> tuple[str, str]:
    """Return stable postimage and preimage digests for an exact generation plan."""

    digest_payload = []
    for path, content in sorted(
        writes.items(),
        key=lambda item: item[0].relative_to(project_dir).as_posix(),
    ):
        digest_payload.append(
            {
                "action": "write",
                "path": path.relative_to(project_dir).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    for path in removals:
        digest_payload.append(
            {
                "action": "delete",
                "path": path.relative_to(project_dir).as_posix(),
            }
        )
    encoded_plan = json.dumps(
        digest_payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    preimage_payload = [
        {
            "path": path.relative_to(project_dir).as_posix(),
            "sha256": digest,
        }
        for path, digest in sorted(
            expected_preimages.items(),
            key=lambda item: item[0].relative_to(project_dir).as_posix(),
        )
    ]
    encoded_preimages = json.dumps(
        preimage_payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        "sha256:" + hashlib.sha256(encoded_plan).hexdigest(),
        "sha256:" + hashlib.sha256(encoded_preimages).hexdigest(),
    )


def _preserve_lifecycle_report_artifacts(
    staged_paths: ProjectPaths,
    final_paths: ProjectPaths,
) -> None:
    names = _lifecycle_report_artifact_names(final_paths.reports_dir)
    dirnames = _lifecycle_report_artifact_dirnames(final_paths.reports_dir)
    if not names and not dirnames:
        return
    staged_paths.reports_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = final_paths.reports_dir / name
        target = staged_paths.reports_dir / name
        if not source.is_file() or target.exists():
            continue
        shutil.copy2(source, target)
    for name in dirnames:
        source = final_paths.reports_dir / name
        target = staged_paths.reports_dir / name
        if not source.is_dir():
            continue
        _copy_missing_lifecycle_report_tree(source, target)


def _copy_missing_lifecycle_report_tree(source: Path, target: Path) -> None:
    if source.is_file():
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return
    if not source.is_dir() or (target.exists() and not target.is_dir()):
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        _copy_missing_lifecycle_report_tree(child, target / child.name)


def promote_staged_generated_paths(
    staged_paths: ProjectPaths,
    final_paths: ProjectPaths,
) -> None:
    """Swap a fully rendered staged bundle into the canonical generated/ path."""
    backup_dir: Path | None = None
    try:
        _preserve_lifecycle_report_artifacts(staged_paths, final_paths)
        if final_paths.generated_dir.exists():
            backup_dir = final_paths.project_dir / f".generated-backup-{uuid4().hex}"
            final_paths.generated_dir.rename(backup_dir)
        staged_paths.generated_dir.rename(final_paths.generated_dir)
    except Exception:
        if (
            backup_dir is not None
            and backup_dir.exists()
            and not final_paths.generated_dir.exists()
        ):
            backup_dir.rename(final_paths.generated_dir)
        raise
    finally:
        if staged_paths.generated_dir.exists():
            shutil.rmtree(staged_paths.generated_dir, ignore_errors=True)
    if backup_dir is not None and backup_dir.exists():
        shutil.rmtree(backup_dir)


def _render_project_to_paths(
    config: Any,
    paths: ProjectPaths,
    *,
    component_output_values: dict[str, Any] | None = None,
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> RenderResult:
    """Render Terraform and Flux artifacts into the provided target paths."""
    if isinstance(config, dict):
        _materialize_soperator_component_defaults(config)
        _materialize_soperator_render_only_values(config)
    prune_inactive_mk8s_gpu_app_rows(config)
    materialize_mk8s_gpu_app_values(config)
    materialize_soperator_child_chart_values(config)
    materialize_observability_infra_values(config)
    materialize_observability_app_values(config)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values=component_output_values,
    )
    paths.infra_dir.mkdir(parents=True, exist_ok=True)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    written.extend(render_terraform_artifacts(config, paths, source_profile=source_profile))
    written.extend(render_flux(config, paths, component_output_values=component_output_values))
    return RenderResult(files_written=sorted(written))


def render_project(
    config: Any,
    paths: ProjectPaths,
    *,
    component_output_values: dict[str, Any] | None = None,
    source_profile: SourceProfile = SourceProfile.PORTABLE,
) -> RenderResult:
    """Render Terraform and Flux artifacts transactionally for one validated config."""
    staged_paths = staged_generated_paths(paths)
    try:
        staged_result = _render_project_to_paths(
            config,
            staged_paths,
            component_output_values=component_output_values,
            source_profile=source_profile,
        )
        promote_staged_generated_paths(staged_paths, paths)
    except Exception:
        reset_generated_bundle(staged_paths)
        raise
    return RenderResult(
        files_written=sorted(
            paths.generated_dir / item.relative_to(staged_paths.generated_dir)
            for item in staged_result.files_written
        )
    )
