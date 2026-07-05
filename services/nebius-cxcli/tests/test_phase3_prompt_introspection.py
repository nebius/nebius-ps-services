from __future__ import annotations

import yaml

import nebius_cxcli.cli as cli_module
from nebius_cxcli.cli import (
    _app_chart_skip_defaults_preview_lines,
    _dynamic_required_prompt,
    _prompt_path_sort_key,
    _prune_mk8s_node_group_defaults_without_soperator,
    _prune_redundant_app_chart_default_values,
    _required_leaf_names_for_entry,
    _run_component_field_wizard,
    _skip_mk8s_gpu_deployment_testing_prompt,
    _skip_sfs_multi_filesystem_prompt,
    _skip_sfs_single_filesystem_prompt,
    _skip_soperator_child_chart_prompt,
    _skip_soperator_install_mode_dependent_prompt,
    _skip_soperator_managed_mk8s_prompt,
    _skip_soperator_qos_configuration_prompt,
    _wizard_field_prompt_enabled,
    _wizard_field_write_default_to_config,
)
from nebius_cxcli.component_sources import ComponentDefault
from nebius_cxcli.components import ComponentEntry


def _mk8s_observability_wizard_entry() -> ComponentEntry:
    return ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "deploy.targets[].observability.enabled": {"default": False},
            "deploy.targets[].observability.kubernetes.logs.enabled": {"default": True},
            "deploy.targets[].observability.kubernetes.metrics.enabled": {"default": True},
            "deploy.targets[].observability.kubernetes.metrics.collect_k8s_cluster_metrics": {
                "default": True,
            },
            "deploy.targets[].observability.kubernetes.traces.enabled": {"default": True},
            "inputs.cluster.public_endpoint": {"default": True},
        },
    )


def _observability_agent_entry() -> ComponentEntry:
    return ComponentEntry(
        id="nebius-observability-agent",
        scope="apps",
        config_path="apps.observability.nebius-observability-agent",
        description="Nebius observability agent",
        group="observability",
        source=(
            "oci://cr.nebius.cloud/observability/public/"
            "nebius-observability-agent-helm/nebius-observability-agent-helm"
        ),
        version="1.0.0",
        default_namespace="observability",
        default_release_name="nebius-observability-agent",
    )


def _grafana_entry() -> ComponentEntry:
    return ComponentEntry(
        id="grafana",
        scope="apps",
        config_path="apps.observability.grafana",
        description="Grafana observability console",
        group="observability",
        source="https://grafana-community.github.io/helm-charts/grafana",
        version="12.1.3",
        default_namespace="observability",
        default_release_name="grafana",
    )


def _gateway_entry() -> ComponentEntry:
    return ComponentEntry(
        id="gateway-helm",
        scope="apps",
        config_path="apps.platform.gateway-helm",
        description="Envoy Gateway control plane",
        group="platform",
        source="oci://docker.io/envoyproxy/gateway-helm",
        version="1.7.0",
        default_namespace="envoy-gateway-system",
        default_release_name="envoy-gateway",
    )


def _mk8s_observability_payload() -> dict[str, object]:
    return {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
        "deploy": {
            "targets": [
                {
                    "instance_id": "mk8s",
                    "observability": {
                        "enabled": False,
                        "kubernetes": {
                            "logs": {"enabled": True},
                            "metrics": {
                                "enabled": True,
                                "collect_k8s_cluster_metrics": True,
                            },
                            "traces": {"enabled": True},
                        },
                    },
                },
            ],
        },
    }


def test_required_leaf_names_for_custom_component(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.module_required_variables",
        lambda _source: ("platform", "preset"),
    )
    entry = ComponentEntry(
        id="wireguard-gw",
        scope="infra",
        config_path="infra.wireguard-gw",
        description="wg",
        source="platform-infra/modules/wireguard-gw",
    )
    required = _required_leaf_names_for_entry(entry)
    assert required == {"platform", "preset"}
    sort_key = _prompt_path_sort_key(
        ("infra", "wireguard-gw", "platform"),
        required_leaf_names=required,
    )
    assert sort_key[0] == 0


def test_prune_redundant_app_chart_defaults_removes_only_chart_default_copies(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.helm_chart_default_values",
        lambda **_kwargs: {
            "foo": {"enabled": True},
            "replicaCount": 2,
            "filters": [{"name": "default", "operator": "In"}],
        },
    )
    entry = ComponentEntry(
        id="demo-app",
        scope="apps",
        config_path="apps.workloads.demo_app",
        description="demo",
    )
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "demo-app",
                    "enabled": True,
                    "group": "workloads",
                    "repo": "https://example.invalid/charts",
                    "version": "1.0.0",
                    "namespace": "demo-app",
                    "release-name": "demo-app",
                    "values": {
                        "foo": {"enabled": True},
                        "replicaCount": 2,
                        "filters": [{"name": "default", "operator": "In", "values": ["custom"]}],
                        "custom": {"enabled": False},
                    },
                },
            ]
        }
    }

    _prune_redundant_app_chart_default_values(payload=payload, app_entries=(entry,))
    values = payload["apps"]["charts"][0]["values"]
    assert values == {
        "filters": [{"name": "default", "operator": "In", "values": ["custom"]}],
        "custom": {"enabled": False},
    }


def test_soperator_child_chart_wizard_prompts_are_gated() -> None:
    entry = ComponentEntry(
        id="soperator",
        scope="apps",
        config_path="apps.slurm.soperator",
        description="soperator",
        wizard_fields={
            "values.soperator-notifier.slack.webhookSource": {"default": "deploy-time"},
            "values.soperator-notifier.slack.mysterybox.secretId": {"default": ""},
        },
    )
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster-a",
                    "enabled": True,
                    "values": {
                        "soperator-notifier": {
                            "enabled": False,
                            "slack": {
                                "mode": "existing-webhook",
                                "webhookSource": "deploy-time",
                                "channelName": "",
                                "mysterybox": {"secretId": ""},
                            },
                        },
                    },
                }
            ]
        }
    }
    enabled_path = ("apps", "charts", 0, "values", "soperator-notifier", "enabled")
    source_path = (
        "apps",
        "charts",
        0,
        "values",
        "soperator-notifier",
        "slack",
        "webhookSource",
    )

    assert _prompt_path_sort_key(enabled_path, required_leaf_names=set()) < _prompt_path_sort_key(
        source_path,
        required_leaf_names=set(),
    )
    assert not _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.enabled",
    )
    assert _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.slack.webhookSource",
    )

    payload["apps"]["charts"][0]["values"]["soperator-notifier"]["enabled"] = True
    assert not _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.slack.webhookSource",
    )
    assert _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.slack.mysterybox.secretId",
    )
    payload["apps"]["charts"][0]["values"]["soperator-notifier"]["slack"]["webhookSource"] = (
        "mysterybox"
    )
    assert not _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.slack.mysterybox.secretId",
    )
    assert _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.slack.channelName",
    )
    assert _skip_soperator_child_chart_prompt(
        payload=payload,
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-notifier.slack.oauth.clientId",
    )


def test_soperator_wizard_parent_values_prompt_suppresses_raw_chart_values() -> None:
    entry = ComponentEntry(
        id="soperator",
        scope="apps",
        config_path="apps.slurm.soperator",
        description="soperator",
        wizard_fields={
            "namespace": {"prompt": False},
            "release-name": {"prompt": False},
            "values": {"prompt": False},
            "values.partitionProfile": {"default": "shape-default"},
            "values.soperator-activechecks.enabled": {"default": False},
            "values.sssd.enabled": {"default": False, "write_default_to_config": True},
        },
    )

    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].namespace",
    )
    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].release-name",
    )
    assert _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.partitionProfile",
    )
    assert _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-activechecks.enabled",
    )
    assert _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.sssd.enabled",
    )
    assert _wizard_field_write_default_to_config(
        entry=entry,
        full_path_label="apps.charts[0].values.sssd.enabled",
    )
    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.nodesets[0].sssd.enabled",
    )
    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.slurmNodes.sssd.enabled",
    )
    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.soperator-activechecks.srunReadyPartition",
    )
    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.rebooter.enabled",
    )
    assert not _wizard_field_prompt_enabled(
        entry=entry,
        full_path_label="apps.charts[0].values.controllerManager.manager.resources.requests.cpu",
    )


def test_soperator_target_prompts_cxcli_workload_gpu_validations() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {"node_groups": {"worker": {"gpu": True}}},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "soperator-cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                }
            ]
        },
        "deploy": {
            "targets": [{"instance_id": "cluster1", "deployment_testing": {"mk8s_gpu": {}}}]
        },
    }

    assert not _skip_mk8s_gpu_deployment_testing_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].deployment_testing.mk8s_gpu.operator_readiness.enabled",
    )
    assert not _skip_mk8s_gpu_deployment_testing_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].deployment_testing.mk8s_gpu.gpu_visibility.enabled",
    )
    payload["deploy"]["targets"][0]["deployment_testing"]["mk8s_gpu"]["gpu_visibility"] = {
        "enabled": True
    }
    assert not _skip_mk8s_gpu_deployment_testing_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].deployment_testing.mk8s_gpu.gpu_visibility.max_nodes",
    )


def test_mk8s_gpu_deployment_testing_max_nodes_required_only_when_section_enabled() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )
    label = "deploy.targets[0].deployment_testing.mk8s_gpu.gpu_visibility.max_nodes"
    payload = {
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "deployment_testing": {
                        "mk8s_gpu": {"gpu_visibility": {"enabled": True}}
                    },
                }
            ]
        }
    }

    assert _dynamic_required_prompt(payload=payload, entry=entry, full_path_label=label)
    payload["deploy"]["targets"][0]["deployment_testing"]["mk8s_gpu"]["gpu_visibility"][
        "enabled"
    ] = False
    assert not _dynamic_required_prompt(payload=payload, entry=entry, full_path_label=label)
    assert _skip_mk8s_gpu_deployment_testing_prompt(
        payload={
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "inputs": {"node_groups": {"worker": {"gpu": True}}},
                    }
                ]
            },
            "apps": {"charts": []},
            **payload,
        },
        entry=entry,
        full_path_label=label,
    )


def test_mk8s_component_prompts_sort_before_target_observability_prompts() -> None:
    required = {"cluster_name", "network_id", "subnet_id", "k8s_version"}
    required_prompts = {
        "deploy.targets[0].deployment_testing.mk8s_gpu.operator_readiness.enabled",
    }

    assert _prompt_path_sort_key(
        ("infra", "components", 0, "inputs", "cluster", "k8s_version"),
        required_leaf_names=required,
    ) < _prompt_path_sort_key(
        ("deploy", "targets", 0, "observability", "enabled"),
        required_leaf_names=required,
    )
    assert _prompt_path_sort_key(
        ("infra", "components", 0, "inputs", "node_groups", "system", "boot_disk", "type"),
        required_leaf_names=required,
    ) < _prompt_path_sort_key(
        ("deploy", "targets", 0, "observability", "kubernetes", "metrics", "enabled"),
        required_leaf_names=required,
    )
    assert _prompt_path_sort_key(
        (
            "infra",
            "components",
            0,
            "inputs",
            "node_group_defaults",
            "gpu",
            "infiniband_fabric",
        ),
        required_leaf_names=required,
        required_prompt_labels=required_prompts,
    ) < _prompt_path_sort_key(
        (
            "deploy",
            "targets",
            0,
            "deployment_testing",
            "mk8s_gpu",
            "operator_readiness",
            "enabled",
        ),
        required_leaf_names=required,
        required_prompt_labels=required_prompts,
    )
    assert _prompt_path_sort_key(
        ("infra", "components", 0, "inputs", "soperator", "worker_gpu_total_nodes"),
        required_leaf_names=required,
        required_prompt_labels=required_prompts,
    ) < _prompt_path_sort_key(
        (
            "deploy",
            "targets",
            0,
            "deployment_testing",
            "mk8s_gpu",
            "operator_readiness",
            "enabled",
        ),
        required_leaf_names=required,
        required_prompt_labels=required_prompts,
    )


def test_mk8s_k8s_version_sorts_before_public_endpoint_and_platform() -> None:
    labels = [
        "infra.components[0].inputs.cluster.public_endpoint",
        "infra.components[0].inputs.node_group_defaults.cpu.platform",
        "infra.components[0].inputs.cluster.k8s_version",
    ]

    sorted_labels = sorted(
        labels,
        key=lambda label: _prompt_path_sort_key(
            tuple(label.split(".")),
            required_leaf_names=set(),
        ),
    )

    assert sorted_labels == [
        "infra.components[0].inputs.cluster.k8s_version",
        "infra.components[0].inputs.cluster.public_endpoint",
        "infra.components[0].inputs.node_group_defaults.cpu.platform",
    ]


def test_run_component_field_wizard_can_skip_preselected_soperator_profile(monkeypatch) -> None:
    prompted_paths: list[str] = []
    payload = {
        "version": "v1",
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "profile": "nebius-cpu-v1",
                    "values": {"partitionProfile": "shape-default"},
                }
            ]
        },
    }
    soperator_entry = ComponentEntry(
        id="soperator",
        scope="apps",
        config_path="apps.slurm.soperator",
        description="soperator",
        wizard_fields={
            "profile": {"default": "nebius-gpu-v1", "write_default_to_config": True},
            "values.partitionProfile": {
                "default": "shape-default",
                "write_default_to_config": True,
            },
        },
    )

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda _label, default=True, allow_back=False: True,
    )
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra=set(),
        selected_apps={"soperator"},
        infra_entries=(),
        app_entries=(soperator_entry,),
        provider_lookup=None,
        skip_soperator_profile_prompt=True,
    )

    assert completed is True
    assert "apps.charts[0].profile" not in prompted_paths
    assert "apps.charts[0].values.partitionProfile" in prompted_paths
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["apps"]["charts"][0]["profile"] == "nebius-cpu-v1"


def test_soperator_managed_mk8s_skips_raw_node_group_prompts() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "values": {},
                }
            ]
        },
    }

    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_groups.system.node_count",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.platform",
    )
    payload["apps"]["charts"][0]["profile"] = "nebius-cpu-v1"
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.platform",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.cpu.platform",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_cpu_total_nodes",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_cpu_nodes_per_group",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_total_nodes",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_autoscaling.enabled",
    )
    payload["apps"]["charts"][0].pop("profile")
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_total_nodes",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_nodes_per_group",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_cpu_total_nodes",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_total_nodes",
    )
    payload["apps"]["charts"][0]["profile"] = "nebius-mixed-v1"
    for field in (
        "worker_cpu_total_nodes",
        "worker_cpu_nodes_per_group",
        "worker_gpu_total_nodes",
        "worker_gpu_nodes_per_group",
    ):
        assert not _skip_soperator_managed_mk8s_prompt(
            payload=payload,
            entry=entry,
            full_path_label=f"infra.components[0].inputs.soperator.{field}",
        )
    payload["apps"]["charts"][0].pop("profile")
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_autoscaling.enabled",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_autoscaling.min_node_count",
    )
    for field in (
        "system_node_count",
        "controller_node_count",
        "login_node_count",
        "accounting_node_count",
    ):
        assert not _skip_soperator_managed_mk8s_prompt(
            payload=payload,
            entry=entry,
            full_path_label=f"infra.components[0].inputs.soperator.{field}",
        )

    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.system_autoscaling.enabled",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.system_autoscaling.min_node_count",
    )
    soperator_inputs = payload["infra"]["components"][0]["inputs"].setdefault(
        "soperator",
        {},
    )
    soperator_inputs["system_autoscaling"] = {"enabled": True}
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.system_autoscaling.min_node_count",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.system_node_count",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_nodes_per_group",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_gpu_autoscaling.max_node_count"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_ephemeral_nodes.enabled",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        ),
    )
    soperator_inputs["worker_node_groups"] = {
        "worker-gpu": {
            "autoscaling": {"enabled": False},
            "ephemeral_nodes": {"enabled": False},
        },
    }
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.enabled"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.min_node_count"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".ephemeral_nodes.enabled"
        ),
    )
    soperator_inputs["worker_node_groups"]["worker-gpu"]["autoscaling"]["enabled"] = True
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.min_node_count"
        ),
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.max_node_count"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".ephemeral_nodes.enabled"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        ),
    )
    soperator_inputs["worker_node_groups"]["worker-gpu"]["ephemeral_nodes"]["enabled"] = True
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.enabled"
        ),
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.min_node_count"
        ),
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.max_node_count"
        ),
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        ),
    )
    soperator_inputs["worker_node_groups"]["worker-gpu"]["autoscaling"]["enabled"] = False
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".ephemeral_nodes.enabled"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.min_node_count"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        ),
    )

    payload["apps"]["charts"][0]["install_mode"] = "onboard-existing-cluster"
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.enabled"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_ephemeral_nodes.enabled",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_total_nodes",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.system_node_count",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.enabled"
        ),
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.platform",
    )
    app_entry = ComponentEntry(
        id="soperator",
        scope="apps",
        config_path="apps.slurm.soperator",
        description="soperator",
    )
    assert not _skip_soperator_install_mode_dependent_prompt(
        payload=payload,
        entry=app_entry,
        full_path_label="apps.charts[0].placements.worker",
    )
    payload["apps"]["charts"][0]["install_mode"] = "production-cluster"
    assert _skip_soperator_install_mode_dependent_prompt(
        payload=payload,
        entry=app_entry,
        full_path_label="apps.charts[0].placements.worker",
    )
    assert _skip_soperator_qos_configuration_prompt(
        payload=payload,
        entry=app_entry,
        full_path_label="apps.charts[0].values.qosConfiguration.enabled",
    )
    payload["apps"]["charts"][0]["values"] = {"partitionProfile": "with-qos-preemption"}
    assert not _skip_soperator_qos_configuration_prompt(
        payload=payload,
        entry=app_entry,
        full_path_label="apps.charts[0].values.qosConfiguration.enabled",
    )

    payload["apps"]["charts"] = []
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_total_nodes",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.system_node_count",
    )
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_group_defaults.gpu.platform",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.node_groups.system.node_count",
    )


def test_run_component_field_wizard_prompts_soperator_worker_controls_per_shard(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    prompt_currents: dict[str, list[object]] = {}
    prompt_required: dict[str, bool] = {}
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-mixed-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.soperator.worker_cpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "required": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_cpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "required": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "required": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "required": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
                "default": 300,
                "write_default_to_config": True,
                "type_hint": "number",
            },
        },
    )
    worker_size_answers = {
        "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 1,
        "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 1,
    }
    bulk_apply_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_apply_to_all"
    )
    bulk_enabled_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_autoscaling_enabled"
    )
    autoscaling_enabled_paths = {
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.enabled",
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
        ".autoscaling.enabled",
    }

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        prompt_currents.setdefault(path_label, []).append(current)
        prompt_required[path_label] = bool(_kwargs.get("required"))
        if path_label in worker_size_answers:
            return worker_size_answers[path_label], False
        if path_label == bulk_apply_path:
            return False, False
        if path_label in autoscaling_enabled_paths:
            return True, False
        if path_label.endswith(".autoscaling.enabled"):
            return False, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert bulk_apply_path in prompted_paths
    assert prompt_currents[bulk_apply_path] == [True]
    assert prompt_required[bulk_apply_path] is True
    assert bulk_enabled_path not in prompted_paths
    for path in worker_size_answers:
        assert prompt_required[path] is True
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu"
        ".autoscaling.enabled"
        not in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
        ".autoscaling.enabled"
        not in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.enabled"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-1"
        ".autoscaling.enabled"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-0"
        ".autoscaling.enabled"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
        ".autoscaling.enabled"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.min_node_count"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.max_node_count"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
        ".autoscaling.min_node_count"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
        ".autoscaling.max_node_count"
        in prompted_paths
    )
    assert not any(path.endswith(".ephemeral_nodes.enabled") for path in prompted_paths)
    worker_enabled_prompt_order = [
        path
        for path in prompted_paths
        if ".inputs.soperator.worker_node_groups." in path
        and path.endswith(".autoscaling.enabled")
    ]
    assert worker_enabled_prompt_order == [
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.enabled",
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-1"
        ".autoscaling.enabled",
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-0"
        ".autoscaling.enabled",
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
        ".autoscaling.enabled",
    ]
    for path in worker_enabled_prompt_order:
        assert prompt_required[path] is True
    for suffix in (".autoscaling.min_node_count", ".autoscaling.max_node_count"):
        assert (
            prompt_required[
                "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
                f"{suffix}"
            ]
            is True
        )
        assert (
            prompt_required[
                "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
                f"{suffix}"
            ]
            is True
        )
    assert prompted_paths.index(bulk_apply_path) < prompted_paths.index(
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.enabled"
    )
    assert prompted_paths.index(
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.enabled"
    ) < prompted_paths.index(
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.min_node_count"
    )
    assert prompted_paths.index(
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.max_node_count"
    ) < prompted_paths.index(
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-1"
        ".autoscaling.enabled"
    )
    assert (
        prompted_paths.index(
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
            ".autoscaling.enabled"
        )
        < prompted_paths.index(
            "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        )
    )

    updated_payload = yaml.safe_load(updated_yaml)
    worker_groups = updated_payload["infra"]["components"][0]["inputs"]["soperator"][
        "worker_node_groups"
    ]
    assert set(worker_groups) == {
        "worker-cpu-0",
        "worker-cpu-1",
        "worker-gpu-0",
        "worker-gpu-1",
    }
    assert worker_groups["worker-cpu-0"]["ephemeral_nodes"]["enabled"] is True
    assert worker_groups["worker-cpu-1"]["ephemeral_nodes"]["enabled"] is False
    assert worker_groups["worker-gpu-0"]["ephemeral_nodes"]["enabled"] is False
    assert worker_groups["worker-gpu-1"]["ephemeral_nodes"]["enabled"] is True
    assert worker_groups["worker-cpu-0"]["autoscaling"]["enabled"] is True
    assert worker_groups["worker-cpu-1"]["autoscaling"]["enabled"] is False
    assert worker_groups["worker-gpu-0"]["autoscaling"]["enabled"] is False
    assert worker_groups["worker-gpu-1"]["autoscaling"]["enabled"] is True
    assert (
        updated_payload["infra"]["components"][0]["inputs"]["soperator"][
            "worker_ephemeral_nodes"
        ]["suspend_time_seconds"]
        == 300
    )


def test_run_component_field_wizard_bulk_enables_soperator_worker_controls(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    prompt_currents: dict[str, list[object]] = {}
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-mixed-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.soperator.worker_cpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_cpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
                "default": 300,
                "write_default_to_config": True,
                "type_hint": "number",
            },
        },
    )
    worker_size_answers = {
        "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 3,
        "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 2,
        "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 3,
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 2,
    }
    bulk_apply_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_apply_to_all"
    )
    bulk_enabled_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_autoscaling_enabled"
    )

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        prompt_currents.setdefault(path_label, []).append(current)
        if path_label in worker_size_answers:
            return worker_size_answers[path_label], False
        if path_label == bulk_apply_path:
            return True, False
        if path_label == bulk_enabled_path:
            return True, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths.count(bulk_apply_path) == 1
    assert prompted_paths.count(bulk_enabled_path) == 1
    assert prompt_currents[bulk_apply_path] == [True]
    assert prompt_currents[bulk_enabled_path] == [False]
    assert not any(
        ".inputs.soperator.worker_node_groups.worker-" in path
        and ".autoscaling." in path
        for path in prompted_paths
    )
    assert not any(path.endswith(".ephemeral_nodes.enabled") for path in prompted_paths)
    assert (
        "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        in prompted_paths
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    worker_groups = inputs["soperator"]["worker_node_groups"]
    assert set(worker_groups) == {
        "worker-cpu-0",
        "worker-cpu-1",
        "worker-gpu-0",
        "worker-gpu-1",
    }
    expected_worker_max = {
        "worker-cpu-0": 2,
        "worker-cpu-1": 1,
        "worker-gpu-0": 2,
        "worker-gpu-1": 1,
    }
    for group_key, worker_group in worker_groups.items():
        assert worker_group["autoscaling"] == {
            "enabled": True,
            "min_node_count": 0,
            "max_node_count": expected_worker_max[group_key],
        }
        assert worker_group["ephemeral_nodes"]["enabled"] is True
    for group_key, node_group in inputs["node_groups"].items():
        if node_group.get("workload") == "worker":
            assert node_group["autoscaling"] == {
                "min_node_count": 0,
                "max_node_count": expected_worker_max[group_key],
            }
            assert "node_count" not in node_group
    assert inputs["soperator"]["worker_ephemeral_nodes"]["suspend_time_seconds"] == 300


def test_run_component_field_wizard_bulk_disables_soperator_worker_controls(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    prompt_currents: dict[str, list[object]] = {}
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-mixed-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.soperator.worker_cpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_cpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
                "default": 300,
                "write_default_to_config": True,
                "type_hint": "number",
            },
        },
    )
    worker_size_answers = {
        "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 1,
        "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 1,
    }
    bulk_apply_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_apply_to_all"
    )
    bulk_enabled_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_autoscaling_enabled"
    )

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        prompt_currents.setdefault(path_label, []).append(current)
        if path_label in worker_size_answers:
            return worker_size_answers[path_label], False
        if path_label == bulk_apply_path:
            return True, False
        if path_label == bulk_enabled_path:
            return False, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths.count(bulk_apply_path) == 1
    assert prompted_paths.count(bulk_enabled_path) == 1
    assert prompt_currents[bulk_apply_path] == [True]
    assert prompt_currents[bulk_enabled_path] == [False]
    assert not any(
        ".inputs.soperator.worker_node_groups.worker-" in path
        and ".autoscaling." in path
        for path in prompted_paths
    )
    assert not any(path.endswith(".ephemeral_nodes.enabled") for path in prompted_paths)
    assert (
        "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        not in prompted_paths
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    worker_groups = inputs["soperator"]["worker_node_groups"]
    assert set(worker_groups) == {
        "worker-cpu-0",
        "worker-cpu-1",
        "worker-gpu-0",
        "worker-gpu-1",
    }
    for worker_group in worker_groups.values():
        assert worker_group["autoscaling"] == {"enabled": False}
        assert worker_group["ephemeral_nodes"]["enabled"] is False
    for node_group in inputs["node_groups"].values():
        if node_group.get("workload") == "worker":
            assert "autoscaling" not in node_group
            assert node_group["node_count"] == 1
    assert "worker_ephemeral_nodes" not in inputs["soperator"]


def test_run_component_field_wizard_bulk_apply_false_keeps_per_shard_flow(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    prompt_currents: dict[str, list[object]] = {}
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-mixed-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.soperator.worker_cpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_cpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
                "default": 300,
                "write_default_to_config": True,
                "type_hint": "number",
            },
        },
    )
    worker_size_answers = {
        "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 1,
        "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 1,
    }
    bulk_apply_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_apply_to_all"
    )
    bulk_enabled_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_autoscaling_enabled"
    )

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        prompt_currents.setdefault(path_label, []).append(current)
        if path_label in worker_size_answers:
            return worker_size_answers[path_label], False
        if path_label == bulk_apply_path:
            return False, False
        if path_label.endswith(".autoscaling.enabled"):
            return False, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert bulk_apply_path in prompted_paths
    assert prompt_currents[bulk_apply_path] == [True]
    assert bulk_enabled_path not in prompted_paths
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu-0"
        ".autoscaling.enabled"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu-1"
        ".autoscaling.enabled"
        in prompted_paths
    )


def test_run_component_field_wizard_bulk_backtrack_to_disabled_clears_workers(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    prompt_currents: dict[str, list[object]] = {}
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-mixed-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.soperator.worker_cpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_cpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
                "default": 300,
                "write_default_to_config": True,
                "type_hint": "number",
            },
        },
    )
    worker_size_answers = {
        "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 1,
        "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 2,
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 1,
    }
    bulk_apply_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_apply_to_all"
    )
    bulk_enabled_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_autoscaling_enabled"
    )
    suspend_path = (
        "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
    )
    answers = {
        bulk_apply_path: [True],
        bulk_enabled_path: [True, False],
        suspend_path: [cli_module._WIZARD_BACKTRACK],
    }

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        prompt_currents.setdefault(path_label, []).append(current)
        if path_label in worker_size_answers:
            return worker_size_answers[path_label], False
        pending = answers.get(path_label)
        if pending:
            return pending.pop(0), False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompted_paths.count(bulk_apply_path) == 1
    assert prompted_paths.count(bulk_enabled_path) == 2
    assert prompt_currents[bulk_apply_path] == [True]
    assert prompt_currents[bulk_enabled_path] == [False, True]
    assert prompted_paths.count(suspend_path) == 1
    assert not any(
        ".inputs.soperator.worker_node_groups.worker-" in path
        and ".autoscaling." in path
        for path in prompted_paths
    )

    updated_payload = yaml.safe_load(updated_yaml)
    inputs = updated_payload["infra"]["components"][0]["inputs"]
    worker_groups = inputs["soperator"]["worker_node_groups"]
    for worker_group in worker_groups.values():
        assert worker_group["autoscaling"] == {"enabled": False}
        assert worker_group["ephemeral_nodes"]["enabled"] is False
    assert "worker_ephemeral_nodes" not in inputs["soperator"]


def test_run_component_field_wizard_bulk_scope_labels_for_cpu_and_gpu_profiles(
    monkeypatch,
) -> None:
    def _run_profile(
        *,
        profile: str,
        wizard_fields: dict[str, dict[str, object]],
        worker_size_answers: dict[str, int],
        expected_scope_path: str,
    ) -> list[str]:
        prompted_paths: list[str] = []
        payload = {
            "version": "v1",
            "infra": {
                "components": [
                    {
                        "id": "mk8s",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "inputs": {},
                    }
                ]
            },
            "apps": {
                "charts": [
                    {
                        "id": "soperator",
                        "instance_id": "cluster1",
                        "enabled": True,
                        "install_mode": "production-cluster",
                        "profile": profile,
                        "values": {},
                    }
                ]
            },
        }
        mk8s_entry = ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.mk8s",
            description="mk8s",
            source="../../platform-infra/modules/mk8s",
            wizard_fields=wizard_fields,
        )
        expected_enabled_path = expected_scope_path.replace(
            "_apply_to_all",
            "_autoscaling_enabled",
        )

        def _capture_continue_phase(
            label: str, *, default: bool = True, allow_back: bool = False
        ) -> bool:
            _ = default, allow_back
            return label.startswith("Configure 'mk8s")

        def _capture_prompt(path_label: str, current, **_kwargs):
            prompted_paths.append(path_label)
            if path_label in worker_size_answers:
                return worker_size_answers[path_label], False
            if path_label == expected_scope_path:
                return True, False
            if path_label == expected_enabled_path:
                return False, False
            return current, False

        monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
        monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
        monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
        monkeypatch.setattr(
            "nebius_cxcli.cli._wizard_continue_phase",
            _capture_continue_phase,
        )
        monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

        _updated_yaml, completed = _run_component_field_wizard(
            config_yaml=yaml.safe_dump(payload, sort_keys=False),
            selected_infra={"cluster1"},
            selected_apps=set(),
            infra_entries=(mk8s_entry,),
            app_entries=(),
            provider_lookup=None,
        )

        assert completed is True
        return prompted_paths

    cpu_fields = {
        "inputs.soperator.worker_cpu_total_nodes": {
            "default": 1,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.worker_cpu_nodes_per_group": {
            "default": 100,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
            "default": 300,
            "write_default_to_config": True,
            "type_hint": "number",
        },
    }
    cpu_prompted_paths = _run_profile(
        profile="nebius-cpu-v1",
        wizard_fields=cpu_fields,
        worker_size_answers={
            "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 2,
            "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 1,
        },
        expected_scope_path=(
            "infra.components[0].inputs.soperator.worker_node_groups."
            "all_cpu_worker_shards_apply_to_all"
        ),
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_cpu_worker_shards_apply_to_all"
        in cpu_prompted_paths
    )
    assert not any("all_gpu_worker_shards" in path for path in cpu_prompted_paths)
    assert not any("all_worker_shards" in path for path in cpu_prompted_paths)

    gpu_fields = {
        "inputs.soperator.worker_gpu_total_nodes": {
            "default": 1,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.worker_gpu_nodes_per_group": {
            "default": 100,
            "write_default_to_config": True,
            "type_hint": "number",
        },
        "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
            "default": 300,
            "write_default_to_config": True,
            "type_hint": "number",
        },
    }
    gpu_prompted_paths = _run_profile(
        profile="nebius-gpu-v1",
        wizard_fields=gpu_fields,
        worker_size_answers={
            "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 2,
            "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 1,
        },
        expected_scope_path=(
            "infra.components[0].inputs.soperator.worker_node_groups."
            "all_gpu_worker_shards_apply_to_all"
        ),
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_gpu_worker_shards_apply_to_all"
        in gpu_prompted_paths
    )
    assert not any("all_cpu_worker_shards" in path for path in gpu_prompted_paths)
    assert not any("all_worker_shards" in path for path in gpu_prompted_paths)


def test_run_component_field_wizard_defaults_worker_max_to_shard_capacity(
    monkeypatch,
) -> None:
    prompt_currents: dict[str, list[object]] = {}
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-mixed-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
        wizard_fields={
            "inputs.soperator.worker_cpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_cpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_total_nodes": {
                "default": 1,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_gpu_nodes_per_group": {
                "default": 100,
                "write_default_to_config": True,
                "type_hint": "number",
            },
            "inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds": {
                "default": 300,
                "write_default_to_config": True,
                "type_hint": "number",
            },
        },
    )
    worker_size_answers = {
        "infra.components[0].inputs.soperator.worker_cpu_total_nodes": 4,
        "infra.components[0].inputs.soperator.worker_cpu_nodes_per_group": 100,
        "infra.components[0].inputs.soperator.worker_gpu_total_nodes": 3,
        "infra.components[0].inputs.soperator.worker_gpu_nodes_per_group": 100,
    }
    bulk_apply_path = (
        "infra.components[0].inputs.soperator.worker_node_groups."
        "all_worker_shards_apply_to_all"
    )
    autoscaling_enabled_paths = {
        "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu"
        ".autoscaling.enabled",
        "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
        ".autoscaling.enabled",
    }

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompt_currents.setdefault(path_label, []).append(current)
        if path_label in worker_size_answers:
            return worker_size_answers[path_label], False
        if path_label == bulk_apply_path:
            return False, False
        if path_label in autoscaling_enabled_paths:
            return True, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert prompt_currents[bulk_apply_path] == [True]
    assert (
        prompt_currents[
            "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu"
            ".autoscaling.min_node_count"
        ]
        == [0]
    )
    assert (
        prompt_currents[
            "infra.components[0].inputs.soperator.worker_node_groups.worker-cpu"
            ".autoscaling.max_node_count"
        ]
        == [4]
    )
    assert (
        prompt_currents[
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.min_node_count"
        ]
        == [0]
    )
    assert (
        prompt_currents[
            "infra.components[0].inputs.soperator.worker_node_groups.worker-gpu"
            ".autoscaling.max_node_count"
        ]
        == [3]
    )

    updated_payload = yaml.safe_load(updated_yaml)
    node_groups = updated_payload["infra"]["components"][0]["inputs"]["node_groups"]
    assert node_groups["worker-cpu"]["autoscaling"] == {
        "min_node_count": 0,
        "max_node_count": 4,
    }
    assert node_groups["worker-gpu"]["autoscaling"] == {
        "min_node_count": 0,
        "max_node_count": 3,
    }


def test_run_component_field_wizard_clears_worker_ephemeral_when_autoscaling_disabled(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "soperator": {
                            "worker_gpu_total_nodes": 1,
                            "worker_gpu_nodes_per_group": 100,
                            "worker_node_groups": {
                                "worker": {
                                    "autoscaling": {
                                        "enabled": True,
                                        "min_node_count": 1,
                                        "max_node_count": 1,
                                    },
                                    "ephemeral_nodes": {"enabled": True},
                                },
                            },
                        },
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-gpu-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
    )

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        if (
            path_label
            == "infra.components[0].inputs.soperator.worker_node_groups.worker"
            ".autoscaling.enabled"
        ):
            return False, False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker"
        ".autoscaling.enabled"
        in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker"
        ".ephemeral_nodes.enabled"
        not in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        not in prompted_paths
    )
    updated_payload = yaml.safe_load(updated_yaml)
    worker_gpu = updated_payload["infra"]["components"][0]["inputs"]["soperator"][
        "worker_node_groups"
    ]["worker"]
    assert worker_gpu["autoscaling"]["enabled"] is False
    assert "min_node_count" not in worker_gpu["autoscaling"]
    assert "max_node_count" not in worker_gpu["autoscaling"]
    assert worker_gpu["ephemeral_nodes"]["enabled"] is False


def test_run_component_field_wizard_restores_worker_ephemeral_after_autoscaling_backtrack(
    monkeypatch,
) -> None:
    prompted_paths: list[str] = []
    payload = {
        "version": "v1",
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "soperator": {
                            "worker_gpu_total_nodes": 1,
                            "worker_gpu_nodes_per_group": 100,
                            "worker_node_groups": {
                                "worker": {
                                    "autoscaling": {"enabled": False},
                                    "ephemeral_nodes": {"enabled": False},
                                },
                            },
                        },
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-gpu-v1",
                    "values": {},
                }
            ]
        },
    }
    mk8s_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
    )
    answers = {
        "infra.components[0].inputs.soperator.worker_node_groups.worker"
        ".autoscaling.enabled": [True, False],
        "infra.components[0].inputs.soperator.worker_node_groups.worker"
        ".autoscaling.min_node_count": [cli_module._WIZARD_BACKTRACK],
    }

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label.startswith("Configure 'mk8s")

    def _capture_prompt(path_label: str, current, **_kwargs):
        prompted_paths.append(path_label)
        pending = answers.get(path_label)
        if pending:
            return pending.pop(0), False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"cluster1"},
        selected_apps=set(),
        infra_entries=(mk8s_entry,),
        app_entries=(),
        provider_lookup=None,
    )

    assert completed is True
    assert (
        prompted_paths.count(
            "infra.components[0].inputs.soperator.worker_node_groups.worker"
            ".autoscaling.enabled"
        )
        == 2
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker"
        ".ephemeral_nodes.enabled"
        not in prompted_paths
    )
    assert (
        prompted_paths.count(
            "infra.components[0].inputs.soperator.worker_node_groups.worker"
            ".autoscaling.min_node_count"
        )
        == 1
    )
    assert (
        "infra.components[0].inputs.soperator.worker_node_groups.worker"
        ".autoscaling.max_node_count"
        not in prompted_paths
    )
    assert (
        "infra.components[0].inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds"
        not in prompted_paths
    )
    updated_payload = yaml.safe_load(updated_yaml)
    worker_gpu = updated_payload["infra"]["components"][0]["inputs"]["soperator"][
        "worker_node_groups"
    ]["worker"]
    assert worker_gpu["autoscaling"]["enabled"] is False
    assert worker_gpu["ephemeral_nodes"]["enabled"] is False


def test_prune_mk8s_node_group_defaults_for_non_soperator_target() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_groups": {
                            "system": {
                                "platform": "cpu-d3",
                                "preset": "32vcpu-128gb",
                                "node_count": 2,
                            }
                        },
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                            }
                        },
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    assert "node_group_defaults" not in payload["infra"]["components"][0]["inputs"]


def test_keep_mk8s_node_group_defaults_for_soperator_target() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "values": {},
                }
            ]
        },
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    assert "node_group_defaults" in payload["infra"]["components"][0]["inputs"]


def test_prune_gpu_node_group_defaults_for_cpu_only_soperator_profile() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "cpu": {
                                "platform": "cpu-d3",
                                "preset": "32vcpu-128gb",
                            },
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                            },
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "production-cluster",
                    "profile": "nebius-cpu-v1",
                    "values": {},
                }
            ]
        },
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    defaults = payload["infra"]["components"][0]["inputs"]["node_group_defaults"]
    assert defaults == {"cpu": {"platform": "cpu-d3", "preset": "32vcpu-128gb"}}


def test_prune_mk8s_node_group_defaults_for_soperator_onboarding_target() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "8gpu-128vcpu-1600gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "install_mode": "onboard-existing-cluster",
                    "values": {},
                }
            ]
        },
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    assert "node_group_defaults" not in payload["infra"]["components"][0]["inputs"]


def test_keep_mk8s_node_group_defaults_when_custom_module_declares_input(monkeypatch) -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="custom mk8s",
        source="../custom-mk8s",
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_node_group_defaults.module_variables",
        lambda _source: (type("ModuleVariable", (), {"name": "node_group_defaults"})(),),
    )

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    assert "node_group_defaults" in payload["infra"]["components"][0]["inputs"]


def test_keep_mk8s_node_group_defaults_when_module_introspection_fails(
    monkeypatch,
    caplog,
) -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="custom mk8s",
        source="../custom-mk8s",
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_node_group_defaults.module_variables",
        lambda _source: (_ for _ in ()).throw(RuntimeError("metadata failed")),
    )
    caplog.set_level("WARNING", logger="nebius_cxcli.mk8s_node_group_defaults")

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    assert "node_group_defaults" in payload["infra"]["components"][0]["inputs"]
    assert "Unable to inspect module inputs" in caplog.text


def test_keep_mk8s_node_group_defaults_when_catalog_seeds_helper() -> None:
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "gpu": {
                                "platform": "gpu-h100-sxm",
                                "preset": "1gpu-16vcpu-200gb",
                            }
                        }
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="catalog mk8s",
        defaults=(
            ComponentDefault(
                target_path="inputs.node_group_defaults.gpu.platform",
                value="gpu-h100-sxm",
            ),
        ),
    )

    _prune_mk8s_node_group_defaults_without_soperator(payload, infra_entries=(entry,))

    assert "node_group_defaults" in payload["infra"]["components"][0]["inputs"]


def test_sfs_multi_filesystem_profile_skips_single_filesystem_prompts() -> None:
    entry = ComponentEntry(
        id="sfs",
        scope="infra",
        config_path="infra.sfs",
        description="sfs",
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "sfs",
                    "instance_id": "sfs",
                    "enabled": True,
                    "inputs": {
                        "filesystems": {
                            "jail": {
                                "name": "cluster1-jail",
                                "size_gib": 1024,
                            }
                        }
                    },
                }
            ]
        }
    }

    assert _skip_sfs_single_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.name",
    )
    assert _skip_sfs_single_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.size_gib",
    )
    assert _skip_sfs_single_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.block_size_kib",
    )
    assert _skip_sfs_single_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.forbid_deletion",
    )
    assert not _skip_sfs_multi_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.filesystems.jail.name",
    )
    assert not _skip_sfs_single_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.type",
    )


def test_sfs_layout_filesystem_prompts_are_skipped_without_filesystems_map() -> None:
    entry = ComponentEntry(
        id="sfs",
        scope="infra",
        config_path="infra.sfs",
        description="sfs",
    )
    payload = {
        "infra": {
            "components": [
                {
                    "id": "sfs",
                    "instance_id": "sfs",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        }
    }

    assert _skip_sfs_multi_filesystem_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.filesystems.jail.name",
    )


def test_run_component_field_wizard_announces_observability_app_at_enable_prompt(
    monkeypatch,
) -> None:
    events: list[str] = []

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = allow_back
        events.append(f"phase:{label}")
        return label == "Configure 'mk8s' component fields now?"

    def _capture_prompt(path_label: str, current, **_kwargs):
        events.append(f"prompt:{path_label}")
        if path_label == "deploy.targets[0].observability.enabled":
            return True, False
        return current, False

    def _capture_print(message="", *_args, **_kwargs) -> None:
        text = str(message)
        if "Adjusted component selection:" in text:
            events.append(f"print:{text}")

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)
    monkeypatch.setattr(cli_module.console, "print", _capture_print)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(_mk8s_observability_payload(), sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(_mk8s_observability_wizard_entry(),),
        app_entries=(_observability_agent_entry(), _grafana_entry(), _gateway_entry()),
        provider_lookup=None,
    )

    assert completed is True
    adjusted_index = next(index for index, event in enumerate(events) if event.startswith("print:"))
    assert events.index("prompt:infra.components[0].inputs.cluster.public_endpoint") < events.index(
        "prompt:deploy.targets[0].observability.enabled"
    )
    assert events.index("prompt:deploy.targets[0].observability.enabled") < adjusted_index
    assert adjusted_index < events.index(
        "phase:Configure 'nebius-observability-agent on mk8s' component fields now?"
    )
    assert "answering 'n' keeps the selected app defaults" in events[adjusted_index]

    updated_payload = yaml.safe_load(updated_yaml)
    enabled_apps = {row["id"]: row for row in updated_payload["apps"]["charts"]}
    assert enabled_apps["nebius-observability-agent"]["enabled"] is True
    assert enabled_apps["grafana"]["enabled"] is True
    assert enabled_apps["gateway-helm"]["enabled"] is True


def test_run_component_field_wizard_defaults_observability_enabled_from_selected_apps(
    monkeypatch,
) -> None:
    observed_defaults: list[object] = []
    payload = _mk8s_observability_payload()
    payload["apps"] = {
        "charts": [
            {
                "id": "nebius-observability-agent",
                "instance_id": "mk8s",
                "enabled": True,
            }
        ]
    }

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        return label == "Configure 'mk8s' component fields now?"

    def _capture_prompt(path_label: str, current, **_kwargs):
        if path_label == "deploy.targets[0].observability.enabled":
            observed_defaults.append(current)
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _capture_prompt)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps={"nebius-observability-agent"},
        infra_entries=(_mk8s_observability_wizard_entry(),),
        app_entries=(_observability_agent_entry(), _grafana_entry(), _gateway_entry()),
        provider_lookup=None,
    )

    assert completed is True
    assert observed_defaults == [True]
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["deploy"]["targets"][0]["observability"]["enabled"] is True


def test_app_chart_skip_defaults_preview_lines_are_concise_and_redacted() -> None:
    entry = ComponentEntry(
        id="soperator",
        scope="apps",
        config_path="apps.slurm.soperator",
        description="soperator",
        group="slurm",
        default_release_timeout="90m",
        defaults=(
            ComponentDefault("values.soperator-activechecks.enabled", False),
            ComponentDefault("values.soperator-activechecks.waitForChecks.enabled", False),
            ComponentDefault("values.soperator-notifier.slack.webhookUrl", "secret-url"),
        ),
    )
    lines = _app_chart_skip_defaults_preview_lines(
        {
            "namespace": "soperator",
            "release-name": "soperator",
            "install_mode": "production-cluster",
            "profile": "nebius-gpu-v1",
            "values": {
                "clusterName": "soperator-cluster1",
                "partitionProfile": "shape-default",
                "topologyProfile": "disabled",
                "volume": {
                    "jail": {"size": "2048Gi"},
                    "controllerSpool": {"size": "128Gi"},
                    "accounting": {"enabled": True, "size": "128Gi"},
                },
                "sfs": {
                    "filesystems": {
                        "jail": {"size_gib": 2048},
                        "controller-spool": {"size_gib": 128},
                        "accounting": {"size_gib": 128},
                    }
                },
            },
        },
        entry=entry,
    )

    assert len(lines) <= 4
    preview = " ".join(lines)
    assert "namespace=soperator" in preview
    assert "install_mode=production-cluster" in preview
    assert "timeout=90m" in preview
    assert "values.volume={jail.size=2048Gi" in preview
    assert "controllerSpool.size=128Gi" in preview
    assert "accounting.size=128Gi" in preview
    assert "values.sfs={jail.size_gib=2048" in preview
    assert "secret-url" not in preview


def test_run_component_field_wizard_previews_app_defaults_before_skip_prompt(
    monkeypatch,
) -> None:
    events: list[str] = []

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = default, allow_back
        events.append(f"phase:{label}")
        return False

    def _capture_print(message="", *_args, **_kwargs) -> None:
        text = str(message)
        if "Default values if skipped" in text or "values.replicaCount" in text:
            events.append(f"print:{text}")

    entry = ComponentEntry(
        id="gateway-helm",
        scope="apps",
        config_path="apps.platform.gateway-helm",
        description="gateway",
        group="platform",
        source="oci://docker.io/envoyproxy/gateway-helm",
        version="1.0.0",
        default_namespace="envoy-gateway-system",
        default_release_name="envoy-gateway",
        default_release_timeout="10m",
        defaults=(
            ComponentDefault("values.replicaCount", 2),
            ComponentDefault("values.service.type", "LoadBalancer"),
        ),
    )
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "gateway-helm",
                    "instance_id": "gateway-helm",
                    "group": "platform",
                    "enabled": True,
                    "repo": "oci://docker.io/envoyproxy",
                    "version": "1.0.0",
                    "namespace": "envoy-gateway-system",
                    "release-name": "envoy-gateway",
                    "values": {},
                }
            ]
        },
    }

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)
    monkeypatch.setattr(cli_module.console, "print", _capture_print)

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra=set(),
        selected_apps={"gateway-helm"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )

    assert completed is True
    header_index = events.index("print:[dim]Default values if skipped:[/dim]")
    default_line_index = next(
        index for index, event in enumerate(events) if "values.replicaCount=2" in event
    )
    phase_index = events.index("phase:Configure 'gateway-helm' component fields now?")
    assert header_index < default_line_index < phase_index


def test_run_component_field_wizard_removes_backtracked_observability_app(
    monkeypatch,
) -> None:
    answers = {
        "deploy.targets[0].observability.enabled": [True, False],
        "deploy.targets[0].observability.kubernetes.logs.enabled": [cli_module._WIZARD_BACKTRACK],
    }

    def _answer_prompt(path_label: str, current, **_kwargs):
        pending = answers.get(path_label)
        if pending:
            return pending.pop(0), False
        return current, False

    monkeypatch.setattr("nebius_cxcli.cli.module_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.module_required_variables", lambda _source: ())
    monkeypatch.setattr("nebius_cxcli.cli.helm_chart_default_values", lambda **_kwargs: {})
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda label, default=True, allow_back=False: (
            label == "Configure 'mk8s' component fields now?"
        ),
    )
    monkeypatch.setattr("nebius_cxcli.cli._prompt_scalar_override", _answer_prompt)
    monkeypatch.setattr(cli_module.console, "print", lambda *_args, **_kwargs: None)

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(_mk8s_observability_payload(), sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(_mk8s_observability_wizard_entry(),),
        app_entries=(_observability_agent_entry(), _grafana_entry(), _gateway_entry()),
        provider_lookup=None,
    )

    assert completed is True
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["deploy"]["targets"][0]["observability"]["enabled"] is False
    assert updated_payload["apps"]["charts"] == []


def test_run_component_field_wizard_keeps_chart_defaults_virtual_on_stop(monkeypatch) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.cli.helm_chart_default_values",
        lambda **_kwargs: {
            "certgen": {
                "job": {
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._wizard_continue_phase",
        lambda _label, default=True, allow_back=False: True,
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli._prompt_scalar_override",
        lambda _path_label, current, **kwargs: (current, True),
    )
    entry = ComponentEntry(
        id="gateway-helm",
        scope="apps",
        config_path="apps.platform.gateway-helm",
        description="gateway",
        group="platform",
        source="oci://docker.io/envoyproxy/gateway-helm",
        version="1.0.0",
        default_namespace="envoy-gateway-system",
        default_release_name="envoy-gateway",
    )
    payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "gateway-helm",
                    "instance_id": "gateway-helm",
                    "group": "platform",
                    "enabled": True,
                    "repo": "oci://docker.io/envoyproxy",
                    "version": "1.0.0",
                    "namespace": "envoy-gateway-system",
                    "release-name": "envoy-gateway",
                    "values": {},
                }
            ]
        },
    }

    updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload, sort_keys=False),
        selected_infra=set(),
        selected_apps={"gateway-helm"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )

    assert completed is False
    updated_payload = yaml.safe_load(updated_yaml)
    assert updated_payload["apps"]["charts"][0]["values"] == {}


def test_run_component_field_wizard_uses_scope_specific_phase_defaults(monkeypatch) -> None:
    prompts: list[tuple[str, bool]] = []

    def _capture_continue_phase(
        label: str, *, default: bool = True, allow_back: bool = False
    ) -> bool:
        _ = allow_back
        prompts.append((label, default))
        return False

    monkeypatch.setattr("nebius_cxcli.cli._wizard_continue_phase", _capture_continue_phase)

    infra_entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
        source="../../platform-infra/modules/mk8s",
    )
    app_entry = ComponentEntry(
        id="gateway-helm",
        scope="apps",
        config_path="apps.platform.gateway-helm",
        description="gateway",
        group="platform",
        source="oci://docker.io/envoyproxy/gateway-helm",
        version="1.0.0",
        default_namespace="envoy-gateway-system",
        default_release_name="envoy-gateway",
    )

    infra_payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "inputs": {},
                }
            ]
        },
        "apps": {"charts": []},
    }
    app_payload = {
        "version": "v1",
        "client_info": {
            "client_name": "demo",
            "nebius": {
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": []},
        "apps": {
            "charts": [
                {
                    "id": "gateway-helm",
                    "instance_id": "gateway-helm",
                    "group": "platform",
                    "enabled": True,
                    "repo": "oci://docker.io/envoyproxy",
                    "version": "1.0.0",
                    "namespace": "envoy-gateway-system",
                    "release-name": "envoy-gateway",
                    "values": {},
                }
            ]
        },
    }

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(infra_payload, sort_keys=False),
        selected_infra={"mk8s"},
        selected_apps=set(),
        infra_entries=(infra_entry,),
        app_entries=(),
        provider_lookup=None,
    )
    assert completed is True

    _updated_yaml, completed = _run_component_field_wizard(
        config_yaml=yaml.safe_dump(app_payload, sort_keys=False),
        selected_infra=set(),
        selected_apps={"gateway-helm"},
        infra_entries=(),
        app_entries=(app_entry,),
        provider_lookup=None,
    )
    assert completed is True

    assert prompts == [
        ("Configure 'mk8s' component fields now?", True),
        ("Configure 'gateway-helm' component fields now?", False),
    ]
