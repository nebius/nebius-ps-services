from __future__ import annotations

from pathlib import Path

import yaml

from nebius_cxcli.config_loader import load_config

SAMPLE_CONFIG = """
version: v1
client_info:
  client_name: client-a
  env: prod
  cluster_name: client-a-prod
  nebius:
    tenant_id: tenant-123
    project_id: project-456
    region_id: eu-north1
infra:
  ssh_user_name: ubuntu
  ssh_public_key: ssh-ed25519 AAAA-replace-me
  mk8s:
    enabled: true
    subnet_id: subnet-abc123
    cpu_nodes:
      count: 2
      platform: cpu-d3
      preset: 4vcpu-16gb
      preemptible: false
      public_ips: false
    gpu_nodes:
      enabled: false
      node_groups: 0
      nodes_per_group: 0
      platform: ""
      preset: ""
      preemptible: false
      public_ips: false
      driverfull_image: true
      mig:
        enabled: false
        strategy: ""
        parted_config: ""
    infiniband_fabric: ""
    api_endpoint:
      public: false
    egress_gateway:
      enabled: false
  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
apps:
  platform:
    envoy_gateway:
      enabled: true
      namespace: envoy-gateway-system
      chart:
        repo: https://envoyproxy.github.io/gateway-helm
        name: gateway-helm
        version: 1.4.2
      values: {}
    cert_manager:
      enabled: true
      namespace: cert-manager
      chart:
        repo: https://charts.jetstack.io
        name: cert-manager
        version: v1.16.0
      values: {}
    external_dns:
      enabled: true
      namespace: external-dns
      chart:
        repo: https://kubernetes-sigs.github.io/external-dns/
        name: external-dns
        version: 1.15.0
      values: {}
    observability:
      enabled: true
      namespace: nebius-o11y
      chart:
        repo: https://helm-charts.nebius.com/observability
        name: nebius-observability
        version: 0.1.0
      values:
        enable_nebius_o11y_agent: true
        enable_grafana: true
  workloads:
    n8n:
      enabled: true
      namespace: n8n
      chart:
        repo: https://8gears.github.io/n8n-helm-chart/
        name: n8n
        version: 1.0.6
      values: {}
      route:
        hostname: n8n.client-a.example.internal
        tls:
          enabled: true
          issuer_ref: internal-ca
"""


def test_schema_valid_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(SAMPLE_CONFIG, encoding="utf-8")

    loaded = load_config(config_path)
    assert loaded.version == "v1"
    assert loaded.client_info.client_name == "client-a"
    assert loaded.apps.workloads.n8n.enabled is True


def test_schema_rejects_unknown_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(SAMPLE_CONFIG + "\nunknown: true\n", encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("Expected validation failure for unknown key")


def test_schema_accepts_optional_mk8s_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_with_overrides = SAMPLE_CONFIG.replace(
        """    egress_gateway:
      enabled: false
""",
        """    egress_gateway:
      enabled: false
    cluster_overrides:
      labels:
        owner: platform
      control_plane:
        subnet_id: subnet-abc123
        audit_logs: true
        etcd_cluster_size: 3
        version: "1.31"
        endpoints:
          public_endpoint: true
      kube_network:
        service_cidrs:
          - 10.96.0.0/16
    gpu_node_group_overrides:
      autoscaling:
        min_node_count: 0
        max_node_count: 20
      strategy:
        max_surge:
          count: 1
      template:
        resources:
          platform: gpu-h200-sxm
          preset: 8gpu-128vcpu-1600gb
        gpu_settings:
          drivers_preset: cuda12.8
        cloud_init_user_data: |
          #cloud-config
          runcmd:
            - echo ok
""",
    )
    config_path.write_text(config_with_overrides, encoding="utf-8")

    loaded = load_config(config_path)
    assert loaded.infra.mk8s.cluster_overrides is not None
    assert loaded.infra.mk8s.cluster_overrides.control_plane is not None
    assert loaded.infra.mk8s.cluster_overrides.control_plane.version == "1.31"
    assert loaded.infra.mk8s.gpu_node_group_overrides is not None
    assert loaded.infra.mk8s.gpu_node_group_overrides.autoscaling is not None
    assert loaded.infra.mk8s.gpu_node_group_overrides.autoscaling.max_node_count == 20


def test_schema_rejects_mig_when_gpu_nodes_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid_config = SAMPLE_CONFIG.replace(
        """      mig:
        enabled: false
        strategy: ""
        parted_config: ""
""",
        """      mig:
        enabled: true
        strategy: single
        parted_config: ""
""",
    )
    config_path.write_text(invalid_config, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "gpu_nodes.mig.enabled=true requires gpu_nodes.enabled=true" in str(exc)
    else:
        raise AssertionError("Expected validation failure for MIG with gpu_nodes.disabled")


def test_schema_requires_mig_strategy_when_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid_config = SAMPLE_CONFIG.replace("enabled: false", "enabled: true", 1)
    invalid_config = (
        invalid_config.replace(
            """      mig:
        enabled: false
        strategy: ""
        parted_config: ""
""",
            """      mig:
        enabled: true
        strategy: ""
        parted_config: ""
""",
        )
        .replace('      platform: ""\n', "      platform: gpu-h200-sxm\n", 1)
        .replace('      preset: ""\n', "      preset: 8gpu-128vcpu-1600gb\n", 1)
        .replace("      node_groups: 0\n", "      node_groups: 1\n", 1)
        .replace("      nodes_per_group: 0\n", "      nodes_per_group: 1\n", 1)
    )
    config_path.write_text(invalid_config, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "gpu_nodes.mig.strategy is required" in str(exc)
    else:
        raise AssertionError("Expected validation failure when MIG is enabled without strategy")


def test_schema_rejects_duplicate_csi_pvc_namespace_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_with_duplicate_pvcs = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  sfs:
    enabled: true
    name: demo-sfs
    size_gib: 500
    block_size_kib: 4
    csi:
      enabled: true
      namespace: kube-system
      create_namespace: true
      chart_url: oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path
      chart_version: 0.1.3
      data_dir: /mnt/data/csi-mounted-fs-path-data/
      pvcs:
        - namespace: n8n
          create_namespace: true
          name: csi-pvc
          size: 1Gi
          access_modes: [ReadWriteMany]
        - namespace: n8n
          create_namespace: false
          name: csi-pvc
          size: 10Gi
          access_modes: [ReadWriteMany]
  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
    )
    config_path.write_text(config_with_duplicate_pvcs, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "Duplicate PVC definition" in str(exc)
    else:
        raise AssertionError("Expected validation failure for duplicate csi pvcs")


def test_schema_rejects_static_pvc_fields_in_dynamic_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_with_dynamic_static_mix = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  sfs:
    enabled: true
    name: demo-sfs
    size_gib: 500
    block_size_kib: 4
    csi:
      enabled: true
      mode: dynamic
      namespace: kube-system
      chart_url: oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path
      chart_version: 0.1.3
      data_dir: /mnt/data/csi-mounted-fs-path-data/
      pvcs:
        - namespace: n8n
          name: csi-pvc
          size: 1Gi
          access_modes: [ReadWriteMany]
          static_pv_name: sfs-pv-n8n
  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
    )
    config_path.write_text(config_with_dynamic_static_mix, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "static_* fields require sfs.csi.mode='static'" in str(exc)
    else:
        raise AssertionError("Expected validation failure for static fields in dynamic mode")


def test_schema_rejects_wireguard_allocation_conflict(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    conflict_config = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: true
    name: client-a-wg
    platform: cpu-d3
    preset: 4vcpu-16gb
    create_public_ip_allocation: true
    public_ip_allocation_id: vpcalloc-abc123
  ssh-jumphost:
    enabled: false
""",
    )
    config_path.write_text(conflict_config, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "create_public_ip_allocation must be false" in str(exc)
    else:
        raise AssertionError("Expected validation failure for wireguard allocation conflict")


def test_schema_rejects_wireguard_without_name_when_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: true
    name: ""
  ssh-jumphost:
    enabled: false
""",
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "wireguard-jumphost.name is required" in str(exc)
    else:
        raise AssertionError("Expected validation failure for empty wireguard name")


def test_schema_rejects_invalid_wireguard_tunnel_cidr(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: true
    name: client-a-wg
    tunnel_cidr: invalid-cidr
  ssh-jumphost:
    enabled: false
""",
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "wireguard-jumphost.tunnel_cidr must be a valid IPv4 interface CIDR" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid wireguard tunnel CIDR")


def test_schema_rejects_invalid_wireguard_listen_port(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: true
    name: client-a-wg
    listen_port: 70000
  ssh-jumphost:
    enabled: false
""",
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "listen_port" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid wireguard listen port")


def test_schema_accepts_wireguard_clients(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_with_clients = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: true
    name: client-a-wg
    clients:
      - name: laptop-ops
        address: 10.8.0.2/32
        allowed_ips:
          - 10.8.0.0/24
          - 10.0.0.0/8
        dns:
          - 1.1.1.1
        persistent_keepalive: 25
        write_ssh_config: true
  ssh-jumphost:
    enabled: false
""",
    )
    config_path.write_text(config_with_clients, encoding="utf-8")
    loaded = load_config(config_path)
    assert loaded.infra.wireguard_jumphost.enabled is True
    assert len(loaded.infra.wireguard_jumphost.clients) == 1
    assert loaded.infra.wireguard_jumphost.clients[0].name == "laptop-ops"


def test_schema_rejects_ssh_jumphost_without_allowed_cidrs(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: false
  ssh-jumphost:
    enabled: true
    name: client-a-ssh-jh
    allowed_cidrs: []
""",
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "ssh-jumphost.allowed_cidrs must contain at least one source CIDR" in str(exc)
    else:
        raise AssertionError(
            "Expected validation failure for enabled SSH jump host with empty allowed_cidrs"
        )


def test_schema_rejects_invalid_infra_ssh_user_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid_config = SAMPLE_CONFIG.replace("  ssh_user_name: ubuntu", "  ssh_user_name: bad user")
    config_path.write_text(invalid_config, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "infra.ssh_user_name must match Linux username format" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid infra.ssh_user_name format")


def test_schema_rejects_invalid_managed_postgresql_tier(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid_config = SAMPLE_CONFIG.replace("      values: {}\n", "      values: {}\n", 1).replace(
        "apps:\n",
        """  managed_postgresql:
    enabled: true
    name: client-a-prod-pg
    tier: xlarge
    storage_gib: 100
apps:
""",
        1,
    )
    config_path.write_text(invalid_config, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "small" in str(exc) and "medium" in str(exc) and "large" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid managed_postgresql.tier")


def test_schema_rejects_invalid_state_bucket_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid_config = SAMPLE_CONFIG.replace("name: tfstate-bucket", "name: TFSTATE_BUCKET", 1)
    config_path.write_text(invalid_config, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "state_bucket.name" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid state bucket name")


def test_schema_rejects_invalid_wireguard_name_format(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
""",
        """  object_storage:
    state_bucket:
      name: tfstate-bucket
      prefix: tfstate
      use_lockfile: true
    inventory_bucket:
      name: inventory-bucket
      prefix: inventory
  wireguard-jumphost:
    enabled: true
    name: Client_A_WG
  ssh-jumphost:
    enabled: false
""",
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "wireguard-jumphost.name must use lowercase letters" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid wireguard name format")


def test_schema_rejects_mysterybox_enabled_without_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        "  mk8s:\n",
        "  mysterybox:\n    enabled: true\n    secrets: []\n  mk8s:\n",
        1,
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "infra.mysterybox.enabled=true requires infra.mysterybox.secrets" in str(exc)
    else:
        raise AssertionError(
            "Expected validation failure for empty mysterybox secrets when enabled"
        )


def test_schema_rejects_invalid_mysterybox_env_var_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    invalid = SAMPLE_CONFIG.replace(
        "  mk8s:\n",
        """  mysterybox:
    enabled: true
    secrets:
      - id: app-runtime
        scope: apps
        name: app-runtime
        entries:
          - key: API_KEY
            value_from_env: bad-env-name
  mk8s:
""",
        1,
    )
    config_path.write_text(invalid, encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "value_from_env must be an environment variable name" in str(exc)
    else:
        raise AssertionError("Expected validation failure for invalid mysterybox value_from_env")


def test_schema_rejects_external_secrets_mysterybox_without_infra_mysterybox(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(SAMPLE_CONFIG)
    payload["apps"]["platform"]["external_secrets"] = {
        "enabled": True,
        "mysterybox": {"enabled": True},
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert (
            "apps.platform.external_secrets.mysterybox.enabled=true requires "
            "infra.mysterybox.enabled=true"
        ) in str(exc)
    else:
        raise AssertionError(
            "Expected validation failure when ESO MysteryBox sync is enabled without infra.mysterybox"
        )


def test_schema_rejects_k8s_sync_for_platform_scope_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    payload = yaml.safe_load(SAMPLE_CONFIG)
    payload["infra"]["mysterybox"] = {
        "enabled": True,
        "secrets": [
            {
                "id": "platform-secret",
                "scope": "platform",
                "name": "platform-secret",
                "entries": [{"key": "API_KEY", "value_from_env": "PLATFORM_API_KEY"}],
                "k8s_sync": {"enabled": True, "namespace": "default"},
            }
        ],
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "k8s_sync.enabled=true requires mysterybox.secrets[].scope='apps'" in str(exc)
    else:
        raise AssertionError("Expected validation failure for platform-scoped k8s_sync")
