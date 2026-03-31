from __future__ import annotations

import pytest

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.component_sources import ComponentOutput


@pytest.fixture(autouse=True)
def _stub_catalog_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_outputs(module_source: str) -> tuple[ComponentOutput, ...]:
        source = str(module_source).strip().lower()
        if "mk8s" not in source:
            return ()
        return (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
                sensitive=False,
            ),
            ComponentOutput(
                name="cluster_ca_certificate",
                kind="terraform_output",
                source_path="cluster_ca_certificate",
                sensitive=True,
            ),
            ComponentOutput(
                name="instance_id",
                kind="terraform_output",
                source_path="instance_id",
                sensitive=False,
            ),
        )

    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        _fake_outputs,
    )
