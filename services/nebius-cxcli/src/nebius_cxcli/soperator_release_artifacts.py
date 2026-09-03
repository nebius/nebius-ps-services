"""Verify official Soperator OCI/Helm artifacts against a frozen release source."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from .archive_safety import open_bounded_tar_gz
from .soperator_cache import locked_cache_entry, prepare_private_cache_root
from .soperator_flux_graph import expected_soperator_release_names
from .soperator_release import SoperatorReleaseSnapshot
from .soperator_release_source import (
    SoperatorSourceReceipt,
    default_soperator_source_cache_root,
)
from .soperator_upgrade_progress import sanitized_bounded_command_output

_BUFFER_SIZE = 1024 * 1024
_MAX_CHART_BYTES = 64 * 1024 * 1024
_MAX_CHART_TAR_BYTES = 80 * 1024 * 1024
_MAX_CHART_FILES = 10_000


@dataclass(frozen=True)
class SoperatorArtifactReceipt:
    release: str
    source_manifest_sha256: str
    chart_package_sha256: tuple[str, ...]
    umbrella_render_sha256: str


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_BUFFER_SIZE):
            total += len(chunk)
            if total > _MAX_CHART_BYTES:
                raise ValueError("chart package exceeds the size limit")
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _verify_rendered_release_graph(
    render: bytes,
    values: Mapping[str, object],
) -> None:
    rendered_releases = {
        str(document.get("metadata", {}).get("name") or "")
        for document in yaml.safe_load_all(render)
        if isinstance(document, dict) and document.get("kind") == "HelmRelease"
    }
    expected_releases = set(expected_soperator_release_names(values))
    if rendered_releases != expected_releases:
        missing = sorted(expected_releases - rendered_releases)
        unexpected = sorted(rendered_releases - expected_releases)
        raise ValueError(
            "locked Soperator release graph differs from the verified upstream render "
            f"(missing={missing}, unexpected={unexpected})"
        )


def _chart_file_map(package: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    casefold_paths: dict[str, str] = {}
    root_name = ""
    count = 0
    total = 0
    with (
        package.open("rb") as compressed,
        open_bounded_tar_gz(
            compressed,
            max_uncompressed_bytes=_MAX_CHART_TAR_BYTES,
            label="chart package",
        ) as chart,
    ):
        for member in chart:
            count += 1
            if count > _MAX_CHART_FILES:
                raise ValueError("chart package exceeds the file-count limit")
            if "\\" in member.name or "\x00" in member.name:
                raise ValueError(f"unsafe chart package path: {member.name!r}")
            normalized = unicodedata.normalize("NFC", member.name.rstrip("/"))
            path = PurePosixPath(normalized)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise ValueError(f"unsafe chart package path: {member.name!r}")
            if not root_name:
                root_name = path.parts[0]
            if path.parts[0] != root_name:
                raise ValueError("chart package must contain one top-level directory")
            relative = PurePosixPath(*path.parts[1:])
            if str(relative) == ".":
                if not member.isdir():
                    raise ValueError("chart package root must be a directory")
                continue
            relative_name = str(relative)
            folded = relative_name.casefold()
            if relative_name in files or folded in casefold_paths:
                raise ValueError(f"chart package contains duplicate path {relative_name!r}")
            casefold_paths[folded] = relative_name
            if member.isdir():
                continue
            if not member.isreg() or member.size < 0:
                raise ValueError(f"chart package member is not regular: {relative_name!r}")
            total += member.size
            if member.size > _MAX_CHART_BYTES or total > _MAX_CHART_BYTES:
                raise ValueError("chart package exceeds the expanded-size limit")
            source = chart.extractfile(member)
            if source is None:
                raise ValueError(f"chart package member is unreadable: {relative_name!r}")
            content = source.read(_MAX_CHART_BYTES + 1)
            if len(content) != member.size:
                raise ValueError(f"chart package member is truncated: {relative_name!r}")
            if relative_name == "Chart.yaml":
                metadata = yaml.safe_load(content)
                content = yaml.safe_dump(metadata, sort_keys=True).encode()
            files[relative_name] = content
    if "Chart.yaml" not in files or "values.yaml" not in files:
        raise ValueError("chart package is missing Chart.yaml or values.yaml")
    return files


def _run(command: list[str], *, label: str) -> subprocess.CompletedProcess[str]:
    timeout = 300
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raw_detail = exc.stderr or exc.stdout or ""
        if isinstance(raw_detail, bytes):
            raw_detail = raw_detail.decode("utf-8", errors="replace")
        detail = sanitized_bounded_command_output(raw_detail)
        raise RuntimeError(
            f"{label} timed out after {timeout} seconds" + (f": {detail}" if detail else "")
        ) from None
    if completed.returncode != 0:
        detail = sanitized_bounded_command_output(completed.stderr or completed.stdout or "")
        raise RuntimeError(
            f"{label} failed with exit code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed


def _cache_chart_package(
    *,
    helm: str,
    chart: str,
    version: str,
    repository: str,
    expected_sha256: str,
    expected_oci_digest: str | None,
    cache_dir: Path,
) -> Path:
    cache_dir = prepare_private_cache_root(cache_dir)
    digest_token = expected_sha256.removeprefix("sha256:")
    package = cache_dir / f"{digest_token}.tgz"
    with locked_cache_entry(cache_dir, digest_token):
        if package.exists():
            if (
                package.is_file()
                and not package.is_symlink()
                and _sha256_file(package) == expected_sha256
            ):
                return package
            if package.is_symlink():
                package.unlink()
            elif package.is_file():
                package.chmod(0o600)
                package.unlink()
            else:
                raise ValueError(f"cached chart package path for {chart} is not a regular file")
        with tempfile.TemporaryDirectory(prefix=f".{chart}-", dir=cache_dir) as staging_value:
            staging = Path(staging_value)
            if repository.startswith("oci://"):
                command = [helm, "pull", f"{repository.rstrip('/')}/{chart}", "--version", version]
            else:
                command = [helm, "pull", chart, "--repo", repository, "--version", version]
            command.extend(["--destination", str(staging)])
            completed = _run(command, label=f"download chart {chart} {version}")
            packages = list(staging.glob("*.tgz"))
            if len(packages) != 1:
                raise ValueError(f"chart download for {chart} produced an unexpected package set")
            downloaded = packages[0]
            actual_sha256 = _sha256_file(downloaded)
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"chart package digest mismatch for {chart}: expected {expected_sha256}, "
                    f"got {actual_sha256}"
                )
            if expected_oci_digest is not None:
                digest_line = f"Digest: {expected_oci_digest}"
                output = "\n".join((completed.stdout or "", completed.stderr or ""))
                if digest_line not in output:
                    raise ValueError(
                        f"OCI manifest digest mismatch for {chart}: expected {expected_oci_digest}"
                    )
            os.replace(downloaded, package)
            package.chmod(0o444)
        return package


def _write_chart_tree(package: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for relative, content in _chart_file_map(package).items():
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)


def _package_verified_source_chart(
    source_chart: Path,
    destination: Path,
    *,
    helm: str,
    dependency_packages: dict[str, Path],
) -> Path:
    staged = destination / "source"
    shutil.copytree(source_chart, staged)
    for path in staged.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    staged.chmod(0o700)
    metadata = yaml.safe_load((staged / "Chart.yaml").read_text(encoding="utf-8"))
    dependencies = metadata.get("dependencies", []) if isinstance(metadata, dict) else []
    if not isinstance(dependencies, list):
        raise ValueError(f"source chart {source_chart.name} has invalid dependencies")
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise ValueError(f"source chart {source_chart.name} has invalid dependency metadata")
        name = str(dependency.get("name") or "").strip()
        package = dependency_packages.get(name)
        if not name or package is None:
            raise ValueError(
                f"source chart {source_chart.name} dependency {name or '?'} is not locked"
            )
        _write_chart_tree(package, staged / "charts" / name)
    _run(
        [helm, "package", str(staged), "--destination", str(destination)],
        label=f"package verified source chart {source_chart.name}",
    )
    packages = list(destination.glob("*.tgz"))
    if len(packages) != 1:
        raise ValueError(f"packaging source chart {source_chart.name} produced unexpected output")
    return packages[0]


def verify_soperator_release_artifacts(
    lock: SoperatorReleaseSnapshot,
    source: SoperatorSourceReceipt,
    *,
    cache_root: Path | None = None,
    values: Mapping[str, object] | None = None,
) -> SoperatorArtifactReceipt:
    """Verify chart packages and the umbrella render against accepted source."""

    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("helm is required to verify the official Soperator release artifacts")
    if source.release != lock.release or source.manifest_sha256 != lock.source_manifest_sha256:
        raise ValueError("Soperator source receipt does not match the operation snapshot")
    source_root = Path(source.source_dir).resolve(strict=True)
    root = (cache_root or default_soperator_source_cache_root()).expanduser()
    chart_cache = prepare_private_cache_root(root / "charts")

    package_digests: list[str] = []
    umbrella_package: Path | None = None
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-soperator-source-charts-") as temp_value:
        temp = Path(temp_value)
        dependency_packages: dict[str, Path] = {}
        for third_party_chart in lock.third_party_charts.values():
            package = _cache_chart_package(
                helm=helm,
                chart=third_party_chart.chart,
                version=third_party_chart.version,
                repository=third_party_chart.repository,
                expected_sha256=third_party_chart.package_sha256,
                expected_oci_digest=third_party_chart.oci_digest,
                cache_dir=chart_cache,
            )
            dependency_packages[third_party_chart.chart] = package
            package_digests.append(third_party_chart.package_sha256)
        for key, upstream_chart in lock.charts.items():
            official = _cache_chart_package(
                helm=helm,
                chart=upstream_chart.name,
                version=upstream_chart.version,
                repository=lock.registry,
                expected_sha256=upstream_chart.package_sha256,
                expected_oci_digest=upstream_chart.digest,
                cache_dir=chart_cache,
            )
            source_package_dir = temp / key
            source_package_dir.mkdir()
            source_package = _package_verified_source_chart(
                source_root / upstream_chart.source_path,
                source_package_dir,
                helm=helm,
                dependency_packages=dependency_packages,
            )
            source_files = _chart_file_map(source_package)
            official_files = _chart_file_map(official)
            # Helm creates Chart.lock from already verified exact dependencies;
            # its generated timestamp is not part of upstream source authority.
            source_files.pop("Chart.lock", None)
            official_files.pop("Chart.lock", None)
            if source_files != official_files:
                raise ValueError(
                    f"official OCI chart {upstream_chart.name} differs from release source"
                )
            package_digests.append(upstream_chart.package_sha256)
            if key == "umbrella":
                umbrella_package = official
        if umbrella_package is None:
            raise ValueError("release snapshot has no umbrella chart")
        value_arguments: list[str] = []
        if values is not None:
            values_path = temp / "values.yaml"
            values_path.write_text(yaml.safe_dump(dict(values), sort_keys=False), encoding="utf-8")
            values_path.chmod(0o600)
            value_arguments = ["--values", str(values_path)]
        source_render = _run(
            [
                helm,
                "template",
                "soperator-fluxcd",
                str(source_root / lock.umbrella.source_path),
                "--namespace",
                "flux-system",
                *value_arguments,
            ],
            label="render verified source umbrella",
        ).stdout.encode()
        package_render = _run(
            [
                helm,
                "template",
                "soperator-fluxcd",
                str(umbrella_package),
                "--namespace",
                "flux-system",
                *value_arguments,
            ],
            label="render official OCI umbrella",
        ).stdout.encode()
        if source_render != package_render:
            raise ValueError("official OCI umbrella render differs from release source")
        if values is not None:
            _verify_rendered_release_graph(source_render, values)
        render_digest = _sha256_bytes(source_render)

    return SoperatorArtifactReceipt(
        release=lock.release,
        source_manifest_sha256=source.manifest_sha256,
        chart_package_sha256=tuple(sorted(package_digests)),
        umbrella_render_sha256=render_digest,
    )


__all__ = ["SoperatorArtifactReceipt", "verify_soperator_release_artifacts"]
