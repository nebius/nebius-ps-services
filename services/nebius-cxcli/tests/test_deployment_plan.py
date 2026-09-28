from __future__ import annotations

import copy

import pytest

from nebius_cxcli.deployment_plan import (
    DeploymentAction,
    assert_protected_terraform_scope,
    assert_stage_plan,
    node_groups,
    plan_deployment,
)


def config():
    return {
        "client_info": {
            "nebius": {"project_id": "project", "tenant_id": "tenant", "region_id": "region"}
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"cluster_name": "cluster", "version": "1.32"},
                        "node_groups": {
                            "worker-a": {"node_count": 3, "os": "old"},
                            "worker-b": {"node_count": 2, "os": "old"},
                        },
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster",
                    "enabled": True,
                    "version": "1.22.3",
                    "values": {"settings": {"limit": 1}},
                }
            ]
        },
    }


def test_fresh_requires_confirmed_absence_and_existing_requires_ownership():
    desired = config()
    assert (
        plan_deployment(
            desired, accepted=None, live_release=None, infrastructure_absent=True
        ).action
        is DeploymentAction.INSTALL
    )
    assert (
        plan_deployment(
            desired, accepted=None, live_release=None, infrastructure_absent=False
        ).action
        is DeploymentAction.INSTALL
    )
    with pytest.raises(ValueError, match="absent or not observable"):
        plan_deployment(desired, accepted=desired, live_release=None, infrastructure_absent=False)


def test_same_release_full_values_change_is_reconciled_and_ordinary_only_is_not_maintenance():
    accepted = config()
    desired = copy.deepcopy(accepted)
    assert (
        plan_deployment(
            desired, accepted=accepted, live_release="1.22.3", infrastructure_absent=False
        ).action
        is DeploymentAction.NOOP
    )
    desired["apps"]["charts"].append({"id": "ordinary", "enabled": True, "values": {"size": 2}})
    assert (
        plan_deployment(
            desired, accepted=accepted, live_release="1.22.3", infrastructure_absent=False
        ).action
        is DeploymentAction.NOOP
    )
    desired["apps"]["charts"][0]["values"]["settings"]["limit"] = 2
    plan = plan_deployment(
        desired, accepted=accepted, live_release="1.22.3", infrastructure_absent=False
    )
    assert plan.action is DeploymentAction.RECONCILE
    assert "soperator.values.settings.limit" in plan.changed_fields


def test_combined_change_retires_old_platform_then_upgrades_survivors_then_adds_target_groups():
    accepted = config()
    desired = copy.deepcopy(accepted)
    desired["apps"]["charts"][0]["version"] = "1.23.0"
    groups = desired["infra"]["components"][0]["inputs"]["node_groups"]
    groups["worker-a"] = {"node_count": 1, "os": "new"}
    del groups["worker-b"]
    groups["worker-c"] = {"node_count": 4, "os": "new"}
    plan = plan_deployment(
        desired, accepted=accepted, live_release="1.22.3", infrastructure_absent=False
    )
    assert [stage.name for stage in plan.stages] == ["retire", "transition", "grow", "reconcile"]
    assert node_groups(plan.stages[0].config, "cluster") == {
        "worker-a": {"node_count": 1, "os": "old"}
    }
    assert node_groups(plan.stages[1].config, "cluster") == {
        "worker-a": {"node_count": 1, "os": "new"}
    }
    assert node_groups(plan.stages[2].config, "cluster") == groups
    assert accepted == config()


def change(
    address="module.cluster.node_group.worker",
    *,
    actions=None,
    after=None,
    resource_type="nebius_mk8s_v1_node_group",
):
    return {
        "address": address,
        "type": resource_type,
        "mode": "managed",
        "change": {
            "actions": actions or ["update"],
            "before": {"count": 3},
            "after": after or {"count": 1},
            "after_unknown": {},
        },
    }


def test_refresh_can_complete_subset_but_cannot_add_deletion_or_expand_desired_state():
    admitted = {"resource_changes": [change()]}
    assert_stage_plan(admitted, {"resource_changes": []})
    assert_stage_plan(admitted, admitted)
    for unexpected in [
        change("module.other.resource"),
        change(actions=["delete"]),
        change(after={"count": 0}),
    ]:
        with pytest.raises(ValueError, match="unadmitted|admitted"):
            assert_stage_plan(admitted, {"resource_changes": [unexpected]})


@pytest.mark.parametrize(
    "kind", ["nebius_compute_v1_filesystem", "nebius_compute_v1_disk", "nebius_mk8s_v1_cluster"]
)
def test_protected_resources_cannot_be_replaced(kind):
    with pytest.raises(ValueError, match="protected storage or cluster"):
        assert_protected_terraform_scope(
            {"resource_changes": [change(actions=["delete", "create"], resource_type=kind)]},
            target_ref="cluster",
        )


def test_same_desired_config_with_live_owned_infra_drift_enters_maintenance():
    from nebius_cxcli.deployment_plan import plan_with_observed_drift

    desired = config()
    noop = plan_deployment(
        desired, accepted=desired, live_release="1.22.3", infrastructure_absent=False
    )
    repaired = plan_with_observed_drift(
        noop, {"resource_changes": [change()]}, owned_modules={"cluster"}
    )
    assert repaired.action is DeploymentAction.RECONCILE
    assert [stage.name.value for stage in repaired.stages] == ["transition", "reconcile"]
    assert (
        plan_with_observed_drift(
            noop,
            {"resource_changes": [change("module.other.node_group.worker")]},
            owned_modules={"cluster"},
        )
        == noop
    )
