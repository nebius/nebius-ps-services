# Architecture Diagram

This folder contains a Graphviz diagram of the Nebius VM-based VPN Gateway architecture.

## Files

- `vpngw-architecture.dot`: Graphviz source describing:
  - Orchestrator CLI, YAML + peer config merge
  - Nebius VPC (VPN subnet + workload subnets)
  - Gateway VMs with strongSwan, FRR, and agent
  - External peers (GCP/AWS/Azure/On‑prem)
  - SDK and SSH flows, routing table behavior

- `vpngw-conn-diagram.dot`: Connections and IPsec tunnels hierarchy between
  GCP HA VPN (two gateway public IPs) and a single Nebius peer VPN gateway
  with two separate public IPs. Shows Gateway → Connection → Tunnel structure,
  APIPA /30 addresses, PSK, and BGP neighbor details.

## Render Commands (macOS / zsh)

First, ensure Graphviz is installed:

```zsh
brew install graphviz
dot -Tpng image/vpngw-architecture.dot -o image/vpngw-architecture.png
dot -Tsvg image/vpngw-architecture.dot -o image/vpngw-architecture.svg
dot -Tpng image/vpngw-conn-diagram.dot -o image/vpngw-conn-diagram.png
dot -Tsvg image/vpngw-conn-diagram.dot -o image/vpngw-conn-diagram.svg
```

## PNG fallback at high DPI

```shell
dot -Tpng -Gdpi=600 image/vpngw-architecture.dot -o image/vpngw-architecture-600dpi.png
dot -Tpng -Gdpi=600 image/vpngw-conn-diagram.dot -o image/vpngw-conn-diagram-600dpi.png
```
