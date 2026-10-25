from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_deploy as deployment
from nebius_cxcli import nat_gateway_quota
from nebius_cxcli.runtime_validation import validate_dynamic_payload_structure
from test_nat_gateway import config, manager_fixture


def prepared_manager(monkeypatch, *, owner="external", current="table-original"):
    manager, sdk, record, target, live, effects = manager_fixture(
        monkeypatch, owner=owner, current=current, enabled=False
    )
    intent = copy.deepcopy(manager.rows[0])
    intent["routing"].pop("enabled")
    record["intent"] = deployment.fingerprint(intent)
    record["module_name"] = "nat"
    manager.executor.manifest = {
        "render": {
            "module_sources": [
                {"component_id": "nat-gateway", "instance_id": "nat", "module_name": "nat"},
                {"component_id": "vpc", "instance_id": "network", "module_name": "network_custom"},
            ]
        }
    }
    manager.executor.paths = NS(infra_dir="fixture")
    manager.executor.cli = NS(
        terraform_show_json=lambda *a, **kw: {}, _terraform_state_resources=lambda raw: []
    )
    monkeypatch.setattr(
        type(sdk), "cpu_preset", lambda *a: {"gpu_count": 0, "vcpu_count": 4}, raising=False
    )
    monkeypatch.setattr(
        type(sdk), "image", lambda *a, **kw: NS(status=NS(min_disk_size_bytes=1)), raising=False
    )
    monkeypatch.setattr(type(sdk), "validate_subnet_cidr", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(nat_gateway_quota, "check_plan_quotas", lambda *a: None)
    return manager, sdk, record, target, live, effects


@pytest.mark.parametrize("check", ["cpu_preset", "image", "validate_subnet_cidr"])
def test_disabled_new_gateway_still_validates_provisioning(monkeypatch, check):
    manager, sdk, _record, _target, _live, _effects = prepared_manager(monkeypatch)
    manager.state.value["gateways"] = {}

    def reject(*a, **kw):
        raise ValueError("invalid provisioning input")

    monkeypatch.setattr(type(sdk), check, reject)
    with pytest.raises(ValueError, match="invalid provisioning"):
        manager.prepare()


def test_disabled_partial_retry_validates_planned_provisioning(monkeypatch):
    manager, sdk, _record, _target, _live, _effects = prepared_manager(monkeypatch)
    manager.prepare()  # Restoration itself must not require provisioning availability.

    def reject(*a):
        raise ValueError("preset became unavailable")

    monkeypatch.setattr(type(sdk), "cpu_preset", reject)
    plan = {
        "resource_changes": [
            {
                "address": "module.nat.module.vm.nebius_compute_v1_instance.this",
                "type": "nebius_compute_v1_instance",
                "change": {"actions": ["create"], "after": {}},
            }
        ]
    }
    with pytest.raises(ValueError, match="preset became unavailable"):
        manager.check_plan(plan)


@pytest.mark.parametrize("root_outputs", [True, False])
def test_partial_apply_recovers_identities_before_attachment_admission(monkeypatch, root_outputs):
    manager, _sdk, record, target, _live, _effects = prepared_manager(
        monkeypatch, owner="terraform", current="table-third-party"
    )
    record["network_id"] = target["subnet_id"] = ""
    row = manager.rows[0]
    row["inputs"].pop("network_id")
    row["bindings"] = {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "network",
            "source_output": "network_id",
        }
    }
    intent = copy.deepcopy(row)
    intent["routing"].pop("enabled")
    record["intent"] = deployment.fingerprint(intent)
    outputs = manager.outputs()
    outputs["network_network_id"] = {"value": "network-test"}
    manager.outputs = lambda: outputs if root_outputs else {}
    resources = [
        {
            "address": 'module.network_custom.nebius_vpc_v1_subnet.this["work"]',
            "index": "work",
            "mode": "managed",
            "type": "nebius_vpc_v1_subnet",
            "values": {
                "id": "subnet-work",
                "network_id": "network-test",
                "parent_id": "project-test",
            },
        }
    ]
    manager.executor.cli._terraform_state_resources = lambda raw: resources
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        manager.prepare()
        manager.assert_attachments()


@pytest.mark.parametrize("module_name", ["module_1nat", "nat_2"])
def test_retirement_uses_recorded_rendered_module_identity(monkeypatch, module_name):
    manager, _sdk, record, _target, _live, _effects = prepared_manager(monkeypatch)
    record["phase"] = "detached"
    record["module_name"] = module_name
    manager.rows = []
    manager.check_plan(
        {
            "resource_changes": [
                {
                    "address": f"module.{module_name}.nebius_vpc_v1_route_table.gateway",
                    "change": {
                        "actions": ["delete"],
                        "before": {"labels": {"cxcli-component": "nat-gateway"}},
                    },
                }
            ]
        }
    )


def test_noop_retirement_recovery_loads_journal_with_other_infra(monkeypatch):
    manager, _sdk, record, _target, _live, _effects = prepared_manager(
        monkeypatch, owner="terraform"
    )
    record["phase"] = "detached"
    state = manager.state
    manager.state = None
    manager.executor.config["infra"]["components"].pop(0)

    def open_state():
        manager.state = state
        return state

    manager._open = open_state
    with manager:
        manager.prepare()
        manager.check_plan({"resource_changes": []})
        manager.complete()
    assert record["phase"] == "retired"


def test_new_journal_records_manifest_module_identity(monkeypatch):
    manager, _sdk, _record, _target, _live, _effects = prepared_manager(monkeypatch)
    manager.state.value["gateways"] = {}
    manager.executor.manifest["render"]["module_sources"][0]["module_name"] = "nat_2"
    manager.prepare()
    assert manager.state.value["gateways"]["nat"]["module_name"] == "nat_2"


def test_external_delegation_rejects_owned_subnet_without_root_outputs(monkeypatch):
    manager, _sdk, _record, _target, _live, _effects = prepared_manager(monkeypatch)
    manager.state.value["gateways"] = {}
    manager.executor.config["infra"]["components"].append(
        config(owner="terraform")["infra"]["components"][1]
    )
    manager.outputs = lambda: {}
    manager.executor.cli._terraform_state_resources = lambda raw: [
        {
            "address": 'module.network_custom.nebius_vpc_v1_subnet.this["work"]',
            "mode": "managed",
            "type": "nebius_vpc_v1_subnet",
            "index": "work",
            "values": {"id": "subnet-work", "network_id": "network-test"},
        }
    ]
    with pytest.raises(ValueError, match="Terraform-owned"):
        manager.prepare()


@pytest.mark.parametrize("kind", ["network", "subnet"])
def test_stale_outputs_cannot_override_current_resource_identity(monkeypatch, kind):
    manager, _sdk, record, _target, _live, _effects = prepared_manager(
        monkeypatch, owner="terraform"
    )
    row = manager.rows[0]
    row["inputs"].pop("network_id")
    row["bindings"] = {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "network",
            "source_output": "network_id",
        }
    }
    intent = copy.deepcopy(row)
    intent["routing"].pop("enabled")
    record["intent"] = deployment.fingerprint(intent)
    outputs = manager.outputs()
    outputs["network_network_id"] = {"value": "network-test"}
    manager.outputs = lambda: outputs
    resource = {
        "address": 'module.network_custom.nebius_vpc_v1_subnet.this["work"]',
        "index": "work",
        "mode": "managed",
        "type": "nebius_vpc_v1_subnet",
        "values": {
            "id": "subnet-replacement" if kind == "subnet" else "subnet-work",
            "network_id": "network-replacement" if kind == "network" else "network-test",
        },
    }
    manager.executor.cli._terraform_state_resources = lambda raw: [resource]
    with pytest.raises(RuntimeError, match="identity.*(ambiguous|disagree)"):
        manager.prepare()


@pytest.mark.parametrize(
    "component",
    [
        None,
        "invalid",
        7,
        {"id": "nat-gateway", "instance_id": "nat", "enabled": True, "inputs": None},
    ],
)
def test_structural_validation_keeps_indexed_errors(component):
    payload = {"infra": {"components": [component]}, "apps": {"charts": []}}
    with pytest.raises(ValueError, match=r"infra.components\[0\].*mapping"):
        validate_dynamic_payload_structure(payload)
