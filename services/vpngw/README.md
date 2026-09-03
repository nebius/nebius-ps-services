# Nebius VPN Gateway

Site-to-site IPsec/BGP VPN gateway for Nebius AI Cloud. Supports GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco IOS, and custom peers.

This project is an open source, self-service, VM-based Nebius VPN gateway. It is not a managed Nebius VPN service.

## Table of Contents

- [Quick Start Guide](#quick-start-guide)
- [Security Notice](#security-notice)
- [Features](#features)
- [Installation (Detailed)](#installation-detailed)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Commands](#commands)
- [Routing Modes](#routing-modes)
- [BGP Configuration](#bgp-configuration)
- [Static Routing Configuration](#static-routing-configuration)
- [Peer Integration](#peer-integration)
- [VM Management](#vm-management)
- [System Monitoring](#system-monitoring)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Release & Versioning](#release--versioning)
- [Project Structure](#project-structure)

## Quick Start Guide

Use this sequence for first-time deployment. It is optimized for end users and keeps advanced topics in later sections.

### Prerequisites

- Nebius AI Cloud account and project
- A VPC where the VPN gateway subnet can be created
- Python 3.10-3.12 and `pipx`

### 1. Install the CLI from a release wheel

Run this from a clean shell, not inside an activated Python virtual environment.

Install directly from the GitHub release wheel URL:

```bash
pipx install https://github.com/nebius/nebius-ps-services/releases/download/nebius-vpngw-vX.Y.Z/nebius_vpngw-X.Y.Z-py3-none-any.whl
```

To replace an existing install with a clean `pipx` venv:

```bash
pipx uninstall nebius-vpngw
pipx install https://github.com/nebius/nebius-ps-services/releases/download/nebius-vpngw-vX.Y.Z/nebius_vpngw-X.Y.Z-py3-none-any.whl
```

Verify:

```bash
which nebius-vpngw
pipx list
nebius-vpngw --version
```

Use `nebius-vpngw --help` for the quick-start sequence and
`nebius-vpngw COMMAND --help` for command-specific guidance. Every public
command help page includes at least one practical invocation example; mutating
commands still retain their documented confirmation, approval, and fencing
boundaries.

For wheel-based installs, `apply` reuses the original release wheel URL or local wheel file recorded
by pip when it deploys the agent to gateway VMs. End users do not need a source checkout or
`python -m build`.

### 2. Create a starter config

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

In a terminal, this opens a guided wizard for project, gateway, network,
connection, tunnel, and optional advanced settings. The wizard validates the
complete schema-v1 candidate before an atomic write and keeps VM-level HA
disabled unless you explicitly enable it. Fresh connection and tunnel defaults
are provider-neutral (`site-1`, `generic`). For each PSK, enter an uppercase
environment-variable name, enter a literal value through hidden input, or press
Enter to retain the generated placeholder and complete it later. Enter `?` for
field help, `b` to restart the previous section, or `q` to quit without writing.

Automation remains compatible: non-TTY invocations write the existing commented
template. Use `--interactive` to force the wizard (including in a scripted test)
or `--no-interactive` to force template generation.

### 3. Prepare Nebius network and reserve public IPs

After the wizard writes a valid config, it offers this operation separately and
defaults to **No**. The confirmation explains that it authenticates to Nebius and
may ensure or create a subnet, route table, public allocations, and the YAML
`external_ips` entries. Declining keeps the config only.

For a network-first workflow, or to rerun the operation later, use the supported
standalone command below. It remains safe to rerun.

`prep-network` creates or reuses a dedicated explicit-CIDR gateway subnet (`vpngw-subnet` by default), using `gateway_group.subnet.cidr` or the first free `/24` (or configured `prefix_length`) from the network's private pool. When CIDR is omitted, an existing exact-name subnet is reused with its current explicit CIDR and `prefix_length` applies only to creation. For an explicit CIDR outside the pool, the command can extend the pool when the network has exactly one private pool; it fails on overlaps or an incompatible existing subnet. It then verifies an exact `0.0.0.0/0` default-egress route, adding it only to an exclusively attached gateway route table. Shared or conflicting route tables and unverifiable reads fail the command instead of producing warning-only success.

Workflow:

1. If you used `--no-interactive`, fill minimal fields in `my-vpn.config.yaml`: `tenant_id`, `project_id`, `region_id`, `gateway_group` (leave `connections` for later).
   `project_id` must be set to a real value (or resolved via `${PROJECT_ID}` env var) before `prep-network`.
   Set `gateway_group.network_id` if you want a custom Nebius VPC instead of the auto-resolved `default-network`.
   Omitting it is supported; `apply` reports the selected network once while retaining its internal VM-HA safety rereads.
2. Run network preparation:

   ```bash
   nebius-vpngw prep-network --local-config-file my-vpn.config.yaml
   ```

   Interactive terminals offer eligible existing allocations from the gateway
   subnet before reserving new addresses. Use `--no-interactive` for prompt-free
   automation or `--interactive` to force the selector.

3. Share the allocated Nebius public IP(s) with the peer network team.
4. The peer team creates their VPN gateway and points tunnels to those Nebius public IPs.
5. After you receive peer tunnel details, complete the config and apply.

### 4. Complete peer gateway/tunnel details

The wizard collects peer public IPs, hybrid PSK inputs, inner `/30` CIDRs,
static prefixes, and BGP ASNs in dependency order. It asks routing mode before
routing-specific fields and asks the local ASN only once, on the first BGP
connection. For a
network-first workflow, rerun with `--interactive --force` after peer-side
creation, or complete `connections` and `tunnels` directly in YAML.

Generated template notes:

- `inner_cidr` must be APIPA `/30` (`169.254.0.0/16`)
- For multi-tunnel HA, use explicit roles (`ha_role: "active"` / `ha_role: "passive"`)
- Keep literal PSKs only in private mode-`0600` config files; `${VAR}` placeholders remain recommended for automation

For shorter starting points, see the [static routing example](examples/static-example.config.yaml) and [BGP routing example](examples/bgp-example.config.yaml).

### 5. Apply the configuration

```bash
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

### 6. Configure local routes

```bash
nebius-vpngw add-routes-local --local-config-file my-vpn.config.yaml
nebius-vpngw add-routes-local --local-config-file my-vpn.config.yaml --swap-route-table
nebius-vpngw list-routes-local --local-config-file my-vpn.config.yaml
```

### 7. Validate status and connectivity

```bash
nebius-vpngw status --local-config-file my-vpn.config.yaml
```

Optional data-plane check: from a VM in one of `gateway.local_prefixes`, test reachability to a remote private IP over the VPN.

### Firewall Requirements

Open any ports and protocols required by your application on your network so traffic from the Nebius side can reach the intended services.

For example, if you want to test connectivity with `ping`, create an ingress firewall rule for the `icmp` protocol that allows traffic from the source `local_prefixes` (the Nebius source subnets) to your network.

### 8. Optional: manual failover/failback (BGP active/passive)

```bash
nebius-vpngw failover tunnel tunnel-2 --local-config-file my-vpn.config.yaml
nebius-vpngw failback tunnel tunnel-1 --local-config-file my-vpn.config.yaml
```

The resource-scoped command migration has no compatibility aliases:

- `vm-ha-failover` → `failover vm`
- `vm-ha-failback` → `failback vm`
- `failover [TUNNEL_NAME]` → `failover tunnel [TUNNEL_NAME]`
- `failback [TUNNEL_NAME]` → `failback tunnel [TUNNEL_NAME]`

This is useful for planned maintenance, peer changes, or operational testing.

For advanced setup, continue with [Configuration](#configuration), [Commands](#commands), and [Routing Modes](#routing-modes).

> **Ordinary VPN gateway route management:** After `apply`, run
> `nebius-vpngw add-routes-local` whenever remote routes change. The command
> updates the route tables of Nebius workload subnets selected by
> `gateway.local_prefixes`, adding routes to the remote prefixes through the
> gateway's private allocation.
>
> **VM-HA-enabled VPN gateway route management:** Nebius VPN Gateway supports
> explicit two-node VM HA by running an active gateway VM and a passive standby
> VM with a movable shared private IP address. In this mode, use `apply` to
> deploy configuration changes. Users do not need to run `add-routes-local` to
> publish remote routes into Nebius VPC route tables. The VM-HA controller
> ensures that only the verified shared-IP owner reconciles those routes through
> the shared private IP address.

## Security Notice

**Configuration files contain sensitive secrets (PSKs, service account keys).**

- **Recommended:** Name configs `*.config.yaml` (auto-ignored by git)
- **Required:** Ensure `.gitignore` includes your config file patterns
- **Best practice:** Use environment variables for secrets with `${VAR}` syntax

Every gateway requires exact operator-to-gateway SSH host trust before `apply`
creates or changes its Compute resources. By default, `apply` manages public pins in a
deployment-specific store at `~/.ssh/nebius-vpngw/<scope-sha256>/`. The scope
binds the tenant, project, region, gateway group, and either the ordinary
topology discriminator or HA cluster. Each member is pinned by
its stable hostname, while SSH connects to the current management IP with that
hostname as `HostKeyAlias`. The authoritative receipt stays hostname-keyed; its
derived OpenSSH projection also lists the exact current management-address
aliases so a supported older release can use that file explicitly.

`apply` can create or repair this store only from trusted evidence: an existing
managed receipt, a validated explicit pin file, a safe exact retained-member
Ed25519 entry in literal `~/.ssh/known_hosts`, or authenticated product
cloud-init. The general file is a read-only one-time migration source: apply
never creates, repairs, or rewrites it, and only exact current hostname/address
records whose Compute and provisioning or lifecycle identity still match are
eligible. When `VPNGW_SSH_HOST_KEYS_DIR` is unset,
actual `apply` uses
`~/.ssh/nebius-vpngw/host-keys/<gateway-group>/<scope-sha256>/` as the effective
default. The digest binds tenant, project, region, gateway group, and topology
so same-named gateways in different deployments never share private identity.
Actual apply creates that owner-only hierarchy and generates a missing
unencrypted Ed25519 `<gateway-hostname>.key` only for a genuinely fresh member.
The same exact key is pinned locally and installed as the new member's SSH
server identity. This includes a newly created ordinary gateway, so its first
SSH connection waits for the pre-pinned identity instead of learning a key from
the network.
Existing valid default keys are reused; retained or recreated members are never
silently given a replacement identity. If their original product-generated key
is no longer local, apply may recover it from the authenticated current Compute
cloud-init at the exact product key path. VM-HA recovery additionally requires
the current product marker, or the legacy product path backed by a hardened
active lifecycle record.
The recovered public key must match every existing authority. `apply --dry-run`
uses only ephemeral preview material and writes no key or trust state.
`create-config` only authors the YAML deployment configuration; it never creates
private host keys or enrolls SSH trust. That work belongs to `apply`, after live
topology discovery can classify each member as genuinely fresh, retained, or a
requested recreation.

Set `VPNGW_SSH_HOST_KEYS_DIR` to an absolute existing directory to override the
default. An explicit directory is never created or populated by the product.
Every consumed key must be a current-user-owned, single-link file with no group
or other access (normally mode `0600`). An explicitly empty or invalid override
fails instead of using the default. A successful apply first verifies every
retained member and rechecks its exact Compute binding for every trust source,
then publishes an owner-only public-key receipt and OpenSSH projection before
cloud mutation. Bootstrap health probes use the same configured management
username as later configuration SSH. `status`, route commands, tunnel
restart/failover/failback, transfer commands, and mTLS commands reuse an existing
managed receipt but never write or repair persistent SSH trust. An ordinary
deployment that predates managed enrollment keeps its former system-known-hosts
behavior until a successful `apply` publishes deployment trust.

`VPNGW_SSH_KNOWN_HOSTS_FILE` remains an optional highest-precedence override and
migration source. If set, it must be an absolute, readable, non-empty OpenSSH
file with exact member pins; an invalid or incomplete value fails without
falling back to managed state. Apply never rewrites that file. Trust-on-first-use,
`ssh-keyscan`-only enrollment, using the general file as ongoing canonical
trust, and disabled host verification are not supported. Apply also proves that
the configured management public key matches exactly one usable non-interactive
identity: the explicit mode-`0600` private key, one matching `ssh-agent` key, or
one matching owner-only default private key. This proof completes before
deletion, allocation, disk, or instance work.
Cloud-init validates the rendered SSH configuration, activates either the
image's socket- or service-based SSH model, and verifies a port-22 listener
before bootstrap completes. Public-only, encrypted, malformed, mismatched,
unreachable, or identity-rejected members fail closed. During clean bootstrap,
each member first
renders inert deterministic local strongSwan and FRR files behind the cold-start
guard. After the controller grants current-boot passive authority, the member
may establish only its local tunnel, XFRM, and BGP materialization needed to
measure promotion readiness. Forwarding, firewall changes, allocation transfer,
VPC route effects, and owner-only reconciliation remain fenced until exact
active authority is proven.

### VM-HA SSH trust and mTLS

These are two separate trust relationships:

- **SSH trust is operator to gateway.** The managed receipt/projection, or the
  optional `VPNGW_SSH_KNOWN_HOSTS_FILE` override, lives on the operator host;
  it is not a `known_hosts` file on either gateway. It stores each member's
  pinned public SSH server key. The matching private host key stays on that
  gateway and is what `sshd` presents. The CLI uses this trust for status,
  configuration staging, agent operations, and managed mTLS bootstrap or
  recovery.
- **mTLS trust is gateway to gateway.** Each member keeps its own private mTLS
  identity and an exact public certificate pin for its peer. The HA runtime
  uses the authenticated, encrypted channel for heartbeats and readiness/state
  evidence. It is not configuration-file synchronization and it does not
  replace Nebius cloud ownership checks.

An `exact SSH trust is unavailable` status result means the local CLI could not
construct the required pin policy; it does not prove that a gateway deleted or
changed its SSH host key. Run `apply` with the same configuration. It will
prepare default keys for genuinely fresh members, repair a missing managed store
when authoritative retained-member host-key files remain in the default
per-gateway directory, migrate a verified exact current entry from
`~/.ssh/known_hosts`, or recover the product-generated identity from bound
Compute cloud-init. This behavior is shared by ordinary and VM-HA gateways in
Static and BGP modes.
Read-only VM-HA status resolves the managed receipt once for its complete
two-member scope and reuses that immutable policy for both probes. An explicit
known-hosts override remains isolated per member and is never persisted by a
read-only command.
Use an environment override only when the keys live elsewhere:

```bash
export VPNGW_SSH_HOST_KEYS_DIR=/absolute/protected/member-host-keys
nebius-vpngw apply --local-config-file vm-ha.config.yaml
```

For a one-time migration, an exact current Ed25519 entry already in the safe
default user file needs no environment variable. Otherwise set
`VPNGW_SSH_KNOWN_HOSTS_FILE` to an authoritative file for one successful
`apply`, then unset it. If neither a verified pin nor authenticated product
provisioning evidence remains for one unchanged pre-branch ordinary VM, actual
ordinary `apply` performs one bounded network enrollment. It verifies two
identical Ed25519 observations around an unchanged Compute reread, connects
with that key pinned and only the configured client identity, correlates guest
identity, and reproves Compute before publishing. This preserves the VM, disk,
NIC, allocations, routes, and installed gateway services, but it cannot exclude
an active transparent attacker during that first observation. A published
receipt is never relearned automatically. `apply --dry-run` does not observe or
write the key; it exits nonzero and tells you to run actual ordinary `apply`.
Never use `ssh-keyscan` as authority.

The configured `ssh_public_key` must match exactly one usable client identity:
the explicit owner-only `ssh_private_key_path`, one loaded `ssh-agent` key, or
one owner-only `~/.ssh/id_ed25519`, `id_ecdsa`, or `id_rsa`. Password,
keyboard-interactive, and unrelated-key fallback are disabled.

### Migrating one gateway VM to VM-HA

VM-HA remains explicit and default-disabled. Adding an enabled
`gateway_group.vm_ha` block does not recreate the existing gateway: the
configured active member keeps its Compute instance, boot disk, NIC, primary
private allocation, public allocation, and unrelated NIC aliases. Apply adds
the missing passive with its own primary address and creates one movable
secondary private alias used only for HA ownership.

The preferred idempotent entry point is `vm-ha`. It accepts either the
ordinary source or an explicit VM-HA candidate, preserves an ordinary source,
and repeatedly uses the same deterministic `<stem>.vm-ha.config.yaml`
candidate unless `--output` selects another path:

```bash
nebius-vpngw apply --local-config-file my-vpn.config.yaml
nebius-vpngw vm-ha --local-config-file my-vpn.config.yaml
nebius-vpngw vm-ha --local-config-file my-vpn.vm-ha.config.yaml --dry-run
```

Run actual ordinary `apply` first, even when the ordinary gateway is already
installed and requires no infrastructure change. That command is the sole
writer for the ordinary managed SSH receipt and performs the one-time bounded
enrollment above only when necessary. `vm-ha` refuses before prompting,
candidate publication, or passive-IP reservation when the receipt is absent.
The VM-HA apply plan then carries the retained active's exact pin, ordinary
predecessor receipt digest, and current Compute binding into the HA trust
receipt and approval. If only an explicit candidate remains, restore the
ordinary source and run `apply -c <ordinary-source>` before retrying `vm-ha`.

Material VM-HA planning is read-only, classifies destructive and VPN-traffic
impact independently, and requires one existing deployable wheel. Set
`VPNGW_AGENT_WHEEL` to the exact wheel, or keep exactly one
`nebius_vpngw-*.whl` in the project `dist/` directory. The planner validates
that the wheel contains standby restoration, publishes its SHA-256 as
`approval.artifact_sha256`, and binds that digest plus the typed impact into
the exact plan. It never
builds, deletes, downloads, or falls back to an installed package. Planning
opens the selected regular file without following a final symlink, validates
its wheel metadata and `RECORD` entries for the real agent capability entry
point, and binds both file identity and digest. Execution rechecks that same
identity and digest before any convergence effect, before the one exact upload
to each member, and again before activation. Package preparation installs both
the agent and its service assets directly from those verified wheel bytes;
activation never selects, rebuilds, uploads, or consumes volatile staged copies
of those assets. It applies the final verified peer-firewall helper and
tmpfiles policy before refreshing services, then proves the installed
capability on each member. Before an apply-owner adoption replaces a prior
promotion receipt, the owner durably retires an exact completed or blocked
standby-restoration authorization; an active or unreadable authorization fails
closed and preserves the prior receipt. A missing, ambiguous,
incompatible, or changed artifact returns typed rebuild or selection guidance.
If it changes after convergence starts, the result explicitly says effects may
have begun and the next `vm-ha` run resumes from durable checkpoints.

New VM-HA activations initialize automatic standby restoration as explicitly
`enabled`. During an agent-schema upgrade, activation invalidates only the
cached advisory peer heartbeat through an exact generation-bound reset consumed
after the old controller stops, while retaining the anti-replay boundary. The
restarted controller must authenticate fresh peer evidence before using it.
For planned maintenance, change the durable two-member policy with the same
role-neutral command:

```bash
nebius-vpngw vm-ha --local-config-file my-vpn.vm-ha.config.yaml \
  --standby-auto-healing disabled
# Stop and maintain the passive VM only after the command reports maintenance-ready.
nebius-vpngw vm-ha --local-config-file my-vpn.vm-ha.config.yaml \
  --standby-auto-healing enabled
```

The command is idempotent and reports the requested result directly: a
same-state request says auto-healing is already enabled or disabled, while an
opposite-state request says it was enabled or disabled successfully. If an
earlier enabled recovery finished but its final private record cleanup was
interrupted, either explicit policy request validates and clears every present
exact member-local completed record internally, rereads both members, and
completes the requested result in the same invocation. Repeated restorations
may leave one completed local record on each member; each is cleared only by
its exact observed digest. Operators do not need to enable first merely to
disable. After a completed policy transaction, the CLI may briefly wait for
the public peer-heartbeat projection to reflect the exact terminal decision;
this observation is read-only, requires the same exact owner and frozen cloud
observation digest for every sample, and never repeats prepare or commit.

Changing the policy is explicitly non-destructive and is not expected to
interrupt current VPN traffic, so the explicit `--standby-auto-healing`
selection executes without a second confirmation. `--dry-run` still emits the
exact read-only plan and impact; when cleanup is pending, that plan orders the
cleanup before the requested policy result without mutating either member and
binds its digest to the exact pending recovery records.
Omitting the option preserves the current policy. Disabled policy blocks
ordinary standby rearm and planned
failover/failback, but it does not disable automatic owner-loss failover or
weaken promotion fencing. The rearm service remains enabled and is still the
sole Compute-start writer. If a bare invocation reports
`standby-auto-healing-policy-invalid`, rerun with the explicit
`--standby-auto-healing enabled` option to plan or resume the exact
approval-bound recovery. The one exception is an exact completed non-owner
replacement whose fresh member still has only its default initialized policy:
bare `vm-ha` treats that as apply-owned replacement convergence, installs the
current agent on the required members, and adopts either the retained owner's
acknowledged enabled decision or its exact deterministic initial enabled
decision on the replacement before that member's control services start. For
the otherwise unacknowledged initial decision, the command then runs the
canonical two-member enabled-policy transaction before releasing replacement
inhibition; an interrupted prepare or commit resumes only that deterministic
successor. Every non-default unacknowledged, unrelated split, or conflicting
policy remains blocked. A recovery that already reached its durable
Compute-completed checkpoint receives a fresh approval bound to current cloud
authority. A Running or transitional target resumes without another start;
a target that is currently Stopped plans a new owner-side re-arm and one exact
start request before standby readiness and peer-policy agreement. If the rearm
service wins its single-writer lock while the CLI submits that request, the CLI
continues waiting only after the exact approval-bound recovery advances.
Policy mutation and completed-recovery cleanup require installed agents that
advertise `vm-ha-standby-auto-healing-policy-v4`; an older agent fails the
capability preflight and must be updated through the supported `apply` workflow
before retrying. There is no mixed-version fallback.

sole Compute-start writer; missing, stale, corrupt, transitioning, or split
policy evidence inhibits starts instead of guessing. Re-enable the policy
after maintenance when the current owner is healthy. If the non-owner is still
stopped, the exact plan explicitly includes standby recovery: the owner
persists a single-use, authority-bound recovery intent, the existing rearm
service starts only that exact stopped member, and `vm-ha` waits for fresh
standby readiness before committing `enabled` on both members. The CLI never
starts Compute directly. Ownership, allocation, target revision, policy, or
writer drift fails closed and requires a fresh plan.

Interactive conversion accepts an existing unattached passive public IP or
asks separately, with a default-No confirmation, whether to create or reuse the
deterministic passive allocation. Effective-region precedence is explicit
`--region`, then `gateway_group.region`, then top-level `region_id`. JSON and
noninteractive runs never prompt and instead return an actionable
`action-required` result.
`--force` can republish
only an exact candidate that needs a safe permissions repair; it never replaces
conflicting YAML. Each material plan exposes a concise impact plus independent
destructive, VPN-interruption, and resource-creation fields. A plan explicitly classified as
non-destructive with no expected VPN interruption and no resource creation executes without a second
confirmation. Otherwise interactive text shows the exact kind, digest, impact,
and effects, then asks once with `[y/N]`; explicit `y` continues in the same
invocation, while Enter or No performs no mutation and keeps exit status `3`.
JSON and noninteractive calls never prompt: safe plans execute automatically,
while risky or unknown plans require `--approve <DIGEST>` after review. Dry-run
never executes. Every execution path binds the plan again inside the executing
apply engine before its first effect. It may
safely resume a journaled apply, provision again from a verified `REMOVED`
tombstone, reconcile apply-owned drift, observe controller progress, or ask the
  existing sole-start writer to restore an exact stopped non-owner. For one
  authoritatively absent current non-owner in an ACTIVE lifecycle, interactive
  text mode asks `Create the missing non-owner VM now? [y/N]` and explicit `y`
  performs the exact replacement in the same invocation. JSON, noninteractive,
  and dry-run output retain the exact `active-standby-replacement` digest and
  `--approve DIGEST` automation contract. These VM-HA-owned plans use the
  `vm-ha-required` classification. Cloud-confirmed member absence takes precedence over
  any stale configured public IP, so the missing non-owner is not probed over
  SSH before this replacement is planned. If that absent member's original
  product-managed private SSH key is also missing, the same digest-bound plan
  includes a managed identity rotation. Only product-managed trust plus default
  product key storage is eligible; explicit `VPNGW_SSH_KNOWN_HOSTS_FILE` or
  `VPNGW_SSH_HOST_KEYS_DIR` ownership is never modified. If either override is
  present when a checkpointed rotation resumes, `vm-ha` reports
  `replacement-ssh-identity-unavailable` and directs the operator to restore
  the product-managed invocation environment. If its exact managed trust
  predecessor is unavailable instead, the same reason directs the operator to
  restore that predecessor. A checkpointed replacement retains the prior
  ACTIVE allocation and route runtime authority while its lifecycle is
  `PROVISIONING`, so the serving-owner capability preflight can safely choose
  the live-peer path or its explicit restart fallback. The approved
  transaction records the trust predecessor, stages one retry-stable key,
  binds its new fingerprint and successor public-trust digests, publishes the
  managed key and trust, and rebuilds strict SSH policy before cloud creation.
  It then freshly re-proves the serving owner and retained allocations and
  installs a dedicated transfer inhibition that preserves owner forwarding
  through the cloud replacement effects. A capable owner is neither upgraded
  nor restarted: managed mTLS supplies the new peer Compute identity live, the
  exact replacement runtime binding is published atomically, and only the new
  standby is installed and activated. If the owner lacks that capability, one
  combined confirmation explicitly covers the owner control-service upgrade
  and restart, the replacement, and the possible brief VPN interruption. If an
  earlier run stopped after journaling the inhibition intent, but before the
  owner refresh became a prerequisite, resume clears only that exact host-local
  intent before any replacement cloud creation, refreshes the owner, and then
  replays and reproves the same inhibition idempotently. The canonical v4
  live-peer capability also covers the exact authoritative owner's local
  dataplane preparation, route reconciliation, and forwarding restoration
  while replacement transfer remains inhibited; ownership-changing effects
  remain fenced. Replacement inhibition advertises its v2 capability after
  gaining exact terminal-restoration cleanup. A guarded owner
  paused at the inhibition checkpoint is admitted only with the matching
  operation, lifecycle, owner, route receipt, unlocked writers, and no accepted
  replacement cloud effect. Resume journals the distinct v5 owner refresh,
  installs the approved artifact, and then retires only a structurally valid
  completed or blocked restoration authorization superseded by the same-
  authority apply-owner-adoption receipt. Active, malformed, foreign, or
  differently owned restoration evidence remains blocking. The transaction
  leaves all old disks untouched, creates a fresh cycle-qualified boot disk and
  configured-name Compute, accepts same-name resources only when their IDs
  match the accepted cloud operations, preserves network authority, and resumes
  idempotently after interruption. It does not
  exercise failover, invent credentials or network-derived SSH trust, reset
  controller repair,
  or infer the replacement target from configured active/passive role. If the
  deleted member uses explicit operator-owned SSH trust or key storage and its
  matching private key is unavailable, planning stops before approval or
  mutation with `replacement-ssh-identity-unavailable`. Restore that original
  private key and rerun `vm-ha`; this trust prerequisite is never reported as
  an authentication failure. Running plain `apply` in either case performs no
  replacement and directs the operator to `vm-ha`, which owns the exact
  creation approval.

One additional apply-owned recovery is supported for the recurring
post-failover/failback skew: an exact ACTIVE cluster with one Running serving
owner that is stale only because its controller lacks standby restoration, and
one alias-free Stopped non-owner. The approved `artifact-standby-recovery`
plan installs and reproves the bound artifact on the owner first, uses only the
owner-side rearm service to restore Compute and SSH, installs the identical
artifact on the standby, then resumes canonical non-owner-first apply and the
normal two-sample health proof. It never grants the CLI direct Compute, route,
allocation, or forwarding authority. The legacy owner's policy view is accepted
only when rearm is inhibited specifically because the stopped peer cannot
provide live policy evidence with blocked policy status, or a pre-v2 runtime
rejected an already committed transfer after its mutable latest agreement
refreshed during cutover while durable policy status remains transitional.
Disabled, changing, invalid, or otherwise blocked policy and any additional
drift stop the lane.

Because that composite recovery upgrades the serving owner before resuming
canonical apply, it remains approval-required. Its concise impact states that
VPN traffic may be briefly interrupted and that no gateway VM or disk is
deleted.

If exact cloud authority proves that a non-owner still reports active
forwarding, `vm-ha` observes the existing controller-owned safety fence instead
of treating the state as an apply plan or an ambiguous dead end. The controller
operation must target that reporting non-owner before the facade accepts it.
The controller disables non-owner forwarding; traffic incorrectly using that
member may be interrupted, while the authoritative owner is preserved. The CLI
performs no apply, rearm, route, allocation, promotion, or direct forwarding
effect, so it does not request approval. Repeated unchanged evidence stops
safely and directs the operator to both controller service journals.

`--output-format json` emits one
`nebius-vpngw/vm-ha-result-v1` object on stdout; progress stays on stderr.
In text mode, a healthy run ends with only `VM-HA is healthy now.` because the
preceding progress already identifies the verified work. The JSON result keeps
the full classification, health, effective config, passive verification scope,
and `failover_tested=false` fields for automation.
Progress names each authoritative inspection, approval/lock check, apply or
rearm action, VM-HA control-service restart, bounded wait, and verification
phase. On an interactive terminal, a running phase uses an animated spinner,
updates its elapsed label in place, and leaves one final row: green `✓` for
success or a fully red `✗` row for failure. Noninteractive streams emit only
that terminal row, and long waits update at a bounded cadence instead of adding
one line per poll. During ordinary-to-VM-HA conversion, configuration
resolution completes before the first blocking wizard prompt so an animated
status never competes with terminal input. A separately confirmed passive-IP
preparation uses its own progress phase. Raw apply/provider/SSH diagnostics are
suppressed by the façade in both formats and cannot concatenate with a running
spinner. Successful config-push readiness for both members appears once as
`✓ both VM-HA members are ready for configuration.`, and exact agent-package
preparation likewise uses one managed completion row instead of per-member
chatter. In particular,
internal Nebius SDK retries do not print
their tracebacks into the spinner; if the SDK ultimately fails, the raised
error still reaches the command's sanitized failure result. A required
activation failure retains only its sanitized command class and exit result;
apply-owned activation failures direct the operator to status and service
journals instead of incorrectly reporting an authentication failure. Success
proves two agreeing fresh samples of the current active/standby state; it does
not exercise failover.

Direct explicit VM-HA `apply` keeps the same finite Nebius SDK request and
retry policy. If that budget ends in `DEADLINE_EXCEEDED`, it stops with a
concise timeout plus `vm-ha` recovery guidance instead of SDK retry or Python
tracebacks. Ordinary non-HA apply output and errors are unchanged.

A manual failover or failback request cannot supersede a transfer whose durable
lineage has already started. Upgraded controllers retire only an exact
contradictory request proven to have been written later by an older runtime;
older or invalid conflicts remain blocked. The guard, controller, and health
monitor also preserve their shared `/run/nebius-vpngw` runtime directory across
service stops and restarts so controller recovery cannot invalidate the guard's
routing lock.

For an existing ordinary `instance_count: 1` config, use the dedicated wizard
instead of editing the two-member topology by hand:

```bash
nebius-vpngw vm-ha \
  --local-config-file my-vpn.config.yaml
```

The default output is `my-vpn.vm-ha.config.yaml`; use `--output` to choose a
different new file. The command never converts in place, asks for credential
paths, or creates credentials or IAM resources. Its summary shows the future
managed source under
`~/.config/nebius-vpngw/credentials/<project>/<gateway>/nebius-credentials.json`.
Only the later, exactly approved `apply` resolves the operator home, creates or
reuses that credential, and installs a separate protected copy on each VM. No
CA, certificate, or TLS private-key path belongs in YAML: `apply` generates an
independent self-signed identity on each VM and exchanges only exact public leaf
certificates over pinned SSH.

The wizard preserves the raw source and environment references, derives one
passive-member counterpart for every existing tunnel, and asks only for the
new passive-member and peer-side inputs. You may enter an already reserved
passive public IP or separately confirm a default-No Nebius operation that
ensures the gateway subnet/route table and creates or reuses only
`<gateway>-1-eth0-ip`. That preparation does not inspect the serving member's
allocation and does not create VMs, shared aliases, managed routes, lifecycle
state, or host configuration. If an allocation request may have been accepted
before its result failed, the command reports the deterministic allocation
name and tells you to rerun for safe reuse; it never claims rollback.

The wizard first prints the secret-free member-1 peer handoff. If the peer is
not ready, it exits successfully without writing a candidate; a separately
reserved passive IP remains allocated and is reused on the next run. After the
peer supplies its new public and APIPA endpoints, the wizard validates and
publishes the complete candidate with mode `0600`. New files use atomic
no-clobber publication. Replacing an expected candidate uses recoverable
conditional publication: a racing writer wins without being overwritten, and
an interruption can leave a clearly named private recovery directory for
manual review. Comments are canonicalized only in the new candidate; the
source stays byte-for-byte unchanged.

Preview the exact current-state plan without changing lifecycle, cloud, route,
or host state:

```bash
nebius-vpngw validate-config my-vpn.vm-ha.config.yaml
nebius-vpngw apply --local-config-file my-vpn.vm-ha.config.yaml --dry-run
```

Run the migration interactively, or copy the exact migration digest printed by
the preview into the noninteractive approval:

```bash
nebius-vpngw apply --local-config-file my-vpn.vm-ha.config.yaml
nebius-vpngw apply --local-config-file my-vpn.vm-ha.config.yaml \
  --approve-vm-ha-migration <MIGRATION_DIGEST>
```

Apply stages the passive before the retained active, installs and verifies the
current agent package on both members in that same order before the first lock
can invoke its private admission checks, then locks and activates both behind
the cold-start guard. It accepts the rollout only after exact pinned status
proves the expected generation, manifest digests, shared alias owner,
route-runtime receipt, and unlocked passive non-forwarding state. Durable
`provisioning` is written and reread before the first cloud effect,
`activating` is retained through both unlock proofs, and `active` is written
last. An unchanged interrupted apply resumes the same checkpointed operation.
When the checkpoint is already `activating`, apply validates two stable cloud
observations and the exact persisted member, shared-alias, route-target, and
runtime identities, then resumes host activation directly; it does not call the
VM provisioning path or repeat its final provisioning transition.
Each VM-HA cloud effect is bound to an exhaustive path-level observation guard,
and an accepted SDK operation is persisted before its bounded wait so restart
resumes the same operation rather than submitting a new identity. The accepted
receipt clears only after explicit terminal success; terminal failure or an
unavailable success status remains durable and blocks. When the current Nebius
operation API reports the exact typed `UNIMPLEMENTED` code for lookup, the
adapter resubmits only the same idempotent request, requires the same operation
ID, and still proves the exact resource postcondition. Deterministic allocation
creates recover an exact typed `ALREADY_EXISTS` result only through an
exact-name reread and complete resource-shape validation. Legacy v2 and v3
lifecycle records remain readable without rewrite; the next approved,
quiescent mutation advances to the guarded v4 record.

Each VM-HA configuration has a local, Git-ignored
`<config>.vm-ha-lifecycle.json` sidecar. This mode-`0600`, secret-free
transaction journal binds the deployment to its exact cloud resources.
`apply`, `status`, recovery operations, and `destroy` use it to verify resource
ownership and resume interrupted changes safely. Do not manually edit or
delete it while the VM-HA deployment exists.

Controller and rearm checkpoints keep their complete internal operation IDs.
At the Nebius SDK boundary, any operation ID containing provider-disallowed
characters is deterministically encoded as a lowercase SHA-256 idempotency key;
already valid keys remain unchanged. Retries therefore retain one accepted
provider identity without weakening the controller's durable replay identity.

Owner-gated route replacement uses a private v2 pending-mutation journal with
the exact removed route metadata and next hop plus the accepted delete, create,
or restore operation identity. A restart resumes that operation instead of
submitting another request. The legacy v1 intent remains readable without an
implicit rewrite; if it cannot prove either the original or desired route, the
controller blocks. A terminal replacement-create failure restores and
re-observes the exact original route before exposing the compensated failure.

During ordinary-to-HA conversion, the retained active is rebound to its exact
Compute identity and authenticated product host-key provisioning record. Apply
does not import a general `~/.ssh/known_hosts` address entry for that member,
because a recycled public address can retain an unrelated stale pin. After
shared-alias attachment and passive creation, apply discovers both exact
members again and rebuilds the immutable SSH policy before the first staging
connection, so product-owned Compute revision changes do not invalidate the
pre-mutation evidence closure.

An interrupted `provisioning` checkpoint does not by itself classify the
passive as failed. The passive-replacement approval appears only after the
current migration cycle records a completed bootstrap timeout in which the
active is ready and only the passive remains unready. During route cutover, an
exact approval-bound ordinary predecessor may remain unlabeled while it still
uses the old ordinary allocation; the controller verifies its replacement
through the shared allocation first, then applies the complete HA authority
labels to the successor. The mutation uses the ledger reread after that
same-cycle adoption, so clean conversion does not require an earlier failed
controller attempt to seed local authority.

Status polling retries only a well-formed expected node that is still
converging on the requested generation, apply lock, or data-plane state.
Malformed or foreign node, cluster, runtime-binding, or lock-operation status
fails immediately. If the final `active` lifecycle write reports an error,
apply re-reads the exact record: an exact active successor is accepted only
after both node states are verified again; an exact activating predecessor is
relocked passive-first and then active on the original operation. The passive
must remain non-forwarding; the exact owner may continue forwarding only with
its current owner and route receipt independently verified. Any other state is
reported as unsafe and requires inspection.

If an interrupted deployment already has two VMs but no lifecycle record,
ordinary migration approval cannot adopt them. Preview the topology and use
only the separately shown recovery digest:

```bash
nebius-vpngw apply --local-config-file my-vpn.config.yaml --dry-run
nebius-vpngw apply --local-config-file my-vpn.config.yaml \
  --recover-vm-ha-migration <RECOVERY_DIGEST>
```

Migration and recovery digests bind exact desired and observed cloud/route
identity, are not interchangeable, and become stale when either side changes.
A same-name or foreign allocation, ambiguous route outcome, identity drift, or
destructive retained-active change remains blocked for operator resolution.

Without `--sa`, apply uses `NEBIUS_IAM_TOKEN` only when you supplied it
explicitly. Otherwise one SDK-native bearer obtains a short-lived token through
the current Nebius CLI profile's supported `iam get-access-token` command. The
child CLI is bounded, non-interactive, and cannot consume an ambient IAM token;
the bearer shares the token across one manager SDK and forces at most one
refresh after Nebius rejects a request as unauthenticated. CLI profile
configuration supplies endpoint context only and is not used as the SDK
credential resolver. No acquired token is exported into the process
environment. An explicit token remains authoritative and never falls back to
the CLI. If Nebius rejects either source during serialized VM-HA apply, the
command exits fail-closed with redacted guidance to refresh the profile or
replace the token.

For ordinary gateways only, an explicit `apply --sa NAME` selects one exact
dedicated identity. Apply uses current Nebius SDK resource APIs to ensure a
same-name custom group whose only member is that service account and whose only
access permit is project-scoped `editor`, then obtains a short-lived token
through supported Nebius CLI service-account impersonation. VM-HA rejects
`--sa`: its runtime identity is deterministic and product-managed.

For VM-HA, `create-config` and the conversion wizard only write YAML. They do
not ask for credential paths or create local files, IAM resources, or keys. A
wizard summary shows the future operator credential location as
`~/.config/nebius-vpngw/credentials/<project>/<gateway>/nebius-credentials.json`;
the `~` is display shorthand and `apply` resolves it through the operator's
home directory.

The exact VM-HA apply plan declares create, reuse, or crash-resume intent and
binds the action and observed identity for the deterministic `<gateway>-vm-ha`
service account, same-name group, sole membership, project-scoped `editor`
permit, and `<gateway>-vm-ha-runtime-key` authorized key. Reuse is strictly
read-only; it cannot fill in a missing grant. After approval, apply uses the
generated Nebius SDK clients to execute only planned creates, waits for every
long-running operation to finish, and rereads the exact labeled graph. The one
authorized key is non-expiring RSA-4096/RS256. Foreign ownership labels,
members, permits, keys, or resource data fail closed and are never normalized
by deletion. A crash-safe private pending key and secret-free enrollment
journal allow an exact retry without silently replacing the cloud key; a
validated final credential also allows cleanup to resume after either residue
file was already removed. Nebius may project an omitted optional expiry as a
present zero-valued protobuf timestamp; apply treats only that exact sentinel
as omitted and still rejects every nonzero expiry.

The resulting operator file and all managed subdirectories are owner-only;
the file is mode `0600`. Apply authenticates that one source with a bounded
forced-renewal `whoami()` and installs separate root-owned copies for member 0
and member 1 at generation-, node-, and digest-bound paths. Runtime and
lifecycle records contain only non-secret account, key, path, and digest
identity. On every controller boot, the gateway revalidates its installed copy
and proves `whoami()` before cloud observation or forwarding can begin.
Missing or changed operator credentials for an active lifecycle block apply;
there is no implicit key rotation or account rebinding. `destroy` deliberately
retains both the operator credential and managed IAM identity. Ordinary SSH
trust is read only while the ordinary-to-HA transaction is provisioning or
activating; once the lifecycle is `ACTIVE`, later applies use the published
VM-HA receipt and no longer depend on the ordinary predecessor receipt.

To return a managed cluster to ordinary mode, remove or disable the explicit
`gateway_group.vm_ha` block and run `apply` again. The resulting ordinary apply
may use `--sa`; without it, apply uses the operator credential. It revalidates
and deactivates both former members, records a terminal removal tombstone, and
only afterward continues ordinary provisioning. The retained VM-HA IAM and
operator credential state is not deleted. An ordinary configuration with no
valid HA lifecycle record performs no HA discovery or teardown.

## Features

- **IPsec:** IKEv2 (default) + IKEv1 fallback, PSK auth, modern crypto (AES-256, SHA-256/384/512)
- **Routing:** BGP (FRR, preferred) or static routes
- **Idempotent:** Declarative YAML config, no manual state management
- **Peer support:** GCP HA VPN, AWS Site-to-Site, Azure VPN Gateway, Cisco IOS
- **Validation:** Strict Pydantic schema catches typos and invalid values
- **HA options:** Tunnel-level active/passive on one VM, plus explicit and
  default-disabled two-node VM-level active/passive HA
- **Gateway groups:** Multiple independent gateway VMs by default; adding an
  explicit `gateway_group.vm_ha` block creates one coordinated two-node cluster
- **Split-brain safety:** VM promotion requires an authoritatively stopped former
  Compute owner and exact shared-secondary-alias confirmation on the candidate before
  forwarding or route reconciliation

## Installation (Detailed)

### End users (pipx + GitHub release wheel)

- Requirements: Python 3.10–3.12 (e.g., `brew install python@3.12` on macOS, `sudo apt-get install python3.12 python3.12-venv` on Ubuntu).
- Install pipx (preferred via package manager to avoid PEP 668 errors):
  - macOS (Homebrew): `brew install pipx && pipx ensurepath`
  - Ubuntu/Debian: `sudo apt-get install pipx && pipx ensurepath`
  - If your distro has no pipx package: `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
  - If pip blocks with "externally managed environment" (PEP 668), rerun with `--break-system-packages` only if you accept the risk:
    `python3 -m pip install --user pipx --break-system-packages && python3 -m pipx ensurepath`
- Use the latest `nebius_vpngw-<version>-py3-none-any.whl` from this repository’s GitHub
  Release assets (version comes from the Git tag).
- Run install commands from a clean shell, not inside an activated Python virtual environment.
- Recommended: install directly from the release URL:

  ```bash
  pipx install <release-wheel-url>
  ```

  `apply` can reuse that original URL later to deploy the VM agent.

- To replace an existing install with a clean `pipx` venv:

  ```bash
  pipx uninstall nebius-vpngw
  pipx install <release-wheel-url>
  ```

- Alternative: download the wheel first and install from the local file:

  ```bash
  pipx install ./nebius_vpngw-<version>-py3-none-any.whl
  ```

- If pipx reports that its bin dir is not on PATH (e.g., `~/.local/bin`), run:

```bash
pipx ensurepath
# then restart your shell, or:
exec $SHELL
```

- Verify:

  ```bash
  which nebius-vpngw
  pipx list
  nebius-vpngw --version
  ```

### Developers (editable install)

- Create a virtual environment (Python 3.10–3.12) and activate it:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

- Install in editable mode with developer tools:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

- Confirm the CLI is reachable: `nebius-vpngw --help`. Developer extras include linting, tests, PyInstaller, and build tooling.

## Architecture

**Components:**

- **Orchestrator CLI:** Runs locally, manages VM lifecycle and config deployment
- **Gateway VM(s):** Ubuntu LTS with strongSwan (IPsec), FRR (BGP), agent daemon
- **Agent:** On-VM service that renders and applies configs idempotently

**Deployment modes:**

- Single VM: Multiple tunnels, VM is single point of failure
- Gateway group without `vm_ha`: Multiple independent VMs with per-tunnel pinning
- Gateway group with explicit `vm_ha.enabled: true`: Exactly two stable members
  sharing one controller-owned private allocation

**Current HA Boundary:**

- Tunnel `ha_role` remains per connection and per VM; it is not VM ownership.
- VM-level HA is enabled only by the explicit `gateway_group.vm_ha` contract and
  supports exactly two members, one configured active and one configured passive.
- Omitting or disabling `vm_ha` preserves independent gateway-VM behavior and
  does not infer clustering from `instance_count`, tunnel roles, or public IPs.
- Active-active forwarding, ECMP, and clusters larger than two members remain
  unsupported.

### Single VM with tunnel-level HA

![Single-VM Nebius VPN Gateway with tunnel-level HA](images/nebius-vpngw-single-vm-tunnel-ha.svg)

Tunnel roles provide a preferred path and a hot standby path on the same
gateway VM. They protect against a tunnel failure, but both paths are lost if
the VM fails because VM-HA is still disabled.

### Explicit two-VM VM-level HA

![Two-VM Nebius VPN Gateway with VM-level and tunnel-level HA](images/nebius-vpngw-two-vm-ha.svg)

The four-tunnel BGP example keeps tunnel selection independent from VM
ownership: `nebius-vpngw0` owns tunnels 0 and 3, while `nebius-vpngw1` owns
tunnels 1 and 2. Only the authoritative shared-allocation owner forwards.
VM-HA remains opt-in and default-disabled; a static-only GCP Classic standby is
tunnel-cold rather than the warm-tunnel topology shown here.

### Two-node VM-level HA

Start from [`examples/vm-ha-bgp-example.yaml`](examples/vm-ha-bgp-example.yaml).
VM-HA member YAML contains only the stable node identity, instance index, and
initial role; it contains no credential, CA, certificate, or private-key path.
After exact-plan approval, `apply` creates or reuses one product-managed Nebius
credential source under the operator's configuration directory and installs a
separate immutable, root-owned mode-`0600` copy for each member. Each VM creates
its own managed mTLS private key locally and exchanges only its public leaf
certificate over pinned SSH. Runtime manifests and lifecycle records retain
only internal paths and secret-free identity digests; public status and logs do
not expose those values.

Before cloud or host mutation, apply authenticates the exact operator source
and verifies the deterministic service account, group, membership, project
permit, and authorized key. Each controller then verifies its installed bundle
and authenticated service-account identity before opening cloud, route, or
dataplane ports.

The HA service starts behind a forwarding and tunnel-initiation guard. A
candidate can promote only after fresh cloud reads prove the former Compute
owner is `Stopped`, the former attachment is absent, and the shared allocation
is attached exactly to the candidate as a secondary alias. Route reconciliation
remains owner-only.
Ambiguous cloud state, credential or policy drift, stale peer state, and partial
transitions fail closed.

When the exact active owner sends a fresh but unhealthy heartbeat, the
controller grants that owner one persisted five-second local repair attempt.
FRR, StrongSwan, or the gateway agent is repaired according to the failed
readiness category; every command is bounded, and the final second is reserved
for directly disabling and verifying IP forwarding. Two fresh healthy
observations cancel passive suspicion, but the consumed attempt is reset only
after sixty seconds of continuous health or a new ownership incarnation.

A missing heartbeat receives no repair grace. One unavailable BGP neighbor is
reported as a degraded redundant path only when every required prefix still
has learned-route and usable-XFRM coverage; loss of a sole required path is a
full outage. The tunnel health monitor is observer-only in VM-HA mode so it
cannot race the controller's single repair attempt. Repair never moves the
shared allocation or changes VPC routes. If recovery does not converge, the
passive still follows the strict Compute-stop, detach, attach, ownership-reread,
route-receipt, and forwarding sequence. `status` includes the current repair
phase, safe readiness reasons, redundancy state, and observed phase timings.
Existing non-HA monitoring behavior is unchanged.

```bash
nebius-vpngw apply --local-config-file vm-ha.config.yaml
nebius-vpngw status --local-config-file vm-ha.config.yaml
nebius-vpngw vm-ha --rotate-mtls --local-config-file vm-ha.config.yaml --dry-run
nebius-vpngw vm-ha --local-config-file vm-ha.config.yaml
nebius-vpngw failover vm --local-config-file vm-ha.config.yaml
nebius-vpngw failback vm --local-config-file vm-ha.config.yaml
```

Initial `apply` and exact member replacement manage mTLS automatically. An
unchanged healthy apply does not rotate keys. To rotate both identities
explicitly, review the dry-run's secret-free plan digest, then rerun with
`--approve PLAN_DIGEST` (or confirm interactively). The transaction inhibits
failover and rearm, expands old/new trust, switches the passive before the
owner, verifies three fresh authenticated rounds, prunes the old identities,
and resumes idempotently after interruption. Certificates use a fixed
year-9999 validity sentinel; there is no scheduled renewal or rotation.

`vm-ha --rotate-mtls` starts with a concise availability note that also says
failover and rearm are paused until completion, then renders a compact human
plan plus its exact digest instead of raw preview or result JSON. During
execution, an interactive terminal shows an animated rotation spinner and then
one terminal result row. The rotation inhibition is separate from the apply lock: it blocks
failover and rearm while leaving a healthy current owner forwarding and the
other member passive. Independent ownership or fencing failures remain
authoritative and can still stop unsafe forwarding. Before changing either
identity, the command proves passive-then-owner controller quiescence; if that
proof drifts, it releases its exact pre-prepare inhibition so transfer and
rearm recovery are not stranded. Before it renders an executable plan or asks
for approval, both installed agents and their running controller processes must
advertise that exact rotation-quiescence contract. A missing, stale, or
restart-skewed capability stops without installing inhibition and directs the
operator to run `apply` with the same configuration and CLI version before
retrying.

For an explicit VM-HA configuration, ordinary `status` includes one concise
`VM-HA Status — <OVERALL>` table with `Gateway`, `Role`, `mTLS`, and `Ready`
columns and one row for each configured gateway. The authoritative current
owner is `active`, the non-owner is `standby`, and both roles are `unknown`
without proven ownership; configured active/passive preference is intentionally
omitted. Healthy aggregate, mTLS, and readiness values are green; planned
maintenance and in-progress transitions are yellow, while blocked or
unavailable evidence is red and retains literal status text. Missing or
conflicting evidence is shown conservatively without exposing cloud resource identities, controller details,
certificate metadata, internal paths, or recovery actions. Tunnel PSK environment references may
remain unresolved for this read-only command because status does not consume
those secrets;
project, topology, credential-path, and other operational placeholders still
must resolve. Status uses the management username and private key from
`gateway_group.vm_spec` for every SSH probe. In explicit VM HA it also requires
an exact immutable pin for each member from the per-deployment managed receipt
or the optional explicit override; one missing pin is reported only on that
member as `ssh-trust-unavailable`, and status never creates, imports, repairs,
enrolls, or bypasses trust. Cloud authority reports route-target,
managed-record, prefix-set, and shared-allocation next-hop drift separately so
the owning apply repair is identifiable without exposing resource IDs. Exact
product authority labels scope records to this cluster, so a complete
foreign-cluster route is ignored while partial or current-cluster drift still
blocks. The agent projects the live writer inhibition and forwarding guard over
its last successful snapshot, preventing a post-unlock controller failure from
being reported as a lock that still exists.
The same section ends with an identity-free `Redundancy`, `Identity`,
`Auto-healing`, and `Action` summary. Auto-healing reports `enabled` or
`disabled` only from committed policy agreement and preserves `transitioning`,
`blocked`, or `unknown` when evidence is incomplete. A disabled,
peer-acknowledged standby-restoration policy is reported as yellow
`MAINTENANCE`, including after the passive VM has been stopped. The summary's
`maintenance` and `disabled` values are red so reduced redundancy is
conspicuous, while verified identity and explanatory details remain neutral.
Action prints the exact shell-quoted command for the current config, for example:

```console
nebius-vpngw vm-ha --local-config-file gateway.config.yaml --standby-auto-healing enabled
```

Cloud identities, operation records, raw
exceptions, and paths other than that already-supplied local config remain
hidden. Other config-bearing actions replace the complete shell-parsed path
argument with `<file>`, including paths containing spaces or apostrophes.

The primary tunnel table uses the mode-neutral `Uptime` heading. BGP gateways
show BGP-session uptime when that peer evidence is available and otherwise
fall back to IPsec SA uptime; Static gateways show IPsec SA uptime. A failed
probe renders `-` in this column and puts its safe diagnostic outside the
table. If later exact VM-HA evidence in the same invocation proves that member
ready, status reruns only the identical failed tunnel or service probe once;
the tunnel retry must include recognizable established-SA evidence and the
service retry must report active before its stale error is replaced. Empty,
no-SA, connecting-only, or malformed tunnel output remains visible as the
original error rather than receiving a recovery note.

After every committed promotion, an independent start-only rearm service on the
exact current owner automatically restores the stopped former owner as an
alias-free warm standby. It requires the terminal promotion receipt and exact
ownership revision. For a promotion admitted under committed enabled standby
auto-healing, the controller binds the last fresh two-member policy agreement
to that exact transfer before its first effect, so rearm does not require the
deliberately stopped peer's heartbeat to remain newer than 30 seconds. It
resumes only its own idempotent Compute start, and has no
authority to stop Compute, move the allocation, change VPC routes or firewall
state, or enable forwarding. `vm-ha` is the role-neutral explicit retry:
it targets whichever exact member is currently the non-owner through that
owner-side reconciler and returns only after fresh current-boot
`standby_ready` evidence less than 10 seconds old is available. Automatic rearm
never changes ownership. Retryable or ambiguous provider failures retain one
logical operation identity for five total submissions separated by 5, 15, 30,
and 60 seconds; a permanent failure or exhausted retry budget closes the
authorization as blocked. `vm-ha` can reopen only that same exact blocked
receipt under the unchanged committed-enabled policy. A crash after cloud
acceptance is recovered only by exact terminal operation lookup. Apply,
removal, retry submission, and rearm share one writer lock, so maintenance
cannot race the final pre-start inhibition check. Deactivation keeps that
root-only lock file and directory in place, removing only sibling HA state, so
a concurrent writer can never acquire a replacement inode while cleanup still
holds the original lock. Rearm is intentionally not a general repair command:
it cannot enroll SSH trust, deploy a stale generation, clean route hygiene on
an already-running member, reconcile cloud routes, move the shared allocation,
change firewall state, or enable forwarding. Those failures must be repaired
through their owning setup or supported apply workflow before rearm can verify
fresh standby readiness.

Removing VM HA uses a two-member barrier: the same lifecycle operation first
inhibits both reconcilers, proves both controllers acknowledge the gate with no
pending cloud effect, and stops both rearm and safety-controller services on
every member before either member is deactivated. That completed barrier is
checkpointed, so a crash after one deactivation resumes with idempotent
teardown instead of trying to invoke the removed agent again.

Repeating `failover vm` or `failback vm` when its requested configured
role is already the exact healthy owner is a successful request-free no-op.
Default text output says `Failover not needed: the passive VM already owns the
gateway.` or `Failback not needed: the active VM already owns the gateway.`
Explicit `--output-format json` instead returns the existing
`nebius-vpngw/vm-ha-planned-transfer-result-v1` record with
`outcome: already-owner`. Neither path disables forwarding or writes a transfer
request. Ambiguous or unhealthy same-owner evidence fails without mutation.
If the same planned command is repeated after its first transfer effect, it
reattaches only when the unchanged exact request still predates and matches the
same-direction durable lineage. The command reuses that request's original
progress identity and publishes nothing new. A missing or later request, an
opposite or automatic transfer, or invalid, stale, or foreign lineage evidence
still fails closed; the durable lineage is never deleted by the request path.
Request publication and transfer-effect dispatch share the VM-HA writer lock;
dispatch rereads the current typed intent under that lock and waits for a fresh
controller observation if a request arrived after the previous decision.
Returning ownership to the configured active remains an explicit failback.
`failover vm` requests a planned move to the configured-passive VM without
changing the tunnel-level `failover tunnel` behavior. Planned failover and
failback share one preparation path: if the role-bound target is stopped, the
current owner requests rearm; if it is starting, the CLI observes it; and once
running, the CLI waits for fresh `standby_ready` evidence and immediately
reproves ownership before submitting the unchanged role- and generation-bound
request. One wall-clock deadline bounds Compute observation, pinned-SSH probes
and sleeps, and every repeated readiness request. The CLI never starts or
stops Compute, moves the allocation, reconciles routes, or enables forwarding.
Every readiness sample is strictly rebound to the expected cluster, member,
role, generation, configuration digests, allocation, and route runtime before
it can authorize the existing role-bound request. A passive standby
intentionally has no active-owner `controller_ready_boot_id`; its fresh fenced
guard and `standby_ready` record are the authoritative current-boot evidence.
Likewise, restoring forwarding on an already-authoritative current owner after
apply or restart is local reconciliation, not a new ownership transfer, and
does not create transfer lineage.

For a real transfer, the default `--output-format text` suppresses raw request
JSON. Existing authentication progress is unchanged. When the target agent
exposes exact request- and lineage-bound progress, each new phase is written to
stderr immediately and the current phase repeats approximately every five
seconds. The elapsed values vary:

```text
Failing over to the passive VM...
Failover in progress: 0.8s elapsed, stopping current owner...
Failover in progress: 38.4s elapsed, unassigning shared IP...
Failover in progress: 58.7s elapsed, assigning shared IP to the target VM...
Failover in progress: 72.1s elapsed, confirming shared IP ownership...
Failover in progress: 74.0s elapsed, establishing VPN...
Failover in progress: 89.3s elapsed, reconciling routes...
Failover in progress: 91.0s elapsed, enabling forwarding...
Failover cutover completed in 92.2s; restoring standby redundancy...
Failover in progress: 93.2s elapsed, starting former owner as standby...
Failover in progress: 98.2s elapsed, waiting for standby readiness...
Failover to the passive VM is done successfully in 130.4s.

Failing back to the active VM...
Failback in progress: 0.9s elapsed, stopping current owner...
Failback in progress: 37.5s elapsed, unassigning shared IP...
Failback in progress: 57.8s elapsed, assigning shared IP to the target VM...
Failback in progress: 71.0s elapsed, confirming shared IP ownership...
Failback in progress: 72.8s elapsed, establishing VPN...
Failback in progress: 87.9s elapsed, reconciling routes...
Failback in progress: 89.6s elapsed, enabling forwarding...
Failback cutover completed in 90.8s; restoring standby redundancy...
Failback in progress: 91.8s elapsed, starting former owner as standby...
Failback in progress: 96.8s elapsed, waiting for standby readiness...
Failback to the active VM is done successfully in 128.6s.
```

Older, mixed-version, missing, or invalid agent progress safely falls back to
the existing `cutting over...` and `restoring standby...` messages. Progress is
presentation-only: it cannot authorize or complete ownership, routes,
forwarding, cutover, or redundancy restoration.

If the exact current transfer reports that a controller effect failed while
the same durable operation remains pending, the command stays attached within
its normal cutover deadline. Forwarding remains fenced while the controller
retries that operation, and text output names the affected phase. The CLI does
not submit a second effect, restart the controller, or invoke rearm.

Missing, foreign, malformed, or pending-operation-mismatched retry evidence is
not safe to follow. The command exits nonzero and directs the operator to use
the same SSH trust configuration for status, then inspect the controller log
on the target VM:

```bash
nebius-vpngw status --local-config-file <file>
sudo journalctl -u nebius-vpngw-vm-ha.service
```

The command keeps its authenticated Nebius client open through both terminal
cutover and restored-standby verification. Those independent cloud rereads do
not reuse a client whose preparation lifetime has already ended.

A pre-cutover controller failure remains controller-owned: `vm-ha` observes and
reports that transition without submitting a standby-start retry. After a
committed cutover, the same command may invoke the sole-start writer to restore
the exact non-owner as standby.

Preparation, request-to-cutover observation, and post-cutover standby
restoration use independent fixed 300-, 600-, and 300-second budgets. No
progress transition resets or extends those deadlines, and time spent preparing
or cutting over therefore cannot consume the restoration budget, while every
displayed elapsed value still measures total command time. Restoration phases
come from current rearm and Compute evidence even when a mixed-version member
cannot provide detailed cutover progress. If restoration fails after a safe
cutover, the error reports cutover, restoration, and total elapsed time
separately before directing the operator to `vm-ha`.
If only the CLI's restoration observation deadline expires while the durable
rearm state remains in progress, the command exits nonzero and says background
restoration continues. `vm-ha` is needed only if later status reports a blocked
recovery.

Terminal SSH, agent-status, and cloud reads are observers, not transfer
writers. A transient read failure is retried within the current phase's
existing deadline without republishing the request, extending the deadline, or
invoking a controller or rearm effect. If those reads remain unavailable, the
command exits nonzero and reports the cutover outcome or standby restoration as
"not yet verified" rather than claiming the VM-HA operation itself failed.
Likewise, a terminal-looking read followed by the controller's exact
current-request ownership reproof remains attached only through the closed
self-fence, passive re-entry, detach/attach, ownership confirmation, route, and
forwarding sequence inside the same fixed cutover budget. Missing or foreign
lineage, an unexpected action or blocked reason, an apply lock, or unexplained
well-formed drift still fails immediately. If exact reproof remains in progress
at the deadline, the command reports that cutover is not yet verified instead
of claiming that the controller operation failed.
Run `status` with the same local config before retrying the transfer; use
`vm-ha` only when status reports a blocked recovery. Permanent, malformed, or
contradictory terminal evidence still fails immediately.

Automation that consumes the former sorted request or no-op JSON can opt in
explicitly; human start, progress, success, and failure messages remain on
stderr:

```bash
nebius-vpngw failover vm --local-config-file vm-ha.config.yaml --output-format json
nebius-vpngw failback vm --local-config-file vm-ha.config.yaml --output-format json
```

#### Optional traffic-timing observation

The standalone `misc/vm_ha_one_way_probe.py` helper can measure one direction
of test traffic while the ordinary transfer command runs separately. It is a
diagnostic observer only: the gateway never reads its output, and one direction
cannot satisfy bidirectional failover acceptance.

Before a trial, synchronize the operator and observer-host clocks to the same
time source and record their offset and uncertainty. If that uncertainty is
unbounded or large enough to change phase attribution, discard the trial. Keep
probe JSONL and command stderr in a private untracked location; they can contain
operational timing even though the helper removes endpoint values from JSONL.

1. Start the helper first with a literal observer address, literal destination,
   explicit pinned known-hosts file, and an explicit current-user-owned private
   key with no group or other permissions. Confirm it remains running.
2. In a separate terminal, invoke the exact existing `failover vm` or
   `failback vm` command and preserve its phase output.
3. After the helper exits successfully, correlate timestamps offline. Any SSH,
   ping, summary, send, clock, or out-of-band recovery error invalidates the
   traffic trial instead of counting as product packet loss.
4. Independently verify Compute fencing, exclusive shared-IP ownership,
   current route receipt before forwarding, owner-only VPN state, and restored
   two-VM redundancy. Repeat from the opposite traffic direction for
   bidirectional acceptance.

See [`misc/README.md`](misc/README.md#vm-ha-one-way-traffic-observation) for the
endpoint-placeholder invocation. The helper never starts the transfer and
never authorizes cutover or success.

The cutover milestone is emitted only after the current-generation promotion
receipt proves the former owner was stopped and alias-free before exclusive
allocation transfer, route reconciliation, and forwarding. The terminal
success line additionally requires both Computes `Running`, terminal owner-side
rearm with ready redundancy, a fresh alias-free guarded standby, and stable final
cloud and agent rereads. A cutover failure exits nonzero without either success
line. A post-cutover rearm failure exits nonzero, explicitly says cutover
succeeded but standby restoration failed, and distinguishes an observation
timeout with continuing background recovery from a closed failure that directs
the operator to `vm-ha`. Persistent terminal-observer loss instead reports the
affected milestone as not yet verified, never emits a late success line, and
directs the operator to `status`. The already-owner no-op requires an exact
healthy owner/standby pair and follows the selected text or JSON output format.

Before `vm-ha` can report healthy or a planned VM transfer can publish its
request, each current controller must advertise
`vm-ha-standby-restoration-v2`. The configuration-independent installed-agent
capability document advertises the same feature for standby-policy recovery.
Missing capability is treated as apply-owned artifact drift even when package
versions and configuration generations match: deploy the current artifact with
the supported `apply` workflow, verify both controllers, and only then retry the
transfer. VM-HA package preparation requires a current build or explicit local
wheel; it never falls back to the wheel recorded by the existing installation.
The uploader binds the local SHA-256, uses an isolated remote staging directory,
verifies the remote bytes before installation, and accepts the installation only
after a fresh capability probe advertises `vm-ha-standby-restoration-v2`.

The latest two-member agreement is admission evidence only. Once a planned
failover or failback is armed, later healthy heartbeats may refresh that cache
without revoking the durable transfer authorization. Post-cutover rearm uses
the exact authorization, terminal promotion receipt, and current policy
transaction; it never requires the mutable latest certificate to remain byte-
identical. Planned transfers accept only their original committed-enabled
policy decision, while the exact prepared-disable predecessor exception is
reserved for automatic failover.

`failback vm` requests the normal fenced ownership-transfer path; it does not
rewrite configured roles or force forwarding. The controller still owns every
stop, detach, attach, authoritative ownership reread, route, and forwarding
effect for planned and automatic transfer. Automatic takeover remains directed
by current ownership rather than configured preference: either exact non-owner
may take over after the current owner fails and all unchanged suspicion,
readiness, fencing, and ownership gates pass. A healthy owner is never moved
merely to restore configured-role preference; that remains an explicit
`failback vm` operation.
The last accepted mTLS heartbeat is stored behind its anti-replay boundary, so
a survivor controller restart retains exact generation/digest parity evidence
when the owner is already unavailable. The reloaded record is deliberately
stale: it cannot count as peer liveness, health, readiness, or a reason to
cancel takeover. Its peer boot and sequence must exactly match the durable
replay high-water mark; if a crash advances replay state before the matching
heartbeat is persisted, restart discards the older cache and blocks transfer
until fresh exact parity arrives.
`status` adds a resource-identity-free redundancy summary with standby readiness
and the closed rearm phase/failure plus a supported action. Internal preparation,
detection or repair, common-cutover, and redundancy-restoration durations remain
available to the controller without exposing raw identity records. The clean-slate
heartbeat-v2 envelope binds the managed-mTLS epoch and fingerprint actually
authenticated on each fresh connection; mixed or older wire versions fail
closed. Its `promotion_ready` flag also represents a fresh exact alias-free
passive standby. The earlier
implementation has completed an authorized
non-production trial with independently observed cloud, route, component-log,
and bidirectional workload-traffic evidence; that evidence does not claim
production validation for this warm-standby refactor or a different
environment.

### Provider-neutral VM-HA peer credential rotation

`apply --prepare-vm-ha-peer-rotation` is the appliance-side checkpoint for a
PSK change on any compatible IPsec peer represented by the existing `gcp`,
`aws`, `azure`, `cisco`, or `generic` vendor values. The core does not branch on
the vendor label and supports static, BGP, and schema-valid mixed VM-HA input.
Compatibility still requires matching IKE/IPsec identities, proposals,
traffic selectors, and routing configuration; the flag does not make an unsafe
peer topology safe.

Use one explicit three-phase workflow:

1. Put the intended peer credentials in the private VM-HA config and run the
   preparation command below. It stages the normal exact generation, locks and
   passively fences both members, and returns before either member can become
   the forwarding owner.
2. Update the peer with its own supported management workflow. This is an
   operator-owned boundary: the appliance receives no AWS, Azure, GCP, Cisco,
   or other peer-management credentials and does not claim a universal peer
   mutation API.
3. Run ordinary `apply` with the same private config. That is the sole path
   that re-establishes the exact locks, releases the cloud-selected owner first,
   verifies mode-appropriate tunnel/BGP/static-route readiness and the exact
   route receipt, enables forwarding, and releases the passive member last.

```bash
nebius-vpngw apply \
  --local-config-file <private-vm-ha.config.yaml> \
  --prepare-vm-ha-peer-rotation

# Update the external peer through its own reviewed workflow.

nebius-vpngw apply \
  --local-config-file <private-vm-ha.config.yaml>
```

Peer products expose different change boundaries, so plan the outage and
rollback from that peer's current documentation:

| Peer family | Documented credential-change shape | Appliance implication |
| --- | --- | --- |
| [AWS Site-to-Site VPN](https://docs.aws.amazon.com/vpn/latest/s2svpn/modify-vpn-tunnel-options.html) | Modify one tunnel's options at a time; AWS warns that the tunnel is interrupted for up to several minutes. | Keep Nebius fenced while the selected AWS tunnels are updated, then run ordinary apply. |
| [Azure VPN Gateway](https://learn.microsoft.com/en-us/rest/api/network-gateway/virtual-network-gateway-connections/set-shared-key?view=rest-network-gateway-2025-05-01) | Set the shared key on the gateway connection. | Treat the connection update as the peer-owned middle phase. |
| [Google Cloud VPN](https://docs.cloud.google.com/compute/docs/reference/rest/v1/vpnTunnels) | The tunnel API exposes insert/delete/get/list/labels, but no tunnel update method. | A PSK change can require tunnel recreation; use a topology-specific reviewed adapter such as the Classic helper below. |
| [strongSwan `swanctl`](https://docs.strongswan.org/docs/latest/swanctl/swanctlLoadCreds.html) or another appliance | Reload credentials or use that appliance's supported configuration transaction. | Do not assume cloud-style replacement semantics; verify the peer reloaded the intended secret before ordinary apply. |

The checkpoint intentionally remains VM-HA-only. An ordinary single-VM gateway
does not have the dual-member exact-lock/passive-fencing invariant, so its peer
maintenance must use a separately planned outage and the normal apply path.

GCP VM HA has two isolated fixture modes:

- `misc/gcp-vpngw.sh --vm-ha-peer` builds the BGP-only four-tunnel HA VPN and
  Cloud Router fixture. Both Nebius members keep warm tunnels while BGP follows
  the current owner.
- `misc/gcp-vpngw.sh --classic-vm-ha-peer` builds two one-to-one Classic VPN
  gateways and tunnels plus explicit static routes. The static passive member
  is Compute-warm but tunnel-cold: it has no established IKE SA or usable XFRM
  path. Only after the standard former-owner `Stopped` and shared-allocation
  ownership gates does the controller prepare the candidate tunnel with
  forwarding still disabled, then reconcile routes and enable forwarding. The
  helper requires Premium-tier external addresses and Premium-tier `EXTERNAL`
  forwarding rules before it creates any missing resource. For an explicitly
  requested credential rotation, the helper can read the exact two tunnel PSKs
  from an ignored regular mode-`0600` VPNGW YAML through
  `--psk-source-config` and recreate only the planned routes and tunnels through
  `--rotate-existing-tunnels`. Actual rotation requires that config to declare
  the exact enabled two-member GCP/static topology, preserves only a complete
  retained address/gateway/forwarding graph, and rechecks resource identity
  immediately after confirmation. Run `apply --prepare-vm-ha-peer-rotation` first
  to stage the replacement generation while both members remain locked and
  passive, then run ordinary apply after peer recreation to perform the only
  supported owner unlock, route reconciliation, and forwarding convergence.
  Ordinary apply remains create-only and non-deleting.

Use separate gateway groups, cluster identities, configs, peer resources, and
routes for these modes. A hybrid config remains schema-valid for existing
users, but it is not the supported GCP VM-HA topology because a re-established
Classic tunnel can make GCP select the non-forwarding standby. See
[`misc/README.md`](misc/README.md#explicit-two-member-vm-ha-peer-mode) and
[`misc/README.md`](misc/README.md#isolated-classic-static-vm-ha-peer-mode).

**Networking:**

- Dedicated gateway subnet for gateway isolation (default name: `vpngw-subnet`)
- One NIC per VM (platform constraint), future-ready for multi-NIC
- Public IP allocations preserved across VM recreation

For detailed architecture, see [design document](docs/design.md).

## Configuration

### File Structure

This is the commented compatibility template generated by:

```bash
nebius-vpngw create-config my-vpn.config.yaml --no-interactive
```

```yaml
# Nebius VPN Gateway config (schema v1)
# Notes:
# - Override order: tunnel > connection > defaults
# - gateway.local_prefixes is the source of truth
# - Use ${VAR} for secrets; keep *.config.yaml out of git
# - Set values directly in YAML OR via ${VAR} envs (do not mix for the same field)

version: 1

# Project context (required)
tenant_id: "${TENANT_ID}"
project_id: "${PROJECT_ID}"
region_id: "${REGION_ID}"  # e.g., eu-north1

gateway_group:
  name: "nebius-vpn-gw"
  instance_count: 1
  external_ips: []  # []=auto
  # Example (list per VM, inner list per NIC):
  # external_ips:
  #   - ["203.0.113.10"]  # VM0 NIC0
  #   - ["203.0.113.20"]  # VM1 NIC0
  # network_id: "vpcnetwork-abc123def456"
  subnet:
    name: "vpngw-subnet"
    cidr: null         # null=auto-carve from parent network private CIDRs; or set e.g. "172.16.30.0/24"
    prefix_length: 24  # used only when cidr is null; valid /28 through parent network CIDR

  vm_spec:
    platform: "cpu-d3"          # cpu-e2|cpu-d3
    preset: "4vcpu-16gb"
    disk_boot_image: "ubuntu24.04-driverless"
    disk_gb: 100
    disk_type: "network_ssd"
    disk_block_bytes: 4096
    num_nics: 1
    ssh_public_key_path: "~/.ssh/id_ed25519.pub"
    ssh_private_key_path: "~/.ssh/id_ed25519"

gateway:
  local_asn: 65010
  local_prefixes:
    - "10.0.0.0/16"
  ipsec_mode: "xfrm-interface"
  quotas:
    max_connections: 16
    max_tunnels: 32
    max_total_bandwidth_mbps: null

defaults:
  vpn_type: "ipsec"
  ike_version: 2
  allow_ikev1: false
  auth:
    method: "psk"

  crypto:
    ike_proposals:
      - "aes256gcm16-prfsha256-modp2048"
      - "aes256-sha256-modp2048"
    ike_lifetime_seconds: 28800
    esp_proposals:
      - "aes256gcm16-modp2048"
      - "aes256-sha256-modp2048"
    esp_lifetime_seconds: 3600
    dh_groups:
      - 14
      - 19
      - 20

  dpd:
    interval_seconds: 5
    timeout_seconds: 15  # timeout > interval

  health_monitoring:
    enabled: true
    check_interval_seconds: 10
    max_failures_before_restart: 2
    proactive_refresh_enabled: false
    proactive_refresh_hours: 8
    ping_enabled: false

  ha_mode: "active-passive"  # one active tunnel per connection per VM

  routing:
    mode: "bgp"  # bgp|static
    bgp:
      router_id: null
      hold_time_seconds: 6
      keepalive_seconds: 2
      graceful_restart: false
      max_prefixes: 1000
      bfd:
        enabled: false  # enable only if peer supports BFD
        transmit_interval_ms: 300
        receive_interval_ms: 300
        detect_multiplier: 3

connections:
  - name: "gcp-ha-vpn"
    vendor: "gcp"
    routing_mode: "bgp"
    # remote_prefixes: ["10.0.0.0/8"]  # optional allowlist for BGP
    bgp:
      enabled: true
      remote_asn: 64514
      advertise_local_prefixes: true
    tunnels:
      - name: "gcp-ha-tunnel-1"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "active"  # exactly one active per connection per VM
        remote_public_ip: "203.0.113.1"
        psk: "${GCP_TUNNEL_1_PSK}"
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: "gcp-ha-tunnel-2"
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: "passive"
        remote_public_ip: "203.0.113.2"
        psk: "${GCP_TUNNEL_2_PSK}"
        inner_cidr: "169.254.11.0/30"
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"

  # Static routing example (remote_prefixes required when routing_mode=static)
  # - name: "onprem-static"
  #   vendor: "cisco"
  #   routing_mode: "static"
  #   remote_prefixes:
  #     - "192.168.0.0/16"
  #   bgp:
  #     enabled: false
  #   tunnels:
  #     - name: "onprem-tunnel-1"
  #       gateway_instance_index: 0
  #       remote_public_ip: "203.0.113.5"
  #       psk: "${ONPREM_PSK}"
  #       inner_cidr: "169.254.30.0/30"
  #       inner_local_ip: "169.254.30.1"
  #       inner_remote_ip: "169.254.30.2"
```

### Remote Prefixes: Static vs BGP Mode

**The `remote_prefixes` field has different semantics depending on routing mode:**

**BGP Mode (default):**

- `remote_prefixes` is **optional**
- If omitted: BGP learns ALL routes advertised by peer dynamically
- If specified: Acts as an inbound **whitelist filter** - only listed prefixes are accepted from BGP
- The route you want must be advertised by the peer
- `remote_prefixes` only allowlists advertised routes
- It does not carve a smaller route out of a larger one
- Routes are learned dynamically by BGP on the gateway VM. Ordinary gateways
  use `nebius-vpngw add-routes-local` for VPC route-table entries; explicit
  VM-HA keeps VPC routes controller-owned. In BGP-only mode the command may
  repair exact authority-bound export drift; in static-only mode it only waits
  for the autonomous controller to converge the already-installed generation.
- Example: Peer advertises 300 networks → you don't need to list all 300 in YAML

```yaml
connections:
  - name: gcp-vpn
    vendor: gcp
    routing_mode: bgp
    # Optional: Whitelist specific prefixes (filter)
    # The peer must advertise these exact routes
    remote_prefixes:
      - "10.10.10.0/24"
      - "172.16.0.0/24"
    bgp:
      enabled: true
      remote_asn: 65001
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
```

Example: if the peer advertises `10.10.0.0/16` but you configure only `10.10.10.0/24`, the `/16` is rejected and the `/24` is only usable if the peer also advertises `10.10.10.0/24` explicitly.

**Static Mode:**

- `remote_prefixes` is **required** (or specified per-tunnel in `static_routes`)
- Used to install kernel routes via XFRM interfaces (rightsubnet stays 0.0.0.0/0)
- You must enumerate each remote network manually
- No dynamic route learning

```yaml
connections:
  - name: peer-vpn
    vendor: generic
    routing_mode: static
    remote_prefixes:      # Required: actual routes to install
      - "192.168.1.0/24"
      - "192.168.2.0/24"
    bgp:
      enabled: false
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.20.0/30"
        inner_local_ip: "169.254.20.1"
        inner_remote_ip: "169.254.20.2"
```

### Schema Validation

**Strict validation** enforces correctness before deployment:

- **Type safety:** IPs, CIDRs, ASNs, booleans validated
- **Constraints:** ASN validated (private 64512-65534 recommended; public/extended allowed), /30 subnets, APIPA 169.254.0.0/16
- **Consistency:** BGP mode requires `bgp.enabled: true` and `bgp.remote_asn`; inner IPs must be host addresses within inner_cidr
- **Unknown fields:** Rejects typos like `inner_ciddr` or `remote_ips`

**API versioning:**

- `version: 1` required in all configs
- Future schema changes increment version
- Backwards compatibility maintained

**Validation workflow:**

```bash
# Explicit validation
nebius-vpngw validate-config my-vpn.config.yaml

# Automatic during deployment
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

### Environment Variables

Use `${VAR}` for secrets and environment-specific values:

```yaml
tenant_id: ${TENANT_ID}
project_id: ${PROJECT_ID}
region_id: ${REGION_ID}
psk: ${TUNNEL_1_PSK}
remote_public_ip: ${PEER_IP_1}
```

Missing variables are reported before deployment.

### Template Generation

Generate a guided config in a terminal:

```bash
nebius-vpngw create-config my-vpn.config.yaml
```

Force either mode explicitly:

```bash
nebius-vpngw create-config my-vpn.config.yaml --interactive
nebius-vpngw create-config my-vpn.config.yaml --no-interactive
```

Non-TTY calls and `--no-interactive` retain the embedded, commented template.
Files ending in `.config.yaml` are auto-ignored by git.

### Merge Precedence

Settings cascade with specific overriding general:

1. Tunnel-level settings (highest priority)
2. Connection-level settings
3. Peer config imports
4. Global defaults (lowest priority)

## Commands

### Configuration Management

**Create new config:**

```bash
nebius-vpngw create-config <file>
# Use --force to overwrite existing files
# Use --interactive to force the wizard
# Use --no-interactive to force the existing template
```

If the target file already contains the current embedded template, rerunning the
same command is a no-op and exits successfully.

The wizard writes only after schema validation and a redacted final confirmation.
Its optional network-preparation prompt is a distinct cloud-effect confirmation
and defaults to No. Quitting, EOF, validation failure, or a declined overwrite
does not publish a partial candidate.

**Convert an existing ordinary config to explicit VM-HA:**

```bash
nebius-vpngw vm-ha --local-config-file <source> [--output <candidate>]
```

For ordinary input, interactive text mode runs a two-phase wizard that preserves
the source, prepares at most the passive public allocation after a separate
default-No confirmation, and writes only a complete schema-valid candidate.
`--force` may repair only an exact candidate; it never replaces conflicting
YAML. Same-path, symlink, and source-hardlink destinations are rejected. JSON
and noninteractive conversion return an actionable result without prompting.
The same `vm-ha` command then plans and performs approved convergence through
the existing apply engine.

**Validate config:**

```bash
nebius-vpngw validate-config <file>
# Returns exit code 0 (valid) or 1 (invalid)
```

**Note:** Operational commands accept `--local-config-file` or `-c`. `validate-config` instead takes the config file as its required positional argument and does not accept either option spelling.

**Prepare network and reserve public IPs:**

```bash
nebius-vpngw prep-network --local-config-file <file>
```

Ensures the dedicated gateway subnet and its route table exist.
Safe to rerun. This public command remains available independently of the
`create-config` wizard for network-first and automation workflows.

- Interactive terminals default to offering eligible existing public allocations; `--interactive` forces this selector and `--no-interactive` disables every prompt

- `gateway_group.network_id` optionally pins deployment to a specific existing Nebius VPC network
- When it is omitted, the CLI reports the auto-selected network once per operation instead of repeating the same progress message for internal safety rereads
- `gateway_group.subnet.name` defaults to `vpngw-subnet`
- `gateway_group.subnet.cidr` pins an exact private CIDR, including an extended RFC1918 range outside the default-network CIDR; its embedded `/prefix` is authoritative
- If `gateway_group.subnet.cidr` is omitted, an existing exact-name subnet is reused with its current CIDR; only when that subnet is absent does the CLI auto-carve the first free subnet using the creation-only `gateway_group.subnet.prefix_length`
- If an explicit CIDR is outside the current network pool, the CLI extends the network pool automatically when the target network has exactly one private pool

- If `gateway_group.external_ips` is empty or partial, it selects or reserves missing public IPs, prints the complete matrix, and conditionally writes it into the YAML.
- Existing allocations are eligible only when they are stable, public, in the exact project/subnet, and unassigned or already assigned to the intended gateway VM/NIC. Multiple candidates are selected independently for VM 0 and VM 1; HA prompts label their initial active/passive roles.
- A matching allocation attached to another resource fails closed instead of being adopted.
- If the matching allocation is in a different subnet, the command fails immediately. Nebius marks public allocation subnet binding fields such as `subnet_id` and `cidr` as immutable, so `vpngw` does not attempt a cross-subnet move.
- If an IP was just released, it will wait briefly (up to ~10s) and retry before giving up.
- A rerun verifies and reuses matching subnet, route, and allocation resources, performs only missing work, and updates YAML through a no-symlink fingerprint check. Cloud resources remain recoverable and the command exits nonzero if that final publication races or fails.

**Generate from peer config (no deployment):**

```bash
nebius-vpngw create-from-peer-config my-vpn.config.yaml \
  --peer-config-file gcp-peer.txt \
  --peer-config-file branch-office.csv
```

Or use the output-path flag form:

```bash
nebius-vpngw create-from-peer-config \
  --peer-config-file gcp-peer.txt \
  --local-config-file my-vpn.config.yaml
```

If the generated output already matches the existing file, rerunning the command
is a no-op and exits successfully.

Supported peer input formats:

- `.txt`: free-form text, exported vendor configs, and config snippets
- `.csv`: one row per tunnel; rows with the same `connection_name` + `vendor` are grouped
- `.json`
- `.yaml` / `.yml`

### Deployment

**Deploy or update:**

```bash
nebius-vpngw apply --local-config-file <file>

# Force VM recreation
nebius-vpngw apply --local-config-file <file> --recreate-gw

# Override project/region
nebius-vpngw apply --local-config-file <file> --project-id <id> --region <region>
```

Safe to rerun. Matching subnet, route table, VM, and allocation state is reused.

When `gateway_group.external_ips` contains explicit IPs, `apply` resolves those
allocations by IP in the current project before creating anything new. Existing
matches are reused only when they are unattached and already in the target
gateway subnet; attached allocations or different-subnet allocations fail fast
so a different resource cannot be silently stolen or silently rebound.

### Monitoring

**Check status:**

```bash
nebius-vpngw status --local-config-file <file>
```

Shows complete tunnel names, configured active/passive roles, IPsec and BGP
state, peer and encryption details, service health, and routing validation.

Gateway discovery queries the exact VM names in the resolved configuration, so
unrelated project instance count does not affect discovery and stale
prefix-matching VMs do not satisfy the check. One present configured member is
sufficient to continue partial VM-HA observation.

Every Nebius collection read reachable from a public command consumes all API
pages before it returns a result. Malformed responses, continuation-token
cycles, duplicate resource identities, page-limit exhaustion, or a provider
failure after an earlier page make the inventory unavailable; partial items are
never rendered or used as mutation authority. `status` reports unavailable
route-table evidence instead of showing `No routes`, and mutating preparation,
apply, route, destroy, IAM, VM-HA, failover, and failback paths stop without a
success result. Known configured resources use exact name or ID lookups and
validate the returned project/name/identity binding.

For multi-connection configs, `status` keeps configured role in the primary
table and prints a `Traffic Override` panel when runtime behavior differs from
the configured active/passive preference. If live BGP multipath is detected
for the same prefix across different active connections, `status` also prints
an `ECMP Warning` panel that lists the overlapping prefix and active tunnel
names currently carrying it.

**Manage routes:**

```bash
# Audit local routes (Nebius VPC → Remote); this command is read-only
# Shows route tables for explicit-pool workload subnets selected by gateway.local_prefixes
# BGP advertisements include tunnel role plus MATCH/DRIFT/UNKNOWN parity
# It never uploads config or reloads/starts a gateway service
nebius-vpngw list-routes-local --local-config-file <file>

# Add or converge local routes (Nebius VPC → Remote)
# Safe to rerun: only missing routes are added, and complete convergence is required
# - BGP mode: Queries BGP-learned routes from gateway VMs via FRR
# - Static mode: Unions connection remote_prefixes with enabled per-tunnel
#   static_routes.remote_prefixes
# - Creates VPC route table entries with gateway private IP as next-hop
# - Explicit VM HA never uses member primary allocations as VPC next hops:
#   static mode observes and waits for its autonomous controller, while BGP
#   mode may repair only proven advertisement drift
# - Before VM-HA convergence or BGP repair, verifies the required installed
#   agent capability on every affected member; deployment skew fails closed
# - Static VM-HA requires a quiescent ACTIVE lifecycle, exact local/installed
#   generation and digests, stable owner/allocation/epochs, and no writer
#   inhibition. It polls every two seconds for at most 120 seconds and never
#   submits a repair request or writes a VPC route itself
# - Static completion requires the exact owner receipt plus an independent VPC
#   read. An exact-prefix foreign route blocks; unequal overlaps such as a
#   foreign /32 inside the managed /24 remain untouched
# - Any BGP discovery failure aborts the command before partial VPC route
#   reconciliation; remote diagnostics are not copied into the CLI error
# - All subnet, route-table, route, network, allocation, and configured-instance
#   inventories complete before the first route mutation; later read failure
#   stops subsequent writes and cannot be reported as convergence
# - Explicitly repairs proven BGP export drift only while lifecycle, owner,
#   allocation, generation, the target member's local ownership epoch,
#   forwarding/fencing, writer inhibition, and pending-operation evidence stay exact
# - Reconciles only the already-installed gateway config; use apply to deploy YAML changes
# - Serializes authority validation through render with the apply/rearm and
#   mTLS writer locks
# - UNKNOWN advertisement evidence never authorizes a reload
# - Filters out local networks automatically
# - Skips remote prefixes that overlap the target network's private pools
# - Sanitizes inherited subnet status CIDRs to ignore explicit CIDRs owned by other subnets
# - Optional: --summarize merges routes only when they already form an exact larger CIDR
#   and use the same gateway next-hop
# - Optional: --swap-route-table builds a fresh custom route table, copies preserved
#   non-vpngw routes, rebuilds managed VPN routes, then reattaches the subnet
# - --swap-route-table asks for confirmation and writes rollback specs under
#   .nebius-vpngw-rollbacks/ next to the local config file
# - --summarize, --swap-route-table, and --yes are ordinary-gateway options;
#   --yes is valid only together with --swap-route-table
# - A later run without --summarize reconciles back to exact managed routes and
#   removes broader `vpngw-*` summaries after the exact routes are in place
# - Targets explicit-pool subnets directly and inherited-pool subnets only after sanitizing inherited status CIDRs
# - Copies existing routes when creating custom route tables
nebius-vpngw add-routes-local --local-config-file <file>

# List remote routes (Remote → Nebius)
# - BGP mode: Shows BGP-learned routes for the selected connection's tunnel peers
#   with whitelist status and XFRM interfaces
# - Static mode: Shows static routes and kernel installation status
# - Filters out locally originated routes (next-hop 0.0.0.0)
nebius-vpngw list-routes-remote --local-config-file <file>
```

**Route Management Concepts:**

- **Local Routes (Nebius → Remote)**: VPC route table entries that direct traffic from Nebius subnets to remote networks via the VPN gateway
  - Destination: Remote networks (BGP-learned or statically configured)
  - Next-hop: VPN gateway private IP
  - Managed via Nebius VPC API
  - Large imported route sets can hit per-route-table limits even when the tenant-wide route quota shown in the console still has headroom. `vpc.routetable.max-route-count` means the target subnet route table is full.
  - Prefixes that overlap the target network's private pools are skipped before route creation. Nebius treats those CIDRs as local to the network and rejects them as route destinations.
- For inherited-pool subnets (`use_network_pools=true`), the CLI reads the current `status.ipv4_private_pools[].cidrs` shape (with an older-SDK fallback), then sanitizes those CIDRs against explicit CIDRs owned by other subnets before it decides which subnet route tables to touch. This avoids a Nebius console/API status bug where inherited subnets appear to own CIDRs that were explicitly carved out elsewhere.
  - If a route with the same destination already exists but points to a different next-hop, `add-routes-local` warns, leaves that route unchanged, and exits nonzero because the requested postcondition was not reached.
  - `add-routes-local --summarize` is conservative. It only merges routes when:
    - the prefixes are exact neighbors or one already contains the other
    - the merged result is a valid CIDR block
    - both routes use the same gateway next-hop
  - Example: `10.0.0.0/24` + `10.0.1.0/24` can become `10.0.0.0/23` if both use the same gateway.
  - It will not create a broader route if there is a gap. Example: `10.0.0.0/24` and `10.0.2.0/24` stay separate; it will not invent `10.0.0.0/22`.
  - If you later rerun `add-routes-local` without `--summarize`, the CLI restores exact `vpngw-*` routes and prunes broader managed summaries after the exact routes are confirmed installed. It does not leave both forms behind.
  - `add-routes-local --swap-route-table` is a blue/green cleanup mode for subnets that already use a custom route table:
    - it creates a fresh custom route table
    - copies non-`vpngw-*` routes from the currently attached table
    - rebuilds managed VPN routes from the current YAML
    - validates the replacement table before attaching the subnet to it
    - leaves the old route table in place for rollback
  - The live `add-routes-local --help` output now calls out that `--swap-route-table` validates the replacement table before cutover and prints a rollback command.
  - The command prompts for confirmation before a swap because reattaching a subnet to a different route table can briefly impact traffic if the replacement table is incomplete or subnet reassignment converges slowly.
  - For each successful swap, the CLI writes a rollback spec file to `.nebius-vpngw-rollbacks/` next to the local config file and prints the exact `nebius vpc subnet update --file ...` rollback command.
  - `.nebius-vpngw-rollbacks/` is ignored in this repo because those files are local recovery artifacts, not source.

- **Remote Routes (Remote → Nebius)**: Routes on the gateway VMs that direct traffic from remote sites to Nebius networks
  - BGP mode: Dynamically learned via FRR and installed in kernel
  - Static mode: Manually configured in YAML
  - Static installation status compares canonical IPv4 networks, so an
    `ip route` host destination rendered without `/32` still matches the
    equivalent configured prefix.
  - Connection-level static prefixes and enabled, member-scoped per-tunnel `static_routes.remote_prefixes` are shown through one canonical resolver.
  - Visible via SSH queries to gateway VMs
  - `list-routes-remote` scopes BGP output to the selected connection's tunnel peers on the owning gateway VM so multi-connection gateways do not repeat the full FRR table for every connection.
  - `list-routes-local` attributes advertised BGP routes by both peer IP and owning gateway VM, so reused APIPA ranges on different gateway instances do not cross-label connection/tunnel output.
  - `list-routes-local` is observational: it reports exact per-gateway export
    parity as `MATCH`, proven mismatch as `DRIFT`, and incomplete peer or VM-HA
    authority evidence as `UNKNOWN`. Use `add-routes-local` to reconcile only
    the already-installed intent, or `apply` to deploy a changed generation.
    The BGP path may force-render under exact authority; the static VM-HA path
    only observes the autonomous controller and waits for its exact receipt and
    cloud postcondition. Neither path uploads replacement configuration.
  - For explicit VM HA, each `list-routes-local` gateway heading shows the
    authoritative current VM role as `ACTIVE`, `STANDBY`, or `UNKNOWN`; the
    active heading is green. These gateway roles are independent from the
    configured `(active)`/`(passive)` preference shown on tunnel rows.

**Tunnel Management:**

```bash
# Manually restart a specific tunnel
nebius-vpngw restart-tunnel gcp-ha-tunnel-1 --local-config-file <file>

# Restart all tunnels on all gateway VMs
nebius-vpngw restart-tunnel all --local-config-file <file>

# Manual failover to passive tunnel
# - If exactly two tunnels exist, passive is auto-selected
# - If more than two tunnels exist, pass the passive tunnel name
# - In multi-connection configs, this is the normal path: be explicit
nebius-vpngw failover tunnel --local-config-file <file>
nebius-vpngw failover tunnel gcp-ha-tunnel-2 --local-config-file <file>

# Manual failback to restore the active tunnel (does not disable passive)
# - If multiple active tunnels exist, pass the active tunnel name
# - In multi-connection configs, this is the normal path: be explicit
nebius-vpngw failback tunnel --local-config-file <file>
nebius-vpngw failback tunnel gcp-ha-tunnel-1 --local-config-file <file>
```

`restart-tunnel <name>` is supported only on regular gateways (non-HA) and
targets only the gateway VM(s) that own that tunnel in the resolved deployment
plan. `failover tunnel` and `failback tunnel` are supported only on regular
gateways (non-HA) using BGP, not Static routing; they operate on the owning
connection/instance. All three are rejected before SSH for explicit VM HA.
Automatic controller-owned tunnel health handling remains active for a
VM-HA-enabled gateway. Use `status` to inspect its health, and use `apply` only
when configuration convergence is required;
`restart-tunnel` has no manual VM-HA equivalent. `failover vm` and `failback vm`
transfer whole-VM ownership only and do not select a tunnel. `destroy` accepts
ordinary and explicit VM-HA configurations through the same identity-bound,
resumable teardown workflow.

For a VM-HA-enabled gateway, `restart-tunnel` exits `1` with only this stderr
line, before a loading banner, authentication, SSH, or subprocess execution:

```text
Tunnel restart is not supported for a VM-HA-enabled gateway. Tunnel recovery is controller-owned; use 'nebius-vpngw status --local-config-file <file>' to inspect health and 'nebius-vpngw apply --local-config-file <file>' only for configuration convergence.
```

For an ordinary gateway, the command remains supported in both routing modes:
Static restarts only the selected IPsec tunnel, while BGP also resets the
matching BGP neighbor.

Manual tunnel failover and failback are not supported for a VM-HA-enabled
gateway, regardless of routing mode. They are also not supported for an ordinary
Static configuration; the tunnel commands are available only for ordinary BGP.
An ordinary Static configuration supports neither tunnel nor VM
failover/failback. Unsupported tunnel transfer exits `1` with one action-specific
stderr line before the loading banner, authentication, SSH, or other external
effects, and does not print a traceback.

The live `--help` output for these commands now reflects that ownership model:
`restart-tunnel`, `failover tunnel`, and `failback tunnel` explicitly identify
regular gateways (non-HA) as their supported topology. The transfer commands
also retain their BGP-only distinction and call out that multi-connection
configs normally require a tunnel name.

`failover tunnel` is an operational override. It administratively shuts down the
configured active tunnel's BGP neighbor to move traffic to the passive tunnel,
but it does not rewrite YAML or swap configured roles. `failback tunnel` clears that
override and restores traffic to the configured active tunnel.

Tunnel names must be globally unique across the full config. The schema enforces
that, and these operator commands rely on it so you do not need to pass a
connection name alongside the tunnel name.

**When to use:**

- Recovering from tunnel state desynchronization issues
- After detecting connectivity failures
- During maintenance windows
- Testing failover behavior

**What it does:**

- Resolves the selected configured BGP path and its owning gateway instance
- For failover, administratively shuts the configured active FRR BGP neighbor
  on that owning instance
- For failback, clears that shutdown from the configured active FRR BGP
  neighbor on that owning instance
- Polls BGP state for up to 30 seconds and reports whether the requested state
  was confirmed
- Does not restart the agent, recreate IPsec/XFRM state, rewrite configured
  roles, or transfer VM ownership

### Automated Health Monitoring

The gateway includes an automated health monitoring system that detects and recovers from tunnel failures.

**What it checks:**

- IPsec state (CHILD_SA installed)
- BGP neighbor state (Established for BGP tunnels)
- XFRM interface error counters
- Optional ICMP ping to the BGP peer (`ping_enabled`)

**Configuration:**

Add to your `nebius-vpngw.config.yaml`:

```yaml
defaults:
  health_monitoring:
    enabled: true                          # Enable automated monitoring
    check_interval_seconds: 10             # Check every 10 seconds
    max_failures_before_restart: 2         # Restart after 2 consecutive failures
    proactive_refresh_enabled: false       # Reactive mode (detect & fix)
    proactive_refresh_hours: 8             # Unused (proactive mode disabled)
    ping_enabled: false                    # Enable only if peer allows ICMP to APIPA
```

**Monitoring Modes:**

| Mode                   | Behavior                                      | Downtime                  | Use Case                    |
|------------------------|-----------------------------------------------|---------------------------|-----------------------------|
| **Reactive (default)** | Detect failures, restart only when broken     | ~35s during failures      | 100% uptime priority        |
| **Proactive**          | Periodic restart every N hours (preventive)   | ~10-15s every N hours     | Prevent stale state buildup |

**Detection Timing:**

With `max_failures_before_restart: 2` and `check_interval_seconds: 10`:

1. **t=0s:** Normal operation
2. **t=10s:** First failure detected → Immediate re-check in 5 seconds
3. **t=15s:** Second failure confirmed → Tunnel restarted immediately
4. **t=35s:** Tunnel re-established, traffic flows

**Total detection time: ~15 seconds** (not 20s)
**Total recovery time: ~35 seconds** (detection + restart)

**Service Management:**

The health monitor runs as a systemd service on each gateway VM:

```bash
# Check monitor status (SSH to gateway VM)
sudo systemctl status nebius-vpngw-health-monitor

# View monitor logs
sudo journalctl -u nebius-vpngw-health-monitor -f

# Restart monitor
sudo systemctl restart nebius-vpngw-health-monitor
```

**Keepalive Strategy:**

The gateway uses three layers of keepalive to maintain tunnel health:

1. **NAT-T Keepalives (20s):** Prevent NAT session timeouts
2. **DPD (5s checks, 15s timeout):** Detect IKE control plane failures
3. **Health Monitor (10s checks):** Detect data plane failures

This multi-layer approach ensures rapid detection and recovery from various failure modes.

## Routing Modes

### Active/Passive HA for Multi-Tunnel Connections

The gateway operates in **Active/Passive mode** to ensure symmetric routing without requiring workload VM configuration changes. When configuring multiple tunnels to the same peer (e.g., GCP HA VPN), **keep only one tunnel active** at a time.
`defaults.ha_mode` is required in the YAML config and must be set to `active-passive` in current releases.

**Tunnel Mode Configuration:**

| Desired Mode | Config Required | Description |
| ------------ | --------------- | ----------- |
| **active** | `ha_role: "active"` **OR** omit the field (default) | Primary tunnel with BGP local-preference 200. Carries all data traffic. |
| **passive** | `ha_role: "passive"` (**must be explicit**) | Standby tunnel with BGP local-preference 100. Hot standby for automatic failover. |
| **disable** | `ha_role: "disable"` (**must be explicit**) | Tunnel completely skipped (no IPsec, no BGP). |

**Important:** If you omit `ha_role` on multiple tunnels, they will all default to `"active"`, creating ECMP load balancing that may cause asymmetric routing and packet loss. Always explicitly set one tunnel to `"passive"` in multi-tunnel configurations.

**Scope boundary:** Tunnel active/passive is enforced per connection per gateway
VM. Two independent gateway VMs can still create conflicting active paths;
`gateway_group` alone does not coordinate them. Use the explicit
`gateway_group.vm_ha` two-member contract when one routed service needs VM-level
ownership fencing. VM HA remains independent from tunnel `ha_role`, and
active-active or ECMP behavior is not supported.

Multiple `connections` on the same gateway are supported for multi-site designs. Keep the Active/Passive rule inside each connection, and prefer distinct site prefixes per connection. If different active connections learn the same prefix, FRR can install multipath for that prefix and `status` will warn with the overlapping prefix and active tunnel names.

**Example:**

```yaml
connections:
  - name: "gcp-ha-vpn"
    routing_mode: bgp
    tunnels:
      - name: "tunnel-1"
        ha_role: "active"    # Primary - carries traffic
        # ...
      - name: "tunnel-2"
        ha_role: "passive"   # Standby - automatic failover
        # ...
```

### BGP (Recommended)

**Advantages:**

- Dynamic route learning
- Automatic failover
- Route filtering and policies
- Scales to large networks

**Requirements:**

- `bgp.remote_asn` must be configured
- `bgp.enabled` must be true when `routing_mode: bgp`
- Inner IPs must be /30 APIPA (169.254.0.0/16)
- Peer must support BGP

**Configuration:**

```yaml
defaults:
  routing:
    mode: bgp

gateway:
  local_asn: 64512
  local_prefixes:
    - "10.0.0.0/16"

connections:
  - name: peer
    vendor: generic
    routing_mode: bgp
    bgp:
      enabled: true
      remote_asn: 65001
      advertise_local_prefixes: true
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
```

### Nebius Managed Kubernetes

For a typical Nebius Managed Kubernetes cluster, treat the stable worker-subnet CIDR as `gateway.local_prefixes`, not the current per-node Pod `/24`s.

- Common pattern: worker nodes and Pod IPs come from the same Nebius VPC subnet CIDR; the current Pod CIDRs are node-assigned and can change.
- Route model: on ordinary gateways, `add-routes-local` updates the worker
  subnet route table so traffic for remote prefixes goes to the VPN gateway.
  Explicit VM-HA reconciles its shared-allocation routes through the controller;
  `add-routes-local` can wait for static convergence only when the local YAML is
  already the installed generation. Pods do not need custom routes.
- ClusterIP: do not treat `ClusterIP` as a VPN target. It is a cluster-internal virtual IP, even if it falls inside the same routed subnet range.
- Cilium defaults commonly seen on Nebius MK8s: `routing-mode: native`, `enable-endpoint-routes: true`, `kube-proxy-replacement: true`.
- Cilium masquerade note: private destinations in `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, and `169.254.0.0/16` are commonly exempt from masquerading. For those prefixes, the remote side may see Pod IPs as the source, so return routing and firewall policy must allow the cluster subnet.
- Stable remote access: use Pod IPs directly, or expose services with `NodePort`, `LoadBalancer`, or Ingress/Gateway. Do not rely on `ClusterIP` over VPN.

### Static Routing

**Advantages:**

- Simpler configuration
- No BGP knowledge required
- Works with any peer

**Disadvantages:**

- Manual route management
- No automatic failover
- Requires VPC route table updates
- Must enumerate all remote networks

**Configuration:**

```yaml
defaults:
  routing:
    mode: static

connections:
  - name: peer
    vendor: generic
    routing_mode: static
    # Required: List all remote networks to route
    remote_prefixes:
      - "192.168.1.0/24"
      - "192.168.2.0/24"
      - "192.168.3.0/24"
    bgp:
      enabled: false
    tunnels:
      - name: tunnel-1
        gateway_instance_index: 0
        remote_public_ip: "203.0.113.1"
        psk: ${TUNNEL_1_PSK}
        inner_cidr: "169.254.20.0/30"
        inner_local_ip: "169.254.20.1"
        inner_remote_ip: "169.254.20.2"
```

**Route management:**

The direct VPC route mutation below applies to ordinary gateways. Explicit
VM-HA static routes remain controller-owned: the same command can verify the
installed generation and wait up to 120 seconds for autonomous convergence,
but it never submits a repair request or writes those routes. Use `apply` first
when the local YAML differs from the installed generation.

```bash
# Add local routes to VPC route table (Nebius → Remote)
nebius-vpngw add-routes-local --local-config-file <file>

# List local routes in VPC
nebius-vpngw list-routes-local --local-config-file <file>

# List remote static routes on gateway VMs
nebius-vpngw list-routes-remote --local-config-file <file>
```

**Note:** For environments with 100+ remote networks, BGP mode is recommended for automatic route learning instead of manual enumeration.

## BGP Configuration

### APIPA Inner IPs

**Requirements:**

- Must be /30 subnet in 169.254.0.0/16 range
- Each tunnel needs unique /30 subnet
- Use .1 and .2 from each /30 (avoid .0 and .3)

**Examples:**

```yaml
tunnels:
  - name: tunnel-1
    inner_cidr: "169.254.10.0/30"
    inner_local_ip: "169.254.10.1"
    inner_remote_ip: "169.254.10.2"
  - name: tunnel-2
    inner_cidr: "169.254.10.4/30"
    inner_local_ip: "169.254.10.5"
    inner_remote_ip: "169.254.10.6"
```

### BGP Timers

Customize defaults (baseline 3:1 ratio; enable BFD for sub-second detection):

```yaml
defaults:
  routing:
    bgp:
      hold_time_seconds: 6
      keepalive_seconds: 2
      graceful_restart: false
      bfd:
        enabled: false
        transmit_interval_ms: 300
        receive_interval_ms: 300
        detect_multiplier: 3
```

**BFD behavior:** BFD support is vendor-specific. The values above are FRR-side defaults, not cloud-vendor-safe defaults. Enable BFD only after confirming the peer platform supports BFD for this VPN type and that both sides use compatible timers. GCP HA VPN and Azure VPN Gateway S2S do not support BFD for BGP over VPN.

### BGP Troubleshooting

**Check BGP sessions:**

```bash
nebius-vpngw status --local-config-file <file>
```

**Common issues:**

- **No OPEN messages:** IPsec tunnel not established or XFRM interface down
- **OPEN errors:** ASN mismatch between peers
- **Routes not installed:** FRR version issue (use 10.x, not 8.4.4)
- **Policy errors:** Add `no bgp ebgp-requires-policy` (automatically configured)

**SSH to VM for debugging:**

```bash
ssh ubuntu@<gateway-ip>
sudo vtysh -c "show bgp summary"
sudo vtysh -c "show ip route"
```

## Static Routing Configuration

### VPC Route Management

For an ordinary gateway, add routes to the VPC route table (Nebius → Remote):

```bash
nebius-vpngw add-routes-local --local-config-file <file>
```

Creates routes for remote prefixes and selects the next-hop from the gateway VM
that owns each connection. In single-VM topologies all routes point to the same
VM; in pinned multi-VM topologies each site's prefixes point to that site's VM.
Explicit VM-HA does not use member-primary allocations as route next hops;
its shared-allocation routes remain owned by the VM-HA controller. In a
static-only deployment, this command waits for that controller only when both
members already run the exact local generation; use `apply` for configuration
changes. A foreign route with the same destination blocks convergence, while a
different-length overlap is preserved and follows normal longest-prefix
selection.

List routes in VPC:

```bash
nebius-vpngw list-routes-local --local-config-file <file>
```

List routes on gateway VMs (Remote → Nebius):

```bash
# BGP mode: Shows BGP-learned routes with whitelist status
# Static mode: Shows static routes and kernel installation status
nebius-vpngw list-routes-remote --local-config-file <file>
```

## Peer Integration

### Supported Peer Inputs

- **Text (`.txt`)**: vendor exports, router snippets, and key/value documents
- **CSV (`.csv`)**: one row per tunnel; rows with the same `connection_name` + `vendor` are grouped
- **JSON**
- **YAML (`.yaml` / `.yml`)**

Vendor detection is best-effort and currently recognizes **GCP**, **AWS**, **Azure**, and **Cisco**. If nothing matches, the importer falls back to `vendor: generic`.

### Import Workflow

```bash
nebius-vpngw create-from-peer-config nebius-vpn.config.yaml \
  --peer-config-file gcp-peer.txt
```

If you omit both `CONFIG_FILE` and `--local-config-file`, the command writes `./nebius-vpngw.config.yaml`.
`--local-config-file` is accepted as an output-path alias on this command.

`create-from-peer-config` now builds `connections:` from parsed peer specs instead of reusing the template's fixed sample topology. The generated file is validated against the schema before it is written.

### Keyword Matching

The importer normalizes input keys and matches them against a keyword list. These aliases work across CSV headers, JSON/YAML keys, and `key: value` / `key = value` text.

| Target field | Accepted keywords |
| --- | --- |
| `connection.name` | `connection_name`, `vpn_name`, `peer_name`, `gateway_name`, `router_name`, `name` |
| `connection.vendor` | `vendor`, `provider`, `cloud`, `platform` |
| `connection.routing_mode` | `routing_mode`, `route_mode`, `routing_protocol`, `mode`, `protocol` |
| `connection.bgp.remote_asn` | `remote_asn`, `peer_asn`, `bgp_asn`, `neighbor_asn`, `cloud_router_asn`, `vgw_asn`, `asn` |
| `connection.remote_prefixes` | `remote_prefixes`, `remote_networks`, `destination_prefixes`, `destination_cidrs`, `routes`, `networks`, `subnets` |
| `tunnel.name` | `tunnel_name`, `vpn_tunnel_name`, `interface_name`, `interface`, `name` |
| `tunnel.remote_public_ip` | `remote_public_ip`, `peer_public_ip`, `remote_gateway_ip`, `vpn_gateway_ip`, `peer_ip`, `endpoint_ip`, `remote_ip`, `outside_ip` |
| `tunnel.psk` | `psk`, `pre_shared_key`, `shared_secret`, `shared_key`, `ipsec_shared_secret`, `secret` |
| `tunnel.inner_cidr` | `inner_cidr`, `inside_cidr`, `tunnel_cidr`, `link_cidr`, `vti_cidr`, `apipa_cidr`, `inside_ip_addresses` |
| `tunnel.inner_local_ip` | `inner_local_ip`, `local_inside_ip`, `customer_inside_ip`, `customer_gateway_inside_address`, `peer_ip_address`, `apipa_local` |
| `tunnel.inner_remote_ip` | `inner_remote_ip`, `remote_inside_ip`, `vpn_gateway_inside_address`, `cloud_inside_ip`, `bgp_peer_ip`, `ip_address`, `apipa_remote` |
| `tunnel.gateway_instance_index` | `gateway_instance_index`, `instance_index`, `vm_index` |
| `tunnel.local_public_ip_index` | `local_public_ip_index`, `public_ip_index`, `nic_index`, `interface_index` |
| `tunnel.ha_role` | `ha_role`, `role`, `state`, `active_standby_role` |

### Defaulting Rules

If a field cannot be matched, the importer keeps going and fills schema-safe defaults:

- `vendor`: `generic`
- `routing_mode`: `static` when remote prefixes exist, `bgp` when a remote ASN exists, otherwise `static` for `cisco/generic` and `bgp` for cloud vendors
- BGP `remote_asn`: `65014` when BGP is selected but no ASN is found
- Static `remote_prefixes`: `192.0.2.0/24`
- Tunnel defaults:
  - `gateway_instance_index: 0`
  - `local_public_ip_index: 0`
  - first tunnel `ha_role: active`, later tunnels `ha_role: passive`
  - generated APIPA `/30` ranges when inner addresses are missing
  - generated PSK placeholders such as `${GCP_HA_VPN_TUNNEL_1_PSK}`

Review the generated YAML before deployment, especially placeholder PSKs, remote prefixes, public IPs, and any inferred routing mode.

### Example: GCP HA VPN

**1. Export GCP Cloud Router config:**

```bash
gcloud compute routers describe my-router \
  --region us-central1 \
  --format yaml > gcp-peer.txt
```

**2. Generate a Nebius config from the peer file:**

```bash
nebius-vpngw create-from-peer-config gcp-ha-vpn.config.yaml \
  --peer-config-file gcp-peer.txt
```

Equivalent flag form:

```bash
nebius-vpngw create-from-peer-config \
  --peer-config-file gcp-peer.txt \
  --local-config-file gcp-ha-vpn.config.yaml
```

**3. Generated BGP target shape (review and adjust values before deploy):**

```yaml
connections:
  - name: gcp-ha-vpn
    vendor: gcp
    routing_mode: bgp
    bgp:
      enabled: true
      remote_asn: 65014
      advertise_local_prefixes: true
    tunnels:
      - name: nebius-204-12-170-147-tunnel-1
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: active
        remote_public_ip: "34.157.15.187"
        psk: "${GCP_HA_VPN_TUNNEL_1_PSK}"
        inner_cidr: "169.254.10.0/30"
        inner_local_ip: "169.254.10.1"
        inner_remote_ip: "169.254.10.2"
      - name: nebius-204-12-170-147-tunnel-2
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: passive
        remote_public_ip: "34.157.140.153"
        psk: "${GCP_HA_VPN_TUNNEL_2_PSK}"
        inner_cidr: "169.254.11.0/30"
        inner_local_ip: "169.254.11.1"
        inner_remote_ip: "169.254.11.2"
```

### Example: Static Routing Import

If the input contains remote prefixes but no BGP ASN, the generator emits a static connection:

```yaml
connections:
  - name: onprem-static
    vendor: cisco
    routing_mode: static
    remote_prefixes:
      - "192.168.0.0/16"
    bgp:
      enabled: false
      remote_asn: null
      advertise_local_prefixes: false
    tunnels:
      - name: onprem-static-tunnel-1
        gateway_instance_index: 0
        local_public_ip_index: 0
        ha_role: active
        remote_public_ip: "203.0.113.5"
        psk: "${ONPREM_STATIC_TUNNEL_1_PSK}"
        inner_cidr: "169.254.30.0/30"
        inner_local_ip: "169.254.30.1"
        inner_remote_ip: "169.254.30.2"
```

**4. Validate and deploy:**

```bash
nebius-vpngw validate-config gcp-ha-vpn.config.yaml
nebius-vpngw apply --local-config-file gcp-ha-vpn.config.yaml
```

Peer import only fills what it can from the input. Any placeholder or inferred value should be treated as a review item, not as final deployment intent.

## VM Management

### VM Lifecycle

**Create:** Initial provisioning with cloud-init hardening

**Update:** Config push + agent reload (no VM recreation)

**Recreate:** Explicit `--recreate-gw` flag required

**Destroy:** Exact ordinary or VM-HA teardown with public IP and network
container preservation

### Destroy Gateway Resources

```bash
# Prompts with a default-No confirmation
nebius-vpngw destroy --local-config-file <file>

# Skip only the confirmation; all identity and safety checks still run
nebius-vpngw destroy --local-config-file <file> --yes
```

`destroy` deletes the exact configured gateway VMs, their boot disks, private
IP allocations, and product-owned routes. For VM HA it also deletes the shared
private allocation. It preserves public IP allocations, VPC/subnet/route-table
containers, foreign routes, peer and IAM resources, local configuration, trust
material, and lifecycle receipts.

The command checkpoints accepted cloud operations and is safe to rerun after an
interruption. Guest health, SSH reachability, VM-HA controller readiness, and
Compute runtime state do not block teardown: an exact non-stopped VM is deleted
and proved absent as its fence. Exact current, replacement, and retired VMs
recorded by the lifecycle are included, so an interrupted replacement cannot
strand an old member. A terminally failed predecessor cloud operation is
resolved and the current inventory is replanned; an unreadable or still-running
operation remains blocked. When operation lookup is unavailable, the exact
checkpoint-bound idempotency key is replayed. A submit, wait, or resume error is
accepted only if an authoritative reread proves the exact deletion/fence
postcondition. If an accepted delete itself is proven terminally failed while
the resource remains, the receipt records that failed attempt and retries the
same effect with a new deterministic key; an in-flight or unreadable operation
is not resubmitted. Canonical product routes from an earlier ordinary lifecycle are
deleted when their exact next hop is lifecycle-bound, while partial/conflicting
HA labels and genuinely foreign routes remain protected. The command fails
nonzero on foreign route references, identity drift, or an unsafe same-name
replacement. It also refuses to delete a private allocation that is still
assigned to any foreign Compute. Success is reported only after two fresh
absence observations agree and every retained public allocation is stably
detached. A later `apply` creates fresh identities without reusing the deleted
lifecycle bindings.

To reinstall a gateway from the same configuration while retaining its public
IP addresses and peer credentials, run:

```bash
nebius-vpngw destroy --local-config-file <file> && \
  nebius-vpngw apply --local-config-file <file> && \
  nebius-vpngw status --local-config-file <file>
```

The `&&` chain prevents provisioning from starting unless teardown finishes
successfully. For an explicit VM-HA configuration, `apply` then begins clean
provisioning from `DESTROYED`, reuses the retained public allocations, recreates
product-owned routes, and renders the unchanged peer credentials from the same
config. The exact retained allocation IDs are lifecycle-bound and revalidated,
so reuse does not depend on listing the addresses explicitly in `external_ips`.
Treat the final `status` result, including established IPsec and BGP sessions,
as the runtime proof that the remote peer re-established the tunnels. Ordinary
gateways using Static routing still require their documented `add-routes-local`
workflow.

### VM Recreation Workflow

```bash
nebius-vpngw apply --local-config-file <file> --recreate-gw
```

**Process:**

1. Detach public IP allocations from old VM
2. Delete old VM
3. Create new VM with same specs
4. Reattach public IP allocations

**Downtime:** Tunnel re-establishment time only (IPs never change)

### Public IP Preservation

`external_ips` is a list per instance; each inner list maps to NICs on that VM. Legacy flat lists are not supported.

**Configuration:**

```yaml
gateway_group:
  external_ips: []  # Auto-allocate
  # OR
  external_ips:
    - ["203.0.113.10"]  # VM 0
    - ["203.0.113.20"]  # VM 1
  # OR (future multi-NIC example)
  external_ips:
    - ["66.201.0.131", "66.201.0.132"]  # VM 0: NIC0, NIC1
    - ["66.201.0.133", "66.201.0.134"]  # VM 1: NIC0, NIC1
```

**Behavior:**

- Empty/omitted: Auto-create allocations
- Provided: Use existing allocations
- Preserved across VM recreation

## System Monitoring

### Status Overview

```bash
nebius-vpngw status --local-config-file <file>
```

**Reports:**

- Tunnel status (ESTABLISHED, CONNECTING, DOWN)
- Complete tunnel name and configured role
- BGP session state and route counts
- Service health (agent, strongSwan, FRR)
- Routing validation (table 220, APIPA routes, orphaned routes)

### Tunnel Status

Per-tunnel information:

- Gateway VM assignment
- Configured active/passive role
- Peer IP address
- Encryption algorithm (e.g., AES_GCM_16-256)
- Uptime (BGP session for BGP mode when available, otherwise IPsec SA uptime)
- BGP state (for BGP tunnels)

### System Health

Service status per VM:

- `nebius-vpngw-agent`: Agent daemon
- `strongswan-starter`: IPsec daemon
- `frr`: Routing daemon

### Routing Validation

Per-VM checks:

- **Table 220:** Detects policy routes (causes asymmetric routing)
- **Broad APIPA:** Detects 169.254.0.0/16 routes (should be /30 only)
- **BGP peer routes:** Shows APIPA routes over XFRM interfaces
- **Orphaned routes:** Routes without corresponding tunnels

## Security

### Cloud-Init Hardening

Applied at VM creation:

- SSH key-only authentication, root login disabled
- Fail2ban for SSH intrusion prevention
- UFW firewall (allows IPsec UDP 500/4500, ESP)
- auditd for command auditing
- Automated security updates (unattended-upgrades)
- ESP4 preflight that removes only `esp4` deny rules left by temporary
  Dirty Frag mitigations, then gates VPN services until ESP4 is loadable
- IP forwarding enabled, ICMP redirects disabled

### Dynamic Firewall

Agent synchronizes UFW rules with active tunnels:

- Adds peer IPs when tunnels configured
- Removes stale peer IPs when tunnels deleted
- Keeps local prefix rules in sync with `gateway.local_prefixes`
- XFRM interfaces not filtered by UFW (allows BGP/encrypted traffic to flow)

### Secrets Management

**Best practices:**

- Use `*.config.yaml` naming (auto-ignored by git)
- Store PSKs in environment variables
- Use `${VAR}` placeholders in config
- Rotate PSKs regularly

**Example:**

```bash
export TUNNEL_1_PSK="$(openssl rand -base64 32)"
export TUNNEL_2_PSK="$(openssl rand -base64 32)"
```

### Audit Logging

`auditd` monitors:

- Configuration file changes
- Command execution history
- Service management (systemctl)

**View audit logs:**

```bash
ssh ubuntu@<gateway-ip>
sudo ausearch -f /etc/nebius-vpngw/
```

## Troubleshooting

### Tunnel Issues

**Check tunnel status:**

```bash
nebius-vpngw status --local-config-file <file>
```

**SSH to gateway VM:**

```bash
ssh ubuntu@<gateway-ip>
sudo ipsec status
sudo ipsec statusall
```

**Check logs:**

```bash
sudo journalctl -u strongswan-starter -f
sudo journalctl -u nebius-vpngw-agent -f
```

### ESP4 Blocked After Dirty Frag Mitigation

Some Ubuntu images temporarily blocked the kernel `esp4` module under
`/etc/modprobe.d` as a mitigation for Dirty Frag kernel issues. This gateway
requires IPv4 ESP for strongSwan/XFRM; if `esp4` remains blocked, IKE can
establish but ESP CHILD_SAs fail to install in the kernel.

New gateway VMs run an automatic preflight during cloud-init:

- package upgrades still run as usual
- only `esp4` block lines are commented out with a `nebius-vpngw` marker
- `esp6`, `rxrpc`, and unrelated module policy are left unchanged
- if an `esp4` policy change or kernel update requires reboot, config push
  waits until the VM has rebooted and `modprobe esp4` succeeds

Fixed future images take the no-op path unless Ubuntu package upgrades require
a reboot.

For an existing gateway that suddenly loses ESP4, use the repair helper from a
source checkout:

```bash
./misc/fix-vpngw-esp4.sh --local-config-file <local-config-file>
# or:
./misc/fix-vpngw-esp4.sh --host ubuntu@<gateway-ip>
```

The helper prints all target gateways and requires typing `REBOOT` unless
`--yes` is passed. It upgrades packages, applies the same ESP4 preflight,
reboots each gateway serially, confirms the remote boot ID changed, verifies
`modprobe esp4`, and restarts `strongswan-starter` and `nebius-vpngw-agent`.
VPN traffic through the target gateway is disrupted for a few minutes during
reboot.

### BGP Issues

**Check BGP sessions:**

```bash
ssh ubuntu@<gateway-ip>
sudo vtysh -c "show bgp summary"
sudo vtysh -c "show bgp neighbors"
sudo vtysh -c "show ip route bgp"
```

**Common fixes:**

1. **ASN mismatch:** Verify `local_asn` and `bgp.remote_asn` match peer
2. **Inner IPs:** Ensure /30 APIPA subnets unique per tunnel
3. **IPsec down:** Fix tunnel before debugging BGP
4. **FRR version:** Upgrade to 10.x if routes not installing

### Routing / XFRM Issues

**Check routing health:**

```bash
nebius-vpngw status --local-config-file <file>
```

**Manual validation:**

```bash
ssh ubuntu@<gateway-ip>
sudo ip route show table 220  # Should be empty
sudo ip route | grep 169.254  # Should show /30 tunnel routes, /32 peer routes, + metadata (169.254.169.x)
sudo ip link show type xfrm   # xfrm0/xfrm1 should exist and be UP
sudo ip addr show xfrm0       # Should have inner_local_ip/30
sudo ip xfrm policy           # Local selector = inner /30 + gateway.local_prefixes; remote = 0.0.0.0/0
```

**Defense-in-Depth Routing Protection (XFRM):**

The gateway uses a three-layer defense against problematic APIPA routing:

#### Layer 1: Kernel Configuration (Hardening)

Sysctl settings harden the kernel's network behavior for VPN environments:

```bash
# /etc/sysctl.d/99-vpn-gateway.conf (APIPA section)
net.ipv4.conf.all.route_localnet=0      # Disable IPv4 link-local routing
net.ipv4.conf.all.accept_local=0        # Prevent local address acceptance
net.ipv4.conf.all.arp_announce=2        # Strict ARP source selection
net.ipv4.conf.all.arp_ignore=1          # Strict ARP target matching
```

These settings:

- Prevent kernel from auto-generating link-local routes on interface events
- Improve ARP security for VPN tunnel operations
- Provide baseline hardening following VPN appliance best practices

**Note:** These settings do NOT prevent DHCP from adding routes (DHCP client explicitly adds them), but they reduce the attack surface and prevent other link-local route issues.

#### Layer 2: Routing Guard (Reactive - Primary Defense)

Agent's `routing_guard.py` runs AFTER all config changes:

- Removes table 220 policy routes (policy routing not used with XFRM)
- Removes broad APIPA routes (169.254.0.0/16) added by DHCP, preserving metadata routes (169.254.169.x)
- Runs on every agent start/reload
- Ensures clean state after netplan/systemd-networkd operations

This is the **primary defense** against DHCP-added broad APIPA routes.

#### Layer 3: Timer Service (Backup)

`nebius-vpngw-fix-routes.timer` runs every 5 minutes:

- Catches policy-rule or route-only table 220 state and broad APIPA routes
  added by periodic DHCP renewals
- Matches the exact selected table, so an unrelated rule at priority 220 or a
  rule selecting table 2200 is preserved
- Uses current-boot VM-HA authority under the shared routing lock: an active
  owner receives the full reconciler, while a fenced passive member receives
  only table 220 and broad `169.254.0.0/16` cleanup
- Skips blocked, stale, transitioning, or unfenced VM-HA members without
  routing mutation
- Independent of agent lifecycle
- Provides continuous enforcement between agent operations

If status reports `routing-hygiene-not-ready`, allow the next five-minute
maintenance cycle and rerun status. If the condition persists, redeploy through
the supported `apply` workflow; `vm-ha` does not repair routing hygiene on
an already-running standby.

Passive maintenance never enables forwarding, rewrites sysctls, creates peer
or local-prefix routes, reloads services, or changes cloud/VPC state. Passive
readiness and `status` remain degraded while either prohibited route artifact
is present or cannot be observed exactly.

**Why This Three-Layer Approach?**

Nebius cloud DHCP server provides gateway `169.254.169.1`, which causes systemd-networkd to add a `169.254.0.0/16` route. This broad route conflicts with VPN tunnel inner IPs (also in 169.254.0.0/16 APIPA range).

We cannot:

- Disable DHCP routes entirely (breaks default gateway and DNS)
- Prevent DHCP client from adding routes (systemd-networkd behavior)

We can:

1. Harden kernel with sysctl (reduce attack surface)
2. Reactively remove bad routes after DHCP adds them (routing_guard)
3. Periodically enforce cleanup (timer)

This follows the same pattern as AWS/Azure/Juniper/Cisco routers when building strongSwan customer gateways in cloud environments.

**If routes persist:**

```bash
# Trigger role-fenced maintenance (blocked/transitioning members are skipped)
ssh ubuntu@<gateway-ip>
sudo systemctl start nebius-vpngw-fix-routes.service

# Or trigger agent reload
sudo systemctl reload nebius-vpngw-agent

# Check sysctl settings
sudo sysctl -a | grep -E "route_localnet|accept_local|arp_announce|arp_ignore"
```

### Agent Issues

**Reload agent:**

```bash
ssh ubuntu@<gateway-ip>
sudo systemctl reload nebius-vpngw-agent
```

**Check agent status:**

```bash
sudo systemctl status nebius-vpngw-agent
sudo journalctl -u nebius-vpngw-agent --since "10 minutes ago"
```

**Trigger config reapply:**

```bash
nebius-vpngw apply --local-config-file <file>
```

### Viewing Logs

**Agent Logs (routing guard, config changes, service health):**

```bash
ssh ubuntu@<gateway-ip>

# Real-time agent logs (routing guard, BGP peer routes, config rendering)
sudo journalctl -u nebius-vpngw-agent -f

# Recent agent logs (last 50 lines)
sudo journalctl -u nebius-vpngw-agent -n 50 --no-pager

# Agent logs since specific time
sudo journalctl -u nebius-vpngw-agent --since "10 minutes ago"

# Search for routing guard operations
sudo journalctl -u nebius-vpngw-agent | grep RoutingGuard

# View clean state confirmations
sudo journalctl -u nebius-vpngw-agent | grep "No orphan routes"

# View route cleanup operations
sudo journalctl -u nebius-vpngw-agent | grep -E "(Removed|orphan|Table 220)"
```

**Route Fix Timer Logs (periodic cleanup every 5 minutes):**

```bash
# Timer service logs
sudo journalctl -u nebius-vpngw-fix-routes.timer -n 20

# Route fix script execution logs
sudo journalctl -u nebius-vpngw-fix-routes.service -n 20

# Check systemd timer status
sudo systemctl status nebius-vpngw-fix-routes.timer
```

**strongSwan IPsec Logs (tunnel establishment, encryption):**

```bash
# Real-time strongSwan logs
sudo journalctl -u strongswan-starter -f

# Recent strongSwan logs
sudo journalctl -u strongswan-starter -n 50

# IKE negotiation logs
sudo journalctl -u strongswan-starter | grep -E "(IKE_SA|CHILD_SA|established)"

# Tunnel errors
sudo journalctl -u strongswan-starter | grep -i error
```

**FRR BGP Logs (routing protocol):**

```bash
# FRR service logs
sudo journalctl -u frr -n 50

# BGP-specific logs (if available)
sudo journalctl | grep bgpd

# Check BGP daemon directly
sudo vtysh -c "show logging" | tail -20
```

**Linux Networking Logs (netplan, systemd-networkd, DHCP):**

```bash
# systemd-networkd logs (DHCP, interface configuration)
sudo journalctl -u systemd-networkd -n 50

# Search for DHCP lease renewals
sudo journalctl -u systemd-networkd | grep -i dhcp

# Search for route additions by DHCP
sudo journalctl -u systemd-networkd | grep -E "(route|169.254)"

# netplan operations
sudo journalctl | grep netplan

# Kernel network messages
sudo dmesg | grep -E "(eth0|xfrm|route)" | tail -20
```

**Firewall Logs (UFW rule changes):**

```bash
# UFW operations from agent
sudo journalctl -u nebius-vpngw-agent | grep -i firewall

# System firewall logs
sudo journalctl | grep ufw | tail -20

# Check current UFW rules
sudo ufw status verbose
# Expected posture (no management CIDRs):
# - SSH allowed from anywhere (or from management CIDRs if configured)
# - ICMP allowed from anywhere on eth0
# - IPsec (UDP 500/4500, ESP) allowed from peers (or anywhere until peers known)
# - TCP 179 not exposed on eth0 (BGP runs over xfrm*)
# - All traffic allowed on xfrm* tunnel interfaces
# - Default deny inbound on eth0 for other ports
```

**Combined View (all critical services):**

```bash
# Follow all VPN-related logs in real-time
sudo journalctl -f -u nebius-vpngw-agent \
                    -u strongswan-starter \
                    -u frr \
                    -u systemd-networkd \
                    -u nebius-vpngw-fix-routes.service

# Recent activity across all services
sudo journalctl --since "5 minutes ago" \
                -u nebius-vpngw-agent \
                -u strongswan-starter \
                -u frr \
                -u systemd-networkd \
                --no-pager
```

**Log Patterns to Watch:**

```bash
# Successful routing guard execution (clean state)
[RoutingGuard] ✓ All invariants OK. BGP peer routes: 2

# Routing issues detected and fixed
[RoutingGuard] Found 1 unexpected APIPA route(s) to remove
[RoutingGuard] Removed orphan APIPA route: 169.254.99.0/24 dev eth0
[RoutingGuard] Removed policy rule: pref 220 from all lookup 220
[RoutingGuard] ✓ Table 220 completely removed
[RoutingGuard] Summary: sysctls_fixed=0 table_220_removed=True broad_apipa_removed=True scope_link_routes_removed=0 orphaned_apipa_removed=1 bgp_peer_routes_ensured=2
[RoutingGuard] ✓ Routing invariants enforced

# BGP sessions established
[RoutingGuard] Ensured route 169.254.18.225/32 via xfrm0
[RoutingGuard] Ensured route 169.254.5.153/32 via xfrm1

# Config changes applied
[Agent] Received signal 1; reloading
[Agent] No changes detected; skipping config render
```

**Troubleshooting Specific Issues:**

```bash
# Issue: BGP not establishing
sudo journalctl -u nebius-vpngw-agent | grep RoutingGuard  # Check routing
sudo journalctl -u strongswan-starter | grep ESTABLISHED   # Check IPsec
sudo vtysh -c "show bgp summary"                           # Check BGP state

# Issue: Routes keep getting added back
sudo journalctl -u systemd-networkd --since "1 hour ago" | grep 169.254
sudo journalctl -u nebius-vpngw-fix-routes.service -n 20

# Issue: Tunnel won't establish
sudo journalctl -u strongswan-starter -n 100 | grep -E "(error|fail|timeout)"
sudo ipsec statusall

# Issue: Agent not responding
sudo systemctl status nebius-vpngw-agent
sudo journalctl -u nebius-vpngw-agent --since "1 hour ago" | tail -50
ps aux | grep "python3 -m nebius_vpngw.agent.main"
```

## Development

### Agent Development

**Modify agent code:**

```bash
# Edit files in src/nebius_vpngw/agent/
vim src/nebius_vpngw/agent/main.py
```

**Rebuild and deploy:**

```bash
python -m build --wheel --no-isolation
nebius-vpngw apply --local-config-file <file>
```

Source/editable installs rebuild and upload the fresh wheel automatically. Wheel-based release/pipx
installs reuse the original wheel URL/file metadata (or `VPNGW_AGENT_WHEEL`) instead of rebuilding
from source.

### Testing Changes

**Schema validation:**

```bash
nebius-vpngw validate-config test.config.yaml
```

**Deploy to test environment:**

```bash
nebius-vpngw apply \
  --local-config-file test.config.yaml \
  --project-id test-project \
  --region eu-north1
```

**Check results:**

```bash
nebius-vpngw status --local-config-file test.config.yaml
```

### Dependency Updates

**Update pyproject.toml:**

```toml
[project]
dependencies = [
  "Pygments>=2.20.0,<3.0.0",  # explicit floor for transitive security advisories
  "pydantic>=2.12.0,<3.0.0",
  # ...
]
```

If a security advisory lands on a transitive package such as `Pygments`, add the floor to
`[project].dependencies` so release wheels and editable installs both carry the constraint.

**Refresh the lockfile, editable install, and build when needed:**

```bash
uv lock
pip install -e ".[dev]"
python -m build --wheel --no-isolation
```

### linting the codes

```bash
python -m ruff format src tests misc
python -m ruff check src tests misc --fix
python -m mypy
```

## Release & Versioning

- Versions are derived from annotated Git tags (`nebius-vpngw-vMAJOR.MINOR.PATCH`) via `setuptools-scm`; no manual edits to `pyproject.toml` are needed. Installed packages use published package metadata, source/editable checkouts prefer live SCM state, and wheel builds keep a package-local `_version.py` only as a fallback for metadata-free environments. Source lookup loads the canonical `pyproject.toml` configuration without writing a version file. When `setuptools-scm` is not installed in a source checkout, runtime version resolution gives `git describe` five seconds before it treats that probe as unavailable and consults package metadata or the generated `_version.py` cache.
- The repo uses the current `setuptools-scm` `semver-pep440` version scheme and nested `[tool.setuptools_scm.tag]` matching so local lint/test/build runs remain free of deprecated scheme and tag-configuration warnings.
- Local developer builds should reuse the prepared project virtualenv (`python -m build --wheel --no-isolation` or `make build`) so the output stays stable and avoids transient isolated-build toolchain warnings.
- The `vpngw` GitHub Actions workflows validate their own YAML in CI and exercise the wheel-build regression test path before release publication, so workflow edits and packaging regressions are checked before tag time.
- Semantic Versioning policy:
  - **MAJOR:** breaking changes (CLI flags removed/changed, behavior changes that could break scripts).
  - **MINOR:** backward-compatible features (new options, new Nebius resources supported).
  - **PATCH:** bug fixes only (no breaking behavior, no new major capability).
- Keep `CHANGELOG.md` updated before tagging; the changelog is the human-friendly record of what changed.
- If you build without a tag, `setuptools-scm` will fall back to `0.0.0`; create a proper `nebius-vpngw-vX.Y.Z` tag before shipping artifacts.

### Release model

- `publish-release.sh` is the local release helper for this service.
- `vpngw-ci.yml` is for PR validation and manual CI only; it does not run from `nebius-vpngw-v*` tags.
- `vpngw-release.yml` is the dedicated release workflow for this service and is triggered only by `nebius-vpngw-v*` tags.
- The local `publish-release.sh --publish X.Y.Z` flow creates the service tag locally and verifies that the tagged source checkout resolves `nebius_vpngw.__version__ == X.Y.Z` before it pushes the tag.
- The release workflow checks out the tagged commit from `services/vpngw`, runs lint and tests, builds the wheel, verifies the wheel version matches the tag, and publishes the GitHub Release asset.

### Choosing the next SemVer

Bump **MAJOR** if there’s a breaking change (CLI flags or behavior changes that can break scripts).
Bump **MINOR** for backward-compatible features.
Bump **PATCH** for fixes only.
**Current working version (including dev distance):** `python -m setuptools_scm`

### How to create a release for this project

1. On your feature branch (or dedicated release-prep branch), run: `./publish-release.sh --prep X.Y.Z`
2. Open a PR from that branch and merge it to `main`.
3. After the PR is merged, switch to `main`, pull the merged commit, and run: `./publish-release.sh --publish X.Y.Z`
4. GitHub Actions workflow [`vpngw-release.yml`](../../.github/workflows/vpngw-release.yml) runs from that tag and publishes the GitHub Release.

Notes:

- `publish-release.sh --publish` only creates and pushes the annotated tag. It does not build or publish artifacts locally.
- `--publish` is intended to run only from a clean local `main` that is up to date with `origin/main`. The clean-worktree check is strict and includes untracked files.
- `--publish` does not require `setuptools-scm` to be installed in your current interpreter; the local tag verification can derive the source-checkout version directly from Git metadata.
- `publish-release.sh --prep` pushes the current branch, and if that branch has no upstream yet it automatically sets `origin/<current-branch>` as upstream on the first push.
- `--prep` now also fails before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`, so you do not prepare a duplicate release version.
- `--prep` preserves markdownlint-safe blank lines between release sections when it moves `Unreleased` notes into the dated release heading.
- `--publish` now fails locally if the target release section exists but is empty, so you do not push a tag that the release workflow would reject later.
- `--prep` is idempotent while the target tag does not already exist. You can run it multiple times for the same unreleased version; it keeps `## [Unreleased]` empty and merges any new Unreleased entries into the target tag section without duplication.
- The script accepts either `X.Y.Z` or `nebius-vpngw-vX.Y.Z`.

### Optional: build a single-file binary (PyInstaller)

- Use when you need a standalone executable instead of a wheel/pipx install.
- Install the tool once in your environment: `pip install pyinstaller`.
- Build the binary: `python -m nebius_vpngw.build` (or run the `build-binary` console script).
- Output lands at `dist/nebius-vpngw`; ship or copy that file directly.

## Project Structure

```text
├── LICENSE
├── README.md
├── pyproject.toml
├── *.config.yaml                         # User configs (git-ignored)
├── publish-release.sh                    # Release helper (prep changelog commit, then create/push release tag)
├── .github/workflows/
│   ├── vpngw-ci.yml                      # PR/manual CI for this service
│   └── vpngw-release.yml                 # Tag-driven GitHub Release workflow for nebius-vpngw-v*
├── doc/
│   └── design.md                         # Detailed design document
├── image/
│   ├── vpngw-architecture.dot            # Architecture diagrams
│   └── vpngw-conn-diagram.dot
├── src/nebius_vpngw/
│   ├── __init__.py
│   ├── __main__.py                       # Entry point
│   ├── cli.py                            # CLI orchestrator
│   ├── config_loader.py                  # YAML parser and merger
│   ├── schema.py                         # Pydantic validation schema
│   ├── config_template.py                # Embedded YAML template (source of truth)
│   ├── build.py                          # Binary build utilities
│   ├── vpngw_sa.py                       # Service account management
│   ├── agent/                            # On-VM agent
│   │   ├── main.py                       # Agent daemon
│   │   ├── frr_renderer.py               # BGP config renderer
│   │   ├── strongswan_renderer.py        # IPsec config renderer
│   │   ├── xfrm_manager.py               # XFRM interface lifecycle (create, address, route)
│   │   ├── routing_guard.py              # Route validation
│   │   ├── firewall_manager.py           # UFW rule sync
│   │   ├── tunnel_iterator.py            # Tunnel enumeration
│   │   ├── state_store.py                # State persistence
│   │   ├── status_check.py               # Health checks
│   │   ├── sanity_check.py               # Routing validation tool
│   │   └── tunnel_health_monitor.py      # Automated tunnel health monitoring
│   ├── deploy/                           # Deployment orchestration
│   │   ├── vm_manager.py                 # VM lifecycle
│   │   ├── vm_diff.py                    # VM change detection
│   │   ├── route_manager.py              # VPC route management
│   │   └── ssh_push.py                   # SSH deployment
│   ├── peer_parsers/                     # Keyword-based peer config importer
│   │   ├── __init__.py
│   │   ├── common.py
│   │   └── importer.py
│   └── systemd/                          # Systemd units/scripts
│       ├── nebius-vpngw-agent.service          # Agent service unit
│       ├── nebius-vpngw-health-monitor.service # Tunnel health monitor service unit
│       ├── nebius-vpngw-fix-routes.service     # Service wrapper for route cleanup
│       ├── nebius-vpngw-fix-routes.timer       # Timer to enforce route cleanup periodically
│       └── setup-vpngw-firewall.sh             # UFW firewall initialization script
```

### Key Modules

**Orchestrator (local):**

- `cli.py`: Command-line interface and workflow orchestration
- `config_loader.py`: YAML parsing, peer-config import/merging, env var expansion, schema validation
- `schema.py`: Pydantic models for strict validation with types and constraints
- `config_template.py`: Embedded YAML template, source of truth, always aligned with schema
- `build.py`: PyInstaller utilities for standalone binary builds

**Agent (on VM):**

- `main.py`: Daemon with idempotent config rendering and SIGHUP reload
- `frr_renderer.py`: Generates FRR BGP configuration with Active/Passive HA (local-preference inbound, MED outbound), prefix filtering, and route-maps
- `strongswan_renderer.py`: Generates strongSwan IPsec configuration with XFRM interfaces
- `xfrm_manager.py`: Manages XFRM tunnel interface lifecycle (create, IP config, MTU)
- `routing_guard.py`: Enforces routing invariants, prevents local_prefix routes that break forwarding, cleans problematic routes
- `fix_routes.py`: Standalone utility invoked by systemd timer to periodically enforce role-fenced routing invariants
- `firewall_manager.py`: Synchronizes UFW rules with active tunnels
- `tunnel_iterator.py`: Centralized tunnel enumeration for consistent indexing
- `state_store.py`: Persists last-applied state for idempotency
- `status_check.py`: Health checks for tunnels, BGP, and routes
- `sanity_check.py`: Routing validation troubleshooting tool
- `tunnel_health_monitor.py`: Automated tunnel health monitoring with ~15s failure detection (immediate re-check), supports reactive/proactive modes

**Deployment:**

- `vm_manager.py`: VM lifecycle via Nebius SDK
- `ssh_push.py`: Package and config deployment over SSH/SFTP
- `route_manager.py`: VPC static route management (static mode only)

**Peer Parsers:**

- `importer.py`: Keyword-based peer config import for `.txt`, `.csv`, `.json`, `.yaml`, `.yml`
- `common.py`: Shared key normalization, vendor inference, and importer helpers

---

For detailed design, workflows, and troubleshooting, see [docs/design.md](docs/design.md).
