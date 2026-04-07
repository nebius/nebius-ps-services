"""Strict, versioned schema for nebius-vpngw configuration files.

This module defines Pydantic models that enforce:
- Type safety and validation for all configuration fields
- Rejection of unknown fields (extra="forbid")
- Clear error messages for misconfigurations
- Support for both BGP and static routing modes
- API versioning for future compatibility

Usage:
    from nebius_vpngw.schema import VPNGatewayConfig

    config = VPNGatewayConfig.model_validate(yaml_dict)
"""

from __future__ import annotations

import ipaddress
import typing as t
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ============================================================================
# Enums for controlled vocabularies
# ============================================================================


class VendorType(str, Enum):
    """Supported VPN peer vendors."""

    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"
    CISCO = "cisco"
    GENERIC = "generic"


class RoutingMode(str, Enum):
    """Routing protocol mode."""

    BGP = "bgp"
    STATIC = "static"


class VPNType(str, Enum):
    """VPN protocol type."""

    IPSEC = "ipsec"


class AuthMethod(str, Enum):
    """Authentication method."""

    PSK = "psk"


class HARole(str, Enum):
    """High availability role for tunnels."""

    ACTIVE = "active"
    PASSIVE = "passive"
    DISABLE = "disable"


class HAMode(str, Enum):
    """High availability mode for tunnel selection."""

    ACTIVE_PASSIVE = "active-passive"
    ACTIVE_ACTIVE = "active-active"


class Platform(str, Enum):
    """Nebius compute platform."""

    CPU_E2 = "cpu-e2"
    CPU_D3 = "cpu-d3"


class DiskType(str, Enum):
    """Disk type for boot disk."""

    NETWORK_SSD = "network_ssd"
    NETWORK_HDD = "network_hdd"


class IPsecMode(str, Enum):
    """IPsec interface mode."""

    XFRM_INTERFACE = "xfrm-interface"  # Modern xfrm netdevs (default, avoids packet duplication)


# ============================================================================
# Reusable validators
# ============================================================================


def validate_cidr(v: str) -> str:
    """Validate CIDR notation."""
    try:
        ipaddress.ip_network(v, strict=False)
        return v
    except ValueError as e:
        raise ValueError(f"Invalid CIDR '{v}': {e}") from e


def validate_ip_address(v: str) -> str:
    """Validate IP address."""
    try:
        ipaddress.ip_address(v)
        return v
    except ValueError as e:
        raise ValueError(f"Invalid IP address '{v}': {e}") from e


def validate_private_ipv4_cidr(v: str, *, min_prefixlen: int = 8, max_prefixlen: int = 28) -> str:
    """Validate RFC1918 IPv4 CIDR notation and normalize host bits."""
    try:
        network = ipaddress.ip_network(v, strict=False)
    except ValueError as e:
        raise ValueError(f"Invalid CIDR '{v}': {e}") from e

    if network.version != 4:
        raise ValueError(f"CIDR '{v}' must be an IPv4 network")
    if not network.is_private:
        raise ValueError(f"CIDR '{v}' must be in RFC1918 private IPv4 space")
    if network.prefixlen < min_prefixlen or network.prefixlen > max_prefixlen:
        raise ValueError(
            f"CIDR '{v}' must use a prefix length between /{min_prefixlen} and /{max_prefixlen}"
        )

    return str(network)


def validate_asn(v: int) -> int:
    """Validate BGP ASN is in private range or valid public range."""
    # Private ASN: 64512-65534 (RFC 6996)
    # Public ASN: 1-64511, 65535-4199999999 (but typically < 4294967295)
    if 64512 <= v <= 65534:
        return v  # Private ASN
    if 1 <= v <= 64511:
        return v  # Public ASN
    if 65535 <= v <= 4199999999:
        return v  # Extended ASN
    raise ValueError(
        f"ASN {v} is invalid. Use private ASN (64512-65534) or public ASN (1-64511, 65535+)"
    )


def validate_apipa_cidr(v: str) -> str:
    """Validate CIDR is in APIPA range (169.254.0.0/16) and is /30."""
    try:
        network = ipaddress.ip_network(v, strict=False)
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValueError(f"inner_cidr '{v}' must be an IPv4 CIDR")
        apipa_range = ipaddress.IPv4Network("169.254.0.0/16")

        # Check if network is within APIPA range
        if not network.subnet_of(apipa_range):
            raise ValueError(f"inner_cidr '{v}' must be in APIPA range 169.254.0.0/16")

        # Check if it's a /30 (required for point-to-point tunnels)
        if network.prefixlen != 30:
            raise ValueError(
                f"inner_cidr '{v}' must be a /30 subnet (4 IPs). Got /{network.prefixlen} instead."
            )

        return v
    except ipaddress.AddressValueError as e:
        raise ValueError(f"Invalid CIDR '{v}': {e}") from e


def validate_apipa_ip(v: str, field_name: str) -> str:
    """Validate IP is in APIPA range (169.254.0.0/16)."""
    try:
        ip = ipaddress.ip_address(v)
        apipa_range = ipaddress.ip_network("169.254.0.0/16")

        if ip not in apipa_range:
            raise ValueError(f"{field_name} '{v}' must be in APIPA range 169.254.0.0/16")

        return v
    except ipaddress.AddressValueError as e:
        raise ValueError(f"Invalid IP address '{v}': {e}") from e


def _tunnel_location(connection_name: str, tunnel_name: str, instance_index: int) -> str:
    return (
        f"connection '{connection_name}' tunnel '{tunnel_name}' "
        f"(gateway_instance_index={instance_index})"
    )


# ============================================================================
# Configuration Models (bottom-up)
# ============================================================================


class CryptoProposals(BaseModel):
    """IPsec cryptographic proposals."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ike_proposals: list[str] = Field(
        ...,
        min_length=1,
        description="IKE proposal strings (e.g., 'aes256-sha256-modp2048')",
        examples=[["aes256-sha256-modp2048", "aes256gcm16-prfsha256-modp2048"]],
    )
    ike_lifetime_seconds: int = Field(
        ..., ge=3600, le=86400, description="IKE SA lifetime in seconds (3600-86400)"
    )
    esp_proposals: list[str] = Field(
        ...,
        min_length=1,
        description="ESP proposal strings (e.g., 'aes256-sha256')",
        examples=[["aes256-sha256", "aes256gcm16"]],
    )
    esp_lifetime_seconds: int = Field(
        ..., ge=1800, le=14400, description="ESP SA lifetime in seconds (1800-14400)"
    )
    dh_groups: list[int] | None = Field(
        default=None, description="Diffie-Hellman group numbers (e.g., 14, 19, 20)"
    )


class DPDConfig(BaseModel):
    """Dead Peer Detection configuration."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(
        ..., ge=5, le=300, description="DPD check interval in seconds (5-300)"
    )
    timeout_seconds: int = Field(..., ge=15, le=600, description="DPD timeout in seconds (15-600)")

    @model_validator(mode="after")
    def validate_timeout_greater_than_interval(self) -> DPDConfig:
        if self.timeout_seconds <= self.interval_seconds:
            raise ValueError(
                f"timeout_seconds ({self.timeout_seconds}) must be greater than "
                f"interval_seconds ({self.interval_seconds})"
            )
        return self


class BFDConfig(BaseModel):
    """Bidirectional Forwarding Detection (BFD) configuration for BGP."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable BFD for BGP neighbors")
    transmit_interval_ms: int = Field(
        default=300,
        ge=50,
        le=10000,
        description="BFD transmit interval in milliseconds (50-10000)",
    )
    receive_interval_ms: int = Field(
        default=300,
        ge=50,
        le=10000,
        description="BFD receive interval in milliseconds (50-10000)",
    )
    detect_multiplier: int = Field(
        default=3, ge=1, le=50, description="BFD detection multiplier (1-50)"
    )


class BGPDefaults(BaseModel):
    """Default BGP configuration."""

    model_config = ConfigDict(extra="forbid")

    router_id: str | None = Field(
        default=None, description="BGP router ID (IPv4 format, e.g., '169.254.50.1')"
    )
    hold_time_seconds: int = Field(
        default=6, ge=3, le=3600, description="BGP hold time in seconds (3-3600)"
    )
    keepalive_seconds: int = Field(
        default=2,
        ge=1,
        le=1200,
        description="BGP keepalive interval in seconds (1-1200)",
    )
    graceful_restart: bool = Field(default=False, description="Enable BGP graceful restart")
    max_prefixes: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum number of prefixes to accept (1-10000)",
    )
    bfd: BFDConfig = Field(
        default_factory=BFDConfig,
        description="BFD settings for faster BGP failure detection (optional)",
    )

    @field_validator("router_id")
    @classmethod
    def validate_router_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ipaddress.IPv4Address(v)
            return v
        except ValueError as e:
            raise ValueError(f"router_id must be a valid IPv4 address: {e}") from e

    @model_validator(mode="after")
    def validate_keepalive_hold_ratio(self) -> BGPDefaults:
        # Best practice: keepalive should be ~1/3 of hold time
        if self.keepalive_seconds > self.hold_time_seconds / 2:
            raise ValueError(
                f"keepalive_seconds ({self.keepalive_seconds}) should be less than "
                f"half of hold_time_seconds ({self.hold_time_seconds}). "
                f"Recommended: keepalive = hold_time / 3"
            )
        return self


class RoutingDefaults(BaseModel):
    """Default routing configuration."""

    model_config = ConfigDict(extra="forbid")

    mode: RoutingMode = Field(
        default=RoutingMode.BGP, description="Default routing mode for connections"
    )
    bgp: BGPDefaults = Field(default_factory=BGPDefaults, description="BGP-specific defaults")


class AuthConfig(BaseModel):
    """Authentication configuration."""

    model_config = ConfigDict(extra="forbid")

    method: AuthMethod = Field(default=AuthMethod.PSK, description="Authentication method")


class HealthMonitoringConfig(BaseModel):
    """Tunnel health monitoring configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable automatic tunnel health monitoring and recovery",
    )
    check_interval_seconds: int = Field(
        default=10,
        ge=5,
        le=300,
        description="Health check interval in seconds (5-300)",
    )
    max_failures_before_restart: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Number of consecutive failures before auto-restart (1-10)",
    )
    proactive_refresh_enabled: bool = Field(
        default=False,
        description="Enable proactive tunnel refresh (periodic restart regardless of health)",
    )
    proactive_refresh_hours: int = Field(
        default=8,
        ge=1,
        le=72,
        description="Hours between proactive tunnel refreshes (1-72). Only used if proactive_refresh_enabled=true",
    )
    ping_enabled: bool = Field(
        default=False,
        description="Enable ICMP ping to BGP peer for data-plane validation",
    )


class DefaultsConfig(BaseModel):
    """Global defaults for VPN behavior."""

    model_config = ConfigDict(extra="forbid")

    vpn_type: VPNType = Field(default=VPNType.IPSEC, description="VPN protocol type")
    ike_version: int = Field(default=2, ge=1, le=2, description="IKE protocol version (1 or 2)")
    allow_ikev1: bool = Field(default=False, description="Allow IKEv1 fallback")
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Authentication configuration")
    crypto: CryptoProposals = Field(..., description="Default cryptographic proposals")
    dpd: DPDConfig = Field(..., description="Dead Peer Detection configuration")
    ha_mode: HAMode = Field(
        ...,
        description="High availability mode for tunnel selection (required: active-passive)",
    )
    routing: RoutingDefaults = Field(
        default_factory=RoutingDefaults, description="Routing configuration defaults"
    )
    health_monitoring: HealthMonitoringConfig = Field(
        default_factory=HealthMonitoringConfig,
        description="Tunnel health monitoring and auto-recovery configuration",
    )


class GatewayQuotas(BaseModel):
    """Resource quotas for gateway."""

    model_config = ConfigDict(extra="forbid")

    max_connections: int = Field(
        default=16, ge=1, le=100, description="Maximum number of connections (1-100)"
    )
    max_tunnels: int = Field(
        default=32, ge=1, le=200, description="Maximum number of tunnels (1-200)"
    )
    max_total_bandwidth_mbps: int | None = Field(
        default=None, ge=1, description="Maximum total bandwidth in Mbps"
    )


class GatewayConfig(BaseModel):
    """Gateway-wide parameters (BGP ASN, local prefixes, quotas)."""

    model_config = ConfigDict(extra="forbid")

    local_asn: int = Field(..., description="Local BGP ASN (private: 64512-65534, public: 1-64511)")
    local_prefixes: list[str] = Field(
        ...,
        min_length=1,
        description="Local prefixes to advertise (CIDR notation)",
        examples=[["10.0.0.0/16", "10.1.0.0/24"]],
    )
    ipsec_mode: IPsecMode = Field(
        default=IPsecMode.XFRM_INTERFACE,
        description="IPsec interface mode (xfrm-interface only)",
    )
    quotas: GatewayQuotas = Field(default_factory=GatewayQuotas, description="Resource quotas")

    @field_validator("local_asn")
    @classmethod
    def validate_local_asn(cls, v: int) -> int:
        return validate_asn(v)

    @field_validator("local_prefixes")
    @classmethod
    def validate_local_prefixes(cls, v: list[str]) -> list[str]:
        return [validate_cidr(cidr) for cidr in v]


class StaticRoutes(BaseModel):
    """Static routing configuration for a tunnel.

    In static routing mode, remote_prefixes define the actual networks to route.
    These are propagated from connection.remote_prefixes if not set per-tunnel.

    Note: gateway.local_prefixes is the single source of truth for local networks.
    Per-tunnel overrides are not supported in route-based VPN architecture.
    """

    model_config = ConfigDict(extra="forbid")

    remote_prefixes: list[str] | None = Field(
        default=None,
        description="Remote networks to route through this tunnel in static mode (CIDR notation)",
    )

    @field_validator("remote_prefixes")
    @classmethod
    def validate_remote_prefixes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [validate_cidr(cidr) for cidr in v]


class TunnelConfig(BaseModel):
    """VPN tunnel configuration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$",
        description="Tunnel name (lowercase, alphanumeric, hyphens)",
    )
    gateway_instance_index: int = Field(
        ..., ge=0, description="Gateway VM instance index (0-based)"
    )
    local_public_ip_index: int | None = Field(
        default=None,
        ge=0,
        description="Index of local public IP to use (for multi-NIC)",
    )
    ha_role: HARole = Field(default=HARole.ACTIVE, description="HA role for this tunnel")
    ike_version: int | None = Field(
        default=None, ge=1, le=2, description="IKE version override (1 or 2)"
    )
    remote_public_ip: str = Field(..., description="Remote peer public IP address")
    psk: str = Field(..., min_length=8, description="Pre-shared key (minimum 8 characters)")
    inner_cidr: str = Field(..., description="Inner tunnel CIDR (must be /30 in 169.254.0.0/16)")
    inner_local_ip: str = Field(..., description="Inner local IP (must be within inner_cidr)")
    inner_remote_ip: str = Field(..., description="Inner remote IP (must be within inner_cidr)")
    crypto: CryptoProposals | None = Field(default=None, description="Crypto proposals override")
    static_routes: StaticRoutes | None = Field(
        default=None, description="Static routing configuration (for static mode only)"
    )

    # Optional escape hatch for custom metadata
    extensions: dict[str, t.Any] = Field(
        default_factory=dict, description="Custom extensions (opaque metadata)"
    )

    @field_validator("remote_public_ip")
    @classmethod
    def validate_remote_public_ip(cls, v: str) -> str:
        return validate_ip_address(v)

    @field_validator("inner_cidr")
    @classmethod
    def validate_inner_cidr(cls, v: str) -> str:
        return validate_apipa_cidr(v)

    @field_validator("inner_local_ip")
    @classmethod
    def validate_inner_local_ip(cls, v: str) -> str:
        return validate_apipa_ip(v, "inner_local_ip")

    @field_validator("inner_remote_ip")
    @classmethod
    def validate_inner_remote_ip(cls, v: str) -> str:
        return validate_apipa_ip(v, "inner_remote_ip")

    @model_validator(mode="after")
    def validate_inner_ips_in_cidr(self) -> TunnelConfig:
        """Validate that inner IPs are within inner_cidr and not network/broadcast."""
        try:
            network = ipaddress.ip_network(self.inner_cidr, strict=False)
            local_ip = ipaddress.ip_address(self.inner_local_ip)
            remote_ip = ipaddress.ip_address(self.inner_remote_ip)

            # Check if IPs are within the network
            if local_ip not in network:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_local_ip {self.inner_local_ip} "
                    f"is NOT within inner_cidr {self.inner_cidr}. "
                    f"Network range: {network.network_address} - {network.broadcast_address}"
                )

            if remote_ip not in network:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_remote_ip {self.inner_remote_ip} "
                    f"is NOT within inner_cidr {self.inner_cidr}. "
                    f"Network range: {network.network_address} - {network.broadcast_address}"
                )

            # Check for network or broadcast address
            if local_ip == network.network_address:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_local_ip {self.inner_local_ip} "
                    f"is the network address. Use a host address within {self.inner_cidr}"
                )

            if local_ip == network.broadcast_address:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_local_ip {self.inner_local_ip} "
                    f"is the broadcast address. Use a host address within {self.inner_cidr}"
                )

            if remote_ip == network.network_address:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_remote_ip {self.inner_remote_ip} "
                    f"is the network address. Use a host address within {self.inner_cidr}"
                )

            if remote_ip == network.broadcast_address:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_remote_ip {self.inner_remote_ip} "
                    f"is the broadcast address. Use a host address within {self.inner_cidr}"
                )

            # Check that local and remote IPs are different
            if local_ip == remote_ip:
                raise ValueError(
                    f"Tunnel '{self.name}': inner_local_ip and inner_remote_ip "
                    f"cannot be the same ({self.inner_local_ip})"
                )

        except (ipaddress.AddressValueError, ValueError) as e:
            if isinstance(e, ValueError) and "Tunnel" in str(e):
                raise  # Re-raise our validation errors
            raise ValueError(f"Tunnel '{self.name}': Invalid IP/CIDR format - {e}") from e

        return self


class BGPConfig(BaseModel):
    """BGP configuration for a connection."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(..., description="Enable BGP for this connection")
    remote_asn: int | None = Field(default=None, description="Remote peer BGP ASN")
    advertise_local_prefixes: bool = Field(
        default=True, description="Advertise gateway.local_prefixes to this peer"
    )
    remote_prefixes: list[str] | None = Field(
        default=None,
        description="Optional: Remote prefixes for filtering/validation. In BGP mode, routes are learned dynamically; this field can optionally whitelist allowed prefixes. Not used for manual route installation in BGP mode.",
    )

    @field_validator("remote_asn")
    @classmethod
    def validate_remote_asn(cls, v: int | None) -> int | None:
        if v is None:
            return v
        return validate_asn(v)

    @field_validator("remote_prefixes")
    @classmethod
    def validate_remote_prefixes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [validate_cidr(cidr) for cidr in v]

    @model_validator(mode="after")
    def validate_bgp_requires_remote_asn(self) -> BGPConfig:
        if self.enabled and self.remote_asn is None:
            raise ValueError("remote_asn is required when BGP is enabled")
        return self


class ConnectionConfig(BaseModel):
    """VPN connection to a peer."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$",
        description="Connection name (lowercase, alphanumeric, hyphens)",
    )
    description: str | None = Field(
        default=None, max_length=256, description="Human-readable description"
    )
    vendor: VendorType = Field(..., description="Peer vendor type")
    routing_mode: RoutingMode = Field(..., description="Routing mode for this connection")
    remote_prefixes: list[str] | None = Field(
        default=None,
        description=(
            "Remote prefixes (CIDR notation). In 'static' routing_mode: required "
            "(unless specified per-tunnel in static_routes) and used for installing "
            "static routes. In 'bgp' routing_mode: optional, used for filtering/"
            "validating received BGP routes (routes are learned dynamically)."
        ),
    )
    bgp: BGPConfig = Field(..., description="BGP configuration")
    tunnels: list[TunnelConfig] = Field(..., min_length=1, description="Tunnel configurations")

    # Optional escape hatch for custom metadata
    extensions: dict[str, t.Any] = Field(
        default_factory=dict, description="Custom extensions (opaque metadata)"
    )

    @field_validator("remote_prefixes")
    @classmethod
    def validate_remote_prefixes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [validate_cidr(cidr) for cidr in v]

    @model_validator(mode="after")
    def validate_routing_mode_consistency(self) -> ConnectionConfig:
        """Validate BGP config matches routing mode."""
        # In static mode, require remote_prefixes (connection-level or per-tunnel)
        if self.routing_mode == RoutingMode.STATIC:
            if not self.remote_prefixes:
                # Check if any tunnel has static_routes.remote_prefixes
                has_tunnel_remote_prefixes = any(
                    t.static_routes and t.static_routes.remote_prefixes for t in self.tunnels
                )
                if not has_tunnel_remote_prefixes:
                    raise ValueError(
                        f"Connection '{self.name}' uses static routing but has no remote_prefixes defined. "
                        "Set connection.remote_prefixes or tunnel.static_routes.remote_prefixes."
                    )

        if self.routing_mode == RoutingMode.BGP:
            if not self.bgp.enabled:
                raise ValueError(
                    f"Connection '{self.name}': routing_mode is 'bgp' but bgp.enabled is false. "
                    f"Either set routing_mode='static' or bgp.enabled=true"
                )
        elif self.routing_mode == RoutingMode.STATIC:
            if self.bgp.enabled:
                raise ValueError(
                    f"Connection '{self.name}': routing_mode is 'static' but bgp.enabled is true. "
                    f"Either set routing_mode='bgp' or bgp.enabled=false"
                )
        return self

    @model_validator(mode="after")
    def validate_tunnel_names_unique(self) -> ConnectionConfig:
        """Ensure tunnel names are unique within connection."""
        names = [t.name for t in self.tunnels]
        duplicates = [n for n in names if names.count(n) > 1]
        if duplicates:
            raise ValueError(
                f"Connection '{self.name}': Duplicate tunnel names found: {set(duplicates)}"
            )
        return self


class VMSpec(BaseModel):
    """VM specification for gateway instances."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    platform: Platform = Field(..., description="Nebius compute platform")
    preset: str = Field(
        ..., pattern=r"^\d+vcpu-\d+gb$", description="VM preset (e.g., '32vcpu-128gb')"
    )
    disk_boot_image: str = Field(
        ..., description="Boot disk image (e.g., 'ubuntu24.04-driverless')"
    )
    disk_gb: int = Field(..., ge=20, le=2000, description="Boot disk size in GB (20-2000)")
    disk_type: DiskType = Field(..., description="Disk type")
    disk_block_bytes: int = Field(
        ..., ge=4096, description="Disk block size in bytes (minimum 4096)"
    )
    num_nics: int = Field(
        default=1,
        ge=1,
        le=1,  # Platform limitation
        description="Number of NICs (currently limited to 1)",
    )
    ssh_public_key: str | None = Field(default=None, description="SSH public key content (inline)")
    ssh_public_key_path: str | None = Field(default=None, description="Path to SSH public key file")
    ssh_private_key_path: str | None = Field(
        default=None, description="Path to SSH private key file"
    )

    @field_validator("num_nics")
    @classmethod
    def validate_num_nics(cls, v: int) -> int:
        if v > 1:
            raise ValueError(
                f"num_nics={v} requested, but current Nebius platform only supports 1 NIC per instance. "
                "Set num_nics=1 in your config."
            )
        return v

    @model_validator(mode="after")
    def validate_ssh_key_config(self) -> VMSpec:
        """Ensure either ssh_public_key or ssh_public_key_path is provided."""
        if not self.ssh_public_key and not self.ssh_public_key_path:
            raise ValueError("Either ssh_public_key or ssh_public_key_path must be provided")
        return self


class GatewaySubnetConfig(BaseModel):
    """Gateway subnet selection and creation policy."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        default="vpngw-subnet",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$",
        description="Gateway subnet name (lowercase, alphanumeric, hyphens)",
    )
    cidr: str | None = Field(
        default=None,
        description=(
            "Explicit private IPv4 CIDR for the gateway subnet. "
            "If set and the CIDR is outside the current network pool, the deployer "
            "will extend the network pool when the network has exactly one private pool."
        ),
    )
    prefix_length: int = Field(
        default=24,
        ge=8,
        le=28,
        description=(
            "Prefix length to auto-carve when cidr is omitted. "
            "The runtime validates this against the target network pool."
        ),
    )

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_private_ipv4_cidr(v)


class GatewayGroup(BaseModel):
    """Gateway group infrastructure specification."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$",
        description="Gateway group name (lowercase, alphanumeric, hyphens)",
    )
    instance_count: int = Field(..., ge=1, le=10, description="Number of gateway VMs (1-10)")
    external_ips: list[list[str]] | None = Field(
        default=None,
        description=(
            "Pre-allocated external IPs per instance (list per instance; each inner "
            "list maps to NICs)"
        ),
    )
    network_id: str | None = Field(
        default=None,
        description=(
            "Network ID (VPC network) to use for the gateway group. Optional. "
            "If not specified, auto-discovers in this order: "
            "1) default-network, 2) single custom network if only one exists, "
            "3) errors if multiple custom networks found (specify gateway_group.network_id to resolve)"
        ),
    )
    subnet: GatewaySubnetConfig = Field(
        default_factory=GatewaySubnetConfig,
        description=(
            "Dedicated gateway subnet configuration. "
            "By default, the deployer creates or reuses 'vpngw-subnet' and auto-carves "
            "the first free /24 from the target network's private pool."
        ),
    )
    vm_spec: VMSpec = Field(..., description="VM specification")
    region: str | None = Field(
        default=None, description="Region ID (can be set at top level or here)"
    )

    @field_validator("external_ips", mode="before")
    @classmethod
    def reject_flat_external_ips(cls, v: t.Any) -> t.Any:
        if v is None:
            return v
        if not isinstance(v, list):
            return v
        if not v:
            return v
        if all(isinstance(item, (str, type(None))) for item in v):
            raise ValueError(
                "external_ips must be a list of lists (one list per instance), "
                'for example: [["203.0.113.10"], ["203.0.113.20"]]'
            )
        for item in v:
            if isinstance(item, (str, type(None))):
                raise ValueError("external_ips must be a list of lists (one list per instance)")
        return v

    @field_validator("external_ips")
    @classmethod
    def validate_external_ips(cls, v: list[list[str]] | None) -> list[list[str]] | None:
        if v is None:
            return v

        # Validate each IP address
        for i, ips in enumerate(v):
            if not ips:
                continue
            if not isinstance(ips, list):
                raise ValueError(f"external_ips[{i}] must be a list of IPs")
            for j, ip in enumerate(ips):
                if not ip:  # Skip empty strings (placeholders)
                    continue
                try:
                    validate_ip_address(ip)
                except ValueError as e:
                    raise ValueError(f"external_ips[{i}][{j}]: {e}") from e

        return v


class VPNGatewayConfig(BaseModel):
    """Complete VPN gateway configuration (v1 schema)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = Field(
        ...,
        ge=1,
        le=1,
        description="Config schema version (currently only v1 supported)",
    )
    tenant_id: str = Field(..., description="Nebius tenant ID")
    project_id: str = Field(..., description="Nebius project ID")
    region_id: str = Field(..., description="Nebius region ID (e.g., 'eu-north1-a')")
    gateway_group: GatewayGroup = Field(..., description="Gateway infrastructure specification")
    gateway: GatewayConfig = Field(..., description="Gateway-wide parameters")
    defaults: DefaultsConfig = Field(..., description="Global defaults for VPN behavior")
    connections: list[ConnectionConfig] = Field(
        ..., min_length=1, description="VPN connections to peers"
    )

    @model_validator(mode="after")
    def validate_connection_names_unique(self) -> VPNGatewayConfig:
        """Ensure connection names are unique."""
        names = [c.name for c in self.connections]
        duplicates = [n for n in names if names.count(n) > 1]
        if duplicates:
            raise ValueError(f"Duplicate connection names found: {set(duplicates)}")
        return self

    @model_validator(mode="after")
    def validate_tunnel_names_unique_globally(self) -> VPNGatewayConfig:
        """Ensure tunnel names are unique across the full config."""
        locations_by_name: dict[str, list[str]] = {}
        for conn in self.connections:
            for tunnel in conn.tunnels:
                locations_by_name.setdefault(tunnel.name, []).append(
                    _tunnel_location(conn.name, tunnel.name, tunnel.gateway_instance_index)
                )

        duplicates = {
            name: locations for name, locations in locations_by_name.items() if len(locations) > 1
        }
        if duplicates:
            details = "; ".join(
                f"{name}: {', '.join(locations)}" for name, locations in sorted(duplicates.items())
            )
            raise ValueError(
                f"Tunnel names must be globally unique across all connections. Conflicts: {details}"
            )
        return self

    @model_validator(mode="after")
    def validate_unique_apipa_values_per_instance(self) -> VPNGatewayConfig:
        """Ensure APIPA tunnel values are unique per gateway instance."""
        conflict_messages: list[str] = []

        def collect_conflicts(field_name: str) -> None:
            locations_by_key: dict[tuple[int, str], list[str]] = {}
            for conn in self.connections:
                for tunnel in conn.tunnels:
                    value = getattr(tunnel, field_name, None)
                    if not value:
                        continue
                    key = (tunnel.gateway_instance_index, str(value))
                    locations_by_key.setdefault(key, []).append(
                        _tunnel_location(conn.name, tunnel.name, tunnel.gateway_instance_index)
                    )

            conflicts = {
                key: locations for key, locations in locations_by_key.items() if len(locations) > 1
            }
            if conflicts:
                details = "; ".join(
                    f"{field_name}='{value}' on gateway_instance_index={instance_index}: "
                    f"{', '.join(locations)}"
                    for (instance_index, value), locations in sorted(conflicts.items())
                )
                conflict_messages.append(
                    f"{field_name} must be unique per gateway instance across all connections. "
                    f"Conflicts: {details}"
                )

        collect_conflicts("inner_cidr")
        collect_conflicts("inner_local_ip")
        collect_conflicts("inner_remote_ip")
        if conflict_messages:
            raise ValueError(" ".join(conflict_messages))
        return self

    @model_validator(mode="after")
    def validate_single_active_tunnel_per_instance(self) -> VPNGatewayConfig:
        """Enforce exactly one active tunnel per connection per gateway instance."""
        if self.defaults.ha_mode == HAMode.ACTIVE_ACTIVE:
            raise ValueError(
                "ha_mode 'active-active' is reserved for future use; "
                "current releases require ha_mode='active-passive'."
            )

        for conn in self.connections:
            active_counts: dict[int, int] = {}
            for tunnel in conn.tunnels:
                instance_index = tunnel.gateway_instance_index
                active_counts.setdefault(instance_index, 0)
                if tunnel.ha_role == HARole.ACTIVE:
                    active_counts[instance_index] += 1

            for instance_index, active_count in active_counts.items():
                if active_count != 1:
                    raise ValueError(
                        f"Connection '{conn.name}' (gateway_instance_index={instance_index}) "
                        f"must have exactly one active tunnel; found {active_count}. "
                        "Set one tunnel ha_role='active' and others 'passive' or 'disable'."
                    )

        return self

    @model_validator(mode="after")
    def validate_quotas(self) -> VPNGatewayConfig:
        """Validate resource usage against quotas."""
        total_connections = len(self.connections)
        total_tunnels = sum(len(c.tunnels) for c in self.connections)

        if total_connections > self.gateway.quotas.max_connections:
            raise ValueError(
                f"Total connections ({total_connections}) exceeds "
                f"max_connections quota ({self.gateway.quotas.max_connections})"
            )

        if total_tunnels > self.gateway.quotas.max_tunnels:
            raise ValueError(
                f"Total tunnels ({total_tunnels}) exceeds "
                f"max_tunnels quota ({self.gateway.quotas.max_tunnels})"
            )

        return self

    @model_validator(mode="after")
    def validate_tunnel_instance_indices(self) -> VPNGatewayConfig:
        """Validate tunnel instance indices are within instance_count."""
        instance_count = self.gateway_group.instance_count

        for conn in self.connections:
            for tunnel in conn.tunnels:
                if tunnel.gateway_instance_index >= instance_count:
                    raise ValueError(
                        f"Tunnel '{tunnel.name}' in connection '{conn.name}': "
                        f"gateway_instance_index ({tunnel.gateway_instance_index}) "
                        f"is >= instance_count ({instance_count})"
                    )

        return self

    @model_validator(mode="after")
    def validate_external_ips_match_instance_count(self) -> VPNGatewayConfig:
        """Validate external_ips list does not exceed instance_count if provided."""
        external_ips = self.gateway_group.external_ips
        if external_ips is not None and external_ips:
            # Filter out empty strings (unresolved placeholders)
            non_empty_ips = [ip for inst in external_ips for ip in inst if ip]
            instance_count = self.gateway_group.instance_count
            # Only validate if we have non-empty IPs
            if non_empty_ips and len(external_ips) > instance_count:
                raise ValueError(
                    f"external_ips has {len(external_ips)} entries but "
                    f"instance_count is {instance_count}. It cannot exceed instance_count."
                )

        return self

    @model_validator(mode="after")
    def validate_external_ips_unique_globally(self) -> VPNGatewayConfig:
        """Ensure configured external IP assignments are globally unique."""
        external_ips = self.gateway_group.external_ips
        if not external_ips:
            return self

        locations_by_ip: dict[str, list[str]] = {}
        for inst_index, inst_ips in enumerate(external_ips):
            for nic_index, ip in enumerate(inst_ips or []):
                if not ip:
                    continue
                normalized_ip = str(ipaddress.ip_address(ip))
                locations_by_ip.setdefault(normalized_ip, []).append(
                    f"external_ips[{inst_index}][{nic_index}]"
                )

        duplicates = {
            ip: locations for ip, locations in locations_by_ip.items() if len(locations) > 1
        }
        if duplicates:
            details = "; ".join(
                f"{ip}: {', '.join(locations)}" for ip, locations in sorted(duplicates.items())
            )
            raise ValueError(
                f"gateway_group.external_ips entries must be globally unique. Conflicts: {details}"
            )

        return self


# ============================================================================
# Public API
# ============================================================================


def validate_config(config_dict: dict) -> VPNGatewayConfig:
    """Validate a configuration dictionary against the schema.

    Args:
        config_dict: Raw configuration dictionary from YAML

    Returns:
        Validated VPNGatewayConfig instance

    Raises:
        pydantic.ValidationError: If validation fails
    """
    return VPNGatewayConfig.model_validate(config_dict)


def validate_config_from_yaml(yaml_str: str) -> VPNGatewayConfig:
    """Validate a YAML string against the schema.

    Args:
        yaml_str: Raw YAML string

    Returns:
        Validated VPNGatewayConfig instance

    Raises:
        pydantic.ValidationError: If validation fails
        yaml.YAMLError: If YAML parsing fails
    """
    import yaml

    config_dict = yaml.safe_load(yaml_str)
    return validate_config(config_dict)
