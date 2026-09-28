from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.component_sources import ComponentOutput


@pytest.fixture
def standard_deployment_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly choose standard in tests of other field-wizard controls."""

    def choose(prompt: str, *, default: bool) -> bool:
        assert prompt == "Use fast deploy (Dev/Test only)?"
        assert default is True
        return False

    monkeypatch.setattr("typer.confirm", choose)


@pytest.fixture
def remote_observability_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choose the remote collectors expected by the target-scope fixtures."""
    from types import SimpleNamespace

    from nebius_cxcli import grafana_install

    def answer(prompt, **kwargs):
        value = "remote" if " storage for " in prompt else kwargs.get("default")
        return SimpleNamespace(ask=lambda: value)

    for method in ("select", "confirm", "text"):
        monkeypatch.setattr(
            grafana_install.questionary,
            method,
            answer,
        )


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
    # gRPC uses native sockets; block the SDK request boundary as well.
    from nebius.aio.request import Request

    async def blocked_sdk_request(*_args, **_kwargs):
        _blocked()

    monkeypatch.setattr(Request, "_request_with_authorization_loop", blocked_sdk_request)
    # Cloud CLIs do not use Python sockets. Keep backend/lease tests hermetic
    # unless they explicitly replace the transport or opt into integration.
    original_popen = subprocess.Popen

    def guarded_popen(args, *positional, **kwargs):
        executable = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
        command = list(args) if isinstance(args, (list, tuple)) else str(args).split()
        if command[1:3] == ["-m", "nebius_cxcli.owned_process_worker"]:
            # Apply the same offline boundary to the contained command; the
            # supervisor must not hide a cloud CLI behind a Python executable.
            command = command[7:]
            executable = command[0]
        name = Path(executable).name
        source_inspection = (
            name == "helm"
            and len(command) > 1
            and command[1] in {"show", "template", "lint", "version"}
        ) or (
            name == "terraform"
            and len(command) > 1
            and (
                command[1] in {"version", "-version", "fmt", "validate"}
                or command[1] == "console"
                and kwargs.get("cwd") is not None
                and not any(Path(kwargs["cwd"]).glob("*.tf*"))
                or command[1] == "init"
                and "-backend=false" in command
            )
        )
        source_inspection = source_inspection or (
            name == "kubectl"
            and len(command) == 3
            and command[1] == "kustomize"
            and Path(command[2]).is_dir()
        )
        if "nebius_cxcli.object_storage_worker" in command:
            _blocked()
        if (
            name in {"aws", "nebius", "terraform", "kubectl", "flux", "helm", "ssh"}
            and not source_inspection
        ):
            _blocked()
        return original_popen(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)


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


@pytest.fixture(autouse=True)
def _empty_destroy_backend_for_offline_tests(monkeypatch, request):
    """Existing offline projects have no destroy record unless a test supplies one.

    Keep the new remote read at its transport boundary; tests of admission and
    recovery inject a populated store instead of disabling the production gate.
    """
    if request.node.get_closest_marker("integration"):
        return
    from contextlib import nullcontext

    from nebius_cxcli import destroy_state

    monkeypatch.setattr(destroy_state, "prepare_destroy_admission_auth", lambda _: None)
    monkeypatch.setattr(destroy_state, "existing_project_write_lease", lambda _: nullcontext())


@pytest.fixture
def offline_shared_lease(monkeypatch):
    """Isolate CLI orchestration from coordination, covered by destroy-state tests."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from nebius_cxcli import cli

    @contextmanager
    def lease(**kwargs):
        yield SimpleNamespace(assert_held=lambda: None)

    monkeypatch.setattr(cli, "_deployment_execution", lease)
