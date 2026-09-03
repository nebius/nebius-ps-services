# `misc`

This folder contains deployment helpers that are separate from the installed
`nebius-vpngw` CLI.

## Contents

- `gcp-vpngw.sh`: configure either the supported legacy single-peer tunnel
  pair or an explicit two-member/four-tunnel GCP HA VPN peer and print the
  matching Nebius `connections:` block.
- `fix-vpngw-esp4.sh`: repair gateway VMs where the Ubuntu image or a temporary
  Dirty Frag mitigation left the required `esp4` module blocked.
- `vm_ha_one_way_probe.py`: observe one-way 5 Hz ICMP recovery from an explicit
  test VM while failover or failback is run separately.

## VM-HA one-way traffic observation

`vm_ha_one_way_probe.py` is a diagnostic trial helper, not an installed product
command or a gateway health check. It never invokes failover/failback and never
changes Compute, allocation, route, tunnel, or forwarding state. Start it from
one terminal, then run the ordinary product command from another terminal.

The helper requires a literal observer IPv4 address, literal destination IPv4
address, explicit bounded packet count, and an existing non-symlink OpenSSH
known-hosts file. It also requires an explicit current-user-owned private key
with no group or other permissions. SSH is fail-closed: host verification and
that exact identity are required, password and keyboard authentication and
proxies are disabled, and no ambient SSH configuration or identities are
loaded.

```bash
python misc/vm_ha_one_way_probe.py \
  --ssh-target observer@192.0.2.10 \
  --known-hosts-file /path/to/known_hosts \
  --identity-file /path/to/id_ed25519 \
  --destination 198.51.100.20 \
  --count 1500 \
  --direction-label nebius-to-peer >one-way-trial.jsonl
```

The endpoint-free JSONL contains timestamped unique replies and a terminal
summary with the complete transmitted domain, exact missing sequences, and the
first five-consecutive-reply recovery after the last loss. SSH failure or
stderr, timeout, ping send/runtime errors, malformed/localized output, or a
missing/inconsistent terminal summary produces no partial JSONL and exits
nonzero.

Synchronize the observer and operator clocks to the same time source before
starting and record their measured offset and uncertainty. Start the helper
before invoking the product command in another terminal. Keep JSONL and CLI
stderr in a private untracked location, then correlate them offline only when
clock uncertainty cannot change phase attribution. Independently verify the
gateway's cloud, allocation, route, VPN, forwarding, and restored-redundancy
postconditions. One direction is useful for timing diagnosis but is not
bidirectional VM-HA acceptance evidence; run an independent reverse-direction
trial for that acceptance.

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

### Explicit two-member VM-HA peer mode

`--vm-ha-peer` is additive. Without it, every legacy argument, resource shape,
and two-tunnel behavior above remains unchanged. With it, the helper delegates
to the isolated VM-HA planner and requires explicit project, region, connection,
ASN, and active/passive Nebius public IPs.

The VM-HA mode converges the current GCP multi-VM topology:

- one regional GCP HA VPN gateway
- one regional Cloud Router
- two mirrored global external VPN gateway resources, each describing the two
  Nebius public IPs in the interface order required by its tunnel pair
- four unique HA VPN tunnels and APIPA `/30` links
- four Cloud Router interfaces and BGP peers
- lower numeric advertised priorities for both configured-active VM sessions
  and higher numeric priorities for both configured-passive VM sessions

The priority is the MED on routes Cloud Router advertises toward Nebius. It
does not select GCP's return path for routes learned from Nebius; the
`nebius-vpngw` controller independently gates forwarding and route writes to
the authoritative VM owner.

Each VM receives one `ha_role: active` and one `ha_role: passive` tunnel in the
single generated Nebius connection, so the configured-active VM does not
create an intra-VM ECMP path. VM role remains independent of tunnel role.

The four PSKs must already be present in the derived environment variables
(`GCP_<CONNECTION>_TUNNEL_1_PSK` through `_4_PSK`) when a missing tunnel is
created. Override those variable names with `PSK1_ENV_NAME` through
`PSK4_ENV_NAME`. The helper never prints secret values; generated YAML contains
only `${...}` references. Tunnel creation passes the secret to `gcloud` through
an inherited anonymous flags-file descriptor, so it is absent from the process
argument list and is never written to disk. Every `gcloud` child environment is
also scrubbed of the complete resolved PSK set and the planned PSK variable
names.

For an explicit migration, `--psk-source-config <private-config.yaml>` may read
the four existing PSKs from one regular, non-symlink mode-`0600` VPNGW YAML
file. The helper selects exactly one connection matching `--connection-name`
and binds each secret to the exact planned tunnel name, independent of YAML
order. It validates the complete four-secret topology before the first GCP
mutation and rejects use together with any planned PSK environment value. It
still prints only environment-variable references and never prints the source
secrets.

Preview and status are non-mutating:

```bash
./misc/gcp-vpngw.sh --vm-ha-peer \
  --connection-name <name> \
  --gcp-project-id <gcp-project-id> \
  --region <gcp-region> \
  --network <gcp-network> \
  --vpn-gateway-name <regional-ha-vpn-gateway> \
  --cloud-router-name <cloud-router> \
  --cloud-router-asn <gcp-asn> \
  --nebius-active-public-ip <vm0-public-ip> \
  --nebius-passive-public-ip <vm1-public-ip> \
  --nebius-asn <nebius-asn> \
  --dry-run

./misc/gcp-vpngw.sh --vm-ha-peer \
  --connection-name <name> \
  --gcp-project-id <gcp-project-id> \
  --region <gcp-region> \
  --nebius-active-public-ip <vm0-public-ip> \
  --nebius-passive-public-ip <vm1-public-ip> \
  --nebius-asn <nebius-asn> \
  --status
```

Both commands require an existing active `gcloud` login. They never start a
browser login or change the active gcloud project. Apply mode prompts unless
`--yes` is provided, validates every existing resource by shape and binding,
and fails closed instead of adopting a same-name foreign resource. It does not
delete or rotate resources; migration cleanup remains a separately reviewed
operation after the four replacement tunnels are healthy.

### Isolated Classic static VM-HA peer mode

`--classic-vm-ha-peer` delegates to a separate static-only planner. It creates
two independent one-to-one GCP Classic VPN paths, one for each Nebius VM:

- two Premium-tier regional external addresses and target VPN gateways
- three Premium-tier `EXTERNAL` forwarding rules per gateway for ESP, UDP 500,
  and UDP 4500
- one IKEv2 Classic tunnel per gateway with `0.0.0.0/0` traffic selectors
- one explicit GCP route per Nebius prefix and path, with a lower priority for
  the configured-active path and a higher priority for the configured-passive
  path

The mode creates no HA VPN gateway, Cloud Router, router interface, or BGP
peer. Keep its Nebius gateway group, VM-HA cluster, configuration, GCP names,
and routes separate from the BGP fixture.

Preview the exact graph without changing GCP:

```bash
./misc/gcp-vpngw.sh --classic-vm-ha-peer \
  --connection-name <static-connection-name> \
  --gcp-project-id <gcp-project-id> \
  --region <gcp-region> \
  --network <gcp-network> \
  --nebius-active-public-ip <vm0-public-ip> \
  --nebius-passive-public-ip <vm1-public-ip> \
  --gcp-prefix <gcp-workload-prefix> \
  --nebius-prefix <nebius-workload-prefix> \
  --dry-run
```

Apply requires `GCP_<CONNECTION>_CLASSIC_A_PSK` and
`GCP_<CONNECTION>_CLASSIC_B_PSK` only when their corresponding tunnels are
missing. Override those variable names with `PSK_A_ENV_NAME` and
`PSK_B_ENV_NAME`. Secrets are validated before the first mutation and passed
through an inherited anonymous flags-file descriptor; they are never printed,
written to disk, or placed in a child process argument or environment.

When environment variables are inconvenient, `--psk-source-config
<private-config.yaml>` reads exactly the two planned named tunnel PSKs from one
regular, non-symlink mode-`0600` VPNGW YAML. The matching connection must
contain exactly those two tunnel names and literal PSKs; `${...}` references
are rejected so no source-secret environment variable reaches the initial
`gcloud` probes. The helper also rejects a source file when either planned PSK
environment variable is set, validates the complete two-secret topology before
mutation, and never prints the values. Actual rotation also requires an enabled
two-member VM-HA declaration, `vendor: gcp`, static routing, exact local/remote
prefixes, one endpoint per member, and exact member, inner-link, and observed
peer-address bindings. Dry-run remains secret-free and therefore does not read
or validate the private source file.

```bash
./misc/gcp-vpngw.sh --classic-vm-ha-peer \
  --connection-name <static-connection-name> \
  --gcp-project-id <gcp-project-id> \
  --region <gcp-region> \
  --network <gcp-network> \
  --nebius-active-public-ip <vm0-public-ip> \
  --nebius-passive-public-ip <vm1-public-ip> \
  --gcp-prefix <gcp-workload-prefix> \
  --nebius-prefix <nebius-workload-prefix> \
  --yes
```

Changing a Classic tunnel PSK requires explicit tunnel recreation. Preview the
exact delete/create plan first, then apply it with the same private config:

```bash
./misc/gcp-vpngw.sh --classic-vm-ha-peer \
  --connection-name <static-connection-name> \
  --gcp-project-id <gcp-project-id> \
  --region <gcp-region> \
  --network <gcp-network> \
  --nebius-active-public-ip <vm0-public-ip> \
  --nebius-passive-public-ip <vm1-public-ip> \
  --gcp-prefix <gcp-workload-prefix> \
  --nebius-prefix <nebius-workload-prefix> \
  --psk-source-config <private-config.yaml> \
  --rotate-existing-tunnels \
  --dry-run
```

First establish the successful fenced Nebius-side checkpoint with the same
private config:

```bash
nebius-vpngw apply \
  --local-config-file <private-config.yaml> \
  --prepare-vm-ha-peer-rotation
```

For this GCP Classic helper, the provider-neutral preparation checkpoint is
invoked with the helper's required static-only config. It stages and activates
the exact generation, then returns with both VM-HA members passively fenced and
locked. Other compatible peers use the same core checkpoint but their own
reviewed peer-update workflow; see the
[provider-neutral rotation contract](../README.md#provider-neutral-vm-ha-peer-credential-rotation).
Remove `--dry-run` from the GCP helper only after the preparation succeeds. Rotation requires the
normal confirmation unless `--yes` is supplied. It validates the complete
retained address, target-gateway, and forwarding-rule graph plus both secrets
first; planned tunnels and routes alone may be absent for retry. Immediately
after confirmation it re-reads immutable resource identity and exact bindings,
then deletes all planned static routes, deletes only the two planned tunnels,
recreates both tunnels, and restores the routes. Retained infrastructure is
never deleted or recreated by rotation. If any mutation or final verification
fails, the helper removes every planned route that it can observe and fails
unless it can prove every planned route is absent. Rerun the same explicit
command with the unchanged private config to complete the missing graph.

After GCP rotation succeeds, run ordinary apply with the same private config.
Only ordinary apply releases the exact owner lock, establishes the owner tunnel,
reconciles the exact static route receipt, and enables forwarding.

Every run inspects the full expected graph first and rejects same-name foreign
resources, including missing or incompatible network-tier and load-balancing
scheme fields, before resolving secrets or creating anything. Rotation also
rejects missing retained infrastructure and confirmation-time replacement or
binding drift before deleting anything. Repeating apply
is idempotent. Apply creates all missing non-route resources for both paths
before it creates any missing static route, so a path-construction failure
cannot expose a newly routed one-path graph. Compatible resources and routes
are retained for an idempotent retry. `--status` is read-only, and the helper
never deletes resources unless `--rotate-existing-tunnels` is explicitly
selected; that mode deletes only the planned routes and tunnels described
above.

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
- Legacy `--status` and VM-HA `--status` require an existing active gcloud
  account. Neither status path runs `gcloud auth login` or changes the active
  project.
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
- HA VPN to VM instances:
  `https://cloud.google.com/network-connectivity/docs/vpn/how-to/connect-ha-vpn-vm`
- Cloud Router overview:
  `https://cloud.google.com/network-connectivity/docs/router/concepts/overview`
