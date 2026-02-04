"""
Embedded YAML configuration template for Nebius VPN Gateway.

This template is embedded in the code to ensure it always stays in sync with the schema.
The template cannot be modified by users without changing the installed package.
When users run 'nebius-vpngw' without a config file, this template is written to
'nebius-vpngw.config.yaml' in their current directory for them to customize.

The template includes concise comments and minimal examples to guide users in configuring
their VPN gateway. All fields align with the Pydantic schema defined in schema.py.
"""

# Schema version aligned with schema.py
SCHEMA_VERSION = 1

# Default template written to user's directory on first run
DEFAULT_CONFIG_TEMPLATE = f"""\
# Nebius VPN Gateway config (schema v{SCHEMA_VERSION})
# Notes:
# - Override order: tunnel > connection > defaults
# - gateway.local_prefixes is the source of truth
# - Use ${{VAR}} for secrets; keep *.config.yaml out of git

version: {SCHEMA_VERSION}

# Project context (required)
tenant_id: "${{TENANT_ID}}"
project_id: "${{PROJECT_ID}}"
region_id: "${{REGION_ID}}"  # e.g., eu-north1

gateway_group:
  name: "nebius-vpn-gw"
  instance_count: 1
  external_ips: []  # []=auto
  # Example (list per VM, inner list per NIC):
  # external_ips:
  #   - ["203.0.113.10"]  # VM0 NIC0
  #   - ["203.0.113.20"]  # VM1 NIC0

  vm_spec:
    platform: "cpu-d3"          # cpu-e2|cpu-d3
    preset: "4vcpu-16gb"
    disk_boot_image: "ubuntu24.04-driverless"
    disk_gb: 100
    disk_type: "network_ssd"
    disk_block_bytes: 4096
    num_nics: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"
    ssh_private_key_path: "~/.ssh/id_ed25519"
    # network_id: "vpcnetwork-abc123def456"

gateway:
  local_asn: 65010
  local_prefixes:
    - "10.0.0.0/16"
  ipsec_mode: "xfrm-interface"
  quotas:
    max_connections: 16
    max_tunnels: 32
    max_total_bandwidth_mbps: null

defaults:
  vpn_type: "ipsec"
  ike_version: 2
  allow_ikev1: false
  auth:
    method: "psk"

  crypto:
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"
      - "aes256-sha256-modp2048"
    ike_lifetime_seconds: 28800
    esp_proposals:
      - "aes256gcm16-modp2048"
      - "aes256-sha256-modp2048"
    esp_lifetime_seconds: 3600
    dh_groups:
      - 14
      - 19
      - 20

  dpd:
    interval_seconds: 5
    timeout_seconds: 15  # timeout > interval

  health_monitoring:
    enabled: true
    check_interval_seconds: 10
    max_failures_before_restart: 2
    proactive_refresh_enabled: false
    proactive_refresh_hours: 8
    ping_enabled: false

  ha_mode: "active-passive"  # one active tunnel per connection per VM

  routing:
    mode: "bgp"  # bgp|static
    bgp:
      router_id: null
      hold_time_seconds: 6
      keepalive_seconds: 2
      graceful_restart: false
      max_prefixes: 1000
      bfd:
        enabled: false  # enable only if peer supports BFD
        transmit_interval_ms: 300
        receive_interval_ms: 300
        detect_multiplier: 3

connections:
  - name: "gcp-ha-vpn"
    vendor: "gcp"
    routing_mode: "bgp"
    # remote_prefixes: ["10.0.0.0/8"]  # optional allowlist for BGP
    bgp:
      enabled: true
      remote_asn: 64514
      advertise_local_prefixes: true
    tunnels:
      - name: "gcp-ha-tunnel-1"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "active"  # exactly one active per connection per VM
        remote_public_ip: "203.0.113.1"
        psk: "${{GCP_TUNNEL_1_PSK}}"
        inner_cidr: "169.254.10.0/30"  # inner_cidr must be /30
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: "gcp-ha-tunnel-2"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "passive"
        remote_public_ip: "203.0.113.2"
        psk: "${{GCP_TUNNEL_2_PSK}}"
        inner_cidr: "169.254.11.0/30"  # inner_cidr must be /30
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"

  # Static routing example (remote_prefixes required when routing_mode=static)
  # - name: "onprem-static"
  #   vendor: "cisco"
  #   routing_mode: "static"
  #   remote_prefixes:
  #     - "192.168.0.0/16"
  #   bgp:
  #     enabled: false
  #   tunnels:
  #     - name: "onprem-tunnel-1"
  #       gateway_instance_index: 0
  #       remote_public_ip: "203.0.113.5"
  #       psk: "${{ONPREM_PSK}}"
  #       inner_cidr: "169.254.30.0/30"
  #       inner_local_ip: "169.254.30.1"
  #       inner_remote_ip: "169.254.30.2"

# Next steps:
#   nebius-vpngw validate-config nebius-vpngw.config.yaml
#   nebius-vpngw apply --local-config-file nebius-vpngw.config.yaml
"""
