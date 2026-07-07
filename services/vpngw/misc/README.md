# `misc`

This folder contains deployment helpers that are separate from the installed
`nebius-vpngw` CLI.

## Contents

- `gcp-vpngw.sh`: configure one GCP-side HA VPN connection to a Nebius VPN
  gateway and print the matching Nebius `connections:` block.
- `fix-vpngw-esp4.sh`: repair gateway VMs where the Ubuntu image or a temporary
  Dirty Frag mitigation left the required `esp4` module blocked.

## `fix-vpngw-esp4.sh`

`fix-vpngw-esp4.sh` is an operational repair helper for existing gateway VMs.
It uploads and runs the same packaged ESP4 preflight used during new-VM
provisioning, upgrades Ubuntu packages, reboots each target gateway serially,
confirms the remote boot ID changed, verifies `modprobe esp4`, and restarts the
gateway services.

The script requires typed `REBOOT` confirmation unless `--yes` is supplied
because VPN traffic through each target is disrupted for a few minutes.

Common forms:

```bash
./misc/fix-vpngw-esp4.sh --host ubuntu@<gateway-ip>
./misc/fix-vpngw-esp4.sh --local-config-file <local-config-file>
./misc/fix-vpngw-esp4.sh --host <gateway-ip> \
  --ssh-user ubuntu --identity-file ~/.ssh/id_ed25519
```

Useful options:

- `--host <user@ip>`: add one target gateway; repeat for multiple gateways
- `--local-config-file <path>`: discover targets from `gateway_group.external_ips`
- `--dry-run`: print planned actions without connecting or rebooting
- `--wait-timeout <seconds>`: change the post-reboot SSH wait timeout
- `--yes`: skip the typed confirmation for automation

## `gcp-vpngw.sh`

`gcp-vpngw.sh` is a stateless per-connection helper:

- one run manages exactly one connection
- the connection is identified by `--connection-name` or `CONNECTION_NAME`
- rerun with the same connection name to update that same connection
- use a different connection name for a separate connection
- the script does not keep local state about other connections

The helper can reuse an existing GCP HA VPN gateway and Cloud Router, but it
creates or reuses the connection-scoped resources for only the selected
connection:

- one external VPN gateway representing the Nebius peer as a single public IP
- two HA VPN tunnels
- two Cloud Router interfaces
- two BGP peers

Successful runs print:

- tunnel PSKs when new tunnels are created or rotated
- a complete Nebius `connections:` entry for that connection only
- literal PSKs in the printed Nebius connection block when the helper knows
  them
- optional export commands with connection-specific variable names for users
  who prefer env vars in YAML

## Design Scope

This helper is intentionally narrow.

- It configures one active/passive GCP HA VPN connection at a time.
- It models the Nebius side as a single external VPN gateway interface on GCP.
- It emits exactly two tunnels for the connection:
  - tunnel 1 as `ha_role: "active"`
  - tunnel 2 as `ha_role: "passive"`
- It is suitable for adding multiple GCP sites to one Nebius gateway by
  running the helper once per site with a different connection name.
- For this single-IP Nebius peer model, one GCP HA VPN gateway can host only
  one tunnel pair to that peer because the valid interface mappings are `0->0`
  and `1->0`.
- A second connection to the same Nebius public IP therefore needs a different
  GCP HA VPN gateway. The helper fails fast if the selected gateway already
  consumes both mappings for that peer.
- When adding another connection on a different GCP HA VPN gateway, the helper
  can auto-pick unused APIPA `/30` ranges for the new pair.

The main `nebius-vpngw` CLI is responsible for validating and applying the
Nebius side. This helper only configures the GCP side and prints the Nebius
connection block to paste into your YAML config.

## Prerequisites

- `gcloud` installed and available in `PATH`
- permission to create or update:
  - HA VPN gateways
  - Cloud Routers
  - external VPN gateways
  - VPN tunnels
  - router interfaces and BGP peers
- a target GCP VPC network in the selected region
- a Nebius public IP already allocated, or a local Nebius config file from
  which the script can discover it

## Required Inputs

All modes require a connection name.

- `--connection-name <name>` or `CONNECTION_NAME`

Create mode also requires these values to resolve:

- GCP project ID
- GCP region
- Nebius public IP
- Nebius peer ASN

Canonical create form:

```bash
./misc/gcp-vpngw.sh --connection-name <name> \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

The same values can also come from:

- flags:
  - `--gcp-project-id`
  - `--region`
  - `--nebius-public-ip`
  - `--nebius-asn`
  - `--local-config-file`
- env vars:
  - `CONNECTION_NAME`
  - `GCP_PROJECT_ID`
  - `REGION`
  - `NEBIUS_PUBLIC_IP`
  - `NEBIUS_ASN`
  - `LOCAL_CONFIG_FILE`
- local Nebius config discovery:
  - public IP from `gateway_group.external_ips`
  - ASN from `gateway.local_asn`

When the helper reuses an existing HA VPN gateway or Cloud Router, it also
auto-detects the VPC network from those resources unless `NETWORK` is set
explicitly.

The required `<nebius-asn>` is the Nebius-side ASN. The GCP Cloud Router ASN
is separate:

- if the helper reuses an existing Cloud Router, it reads and keeps that
  router's actual ASN
- if the helper creates a new Cloud Router, it uses `CLOUD_ROUTER_ASN`
  (default `64514`)

For multi-connection use, `--local-config-file` is strongly recommended. When
it is available, the helper also checks the existing Nebius YAML so the newly
printed connection block does not reuse tunnel APIPA ranges that are already in
the file.

## Naming Model

Tunnel, interface, and BGP peer names are derived from `--connection-name`
unless you override them with env vars:

- `TUNNEL1_NAME`
- `TUNNEL2_NAME`
- `IFACE1_NAME`
- `IFACE2_NAME`
- `PEER1_NAME`
- `PEER2_NAME`

If you point the helper at older GCP resources whose names were created outside
this naming model, pass those env var overrides explicitly for status or
update operations. The canonical workflow is to let the helper own the
connection-scoped GCP resource names from the first run.

The external VPN gateway is treated as a peer-scoped resource instead of a
connection-scoped one:

- if GCP already has a single-IP external VPN gateway for the same Nebius
  public IP, the helper reuses it automatically
- otherwise it creates one with a name derived from the Nebius public IP
- `EXTERNAL_GW_NAME` still overrides that choice explicitly

This keeps the helper stateless while still making reruns idempotent.

If the selected connection name already maps to GCP resources with different
peer values, interfaces, or bindings, the script exits with a conflict error.
Use the same connection parameters to update that connection, or choose a
different connection name for a separate site.

By default, the helper also derives connection-specific PSK variable names for
the optional export commands it prints:

- `GCP_<CONNECTION_NAME>_TUNNEL_1_PSK`
- `GCP_<CONNECTION_NAME>_TUNNEL_2_PSK`

You can override those printed variable names with `PSK1_ENV_NAME` and
`PSK2_ENV_NAME`.

For tunnel addressing:

- if the selected connection already exists on the Cloud Router, the helper
  reuses that connection's existing APIPA values
- otherwise it chooses two unused `/30` ranges by checking the selected Cloud
  Router first and the local Nebius config file when available
- you can override APIPA values explicitly with `TUN1_*` and `TUN2_*`

## What The Helper Does

At a high level:

1. Check that `gcloud` is available.
2. Ensure an active GCP login exists, or run `gcloud auth login`.
3. Resolve the connection name, project, region, Nebius public IP, and Nebius
   ASN.
4. Reuse or create the HA VPN gateway.
5. Reuse or create the Cloud Router.
6. Reuse the existing external VPN gateway for the Nebius peer IP when one is
   already present, or create it when missing.
7. Validate that the selected connection name still points to the same
   connection-scoped resources and peer values.
8. Reuse existing APIPA values for that connection when they already exist, or
   auto-select two unused `/30` ranges for the new connection.
9. Reuse or create the tunnel pair, router interfaces, and BGP peers for that
   one connection.
10. Print the PSKs and the Nebius connection block for that connection.

With `--rotate-existing-tunnels`, the helper deletes the matching BGP peers and
router interfaces first, then recreates the tunnel pair with new PSKs.

## Recommended Workflow

From the repo root:

1. Prepare Nebius networking and reserve the Nebius public IP:

```bash
nebius-vpngw prep-network --local-config-file <local-config-file>
```

1. Create the first GCP-side connection:

```bash
./misc/gcp-vpngw.sh --connection-name site-1 \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

1. Export the printed PSKs:

```bash
export GCP_SITE_1_TUNNEL_1_PSK='<printed-psk-1>'
export GCP_SITE_1_TUNNEL_2_PSK='<printed-psk-2>'
```

1. Paste the printed connection block into `connections:` in the Nebius YAML.

2. If you need another connection to the same Nebius public IP, choose a
   different GCP HA VPN gateway name. Reusing the same Cloud Router is still
   allowed when the region, VPC, and routing policy fit:

```bash
./misc/gcp-vpngw.sh --connection-name site-2 \
  --local-config-file <local-config-file> \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

When the helper detects that the default HA VPN gateway is already using both
`0->0` and `1->0` mappings to the selected single-IP Nebius peer, choose `n`
when it asks whether to reuse the existing GCP HA VPN gateway and provide a
new gateway name. Then either reuse the existing Cloud Router or create a new
one, depending on your routing policy.

1. Validate and apply the Nebius side:

```bash
nebius-vpngw validate-config <local-config-file>
nebius-vpngw apply --local-config-file <local-config-file>
```

## Common Commands

From the repo root:

```bash
./misc/gcp-vpngw.sh --help
./misc/gcp-vpngw.sh --connection-name site-1 \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
./misc/gcp-vpngw.sh --connection-name site-2 \
  --local-config-file <local-config-file> \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
./misc/gcp-vpngw.sh --connection-name site-1 --status --region <gcp-region>
./misc/gcp-vpngw.sh --connection-name site-1 \
  --status --local-config-file <local-config-file>
CONNECTION_NAME=site-1 PSK1=<tunnel-1-psk> PSK2=<tunnel-2-psk> \
  ./misc/gcp-vpngw.sh --rotate-existing-tunnels \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

From inside `misc/`:

```bash
./gcp-vpngw.sh --help
./gcp-vpngw.sh --connection-name site-1 \
  <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
./gcp-vpngw.sh --connection-name site-1 --status --region <gcp-region>
```

## Operational Notes

- One GCP HA VPN gateway and one Cloud Router can serve multiple peer sites in
  the same GCP region and VPC, but this helper's single-IP Nebius peer model
  uses peer interface `0` only.
- HA VPN gateways and Cloud Routers are regional GCP resources. A different GCP
  region needs a different gateway and Cloud Router in that region.
- When the same Nebius public IP is reused for multiple connections, the helper
  reuses the matching GCP external VPN gateway resource by default.
- The helper does not create or modify Cloud NAT.
- If the selected Cloud Router already has Cloud NAT attached, the helper warns
  because Google documents that combination as unsupported for HA VPN.
- Existing tunnel PSKs cannot be read back from GCP. Reuse keeps them in place;
  `--rotate-existing-tunnels` is how you force new PSKs to be printed.
- A second connection to the same single-IP Nebius peer cannot reuse the same
  GCP HA VPN gateway after mappings `0->0` and `1->0` are already occupied.
  The helper detects this and tells you to create a new GCP HA VPN gateway.
- When you place that second connection on a different GCP HA VPN gateway, the
  helper picks different tunnel APIPA values by checking the selected Cloud
  Router and, when provided, the local Nebius YAML.
- The printed Nebius connection block is meant to be combined with the main
  config template and schema rules, which require globally unique tunnel names
  and unique APIPA tunnel values per gateway instance.
- If the local Nebius YAML already contains the same `connection.name`, replace
  that existing block instead of appending a second block with the same name.

## References

- Cloud VPN best practices:
  `https://cloud.google.com/network-connectivity/docs/vpn/support/best-practices`
- HA VPN advanced topology guidance:
  `https://cloud.google.com/network-connectivity/docs/vpn/concepts/advanced`
- HA VPN topology guidance:
  `https://cloud.google.com/network-connectivity/docs/vpn/concepts/topologies`
- Cloud Router overview:
  `https://cloud.google.com/network-connectivity/docs/router/concepts/overview`
