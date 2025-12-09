# Nebius VPN Gateway (VM-Based) — Design Document

Version: v0.2

## 1. Purpose & Scope

- Deliver a VM-based site-to-site VPN gateway for Nebius AI Cloud using IPsec (strongSwan) and routing (FRR for BGP, static as fallback).
- Provide a CLI orchestrator plus per-VM agent with idempotent applies from a single YAML configuration, optionally merged with vendor peer configs.
- Support common cloud/on-prem peers (GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco/others).

## 2. Goals & Non-Goals

- **Goals:** IKEv2 (default, optional IKEv1), PSK auth, AES-256, SHA-256/384/512, DH 14/20/24, configurable lifetimes; BGP preferred, static supported; repeatable applies; minimal operator state; stable public IPs.
- **Non-goals (current):** ECMP in VPC route tables, external NAT/LB, full multi-NIC support (future-ready but platform currently single-NIC).

## 3. Architecture Overview

- **Orchestrator CLI (`nebius-vpngw`):** runs on operator laptop/CI. Reads YAML (+ optional peer configs), resolves a per-VM plan, ensures VMs and IP allocations, pushes configs over SSH, triggers agent reload.
- **Gateway VM:** Ubuntu LTS, packages strongSwan/FRR/Python; runs one `nebius-vpngw-agent` systemd service.
- **Agent:** single daemon per VM; renders configs, applies idempotently, and persists `/etc/nebius-vpngw/last-applied.json`. Reload via SIGHUP.
- **Optional HA Route Controller:** updates VPC route next-hops to flip between gateway VMs for HA.
- **Deployment modes:** (a) single VM with multiple tunnels (active/active tunnels, VM is SPOF), (b) gateway group (N VMs) with per-tunnel pinning and route switching for VM-level HA.

## 4. Nebius Networking Model

- One VPC network chosen via `network_id` (optional; defaults to environment’s default VPC).
- One dedicated subnet `vpngw-subnet` (/27) created if missing under the selected network; workload subnets remain separate.
- Platform constraint (current): 1 NIC per VM, each with 1 public IP; all tunnels share that IP and are differentiated by IKE/IPsec IDs.
- Future-ready: `vm_spec.num_nics` accepts >1 when platform allows; code and config are structured for multi-NIC.
- Dedicated subnet rationale:
  - Isolation and blast-radius control for firewall/NACL rules and logging; limits exposure of workloads to gateway misconfig.
  - Routing clarity for next-hops, failover, and asymmetric-path avoidance; easier to reason about HA route flips.
  - IP hygiene: small, controlled CIDR for gateway NICs/allocations; avoids overlap or churn with app addresses.
  - Policy separation: distinct egress controls/inspection for gateway traffic without touching app subnets.
  - Operations: safer redeploys/recreates and reduced ARP/ND noise near workloads.

## 5. Public IP Allocations (`external_ips`)

- Config shape: `external_ips[instance_index][nic_index]` → allocation IP string.
- Behavior: omitted/empty ⇒ create allocations; provided ⇒ use existing; insufficient ⇒ create missing. Auto names: `{instance}-eth{N}-ip`.
- Preservation on recreation: allocations are kept and reattached; recreation does not delete allocations. Empty slots trigger new allocation creation.
- Examples:
  - Single VM, single NIC:

    ```yaml
    external_ips: [["1.2.3.4"]]
    ```

  - Two VMs, single NIC each:

    ```yaml
    external_ips: [["1.2.3.4"], ["5.6.7.8"]]
    ```

  - Future multi-NIC:

    ```yaml
    external_ips: [["1.2.3.4", "1.2.3.5"]]
    ```

- Limits: platform only supports 1 NIC and no public IP aliases (aliases are private-only).

## 6. Configuration Model (YAML)

- Single file `nebius-vpngw-config.config.yaml` with sections: `gateway_group`, `defaults`, `gateway`, `connections[*].tunnels[*]` (supports `ha_role`, `routing_mode`, crypto, APIPA inner IPs).
- Key fields: `network_id` (optional), implicit `vpngw-subnet`, `external_ips` as above, `gateway.local_prefixes`.
- Merge precedence when resolving: tunnel overrides connection overrides peer-config overrides defaults; missing mandatory fields error out.
- Environment placeholders `${VAR}` expand; all missing vars reported together.
- First run convenience: if no local config is provided/found, CLI copies a template to CWD then exits.

## 7. Workflows & CLI

- Command: `nebius-vpngw` (or `python -m nebius_vpngw`).
- Typical apply: parse args → load YAML and peer configs → merge and validate → ensure network/subnet → ensure VMs + allocations → push per-VM config over SSH → `systemctl reload nebius-vpngw-agent` → reconcile static routes (if static mode).
- Flags: `--local-config-file`, repeatable `--peer-config-file`, `--recreate-gw`, `--project-id`, `--zone`, `--dry-run`.
- Peer import: vendor parsers (GCP/AWS/Azure/Cisco) normalize templates; merger fills only missing fields—YAML topology is never overridden.

## 8. Routing Modes & Local Prefixes

- Modes: global default under `defaults.routing.mode` (bgp|static); override per connection/tunnel.
- BGP: FRR neighbors per active tunnel; advertises `gateway.local_prefixes` when `connection.bgp.advertise_local_prefixes` is true. No tunnel-level override.
- Static: strongSwan leftsubnet from `gateway.local_prefixes` unless `tunnel.static_routes.local_prefixes` overrides (supports subnet isolation/migration).
- Use cases: selective BGP advertisement, BGP peer without ads, per-tunnel static prefixes, mixed BGP/static across connections.

## 9. HA & SLA

- Single VM (`instance_count=1`): multiple active tunnels; ECMP within gateway and/or peer side; VM is SPOF.
- Multi-VM (`instance_count>1`): assign tunnels via `gateway_instance_index`; `ha_role: active|disable` to control live tunnels per VM; optional route controller flips VPC routes to standby on failure.

## 10. VM Build & Services

- OS: Ubuntu 22.04 LTS. Packages: strongSwan ≥5.9, FRR ≥9, Python 3.x.
- Agent: systemd service (`nebius-vpngw-agent`), reload via SIGHUP; renders strongSwan/FRR configs and persists last applied state.
- Bootstrap via cloud-init installs packages, unit file, directories; tunnel config applied only at runtime by the agent.

## 11. Idempotency & State

- Orchestrator is effectively stateless: derives desired state each run from YAML + live discovery by deterministic names.
- Agent keeps minimal VM-local state (`last-applied.json`) to avoid unnecessary reloads and to be restart-safe.
- Re-runs reconcile actual vs desired; `--recreate-gw` forces VM replacement while preserving allocations.

## 12. Implementation Status (repo)

- CLI and config merger functional, including env expansion and peer merge heuristics.
- Agent renders scaffold configs and writes `last-applied.json`.
- Managers under `deploy/` (VM/SSH/route) are scaffolds logging intended actions; they need Nebius SDK + SSH integration to be operational.
- Entry points in `pyproject.toml`: `nebius-vpngw`, `nebius-vpngw-agent`, `build-binary`. Template creation on first run is implemented.

## 13. Allocation Preservation Algorithm (Summary)

- On recreation: capture attached allocations, delete VM (allocations stay), recreate VM, reattach preserved allocations; create new ones for any gaps. Error if YAML-specified allocation is busy/missing; fall back to creation only when YAML omitted/insufficient.

## 14. Deployment & Packaging Workflow

- Local dev: `poetry install` or `pip install -e .` for orchestrator use.
- Agent deployment: orchestrator builds/uses wheel from `dist/`, uploads via SSH/SFTP, installs on VM (`pip install --force-reinstall`), places config under `/etc/nebius-vpngw/config-resolved.yaml`, reloads agent.
- Always rebuild wheel after agent code changes (clean `dist/ build/ *.egg-info` then `poetry build` or `python -m build --wheel`); cached wheels otherwise hide changes on VMs.
- Verification on VM: `systemctl status nebius-vpngw-agent`, `journalctl -u nebius-vpngw-agent -n 50`, inspect `/etc/ipsec.conf` and `/etc/frr/bgpd.conf`.

## 15. Limitations & Risks

- Platform: single NIC per VM, no public-IP aliases; no ECMP in route tables (load sharing happens within gateway/peer).
- Requires stable public allocations—document and monitor unassigned allocations (30-day grace before provider GC).
- SSH/SDK integrations are scaffolds; production requires completing those components and adding tests.

## 16. References

- Diagrams: `image/vpngw-architecture.dot`, `image/vpngw-conn-diagram.dot` (render with Graphviz).
- README: quick start, packaging, build instructions (root `README.md`).
