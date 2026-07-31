from __future__ import annotations

import socket

import pytest

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.component_sources import ComponentOutput


@pytest.fixture(autouse=True)
def _block_unit_test_network(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Prevent accidental network access in the default fast test lane."""
    if request.node.get_closest_marker("integration"):
        return

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "Network access is disabled in unit tests. Mark the test with "
            "@pytest.mark.integration if real network access is required."
        )

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def _zero_fast_verification_convergence_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep failing fast-verification tests single-attempt and sleep-free.

    The production default retries a failed read-only stage verification for a
    bounded convergence budget; unit tests exercising failure paths must not
    wait it out. Tests covering the retry loop set their own budget explicitly.
    """
    import nebius_cxcli.soperator_migration as soperator_migration

    monkeypatch.setattr(
        soperator_migration,
        "_FAST_STAGE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS",
        0,
    )


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
