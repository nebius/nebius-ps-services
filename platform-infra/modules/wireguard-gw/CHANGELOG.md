# Changelog

## Unreleased

- Refactored the module into a wrapper around `../vm`; the shared VM module now
  owns Compute disk/instance/network behavior, and this module keeps the
  WireGuard-specific cloud-init and static public IP allocation policy.
- Made `platform`, `preset`, and `source_image_family` explicit required
  inputs instead of relying on module-local hardcoded VM defaults.
- Made `boot_disk_size_gib` explicit; `nebius-cxcli` renders the recommended
  value from its shared Compute boot-disk policy.
- Added pass-through boot-disk managed-encryption and deletion-protection
  inputs from the shared VM module.
- Removed Terraform `moved` compatibility blocks so new deployments have one
  canonical VM-module-backed state address without legacy resource-address
  adoption.
- Moved day-2 WireGuard client generation out of fixed cloud-init peer
  rendering into the VM-local `nebius-wireguard-client` command, which
  auto-assigns the next free tunnel address, updates `wg0`, stores client
  metadata on the VM, and emits client configs on demand.
- Changed generated WireGuard client names to short `wg-...` values and limited
  explicit names to 15 characters so downloaded `.conf` files work directly
  with `wg-quick`.
- Changed client seed fields to `client_wg_tunnel_address` and
  `local_subnets`; client SSH config generation is no longer part of the
  Terraform module contract.
- Added `local_subnets`, `client_default_dns`, and
  `client_default_persistent_keepalive` for day-2 client generation defaults.
- Changed the default tunnel CIDR to `10.8.0.1/22` so new deployments have
  about 1,000 client address slots, and defaulted `client_default_dns` to
  `1.1.1.1` plus `1.0.0.1`.
- Added automatic `component` and `name` labels to resources while preserving
  caller-provided label overrides.
- Added VM-local day-2 add/remove commands for default `local_subnets` so
  future generated clients can pick up changed private destination CIDRs
  without rewriting existing client configs.
- Fixed planning when `endpoint_host` is left null for cloud-init public
  endpoint auto-detection.
- Fixed cloud-init heredoc rendering so generated WireGuard client sections do
  not break under `set -u`.
- Fixed cloud-init SSH service detection under `set -o pipefail` and forced
  IPv4 forwarding after host sysctl defaults are applied so Ubuntu images
  finish setup and start `wg-quick`.
- Fixed first-boot startup by creating `/run/sshd` before validating SSH
  configuration on fresh Ubuntu images.
- Hardened the public WireGuard VPN gateway VM bootstrap with the same SSH security
  posture used by the SSH jump-host where applicable: key-only admin access,
  no SSH forwarding on the WireGuard host, fail2ban, auditd watches, unattended
  security upgrades, stricter forwarding sysctls, and a narrower routed UFW
  policy that keeps client-initiated VPC access working.
- Hardened module contract with stricter Terraform baseline (`>= 1.10.0`).
- Added explicit variable nullability and stronger validation for critical inputs.
- Enforced passwordless sudo plus key-only SSH in cloud-init, matching the
  operator runbook and security notes.
- Rewrote the README to focus on Terraform module inputs, WireGuard networking
  concepts, NAT behavior, public endpoint requirements, and client-config
  secret handling.
- Clarified macOS client setup around the Homebrew `wireguard-tools` CLI
  workflow.
- Redacted secret fields from `nebius-wireguard-client list` output; generated
  client configs remain the only command output that carries client private key
  material.
