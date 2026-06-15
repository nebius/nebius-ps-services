"""Managed CLI tool resolution and on-demand installation helpers."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .component_sources import load_cli_settings

FLUX_RELEASES_URL = "https://github.com/fluxcd/flux2/releases"
TERRAFORM_RELEASES_URL = "https://releases.hashicorp.com/terraform"
FLUX_VERSION_ENV = "NEBIUS_CXCLI_FLUX_VERSION"
TERRAFORM_VERSION_ENV = "NEBIUS_CXCLI_TERRAFORM_VERSION"
MAX_MANAGED_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MANAGED_CHECKSUM_BYTES = 4 * 1024 * 1024
MAX_MANAGED_BINARY_BYTES = 512 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _cache_root() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    if cache_home:
        return Path(cache_home).expanduser()
    return Path.home() / ".cache"


def _platform_tokens() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_token = "darwin"
    elif system == "linux":
        os_token = "linux"
    else:
        raise RuntimeError(
            f"Automatic managed tool download is not supported on platform '{platform.system()}'. "
            "Install the required tool manually and rerun the command."
        )

    if machine in {"x86_64", "amd64"}:
        arch_token = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch_token = "arm64"
    else:
        raise RuntimeError(
            f"Automatic managed tool download is not supported on architecture '{platform.machine()}'. "
            "Install the required tool manually and rerun the command."
        )
    return os_token, arch_token


def _cached_binary_path(*, tool: str, version: str) -> Path:
    os_token, arch_token = _platform_tokens()
    return (
        _cache_root()
        / "nebius-cxcli"
        / "tools"
        / tool
        / version
        / f"{os_token}-{arch_token}"
        / tool
    )


def _read_limited_stream(stream: object, *, limit: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_DOWNLOAD_CHUNK_BYTES)  # type: ignore[attr-defined]
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise RuntimeError(f"{label} exceeded the maximum supported size of {limit} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


def _download_bytes(
    url: str,
    *,
    tool_name: str,
    max_bytes: int = MAX_MANAGED_ARCHIVE_BYTES,
) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            content_length = _response_content_length(response)
            if content_length is not None and content_length > max_bytes:
                raise RuntimeError(
                    f"{tool_name} managed download from {url} is {content_length} bytes, "
                    f"which exceeds the maximum supported size of {max_bytes} bytes."
                )
            return _read_limited_stream(
                response,
                limit=max_bytes,
                label=f"{tool_name} managed download from {url}",
            )
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"{tool_name} was not found in PATH, and the managed download failed from {url}: {exc}. "
            f"Install {tool_name} manually or ensure the release endpoint is reachable."
        ) from exc


def _response_content_length(response: object) -> int | None:
    headers = response.info()  # type: ignore[attr-defined]
    raw = headers.get("Content-Length") if headers is not None else None
    try:
        return int(str(raw).strip()) if raw is not None else None
    except ValueError:
        return None


def _checksum_from_manifest(
    manifest: bytes,
    *,
    artifact_name: str,
    checksum_url: str,
    tool_name: str,
) -> str:
    for line in manifest.decode("utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1]
        if Path(name).name == artifact_name:
            if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
                raise RuntimeError(
                    f"{tool_name} checksum manifest {checksum_url} contains an invalid "
                    f"SHA256 digest for {artifact_name}."
                )
            return digest.lower()
    raise RuntimeError(
        f"{tool_name} checksum manifest {checksum_url} did not contain {artifact_name}."
    )


def _verify_sha256(payload: bytes, *, expected: str, artifact_name: str, tool_name: str) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"{tool_name} managed download checksum mismatch for {artifact_name}: "
            f"expected {expected}, got {actual}."
        )


def _cached_checksum_path(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.sha256")


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(raw_temp_path)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _atomic_install_binary(destination: Path, payload: bytes) -> str:
    _atomic_write_bytes(destination, payload, mode=0o755)
    checksum = hashlib.sha256(payload).hexdigest()
    _atomic_write_bytes(
        _cached_checksum_path(destination),
        f"{checksum}\n".encode(),
        mode=0o644,
    )
    return checksum


def _cached_binary_matches_checksum(destination: Path) -> bool:
    if not destination.exists() or not os.access(destination, os.X_OK):
        return False
    checksum_path = _cached_checksum_path(destination)
    try:
        expected = checksum_path.read_text(encoding="utf-8").strip().lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            return False
        digest = hashlib.sha256()
        total = 0
        with destination.open("rb") as handle:
            while True:
                chunk = handle.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_MANAGED_BINARY_BYTES:
                    return False
                digest.update(chunk)
        return total > 0 and digest.hexdigest() == expected
    except (OSError, UnicodeError):
        return False


def _read_archive_member(stream: object, *, label: str) -> bytes:
    return _read_limited_stream(stream, limit=MAX_MANAGED_BINARY_BYTES, label=label)


def _write_flux_binary(version: str, destination: Path) -> Path:
    normalized = version.lstrip("v")
    os_token, arch_token = _platform_tokens()
    artifact_name = f"flux_{normalized}_{os_token}_{arch_token}.tar.gz"
    url = f"{FLUX_RELEASES_URL}/download/{version}/{artifact_name}"
    checksum_url = f"{FLUX_RELEASES_URL}/download/{version}/flux_{normalized}_checksums.txt"
    checksum_bytes = _download_bytes(
        checksum_url,
        tool_name="flux",
        max_bytes=MAX_MANAGED_CHECKSUM_BYTES,
    )
    expected_sha256 = _checksum_from_manifest(
        checksum_bytes,
        artifact_name=artifact_name,
        checksum_url=checksum_url,
        tool_name="flux",
    )
    archive_bytes = _download_bytes(url, tool_name="flux")
    _verify_sha256(
        archive_bytes,
        expected=expected_sha256,
        artifact_name=artifact_name,
        tool_name="flux",
    )
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        flux_member = next(
            (
                member
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name == "flux"
            ),
            None,
        )
        if flux_member is None:
            raise RuntimeError(f"Managed Flux archive from {url} did not contain a `flux` binary.")
        extracted = archive.extractfile(flux_member)
        if extracted is None:
            raise RuntimeError(
                f"Managed Flux archive from {url} contained an unreadable `flux` binary."
            )
        if flux_member.size > MAX_MANAGED_BINARY_BYTES:
            raise RuntimeError(
                f"Managed Flux archive from {url} contains a `flux` binary larger than "
                f"{MAX_MANAGED_BINARY_BYTES} bytes."
            )
        _atomic_install_binary(
            destination,
            _read_archive_member(extracted, label=f"flux binary from {url}"),
        )
    return destination


def _write_terraform_binary(version: str, destination: Path) -> Path:
    os_token, arch_token = _platform_tokens()
    artifact_name = f"terraform_{version}_{os_token}_{arch_token}.zip"
    url = f"{TERRAFORM_RELEASES_URL}/{version}/{artifact_name}"
    checksum_url = f"{TERRAFORM_RELEASES_URL}/{version}/terraform_{version}_SHA256SUMS"
    checksum_bytes = _download_bytes(
        checksum_url,
        tool_name="terraform",
        max_bytes=MAX_MANAGED_CHECKSUM_BYTES,
    )
    expected_sha256 = _checksum_from_manifest(
        checksum_bytes,
        artifact_name=artifact_name,
        checksum_url=checksum_url,
        tool_name="terraform",
    )
    archive_bytes = _download_bytes(url, tool_name="terraform")
    _verify_sha256(
        archive_bytes,
        expected=expected_sha256,
        artifact_name=artifact_name,
        tool_name="terraform",
    )
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        try:
            info = archive.getinfo("terraform")
        except KeyError as exc:
            raise RuntimeError(
                f"Managed Terraform archive from {url} did not contain a `terraform` binary."
            ) from exc
        if info.file_size > MAX_MANAGED_BINARY_BYTES:
            raise RuntimeError(
                f"Managed Terraform archive from {url} contains a `terraform` binary larger than "
                f"{MAX_MANAGED_BINARY_BYTES} bytes."
            )
        with archive.open(info) as extracted:
            _atomic_install_binary(
                destination,
                _read_archive_member(extracted, label=f"terraform binary from {url}"),
            )
    return destination


def configured_flux_version() -> str:
    override = os.environ.get(FLUX_VERSION_ENV, "").strip()
    if override:
        return override if override.startswith("v") else f"v{override}"
    return load_cli_settings().flux.version


def configured_terraform_version() -> str:
    override = os.environ.get(TERRAFORM_VERSION_ENV, "").strip()
    if override:
        return override
    return load_cli_settings().terraform.version


def resolve_flux_binary() -> str:
    from_path = shutil.which("flux")
    if from_path:
        return from_path
    version = configured_flux_version()
    destination = _cached_binary_path(tool="flux", version=version)
    if _cached_binary_matches_checksum(destination):
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_flux_binary(version, destination)
    print(f"Downloaded Flux CLI {version} to {destination}")
    return str(destination)


def resolve_terraform_binary() -> str:
    from_path = shutil.which("terraform")
    if from_path:
        return from_path
    version = configured_terraform_version()
    destination = _cached_binary_path(tool="terraform", version=version)
    if _cached_binary_matches_checksum(destination):
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_terraform_binary(version, destination)
    print(f"Downloaded Terraform CLI {version} to {destination}")
    return str(destination)
