from __future__ import annotations

from types import SimpleNamespace

import nebius_cxcli.managed_tools as managed_tools


def test_resolve_terraform_binary_uses_managed_cache_when_missing_from_path(
    monkeypatch,
) -> None:
    monkeypatch.setattr(managed_tools.shutil, "which", lambda name: None if name == "terraform" else None)
    monkeypatch.setattr(
        managed_tools,
        "load_component_sources",
        lambda: SimpleNamespace(
            cli=SimpleNamespace(terraform=SimpleNamespace(version="1.14.1"))
        ),
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
