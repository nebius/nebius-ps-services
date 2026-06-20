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
    _skip_mk8s_gpu_validation_prompt,
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
        "deploy": {"targets": [{"instance_id": "cluster1", "validations": {"mk8s_gpu": {}}}]},
    }

    assert not _skip_mk8s_gpu_validation_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].validations.mk8s_gpu.operator_readiness.enabled",
    )
    assert not _skip_mk8s_gpu_validation_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].validations.mk8s_gpu.gpu_visibility.enabled",
    )
    assert not _skip_mk8s_gpu_validation_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].validations.mk8s_gpu.nccl.enabled",
    )
    payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]["gpu_visibility"] = {"enabled": True}
    payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]["nccl"] = {"enabled": True}
    assert not _skip_mk8s_gpu_validation_prompt(
        payload=payload,
        entry=entry,
        full_path_label="deploy.targets[0].validations.mk8s_gpu.nccl.max_nodes",
    )


def test_mk8s_gpu_validation_max_nodes_required_only_when_section_enabled() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.mk8s",
        description="mk8s",
    )
    label = "deploy.targets[0].validations.mk8s_gpu.gpu_visibility.max_nodes"
    payload = {
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "validations": {"mk8s_gpu": {"gpu_visibility": {"enabled": True}}},
                }
            ]
        }
    }

    assert _dynamic_required_prompt(payload=payload, entry=entry, full_path_label=label)
    payload["deploy"]["targets"][0]["validations"]["mk8s_gpu"]["gpu_visibility"]["enabled"] = False
    assert not _dynamic_required_prompt(payload=payload, entry=entry, full_path_label=label)
    assert _skip_mk8s_gpu_validation_prompt(
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
        "deploy.targets[0].validations.mk8s_gpu.operator_readiness.enabled",
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
            "validations",
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
            "validations",
            "mk8s_gpu",
            "operator_readiness",
            "enabled",
        ),
        required_leaf_names=required,
        required_prompt_labels=required_prompts,
    )


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
    assert not _skip_soperator_managed_mk8s_prompt(
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
    soperator_inputs["worker_gpu_autoscaling"] = {"enabled": True}
    assert _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_total_nodes",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.soperator.worker_gpu_nodes_per_group",
    )
    assert not _skip_soperator_managed_mk8s_prompt(
        payload=payload,
        entry=entry,
        full_path_label=(
            "infra.components[0].inputs.soperator.worker_gpu_autoscaling.max_node_count"
        ),
    )
    assert not _skip_soperator_managed_mk8s_prompt(
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
    soperator_inputs["worker_ephemeral_nodes"] = {"enabled": True}
    assert not _skip_soperator_managed_mk8s_prompt(
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
                    "jail": {"size": "1024Gi"},
                    "controllerSpool": {"size": "128Gi"},
                    "accounting": {"enabled": True, "size": "128Gi"},
                },
                "sfs": {
                    "filesystems": {
                        "jail": {"size_gib": 1024},
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
    assert "values.volume={jail.size=1024Gi" in preview
    assert "controllerSpool.size=128Gi" in preview
    assert "accounting.size=128Gi" in preview
    assert "values.sfs={jail.size_gib=1024" in preview
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
