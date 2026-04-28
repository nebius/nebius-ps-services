from __future__ import annotations

import json
from pathlib import Path

import yaml

from nebius_cxcli.components import component_entries, reset_component_entry_cache
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.grafana_runtime import read_grafana_status, write_grafana_status
from nebius_cxcli.inventory_ops import write_inventory
from nebius_cxcli.paths import resolve_project_paths, validate_path_alignment

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)


def _project_config_path(base: Path) -> Path:
    return base / "deployments" / "tenant-name-example" / "project-name-example" / "config.yaml"


def _starter_payload(*, selected_infra: set[str], selected_apps: set[str]) -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email="ops@example.com",
            selected_infra=selected_infra,
            selected_apps=selected_apps,
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    return payload


def _infra_component_row(payload: dict, component_id: str) -> dict:
    components = payload.get("infra", {}).get("components", [])
    if not isinstance(components, list):
        raise KeyError(component_id)
    for item in components:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")).strip().lower() == component_id:
            return item
    raise KeyError(component_id)


def _chart_row(payload: dict, chart_id: str) -> dict:
    charts = payload.get("apps", {}).get("charts", [])
    if not isinstance(charts, list):
        raise KeyError(chart_id)
    for item in charts:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "")).strip().lower() == chart_id:
            return item
    raise KeyError(chart_id)


def _enable_mk8s_observability(payload: dict, *, target_ref: str = "mk8s") -> None:
    deploy = payload.setdefault("deploy", {})
    assert isinstance(deploy, dict)
    targets = deploy.setdefault("targets", [{"instance_id": target_ref}])
    assert isinstance(targets, list)
    target = next(
        (
            item
            for item in targets
            if isinstance(item, dict) and item.get("instance_id") == target_ref
        ),
        None,
    )
    if target is None:
        target = {"instance_id": target_ref}
        targets.append(target)
    observability = target.setdefault("observability", {})
    assert isinstance(observability, dict)
    observability["enabled"] = True


def test_write_inventory_handles_dynamic_component_model(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"n8n"})
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs["cpu_nodes_count"] = 1
    mk8s_inputs["cpu_nodes_platform"] = "cpu-d3"
    mk8s_inputs["cpu_nodes_preset"] = "4vcpu-16gb"

    n8n_release = _chart_row(payload, "n8n")
    n8n_values = n8n_release.get("values", {})
    assert isinstance(n8n_values, dict)
    values_payload = n8n_values.setdefault("values", {})
    assert isinstance(values_payload, dict)
    values_payload["route"] = {"hostname": "n8n.example.com"}

    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    assert artifacts.markdown.exists()
    assert artifacts.markdown.name == "deploy-report.md"
    markdown = artifacts.markdown.read_text(encoding="utf-8")
    assert "## Infra\n\n- Client:" in markdown
    assert "- MK8s: `True`" in markdown
    assert "## Apps\n\n- Envoy Gateway:" in markdown
    assert "## Validations\n\n- No deploy-time validations configured." in markdown
    assert "- n8n: `True` (n8n.example.com)" in markdown


def test_write_inventory_lists_each_mk8s_cluster(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-gpu-operator", "nvidia-network-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "mk8s"
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "cluster_name": "cluster1",
            "cpu_nodes_count": 2,
            "cpu_nodes_platform": "cpu-d3",
            "cpu_nodes_preset": "32vcpu-128gb",
            "gpu_enabled": True,
            "gpu_node_groups": 1,
            "gpu_nodes_count_per_group": 2,
            "gpu_nodes_platform": "gpu-h100-sxm",
            "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
            "mk8s_cluster_public_endpoint": True,
            "infiniband_fabric": "fabric-6",
        }
    )
    cluster2 = yaml.safe_load(yaml.safe_dump(mk8s, sort_keys=False))
    cluster2["instance_id"] = "cluster2"
    cluster2["inputs"]["cluster_name"] = "cluster2"
    cluster2["inputs"]["gpu_nodes_preset"] = "1gpu-16vcpu-200gb"
    cluster2["inputs"].pop("infiniband_fabric", None)
    payload["infra"]["components"].append(cluster2)
    payload["apps"]["charts"].append(
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "cluster2",
            "enabled": True,
            "target_ref": "cluster2",
            "values": {},
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert (
        "- MK8s cluster `mk8s` (`cluster1`): CPU `2` node(s) at "
        "`cpu-d3/32vcpu-128gb`; GPU `1x2` node(s) at "
        "`gpu-h100-sxm/8gpu-128vcpu-1600gb`; fabric `fabric-6`; "
        "public endpoint `True`"
    ) in markdown
    assert (
        "- MK8s cluster `cluster2` (`cluster2`): CPU `2` node(s) at "
        "`cpu-d3/32vcpu-128gb`; GPU `1x2` node(s) at "
        "`gpu-h100-sxm/1gpu-16vcpu-200gb`; fabric `none`; public endpoint `True`"
    ) in markdown


def test_write_inventory_includes_observability_endpoint_contract(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"nvidia-gpu-operator"})
    _enable_mk8s_observability(payload)
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs["gpu_enabled"] = True
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "## Observability Write Endpoints" in markdown
    assert "## Observability Read Endpoints" in markdown
    assert (
        "- Metrics read (Prometheus, Nebius service metrics): "
        "`https://read.monitoring.api.nebius.cloud/projects/"
        "project-456/service-provider/prometheus`"
    ) in markdown
    assert (
        "https://read.monitoring.api.nebius.cloud/projects/project-456/service-provider/prometheus"
    ) in markdown
    assert (
        "- Metrics read (federate, `gpu` bucket): "
        "`https://read.monitoring.api.nebius.cloud/projects/"
        "project-456/buckets/gpu/prometheus/federate`"
    ) in markdown
    assert "- Metrics read (federate, `msp` bucket): " not in markdown
    assert "/buckets/<service-provider>/" not in markdown
    assert (
        "https://write.monitoring.eu-north1.nebius.cloud/projects/"
        "project-456/opentelemetry/v1/metrics"
    ) in markdown
    assert "https://write.logging.eu-north1.nebius.cloud" in markdown
    assert "dns:///write.tracing.eu-north1.nebius.cloud:443" in markdown
    assert "## Grafana" in markdown
    assert "- Target `mk8s` Grafana: `pending`" in markdown
    assert "- Target `mk8s` metrics: `pending`" in markdown
    assert (
        "- Pending Grafana links are populated after `deploy` or `flux apply` can "
        "read each target Gateway/LoadBalancer status."
    ) in markdown
    assert "Datasources are provisioned in Grafana with server/proxy access" in markdown
    assert (
        "- Bucket note: `service-provider` is a literal path segment for Nebius service "
        "metrics. Only the federation template placeholder `<service-provider>` should be "
        "replaced. This deployment shows `compute`, `nbs`, `gpu` bucket URLs."
    ) in markdown
    assert (
        "- Log bucket note: Nebius service logs are selected with the Loki `__bucket__` "
        "label. This deployment has `sp_mk8s_control_plane`, `sp_mk8s_audit_logs` "
        "service log buckets."
    ) in markdown
    assert "## Read Endpoint Probe URLs" not in markdown
    assert "Metrics API probe" not in markdown
    assert "Logs API probe" not in markdown
    assert "Traces API probe" not in markdown
    assert "Bearer <observability static token or IAM token>" in markdown


def test_write_inventory_includes_live_grafana_urls_when_status_exists(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    _enable_mk8s_observability(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    (paths.inventory_dir / "grafana-status.json").write_text(
        json.dumps(
            {
                "grafana": [
                    {
                        "target_ref": "mk8s",
                        "namespace": "observability",
                        "admin_secret_name": "nebius-cxcli-grafana-admin",
                        "admin_user": "admin",
                        "admin_password_key": "admin-password",
                        "kube_context": "nebius-cluster2-mk8scluster-123-external",
                        "base_url": "http://203.0.113.10/",
                        "metrics_url_kind": "dashboard",
                        "metrics_url": "http://203.0.113.10/goto/metrics123?orgId=1",
                        "logs_url_kind": "dashboard",
                        "logs_url": "http://203.0.113.10/goto/logs123?orgId=1",
                        "traces_url_kind": "dashboard",
                        "traces_url": "http://203.0.113.10/goto/traces123?orgId=1",
                        "dashboards_url": "http://203.0.113.10/dashboards",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "- Target `mk8s` Grafana: [Open Grafana](http://203.0.113.10/)" in markdown
    assert (
        "- Target `mk8s` metrics: "
        "[Open metrics dashboard](http://203.0.113.10/goto/metrics123?orgId=1)"
    ) in markdown
    assert (
        "- Target `mk8s` logs: [Open logs dashboard](http://203.0.113.10/goto/logs123?orgId=1)"
    ) in markdown
    assert (
        "- Target `mk8s` traces: "
        "[Open traces dashboard](http://203.0.113.10/goto/traces123?orgId=1)"
    ) in markdown
    assert "- Target `mk8s` credentials: user `admin`; password command:" in markdown
    assert (
        "- Target `mk8s` credentials: user `admin`; password command:\n\n"
        "```bash\n"
        "printf '%s\\n' \"$(kubectl --context=nebius-cluster2-mk8scluster-123-external "
        "-n observability get secret "
        "nebius-cxcli-grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)\"\n"
        "```\n"
    ) in markdown
    assert (
        "- Prometheus datasources are catalog-bound: "
        "`Nebius Services` uses `Metrics read (Prometheus, Nebius service metrics)`; "
        "`Nebius User Metrics` uses `Metrics read (Prometheus, user-ingested metrics)`."
    ) in markdown
    assert (
        "- Report links open the catalog-bound dashboards for Metrics, Logs, or Traces "
        "when Grafana has imported them; otherwise they fall back to the matching "
        "Explore view."
    ) in markdown


def test_write_inventory_lists_pending_grafana_links_per_target(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"]["cluster_name"] = "cluster1"
    cluster2 = yaml.safe_load(yaml.safe_dump(mk8s, sort_keys=False))
    cluster2["instance_id"] = "cluster2"
    cluster2["inputs"]["cluster_name"] = "cluster2"
    payload["infra"]["components"].append(cluster2)
    payload["deploy"] = {"targets": []}
    _enable_mk8s_observability(payload, target_ref="cluster1")
    _enable_mk8s_observability(payload, target_ref="cluster2")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "- Target `cluster1` Grafana: `pending`" in markdown
    assert "- Target `cluster2` Grafana: `pending`" in markdown
    assert "- Target `cluster1` credentials: user `admin`; password command:" in markdown
    assert "- Target `cluster2` credentials: user `admin`; password command:" in markdown


def test_write_inventory_ignores_runtime_grafana_status_for_removed_target(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s["inputs"]["cluster_name"] = "cluster1"
    payload["deploy"] = {"targets": []}
    _enable_mk8s_observability(payload, target_ref="cluster1")
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    write_grafana_status(
        paths,
        (
            {
                "target_ref": "cluster2",
                "namespace": "observability",
                "release_name": "grafana",
                "base_url": "http://203.0.113.20/",
            },
        ),
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "- Target `cluster1` Grafana: `pending`" in markdown
    assert "cluster2" not in markdown
    assert "203.0.113.20" not in markdown


def test_write_grafana_status_can_preserve_existing_target_statuses(tmp_path: Path) -> None:
    paths = resolve_project_paths(_project_config_path(tmp_path))
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    write_grafana_status(
        paths,
        (
            {
                "target_ref": "cluster1",
                "namespace": "observability",
                "release_name": "grafana",
                "base_url": "http://203.0.113.10/",
            },
        ),
    )
    write_grafana_status(
        paths,
        (
            {
                "target_ref": "cluster2",
                "namespace": "observability",
                "release_name": "grafana",
                "base_url": "http://203.0.113.20/",
            },
        ),
        preserve_existing=True,
    )

    statuses = read_grafana_status(paths)

    assert [item["target_ref"] for item in statuses] == ["cluster1", "cluster2"]
    assert [item["base_url"] for item in statuses] == [
        "http://203.0.113.10/",
        "http://203.0.113.20/",
    ]


def test_write_inventory_includes_msp_federation_bucket_when_postgresql_enabled(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s", "managed-postgresql"}, selected_apps=set())
    _enable_mk8s_observability(payload)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "- Managed PostgreSQL: `True`" in markdown
    assert (
        "- Metrics read (federate, `msp` bucket): "
        "`https://read.monitoring.api.nebius.cloud/projects/"
        "project-456/buckets/msp/prometheus/federate`"
    ) in markdown


def test_write_inventory_omits_disabled_observability_signal_endpoints(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": "mk8s",
                "observability": {
                    "enabled": True,
                    "kubernetes": {
                        "logs": {"enabled": False},
                        "metrics": {"enabled": True},
                        "traces": {"enabled": False},
                    },
                },
            }
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "## Observability Write Endpoints" in markdown
    assert "## Observability Read Endpoints" in markdown
    assert "Metrics write (OTLP HTTP/protobuf)" in markdown
    assert "Logs read (Loki)" in markdown
    assert "Logs write (bundled agent gRPC)" not in markdown
    assert "Traces read (Tempo)" not in markdown
    assert "Traces write (OTLP gRPC)" not in markdown
    assert "## Read Endpoint Probe URLs" not in markdown
    assert "Logs API probe" not in markdown
    assert "Traces API probe" not in markdown


def test_write_inventory_includes_vm_observability_read_paths_and_managed_write_notes(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vm"}, selected_apps=set())
    payload.setdefault("deploy", {}).setdefault("observability", {})["enabled"] = True
    payload["deploy"]["observability"].setdefault("vm", {}).setdefault("logs", {})["enabled"] = True
    vm = _infra_component_row(payload, "vm")
    vm_inputs = vm.setdefault("inputs", {})
    assert isinstance(vm_inputs, dict)
    vm_inputs.update(
        {
            "parent_id": "project-456",
            "subnet_id": "subnet-123",
            "platform": "cpu-d3",
            "preset": "2vcpu-8gb",
            "source_image_family": "ubuntu24.04-driverless",
            "ssh_user_name": "ubuntu",
            "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "VM monitoring agent: `True`" in markdown
    assert "VM journald logs (systemd services): `True`" in markdown
    assert "Metrics read (Prometheus, Nebius service metrics)" in markdown
    assert "Logs read (Loki)" in markdown
    assert "Metrics write (VM monitoring agent)" in markdown
    assert "Logs write (VM monitoring agent)" in markdown
    assert "Logs write (bundled agent gRPC)" not in markdown


def test_write_inventory_includes_vm_metrics_even_when_project_observability_is_off(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vm"}, selected_apps=set())
    payload.setdefault("deploy", {}).setdefault("observability", {})["enabled"] = False
    vm = _infra_component_row(payload, "vm")
    vm_inputs = vm.setdefault("inputs", {})
    assert isinstance(vm_inputs, dict)
    vm_inputs.update(
        {
            "parent_id": "project-456",
            "subnet_id": "subnet-123",
            "platform": "cpu-d3",
            "preset": "2vcpu-8gb",
            "source_image_family": "ubuntu24.04-driverless",
            "ssh_user_name": "ubuntu",
            "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "VM monitoring agent: `True`" in markdown
    assert "## Observability Write Endpoints" in markdown
    assert "## Observability Read Endpoints" in markdown
    assert "Metrics read (Prometheus, Nebius service metrics)" in markdown
    assert "Metrics write (VM monitoring agent)" in markdown
    assert "VM journald logs (systemd services): `False`" in markdown


def test_write_inventory_merges_validation_status_into_deploy_report(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    (paths.inventory_dir / "gpu-visibility-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "selected_node_count": 1,
                "total_gpu_node_count": 1,
                "passed_node_count": 1,
                "skipped_node_count": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    artifacts = write_inventory(
        config,
        paths,
        validations=[
            {
                "kind": "mk8s_gpu_visibility",
                "name": "GPU Visibility test",
                "report_file": "gpu-visibility-report.json",
            }
        ],
    )

    markdown = artifacts.markdown.read_text(encoding="utf-8")
    assert "## Infra" in markdown
    assert "## Apps" in markdown
    assert "## Validations" in markdown
    assert "- Overall status: `PASS`" in markdown
    assert "### GPU Visibility test" in markdown
    assert "- Detail report: `gpu-visibility-report.json`" in markdown
    assert not markdown.endswith("\n\n")
