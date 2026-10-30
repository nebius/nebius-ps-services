"""The source contract has one policy owner and no deploy compatibility path."""

from __future__ import annotations

import base64
import copy
import json

import pytest
import yaml

from nebius_cxcli.cluster_config import (
    cluster_rows,
    require_cluster_row,
    require_soperator_row,
    validate_source_contract,
)
from nebius_cxcli.config_loader import normalize_runtime_config_payload
from nebius_cxcli.config_model import to_dynamic_payload, to_runtime_payload
from nebius_cxcli.deployment_recovery import restore_execution_cache
from nebius_cxcli.generated_manifest import validate_manifest_contract
from nebius_cxcli.observability import (
    materialize_observability_infra_values,
    normalize_observability_project_settings,
)
from nebius_cxcli.ordinary_apps import protected_config_digest
from nebius_cxcli.runtime_validation import validate_dynamic_payload_structure


def source():
    return {
        "version": "v2",
        "client_info": {"nebius": {"project_id": "project-test", "region_id": "eu-north1"}},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "managed",
                    "enabled": True,
                    "inputs": {},
                    "observability": {"enabled": False},
                },
            ],
            "external_clusters": [
                {
                    "instance_id": "external",
                    "kube_context": "external-context",
                    "observability": {"enabled": False},
                },
            ],
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "external",
                    "enabled": True,
                    "values": {},
                    "deployment_testing": {"smoke": {"enabled": False}},
                },
            ]
        },
    }


@pytest.mark.parametrize("value", [None, {}, {"targets": []}])
def test_old_section_is_rejected_before_normalization(value):
    payload = source()
    payload["deploy"] = value
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="deploy section is unsupported"):
        normalize_runtime_config_payload(payload)
    assert payload == before


@pytest.mark.parametrize("version", [None, "v1", "v3"])
def test_version_must_be_explicit_v2(version):
    payload = source()
    if version is None:
        payload.pop("version")
    else:
        payload["version"] = version
    with pytest.raises(ValueError, match="requires version: v2"):
        validate_source_contract(payload)


def test_projection_preserves_each_owner_and_external_registry():
    payload = source()
    require_cluster_row(payload, "managed")["secrets"] = {"mysterybox": {"enabled": False}}
    require_soperator_row(payload, "external")["profiling"] = {"enabled": False}
    assert to_dynamic_payload(to_runtime_payload(payload)) == payload
    assert "deploy" not in to_runtime_payload(payload)


def test_external_only_observability_is_preserved_and_normalization_is_idempotent():
    payload = source()
    payload["infra"]["components"] = []
    payload["apps"]["charts"] = []
    assert normalize_observability_project_settings(payload)
    assert cluster_rows(payload)[0]["observability"]["enabled"] is False
    assert not normalize_observability_project_settings(payload)
    assert payload["infra"]["components"] == []
    assert "deploy" not in payload


def test_two_vms_keep_separate_logs_and_systemd_units():
    payload = source()
    payload["apps"]["charts"] = []
    payload["infra"]["components"] = [
        {
            "id": "vm",
            "instance_id": "one",
            "enabled": True,
            "inputs": {},
            "observability": {
                "enabled": True,
                "vm": {"logs": {"enabled": True, "systemd_units": ["one.service"]}},
            },
        },
        {
            "id": "vm",
            "instance_id": "two",
            "enabled": True,
            "inputs": {},
            "observability": {
                "enabled": False,
                "vm": {"logs": {"enabled": True, "systemd_units": ["two.service"]}},
            },
        },
    ]
    normalize_observability_project_settings(payload)
    materialize_observability_infra_values(payload)
    one, two = payload["infra"]["components"]
    assert "one.service" in str(one["inputs"])
    assert "two.service" not in str(one["inputs"])
    assert "two.service" not in str(two["inputs"])
    assert two["observability"]["vm"]["logs"]["systemd_units"] == ["two.service"]


def test_external_and_managed_identity_collision_is_rejected():
    payload = source()
    payload["infra"]["external_clusters"][0]["instance_id"] = "managed"
    with pytest.raises(ValueError, match="duplicated"):
        validate_dynamic_payload_structure(payload)


@pytest.mark.parametrize("project", ["project-other", "project-test"])
def test_managed_project_identity_matches_nested_terraform_parent(project):
    from nebius_cxcli.runtime_validation import _validate_cluster_policies

    payload = source()
    managed = require_cluster_row(payload, "managed")
    managed["inputs"] = {"cluster": {"parent_id": "project-other"}}
    managed["project_id"] = project
    if project == "project-other":
        _validate_cluster_policies(payload)
    else:
        with pytest.raises(
            ValueError, match="project_id conflicts with its infrastructure identity"
        ):
            _validate_cluster_policies(payload)


@pytest.mark.parametrize("field", ["registration", "rollout", "profiling", "deployment_testing"])
def test_ordinary_apps_protect_soperator_policy(field):
    payload = source()
    before = protected_config_digest(payload)
    require_soperator_row(payload, "external")[field] = {"changed": True}
    assert protected_config_digest(payload) != before


def test_recovery_rejects_old_postimages_before_publishing_any_file(tmp_path):
    cache = {
        "tenant/project/generated/infra/main.tf": base64.b64encode(b"new").decode(),
        "tenant/project/config.yaml": base64.b64encode(b"version: v1\ndeploy: {}\n").decode(),
    }
    with pytest.raises(ValueError, match="deploy section is unsupported"):
        restore_execution_cache(tmp_path, cache)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["schema", "source", "deploy"])
def test_generated_manifest_rejects_old_outer_and_embedded_contracts(change, tmp_path):
    manifest = {
        "schema": "nebius-cxcli-generated/v3",
        "runtime_config": source(),
        "execution": {
            "backend": {},
            "targets": [],
            "required_component_outputs": [],
            "status_watchers": [],
            "validations": [],
        },
    }
    validate_manifest_contract(manifest)
    if change == "schema":
        manifest["schema"] = "nebius-cxcli-generated/v2"
    elif change == "source":
        manifest["runtime_config"]["version"] = "v1"
    else:
        manifest["deploy"] = {}
    cache = {
        "tenant/project/generated/nebius-cxcli-manifest.json": base64.b64encode(
            json.dumps(manifest).encode()
        ).decode()
    }
    with pytest.raises(ValueError):
        restore_execution_cache(tmp_path, cache)
    assert not list(tmp_path.iterdir())


def test_raw_reader_rejects_old_config_before_auth(tmp_path, monkeypatch):
    from nebius_cxcli import cli

    path = tmp_path / "config.yaml"
    payload = source()
    payload["version"] = "v1"
    path.write_text(yaml.safe_dump(payload))
    monkeypatch.setattr(
        cli, "_ensure_project_auth_identity", lambda **_: pytest.fail("auth called")
    )
    with pytest.raises(ValueError, match="requires version: v2"):
        cli._load_config_payload(path)


@pytest.mark.parametrize(
    "field,value",
    [("project_id", "other-project"), ("region_id", "other-region"), ("project_id", [])],
)
def test_managed_identity_override_cannot_diverge(field, value):
    payload = source()
    require_cluster_row(payload, "managed")[field] = value
    with pytest.raises(ValueError, match="conflicts|non-empty string"):
        validate_dynamic_payload_structure(payload)


def test_external_only_collectors_use_the_declared_cluster():
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.observability import resolve_observability_app_selection

    payload = source()
    payload["infra"]["components"] = []
    payload["apps"]["charts"] = []
    require_cluster_row(payload, "external")["observability"]["enabled"] = True
    normalize_observability_project_settings(payload)
    selected = resolve_observability_app_selection(
        payload,
        selected_app_ids={"nebius-observability-agent", "grafana"},
        app_entries=component_entries("apps"),
    )
    assert selected.kubernetes_agent_required
    assert not selected.issues


def test_external_only_source_renders_no_terraform_cluster(tmp_path):
    from nebius_cxcli import cli
    from nebius_cxcli.config_loader import load_config
    from nebius_cxcli.infra_render import render_terraform_artifacts
    from nebius_cxcli.paths import resolve_project_paths

    payload = source()
    payload["client_info"]["client_name"] = "example"
    payload["client_info"]["notifications"] = {"email_enabled": False, "email": None}
    payload["client_info"]["nebius"]["tenant_id"] = "tenant-test"
    payload["infra"]["components"] = []
    payload["apps"]["charts"] = []
    config_path = tmp_path / "tenant-test" / "project-test" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    paths.infra_dir.mkdir(parents=True)
    render_terraform_artifacts(config, paths)
    assert 'module "' not in (paths.infra_dir / "main.tf").read_text()
    assert "external" not in (paths.infra_dir / "terraform.auto.tfvars.json").read_text()
    assert cli._enabled_cluster_handoffs(config) == []
    targets = cli._enabled_external_cluster_targets(config)
    assert [target["instance_id"] for target in targets] == ["external"]
    assert targets[0]["ownership"] == "external"
    assert yaml.safe_load(config_path.read_text()) == payload


@pytest.mark.parametrize("policy", ["routing", "secrets"])
def test_disabled_cluster_retains_policy_until_reenabled(policy):
    from nebius_cxcli.mysterybox_eso import normalize_mysterybox_eso_project_settings
    from nebius_cxcli.runtime_validation import _validate_cluster_policies

    payload = source()
    payload["infra"]["external_clusters"] = []
    payload["apps"]["charts"] = []
    owner = payload["infra"]["components"][0]
    owner["enabled"] = False
    if policy == "routing":
        owner["observability"]["routing"] = {}
    else:
        owner["secrets"] = {"mysterybox": {"enabled": True, "sync_namespaces": ["default"]}}
    before = copy.deepcopy(payload)
    _validate_cluster_policies(payload)
    normalize_observability_project_settings(payload)
    normalize_mysterybox_eso_project_settings(payload)
    _validate_cluster_policies(payload, materialized=True)
    assert payload == before
    owner["enabled"] = True
    normalize_observability_project_settings(payload)
    normalize_mysterybox_eso_project_settings(payload)
    _validate_cluster_policies(payload, materialized=True)
    if policy == "secrets":
        assert owner["secrets"]["mysterybox"]["store_name"]


@pytest.mark.parametrize("ordinary_first", [False, True])
def test_soperator_smoke_disable_is_not_overridden_by_another_app(ordinary_first):
    from nebius_cxcli.soperator_validation import soperator_cluster_validation_specs

    payload = source()
    ordinary = {"id": "grafana", "instance_id": "external", "enabled": True, "values": {}}
    charts = payload["apps"]["charts"]
    charts.insert(0 if ordinary_first else len(charts), ordinary)
    assert soperator_cluster_validation_specs(payload) == []
    assert len(soperator_cluster_validation_specs(payload, respect_deployment_testing=False)) == 1


@pytest.mark.parametrize("testing", [None, {"smoke": None}])
def test_soperator_nullable_testing_defaults_are_idempotent(testing):
    from nebius_cxcli.runtime_validation import _validate_soperator_policy
    from nebius_cxcli.soperator_validation import (
        normalize_soperator_project_deployment_testing_settings,
    )

    payload = source()
    row = require_soperator_row(payload, "external")
    row["deployment_testing"] = testing
    _validate_soperator_policy(row, "apps.charts[0]")
    assert normalize_soperator_project_deployment_testing_settings(payload)
    assert row["deployment_testing"] == {"smoke": {"enabled": True}}
    assert not normalize_soperator_project_deployment_testing_settings(payload)


@pytest.mark.parametrize("instance_id", ["external-clusters", "components"])
@pytest.mark.parametrize("declare_external", [False, True])
def test_runtime_alias_preserves_canonical_infrastructure(instance_id, declare_external):
    from nebius_cxcli.deploy_targets import enabled_cluster_target_refs

    payload = source()
    payload["infra"]["components"] = [
        {"id": "vm", "instance_id": instance_id, "enabled": False, "inputs": {}}
    ]
    payload["apps"]["charts"] = []
    if not declare_external:
        payload["infra"].pop("external_clusters")
    validate_dynamic_payload_structure(payload)
    runtime = to_runtime_payload(payload)
    assert runtime["infra"]["components"] == payload["infra"]["components"]
    assert runtime["infra"].get("external_clusters") == payload["infra"].get("external_clusters")
    assert enabled_cluster_target_refs(runtime) == enabled_cluster_target_refs(payload)
    assert to_dynamic_payload(runtime) == payload


@pytest.mark.parametrize("target", ["managed", "external"])
def test_scoped_normalization_preserves_unselected_null_observability(target):
    from nebius_cxcli.observability_routing import (
        collection_enabled,
        routed_targets,
        target_settings,
    )

    payload = source()
    payload["apps"]["charts"] = []
    owner = require_cluster_row(payload, target)
    owner["observability"] = None
    validate_dynamic_payload_structure(payload)
    normalize_runtime_config_payload(payload, observability_target_refs=frozenset())
    assert owner["observability"] is None
    assert routed_targets(payload) == ()
    assert target_settings(payload, target) == {}
    assert collection_enabled(payload, target)


@pytest.mark.parametrize("target", ["managed", "external"])
def test_routing_can_be_saved_over_null_observability(target):
    from nebius_cxcli.observability_routing import (
        collection_enabled,
        routed_targets,
        save_settings,
        target_settings,
    )

    payload = source()
    payload["apps"]["charts"] = []
    owner = require_cluster_row(payload, target)
    owner["observability"] = None
    validate_dynamic_payload_structure(payload)
    settings = {"signals": ["logs"]}
    save_settings(payload, target, settings, enabled=False)
    assert routed_targets(payload) == (target,)
    assert target_settings(payload, target) == settings
    assert not collection_enabled(payload, target)
