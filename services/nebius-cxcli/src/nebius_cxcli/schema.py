"""Strict, versioned schema for nebius-cxcli `config.yaml` files."""

from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown keys and trims string inputs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Environment(StrEnum):
    DEV = "dev"
    STAGE = "stage"
    PROD = "prod"


class NebiusConfig(StrictModel):
    tenant_id: str = Field(min_length=3)
    project_id: str = Field(min_length=3)
    region_id: str = Field(min_length=3)


class NotificationsConfig(StrictModel):
    inventory_markdown: bool = True
    email: str | None = None


class ClientInfoConfig(StrictModel):
    client_name: str
    env: Environment
    cluster_name: str
    nebius: NebiusConfig
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @field_validator("client_name", "cluster_name")
    @classmethod
    def validate_name_segments(cls, value: str) -> str:
        if not value:
            raise ValueError("value cannot be empty")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
        if any(ch not in allowed for ch in value):
            raise ValueError("must contain only lowercase letters, digits, and hyphens")
        if value.startswith("-") or value.endswith("-"):
            raise ValueError("cannot start or end with '-' ")
        return value

    @model_validator(mode="after")
    def validate_notifications(self) -> ClientInfoConfig:
        if self.notifications.email and "@" not in self.notifications.email:
            raise ValueError("notifications.email must be a valid email address")
        return self


class CpuNodesConfig(StrictModel):
    count: int = Field(default=2, ge=0)
    platform: str
    preset: str
    preemptible: bool = False
    public_ips: bool = False


class MigConfig(StrictModel):
    enabled: bool = False
    strategy: Literal["", "single", "mixed", "none"] = ""
    parted_config: str = ""

    @model_validator(mode="after")
    def validate_mig_config(self) -> MigConfig:
        if self.enabled and not self.strategy:
            raise ValueError("gpu_nodes.mig.strategy is required when gpu_nodes.mig.enabled=true")
        if not self.enabled and (self.strategy or self.parted_config):
            raise ValueError(
                "gpu_nodes.mig.strategy/parted_config require gpu_nodes.mig.enabled=true"
            )
        return self


class GpuNodesConfig(StrictModel):
    enabled: bool = False
    node_groups: int = Field(default=0, ge=0)
    nodes_per_group: int = Field(default=0, ge=0)
    platform: str = ""
    preset: str = ""
    preemptible: bool = False
    public_ips: bool = False
    driverfull_image: bool = True
    mig: MigConfig = Field(default_factory=MigConfig)

    @model_validator(mode="after")
    def validate_gpu_fields(self) -> GpuNodesConfig:
        if self.mig.enabled and not self.enabled:
            raise ValueError("gpu_nodes.mig.enabled=true requires gpu_nodes.enabled=true")
        if not self.enabled:
            return self
        if self.node_groups < 1 or self.nodes_per_group < 1:
            raise ValueError("gpu_nodes requires node_groups >= 1 and nodes_per_group >= 1")
        if not self.platform:
            raise ValueError("gpu_nodes.platform is required when gpu_nodes.enabled=true")
        if not self.preset:
            raise ValueError("gpu_nodes.preset is required when gpu_nodes.enabled=true")
        return self


class ApiEndpointConfig(StrictModel):
    public: bool = False


class EgressGatewayConfig(StrictModel):
    enabled: bool = False


class Mk8sControlPlaneEndpointsConfig(StrictModel):
    public_endpoint: bool | None = None


class Mk8sControlPlaneConfig(StrictModel):
    subnet_id: str | None = None
    audit_logs: bool | None = None
    endpoints: Mk8sControlPlaneEndpointsConfig | None = None
    etcd_cluster_size: int | None = Field(default=None, ge=1)
    version: str | None = None

    @field_validator("version")
    @classmethod
    def validate_version_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        chunks = value.split(".")
        if len(chunks) != 2 or not all(chunk.isdigit() for chunk in chunks):
            raise ValueError("control_plane.version must be '<major>.<minor>', for example '1.31'")
        return value


class Mk8sKubeNetworkConfig(StrictModel):
    service_cidrs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_service_cidrs(self) -> Mk8sKubeNetworkConfig:
        if len(self.service_cidrs) > 1:
            raise ValueError("kube_network.service_cidrs currently supports a single CIDR value")
        return self


class Mk8sClusterOverridesConfig(StrictModel):
    name: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    parent_id: str | None = None
    resource_version: int | None = Field(default=None, ge=0)
    control_plane: Mk8sControlPlaneConfig | None = None
    kube_network: Mk8sKubeNetworkConfig | None = None


class Mk8sNodeGroupAutoRepairConditionConfig(StrictModel):
    type: str
    disabled: bool | None = None
    status: Literal["CONDITION_STATUS_UNSPECIFIED", "TRUE", "FALSE", "UNKNOWN"] | None = None
    timeout: str | None = None

    @model_validator(mode="after")
    def validate_disabled_timeout(self) -> Mk8sNodeGroupAutoRepairConditionConfig:
        if self.disabled is True and self.timeout is not None:
            raise ValueError("auto_repair.conditions.timeout cannot be set when disabled=true")
        return self


class Mk8sNodeGroupAutoRepairConfig(StrictModel):
    conditions: list[Mk8sNodeGroupAutoRepairConditionConfig] = Field(default_factory=list)


class Mk8sNodeGroupAutoscalingConfig(StrictModel):
    min_node_count: int | None = Field(default=None, ge=0)
    max_node_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_min_max(self) -> Mk8sNodeGroupAutoscalingConfig:
        if self.min_node_count is None and self.max_node_count is None:
            raise ValueError(
                "autoscaling requires at least one of min_node_count or max_node_count"
            )
        if (
            self.min_node_count is not None
            and self.max_node_count is not None
            and self.max_node_count < self.min_node_count
        ):
            raise ValueError("autoscaling.max_node_count must be >= autoscaling.min_node_count")
        return self


class Mk8sNodeGroupCountOrPercentConfig(StrictModel):
    count: int | None = Field(default=None, ge=0)
    percent: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_choice(self) -> Mk8sNodeGroupCountOrPercentConfig:
        if self.count is not None and self.percent is not None:
            raise ValueError("Specify either count or percent, not both")
        return self


class Mk8sNodeGroupStrategyConfig(StrictModel):
    drain_timeout: str | None = None
    max_surge: Mk8sNodeGroupCountOrPercentConfig | None = None
    max_unavailable: Mk8sNodeGroupCountOrPercentConfig | None = None


class Mk8sNodeTemplateResourcesConfig(StrictModel):
    platform: str = Field(min_length=1)
    preset: str | None = None


class Mk8sNodeTemplateBootDiskConfig(StrictModel):
    block_size_bytes: int | None = Field(default=None, ge=0)
    size_bytes: int | None = Field(default=None, ge=0)
    size_gibibytes: int | None = Field(default=None, ge=0)
    size_kibibytes: int | None = Field(default=None, ge=0)
    size_mebibytes: int | None = Field(default=None, ge=0)
    type: str | None = None

    @model_validator(mode="after")
    def validate_size_selector(self) -> Mk8sNodeTemplateBootDiskConfig:
        size_fields = [
            self.size_bytes,
            self.size_gibibytes,
            self.size_kibibytes,
            self.size_mebibytes,
        ]
        selected = sum(1 for value in size_fields if value is not None)
        if selected > 1:
            raise ValueError(
                "boot_disk supports only one size field: size_bytes, size_gibibytes, "
                "size_kibibytes, or size_mebibytes"
            )
        return self


class Mk8sNodeTemplateFilesystemRefConfig(StrictModel):
    id: str


class Mk8sNodeTemplateFilesystemConfig(StrictModel):
    attach_mode: Literal["READ_ONLY", "READ_WRITE"]
    mount_tag: str = Field(min_length=1)
    existing_filesystem: Mk8sNodeTemplateFilesystemRefConfig | None = None


class Mk8sNodeTemplateGpuClusterConfig(StrictModel):
    id: str


class Mk8sNodeTemplateGpuSettingsConfig(StrictModel):
    drivers_preset: str


class Mk8sNodeTemplateMetadataConfig(StrictModel):
    labels: dict[str, str] = Field(default_factory=dict)


class Mk8sNodeTemplateNetworkInterfaceConfig(StrictModel):
    subnet_id: str | None = None
    public_ip_address: bool | None = None


class Mk8sNodeTemplateReservationPolicyConfig(StrictModel):
    policy: Literal["AUTO", "FORBID", "STRICT"] | None = None
    reservation_ids: list[str] = Field(default_factory=list)


class Mk8sNodeTemplateTaintConfig(StrictModel):
    key: str
    value: str
    effect: Literal["EFFECT_UNSPECIFIED", "NO_EXECUTE", "NO_SCHEDULE", "PREFER_NO_SCHEDULE"]


class Mk8sNodeTemplateConfig(StrictModel):
    resources: Mk8sNodeTemplateResourcesConfig | None = None
    boot_disk: Mk8sNodeTemplateBootDiskConfig | None = None
    cloud_init_user_data: str | None = None
    filesystems: list[Mk8sNodeTemplateFilesystemConfig] = Field(default_factory=list)
    gpu_cluster: Mk8sNodeTemplateGpuClusterConfig | None = None
    gpu_settings: Mk8sNodeTemplateGpuSettingsConfig | None = None
    metadata: Mk8sNodeTemplateMetadataConfig | None = None
    network_interfaces: list[Mk8sNodeTemplateNetworkInterfaceConfig] = Field(default_factory=list)
    os: str | None = None
    preemptible: bool | None = None
    reservation_policy: Mk8sNodeTemplateReservationPolicyConfig | None = None
    service_account_id: str | None = None
    taints: list[Mk8sNodeTemplateTaintConfig] = Field(default_factory=list)


class Mk8sNodeGroupOverridesConfig(StrictModel):
    name: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    parent_id: str | None = None
    resource_version: int | None = Field(default=None, ge=0)
    version: str | None = None
    fixed_node_count: int | None = Field(default=None, ge=0)
    autoscaling: Mk8sNodeGroupAutoscalingConfig | None = None
    auto_repair: Mk8sNodeGroupAutoRepairConfig | None = None
    strategy: Mk8sNodeGroupStrategyConfig | None = None
    template: Mk8sNodeTemplateConfig | None = None

    @field_validator("version")
    @classmethod
    def validate_version_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        chunks = value.split(".")
        if len(chunks) != 2 or not all(chunk.isdigit() for chunk in chunks):
            raise ValueError("node_group.version must be '<major>.<minor>', for example '1.31'")
        return value

    @model_validator(mode="after")
    def validate_scaling_mode(self) -> Mk8sNodeGroupOverridesConfig:
        if self.fixed_node_count is not None and self.autoscaling is not None:
            raise ValueError("node_group cannot set both fixed_node_count and autoscaling")
        return self


class Mk8sInfraConfig(StrictModel):
    enabled: bool = True
    subnet_id: str = Field(min_length=3)
    cpu_nodes: CpuNodesConfig
    gpu_nodes: GpuNodesConfig = Field(default_factory=GpuNodesConfig)
    infiniband_fabric: str = ""
    api_endpoint: ApiEndpointConfig = Field(default_factory=ApiEndpointConfig)
    egress_gateway: EgressGatewayConfig = Field(default_factory=EgressGatewayConfig)
    cluster_overrides: Mk8sClusterOverridesConfig | None = None
    cpu_node_group_overrides: Mk8sNodeGroupOverridesConfig | None = None
    gpu_node_group_overrides: Mk8sNodeGroupOverridesConfig | None = None


class ManagedPostgresqlConfig(StrictModel):
    enabled: bool = False
    name: str = ""
    tier: Literal["small", "medium", "large"] = "medium"
    storage_gib: int = Field(default=0, ge=0)
    postgresql_version: int = Field(default=16, ge=10)
    public_access: bool = False

    @model_validator(mode="after")
    def validate_pg(self) -> ManagedPostgresqlConfig:
        if self.enabled and (not self.name or self.storage_gib < 1):
            raise ValueError("managed_postgresql requires name and storage_gib when enabled")
        return self


class SfsCsiPvcConfig(StrictModel):
    namespace: str = "n8n"
    name: str = "csi-pvc"
    size: str = "1Gi"
    access_modes: list[str] = Field(default_factory=lambda: ["ReadWriteMany"])
    create_namespace: bool = True
    static_pv_name: str | None = None
    static_sub_path: str | None = None

    @model_validator(mode="after")
    def validate_pvc(self) -> SfsCsiPvcConfig:
        if not self.name:
            raise ValueError("sfs.csi.pvcs[].name cannot be empty")
        if not self.namespace:
            raise ValueError("sfs.csi.pvcs[].namespace cannot be empty")
        if not self.size:
            raise ValueError("sfs.csi.pvcs[].size cannot be empty")
        if not self.access_modes:
            raise ValueError("sfs.csi.pvcs[].access_modes must contain at least one mode")
        if self.static_sub_path is not None and self.static_sub_path.startswith("/"):
            raise ValueError(
                "sfs.csi.pvcs[].static_sub_path must be relative (for example 'team-a/data')"
            )
        if self.static_sub_path is not None and ".." in self.static_sub_path.split("/"):
            raise ValueError("sfs.csi.pvcs[].static_sub_path cannot contain '..'")
        return self


class SfsCsiStaticConfig(StrictModel):
    shared_path: str = "/mnt/data/shared"
    driver_name: str = "mounted-fs-path.csi.nebius.ai"
    reclaim_policy: Literal["Retain", "Delete"] = "Retain"

    @model_validator(mode="after")
    def validate_static(self) -> SfsCsiStaticConfig:
        if not self.shared_path.startswith("/"):
            raise ValueError("sfs.csi.static.shared_path must be an absolute path")
        if not self.driver_name:
            raise ValueError("sfs.csi.static.driver_name cannot be empty")
        return self


class SfsCsiConfig(StrictModel):
    enabled: bool = False
    namespace: str = "kube-system"
    create_namespace: bool = True
    mode: Literal["dynamic", "static"] = "dynamic"
    chart_url: str = "oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path"
    chart_version: str = "0.1.3"
    data_dir: str = "/mnt/data/csi-mounted-fs-path-data/"
    static: SfsCsiStaticConfig = Field(default_factory=SfsCsiStaticConfig)
    pvcs: list[SfsCsiPvcConfig] = Field(default_factory=lambda: [SfsCsiPvcConfig()])

    @model_validator(mode="after")
    def validate_csi(self) -> SfsCsiConfig:
        if not self.namespace:
            raise ValueError("sfs.csi.namespace cannot be empty")
        if not self.chart_url.startswith("oci://"):
            raise ValueError("sfs.csi.chart_url must be an OCI URL (oci://...)")
        if not self.chart_version:
            raise ValueError("sfs.csi.chart_version cannot be empty")
        if not self.data_dir:
            raise ValueError("sfs.csi.data_dir cannot be empty")
        if not self.pvcs:
            raise ValueError("sfs.csi.pvcs must contain at least one PVC definition")
        seen: set[tuple[str, str]] = set()
        for pvc in self.pvcs:
            key = (pvc.namespace, pvc.name)
            if key in seen:
                raise ValueError(
                    f"Duplicate PVC definition for namespace/name '{pvc.namespace}/{pvc.name}'"
                )
            if self.mode == "dynamic" and (
                pvc.static_pv_name is not None or pvc.static_sub_path is not None
            ):
                raise ValueError("sfs.csi.pvcs[].static_* fields require sfs.csi.mode='static'")
            seen.add(key)
        return self


class SfsConfig(StrictModel):
    enabled: bool = False
    name: str = ""
    size_gib: int = Field(default=0, ge=0)
    block_size_kib: int = Field(default=4, ge=4)
    type: Literal[
        "NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"
    ] = "NETWORK_SSD"
    csi: SfsCsiConfig = Field(default_factory=SfsCsiConfig)

    @model_validator(mode="after")
    def validate_sfs(self) -> SfsConfig:
        if self.enabled and (not self.name or self.size_gib < 1):
            raise ValueError("sfs requires name and size_gib when enabled")
        if self.csi.enabled and not self.enabled:
            raise ValueError("sfs.csi.enabled=true requires sfs.enabled=true")
        return self


class StateBucketConfig(StrictModel):
    manage: bool = False
    name: str
    prefix: str = "tfstate"
    use_lockfile: bool = True
    encryption: bool = True
    versioning_policy: Literal["DISABLED", "ENABLED", "SUSPENDED"] = "ENABLED"
    object_audit_logging: Literal["NONE", "MUTATE_ONLY", "ALL"] = "ALL"
    protect_from_destroy: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$", value):
            raise ValueError("state_bucket.name must use lowercase letters, digits, and hyphens")
        return value


class InventoryBucketConfig(StrictModel):
    manage: bool = True
    name: str
    prefix: str = "inventory"
    versioning_policy: Literal["DISABLED", "ENABLED", "SUSPENDED"] = "DISABLED"
    object_audit_logging: Literal["NONE", "MUTATE_ONLY", "ALL"] = "NONE"
    protect_from_destroy: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$", value):
            raise ValueError(
                "inventory_bucket.name must use lowercase letters, digits, and hyphens"
            )
        return value


class ObjectStorageConfig(StrictModel):
    state_bucket: StateBucketConfig
    inventory_bucket: InventoryBucketConfig

    @model_validator(mode="after")
    def validate_bucket_management(self) -> ObjectStorageConfig:
        if not self.state_bucket.manage and not self.inventory_bucket.manage:
            raise ValueError(
                "object_storage requires at least one managed bucket "
                "(state_bucket.manage or inventory_bucket.manage)"
            )
        return self


class MysteryBoxEntryConfig(StrictModel):
    key: str = Field(min_length=1)
    value_from_env: str = Field(min_length=1)

    @field_validator("value_from_env")
    @classmethod
    def validate_env_var_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[A-Z_][A-Z0-9_]*$", value):
            raise ValueError(
                "mysterybox.secrets[].entries[].value_from_env must be an environment variable "
                "name (for example N8N_ENCRYPTION_KEY)"
            )
        return value


class MysteryBoxK8sSyncConfig(StrictModel):
    enabled: bool = False
    namespace: str = "default"
    target_secret_name: str | None = None
    refresh_interval: str | None = None
    creation_policy: Literal["Owner", "Merge", "Orphan", "None"] = "Owner"
    deletion_policy: Literal["Retain", "Delete", "Merge"] = "Retain"

    @model_validator(mode="after")
    def validate_sync(self) -> MysteryBoxK8sSyncConfig:
        if self.enabled and not self.namespace:
            raise ValueError("mysterybox.secrets[].k8s_sync.namespace cannot be empty")
        if self.enabled and self.target_secret_name is not None and not self.target_secret_name:
            raise ValueError("mysterybox.secrets[].k8s_sync.target_secret_name cannot be empty")
        return self


class MysteryBoxSecretConfig(StrictModel):
    id: str
    scope: Literal["platform", "apps"] = "platform"
    name: str = Field(min_length=1)
    description: str | None = None
    version_description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    set_primary: bool = True
    entries: list[MysteryBoxEntryConfig] = Field(default_factory=list)
    k8s_sync: MysteryBoxK8sSyncConfig = Field(default_factory=MysteryBoxK8sSyncConfig)

    @field_validator("id")
    @classmethod
    def validate_secret_id(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "mysterybox.secrets[].id must use lowercase letters, digits, and hyphens"
            )
        return value

    @model_validator(mode="after")
    def validate_entries(self) -> MysteryBoxSecretConfig:
        if not self.entries:
            raise ValueError("mysterybox.secrets[].entries must contain at least one payload entry")
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("mysterybox.secrets[].entries keys must be unique per secret")
        if self.k8s_sync.enabled and self.scope != "apps":
            raise ValueError(
                "mysterybox.secrets[].k8s_sync.enabled=true requires mysterybox.secrets[].scope='apps'"
            )
        return self


class MysteryBoxConfig(StrictModel):
    enabled: bool = False
    secrets: list[MysteryBoxSecretConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mysterybox(self) -> MysteryBoxConfig:
        if self.enabled and not self.secrets:
            raise ValueError("infra.mysterybox.enabled=true requires infra.mysterybox.secrets")
        ids = [secret.id for secret in self.secrets]
        if len(ids) != len(set(ids)):
            raise ValueError("infra.mysterybox.secrets[].id values must be unique")
        return self


class WireguardClientConfig(StrictModel):
    name: str
    address: str
    allowed_ips: list[str] = Field(default_factory=list)
    dns: list[str] = Field(default_factory=lambda: ["1.1.1.1"])
    persistent_keepalive: int = Field(default=25, ge=0, le=65535)
    write_ssh_config: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "wireguard-jumphost.clients[].name must use lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        try:
            iface = ipaddress.ip_interface(value)
        except ValueError as exc:
            raise ValueError(
                "wireguard-jumphost.clients[].address must be a valid IPv4 interface CIDR"
            ) from exc
        if iface.version != 4:
            raise ValueError(
                "wireguard-jumphost.clients[].address must be a valid IPv4 interface CIDR"
            )
        return value

    @field_validator("allowed_ips")
    @classmethod
    def validate_allowed_ips(cls, value: list[str]) -> list[str]:
        for cidr in value:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(
                    "wireguard-jumphost.clients[].allowed_ips must contain valid CIDRs"
                ) from exc
            if network.version != 4:
                raise ValueError(
                    "wireguard-jumphost.clients[].allowed_ips must contain IPv4 CIDRs only"
                )
        return value

    @field_validator("dns")
    @classmethod
    def validate_dns(cls, value: list[str]) -> list[str]:
        for item in value:
            try:
                ipaddress.IPv4Address(item)
            except ValueError as exc:
                raise ValueError(
                    "wireguard-jumphost.clients[].dns must contain IPv4 addresses"
                ) from exc
        return value


class WireguardConfig(StrictModel):
    enabled: bool = False
    name: str = ""
    platform: str = "cpu-d3"
    preset: str = "4vcpu-16gb"
    create_public_ip_allocation: bool = True
    public_ip_allocation_id: str | None = None
    public_ip_allocation_name: str | None = None
    boot_disk_size_gib: int = Field(default=60, ge=20)
    boot_disk_block_size_bytes: int = Field(default=4096, ge=4096, le=131072)
    boot_disk_type: Literal[
        "NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"
    ] = "NETWORK_SSD"
    source_image_family: str = "ubuntu22.04-driverless"
    tunnel_cidr: str = "10.8.0.1/24"
    listen_port: int = Field(default=51820, ge=1, le=65535)
    nat_mode: bool = True
    endpoint_host: str | None = None
    clients: list[WireguardClientConfig] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            return value
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "wireguard-jumphost.name must use lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("public_ip_allocation_name")
    @classmethod
    def validate_allocation_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "wireguard-jumphost.public_ip_allocation_name must use lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("endpoint_host")
    @classmethod
    def validate_endpoint_host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value:
            raise ValueError("wireguard-jumphost.endpoint_host cannot be empty")
        return value

    @field_validator("tunnel_cidr")
    @classmethod
    def validate_tunnel_cidr(cls, value: str) -> str:
        try:
            interface = ipaddress.ip_interface(value)
        except ValueError as exc:
            raise ValueError(
                "wireguard-jumphost.tunnel_cidr must be a valid IPv4 interface CIDR "
                "(example: 10.8.0.1/24)"
            ) from exc
        if interface.version != 4:
            raise ValueError(
                "wireguard-jumphost.tunnel_cidr must be an IPv4 interface CIDR "
                "(example: 10.8.0.1/24)"
            )
        return value

    @model_validator(mode="after")
    def validate_wireguard(self) -> WireguardConfig:
        if not self.enabled:
            return self
        if not self.name:
            raise ValueError("wireguard-jumphost.name is required when enabled=true")
        if self.public_ip_allocation_id and self.create_public_ip_allocation:
            raise ValueError(
                "wireguard-jumphost.create_public_ip_allocation must be false "
                "when public_ip_allocation_id is set"
            )
        block_size = self.boot_disk_block_size_bytes
        if block_size & (block_size - 1) != 0:
            raise ValueError("wireguard-jumphost.boot_disk_block_size_bytes must be a power of two")
        names = [client.name for client in self.clients]
        if len(names) != len(set(names)):
            raise ValueError("wireguard-jumphost.clients must have unique names")
        return self


class SshJumpHostConfig(StrictModel):
    enabled: bool = False
    name: str = ""
    platform: str = "cpu-d3"
    preset: str = "4vcpu-16gb"
    create_public_ip_allocation: bool = True
    public_ip_allocation_id: str | None = None
    public_ip_allocation_name: str | None = None
    allowed_cidrs: list[str] = Field(default_factory=list)
    boot_disk_size_gib: int = Field(default=60, ge=20)
    boot_disk_block_size_bytes: int = Field(default=4096, ge=4096, le=131072)
    boot_disk_type: Literal[
        "NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"
    ] = "NETWORK_SSD"
    source_image_family: str = "ubuntu22.04-driverless"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            return value
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError("ssh-jumphost.name must use lowercase letters, digits, and hyphens")
        return value

    @field_validator("public_ip_allocation_name")
    @classmethod
    def validate_allocation_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "ssh-jumphost.public_ip_allocation_name must use lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("allowed_cidrs")
    @classmethod
    def validate_allowed_cidrs(cls, value: list[str]) -> list[str]:
        for cidr in value:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(
                    "ssh-jumphost.allowed_cidrs must contain valid CIDRs "
                    "(for example 203.0.113.10/32)"
                ) from exc
            if network.version != 4:
                raise ValueError("ssh-jumphost.allowed_cidrs currently supports IPv4 CIDRs only")
        return value

    @model_validator(mode="after")
    def validate_ssh_jumphost(self) -> SshJumpHostConfig:
        if not self.enabled:
            return self
        if not self.name:
            raise ValueError("ssh-jumphost.name is required when enabled=true")
        if not self.allowed_cidrs:
            raise ValueError(
                "ssh-jumphost.allowed_cidrs must contain at least one source CIDR when enabled=true"
            )
        if self.public_ip_allocation_id and self.create_public_ip_allocation:
            raise ValueError(
                "ssh-jumphost.create_public_ip_allocation must be false "
                "when public_ip_allocation_id is set"
            )
        block_size = self.boot_disk_block_size_bytes
        if block_size & (block_size - 1) != 0:
            raise ValueError("ssh-jumphost.boot_disk_block_size_bytes must be a power of two")
        return self


class InfraConfig(StrictModel):
    ssh_user_name: str = Field(default="ubuntu", min_length=1)
    ssh_public_key: str = Field(min_length=20)
    mysterybox: MysteryBoxConfig = Field(default_factory=MysteryBoxConfig)
    mk8s: Mk8sInfraConfig
    managed_postgresql: ManagedPostgresqlConfig = Field(default_factory=ManagedPostgresqlConfig)
    sfs: SfsConfig = Field(default_factory=SfsConfig)
    object_storage: ObjectStorageConfig
    wireguard_jumphost: WireguardConfig = Field(
        default_factory=WireguardConfig,
        alias="wireguard-jumphost",
    )
    ssh_jumphost: SshJumpHostConfig = Field(
        default_factory=SshJumpHostConfig,
        alias="ssh-jumphost",
    )

    @field_validator("ssh_user_name")
    @classmethod
    def validate_ssh_user_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z_][a-z0-9_-]{0,31}$", value):
            raise ValueError(
                "infra.ssh_user_name must match Linux username format "
                "(for example ubuntu, admin_user)"
            )
        return value

    @model_validator(mode="after")
    def validate_jump_hosts(self) -> InfraConfig:
        if self.wireguard_jumphost.enabled and not self.mk8s.enabled:
            raise ValueError("wireguard-jumphost.enabled=true requires infra.mk8s.enabled=true")
        return self


class ChartConfig(StrictModel):
    repo: str
    name: str
    version: str


class HelmComponentConfig(StrictModel):
    enabled: bool = False
    namespace: str
    chart: ChartConfig
    values: dict[str, Any] = Field(default_factory=dict)


class ObservabilityComponentConfig(StrictModel):
    enabled: bool = False
    namespace: str = "nebius-o11y"
    chart: ChartConfig
    values: dict[str, Any] = Field(default_factory=dict)


def _default_external_secrets_chart() -> ChartConfig:
    return ChartConfig(
        repo="https://charts.external-secrets.io",
        name="external-secrets",
        version="2.0.1",
    )


class ExternalSecretsMysteryBoxBridgeConfig(StrictModel):
    class AuthConfig(StrictModel):
        enabled: bool = True
        header_name: str = "X-MBX-Request"
        secret_name: str = "mysterybox-bridge-webhook-auth"
        secret_namespace: str | None = None
        secret_key: str = "token"

        @field_validator("header_name")
        @classmethod
        def validate_header_name(cls, value: str) -> str:
            if not value or "\n" in value or "\r" in value:
                raise ValueError(
                    "apps.platform.external_secrets.mysterybox.bridge.auth.header_name must be a single-line non-empty header name"
                )
            return value

        @field_validator("secret_name")
        @classmethod
        def validate_secret_name(cls, value: str) -> str:
            if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
                raise ValueError(
                    "apps.platform.external_secrets.mysterybox.bridge.auth.secret_name must use lowercase letters, digits, and hyphens"
                )
            return value

        @field_validator("secret_namespace")
        @classmethod
        def validate_secret_namespace(cls, value: str | None) -> str | None:
            if value is None:
                return value
            if not value:
                raise ValueError(
                    "apps.platform.external_secrets.mysterybox.bridge.auth.secret_namespace cannot be empty"
                )
            return value

        @field_validator("secret_key")
        @classmethod
        def validate_secret_key(cls, value: str) -> str:
            if not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
                raise ValueError(
                    "apps.platform.external_secrets.mysterybox.bridge.auth.secret_key must be a simple identifier (letters/digits/underscore)"
                )
            return value

    enabled: bool = True
    service_name: str = "mysterybox-bridge"
    service_port: int = Field(default=8080, ge=1, le=65535)
    image: str = "quay.io/nebius/mysterybox-bridge:latest"
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "apps.platform.external_secrets.mysterybox.bridge.service_name must use lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "apps.platform.external_secrets.mysterybox.bridge.image cannot be empty"
            )
        return value


class ExternalSecretsMysteryBoxConfig(StrictModel):
    enabled: bool = False
    secret_store_name: str = "nebius-mysterybox"
    auth_secret_name: str = "nebius-mysterybox-auth"
    auth_secret_namespace: str | None = None
    refresh_interval_default: str = "1h"
    bridge: ExternalSecretsMysteryBoxBridgeConfig = Field(
        default_factory=ExternalSecretsMysteryBoxBridgeConfig
    )

    @field_validator("secret_store_name", "auth_secret_name")
    @classmethod
    def validate_resource_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", value):
            raise ValueError(
                "apps.platform.external_secrets.mysterybox names must use lowercase letters, digits, and hyphens"
            )
        return value

    @field_validator("auth_secret_namespace")
    @classmethod
    def validate_auth_secret_namespace(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value:
            raise ValueError(
                "apps.platform.external_secrets.mysterybox.auth_secret_namespace cannot be empty"
            )
        return value


class ExternalSecretsComponentConfig(StrictModel):
    enabled: bool = False
    namespace: str = "external-secrets"
    create_namespace: bool = True
    chart: ChartConfig = Field(default_factory=_default_external_secrets_chart)
    values: dict[str, Any] = Field(default_factory=dict)
    mysterybox: ExternalSecretsMysteryBoxConfig = Field(
        default_factory=ExternalSecretsMysteryBoxConfig
    )

    @model_validator(mode="after")
    def validate_component(self) -> ExternalSecretsComponentConfig:
        if not self.namespace:
            raise ValueError("apps.platform.external_secrets.namespace cannot be empty")
        if self.mysterybox.enabled and not self.enabled:
            raise ValueError(
                "apps.platform.external_secrets.mysterybox.enabled=true requires apps.platform.external_secrets.enabled=true"
            )
        return self


class PlatformAppsConfig(StrictModel):
    envoy_gateway: HelmComponentConfig
    cert_manager: HelmComponentConfig
    external_dns: HelmComponentConfig
    observability: ObservabilityComponentConfig
    external_secrets: ExternalSecretsComponentConfig = Field(
        default_factory=ExternalSecretsComponentConfig
    )


class RouteTlsConfig(StrictModel):
    enabled: bool = False
    issuer_ref: str = ""

    @model_validator(mode="after")
    def validate_tls(self) -> RouteTlsConfig:
        if self.enabled and not self.issuer_ref:
            raise ValueError("route.tls.issuer_ref is required when route.tls.enabled=true")
        return self


class WorkloadRouteConfig(StrictModel):
    hostname: str
    tls: RouteTlsConfig = Field(default_factory=RouteTlsConfig)


class N8nWorkloadConfig(StrictModel):
    enabled: bool = False
    namespace: str = "n8n"
    chart: ChartConfig
    values: dict[str, Any] = Field(default_factory=dict)
    route: WorkloadRouteConfig


class WorkloadAppsConfig(StrictModel):
    n8n: N8nWorkloadConfig


class AppsConfig(StrictModel):
    platform: PlatformAppsConfig
    workloads: WorkloadAppsConfig

    @model_validator(mode="after")
    def validate_dependencies(self) -> AppsConfig:
        if self.workloads.n8n.enabled and not self.platform.envoy_gateway.enabled:
            raise ValueError(
                "n8n requires platform.envoy_gateway.enabled=true for HTTPRoute parentRef"
            )
        return self


class ConfigV1(StrictModel):
    version: Literal["v1"]
    client_info: ClientInfoConfig
    infra: InfraConfig
    apps: AppsConfig

    @model_validator(mode="after")
    def validate_cross_component_dependencies(self) -> ConfigV1:
        external_secrets = self.apps.platform.external_secrets
        if external_secrets.enabled and external_secrets.mysterybox.enabled:
            if not self.infra.mysterybox.enabled:
                raise ValueError(
                    "apps.platform.external_secrets.mysterybox.enabled=true requires infra.mysterybox.enabled=true"
                )
            if not any(secret.k8s_sync.enabled for secret in self.infra.mysterybox.secrets):
                raise ValueError(
                    "apps.platform.external_secrets.mysterybox.enabled=true requires at least one mysterybox.secrets[].k8s_sync.enabled=true entry"
                )
        return self


def validate_config(config_dict: dict[str, Any]) -> ConfigV1:
    """Validate a raw config dictionary and return typed config."""
    return ConfigV1.model_validate(config_dict)


def validate_config_from_yaml(yaml_str: str) -> ConfigV1:
    """Validate YAML text and return typed config."""
    payload = yaml.safe_load(yaml_str) or {}
    if not isinstance(payload, dict):
        raise ValueError("config.yaml root must be a mapping")
    return validate_config(payload)
