from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

import nebius_cxcli.managed_tools as managed_tools


def test_resolve_terraform_binary_uses_managed_cache_when_missing_from_path(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        managed_tools.shutil, "which", lambda name: None if name == "terraform" else None
    )
    monkeypatch.setattr(
        managed_tools,
        "load_cli_settings",
        lambda: SimpleNamespace(terraform=SimpleNamespace(version="1.15.5")),
    )
    monkeypatch.setattr(
        managed_tools,
        "_cached_binary_path",
        lambda *, tool, version: managed_tools.Path(f"/tmp/{tool}/{version}/{tool}"),
    )
    monkeypatch.setattr(managed_tools.os, "access", lambda path, mode: False)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        managed_tools,
        "_write_terraform_binary",
        lambda version, destination: calls.append((version, str(destination))) or destination,
    )

    resolved = managed_tools.resolve_terraform_binary()

    assert resolved == "/tmp/terraform/1.15.5/terraform"
    assert calls == [("1.15.5", "/tmp/terraform/1.15.5/terraform")]


def test_resolve_flux_binary_prefers_existing_path(monkeypatch) -> None:
    monkeypatch.setattr(
        managed_tools.shutil,
        "which",
        lambda name: "/usr/local/bin/flux" if name == "flux" else None,
    )

    resolved = managed_tools.resolve_flux_binary()

    assert resolved == "/usr/local/bin/flux"


def test_resolve_terraform_binary_uses_env_override_without_loading_catalog(monkeypatch) -> None:
    monkeypatch.setenv(managed_tools.TERRAFORM_VERSION_ENV, "1.15.0")
    monkeypatch.setattr(
        managed_tools.shutil, "which", lambda name: None if name == "terraform" else None
    )
    monkeypatch.setattr(
        managed_tools,
        "load_cli_settings",
        lambda: (_ for _ in ()).throw(AssertionError("catalog should not be loaded")),
    )
    monkeypatch.setattr(
        managed_tools,
        "_cached_binary_path",
        lambda *, tool, version: managed_tools.Path(f"/tmp/{tool}/{version}/{tool}"),
    )
    monkeypatch.setattr(managed_tools.os, "access", lambda path, mode: False)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        managed_tools,
        "_write_terraform_binary",
        lambda version, destination: calls.append((version, str(destination))) or destination,
    )

    resolved = managed_tools.resolve_terraform_binary()

    assert resolved == "/tmp/terraform/1.15.0/terraform"
    assert calls == [("1.15.0", "/tmp/terraform/1.15.0/terraform")]


def test_resolve_flux_binary_uses_env_override_without_loading_catalog(monkeypatch) -> None:
    monkeypatch.setenv(managed_tools.FLUX_VERSION_ENV, "2.9.0")
    monkeypatch.setattr(
        managed_tools.shutil, "which", lambda name: None if name == "flux" else None
    )
    monkeypatch.setattr(
        managed_tools,
        "load_cli_settings",
        lambda: (_ for _ in ()).throw(AssertionError("catalog should not be loaded")),
    )
    monkeypatch.setattr(
        managed_tools,
        "_cached_binary_path",
        lambda *, tool, version: managed_tools.Path(f"/tmp/{tool}/{version}/{tool}"),
    )
    monkeypatch.setattr(managed_tools.os, "access", lambda path, mode: False)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        managed_tools,
        "_write_flux_binary",
        lambda version, destination: calls.append((version, str(destination))) or destination,
    )

    resolved = managed_tools.resolve_flux_binary()

    assert resolved == "/tmp/flux/v2.9.0/flux"
    assert calls == [("v2.9.0", "/tmp/flux/v2.9.0/flux")]


def test_write_terraform_binary_verifies_checksum_and_writes_cache_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    binary = b"terraform-binary"
    archive_io = io.BytesIO()
    with zipfile.ZipFile(archive_io, "w") as archive:
        archive.writestr("terraform", binary)
    archive_bytes = archive_io.getvalue()
    artifact = "terraform_1.15.5_linux_amd64.zip"
    manifest = f"{hashlib.sha256(archive_bytes).hexdigest()}  {artifact}\n".encode()

    monkeypatch.setattr(managed_tools, "_platform_tokens", lambda: ("linux", "amd64"))

    def _download(url: str, *, tool_name: str, max_bytes: int = managed_tools.MAX_MANAGED_ARCHIVE_BYTES):
        assert tool_name == "terraform"
        if url.endswith("SHA256SUMS"):
            return manifest
        assert url.endswith(artifact)
        return archive_bytes

    monkeypatch.setattr(managed_tools, "_download_bytes", _download)
    destination = tmp_path / "terraform"

    assert managed_tools._write_terraform_binary("1.15.5", destination) == destination

    assert destination.read_bytes() == binary
    assert destination.stat().st_mode & 0o111
    assert managed_tools._cached_checksum_path(destination).read_text(encoding="utf-8").strip() == (
        hashlib.sha256(binary).hexdigest()
    )
    assert managed_tools._cached_binary_matches_checksum(destination)


def test_write_flux_binary_verifies_checksum_and_writes_cache_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    binary = b"flux-binary"
    archive_io = io.BytesIO()
    with tarfile.open(fileobj=archive_io, mode="w:gz") as archive:
        info = tarfile.TarInfo("flux")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))
    archive_bytes = archive_io.getvalue()
    artifact = "flux_2.8.0_linux_amd64.tar.gz"
    manifest = f"{hashlib.sha256(archive_bytes).hexdigest()}  {artifact}\n".encode()

    monkeypatch.setattr(managed_tools, "_platform_tokens", lambda: ("linux", "amd64"))

    def _download(url: str, *, tool_name: str, max_bytes: int = managed_tools.MAX_MANAGED_ARCHIVE_BYTES):
        assert tool_name == "flux"
        if url.endswith("checksums.txt"):
            return manifest
        assert url.endswith(artifact)
        return archive_bytes

    monkeypatch.setattr(managed_tools, "_download_bytes", _download)
    destination = tmp_path / "flux"

    assert managed_tools._write_flux_binary("v2.8.0", destination) == destination

    assert destination.read_bytes() == binary
    assert managed_tools._cached_binary_matches_checksum(destination)


def test_write_terraform_binary_rejects_checksum_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    archive_io = io.BytesIO()
    with zipfile.ZipFile(archive_io, "w") as archive:
        archive.writestr("terraform", b"terraform-binary")
    artifact = "terraform_1.15.5_linux_amd64.zip"
    manifest = f"{'0' * 64}  {artifact}\n".encode()

    monkeypatch.setattr(managed_tools, "_platform_tokens", lambda: ("linux", "amd64"))
    monkeypatch.setattr(
        managed_tools,
        "_download_bytes",
        lambda url, *, tool_name, max_bytes=managed_tools.MAX_MANAGED_ARCHIVE_BYTES: (
            manifest if url.endswith("SHA256SUMS") else archive_io.getvalue()
        ),
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        managed_tools._write_terraform_binary("1.15.5", tmp_path / "terraform")

    assert not (tmp_path / "terraform").exists()


def test_download_bytes_rejects_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def info(self):
            return {"Content-Length": "6"}

        def read(self, _size=-1):
            return b"abcdef"

    monkeypatch.setattr(managed_tools.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    with pytest.raises(RuntimeError, match="exceeds the maximum supported size"):
        managed_tools._download_bytes("https://example.invalid/tool", tool_name="tool", max_bytes=5)
