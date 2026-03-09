# `misc`

This folder contains helper automation that is useful during deployment but is
not part of the installed `nebius-vpngw` CLI surface.

## Contents

- `gcp-vpngw.sh`: bootstrap the GCP side of a Nebius-to-GCP site-to-site VPN.

## `gcp-vpngw.sh`

`gcp-vpngw.sh` creates or reuses the GCP resources needed for the current
`nebius-vpngw` design:

- one GCP HA VPN gateway for the Nebius connection
- one Cloud Router in the same region/VPC
- one external VPN gateway that models the Nebius peer as a single public IP
- exactly two HA VPN tunnels on the GCP side
- two Cloud Router interfaces
- two BGP peers

After creation, it prints:

- the generated PSKs
- the GCP tunnel public IPs
- a YAML snippet to paste into the Nebius config under the GCP connection

It creates or reuses the HA VPN gateway, Cloud Router, external VPN gateway,
tunnel pair, router interfaces, and BGP peers. It does not create Cloud NAT.

## Design Constraints

This script is intentionally aligned to the current Nebius implementation.

- Nebius currently exposes one public peer IP for this workflow.
- The Nebius side is active/passive, not active/active.
- The Nebius connection uses exactly two tunnels.
- The GCP side for this workflow is therefore one HA VPN gateway plus two
  tunnels to the same Nebius peer.
- Tunnel 1 is emitted as `ha_role: "active"`.
- Tunnel 2 is emitted as `ha_role: "passive"`.

This script is not intended to build a general multi-gateway active/active
topology for Nebius. If the GCP project already has an HA VPN gateway, the
script prompts whether to reuse it or create a new one, but the Nebius
connection created by this script still remains a single active/passive tunnel
pair on one GCP HA VPN gateway.

## Prerequisites

- Google Cloud CLI (`gcloud`) installed and available in `PATH`
- permission to create or update:
  - HA VPN gateways
  - Cloud Routers
  - external VPN gateways
  - VPN tunnels
  - BGP peers and router interfaces
- a target GCP VPC network in the selected region
- a Nebius public IP already allocated, or a local Nebius config file from
  which the script can discover it

## Required Inputs

Create mode requires these values to be resolved:

- GCP project ID
- GCP region
- Nebius public IP
- Nebius peer ASN

The canonical positional form is:

```bash
./misc/gcp-vpngw.sh <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

The same values can also come from:

- flags: `--gcp-project-id`, `--region`, `--nebius-public-ip`, `--nebius-asn`
- env vars: `GCP_PROJECT_ID`, `REGION`, `NEBIUS_PUBLIC_IP`, `NEBIUS_ASN`
- local Nebius config:
  - public IP from allocated `gateway_group.external_ips`
  - ASN from `gateway.local_asn`

If the connection-scoped names are left at their defaults, the script derives
the external gateway, tunnel, interface, and BGP peer names from the Nebius
public IP so repeated runs target the same GCP resources.

The required `<nebius-asn>` input is the Nebius-side peer ASN. The GCP Cloud
Router ASN is a separate setting:

- if you reuse an existing Cloud Router, the script reads and keeps that
  router's actual ASN and uses it as `bgp.remote_asn` in the Nebius YAML
- if you create a new Cloud Router, the script uses `CLOUD_ROUTER_ASN`
  (default `64514`) as the GCP-side ASN

## What The Script Does

At a high level, the script performs this flow:

1. Check that `gcloud` is installed.
2. Ensure there is an active GCP login, or run `gcloud auth login`.
3. Resolve the project, region, Nebius public IP, and Nebius ASN.
4. If the selected HA VPN gateway already exists, prompt whether to reuse it or
   create a new one.
5. Reuse or create the GCP HA VPN gateway.
6. If the selected Cloud Router already exists, prompt whether to reuse it or
   create a new one.
7. If the selected Cloud Router already has Cloud NAT attached, warn that GCP
   documents this as unsupported for HA VPN, but still allow reuse if the user
   explicitly chooses it.
8. If the selected Cloud Router is reused, keep its actual ASN and use that as
   the GCP-side ASN in the final Nebius YAML output.
9. If the selected gateway or router already hosts another site and the default
   external gateway, tunnel, interface, or peer names would collide, prompt
   for a new connection resource prefix and derive unique names from it.
10. Reuse or create the Cloud Router.
11. Reuse or create the external VPN gateway for the Nebius public IP.
12. Reuse or create two VPN tunnels.
13. Reuse or create two router interfaces and two BGP peers.
14. Print the PSKs and the YAML snippet for the Nebius config.

If you run with `--rotate-existing-tunnels`, the script removes the matching
BGP peers and router interfaces first, then recreates the tunnel pair with new
PSKs.

## Recommended Workflow

From the repo root:

1. Prepare Nebius networking and reserve the Nebius public IP:

```bash
nebius-vpngw prep-network --local-config-file <local-config-file>
```

1. Create the GCP side:

```bash
./misc/gcp-vpngw.sh <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

1. Export the printed PSKs:

```bash
export GCP_TUNNEL_1_PSK='<printed-psk-1>'
export GCP_TUNNEL_2_PSK='<printed-psk-2>'
```

1. Paste the printed YAML snippet into the GCP connection section of the Nebius
   config.

1. Validate and apply the Nebius side:

```bash
nebius-vpngw validate-config <local-config-file>
nebius-vpngw apply --local-config-file <local-config-file>
```

## Common Commands

From the repo root:

```bash
./misc/gcp-vpngw.sh --help
./misc/gcp-vpngw.sh <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
./misc/gcp-vpngw.sh --local-config-file <local-config-file> --region <gcp-region>
./misc/gcp-vpngw.sh --status --region <gcp-region>
./misc/gcp-vpngw.sh --status --local-config-file <local-config-file>
PSK1=<tunnel-1-psk> PSK2=<tunnel-2-psk> ./misc/gcp-vpngw.sh --rotate-existing-tunnels <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
```

From inside `misc/`:

```bash
./gcp-vpngw.sh --help
./gcp-vpngw.sh <gcp-project-id> <gcp-region> <nebius-public-ip> <nebius-asn>
./gcp-vpngw.sh --status --region <gcp-region>
```

## Operational Notes

- The script reuses an existing Cloud Router by default when the existing router
  matches the region/VPC/ASN assumptions.
- If the existing Cloud Router already has Cloud NAT attached, the script warns
  that Google documents this as unsupported for HA VPN, but it can still reuse
  that router if you explicitly choose to do so.
- Status mode only needs the GCP project and region to be resolved. Nebius
  public IP is optional there and can come from `--nebius-public-ip` or
  `--local-config-file`.
- By default, the script derives connection-scoped names from the Nebius public
  IP so repeated runs stay idempotent and target the same GCP resources.
- If the existing HA VPN gateway or Cloud Router already serves another site,
  the script prompts for a new connection resource prefix so tunnel, interface,
  peer, and external gateway names do not collide.
- The required `<nebius-asn>` input is the Nebius-side peer ASN, not the GCP
  Cloud Router ASN.
- If the existing Cloud Router is reused, the script keeps that router's
  actual ASN and prints it as `bgp.remote_asn` in the Nebius YAML snippet.
- If a new Cloud Router is created, the GCP-side ASN comes from
  `CLOUD_ROUTER_ASN` and defaults to `64514`.
- The script does not create or modify Cloud NAT. If you choose a new Cloud
  Router and want a separate NAT on it, create that NAT explicitly after the
  VPN setup.
- The script treats the Nebius peer as a single-interface external VPN gateway
  on GCP.
- The script emits exactly two tunnels and keeps the Nebius-side YAML in
  active/passive form.
- If existing tunnel names are reused, PSKs cannot be read back from GCP; the
  script tells you to rotate them if you need fresh PSKs printed again.

## References

- Cloud VPN best practices:
  `https://cloud.google.com/network-connectivity/docs/vpn/support/best-practices`
- HA VPN advanced topology guidance:
  `https://cloud.google.com/network-connectivity/docs/vpn/concepts/advanced`
- HA VPN topologies and multi-site guidance:
  `https://cloud.google.com/network-connectivity/docs/vpn/concepts/topologies-increase-bandwidth`
- Cloud Router overview and creation:
  `https://cloud.google.com/network-connectivity/docs/router/how-to/create-network-set-cloud-router-managed-by-router`
