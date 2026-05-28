from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import nebius_cxcli.inventory_ops as inventory_ops
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


def _align_infra_resource_name(
    payload: dict,
    row: dict,
    resource_name: str,
    *,
    name_input: str | None = None,
) -> None:
    component_id = str(row.get("id", "")).strip().lower()
    old_instance_id = str(row.get("instance_id", "")).strip()
    row["instance_id"] = resource_name
    inputs = row.setdefault("inputs", {})
    assert isinstance(inputs, dict)
    if component_id == "mk8s":
        cluster = inputs.setdefault("cluster", {})
        assert isinstance(cluster, dict)
        cluster[name_input or "cluster_name"] = resource_name
    else:
        inputs[name_input or "name"] = resource_name
    if component_id != "mk8s" or not old_instance_id or old_instance_id == resource_name:
        return
    for chart in payload.get("apps", {}).get("charts", []):
        if not isinstance(chart, dict):
            continue
        if chart.get("instance_id") == old_instance_id:
            chart["instance_id"] = resource_name
        if chart.get("target_ref") == old_instance_id:
            chart["target_ref"] = resource_name
    for target in payload.get("deploy", {}).get("targets", []):
        if isinstance(target, dict) and target.get("instance_id") == old_instance_id:
            target["instance_id"] = resource_name


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


def _markdown_headings(markdown: str) -> list[str]:
    headings: list[str] = []
    in_fenced_block = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block:
            continue
        if stripped.startswith("#"):
            headings.append(stripped)
    return headings


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
    _align_infra_resource_name(payload, mk8s, "vm-observability")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs["node_groups"] = {
        "cpu": {
            "node_count": 1,
            "gpu": False,
            "platform": "cpu-d3",
            "preset": "4vcpu-16gb",
        }
    }

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
    assert "## Client\n\n- Client:" in markdown
    assert "## Infra\n\n### Infra Component Status\n\n- `mk8s`" in markdown
    assert "- `mk8s` (Managed Kubernetes baseline cluster): `enabled`" in markdown
    assert "- `vm` (Compute virtual machine): `disabled`" in markdown
    assert "### Infra Component Reports" in markdown
    assert "- `mk8s` (Managed Kubernetes baseline cluster)" in markdown
    assert (
        "  - Resource: `nebius.mk8s.cluster` with `cluster.cluster_name` = `vm-observability`"
        in markdown
    )
    assert "`cluster.cluster_name=vm-observability`" in markdown
    assert "`cluster.public_endpoint=true`" in markdown
    assert "`cluster=3 key(s)`" not in markdown
    assert "### MK8s Clusters" in markdown
    assert "## Apps\n\n### App Component Status" in markdown
    assert "### App Component Reports" in markdown
    assert "- `n8n@vm-observability` (n8n workflow automation)" in markdown
    assert "  - Release: `n8n/n8n`" in markdown
    assert "  - Target: `vm-observability`" in markdown
    assert "### Platform Apps" not in markdown
    assert "### Observability Apps" not in markdown
    assert "### Workloads" in markdown
    assert "## Validations\n\n- No deploy-time validations configured." in markdown
    assert "- n8n: `enabled`; hostname `n8n.example.com`" in markdown
    headings = _markdown_headings(markdown)
    assert len(headings) == len(set(headings))


def test_write_inventory_lists_selected_security_and_platform_components(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s", "mysterybox"},
        selected_apps={
            "external-secrets",
            "gateway-helm",
            "nvidia-gpu-operator",
            "nvidia-network-operator",
        },
    )
    mysterybox = _infra_component_row(payload, "mysterybox")
    mysterybox["inputs"] = {
        "parent_id": "project-456",
        "secrets": [
            {
                "name": "db-username-password",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}, "PASSWORD": {"type": "text"}},
            },
            {
                "name": "secret2",
                "version_id": "n/a",
                "payload": {"MYKEY": {"type": "text"}},
            },
        ],
    }
    payload["deploy"]["targets"][0]["secrets"] = {
        "mysterybox": {
            "enabled": True,
            "sync_namespaces": ["ns1", "ns2"],
            "refresh_interval": "1m",
            "store_name": "nebius-mysterybox-shared",
        }
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "- `mysterybox` (MysteryBox secrets): `enabled`" in markdown
    assert "### MysteryBox Secrets" in markdown
    assert "- `mysterybox`: `db-username-password`, `secret2`" in markdown
    assert "### MysteryBox Kubernetes Sync" in markdown
    assert (
        "- `mk8s`: namespaces `ns1`, `ns2`; refresh interval `1m`; store `nebius-mysterybox-shared`"
    ) in markdown
    assert "- Envoy Gateway: `enabled`" in markdown
    assert "- External Secrets Operator: `enabled`" in markdown
    assert "- NVIDIA GPU Operator: `enabled`" in markdown
    assert "- NVIDIA Network Operator: `enabled`" in markdown


def test_write_inventory_lists_each_mk8s_cluster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s"},
        selected_apps={"nvidia-gpu-operator", "nvidia-network-operator"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    _align_infra_resource_name(payload, mk8s, "cluster1")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.update(
        {
            "cluster": {
                **mk8s_inputs.get("cluster", {}),
                "public_endpoint": True,
            },
            "node_groups": {
                "cpu": {
                    "node_count": 2,
                    "gpu": False,
                    "platform": "cpu-d3",
                    "preset": "32vcpu-128gb",
                },
                "worker": {
                    "node_count": 2,
                    "gpu": True,
                    "platform": "gpu-h100-sxm",
                    "preset": "8gpu-128vcpu-1600gb",
                    "gpu_cluster_key": "workers",
                },
            },
            "gpu_clusters": {"workers": {"infiniband_fabric": "fabric-6"}},
        }
    )
    cluster2 = yaml.safe_load(yaml.safe_dump(mk8s, sort_keys=False))
    cluster2["instance_id"] = "cluster2"
    cluster2["inputs"]["cluster"]["cluster_name"] = "cluster2"
    cluster2["inputs"]["node_groups"]["worker"]["preset"] = "1gpu-16vcpu-200gb"
    cluster2["inputs"]["node_groups"]["worker"].pop("gpu_cluster_key", None)
    cluster2["inputs"].pop("gpu_clusters", None)
    payload["infra"]["components"].append(cluster2)
    payload["apps"]["charts"].append(
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "cluster2",
            "enabled": True,
            "values": {},
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    (paths.infra_dir / ".terraform").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        inventory_ops,
        "terraform_output_json",
        lambda _infra_dir, *, initialize: {
            "cluster1_cluster_id": {"value": "mk8scluster-111"},
            "cluster1_cluster_name": {"value": "cluster1"},
            "cluster2_cluster_id": {"value": "mk8scluster-222"},
            "cluster2_cluster_name": {"value": "cluster2"},
        },
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "- `cluster1` (`cluster1`)" in markdown
    assert "  - CPU node groups: `cpu`: `2` node(s) at `cpu-d3/32vcpu-128gb`" in markdown
    assert (
        "  - GPU node groups: `worker`: `2` node(s) at `gpu-h100-sxm/8gpu-128vcpu-1600gb`"
        in markdown
    )
    assert "  - InfiniBand fabric: `fabric-6`" in markdown
    assert "  - Public endpoint: `enabled`" in markdown
    assert "  - Cluster ID: `mk8scluster-111`" in markdown
    assert "  - Kube context: `nebius-cluster1-mk8scluster-111-external`" in markdown
    assert "- `cluster2` (`cluster2`)" in markdown
    assert (
        "  - GPU node groups: `worker`: `2` node(s) at `gpu-h100-sxm/1gpu-16vcpu-200gb`"
        in markdown
    )
    assert "  - InfiniBand fabric: `none`" in markdown
    assert "  - Cluster ID: `mk8scluster-222`" in markdown
    assert "  - Kube context: `nebius-cluster2-mk8scluster-222-external`" in markdown


def test_write_inventory_uses_grafana_status_target_metadata_when_tf_outputs_unavailable(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s["instance_id"] = "cluster1"
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs.setdefault("cluster", {})["cluster_name"] = "cluster1"
    payload["deploy"] = {"targets": [{"instance_id": "cluster1"}]}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    write_grafana_status(
        paths,
        [
            {
                "target_ref": "cluster1",
                "namespace": "observability",
                "release_name": "grafana",
                "cluster_id": "mk8scluster-live",
                "kube_context": "nebius-cluster1-mk8scluster-live-external",
            }
        ],
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "  - Cluster ID: `mk8scluster-live`" in markdown
    assert "  - Kube context: `nebius-cluster1-mk8scluster-live-external`" in markdown


def test_write_inventory_includes_observability_endpoint_contract(tmp_path: Path) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"nvidia-gpu-operator"})
    _enable_mk8s_observability(payload)
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs["node_groups"] = {
        "worker": {
            "node_count": 1,
            "gpu": True,
            "platform": "gpu-h100-sxm",
            "preset": "8gpu-128vcpu-1600gb",
            "gpu_stack_source": "nebius_image",
        }
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")
    markdown_lines = markdown.splitlines()

    assert "## Observability Write Endpoints" in markdown
    assert "## Observability Read Endpoints" in markdown
    assert (
        "- Metrics read (Prometheus, Nebius service metrics): "
        "`https://read.monitoring.api.nebius.cloud/projects/"
        "project-456/service-provider/prometheus`"
    ) in markdown_lines
    assert (
        "- Metrics read (federate, `gpu` bucket): "
        "`https://read.monitoring.api.nebius.cloud/projects/"
        "project-456/buckets/gpu/prometheus/federate`"
    ) in markdown_lines
    assert "- Metrics read (federate, `msp` bucket): " not in markdown
    assert all("/buckets/<service-provider>/" not in line for line in markdown_lines)
    assert (
        "- Metrics write (OTLP HTTP/protobuf): "
        "`"
        "https://write.monitoring.eu-north1.nebius.cloud/projects/"
        "project-456/opentelemetry/v1/metrics"
        "`"
    ) in markdown_lines
    assert (
        "- Logs write (direct/self-managed): `https://write.logging.eu-north1.nebius.cloud`"
    ) in markdown_lines
    assert (
        "- Traces write (OTLP gRPC): `dns:///write.tracing.eu-north1.nebius.cloud:443`"
    ) in markdown_lines
    assert "## Grafana" in markdown
    assert "### Target `mk8s`" in markdown
    assert "- Grafana: `pending`" in markdown
    assert "- Metrics:" not in markdown
    assert "- Logs:" not in markdown
    assert "- Traces:" not in markdown
    assert "- Dashboards:" not in markdown
    assert "- Bundled dashboards:" in markdown
    assert (
        "  - Nebius Kubernetes Metrics: `pending` (`nebius-kubernetes/kubernetes-cluster-monitoring`)"
        in markdown
    )
    assert (
        "  - Nebius Kubernetes GPU Metrics: `pending` (`nebius-kubernetes/kubernetes-gpu`)"
        in markdown
    )
    assert (
        "  - Nebius Kubernetes Logs: `pending` (`nebius-kubernetes/kubernetes-logs-from-loki`)"
    ) in markdown
    assert (
        "  - Nebius Kubernetes Traces: `pending` (`nebius-kubernetes/kubernetes-traces`)"
        in markdown
    )
    assert "  - Nebius VM Metrics: `pending` (`nebius-vm/vm-metrics`)" in markdown
    assert "  - Nebius VM Logs: `pending` (`nebius-vm/vm-logs`)" in markdown
    assert "### Notes" in markdown
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
                        "cluster_id": "mk8scluster-123",
                        "kube_context": "nebius-cluster2-mk8scluster-123-external",
                        "base_url": "http://203.0.113.10/",
                        "metrics_url_kind": "dashboard",
                        "metrics_url": "http://203.0.113.10/goto/metrics123?orgId=1",
                        "logs_url_kind": "dashboard",
                        "logs_url": "http://203.0.113.10/goto/logs123?orgId=1",
                        "traces_url_kind": "dashboard",
                        "traces_url": "http://203.0.113.10/goto/traces123?orgId=1",
                        "dashboards_url": "http://203.0.113.10/dashboards",
                        "root_url_warning": "Timed out waiting for Grafana root_url",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "### Target `mk8s`" in markdown
    assert "- Grafana: [Open Grafana](http://203.0.113.10/)" in markdown
    assert "- Metrics:" not in markdown
    assert "- Logs:" not in markdown
    assert "- Traces:" not in markdown
    assert "- Dashboards:" not in markdown
    assert "- Bundled dashboards:" in markdown
    assert (
        "  - [Nebius Kubernetes Metrics]"
        "(http://203.0.113.10/d/cxcli-kubernetes-metrics?orgId=1&var-Cluster=mk8scluster-123) "
        "(`nebius-kubernetes/kubernetes-cluster-monitoring`)"
    ) in markdown
    assert (
        "  - [Nebius Kubernetes GPU Metrics]"
        "(http://203.0.113.10/d/cxcli-kubernetes-gpu?orgId=1&var-Cluster=mk8scluster-123) "
        "(`nebius-kubernetes/kubernetes-gpu`)"
    ) in markdown
    assert (
        "  - [Nebius VM Metrics]"
        "(http://203.0.113.10/d/cxcli-vm-metrics?orgId=1) "
        "(`nebius-vm/vm-metrics`)"
    ) in markdown
    assert (
        "  - [Nebius VM Logs](http://203.0.113.10/d/cxcli-vm-logs?orgId=1) (`nebius-vm/vm-logs`)"
    ) in markdown
    assert "- Credentials: user `admin`; password command:" in markdown
    assert "- Root URL note: `Timed out waiting for Grafana root_url`" in markdown
    assert (
        "- Credentials: user `admin`; password command:\n\n"
        "```bash\n"
        "printf '%s\\n' \"$(kubectl --context=nebius-cluster2-mk8scluster-123-external "
        "-n observability get secret "
        "nebius-cxcli-grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)\"\n"
        "```\n"
    ) in markdown
    assert "- Prometheus datasource split:" in markdown
    assert (
        "  - `Nebius Services` reads `Metrics read (Prometheus, Nebius service metrics)`: "
        "Nebius/provider service metrics for cloud resources, the cxcli GPU dashboard, "
        "and service dashboard examples."
    ) in markdown
    assert (
        "  - `Nebius User Metrics` reads `Metrics read (Prometheus, user-ingested metrics)`: "
        "Customer/user-ingested Prometheus metrics, including Kubernetes metrics written by "
        "the Nebius observability agent."
    ) in markdown
    assert (
        "- Bundled dashboard links list cxcli-owned JSON dashboards shipped under "
        "`src/nebius_cxcli/grafana_dashboards`; operator-owned external dashboard JSON "
        "is still imported into Grafana but is not listed here."
    ) in markdown


def test_bundled_grafana_dashboard_report_links_track_package_assets() -> None:
    reset_component_entry_cache()

    dashboards = inventory_ops._bundled_grafana_dashboards()

    assert [
        {
            "folder": item.folder,
            "dashboard": item.dashboard,
            "uid": item.uid,
            "title": item.title,
            "datasource": item.datasource,
        }
        for item in dashboards
    ] == [
        {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-cluster-monitoring",
            "uid": "cxcli-kubernetes-metrics",
            "title": "Nebius Kubernetes Metrics",
            "datasource": "Nebius User Metrics",
        },
        {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-gpu",
            "uid": "cxcli-kubernetes-gpu",
            "title": "Nebius Kubernetes GPU Metrics",
            "datasource": "Nebius Services",
        },
        {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-logs-from-loki",
            "uid": "cxcli-kubernetes-logs",
            "title": "Nebius Kubernetes Logs",
            "datasource": "Nebius Logs",
        },
        {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-traces",
            "uid": "cxcli-kubernetes-traces",
            "title": "Nebius Kubernetes Traces",
            "datasource": "Nebius Traces",
        },
        {
            "folder": "nebius-vm",
            "dashboard": "vm-metrics",
            "uid": "cxcli-vm-metrics",
            "title": "Nebius VM Metrics",
            "datasource": "Nebius Services",
        },
        {
            "folder": "nebius-vm",
            "dashboard": "vm-logs",
            "uid": "cxcli-vm-logs",
            "title": "Nebius VM Logs",
            "datasource": "Nebius Logs",
        },
    ]


def test_write_inventory_lists_pending_grafana_links_per_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    (paths.infra_dir / ".terraform").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        inventory_ops,
        "terraform_output_json",
        lambda _infra_dir, *, initialize: {
            "cluster1_cluster_id": {"value": "mk8scluster-111"},
            "cluster1_cluster_name": {"value": "cluster1"},
            "cluster2_cluster_id": {"value": "mk8scluster-222"},
            "cluster2_cluster_name": {"value": "cluster2"},
        },
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "### Target `cluster1`" in markdown
    assert "### Target `cluster2`" in markdown
    assert markdown.count("- Grafana: `pending`") == 2
    assert (
        "- MK8s: cluster ID `mk8scluster-222`; "
        "kube context `nebius-cluster2-mk8scluster-222-external`"
    ) in markdown
    assert markdown.count("- Credentials: user `admin`; password command:") == 2
    assert (
        "kubectl --context=nebius-cluster2-mk8scluster-222-external -n observability "
        "get secret nebius-cxcli-grafana-admin"
    ) in markdown


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

    assert "### Target `cluster1`" in markdown
    assert "- Grafana: `pending`" in markdown
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
    markdown_lines = markdown.splitlines()

    assert "- `managed-postgresql` (Managed PostgreSQL database): `enabled`" in markdown
    assert (
        "- Metrics read (federate, `msp` bucket): "
        "`https://read.monitoring.api.nebius.cloud/projects/"
        "project-456/buckets/msp/prometheus/federate`"
    ) in markdown_lines


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

    assert "VM monitoring agent: `enabled`" in markdown
    assert "VM journald logs (systemd services): `enabled`" in markdown
    assert "Metrics read (Prometheus, Nebius service metrics)" in markdown
    assert "Logs read (Loki)" in markdown
    assert "## Observability Write Endpoints" not in markdown
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

    assert "VM monitoring agent: `enabled`" in markdown
    assert "## Observability Write Endpoints" not in markdown
    assert "## Observability Read Endpoints" in markdown
    assert "Metrics read (Prometheus, Nebius service metrics)" in markdown
    assert "VM journald logs (systemd services): `disabled`" not in markdown


def test_write_inventory_includes_ssh_jumphost_proxyjump_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"vm", "ssh-jumphost"}, selected_apps=set())
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
            "public_ip_mode": "none",
        }
    )
    jumphost = _infra_component_row(payload, "ssh-jumphost")
    jumphost_inputs = jumphost.setdefault("inputs", {})
    assert isinstance(jumphost_inputs, dict)
    jumphost_inputs.update(
        {
            "parent_id": "project-456",
            "subnet_id": "subnet-123",
            "platform": "cpu-d3",
            "preset": "2vcpu-8gb",
            "source_image_family": "ubuntu24.04-driverless",
            "ssh_user_name": "admin",
            "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
            "allowed_cidrs": ["203.0.113.10/32"],
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    (paths.infra_dir / ".terraform").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        inventory_ops,
        "terraform_output_json",
        lambda *_args, **_kwargs: {
            "ssh_jumphost_public_ip": {"value": "198.51.100.20"},
            "vm_private_ip": {"value": "10.0.0.15"},
        },
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "### SSH Jump Host Access" in markdown
    assert "# `vm` via `ssh-jumphost`" not in markdown
    assert "# vm via ssh-jumphost" in markdown
    assert "ssh -J admin@198.51.100.20 ubuntu@10.0.0.15" in markdown
    assert "`-i /path/to/private_key`" in markdown


def test_write_inventory_includes_wireguard_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"wireguard-gw"}, selected_apps=set())
    wireguard = _infra_component_row(payload, "wireguard-gw")
    _align_infra_resource_name(payload, wireguard, "wg-gw")
    wireguard_inputs = wireguard.setdefault("inputs", {})
    assert isinstance(wireguard_inputs, dict)
    wireguard_inputs.update(
        {
            "parent_id": "project-456",
            "subnet_id": "subnet-123",
            "platform": "cpu-d3",
            "preset": "2vcpu-8gb",
            "source_image_family": "ubuntu24.04-driverless",
            "ssh_user_name": "ubuntu",
            "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
            "local_subnets": ["10.20.0.0/16", "10.30.0.0/16"],
            "wireguard_tunnel_cidr": "10.8.0.1/22",
            "wireguard_listen_port": 51820,
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    (paths.infra_dir / ".terraform").mkdir(parents=True, exist_ok=True)
    client_dir = paths.project_dir / "wireguard-clients"
    client_dir.mkdir()
    (client_dir / "laptop.conf").write_text(
        "[Interface]\nPrivateKey = redacted\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        inventory_ops,
        "terraform_output_json",
        lambda *_args, **_kwargs: {
            "wg_gw_public_ip": {"value": "198.51.100.30"},
            "wg_gw_private_ip": {"value": "10.0.0.30"},
            "wg_gw_wireguard_listen_port": {"value": 51820},
        },
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "### WireGuard VPN Gateway Access" in markdown
    assert "- Component: `wireguard-gw@wg-gw`" in markdown
    assert "  - Public endpoint: `198.51.100.30:51820`" in markdown
    assert "  - Private IP: `10.0.0.30`" in markdown
    assert "  - WireGuard tunnel CIDR: `10.8.0.1/22`" in markdown
    assert "  - Default routed local subnets: `10.20.0.0/16`, `10.30.0.0/16`" in markdown
    assert "  - Default client DNS: `1.1.1.1`, `1.0.0.1`" in markdown
    assert f"nebius-cxcli wireguard --gen-client-conf {config_path}" in markdown
    assert "--component wireguard-gw" not in markdown
    assert "--add-local-subnets" not in markdown
    assert "--remove-local-subnets" not in markdown
    assert f"wg-quick up {client_dir / 'laptop.conf'}" in markdown
    assert f"wg-quick down {client_dir / 'laptop.conf'}" in markdown

    command_hints = inventory_ops.wireguard_access_command_hints(config, paths)
    assert command_hints == [
        {
            "label": "WireGuard connect laptop",
            "command": f"wg-quick up {client_dir / 'laptop.conf'}",
        },
        {
            "label": "WireGuard disconnect laptop",
            "command": f"wg-quick down {client_dir / 'laptop.conf'}",
        },
    ]


def test_write_inventory_wireguard_handoff_without_local_client_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(selected_infra={"wireguard-gw"}, selected_apps=set())
    wireguard = _infra_component_row(payload, "wireguard-gw")
    _align_infra_resource_name(payload, wireguard, "wg-gw")
    wireguard_inputs = wireguard.setdefault("inputs", {})
    assert isinstance(wireguard_inputs, dict)
    wireguard_inputs.update(
        {
            "parent_id": "project-456",
            "subnet_id": "subnet-123",
            "platform": "cpu-d3",
            "preset": "2vcpu-8gb",
            "source_image_family": "ubuntu24.04-driverless",
            "ssh_user_name": "ubuntu",
            "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
            "local_subnets": ["10.20.0.0/16"],
            "wireguard_tunnel_cidr": "10.8.0.1/22",
            "wireguard_listen_port": 51820,
        }
    )
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)
    (paths.infra_dir / ".terraform").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        inventory_ops,
        "terraform_output_json",
        lambda *_args, **_kwargs: {
            "wg_gw_public_ip": {"value": "198.51.100.30"},
            "wg_gw_private_ip": {"value": "10.0.0.30"},
            "wg_gw_wireguard_listen_port": {"value": 51820},
        },
    )

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert f"nebius-cxcli wireguard --gen-client-conf {config_path}" in markdown
    assert "--component wireguard-gw" not in markdown
    assert "Generate a WireGuard client config with the command above" in markdown
    assert "# Connect to WireGuard" in markdown
    assert "# Disconnect from WireGuard" in markdown
    assert "wg-quick up" in markdown
    assert "wg-quick down" in markdown
    assert "--add-local-subnets" not in markdown
    assert "--remove-local-subnets" not in markdown

    command_hints = inventory_ops.wireguard_access_command_hints(config, paths)
    assert command_hints == [
        {
            "label": "Generate WireGuard client config for wireguard-gw@wg-gw",
            "command": f"nebius-cxcli wireguard --gen-client-conf {config_path}",
        }
    ]


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


def test_write_inventory_reports_enabled_catalog_components_without_custom_sections(
    tmp_path: Path,
) -> None:
    reset_component_entry_cache()
    config_path = _project_config_path(tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _starter_payload(
        selected_infra={"mk8s", "object-storage", "vm"},
        selected_apps={"cert-manager"},
    )
    mk8s = _infra_component_row(payload, "mk8s")
    mk8s_inputs = mk8s.setdefault("inputs", {})
    assert isinstance(mk8s_inputs, dict)
    mk8s_inputs["cluster_name"] = "mk8s"
    object_storage = _infra_component_row(payload, "object-storage")
    _align_infra_resource_name(payload, object_storage, "training-artifacts")
    object_storage["inputs"] = {
        "parent_id": "project-456",
        "name": "training-artifacts",
        "versioning_enabled": True,
    }
    vm = _infra_component_row(payload, "vm")
    _align_infra_resource_name(payload, vm, "private-vm")
    vm["inputs"] = {
        "parent_id": "project-456",
        "name": "private-vm",
        "platform": "cpu-d3",
        "preset": "4vcpu-16gb",
        "subnet_id": "subnet-123",
        "ssh_user_name": "ubuntu",
        "ssh_public_key": _VALID_ED25519_PUBLIC_KEY,
        "public_ip_mode": "none",
    }
    cert_manager = _chart_row(payload, "cert-manager")
    cert_manager["enabled"] = True
    cert_manager["instance_id"] = "mk8s"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_project_paths(config_path)
    validate_path_alignment(config, paths)

    artifacts = write_inventory(config, paths)
    markdown = artifacts.markdown.read_text(encoding="utf-8")

    assert "### Infra Component Reports" in markdown
    assert "- `object-storage` (Object storage bucket)" in markdown
    assert "  - Resource: `nebius.storage.bucket` with `name` = `training-artifacts`" in markdown
    assert "  - Inputs: `name=training-artifacts`" in markdown
    assert "- `vm` (Compute virtual machine)" in markdown
    assert "  - Resource: `nebius.compute.instance` with `name` = `private-vm`" in markdown
    assert "ssh_public_key" not in markdown
    assert _VALID_ED25519_PUBLIC_KEY not in markdown

    assert "### App Component Reports" in markdown
    assert "- `cert-manager@mk8s` (cert-manager for certificate automation)" in markdown
    assert "  - Release: `cert-manager/cert-manager`" in markdown
    assert "  - Target: `mk8s`" in markdown
    assert "  - Chart: `cert-manager`" in markdown
