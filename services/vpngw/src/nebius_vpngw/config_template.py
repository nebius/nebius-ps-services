"""
Embedded YAML configuration template for Nebius VPN Gateway.

This template is embedded in the code to ensure it always stays in sync with the schema.
The template cannot be modified by users without changing the installed package.
When users run 'nebius-vpngw' without a config file, this template is written to
'nebius-vpngw.config.yaml' in their current directory for them to customize.

The template includes detailed comments and examples to guide users in configuring
their VPN gateway. All fields align with the Pydantic schema defined in schema.py.
"""

# Schema version aligned with schema.py
SCHEMA_VERSION = 1

# Default template written to user's directory on first run
DEFAULT_CONFIG_TEMPLATE = """\
# Nebius VPN Gateway Configuration
# Generated from embedded template (schema version {version})
# 
# YAML structure:
#   - gateway_group: VM infrastructure (instances, IPs, VM specs)
#   - gateway: Routing identity (ASN, local prefixes, quotas)
#   - defaults: Global VPN behavior (crypto, DPD, BGP settings)
#   - connections: Peer gateways with tunnels
#
# Override hierarchy: tunnel > connection > defaults
# Local prefixes: gateway.local_prefixes is the single source of truth
# Environment variables: Use ${{VAR}} syntax (e.g., ${{GCP_PSK}})
# Security: Keep secrets out of git-tracked files

version: {version}

###############################################################################
# Nebius Project Context
###############################################################################
# REQUIRED: Set these via environment variables or replace with literal values
tenant_id: "${{TENANT_ID}}"
project_id: "${{PROJECT_ID}}"
region_id: "${{REGION_ID}}"  # Examples: eu-north1, eu-west1, us-central1

###############################################################################
# Gateway Group: VM Infrastructure
###############################################################################
gateway_group:
  name: "nebius-vpn-gw"
  instance_count: 1  # Number of gateway VMs (1 for simple, 2+ for HA)

  # External IPs (optional): Nested list [instance_index][nic_index]
  # - Omit or leave empty: Auto-allocate public IPs for all NICs
  # - Provide IPs: Use existing IP allocations (create missing ones)
  # - Platform constraint: Currently 1 NIC per VM
  #
  # Examples:
  #   Single VM, auto-allocate:
  #     external_ips: []
  #
  #   Single VM, use existing IP:
  #     external_ips:
  #       - ["203.0.113.10"]
  #
  #   Two VMs, existing IPs:
  #     external_ips:
  #       - ["203.0.113.10"]  # VM 0
  #       - ["203.0.113.20"]  # VM 1
  external_ips: []

  # Management access (optional): Restrict SSH to specific CIDRs
  # Omit to allow SSH from anywhere (not recommended for production)
  # management_cidrs:
  #   - "10.0.0.0/8"        # Corporate network
  #   - "203.0.113.0/24"    # VPN range

  # VM Specification
  vm_spec:
    platform: "cpu-d3"              # Options: cpu-e2, cpu-d3
    preset: "4vcpu-16gb"            # See Nebius docs for available presets
    disk_boot_image: "ubuntu24.04-driverless"
    disk_gb: 50
    disk_type: "network_ssd"        # Options: network_ssd, network_ssd_nonreplicated
    disk_block_bytes: 4096
    num_nics: 1                     # Platform currently supports 1 NIC

    # SSH Access
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"  # File is auto-read and inlined
    ssh_private_key_path: "~/.ssh/id_ed25519"     # Optional, uses SSH agent if omitted

    # VPC Network (optional): Defaults to your project's default VPC
    # network_id: "my-vpc-network-id"

###############################################################################
# Gateway: Routing Identity and Prefixes
###############################################################################
gateway:
  # BGP ASN (required for BGP mode, ignored for static mode)
  # Use private ASN (64512-65534) that doesn't conflict with your network
  local_asn: 65010

  # Local prefixes: SINGLE SOURCE OF TRUTH for Nebius-side networks
  # - BGP mode: Advertised to peers when advertise_local_prefixes=true
  # - Static mode: Used as leftsubnet in IPsec (unless overridden per-tunnel)
  # List all VPC subnets and workload CIDRs that should be accessible via VPN
  local_prefixes:
    - "10.0.0.0/16"  # Example: Main VPC CIDR
    # - "10.1.0.0/16"  # Example: Additional subnet

  # Resource quotas (optional)
  quotas:
    max_connections: 16
    max_tunnels: 32
    max_total_bandwidth_mbps: null  # null = unlimited

###############################################################################
# Defaults: Global VPN Behavior
###############################################################################
defaults:
  vpn_type: "ipsec"
  ike_version: 2        # Default to IKEv2
  allow_ikev1: true     # Allow fallback to IKEv1 if peer requires it

  auth:
    method: "psk"       # Pre-shared key authentication

  # Cryptographic proposals
  crypto:
    # IKE Phase 1 proposals (encryption-integrity-dhgroup)
    # Order matters: strongSwan tries from top to bottom
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"  # GCP HA VPN, modern cipher
      - "aes256-sha256-modp2048"          # GCP HA VPN, compatible
      - "aes256-sha1-modp1024"            # Legacy fallback
    ike_lifetime_seconds: 28800  # 8 hours

    # ESP Phase 2 proposals (encryption-integrity-dhgroup)
    esp_proposals:
      - "aes256gcm16-modp2048"    # GCP HA VPN, modern AEAD
      - "aes256-sha256-modp2048"  # GCP HA VPN, compatible
      - "aes256-sha1-modp1024"    # Legacy fallback
    esp_lifetime_seconds: 3600   # 1 hour

    # Diffie-Hellman groups
    dh_groups:
      - 14  # modp2048
      - 19  # ecp256
      - 20  # ecp384

  # Dead Peer Detection
  dpd:
    interval_seconds: 30   # Check every 30 seconds
    timeout_seconds: 120   # Consider peer dead after 120 seconds

  # Routing mode
  routing:
    mode: "bgp"  # Options: "bgp" or "static"

    # BGP settings (only used when mode=bgp)
    bgp:
      router_id: null  # null = auto-select from local IPs
      hold_time_seconds: 60
      keepalive_seconds: 20  # Should be < hold_time/3
      graceful_restart: true
      max_prefixes: 1000

###############################################################################
# Connections: Peer Gateways
###############################################################################
# Each connection represents one peer gateway (e.g., GCP, AWS, on-prem)
# A connection can have multiple tunnels for HA
# Settings cascade: tunnel > connection > defaults

connections:
  # Example 1: GCP HA VPN with BGP (active-active tunnels)
  - name: "gcp-ha-vpn"
    description: "GCP HA VPN with BGP routing"
    vendor: "gcp"
    routing_mode: "bgp"

    # Remote prefixes: Networks on the peer side (GCP VPC subnets)
    # For BGP: These are learned dynamically, but list here for reference
    # For static: These become rightsubnet in IPsec
    remote_prefixes:
      - "10.10.0.0/24"  # Example: GCP VPC subnet

    bgp:
      enabled: true
      remote_asn: 64514  # REQUIRED: GCP Cloud Router ASN
      advertise_local_prefixes: true

    tunnels:
      # Tunnel 1: First interface of GCP HA VPN gateway
      - name: "gcp-ha-tunnel-1"
        gateway_instance_index: 0  # Which Nebius VM (0 = first VM)
        local_public_ip_index: 0   # Which NIC's public IP (0 = first NIC)
        ha_role: "active"          # Options: "active" or "passive"

        # Peer details
        remote_public_ip: "203.0.113.1"  # REPLACE: GCP HA VPN interface 0 IP
        psk: "${{GCP_TUNNEL_1_PSK}}"      # REPLACE: Min 8 chars, set via env var

        # Inner tunnel IPs for BGP peering
        # MUST be /30 subnet in APIPA range (169.254.0.0/16)
        # Example: 169.254.10.0/30 → usable IPs are .1 and .2
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"   # Nebius side
        inner_remote_ip: "169.254.10.2"  # GCP Cloud Router side

      # Tunnel 2: Second interface of GCP HA VPN gateway
      - name: "gcp-ha-tunnel-2"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "active"

        remote_public_ip: "203.0.113.2"  # REPLACE: GCP HA VPN interface 1 IP
        psk: "${{GCP_TUNNEL_2_PSK}}"

        inner_cidr: "169.254.11.0/30"
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"

  # Example 2: GCP Classic VPN with static routing
  # - name: "gcp-classic-vpn"
  #   description: "GCP Classic VPN with static routes"
  #   vendor: "gcp"
  #   routing_mode: "static"
  #
  #   remote_prefixes:
  #     - "10.20.0.0/24"
  #
  #   bgp:
  #     enabled: false
  #
  #   tunnels:
  #     - name: "gcp-classic-tunnel-1"
  #       gateway_instance_index: 0
  #       remote_public_ip: "203.0.113.3"  # REPLACE
  #       psk: "${{GCP_CLASSIC_PSK}}"
  #
  #       # Static mode: no inner IPs needed
  #       # Uses gateway.local_prefixes and connection.remote_prefixes

  # Example 3: On-premises router with static routing
  # - name: "onprem-router"
  #   description: "On-premises Cisco router"
  #   vendor: "cisco"
  #   routing_mode: "static"
  #
  #   remote_prefixes:
  #     - "192.168.0.0/16"  # On-prem network
  #
  #   bgp:
  #     enabled: false
  #
  #   tunnels:
  #     - name: "onprem-tunnel-1"
  #       gateway_instance_index: 0
  #       ike_version: 1  # Override if peer requires IKEv1
  #       remote_public_ip: "203.0.113.5"  # REPLACE
  #       psk: "${{ONPREM_PSK}}"
  #
  #       # Custom crypto (override defaults)
  #       crypto:
  #         ike_proposals:
  #           - "aes256-sha256-modp2048"
  #         esp_proposals:
  #           - "aes256-sha256"

###############################################################################
# Next Steps:
# 1. Replace placeholder values (IPs, ASNs, PSKs)
# 2. Set environment variables for secrets:
#      export GCP_TUNNEL_1_PSK="your-secret-here"
#      export GCP_TUNNEL_2_PSK="your-secret-here"
# 3. Validate config:
#      nebius-vpngw validate-config nebius-vpngw.config.yaml
# 4. Deploy gateway:
#      nebius-vpngw apply --local-config-file nebius-vpngw.config.yaml
###############################################################################
""".format(version=SCHEMA_VERSION)
