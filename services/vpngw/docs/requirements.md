<!-- markdownlint-disable MD001 MD013 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v1 -->
# Project Requirements

## Task Implementer Requirements

### TI-REQ-001: Add opt-in two-node VM-level active/passive HA

- Status: active
- Requirement: Allow one gateway group to operate as exactly two stable VM members with one active owner and one passive candidate, independently of the existing per-tunnel active/passive roles.
- Constraints: VM-level HA must be explicit and default-disabled; omitting it must preserve supported configuration, CLI, allocation naming, planning, deployment, status, and route behavior for existing users.
- Non-goals: Active-active forwarding, ECMP, more than two HA members, legacy aliases, migration shims, or changes to existing tunnel-level HA semantics.

#### Acceptance criteria

- A valid VM-HA configuration resolves two deterministic node identities and one shared cluster identity.
- After provisioning, each node receives one secret-free runtime binding that names the single shared private allocation, both authoritative Compute instance and NIC identities, peer endpoint and credential file references, and the route-runtime identity needed by the controller.
- Invalid member counts, ambiguous roles, or VM-HA and tunnel-role conflation fail before cloud or host mutation.
- Representative configurations without VM HA produce the same resolved plan and observable command behavior as before this feature.

#### Verification

- Run focused schema, template, and configuration-loader tests, including omitted-field golden regressions and invalid-topology cases.

### TI-REQ-002: Apply one immutable cluster generation to both nodes

- Status: active
- Requirement: Compile canonical operator configuration into one cluster generation and digest, two node manifests, logical static-route and BGP-policy manifests, checksums, and node-local rendered artifacts.
- Constraints: Apply must stage and validate the passive before the active, commit each node durably and atomically, recover partial cross-node progress explicitly, and permit automatic failover only while both nodes report the same committed generation and required policy digests.
- Non-goals: Treating the active VM, observed kernel routes, or copied peer state as canonical configuration; introducing a second configuration owner.

#### Acceptance criteria

- Each apply produces deterministic logical manifests and node-specific renderings from the same canonical input.
- A failure after one node commits leaves the serving generation unchanged, marks the newer node non-promotable, and recovers idempotently while retaining current, previous, and last-known-good generations.
- A generation becomes activation-eligible only after both nodes independently acknowledge the same committed generation and required policy digests.
- Generation or required-policy mismatch keeps the active serving, marks the passive non-promotable, and disables automatic failover until parity is restored.
- An explicitly authorized emergency active-only update also disables automatic failover until both nodes are synchronized.

#### Verification

- Run deterministic manifest, digest, passive-first apply, corruption, interrupted-write, and resynchronization tests using injected filesystem and node failures.

### TI-REQ-003: Prevent split brain with authoritative fencing and allocation ownership

- Status: active
- Requirement: Permit promotion only after Nebius Compute authoritatively reports the former owner stopped, the former attachment is absent, and the shared private allocation is independently confirmed on the candidate.
- Constraints: Peer heartbeat, local role, route state, transition journals, timeouts, and process failure are advisory only; ambiguous, unavailable, transitional, running, stopping, or error cloud states must block promotion.
- Non-goals: Consensus claims, lease authority derived only from the two VMs, simultaneous forwarding, or promotion based on loss of peer connectivity alone.

#### Acceptance criteria

- Exactly one node may enable forwarding and owner-only reconciliation for each authoritative allocation snapshot.
- The enforced transition order is former owner stopped, former attachment absent, new attachment exact, ownership re-read exact, then candidate promotion.
- Ownership continuity is keyed by the exact attached candidate Compute resource revision read after assignment; allocation status alone and locally synthesized journals, hashes, or counters are not authoritative ownership epochs.
- Every HA member starts with forwarding and cluster tunnel initiation fail-closed; a boot, process restart, or automatic Compute recovery requires fresh role and cloud-ownership proof before the appropriate passive or active data plane is enabled.
- Every external side effect has durable before-and-after checkpoints and can be retried without skipping fencing or duplicating an unsafe mutation.
- Fencing-critical SDK errors never enter permissive scaffold or best-effort fallback behavior.

#### Verification

- Run fake Compute and allocation tests for stopped, running, stopping, error, unavailable, permission, timeout, stale-read, foreign-owner, detached, partial-update, and crash-replay cases.

### TI-REQ-004: Reconcile routes only from authoritative desired and local learned state

- Status: active
- Requirement: Keep VPC route next hops bound to the shared private allocation while the verified owner reconciles static routes from the committed logical manifest and BGP routes from its own local FRR RIB.
- Constraints: A non-owner must not mutate managed VPC routes; takeover must preserve existing managed BGP routes during a configurable convergence window and resume withdrawal only after bounded stability observations.
- Non-goals: Copying kernel routes, FRR routes, or learned next hops from the active node to the passive; using the transition journal as route truth.

#### Acceptance criteria

- Static logical-route digests match across nodes while node-local XFRM interface renderings may differ.
- BGP promotion readiness requires configured sessions, required prefixes, current import policy, and usable local XFRM next hops; optional-prefix parity is informational.
- Promotion preserves existing managed BGP routes during takeover hold-down, allows newly valid routes, and reconciles static routes from the committed manifest.
- Route completion is durable only when the runtime re-observes a success receipt bound to the exact controller operation ID and full current owner, allocation, ownership revision, generation, policy-digest, and ownership-incarnation context.
- Existing non-HA conflicting-next-hop rejection remains unchanged.

#### Verification

- Run owner-gating, static-manifest, local-FRR, hold-down, stability, withdrawal, partial-failure, retry, and existing non-HA route-selection tests.

### TI-REQ-005: Recover a deterministic fail-closed HA controller

- Status: active
- Requirement: Implement one explicit controller for heartbeat evaluation, readiness, suspicion, fencing, ownership transfer, promotion, degradation, recovery, and manual failback.
- Constraints: Persist immutable revisions and transition checkpoints atomically; authenticate peer traffic; reject stale boot identities and heartbeat sequences; install a cold-start data-plane guard before strongSwan, FRR, or the gateway agent can use stale HA state; use bounded timers and injected clocks.
- Non-goals: Automatic failback, distributed consensus storage, Object Storage as a correctness dependency, or the append-only journal as ownership authority.

#### Acceptance criteria

- The controller exposes normal, suspect, fencing, ownership-transfer, promoting, active, degraded, and blocked outcomes with explicit prerequisites.
- On every boot or restart, the controller begins behind the cold-start guard, re-reads Compute and allocation ownership, and enables only the data-plane mode justified by fresh authoritative state.
- Automatic failover requires generation parity plus required static, BGP, XFRM, service-health, and cloud-ownership readiness.
- Restart at any checkpoint reconstructs the next safe action from committed local state and current cloud truth without enabling forwarding early.
- Authenticated heartbeats report role, owner observation, generation, policy digests, service health, route readiness, and promotion readiness without carrying secrets.
- Every forwarding writer, route timer, agent startup path, and service dependency remains behind the current-boot guard until the controller durably records and exposes the justified data-plane mode; controller stop, failure, or stale readiness restores the guard.

#### Verification

- Run table-driven state-machine, stale-heartbeat, boot-change, timeout-boundary, dual-suspicion, filesystem-fault, cloud-failure, route-failure, and restart tests.

### TI-REQ-006: Provide safe operations, security, and offline proof

- Status: active
- Requirement: Expose generation parity, observed owner, promotion readiness, fencing progress, degraded reasons, explicit recovery, and manual failback through the existing operator workflow when VM HA is enabled.
- Constraints: Use least-privilege cloud permissions, keep secrets out of manifests, journals, status, and logs, package all required services, and perform no live cloud mutation without a separately approved non-production trial.
- Non-goals: Renaming or silently changing existing non-HA commands, automatic failback, production validation, or claiming live readiness from offline tests alone.

#### Acceptance criteria

- Non-HA command syntax, defaults, output meaning, and exit behavior remain supported.
- Removing explicit VM HA performs a fail-closed deactivation transaction that stops and disables HA services, removes HA-only systemd state and credential references, reloads service state, and only then resumes the ordinary non-HA path.
- VM-HA status explains why a passive is promotable or blocked and names the safe operator recovery action.
- Manual failback follows the same fencing, ownership-transfer, readiness, and route-reconciliation invariants as automatic failover.
- HA activation aborts on the first critical remote failure, revalidates the remote generation and digest immediately before installation, and never reports success from stale staging acknowledgements or unverified guard/controller state.
- Manifests, status, journals, and logs contain only absolute credential references; credential material is installed separately with restrictive permissions, and HA IAM grants are selected only from a reviewed action-to-role allowlist.
- Offline two-node tests prove no forwarding or VPC-route mutation occurs before authoritative fencing and exact allocation ownership.
- A later live-ready claim requires a separately authorized non-production trial with independently observed cloud, allocation, forwarding, and route postconditions.

#### Verification

- Run focused CLI, IAM, systemd, packaging, build, release, security, and deterministic composed failover tests, followed by the full unit and integration suites.

## Task Implementer Open Questions

- No architecture-blocking questions remain for offline implementation. Current official API metadata defines Compute `resource_version` as a positive monotonic revision for instance specification changes, which is the ownership-revision source after an exact attachment re-read; exact IAM role names, SDK field parity, and live allocation-transfer behavior still require verification before any non-production trial.

## Task Implementer Requirements Change Log

- 2026-08-12: Reconciled TI-REQ-001 through TI-REQ-006 with the proven post-provision runtime-binding, authoritative ownership-revision, exact route-receipt, guard-closure, fail-closed deactivation, credential-reference, IAM-allowlist, and activation-verification requirements.
- 2026-08-11: Added TI-REQ-001 through TI-REQ-006 for additive two-node VM-level active/passive HA.
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD013 MD024 -->
