from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import yaml

from nebius_vpngw.cli import _vm_ha_apply_order
from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs


def _write_config(tmp_path, name: str, config: dict):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _legacy_trace(plan) -> tuple[object, ...]:
    instances = tuple(plan.iter_instance_configs())
    return (
        plan.vm_ha,
        tuple((item.instance_index, item.hostname) for item in instances),
        tuple("vm_ha" in yaml.safe_load(item.config_yaml) for item in instances),
    )


def test_omitted_vm_ha_keeps_the_single_node_golden_trace(
    tmp_path,
    sample_config: dict,
) -> None:
    loaded = load_local_config(_write_config(tmp_path, "omitted-vm-ha.yaml", sample_config))
    plan = merge_with_peer_configs(loaded, [])

    assert "vm_ha" not in loaded["gateway_group"]
    assert _legacy_trace(plan) == (
        None,
        ((0, "nebius-vpn-gw-0"),),
        (False,),
    )


def test_explicitly_disabled_vm_ha_resolves_to_the_omitted_legacy_plan(
    tmp_path,
    sample_config: dict,
) -> None:
    omitted = load_local_config(_write_config(tmp_path, "omitted.yaml", sample_config))
    disabled_config = deepcopy(sample_config)
    disabled_config["gateway_group"]["vm_ha"] = {"enabled": False}
    disabled = load_local_config(_write_config(tmp_path, "disabled.yaml", disabled_config))

    omitted_plan = merge_with_peer_configs(omitted, [])
    disabled_plan = merge_with_peer_configs(disabled, [])

    assert disabled["gateway_group"]["vm_ha"]["enabled"] is False
    assert "vm_ha" not in omitted["gateway_group"]
    assert disabled_plan.vm_ha is None
    assert [item.config_yaml for item in disabled_plan.iter_instance_configs()] == [
        item.config_yaml for item in omitted_plan.iter_instance_configs()
    ]
    assert _legacy_trace(disabled_plan) == _legacy_trace(omitted_plan)


def test_two_instances_and_public_ips_do_not_infer_vm_ha(
    tmp_path,
    sample_config: dict,
) -> None:
    config = deepcopy(sample_config)
    config["gateway_group"]["instance_count"] = 2
    config["gateway_group"]["external_ips"] = [
        ["203.0.113.10"],
        ["203.0.113.20"],
    ]
    loaded = load_local_config(_write_config(tmp_path, "two-node-ordinary.yaml", config))
    plan = merge_with_peer_configs(loaded, [])

    assert plan.vm_ha is None
    assert [item.instance_index for item in plan.iter_instance_configs()] == [0, 1]
    assert all(
        "vm_ha" not in yaml.safe_load(item.config_yaml) for item in plan.iter_instance_configs()
    )


def test_non_ha_apply_order_remains_the_declared_instance_order() -> None:
    first = SimpleNamespace(instance_index=0, vm_ha_node=None)
    second = SimpleNamespace(instance_index=1, vm_ha_node=None)
    plan = SimpleNamespace(vm_ha=None, iter_instance_configs=lambda: iter([first, second]))

    assert _vm_ha_apply_order(plan) == [first, second]
