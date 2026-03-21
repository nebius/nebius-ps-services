from __future__ import annotations

from types import SimpleNamespace

import nebius_cxcli.managed_tools as managed_tools


def test_resolve_terraform_binary_uses_managed_cache_when_missing_from_path(
    monkeypatch,
) -> None:
    monkeypatch.setattr(managed_tools.shutil, "which", lambda name: None if name == "terraform" else None)
    monkeypatch.setattr(
        managed_tools,
        "load_cli_settings",
        lambda: SimpleNamespace(terraform=SimpleNamespace(version="1.14.1")),
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

    assert resolved == "/tmp/terraform/1.14.1/terraform"
    assert calls == [("1.14.1", "/tmp/terraform/1.14.1/terraform")]


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
    monkeypatch.setattr(managed_tools.shutil, "which", lambda name: None if name == "terraform" else None)
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
    monkeypatch.setattr(managed_tools.shutil, "which", lambda name: None if name == "flux" else None)
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
