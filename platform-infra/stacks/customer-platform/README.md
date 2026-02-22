# customer-platform stack

Composable platform stack for Nebius customer deployments.

This stack is consumed remotely from generated Terraform roots created by
`nebius-cxcli` and wires these modules:

- `modules/mk8s`
- `modules/managed-postgresql`
- `modules/sfs`
- `modules/object-storage`
- `modules/mysterybox`
- `modules/wireguard-jumphost`
- `modules/ssh-jumphost`

It is infra-only; in-cluster workloads/components are reconciled by Flux.

WireGuard behavior in this stack:

- NAT mode is enabled by default (`wireguard_nat_mode=true`).
- UDP listen port defaults to `51820` (`wireguard_listen_port`).
- Optional `wireguard_clients` input enables automated peer/client config
  generation on the WireGuard VM.

SSH jump-host behavior in this stack:

- `ssh_jumphost_allowed_cidrs` defines inbound SSH source allowlist.
- Strict bootstrap mode is used: empty allowlist is rejected (no open-to-world
  fallback).

MysteryBox behavior in this stack:

- When `mysterybox_enabled=true`, stack manages MysteryBox secrets and primary
  versions via `modules/mysterybox`.
- Secret payload values are expected at runtime from
  `TF_VAR_mysterybox_secret_values` (for example in CI).
- Payload values are passed through provider write-only fields, so raw secret
  data is not persisted in Terraform state.
- SSH daemon is hardened for bastion usage and supports ProxyJump workflows.
