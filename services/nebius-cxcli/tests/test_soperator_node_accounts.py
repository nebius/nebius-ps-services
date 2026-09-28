from __future__ import annotations

import copy

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.components import component_entries, soperator_install_entry
from nebius_cxcli.soperator_config_materialization import (
    _materialize_soperator_component_defaults,
)
from test_render import _starter_payload


def _new_payload():
    return _starter_payload(selected_infra={"mk8s", "sfs"}, selected_apps={"soperator"})


def _creation_payload():
    return cli._starter_component_payload(
        client_name="example",
        tenant_id="tenant-example",
        project_id="project-example",
        region_id="eu-north1",
        email=None,
        selected_infra={"mk8s", "sfs"},
        selected_apps={"soperator"},
        infra_entries=component_entries("infra"),
        app_entries=(
            soperator_install_entry("4.1.7", chart_repo="oci://example.invalid/soperator"),
        ),
    )


def _inputs(payload):
    return next(row["inputs"] for row in payload["infra"]["components"] if row["id"] == "mk8s")


def _accounts(payload):
    return {
        key: copy.deepcopy(group.get("service_account"))
        for key, group in _inputs(payload)["node_groups"].items()
    }


def test_wizard_defers_accounts_until_cluster_name_is_selected():
    payload = _new_payload()
    assert not _inputs(payload)["cluster"].get("cluster_name")
    assert all(account is None for account in _accounts(payload).values())


def test_real_creation_defers_accounts_until_cluster_name_is_selected():
    payload = _creation_payload()
    assert not _inputs(payload)["cluster"].get("cluster_name")
    assert all(account is None for account in _accounts(payload).values())


def test_real_creation_uses_final_cluster_name_for_accounts():
    allocated = []
    for name in ("cluster-a", "cluster-b", "mk8s"):
        payload = _creation_payload()
        _inputs(payload)["cluster"]["cluster_name"] = name
        _materialize_soperator_component_defaults(payload)
        names = {account["name"] for account in _accounts(payload).values()}
        assert all(account.startswith(f"sop-{name}-") for account in names)
        assert all(names.isdisjoint(prior) for prior in allocated)
        allocated.append(names)


@pytest.mark.parametrize(
    "names",
    [
        ("cluster-a", "cluster-b"),
        ("same-long-cluster-prefix-a", "same-long-cluster-prefix-b"),
        ("cluster.a", "cluster-a"),
    ],
)
def test_separate_clusters_with_default_component_id_get_distinct_accounts(names):
    accounts = []
    for name in names:
        payload = _new_payload()
        _inputs(payload)["cluster"]["cluster_name"] = name
        _materialize_soperator_component_defaults(payload)
        accounts.append({account["name"] for account in _accounts(payload).values()})
    assert len(accounts[0]) == len(_accounts(payload))
    assert accounts[0].isdisjoint(accounts[1])


def test_accounts_are_stable_across_independent_loads_and_saved_round_trips():
    raw = _new_payload()
    _inputs(raw)["cluster"]["cluster_name"] = "cluster-a"
    first, second = copy.deepcopy(raw), copy.deepcopy(raw)
    _materialize_soperator_component_defaults(first)
    _materialize_soperator_component_defaults(second)
    assert _accounts(first) == _accounts(second)
    restored = yaml.safe_load(yaml.safe_dump(first))
    _materialize_soperator_component_defaults(restored)
    assert _accounts(restored) == _accounts(first)


@pytest.mark.parametrize(
    "account", [{"name": "operator-selected"}, {"id": "serviceaccount-selected"}, {}]
)
def test_worker_resize_preserves_saved_account_mapping(account):
    payload = _new_payload()
    inputs = _inputs(payload)
    inputs["cluster"]["cluster_name"] = "cluster-a"
    _materialize_soperator_component_defaults(payload)
    inputs["node_groups"]["worker"]["service_account"] = copy.deepcopy(account)
    inputs["soperator"]["worker_node_groups"]["worker"]["autoscaling"] = {
        "enabled": True,
        "min_node_count": 0,
        "max_node_count": 1,
    }
    _materialize_soperator_component_defaults(payload)
    assert inputs["node_groups"]["worker"]["service_account"] == account


def test_saved_names_survive_cluster_name_edit():
    payload = _new_payload()
    _inputs(payload)["cluster"]["cluster_name"] = "cluster-a"
    _materialize_soperator_component_defaults(payload)
    before = _accounts(payload)
    _inputs(payload)["cluster"]["cluster_name"] = "cluster-b"
    _materialize_soperator_component_defaults(payload)
    assert _accounts(payload) == before


def test_worker_shard_growth_and_shrink_retain_surviving_accounts():
    payload = _new_payload()
    inputs = _inputs(payload)
    inputs["cluster"]["cluster_name"] = "cluster-a"
    inputs["soperator"]["worker_gpu_total_nodes"] = 2
    inputs["soperator"]["worker_gpu_nodes_per_group"] = 1
    _materialize_soperator_component_defaults(payload)
    inputs["node_groups"]["worker-0"]["service_account"] = {"id": "serviceaccount-selected"}
    before = _accounts(payload)

    inputs["soperator"]["worker_gpu_total_nodes"] = 3
    _materialize_soperator_component_defaults(payload)
    grown = _accounts(payload)
    assert {key: grown[key] for key in before} == before
    assert grown["worker-2"]["name"].startswith("sop-cluster-a-worker-2-")
    assert grown["worker-2"] not in before.values()

    inputs["soperator"]["worker_gpu_total_nodes"] = 2
    _materialize_soperator_component_defaults(payload)
    assert _accounts(payload) == before
