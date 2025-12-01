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
```

Render PNG (raster):

```zsh
dot -Tpng vpngw-architecture.dot -o vpngw-architecture.png
```

Render SVG (vector, high-res for web/blogs):

```zsh
dot -Tsvg vpngw-architecture.dot -o vpngw-architecture.svg
```

Open the PNG (Preview on macOS):

```zsh
open vpngw-architecture.png
```

### Connections/Tunnels Diagram

Render PNG (raster):

```zsh
dot -Tpng vpngw-conn-diagram.dot -o vpngw-conn-diagram.png
```

Render SVG (vector, high-res for web/blogs):

```zsh
dot -Tsvg vpngw-conn-diagram.dot -o vpngw-conn-diagram.svg
```

Open the PNG (Preview on macOS):

```zsh
open vpngw-conn-diagram.png
```
