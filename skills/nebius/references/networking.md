# Networking

Reviewed 2026-09-07. Public APIs: `vpc.v1`, `dns.v1`, `tunnel.v1`;
Managed Kubernetes load balancers are configured through Kubernetes Services.
Verify current regional support and API maturity; do not invent a standalone
managed load-balancer or managed NAT service from another cloud's terminology.

## Dependencies and ownership

Start from project -> network -> subnet -> allocation/NIC. Pools allocate
addresses; subnet ownership is not inferred from status CIDRs alone. Read
`vpc-networking.md` before changing pools and `route-inspection.md` before
changing routes. Explicit child CIDRs must fit the network's private pool and
avoid peer-subnet and live allocation overlap. Inherited subnet allocation is
a different mode. Never reuse an assigned, public, empty or wrong-tree pool.

`assets/networking/network.py` builds network, explicit-subnet, security-group
and scoped TCP-rule requests. Preflight all live pool/identity constraints first.
The builder's CIDR validation is syntax validation, not a cloud allocation proof.
Create dependency resources in order; wait and read back each identity before
passing its ID onward. Use the complete VPC inspectors before any route changes.
They do not authorize ownership transfer from another controller.

## Security groups and exposure

Nebius security rules can allow or deny, have priorities, and can be stateful
or stateless. Do not import AWS rule-combination assumptions. An empty assigned
security group denies traffic; the documented default group permits ingress and
egress. Create the intended rules before attaching a restrictive group so you do
not lock out management access. Scope management CIDRs and ports explicitly.
Verify effective rules, group/network binding and NIC attachment. Connectivity
probes require scoped authority and are distinct from configuration inspection.

Public IP allocation, public Kubernetes Services and Tunnels create exposure.
Use a private Kubernetes load balancer through the documented internal annotation
when intended; public/private allocations must match its type. Respect the
current private-load-balancer region/subnet reachability constraints. Source IP
preservation, probes and ingress/TLS are separate application networking concerns.

## DNS, NAT, VPN and Tunnels

- DNS: use `dns.v1` Zone/Record resources, explicit parent zone and relative
  name, type/data/TTL. Verify effective FQDN and authoritative resolution;
  zone/record creation is not evidence that delegation or clients have converged.
- NAT: Nebius documents a custom NAT gateway pattern. It requires a gateway VM,
  forwarding and routes; it is not provider-managed HA. Freeze traffic direction,
  return routes and workload subnet consumers before change.
- VPN: use the supported VPN Gateway workflow. vpngw owns route authority,
  shared IPs, failback and fencing; never recreate its state machine in examples.
- Tunnels: use for explicitly authorized service access without inventing a
  replacement for general VPC connectivity. Define destination service, client
  identity, authentication, exposure and lifecycle. Agent connectivity and actual
  end-to-end service reachability are separate checks.

## Official references

- [VPC overview](https://docs.nebius.com/vpc/overview)
- [Security groups](https://docs.nebius.com/vpc/security-groups/overview)
- [Routing](https://docs.nebius.com/vpc/routing/overview)
- [Custom NAT gateway](https://docs.nebius.com/vpc/routing/custom-nat-gateway)
- [DNS zones](https://docs.nebius.com/terraform-provider/reference/resources/dns_v1_zone)
- [DNS records](https://docs.nebius.com/terraform-provider/reference/resources/dns_v1_record)
- [Load balancers](https://docs.nebius.com/kubernetes/clusters/load-balancer)
- [VPN Gateway](https://docs.nebius.com/vpc/nebius-vpn-gateway)
- [Tunnels](https://docs.nebius.com/tunnels/overview)
