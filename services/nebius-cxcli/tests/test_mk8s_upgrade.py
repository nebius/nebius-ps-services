from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_cxcli import mk8s_upgrade as upgrade


def _cluster(*, version: str = "1.32") -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(id="cluster-1", name="mk8s", resource_version=1),
        spec=SimpleNamespace(control_plane=SimpleNamespace(version=version)),
    )


def _node_group(
    *,
    id: str,
    name: str,
    version: str = "1.32",
    platform: str = "cpu-platform",
    preset: str = "cpu-4-16",
    os: str = "ubuntu24.04",
    drivers_preset: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(id=id, name=name, resource_version=1),
        spec=SimpleNamespace(
            version=version,
            template=SimpleNamespace(
                os=os,
                resources=SimpleNamespace(platform=platform, preset=preset),
                gpu_settings=SimpleNamespace(drivers_preset=drivers_preset),
            ),
            strategy=SimpleNamespace(name="original"),
        ),
    )


def _ready_status() -> SimpleNamespace:
    return SimpleNamespace(
        ready_node_count=1,
        target_node_count=1,
        node_count=1,
        outdated_node_count=0,
        reconciling=False,
    )


def _source_node_group(
    *,
    key: str,
    name: str,
    gpu: bool = False,
    platform: str = "cpu-platform",
    preset: str = "cpu-4-16",
    os: str = "ubuntu24.04",
    gpu_stack_source: str = "operator_managed",
    gpu_stack_preset: str = "",
    node_count: int | None = 2,
) -> upgrade.Mk8sNodeGroup:
    return upgrade.Mk8sNodeGroup(
        key=key,
        name=name,
        gpu=gpu,
        platform=platform,
        preset=preset,
        os=os,
        node_count=node_count,
        autoscaling_min_node_count=None,
        autoscaling_max_node_count=None,
        gpu_stack_source=gpu_stack_source,
        gpu_stack_preset=gpu_stack_preset,
        gpu_cluster_key="",
        gpu_cluster_id="",
        reservation_policy="none",
        reservation_ids=(),
        node_labels={},
        labels={},
    )


def test_parse_upgrade_selector_requires_canonical_mk8s_infra_target() -> None:
    parsed = upgrade.parse_upgrade_selector("infra:mk8s@prod")

    assert parsed.instance_id == "prod"
    assert parsed.selector == "infra:mk8s@prod"

    with pytest.raises(ValueError, match="MK8s upgrades require"):
        upgrade.parse_upgrade_selector("apps:soperator@prod")
    with pytest.raises(ValueError, match="Missing MK8s target instance id"):
        upgrade.parse_upgrade_selector("infra:mk8s@")


@pytest.mark.parametrize(
    ("policy", "expected_seconds", "expected_label"),
    [
        ("safe-surge", 1800, "30m"),
        ("zero-surge", 1800, "30m"),
        ("force-delete", 600, "10m"),
    ],
)
def test_resolve_drain_timeout_auto_by_upgrade_strategy(
    policy: str,
    expected_seconds: int | None,
    expected_label: str,
) -> None:
    timeout = upgrade.resolve_drain_timeout(policy, "auto")

    assert timeout.seconds == expected_seconds
    assert timeout.label == expected_label


def test_resolve_drain_timeout_accepts_go_style_duration_and_none() -> None:
    assert upgrade.resolve_drain_timeout("force-delete", "1h30m").seconds == 5400
    assert upgrade.resolve_drain_timeout("zero-surge", "45m").seconds == 2700
    assert upgrade.resolve_drain_timeout("safe-surge", "none").seconds is None
    with pytest.raises(ValueError, match="Go-style duration"):
        upgrade.resolve_drain_timeout("safe-surge", "10minutes")


def test_require_single_minor_hop_rejects_downgrade_and_skipped_minor() -> None:
    assert upgrade.require_single_minor_hop("1.32", "1.33") == (
        upgrade.UpgradeHop(from_version="1.32", to_version="1.33"),
    )
    assert upgrade.require_single_minor_hop("1.33", "1.33") == ()
    with pytest.raises(ValueError, match="Downgrades are not supported"):
        upgrade.require_single_minor_hop("1.33", "1.32")
    with pytest.raises(ValueError, match="Upstream Kubernetes does not support skipped minors"):
        upgrade.require_single_minor_hop("1.31", "1.33")


def test_update_source_k8s_versions_updates_cluster_and_all_node_group_versions() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"k8s_version": "1.32"},
                        "node_groups": {
                            "system": {"version": "1.32"},
                            "gpu": {"gpu": True},
                        },
                    },
                }
            ]
        }
    }

    changed = upgrade.update_source_k8s_versions(
        payload,
        instance_id="prod",
        target_version="1.33",
    )

    component = payload["infra"]["components"][0]
    assert changed is True
    assert component["inputs"]["cluster"]["k8s_version"] == "1.33"
    assert component["inputs"]["node_groups"]["system"]["version"] == "1.33"
    assert component["inputs"]["node_groups"]["gpu"]["version"] == "1.33"


def test_update_source_k8s_versions_can_pin_node_groups_for_control_plane_stage() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"k8s_version": "1.32"},
                        "node_groups": {
                            "system": {},
                            "gpu": {"name": "gpu-workers"},
                        },
                    },
                }
            ]
        }
    }

    changed = upgrade.update_source_k8s_versions(
        payload,
        instance_id="prod",
        target_version="1.33",
        node_group_versions={"system": "1.32", "gpu-workers": "1.32"},
    )

    component = payload["infra"]["components"][0]
    assert changed is True
    assert component["inputs"]["cluster"]["k8s_version"] == "1.33"
    assert component["inputs"]["node_groups"]["system"]["version"] == "1.32"
    assert component["inputs"]["node_groups"]["gpu"]["version"] == "1.32"


def test_update_source_node_group_os_updates_selected_enabled_groups() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"k8s_version": "1.33"},
                        "node_groups": {
                            "system": {
                                "os": "ubuntu22.04",
                                "platform": "cpu-platform",
                                "strategy": {"max_surge": {"count": 1}},
                            },
                            "gpu": {
                                "gpu": True,
                                "os": "ubuntu22.04",
                                "gpu_stack_preset": "cuda13.0",
                            },
                            "disabled": {
                                "enabled": False,
                                "os": "ubuntu22.04",
                            },
                        },
                    },
                }
            ]
        }
    }

    changed = upgrade.update_source_node_group_os(
        payload,
        instance_id="prod",
        target_os="ubuntu24.04",
        node_group_keys=("gpu",),
    )

    groups = payload["infra"]["components"][0]["inputs"]["node_groups"]
    assert changed is True
    assert groups["system"]["os"] == "ubuntu22.04"
    assert groups["system"]["platform"] == "cpu-platform"
    assert groups["system"]["strategy"] == {"max_surge": {"count": 1}}
    assert groups["gpu"]["os"] == "ubuntu24.04"
    assert groups["gpu"]["gpu_stack_preset"] == "cuda13.0"
    assert groups["disabled"]["os"] == "ubuntu22.04"


def test_update_source_node_group_os_rejects_unknown_source_key() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {"node_groups": {"system": {}}},
                }
            ]
        }
    }

    with pytest.raises(ValueError, match="missing"):
        upgrade.update_source_node_group_os(
            payload,
            instance_id="prod",
            target_os="ubuntu24.04",
            node_group_keys=("missing",),
        )


def test_verify_mk8s_upgrade_plan_ready_confirms_live_k8s_os_and_gpu_stack() -> None:
    live_group = _node_group(
        id="ng-gpu",
        name="gpu-workers",
        version="1.33",
        platform="gpu-platform",
        preset="8gpu",
        os="ubuntu24.04",
        drivers_preset="cuda13.0",
    )
    live_group.status = _ready_status()
    planned_group = upgrade.live_node_group_from_sdk(
        live_group,
        source=_source_node_group(
            key="gpu",
            name="gpu-workers",
            gpu=True,
            platform="gpu-platform",
            preset="8gpu",
            os="ubuntu22.04",
            gpu_stack_source="nebius_image",
            gpu_stack_preset="cuda12.8",
        ),
    )
    plan = upgrade.Mk8sNodeTemplateUpgradePlan(
        target=upgrade.UpgradeTarget("infra:mk8s@prod", "prod"),
        cluster_id="cluster-1",
        cluster_name="mk8s-live",
        current_version="1.32",
        target_version="1.33",
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        hops=(upgrade.UpgradeHop("1.32", "1.33"),),
        disruption_policy=upgrade.DISRUPTION_POLICY_SAFE,
        drain_timeout=upgrade.resolve_drain_timeout(upgrade.DISRUPTION_POLICY_SAFE, "auto"),
        all_node_groups=(planned_group,),
        node_groups=(planned_group,),
        compatibility_failures=(),
    )

    class FakeExecutor:
        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return _cluster(version="1.33")

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (live_group,)

    result = upgrade.verify_mk8s_upgrade_plan_ready(
        executor=FakeExecutor(),
        plan=plan,
        label="MK8s node-template upgrade",
    )

    assert result.ready_node_group_count == 1
    assert "Kubernetes 1.33" in result.summary()


def test_verify_mk8s_upgrade_plan_ready_fails_when_live_os_does_not_match() -> None:
    planned_raw = _node_group(
        id="ng-system",
        name="system",
        version="1.33",
        os="ubuntu22.04",
    )
    planned_raw.status = _ready_status()
    planned_group = upgrade.live_node_group_from_sdk(
        planned_raw,
        source=_source_node_group(key="system", name="system", os="ubuntu22.04"),
    )
    live_raw = _node_group(
        id="ng-system",
        name="system",
        version="1.33",
        os="ubuntu22.04",
    )
    live_raw.status = _ready_status()
    plan = upgrade.Mk8sOsImageUpgradePlan(
        target=upgrade.UpgradeTarget("infra:mk8s@prod", "prod"),
        cluster_id="cluster-1",
        cluster_name="mk8s-live",
        k8s_version="1.33",
        target_os="ubuntu24.04",
        disruption_policy=upgrade.DISRUPTION_POLICY_SAFE,
        drain_timeout=upgrade.resolve_drain_timeout(upgrade.DISRUPTION_POLICY_SAFE, "auto"),
        node_groups=(planned_group,),
        compatibility_failures=(),
    )

    class FakeExecutor:
        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return _cluster(version="1.33")

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (live_raw,)

    with pytest.raises(RuntimeError, match="system: os=ubuntu22.04"):
        upgrade.verify_mk8s_upgrade_plan_ready(
            executor=FakeExecutor(),
            plan=plan,
            label="MK8s OS image upgrade",
        )


def test_verify_mk8s_upgrade_plan_ready_fails_when_status_is_missing() -> None:
    planned_raw = _node_group(
        id="ng-system",
        name="system",
        version="1.33",
        os="ubuntu24.04",
    )
    planned_raw.status = _ready_status()
    planned_group = upgrade.live_node_group_from_sdk(
        planned_raw,
        source=_source_node_group(key="system", name="system", os="ubuntu24.04"),
    )
    live_raw = _node_group(
        id="ng-system",
        name="system",
        version="1.33",
        os="ubuntu24.04",
    )
    plan = upgrade.Mk8sOsImageUpgradePlan(
        target=upgrade.UpgradeTarget("infra:mk8s@prod", "prod"),
        cluster_id="cluster-1",
        cluster_name="mk8s-live",
        k8s_version="1.33",
        target_os="ubuntu24.04",
        disruption_policy=upgrade.DISRUPTION_POLICY_SAFE,
        drain_timeout=upgrade.resolve_drain_timeout(upgrade.DISRUPTION_POLICY_SAFE, "auto"),
        node_groups=(planned_group,),
        compatibility_failures=(),
    )

    class FakeExecutor:
        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return _cluster(version="1.33")

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (live_raw,)

    with pytest.raises(RuntimeError, match="status not returned by Nebius SDK"):
        upgrade.verify_mk8s_upgrade_plan_ready(
            executor=FakeExecutor(),
            plan=plan,
            label="MK8s OS image upgrade",
        )


def test_verify_mk8s_upgrade_plan_ready_fails_when_status_counts_are_missing() -> None:
    planned_raw = _node_group(
        id="ng-system",
        name="system",
        version="1.33",
        os="ubuntu24.04",
    )
    planned_raw.status = _ready_status()
    planned_group = upgrade.live_node_group_from_sdk(
        planned_raw,
        source=_source_node_group(key="system", name="system", os="ubuntu24.04"),
    )
    live_raw = _node_group(
        id="ng-system",
        name="system",
        version="1.33",
        os="ubuntu24.04",
    )
    live_raw.status = SimpleNamespace(version="1.33")
    plan = upgrade.Mk8sOsImageUpgradePlan(
        target=upgrade.UpgradeTarget("infra:mk8s@prod", "prod"),
        cluster_id="cluster-1",
        cluster_name="mk8s-live",
        k8s_version="1.33",
        target_os="ubuntu24.04",
        disruption_policy=upgrade.DISRUPTION_POLICY_SAFE,
        drain_timeout=upgrade.resolve_drain_timeout(upgrade.DISRUPTION_POLICY_SAFE, "auto"),
        node_groups=(planned_group,),
        compatibility_failures=(),
    )

    class FakeExecutor:
        def get_cluster(self, cluster_id: str) -> SimpleNamespace:
            assert cluster_id == "cluster-1"
            return _cluster(version="1.33")

        def list_node_groups(self, cluster_id: str) -> tuple[SimpleNamespace, ...]:
            assert cluster_id == "cluster-1"
            return (live_raw,)

    with pytest.raises(RuntimeError, match="status missing ready_node_count"):
        upgrade.verify_mk8s_upgrade_plan_ready(
            executor=FakeExecutor(),
            plan=plan,
            label="MK8s OS image upgrade",
        )


def test_update_source_node_group_field_updates_selected_enabled_groups() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
                            "gpu": {
                                "gpu": True,
                                "platform": "gpu-platform",
                                "preset": "8gpu",
                                "gpu_stack_preset": "cuda12.8",
                            },
                            "disabled": {
                                "enabled": False,
                                "gpu_stack_preset": "cuda12.8",
                            },
                        },
                    },
                }
            ]
        }
    }

    changed = upgrade.update_source_node_group_field(
        payload,
        instance_id="prod",
        field="gpu_stack_preset",
        value="cuda13.0",
        node_group_keys=("gpu",),
    )

    groups = payload["infra"]["components"][0]["inputs"]["node_groups"]
    assert changed is True
    assert groups["system"]["preset"] == "cpu-4-16"
    assert groups["gpu"]["gpu_stack_preset"] == "cuda13.0"
    assert groups["disabled"]["gpu_stack_preset"] == "cuda12.8"


def test_update_source_node_group_field_rejects_unknown_source_key() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {"node_groups": {"system": {}}},
                }
            ]
        }
    }

    with pytest.raises(ValueError, match="missing"):
        upgrade.update_source_node_group_field(
            payload,
            instance_id="prod",
            field="preset",
            value="cpu-8-32",
            node_group_keys=("missing",),
        )


def test_update_source_node_template_updates_selected_tuple_and_pins_versions() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"k8s_version": "1.32"},
                        "node_groups": {
                            "system": {
                                "version": "1.32",
                                "os": "ubuntu22.04",
                                "platform": "cpu-platform",
                            },
                            "gpu": {
                                "gpu": True,
                                "version": "1.32",
                                "os": "ubuntu22.04",
                                "gpu_stack_source": "nebius_image",
                                "gpu_stack_preset": "cuda12.8",
                            },
                            "operator-gpu": {
                                "gpu": True,
                                "version": "1.32",
                                "os": "ubuntu22.04",
                                "gpu_stack_source": "operator_managed",
                            },
                        },
                    },
                }
            ]
        }
    }

    changed = upgrade.update_source_node_template(
        payload,
        instance_id="prod",
        target_version="1.33",
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        node_group_keys=("system", "gpu", "operator-gpu"),
        node_group_versions={
            "system": "1.33",
            "gpu": "1.33",
            "operator-gpu": "1.32",
        },
    )

    groups = payload["infra"]["components"][0]["inputs"]["node_groups"]
    assert changed is True
    assert payload["infra"]["components"][0]["inputs"]["cluster"]["k8s_version"] == "1.33"
    assert groups["system"]["version"] == "1.33"
    assert groups["system"]["os"] == "ubuntu24.04"
    assert "gpu_stack_preset" not in groups["system"]
    assert groups["gpu"]["version"] == "1.33"
    assert groups["gpu"]["os"] == "ubuntu24.04"
    assert groups["gpu"]["gpu_stack_preset"] == "cuda13.0"
    assert groups["operator-gpu"]["version"] == "1.32"
    assert groups["operator-gpu"]["os"] == "ubuntu24.04"
    assert "gpu_stack_preset" not in groups["operator-gpu"]


def test_terraform_node_group_strategy_for_policy_matches_nebius_provider_schema() -> None:
    assert upgrade.terraform_node_group_strategy_for_policy(
        "safe-surge",
        upgrade.resolve_drain_timeout("safe-surge", "auto"),
    ) == {
        "max_surge": {"count": 1},
        "max_unavailable": {"count": 0},
        "drain_timeout": "30m",
    }
    assert upgrade.terraform_node_group_strategy_for_policy(
        "zero-surge",
        upgrade.resolve_drain_timeout("zero-surge", "auto"),
    ) == {
        "max_surge": {"count": 0},
        "max_unavailable": {"count": 1},
        "drain_timeout": "30m",
    }
    assert upgrade.terraform_node_group_strategy_for_policy(
        "force-delete",
        upgrade.resolve_drain_timeout("force-delete", "auto"),
    ) == {
        "max_surge": {"count": 0},
        "max_unavailable": {"count": 1},
        "drain_timeout": "10m",
    }


def test_source_node_groups_by_name_matches_terraform_default_names() -> None:
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "cluster": {"cluster_name": "training"},
            "node_groups": {
                "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
                "gpu": {"gpu": True, "platform": "gpu-platform", "preset": "8gpu"},
            },
        },
    }

    groups = upgrade.source_node_groups_by_name(source)

    assert groups["system"].key == "system"
    assert groups["training-system"].key == "system"
    assert groups["gpu"].key == "gpu"
    assert groups["training-gpu"].key == "gpu"


def test_source_node_group_strategy_snapshot_and_restore() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "prod",
                    "enabled": True,
                    "inputs": {
                        "cluster": {"k8s_version": "1.32"},
                        "node_groups": {
                            "system": {"strategy": {"max_surge": {"count": 1}}},
                            "gpu": {},
                        },
                    },
                }
            ]
        }
    }

    snapshot = upgrade.source_node_group_strategy_snapshot(payload, instance_id="prod")
    upgrade.set_source_node_group_strategies(
        payload,
        instance_id="prod",
        strategies={
            "system": {"max_surge": {"count": 0}, "max_unavailable": {"count": 1}},
            "gpu": {"max_surge": {"count": 0}, "max_unavailable": {"count": 1}},
        },
    )
    upgrade.set_source_node_group_strategies(
        payload,
        instance_id="prod",
        strategies=snapshot,
    )

    groups = payload["infra"]["components"][0]["inputs"]["node_groups"]
    assert groups["system"]["strategy"] == {"max_surge": {"count": 1}}
    assert "strategy" not in groups["gpu"]


def test_source_mk8s_cluster_name_uses_configured_name_or_target_fallback() -> None:
    assert (
        upgrade.source_mk8s_cluster_name(
            {"inputs": {"cluster": {"cluster_name": "prod-cluster"}}},
            fallback="mk8s",
        )
        == "prod-cluster"
    )
    assert upgrade.source_mk8s_cluster_name({"inputs": {}}, fallback="mk8s") == "mk8s"


def test_compatibility_choices_parse_version_items_response() -> None:
    response = SimpleNamespace(
        versions=(
            SimpleNamespace(
                items=(
                    SimpleNamespace(
                        compatible_platforms=("gpu-h100-sxm",),
                        os="ubuntu24.04",
                        drivers_preset="cuda13.0",
                    ),
                )
            ),
        )
    )

    assert upgrade.compatibility_choices_from_response(response, platform="gpu-h100-sxm") == (
        upgrade.CompatibilityChoice(
            platform="gpu-h100-sxm",
            os="ubuntu24.04",
            drivers_preset="cuda13.0",
        ),
    )


def test_gpu_compatibility_requires_matching_nonempty_driver_preset() -> None:
    group = upgrade.LiveNodeGroup(
        id="ng-gpu",
        name="gpu",
        version="1.32",
        resource_version=1,
        platform="gpu-h100-sxm",
        preset="1gpu-16vcpu-200gb",
        os="ubuntu24.04",
        drivers_preset="cuda13.0",
        gpu=True,
        raw=object(),
    )

    assert upgrade.compatibility_failures_for_node_group(
        config_path="config.yaml",
        target_selector="infra:mk8s@prod",
        target_version="1.33",
        group=group,
        choices=[
            upgrade.CompatibilityChoice(
                platform="gpu-h100-sxm",
                os="ubuntu24.04",
                drivers_preset="",
            )
        ],
    )
    assert not upgrade.compatibility_failures_for_node_group(
        config_path="config.yaml",
        target_selector="infra:mk8s@prod",
        target_version="1.33",
        group=group,
        choices=[
            upgrade.CompatibilityChoice(
                platform="gpu-h100-sxm",
                os="ubuntu24.04",
                drivers_preset="",
            ),
            upgrade.CompatibilityChoice(
                platform="gpu-h100-sxm",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
        ],
    )


def test_node_group_rollout_complete_requires_no_outdated_or_surge_nodes() -> None:
    rolling = _node_group(id="ng-system", name="system", version="1.33")
    rolling.status = SimpleNamespace(
        version="v1.33.7-nebius-node.64",
        ready_node_count=2,
        target_node_count=2,
        node_count=3,
        outdated_node_count=1,
        reconciling=True,
    )
    complete = _node_group(id="ng-system", name="system", version="1.33")
    complete.status = SimpleNamespace(
        version="v1.33.7-nebius-node.64",
        ready_node_count=2,
        target_node_count=2,
        node_count=2,
        outdated_node_count=0,
        reconciling=False,
    )
    missing_outdated = _node_group(id="ng-system", name="system", version="1.33")
    missing_outdated.status = SimpleNamespace(
        version="v1.33.7-nebius-node.64",
        ready_node_count=2,
        target_node_count=2,
        node_count=2,
        reconciling=False,
    )

    assert not upgrade.node_group_rollout_complete(rolling, version="1.33")
    assert not upgrade.node_group_rollout_complete(missing_outdated, version="1.33")
    assert upgrade.node_group_rollout_complete(complete, version="1.33")


def test_compatibility_failure_points_to_os_image_layer_first() -> None:
    group = upgrade.LiveNodeGroup(
        id="ng-1",
        name="system",
        version="1.32",
        resource_version=1,
        platform="cpu-platform",
        preset="cpu-4-16",
        os="ubuntu22.04",
        drivers_preset="",
        gpu=False,
        raw=object(),
        source=_source_node_group(
            key="cpu-nodegroup1",
            name="system",
            os="ubuntu22.04",
        ),
    )

    failures = upgrade.compatibility_failures_for_node_group(
        config_path="config.yaml",
        target_selector="infra:mk8s@prod",
        target_version="1.33",
        group=group,
        choices=[
            upgrade.CompatibilityChoice(
                platform="cpu-platform",
                os="ubuntu24.04",
                drivers_preset="",
            )
        ],
    )

    assert len(failures) == 1
    assert (
        "nebius-cxcli upgrade os-image config.yaml infra:mk8s@prod --to-os ubuntu24.04"
        in failures[0].follow_up
    )
    assert "inputs.node_groups.cpu-nodegroup1.os to ubuntu24.04" in failures[0].follow_up
    assert "`upgrade os-image` command shape is reserved" not in failures[0].follow_up


def test_compatibility_failure_points_to_gpu_stack_layer() -> None:
    group = upgrade.LiveNodeGroup(
        id="ng-gpu",
        name="gpu",
        version="1.32",
        resource_version=1,
        platform="gpu-platform",
        preset="8gpu",
        os="ubuntu24.04",
        drivers_preset="cuda12.8",
        gpu=True,
        raw=object(),
        source=_source_node_group(
            key="gpu-nodegroup1",
            name="gpu",
            gpu=True,
            platform="gpu-platform",
            preset="8gpu",
            gpu_stack_preset="cuda12.8",
        ),
    )

    failures = upgrade.compatibility_failures_for_node_group(
        config_path="config.yaml",
        target_selector="infra:mk8s@prod",
        target_version="1.33",
        group=group,
        choices=[
            upgrade.CompatibilityChoice(
                platform="gpu-platform",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            )
        ],
    )

    assert len(failures) == 1
    assert "inputs.node_groups.gpu-nodegroup1.gpu_stack_preset to cuda13.0" in failures[0].follow_up
    assert (
        "nebius-cxcli upgrade gpu-stack-preset config.yaml infra:mk8s@prod "
        "--to-gpu-stack-preset cuda13.0"
    ) in failures[0].follow_up
    assert "`upgrade gpu-stack-preset` command shape is reserved" not in failures[0].follow_up


def test_compatibility_failure_points_to_platform_layer() -> None:
    group = upgrade.LiveNodeGroup(
        id="ng-1",
        name="system",
        version="1.32",
        resource_version=1,
        platform="cpu-old",
        preset="cpu-4-16",
        os="ubuntu24.04",
        drivers_preset="",
        gpu=False,
        raw=object(),
        source=_source_node_group(
            key="cpu-nodegroup1",
            name="system",
            platform="cpu-old",
        ),
    )

    failures = upgrade.compatibility_failures_for_node_group(
        config_path="config.yaml",
        target_selector="infra:mk8s@prod",
        target_version="1.33",
        group=group,
        choices=[
            upgrade.CompatibilityChoice(
                platform="cpu-d3",
                os="ubuntu24.04",
                drivers_preset="",
            )
        ],
    )

    assert len(failures) == 1
    assert "inputs.node_groups.cpu-nodegroup1.platform to cpu-d3" in failures[0].follow_up
    assert (
        "nebius-cxcli upgrade platform config.yaml infra:mk8s@prod --to-platform cpu-d3"
    ) in failures[0].follow_up
    assert "command shape is reserved" not in failures[0].follow_up


def test_blocking_preflight_findings_respect_disruption_policy() -> None:
    pdb = upgrade.PreflightFinding(
        kind=upgrade.PDB_BLOCKER_KIND,
        namespace="default",
        name="web",
        message="blocked",
    )
    stuck = upgrade.PreflightFinding(
        kind=upgrade.STUCK_TERMINATING_POD_KIND,
        namespace="default",
        name="db",
        message="stuck",
    )
    inspection_failed = upgrade.PreflightFinding(
        kind=upgrade.PREFLIGHT_INSPECTION_FAILED_KIND,
        namespace="",
        name="pods",
        message="kubectl failed",
    )

    assert upgrade.blocking_preflight_findings(
        [pdb, stuck, inspection_failed],
        disruption_policy="safe-surge",
    ) == (
        pdb,
        stuck,
        inspection_failed,
    )
    assert upgrade.blocking_preflight_findings(
        [pdb, stuck, inspection_failed],
        disruption_policy="force-delete",
    ) == (stuck, inspection_failed)


def test_kubernetes_preflight_inspection_failures_always_block_force_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upgrade,
        "_kubectl_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("kubectl failed")),
    )

    findings = upgrade._pod_findings(kube_env={}, timeout_seconds=1) + upgrade._soperator_findings(
        kube_env={}, timeout_seconds=1
    )

    assert {finding.kind for finding in findings} == {upgrade.PREFLIGHT_INSPECTION_FAILED_KIND}
    assert (
        upgrade.blocking_preflight_findings(
            findings,
            disruption_policy="force-delete",
        )
        == findings
    )


def test_plan_orders_non_gpu_node_groups_before_gpu_node_groups() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
                "gpu": {"gpu": True, "platform": "gpu-platform", "preset": "8gpu"},
            }
        },
    }

    plan = upgrade.plan_k8s_upgrade(
        config_path="config.yaml",
        target=target,
        cluster=_cluster(version="1.32"),
        cluster_id="cluster-1",
        source_component=source,
        target_version="1.33",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu",
                platform="gpu-platform",
                preset="8gpu",
                drivers_preset="cuda13.0",
            ),
            _node_group(id="ng-system", name="system"),
        ],
        compatibility_lookup=lambda **_kwargs: [
            upgrade.CompatibilityChoice(
                platform="cpu-platform", os="ubuntu24.04", drivers_preset=""
            ),
            upgrade.CompatibilityChoice(
                platform="gpu-platform",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
        ],
    )

    assert [group.name for group in plan.node_groups] == ["system", "gpu"]
    assert plan.hops == (upgrade.UpgradeHop(from_version="1.32", to_version="1.33"),)
    assert not plan.compatibility_failures


def test_plan_os_image_upgrade_orders_and_can_filter_node_group() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "cluster": {"cluster_name": "training"},
            "node_groups": {
                "system": {
                    "platform": "cpu-platform",
                    "preset": "cpu-4-16",
                    "os": "ubuntu22.04",
                },
                "gpu": {
                    "name": "gpu-workers",
                    "gpu": True,
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu22.04",
                    "gpu_stack_preset": "cuda13.0",
                },
            },
        },
    }

    plan = upgrade.plan_os_image_upgrade(
        target=target,
        cluster=_cluster(version="1.33"),
        cluster_id="cluster-1",
        source_component=source,
        target_os="ubuntu24.04",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu-workers",
                version="1.33",
                platform="gpu-platform",
                preset="8gpu",
                os="ubuntu22.04",
                drivers_preset="cuda13.0",
            ),
            _node_group(
                id="ng-system",
                name="training-system",
                version="1.33",
                os="ubuntu22.04",
            ),
        ],
        compatibility_lookup=lambda **kwargs: [
            upgrade.CompatibilityChoice(
                platform=kwargs["platform"],
                os="ubuntu24.04",
                drivers_preset="cuda13.0" if kwargs["platform"] == "gpu-platform" else "",
            ),
        ],
        node_group="gpu",
    )

    assert [group.name for group in plan.node_groups] == ["gpu-workers"]
    assert plan.node_groups[0].source is not None
    assert plan.node_groups[0].source.key == "gpu"
    assert plan.k8s_version == "1.33"
    assert plan.target_os == "ubuntu24.04"
    assert plan.mutates is True
    assert not plan.compatibility_failures
    rendered = "\n".join(upgrade.format_os_image_upgrade_plan(plan, dry_run=True))
    assert "MK8s OS image upgrade plan" in rendered
    assert "gpu-workers: ubuntu22.04 -> ubuntu24.04" in rendered
    assert "Kubernetes version upgrade plan" not in rendered


def test_plan_os_image_upgrade_rejects_unknown_node_group() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
            }
        },
    }

    with pytest.raises(ValueError, match="missing"):
        upgrade.plan_os_image_upgrade(
            target=target,
            cluster=_cluster(version="1.33"),
            cluster_id="cluster-1",
            source_component=source,
            target_os="ubuntu24.04",
            disruption_policy="safe-surge",
            drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
            live_node_groups=[_node_group(id="ng-system", name="system")],
            compatibility_lookup=lambda **_kwargs: [
                upgrade.CompatibilityChoice(
                    platform="cpu-platform",
                    os="ubuntu24.04",
                    drivers_preset="",
                ),
            ],
            node_group="missing",
        )


def test_plan_os_image_upgrade_rejects_unmanaged_live_node_groups() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
            }
        },
    }

    with pytest.raises(ValueError, match="not declared in config.yaml"):
        upgrade.plan_os_image_upgrade(
            target=target,
            cluster=_cluster(version="1.33"),
            cluster_id="cluster-1",
            source_component=source,
            target_os="ubuntu24.04",
            disruption_policy="safe-surge",
            drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
            live_node_groups=[
                _node_group(id="ng-system", name="system"),
                _node_group(id="ng-rogue", name="rogue"),
            ],
            compatibility_lookup=lambda **_kwargs: [
                upgrade.CompatibilityChoice(
                    platform="cpu-platform",
                    os="ubuntu24.04",
                    drivers_preset="",
                ),
            ],
        )


def test_plan_node_template_upgrade_validates_combined_matrix_tuple() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {
                    "platform": "cpu-platform",
                    "preset": "cpu-4-16",
                    "os": "ubuntu22.04",
                },
                "gpu": {
                    "gpu": True,
                    "gpu_stack_source": "nebius_image",
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu22.04",
                    "gpu_stack_preset": "cuda12.8",
                },
            }
        },
    }

    plan = upgrade.plan_node_template_upgrade(
        target=target,
        cluster=_cluster(version="1.32"),
        cluster_id="cluster-1",
        source_component=source,
        target_version="1.33",
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu",
                platform="gpu-platform",
                preset="8gpu",
                os="ubuntu22.04",
                drivers_preset="cuda12.8",
            ),
            _node_group(id="ng-system", name="system", os="ubuntu22.04"),
        ],
        compatibility_lookup=lambda **kwargs: [
            upgrade.CompatibilityChoice(
                platform=kwargs["platform"],
                os="ubuntu24.04",
                drivers_preset="cuda13.0" if kwargs["platform"] == "gpu-platform" else "",
            )
        ],
    )

    assert [group.name for group in plan.node_groups] == ["system", "gpu"]
    assert plan.hops == (upgrade.UpgradeHop(from_version="1.32", to_version="1.33"),)
    assert plan.target_os == "ubuntu24.04"
    assert plan.target_gpu_stack_preset == "cuda13.0"
    assert plan.mutates is True
    assert not plan.compatibility_failures
    rendered = "\n".join(upgrade.format_node_template_upgrade_plan(plan, dry_run=True))
    assert "MK8s node-template upgrade plan" in rendered
    assert "system: version 1.32 -> 1.33, OS ubuntu22.04 -> ubuntu24.04" in rendered
    assert "gpu: version 1.32 -> 1.33, OS ubuntu22.04 -> ubuntu24.04" in rendered
    assert "GPU stack cuda12.8 -> cuda13.0" in rendered


def test_plan_node_template_upgrade_requires_gpu_stack_for_nebius_image_gpu() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "gpu": {
                    "gpu": True,
                    "gpu_stack_source": "nebius_image",
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu22.04",
                    "gpu_stack_preset": "cuda12.8",
                },
            }
        },
    }

    with pytest.raises(ValueError, match="--to-gpu-stack-preset is required"):
        upgrade.plan_node_template_upgrade(
            target=target,
            cluster=_cluster(version="1.32"),
            cluster_id="cluster-1",
            source_component=source,
            target_version="1.33",
            target_os="ubuntu24.04",
            disruption_policy="safe-surge",
            drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
            live_node_groups=[
                _node_group(
                    id="ng-gpu",
                    name="gpu",
                    platform="gpu-platform",
                    preset="8gpu",
                    os="ubuntu22.04",
                    drivers_preset="cuda12.8",
                ),
            ],
            compatibility_lookup=lambda **_kwargs: (),
        )


def test_plan_node_template_upgrade_rejects_unused_gpu_stack_flag() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {
                    "platform": "cpu-platform",
                    "preset": "cpu-4-16",
                    "os": "ubuntu22.04",
                },
            }
        },
    }

    with pytest.raises(ValueError, match="no selected node group uses Nebius-image"):
        upgrade.plan_node_template_upgrade(
            target=target,
            cluster=_cluster(version="1.32"),
            cluster_id="cluster-1",
            source_component=source,
            target_version="1.33",
            target_os="ubuntu24.04",
            target_gpu_stack_preset="cuda13.0",
            disruption_policy="safe-surge",
            drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
            live_node_groups=[
                _node_group(id="ng-system", name="system", os="ubuntu22.04"),
            ],
            compatibility_lookup=lambda **_kwargs: (),
        )


def test_plan_node_template_upgrade_allows_operator_managed_gpu_without_driver_preset() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "gpu": {
                    "gpu": True,
                    "gpu_stack_source": "operator_managed",
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu22.04",
                },
            }
        },
    }

    plan = upgrade.plan_node_template_upgrade(
        target=target,
        cluster=_cluster(version="1.32"),
        cluster_id="cluster-1",
        source_component=source,
        target_version="1.33",
        target_os="ubuntu24.04",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu",
                platform="gpu-platform",
                preset="8gpu",
                os="ubuntu22.04",
                drivers_preset="",
            ),
        ],
        compatibility_lookup=lambda **kwargs: [
            upgrade.CompatibilityChoice(
                platform=kwargs["platform"],
                os="ubuntu24.04",
                drivers_preset="",
            )
        ],
    )

    assert plan.mutates is True
    assert not plan.compatibility_failures
    assert (
        upgrade.node_template_target_drivers_preset(
            plan.node_groups[0],
            target_gpu_stack_preset=plan.target_gpu_stack_preset,
        )
        == ""
    )


def test_plan_node_template_upgrade_reports_invalid_matrix_tuple() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "gpu": {
                    "gpu": True,
                    "gpu_stack_source": "nebius_image",
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu22.04",
                    "gpu_stack_preset": "cuda12.8",
                },
            }
        },
    }

    plan = upgrade.plan_node_template_upgrade(
        target=target,
        cluster=_cluster(version="1.32"),
        cluster_id="cluster-1",
        source_component=source,
        target_version="1.33",
        target_os="ubuntu24.04",
        target_gpu_stack_preset="cuda13.0",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu",
                platform="gpu-platform",
                preset="8gpu",
                os="ubuntu22.04",
                drivers_preset="cuda12.8",
            ),
        ],
        compatibility_lookup=lambda **kwargs: [
            upgrade.CompatibilityChoice(
                platform=kwargs["platform"],
                os="ubuntu24.04",
                drivers_preset="cuda12.8",
            )
        ],
    )

    assert len(plan.compatibility_failures) == 1
    assert "cannot use Kubernetes 1.33, OS 'ubuntu24.04'" in plan.compatibility_failures[0].reason
    assert "Compatible GPU stack values" in plan.compatibility_failures[0].follow_up


def test_plan_node_layer_upgrade_filters_cpu_and_gpu_groups() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "cluster": {"cluster_name": "training"},
            "node_groups": {
                "system": {
                    "platform": "cpu-platform",
                    "preset": "cpu-4-16",
                    "os": "ubuntu24.04",
                },
                "gpu": {
                    "name": "gpu-workers",
                    "gpu": True,
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu24.04",
                    "gpu_stack_preset": "cuda13.0",
                },
            },
        },
    }

    plan = upgrade.plan_node_layer_upgrade(
        target=target,
        cluster=_cluster(version="1.33"),
        cluster_id="cluster-1",
        source_component=source,
        spec=upgrade.NodeLayerUpgradeSpec(
            command="cpu-preset",
            title="MK8s CPU preset",
            source_field="preset",
            live_field="preset",
            target_label="CPU preset",
            group_filter="cpu",
        ),
        target_value="cpu-8-32",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu-workers",
                version="1.33",
                platform="gpu-platform",
                preset="8gpu",
                drivers_preset="cuda13.0",
            ),
            _node_group(
                id="ng-system",
                name="training-system",
                version="1.33",
                preset="cpu-4-16",
            ),
        ],
        compatibility_lookup=lambda **_kwargs: (),
    )

    assert [group.name for group in plan.node_groups] == ["training-system"]
    assert plan.mutates is True
    assert not plan.compatibility_failures
    rendered = "\n".join(upgrade.format_node_layer_upgrade_plan(plan, dry_run=True))
    assert "MK8s CPU preset upgrade plan" in rendered
    assert "training-system: cpu-4-16 -> cpu-8-32" in rendered
    assert "gpu-workers" not in rendered


def test_plan_node_layer_upgrade_rejects_wrong_group_kind() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "gpu": {
                    "gpu": True,
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "gpu_stack_preset": "cuda13.0",
                },
            }
        },
    }

    with pytest.raises(ValueError, match="can target only CPU/system"):
        upgrade.plan_node_layer_upgrade(
            target=target,
            cluster=_cluster(version="1.33"),
            cluster_id="cluster-1",
            source_component=source,
            spec=upgrade.NodeLayerUpgradeSpec(
                command="cpu-preset",
                title="MK8s CPU preset",
                source_field="preset",
                live_field="preset",
                target_label="CPU preset",
                group_filter="cpu",
            ),
            target_value="cpu-8-32",
            disruption_policy="safe-surge",
            drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
            live_node_groups=[
                _node_group(
                    id="ng-gpu",
                    name="gpu",
                    version="1.33",
                    platform="gpu-platform",
                    preset="8gpu",
                    drivers_preset="cuda13.0",
                ),
            ],
            compatibility_lookup=lambda **_kwargs: (),
            node_group="gpu",
        )


def test_plan_gpu_stack_upgrade_uses_compatibility_matrix() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "gpu": {
                    "gpu": True,
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu24.04",
                    "gpu_stack_preset": "cuda12.8",
                },
            }
        },
    }

    plan = upgrade.plan_node_layer_upgrade(
        target=target,
        cluster=_cluster(version="1.33"),
        cluster_id="cluster-1",
        source_component=source,
        spec=upgrade.NodeLayerUpgradeSpec(
            command="gpu-stack-preset",
            title="MK8s GPU stack preset",
            source_field="gpu_stack_preset",
            live_field="drivers_preset",
            target_label="GPU stack preset",
            group_filter="gpu",
        ),
        target_value="cuda13.0",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu",
                version="1.33",
                platform="gpu-platform",
                preset="8gpu",
                drivers_preset="cuda12.8",
            ),
        ],
        compatibility_lookup=lambda **_kwargs: [
            upgrade.CompatibilityChoice(
                platform="gpu-platform",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            )
        ],
    )

    assert plan.mutates is True
    assert not plan.compatibility_failures


def test_plan_gpu_stack_upgrade_rejects_incompatible_stack() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "gpu": {
                    "gpu": True,
                    "platform": "gpu-platform",
                    "preset": "8gpu",
                    "os": "ubuntu24.04",
                    "gpu_stack_preset": "cuda12.8",
                },
            }
        },
    }

    plan = upgrade.plan_node_layer_upgrade(
        target=target,
        cluster=_cluster(version="1.33"),
        cluster_id="cluster-1",
        source_component=source,
        spec=upgrade.NodeLayerUpgradeSpec(
            command="gpu-stack-preset",
            title="MK8s GPU stack preset",
            source_field="gpu_stack_preset",
            live_field="drivers_preset",
            target_label="GPU stack preset",
            group_filter="gpu",
        ),
        target_value="cuda14.0",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(
                id="ng-gpu",
                name="gpu",
                version="1.33",
                platform="gpu-platform",
                preset="8gpu",
                drivers_preset="cuda12.8",
            ),
        ],
        compatibility_lookup=lambda **_kwargs: [
            upgrade.CompatibilityChoice(
                platform="gpu-platform",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            )
        ],
    )

    assert len(plan.compatibility_failures) == 1
    assert "cannot use GPU stack preset 'cuda14.0'" in plan.compatibility_failures[0].reason


def test_os_image_compatibility_requires_current_gpu_stack() -> None:
    group = upgrade.LiveNodeGroup(
        id="ng-gpu",
        name="gpu",
        version="1.33",
        resource_version=1,
        platform="gpu-platform",
        preset="8gpu",
        os="ubuntu22.04",
        drivers_preset="cuda12.8",
        gpu=True,
        raw=object(),
    )

    failures = upgrade.os_image_compatibility_failures_for_node_group(
        target_version="1.33",
        target_os="ubuntu24.04",
        group=group,
        choices=[
            upgrade.CompatibilityChoice(
                platform="gpu-platform",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            )
        ],
    )

    assert len(failures) == 1
    assert "upgrade gpu-stack-preset" in failures[0].follow_up
    assert "cuda12.8" in failures[0].reason


def test_plan_rejects_node_groups_above_target_control_plane_version() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
            }
        },
    }

    with pytest.raises(ValueError, match=r"system \(1\.33\)"):
        upgrade.plan_k8s_upgrade(
            config_path="config.yaml",
            target=target,
            cluster=_cluster(version="1.32"),
            cluster_id="cluster-1",
            source_component=source,
            target_version="1.32",
            disruption_policy="safe-surge",
            drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
            live_node_groups=[
                _node_group(id="ng-system", name="system", version="v1.33.7-nebius-node.64"),
            ],
            compatibility_lookup=lambda **_kwargs: [
                upgrade.CompatibilityChoice(
                    platform="cpu-platform", os="ubuntu24.04", drivers_preset=""
                ),
            ],
        )


def test_plan_mutates_when_control_plane_is_target_but_node_group_is_old() -> None:
    target = upgrade.parse_upgrade_selector("infra:mk8s@prod")
    source = {
        "id": "mk8s",
        "instance_id": "prod",
        "enabled": True,
        "inputs": {
            "node_groups": {
                "system": {"platform": "cpu-platform", "preset": "cpu-4-16"},
            }
        },
    }

    plan = upgrade.plan_k8s_upgrade(
        config_path="config.yaml",
        target=target,
        cluster=_cluster(version="1.33"),
        cluster_id="cluster-1",
        source_component=source,
        target_version="1.33",
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        live_node_groups=[
            _node_group(id="ng-system", name="system", version="1.32"),
        ],
        compatibility_lookup=lambda **_kwargs: [
            upgrade.CompatibilityChoice(
                platform="cpu-platform", os="ubuntu24.04", drivers_preset=""
            ),
        ],
    )

    assert plan.hops == ()
    assert plan.node_group_updates_required
    assert plan.mutates


def test_format_upgrade_plan_summarizes_emptydir_findings_and_repeat_command() -> None:
    group = _node_group(id="ng-system", name="system", version="1.31")
    plan = upgrade.Mk8sUpgradePlan(
        target=upgrade.parse_upgrade_selector("infra:mk8s@prod"),
        cluster_id="cluster-1",
        cluster_name="prod",
        current_version="1.31",
        target_version="1.32",
        hops=(upgrade.UpgradeHop(from_version="1.31", to_version="1.32"),),
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        node_groups=(
            upgrade.LiveNodeGroup(
                id="ng-system",
                name="system",
                version="1.31",
                resource_version=1,
                platform="cpu-platform",
                preset="cpu-4-16",
                os="ubuntu24.04",
                drivers_preset="",
                gpu=False,
                raw=group,
            ),
        ),
        compatibility_failures=(),
        preflight_findings=(
            upgrade.PreflightFinding(
                kind=upgrade.EMPTYDIR_POD_KIND,
                namespace="flux-system",
                name="source-controller-abc",
                message="Pod uses emptyDir.",
            ),
            upgrade.PreflightFinding(
                kind=upgrade.EMPTYDIR_POD_KIND,
                namespace="kube-system",
                name="metrics-server-def",
                message="Pod uses emptyDir.",
            ),
            upgrade.PreflightFinding(
                kind=upgrade.PDB_BLOCKER_KIND,
                namespace="app",
                name="api",
                message="PDB allows zero disruptions.",
            ),
        ),
        warnings=("safe mode generic warning",),
    )

    lines = upgrade.format_upgrade_plan(
        plan,
        dry_run=True,
        repeat_dry_run_command="nebius-cxcli upgrade k8s-version config.yaml infra:mk8s@prod --dry-run",
    )

    assert (
        "  - emptydir-pod: 2 pods use emptyDir; local ephemeral data is lost during node "
        "replacement. This is expected for scratch or intermediate data when persistent data "
        "uses PVC-backed volumes."
    ) in lines
    assert "  - pdb-blocker: app/api - PDB allows zero disruptions." in lines
    assert all("source-controller-abc" not in line for line in lines)
    assert all("metrics-server-def" not in line for line in lines)
    assert "- repeat dry-run command:" in lines
    assert "nebius-cxcli upgrade k8s-version config.yaml infra:mk8s@prod --dry-run" in lines


def test_wait_for_node_group_rollout_uses_sdk_status_without_sdk_updates() -> None:
    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def wait_node_group_version(
            self,
            *,
            cluster_id: str,
            node_group_id: str,
            version: str,
            timeout_seconds: int = 3600,
        ) -> None:
            self.calls.append(
                ("wait-node-group", f"{cluster_id}:{node_group_id}:{version}:{timeout_seconds}")
            )

    fake = FakeExecutor()
    group = _node_group(id="ng-system", name="system", version="1.33")
    group.status = SimpleNamespace(
        version="v1.33.7-nebius-node.64",
        ready_node_count=2,
        target_node_count=2,
        node_count=3,
        outdated_node_count=1,
        reconciling=True,
    )
    plan = upgrade.Mk8sUpgradePlan(
        target=upgrade.parse_upgrade_selector("infra:mk8s@prod"),
        cluster_id="cluster-1",
        cluster_name="prod",
        current_version="1.33",
        target_version="1.33",
        hops=(),
        disruption_policy="safe-surge",
        drain_timeout=upgrade.resolve_drain_timeout("safe-surge", "auto"),
        node_groups=(
            upgrade.LiveNodeGroup(
                id="ng-system",
                name="system",
                version="1.33",
                resource_version=1,
                platform="cpu-platform",
                preset="cpu-4-16",
                os="ubuntu24.04",
                drivers_preset="",
                gpu=False,
                raw=group,
            ),
        ),
        compatibility_failures=(),
    )

    assert plan.rollout_incomplete

    upgrade.wait_for_node_group_rollout(
        executor=fake,  # type: ignore[arg-type]
        plan=plan,
        planned_group=plan.node_groups[0],
    )

    assert fake.calls == [("wait-node-group", "cluster-1:ng-system:1.33:3600")]


def test_force_delete_drain_timeout_does_not_shorten_rollout_wait() -> None:
    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def wait_node_group_version(
            self,
            *,
            cluster_id: str,
            node_group_id: str,
            version: str,
            timeout_seconds: int = 3600,
        ) -> None:
            self.calls.append(
                ("wait-node-group", f"{cluster_id}:{node_group_id}:{version}:{timeout_seconds}")
            )

    raw_group = _node_group(id="ng-system", name="system", version="1.33")
    raw_group.status = SimpleNamespace(
        version="v1.33.7-nebius-node.64",
        ready_node_count=99,
        target_node_count=100,
        node_count=101,
        outdated_node_count=1,
        reconciling=True,
    )
    group = upgrade.LiveNodeGroup(
        id="ng-system",
        name="system",
        version="1.33",
        resource_version=1,
        platform="cpu-platform",
        preset="cpu-4-16",
        os="ubuntu24.04",
        drivers_preset="",
        gpu=False,
        raw=raw_group,
    )
    plan = upgrade.Mk8sUpgradePlan(
        target=upgrade.parse_upgrade_selector("infra:mk8s@prod"),
        cluster_id="cluster-1",
        cluster_name="prod",
        current_version="1.33",
        target_version="1.33",
        hops=(),
        disruption_policy="force-delete",
        drain_timeout=upgrade.resolve_drain_timeout("force-delete", "10m"),
        node_groups=(group,),
        compatibility_failures=(),
    )
    fake = FakeExecutor()

    upgrade.wait_for_node_group_rollout(
        executor=fake,  # type: ignore[arg-type]
        plan=plan,
        planned_group=group,
    )

    assert fake.calls == [("wait-node-group", "cluster-1:ng-system:1.33:60000")]


def test_node_group_rollout_wait_uses_source_target_size_when_status_is_absent() -> None:
    group = upgrade.LiveNodeGroup(
        id="ng-system",
        name="system",
        version="1.33",
        resource_version=1,
        platform="cpu-platform",
        preset="cpu-4-16",
        os="ubuntu24.04",
        drivers_preset="",
        gpu=False,
        raw=_node_group(id="ng-system", name="system", version="1.33"),
        source=upgrade.Mk8sNodeGroup(
            key="system",
            name="system",
            gpu=False,
            platform="cpu-platform",
            preset="cpu-4-16",
            os="ubuntu24.04",
            node_count=25,
            autoscaling_min_node_count=None,
            autoscaling_max_node_count=None,
            gpu_stack_source="operator_managed",
            gpu_stack_preset="",
            gpu_cluster_key="",
            gpu_cluster_id="",
            reservation_policy="none",
            reservation_ids=(),
            node_labels={},
            labels={},
        ),
    )

    assert upgrade.node_group_rollout_wait_seconds(group) == 15000
