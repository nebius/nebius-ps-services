"""Managed CLI tool resolution and on-demand installation helpers."""

from __future__ import annotations

import io
import os
import platform
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .component_sources import load_cli_settings

FLUX_RELEASES_URL = "https://github.com/fluxcd/flux2/releases"
TERRAFORM_RELEASES_URL = "https://releases.hashicorp.com/terraform"
FLUX_VERSION_ENV = "NEBIUS_CXCLI_FLUX_VERSION"
TERRAFORM_VERSION_ENV = "NEBIUS_CXCLI_TERRAFORM_VERSION"


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


def _download_bytes(url: str, *, tool_name: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"{tool_name} was not found in PATH, and the managed download failed from {url}: {exc}. "
            f"Install {tool_name} manually or ensure the release endpoint is reachable."
        ) from exc


def _write_flux_binary(version: str, destination: Path) -> Path:
    normalized = version.lstrip("v")
    os_token, arch_token = _platform_tokens()
    url = f"{FLUX_RELEASES_URL}/download/{version}/flux_{normalized}_{os_token}_{arch_token}.tar.gz"
    archive_bytes = _download_bytes(url, tool_name="flux")
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
        destination.write_bytes(extracted.read())
    destination.chmod(0o755)
    return destination


def _write_terraform_binary(version: str, destination: Path) -> Path:
    os_token, arch_token = _platform_tokens()
    url = f"{TERRAFORM_RELEASES_URL}/{version}/terraform_{version}_{os_token}_{arch_token}.zip"
    archive_bytes = _download_bytes(url, tool_name="terraform")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        try:
            extracted = archive.read("terraform")
        except KeyError as exc:
            raise RuntimeError(
                f"Managed Terraform archive from {url} did not contain a `terraform` binary."
            ) from exc
        destination.write_bytes(extracted)
    destination.chmod(0o755)
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
    if destination.exists() and os.access(destination, os.X_OK):
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
    if destination.exists() and os.access(destination, os.X_OK):
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_terraform_binary(version, destination)
    print(f"Downloaded Terraform CLI {version} to {destination}")
    return str(destination)
