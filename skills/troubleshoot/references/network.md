# Network Troubleshooting Playbook

Use this playbook only after defining the failing source identity and network
namespace, destination identity, protocol, port, expected response, incident
window, and one affected and unaffected comparison.

## Path Model

Trace the path in order:

```text
application -> resolver -> address choice -> local socket and policy -> route
-> interface and link -> firewall or security policy -> NAT, proxy, or load balancer
-> remote route and policy -> transport or TLS handshake -> service -> application
```

For overlays or accelerated fabrics, add CNI, tunnel or eBPF datapath, MTU,
network namespace, SR-IOV, RDMA, InfiniBand subnet management, and GPU collective
libraries. Identify asymmetric return paths and retry or connection reuse.

## Component Verification

Verify interface and link state, addresses, neighbor and route selection, DNS
configuration and answer, MTU, firewall and policy generation, NAT or
load-balancer health, listening socket and process, certificate and identity,
clock sync, packet and error counters, queue or buffer pressure, CNI or fabric
component health, and recent topology or policy changes.

Test from the actual affected source namespace. A successful test from an
operator workstation or another node does not prove the failing path.

## Logs And Correlation

Correlate request or connection ID, source and destination tuple, DNS name,
pod or process, node, interface, flow or firewall decision, load-balancer
backend, TLS identity, and timestamp. Examine as relevant:

- application client and server logs with timeout phase and error class;
- resolver and DNS server logs, cache state, and answer lifetime;
- firewall, security group, network policy, proxy, ingress, gateway, load
  balancer, CNI, eBPF, NAT, and connection-tracking evidence;
- interface, driver, link, switch or fabric, RDMA, InfiniBand, and kernel logs;
- retransmit, reset, drop, error, queue, MTU, route, and handshake counters.

Distinguish timeout, refusal, reset, DNS, route, policy denial, TLS,
authentication, protocol, overload, and application failure. Do not treat a
generic timeout as proof of network loss.

## Hypothesis Branches

- **Name resolution:** query path, search domains, cache, split horizon, answer
  family, negative TTL, and client address selection.
- **Routing or reachability:** source route, return route, neighbor, MTU,
  tunnel, policy, NAT, and failure location.
- **Intermittent loss or latency:** timestamped counters, affected flows,
  congestion, queueing, retransmits, CPU or interrupt pressure, link or fabric
  events, and topology comparison.
- **TLS or identity:** clock, SNI, certificate chain, trust, audience, protocol
  negotiation, proxy termination, and backend identity.
- **Service discovery or load balancing:** endpoint membership, readiness,
  propagation, affinity, health checks, stale connections, and backend logs.
- **RDMA or InfiniBand:** device, port and link state, subnet manager, GID,
  partition, route, counters, firmware and driver, collective-library logs, and
  topology-aware affected versus unaffected nodes.

## Bounded Capture And Debug

Packet capture, flow tracing, eBPF, firewall logging, or fabric diagnostics must
test a named hypothesis. Restrict interfaces, hosts, ports, protocol, packet or
byte count, and duration; state privacy and performance risks; protect capture
files; and remove or hand them off only to an authorized location. Prefer
metadata and counters when payload is unnecessary. Never use indefinite capture.

## Official Sources

- [Linux networking documentation](https://docs.kernel.org/networking/index.html)
- [Kubernetes networking concepts](https://kubernetes.io/docs/concepts/services-networking/)
- [CNI specification](https://github.com/containernetworking/cni/blob/main/SPEC.md)
- Use the deployed NIC, switch, CNI, load-balancer, firewall, and fabric vendor's
  current official documentation for version-specific counters and log fields.
