from __future__ import annotations

import copy

import pytest

from nebius_cxcli.destroy_target import (
    check_retired_target_references,
    require_non_mk8s_destroy,
    resolve_destroy_target,
)


def binding(*, external=False):
    target = {
        "component_id": "mk8s",
        "instance_id": "training",
        "target_ref": "training",
        "ownership": "managed",
        "cluster_id_output_name": "training_cluster_id",
    }
    source = {"infra": {"components": [{"id": "mk8s", "instance_id": "training", "enabled": True}]}}
    state = {
        "root_module": {
            "resources": [
                {
                    "address": "module.training.nebius_mk8s_v1_cluster.this",
                    "type": "nebius_mk8s_v1_cluster",
                    "mode": "managed",
                    "values": {"id": "mk8scluster-one"},
                }
            ]
        },
        "outputs": {"training_cluster_id": {"value": "mk8scluster-one"}},
    }
    modules = [{"component_id": "mk8s", "instance_id": "training", "module_name": "training"}]
    if external:
        target.update(
            component_id="external-mk8s",
            kind="external-mk8s",
            ownership="external",
            cluster_id="mk8scluster-one",
        )
        source = {
            "deploy": {
                "targets": [
                    {
                        "kind": "external-mk8s",
                        "instance_id": "training",
                        "cluster_id": "mk8scluster-one",
                    }
                ]
            }
        }
        state, modules = {}, []
    return dict(
        cluster_id="mk8scluster-one",
        source=source,
        targets=[target],
        module_sources=modules,
        state_values=state,
    )


@pytest.mark.parametrize("external", [False, True])
def test_identity_is_bound_without_soperator_registration(external):
    selected = resolve_destroy_target(**binding(external=external))
    assert selected.cluster_id == "mk8scluster-one"
    assert selected.target_ref == "training"
    assert selected.ownership == ("onboarded" if external else "managed")


@pytest.mark.parametrize("value", ["training", "other-cloud-id", " mk8scluster-one", ""])
def test_alias_or_arbitrary_cloud_id_cannot_select_a_cluster(value):
    args = binding()
    args["cluster_id"] = value
    with pytest.raises(ValueError, match="immutable"):
        resolve_destroy_target(**args)


@pytest.mark.parametrize("external", [False, True])
def test_conflicting_source_state_or_output_id_is_rejected(external):
    args = binding(external=external)
    if external:
        args["source"]["deploy"]["targets"][0]["cluster_id"] = "other-cluster"
    else:
        args["state_values"]["outputs"]["training_cluster_id"]["value"] = "other-cluster"
    with pytest.raises(ValueError, match="conflicting"):
        resolve_destroy_target(**args)


def test_context_only_onboarding_requires_explicit_registration():
    args = binding(external=True)
    del args["source"]["deploy"]["targets"][0]["cluster_id"]
    args["source"]["deploy"]["targets"][0]["kube_context"] = "mk8scluster-one"
    with pytest.raises(ValueError, match="explicit cluster_id"):
        resolve_destroy_target(**args)


def test_duplicate_cloud_id_does_not_select_first_match():
    args = binding(external=True)
    duplicate = copy.deepcopy(args["targets"][0])
    duplicate.update(instance_id="other", target_ref="other")
    args["targets"].append(duplicate)
    args["source"]["deploy"]["targets"].append(
        {"instance_id": "other", "kind": "external-mk8s", "cluster_id": "mk8scluster-one"}
    )
    with pytest.raises(ValueError, match="duplicate immutable"):
        resolve_destroy_target(**args)


def test_stale_managed_module_mapping_cannot_authorize_deletion():
    args = binding()
    args["module_sources"][0]["module_name"] = "other"
    with pytest.raises(ValueError, match="verified project-bound"):
        resolve_destroy_target(**args)


@pytest.mark.parametrize("location", ["source", "manifest", "state"])
def test_generic_teardown_rejects_mk8s_in_every_authority(location):
    args = binding()
    with pytest.raises(ValueError, match="--target CLUSTER_ID"):
        require_non_mk8s_destroy(
            args["source"] if location == "source" else {},
            {"deploy": {"targets": args["targets"]}} if location == "manifest" else {},
            args["state_values"] if location == "state" else {},
        )


def test_unrelated_project_and_output_references():
    require_non_mk8s_destroy({"infra": {"components": [{"id": "vm", "enabled": True}]}})
    check_retired_target_references({"binding": "other.cluster_id"}, "training")
    with pytest.raises(RuntimeError, match="retired MK8s"):
        check_retired_target_references(
            {"nested": [{"output_ref": "training.cluster_id"}]}, "training"
        )


@pytest.mark.parametrize("ownership", ["managed", "onboarded"])
def test_cleanup_removes_only_selected_target_and_apps_without_soperator(tmp_path, ownership):
    import yaml

    from nebius_cxcli.config_loader import validate_config
    from nebius_cxcli.config_template import starter_config_yaml
    from nebius_cxcli.destroy_cli import cleanup_payload

    source = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email="ops@example.com",
        )
    )
    retained = {"id": "object-storage", "instance_id": "archive", "enabled": False, "inputs": {}}
    source["infra"]["components"] = [retained]
    if ownership == "managed":
        source["infra"]["components"].append(
            {
                "id": "mk8s",
                "instance_id": "retired",
                "enabled": False,
                "inputs": {},
            }
        )
    source["deploy"] = {
        "targets": [
            {"instance_id": ref, "kind": "external-mk8s", "cluster_id": f"mk8scluster-{ref}"}
            for ref in ("retired", "retained")
        ]
    }
    source["apps"]["charts"] = [
        {"id": "gateway-helm", "instance_id": ref, "enabled": False, "values": {}}
        for ref in ("retired", "retained")
    ]
    original = copy.deepcopy(source)
    cleaned = cleanup_payload(
        source_payload=source, target_ref="retired", ownership=ownership, base_dir=tmp_path
    )
    validate_config(copy.deepcopy(cleaned), base_dir=tmp_path)
    assert source == original
    assert [row["instance_id"] for row in cleaned["infra"]["components"]] == ["archive"]
    assert [row["instance_id"] for row in cleaned["deploy"]["targets"]] == ["retained"]
    assert [row["instance_id"] for row in cleaned["apps"]["charts"]] == ["retained"]


@pytest.mark.parametrize(
    "conflict", ["source-component", "generated-module", "external-registration"]
)
def test_mixed_managed_and_onboarded_ownership_is_rejected(conflict):
    args = binding(external=conflict != "external-registration")
    if conflict == "source-component":
        args["source"]["infra"] = {
            "components": [{"id": "mk8s", "instance_id": "training", "enabled": False}]
        }
    elif conflict == "generated-module":
        args["module_sources"] = [
            {"component_id": "mk8s", "instance_id": "training", "module_name": "training"}
        ]
    else:
        args["source"]["deploy"] = {
            "targets": [
                {
                    "instance_id": "training",
                    "kind": "external-mk8s",
                    "cluster_id": "mk8scluster-one",
                }
            ]
        }
    with pytest.raises(ValueError, match="ownership"):
        resolve_destroy_target(**args)
