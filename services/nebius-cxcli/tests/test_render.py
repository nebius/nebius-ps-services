from __future__ import annotations

import json
from pathlib import Path

import yaml

from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.paths import resolve_instance_paths, validate_path_alignment
from nebius_cxcli.render import render_instance


def test_render_creates_expected_outputs(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "nebius-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(source, encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    result = render_instance(config, paths)

    assert (paths.infra_dir / "terraform.tf").exists()
    assert (paths.infra_dir / "main.tf").exists()
    assert (paths.infra_dir / "terraform.auto.tfvars.json").exists()
    assert (paths.flux_dir / "kustomization.yaml").exists()
    assert (paths.flux_dir / "apps/platform/csi-mounted-fs-path-helmrelease.yaml").exists()
    assert (paths.flux_dir / "apps/platform/namespace-kube-system.yaml").exists()
    assert (paths.flux_dir / "apps/workloads/namespace-n8n.yaml").exists()
    assert (paths.flux_dir / "apps/workloads/pvc-n8n-csi-pvc.yaml").exists()
    backend_text = (paths.infra_dir / "terraform.tf").read_text(encoding="utf-8")
    assert "encrypt = true" in backend_text
    assert 'account_id_env       = "NEBIUS_SA_ID"' in backend_text
    assert 'public_key_id_env    = "NEBIUS_AUTH_PUBLIC_KEY_ID"' in backend_text
    assert 'private_key_file_env = "NEBIUS_AUTH_PRIVATE_KEY_FILE"' in backend_text
    main_text = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert 'module "customer_platform"' in main_text
    assert "wireguard_enabled = local.rendered_inputs.wireguard_enabled" in main_text

    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert tfvars["state_bucket_versioning_policy"] == "ENABLED"
    assert tfvars["state_bucket_object_audit_logging"] == "ALL"
    assert tfvars["state_bucket_manage"] is False
    assert tfvars["state_bucket_protect_from_destroy"] is True
    assert tfvars["inventory_bucket_manage"] is True
    assert tfvars["inventory_bucket_versioning_policy"] == "DISABLED"
    assert tfvars["inventory_bucket_object_audit_logging"] == "NONE"
    assert tfvars["inventory_bucket_protect_from_destroy"] is False
    assert tfvars["managed_postgresql_postgresql_version"] == 16
    assert tfvars["managed_postgresql_public_access"] is False
    assert tfvars["sfs_type"] == "NETWORK_SSD"
    assert tfvars["wireguard_enabled"] is True
    assert tfvars["wireguard_name"] == "client-a-prod-wg"
    assert tfvars["wireguard_tunnel_cidr"] == "10.8.0.1/24"
    assert tfvars["wireguard_listen_port"] == 51820
    assert tfvars["wireguard_nat_mode"] is True
    assert tfvars["wireguard_endpoint_host"] is None
    assert tfvars["wireguard_clients"] == []
    assert tfvars["ssh_user_name"] == "ubuntu"
    assert tfvars["ssh_public_key"].startswith("ssh-ed25519 ")
    assert tfvars["ssh_jumphost_enabled"] is False
    assert tfvars["mysterybox_enabled"] is False
    assert "mysterybox_secrets" not in tfvars

    assert len(result.files_written) >= 6


def test_render_writes_optional_mk8s_override_tfvars(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "my-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["mk8s"]["cluster_overrides"] = {
        "control_plane": {
            "subnet_id": "subnet-abc123",
            "version": "1.31",
            "etcd_cluster_size": 3,
            "endpoints": {"public_endpoint": True},
        },
        "kube_network": {"service_cidrs": ["10.96.0.0/16"]},
    }
    payload["infra"]["mk8s"]["gpu_node_group_overrides"] = {
        "autoscaling": {"min_node_count": 0, "max_node_count": 10},
        "template": {
            "gpu_settings": {"drivers_preset": "cuda12.8"},
            "preemptible": True,
        },
    }
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)
    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert tfvars["k8s_version"] == "1.31"
    assert tfvars["etcd_cluster_size"] == 3
    assert tfvars["subnet_id"] == "subnet-abc123"
    assert tfvars["mk8s_cluster_public_endpoint"] is True
    assert tfvars["mk8s_cluster_overrides"]["kube_network"]["service_cidrs"] == ["10.96.0.0/16"]
    assert tfvars["mk8s_gpu_node_group_overrides"]["template"]["preemptible"] == {}


def test_render_writes_mysterybox_tfvars_without_payload_values(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "my-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["mysterybox"]["enabled"] = True
    payload["infra"]["mysterybox"]["secrets"] = [
        {
            "id": "n8n-runtime",
            "scope": "apps",
            "name": "n8n-runtime",
            "labels": {"app": "n8n"},
            "set_primary": True,
            "entries": [
                {"key": "N8N_ENCRYPTION_KEY", "value_from_env": "N8N_ENCRYPTION_KEY"},
                {"key": "N8N_BASIC_AUTH_PASSWORD", "value_from_env": "N8N_BASIC_AUTH_PASSWORD"},
            ],
        }
    ]
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert tfvars["mysterybox_enabled"] is True
    assert "mysterybox_secrets" not in tfvars
    assert "mysterybox_secret_values" not in tfvars


def test_render_writes_external_secrets_mysterybox_flux_artifacts(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "my-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["mysterybox"]["enabled"] = True
    payload["infra"]["mysterybox"]["secrets"] = [
        {
            "id": "n8n-runtime",
            "scope": "apps",
            "name": "client-a-prod-n8n-runtime",
            "entries": [
                {"key": "N8N_ENCRYPTION_KEY", "value_from_env": "N8N_ENCRYPTION_KEY"},
            ],
            "k8s_sync": {
                "enabled": True,
                "namespace": "n8n",
                "target_secret_name": "n8n-secrets",
                "refresh_interval": "30m",
            },
        }
    ]
    payload["apps"]["platform"]["external_secrets"]["enabled"] = True
    payload["apps"]["platform"]["external_secrets"]["mysterybox"]["enabled"] = True

    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    assert (paths.flux_dir / "apps/platform/external-secrets-helmrelease.yaml").exists()
    assert (paths.flux_dir / "apps/platform/mysterybox-clustersecretstore.yaml").exists()
    assert (paths.flux_dir / "apps/platform/mysterybox-bridge-deployment.yaml").exists()
    assert (paths.flux_dir / "apps/platform/mysterybox-bridge-service.yaml").exists()
    assert (paths.flux_dir / "apps/workloads/externalsecret-n8n-n8n-runtime.yaml").exists()

    external_secret_doc = yaml.safe_load(
        (paths.flux_dir / "apps/workloads/externalsecret-n8n-n8n-runtime.yaml").read_text(
            encoding="utf-8"
        )
    )
    store_doc = yaml.safe_load(
        (paths.flux_dir / "apps/platform/mysterybox-clustersecretstore.yaml").read_text(
            encoding="utf-8"
        )
    )
    bridge_deploy_doc = yaml.safe_load(
        (paths.flux_dir / "apps/platform/mysterybox-bridge-deployment.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert external_secret_doc["spec"]["secretStoreRef"]["name"] == "nebius-mysterybox"
    assert external_secret_doc["spec"]["target"]["name"] == "n8n-secrets"
    assert external_secret_doc["spec"]["data"][0]["remoteRef"]["key"] == "client-a-prod-n8n-runtime"
    assert external_secret_doc["spec"]["data"][0]["remoteRef"]["property"] == "N8N_ENCRYPTION_KEY"
    assert store_doc["spec"]["provider"]["webhook"]["headers"]["X-MBX-Request"] == (
        "{{ .bridgeAuth.token }}"
    )
    assert store_doc["spec"]["provider"]["webhook"]["secrets"][0]["secretRef"]["name"] == (
        "mysterybox-bridge-webhook-auth"
    )
    assert bridge_deploy_doc["spec"]["template"]["spec"]["containers"][0]["command"][0] == "/bin/sh"
    assert (
        "--factory mysterybox_bridge.app:create_app"
        in bridge_deploy_doc["spec"]["template"]["spec"]["containers"][0]["command"][2]
    )
    assert (
        bridge_deploy_doc["spec"]["template"]["spec"]["containers"][0]["readinessProbe"][
            "httpGet"
        ]["path"]
        == "/readyz"
    )
    assert (
        bridge_deploy_doc["spec"]["template"]["spec"]["containers"][0]["env"][1]["name"]
        == "MYSTERYBOX_WEBHOOK_AUTH_HEADER"
    )


def test_render_sets_wireguard_toggle_false_when_disabled(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "my-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["wireguard-jumphost"]["enabled"] = False
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    main_text = (paths.infra_dir / "main.tf").read_text(encoding="utf-8")
    assert 'module "wireguard_jump_host"' not in main_text
    assert "wireguard_enabled = local.rendered_inputs.wireguard_enabled" in main_text
    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert tfvars["wireguard_enabled"] is False


def test_render_writes_ssh_jumphost_tfvars_when_enabled(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "my-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["ssh-jumphost"]["enabled"] = True
    payload["infra"]["ssh-jumphost"]["name"] = "client-a-ssh-jh"
    payload["infra"]["ssh-jumphost"]["allowed_cidrs"] = ["203.0.113.10/32"]

    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert tfvars["ssh_jumphost_enabled"] is True
    assert tfvars["ssh_jumphost_name"] == "client-a-ssh-jh"
    assert tfvars["ssh_jumphost_allowed_cidrs"] == ["203.0.113.10/32"]


def test_render_skips_csi_files_when_disabled(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path / "my-deployments" / "instances" / "client-a--tenant-123" / "dev" / "client-a-dev"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="dev",
        cluster_name="client-a-dev",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["sfs"]["csi"]["enabled"] = False
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    assert not (paths.flux_dir / "apps/platform/csi-mounted-fs-path-helmrelease.yaml").exists()
    assert not any((paths.flux_dir / "apps/workloads").glob("pvc-*.yaml"))


def test_render_supports_multiple_csi_pvcs(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path
        / "my-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["sfs"]["csi"]["pvcs"] = [
        {
            "namespace": "n8n",
            "create_namespace": True,
            "name": "csi-pvc",
            "size": "1Gi",
            "access_modes": ["ReadWriteMany"],
        },
        {
            "namespace": "ml-team",
            "create_namespace": True,
            "name": "shared-data",
            "size": "10Gi",
            "access_modes": ["ReadWriteMany"],
        },
    ]
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    assert (paths.flux_dir / "apps/workloads/pvc-n8n-csi-pvc.yaml").exists()
    assert (paths.flux_dir / "apps/workloads/pvc-ml-team-shared-data.yaml").exists()
    assert (paths.flux_dir / "apps/workloads/namespace-ml-team.yaml").exists()


def test_render_static_csi_mode_generates_pv_and_bound_pvc(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path / "static-root" / "instances" / "client-a--tenant-123" / "prod" / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["sfs"]["csi"]["mode"] = "static"
    payload["infra"]["sfs"]["csi"]["static"]["shared_path"] = "/mnt/data/shared"
    payload["infra"]["sfs"]["csi"]["pvcs"] = [
        {
            "namespace": "n8n",
            "create_namespace": True,
            "name": "csi-pvc",
            "size": "1Gi",
            "access_modes": ["ReadWriteMany"],
            "static_pv_name": "sfs-pv-n8n",
            "static_sub_path": "team-shared",
        },
        {
            "namespace": "ml-team",
            "create_namespace": True,
            "name": "ml-pvc",
            "size": "5Gi",
            "access_modes": ["ReadWriteMany"],
            "static_pv_name": "sfs-pv-ml-team",
            "static_sub_path": "team-shared",
        },
    ]
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)

    pv_file = paths.flux_dir / "apps/workloads/pv-sfs-pv-n8n.yaml"
    pvc_file = paths.flux_dir / "apps/workloads/pvc-n8n-csi-pvc.yaml"
    assert pv_file.exists()
    assert pvc_file.exists()
    pv_doc = yaml.safe_load(pv_file.read_text(encoding="utf-8"))
    pvc_doc = yaml.safe_load(pvc_file.read_text(encoding="utf-8"))
    assert pv_doc["spec"]["csi"]["driver"] == "mounted-fs-path.csi.nebius.ai"
    assert pv_doc["spec"]["csi"]["volumeAttributes"]["path"] == "/mnt/data/shared/team-shared"
    assert pvc_doc["spec"]["volumeName"] == "sfs-pv-n8n"


def test_render_writes_mig_tfvars_when_enabled(tmp_path: Path) -> None:
    cluster_dir = (
        tmp_path / "custom-root" / "instances" / "client-a--tenant-123" / "prod" / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["mk8s"]["gpu_nodes"]["enabled"] = True
    payload["infra"]["mk8s"]["gpu_nodes"]["node_groups"] = 1
    payload["infra"]["mk8s"]["gpu_nodes"]["nodes_per_group"] = 1
    payload["infra"]["mk8s"]["gpu_nodes"]["platform"] = "gpu-h200-sxm"
    payload["infra"]["mk8s"]["gpu_nodes"]["preset"] = "8gpu-128vcpu-1600gb"
    payload["infra"]["mk8s"]["gpu_nodes"]["mig"]["enabled"] = True
    payload["infra"]["mk8s"]["gpu_nodes"]["mig"]["strategy"] = "single"
    payload["infra"]["mk8s"]["gpu_nodes"]["mig"]["parted_config"] = "all-disabled"
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)
    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )

    assert tfvars["mig_strategy"] == "single"
    assert tfvars["mig_parted_config"] == "all-disabled"
    assert "gpu_mig_strategy" not in tfvars
    assert "gpu_mig_parted_config" not in tfvars


def test_render_writes_egress_gateway_flux_manifests_without_tfvars_toggle(
    tmp_path: Path,
) -> None:
    cluster_dir = (
        tmp_path / "egress-root" / "instances" / "client-a--tenant-123" / "prod" / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)

    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    payload["infra"]["mk8s"]["egress_gateway"]["enabled"] = True
    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)

    render_instance(config, paths)
    assert (paths.flux_dir / "apps/platform/cilium-config-egress-gateway.yaml").exists()
    assert (paths.flux_dir / "apps/platform/cilium-daemonset-restart-egress-gateway.yaml").exists()
    assert (paths.flux_dir / "apps/platform/cilium-operator-restart-egress-gateway.yaml").exists()
    assert (paths.flux_dir / "apps/platform/cilium-egress-nodes-network-policy.yaml").exists()

    tfvars = json.loads(
        (paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8")
    )
    assert "enable_egress_gateway" not in tfvars
