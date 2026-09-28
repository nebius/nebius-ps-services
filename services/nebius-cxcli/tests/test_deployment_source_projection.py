"""Coordinated stages must re-enter the strict source schema, not runtime aliases."""

import copy
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from nebius_cxcli.config_model import to_dynamic_payload, to_runtime_payload
from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
from nebius_cxcli.deployment_plan import DeploymentStage, DeploymentStageKind
from nebius_cxcli.runtime_validation import validate_dynamic_payload_structure


def source_payload():
    return {
        "version": "v1",
        "client_info": {},
        "deploy": {},
        "infra": {
            "components": [{"id": "mk8s", "instance_id": "cluster", "enabled": True, "inputs": {}}]
        },
        "apps": {
            "charts": [
                {
                    "id": component,
                    "instance_id": "cluster",
                    "group": "platform",
                    "enabled": True,
                    "repo": "oci://cr.eu-north1.nebius.cloud/marketplace",
                    "version": "25.7.0",
                    "values": {"selected": {"setting": "preserved"}},
                }
                for component in ("nvidia-network-operator", "nvidia-gpu-operator")
            ]
        },
    }


def test_runtime_projection_round_trips_into_strict_source_without_mutating_authority():
    source = source_payload()
    validate_dynamic_payload_structure(source)
    runtime = to_runtime_payload(source)
    before = copy.deepcopy(runtime)
    assert runtime["apps"]["charts"][0]["target_ref"] == "cluster"
    projected = to_dynamic_payload(runtime)
    validate_dynamic_payload_structure(projected)
    assert projected == source
    assert runtime == before
    assert to_runtime_payload(projected) == runtime


def test_projection_rejects_conflicting_derived_target_identity():
    runtime = to_runtime_payload(source_payload())
    runtime["apps"]["charts"][0]["target_ref"] = "different-cluster"
    with pytest.raises(ValueError, match="target identity"):
        to_dynamic_payload(runtime)


def test_public_source_schema_still_rejects_authored_target_ref():
    source = source_payload()
    source["apps"]["charts"][0]["target_ref"] = "cluster"
    with pytest.raises(ValueError, match="unsupported field.*target_ref"):
        validate_dynamic_payload_structure(source)


def test_coordinated_admission_projects_runtime_at_the_real_renderer_boundary(tmp_path):
    runtime = to_runtime_payload(source_payload())
    before = copy.deepcopy(runtime)

    class RendererReached(Exception):
        pass

    def render(**kwargs):
        validate_dynamic_payload_structure(kwargs["source_payload"])
        assert kwargs["source_payload"] == source_payload()
        raise RendererReached

    executor = _CliDeploymentExecutor.__new__(_CliDeploymentExecutor)
    executor.cli = SimpleNamespace(
        _render_soperator_upgrade_admission=render,
        progress_console=Console(file=StringIO(), force_terminal=False),
    )
    executor.generation = executor.source_generation = SimpleNamespace(
        manifest={"runtime_config": runtime}
    )
    executor.paths = SimpleNamespace(config_path=tmp_path / "config.yaml")
    executor.plan = SimpleNamespace(
        target_ref="cluster", stages=(DeploymentStage(DeploymentStageKind.TRANSITION, runtime),)
    )
    with pytest.raises(RendererReached):
        executor._admit_coordinated_stages(executor.plan)
    assert runtime == before
