<!-- markdownlint-disable MD001 MD013 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v1 -->
## Project Design Contract

## Core Designs

### FEAT-001: Conservative brownfield Python-project hardening

- Status: active
- Requirements: REQ-001
- Selected approach: Retain the existing PEP 621/setuptools-scm `src`-layout project and its current runtime dependencies, entrypoints, test lanes, Makefile, and CI workflows. Harden only proven gaps: bind direct Git fallback time, load runtime SCM state through one environment-backed configuration built from `pyproject.toml`, use nested tag configuration instead of deprecated `tag_regex` inputs, codify existing public packaging and developer-workflow invariants in focused tests, and ignore standard local-only tool output.
- Boundaries and interfaces: `runtime_version.py` owns source-checkout version discovery and must return the same successful versions while treating a timed-out direct Git probe as unavailable. Runtime SCM lookup reads the canonical project configuration, performs no version-file writes, and falls through to the existing direct Git, metadata, generated-file, and unknown sequence when unavailable. Project-contract tests inspect `pyproject.toml` and `Makefile` without importing cloud clients or mutating external state. `.gitignore` changes affect only untracked local artifacts. Runtime cloud, networking, systemd, CLI, schema, persistence, and release interfaces remain unchanged.
- Validation: Warning-strict runtime-version and wheel-build regressions pass, focused project-contract assertions bind the supported configuration, and the complete `make all` workflow passes Ruff, mypy, all 1,284 unit tests, and wheel construction without warnings.
- Rollback: Revert the bounded fallback and additive tests/ignore entries; no data, configuration, dependency, or upgrade migration is required.

#### Alternatives considered

- Replacing the established project with the generic scaffold was rejected because the current project already implements the required structure and a rewrite would risk user-facing regressions.
- Raising Python versions, changing dependency constraints, adding repository-local pre-commit ownership, or broadly hardening systemd/runtime subprocesses was deferred because those changes require separate compatibility and operational evidence.

#### Implementation evidence

- `runtime_version.py` bounds the direct `git describe` fallback at five seconds, catches timeout as an unavailable source, and preserves the existing resolver order and successful version parsing.
- Runtime SCM lookup uses the environment-backed current configuration model, reads nested tag matching from `pyproject.toml`, and explicitly suppresses version-file writes instead of passing deprecated programmatic fields. Build configuration explicitly retains the established source `_version.py` generation behavior.
- `test_python_project_contract.py` binds the supported Python range, public console scripts, `src` package discovery, systemd package data, SCM dependency/configuration/version-file/tag contract, and canonical Makefile targets. Runtime-version coverage proves the timeout path and rejects dependency deprecations.
- `.gitignore` excludes standard local coverage, tox, and nox output while preserving tracked source and public examples.

### FEAT-002: Evidence-driven pytest feedback optimization

- Status: implemented
- Requirements: REQ-002
- Selected approach: Freeze the current dirty non-candidate source identity, measure the isolated unit lane with task-owned cold caches and hard timeouts, and use pytest duration reporting to identify cumulative call cost. The seven-case VM-HA crash-replay test replaces its injected runtime sleeper with a no-op because the fixture's peer sends do not fail and listener-startup pacing is outside its contract; the existing dedicated retry test continues to assert the production delay schedule exactly. A later current-suite diagnostic identified a separate SDK-operation unit test that spent two seconds in real polling only while verifying bounded SDK keyword routing; that test now sets the injected production poll-interval constant to a minimal positive value locally, as required by the current SDK, while retaining the real SDK wait path and all assertions.
- Boundaries and interfaces: Only `tests/unit/test_vm_ha_agent_runtime.py` and `tests/unit/test_vm_manager_allocations.py` change. Production source, pytest configuration, dependencies, public CLI/configuration/persistence behavior, test assertions and selection, effect/restart execution, integration classification, network blocking, serial debugging, coverage, CI, and full correctness lanes remain unchanged. The listener thread and SDK operation waiter are still exercised, but their test fixtures no longer wait for pacing outside the asserted contracts.
- Validation: The original five cold-cache collection samples retained 682 unit tests with a 0.48-second median. Five like-for-like serial samples improved from a 4.07-second median (3.90-4.54 seconds) to 2.80 seconds (2.68-2.85 seconds), while every sample passed the same 682 tests; the focused seven-case median improved from 1.91 to 0.58 seconds. On the current 1,284-test suite, five process-level serial samples improved from a median of 8.98 seconds (8.89-9.24 seconds) to 7.03 seconds (6.83-7.30 seconds), and the focused SDK-operation test improved from 2.42 seconds (2.37-2.42 seconds) to 0.38 seconds (0.37-0.40 seconds). Every sample preserved the exact selection and passing outcome. Duration diagnostics, focused contracts, Ruff, mypy, full project gates, and diff checks pass.
- Rollback: Revert only the focused test/configuration optimization if timing evidence is inconclusive or any selection, outcome, isolation, debugging, or compatibility invariant changes; no production or data migration is involved.

#### Alternatives considered

- Removing tests, weakening assertions, permanent reruns, marker-based under-selection, global plugin-autoload disabling, and unbounded `-n auto` adoption are rejected as displayed-runtime optimizations that can weaken correctness or predictability.
- New affected-test, sharding, or build-system dependencies are deferred unless the measured serial suite and existing xdist execution cannot meet the feedback objective.

#### Implementation evidence

- The frozen non-candidate `test_vm_ha_agent_runtime.py` state was SHA-256 `9dd243b60fea4af7b8744715507eb1e67af5f5c4065500d9f47ff25ba718bbdc`; the measured candidate state, differing only by the focused sleeper patch and its explanatory comments, was SHA-256 `5ae0723ada32500fe41a17bdcd1cb0101daa1af9334357fa7c089e124400ff83`.
- The crash-replay test still constructs production-composed runtimes, starts and stops the peer listener, crashes after each effect, reconstructs runtime state, and proves ordered convergence; it skips only the real listener-startup pacing delay through the runtime's existing sleeper injection point.
- `test_default_service_runtime_retries_bounded_peer_send` remains the focused contract for three bounded send attempts and the exact `0.05`-second retry schedule.
- The frozen non-candidate `test_vm_manager_allocations.py` state was SHA-256 `5cc21ce1d32789cc55b296ce3476bf57dd80f9db82bb0645e390453c179c012c`; the current candidate state is SHA-256 `094a290b30d74491f1ef5e1165f294126f32b99953ef18ca6b46345b94332cf8`, with `vm_ha_cloud.py` unchanged at SHA-256 `f19f3b02c135bff8c6e56683a99060009cbd564f059db9fe68bbae2b2f66d66c`.
- The SDK-operation test still constructs a valid generated SDK operation wrapper, runs its real synchronous waiter, intercepts only the bounded internal update request, and asserts the exact request-keyword set; only its local poll interval is reduced to one microsecond.
- No pytest configuration, fixture scope, marker, dependency, Makefile, CI, coverage, or production-code change was required.

### FEAT-003: Compatibility-preserving configuration wizard

- Status: active
- Requirements: REQ-003
- Selected approach: Route `create-config CONFIG_FILE` to a small Typer/Rich wizard only when both terminal streams are interactive or `--interactive` is explicit; keep the existing template generator as the sole noninteractive and `--no-interactive` path. Build one candidate from current schema-aligned defaults, guide common fields in dependency order, expose advanced settings and VM-HA only through explicit choices, validate through the existing Pydantic model, and atomically replace the target only after redacted review and confirmation. After a valid write, offer the existing network-preparation operation behind a separate default-No effect summary.
- Boundaries and interfaces: A focused `config_wizard.py` owns prompt state, typed coercion, help/back/quit navigation, candidate construction, redacted summaries, schema validation, and stable YAML serialization; `cli.py` owns TTY/flag routing, existing file and filename policy, atomic publication, public output, and the final preparation confirmation. One internal network-preparation service owns config loading, authentication, `GatewayGroupSpec` construction, `VMManager.prepare_network`, and the targeted `external_ips` update; both the wizard and the unchanged `prep-network --local-config-file/--zone` wrapper use it. `schema.py`, configuration version 1, apply/runtime code, and VM/tunnel HA policy remain authoritative and unchanged.
- Validation: Add pure wizard-state tests, forced-interactive CLI transcripts for static, BGP, and explicit VM-HA candidates, byte-compatible non-TTY template regressions, cancellation/overwrite and sensitive-summary tests, shared preparation-service failure tests, real schema/CLI validation of generated files, CLI help checks, and full project gates.
- Rollback: Remove the wizard module and interactive flags, route `create-config` directly to the retained template generator, and leave the shared preparation behavior in the standalone wrapper. No configuration, state, or cloud migration is required.

#### Alternatives considered

- Removing `prep-network` was rejected because it is a supported public command and remains necessary for operators who reserve Nebius-side addresses before peer details are available.
- Always prompting in non-TTY contexts was rejected because it would hang or break existing automation; requiring a resumable schema-invalid draft was rejected because it adds a second persisted lifecycle and unsafe overwrite/comment-preservation problems.
- Importing the full cxcli component wizard or adding `questionary` was rejected because existing Typer/Rich primitives cover the bounded prompts without a new runtime dependency.

#### Implementation evidence

- Implemented the focused `config_wizard.py` prompt/state owner, TTY and
  explicit-mode routing in `cli.py`, destination-fingerprint-guarded atomic
  publication, and one preparation service shared by the wizard handoff and
  public `prep-network` wrapper. No schema, apply/runtime, dependency, or
  public `prep-network` option was changed.
- Forced-interactive BGP, static, and explicit VM-HA transcripts produce
  schema-v1 candidates; non-TTY and `--no-interactive` output remains the exact
  embedded template. Focused tests cover typed reprompting, help/back/quit/EOF,
  overwrite and concurrent-writer preservation, secret-reference summaries,
  separate preparation confirmation, missing-project admission, and the
  cloud-success/YAML-failure boundary.
- Final offline gates passed Ruff, mypy, 987 unit tests, 45 isolated
  integration tests, CLI help smoke, scoped secret-signature checks, and diff
  integrity. No live cloud preparation was invoked for this implementation.

### FEAT-004: Two-phase ordinary-to-VM-HA configuration wizard

- Status: implemented
- Requirements: REQ-004
- Selected approach: Add a dedicated `configure-vm-ha` command that reads one admitted ordinary raw YAML document, derives an allowlisted two-member transform in memory, and uses a two-phase peer handoff. Phase one derives deterministic member-one topology, collects and preflights the two local mode-`0600` credential bundles before authentication, and either accepts a preallocated passive IP or, after a separate default-No confirmation, invokes a selected-index allocation seam for instance one only. It prints the incremental peer parameters and exits without a candidate until the peer is ready. Phase two collects the peer's remote endpoints, validates the complete candidate, presents a redacted summary, and conditionally publishes a new owner-only file without clobbering a racing writer. Existing apply remains the only migration discovery, approval, provisioning, activation, fencing, and recovery engine.
- Boundaries and interfaces: `vm_ha_config_wizard.py` owns raw-document admission, a resolved derivation-only view, deterministic defaults, prompt state, credential preflight, member-one tunnel derivation, structural-diff allowlist, redacted review, and complete-candidate validation; `config_wizard.py` remains focused on initial config creation. `cli.py` owns command registration, TTY admission, source/destination identity and fingerprint checks, exit semantics, passive-reservation confirmation and handoff, and mode-`0600` no-clobber conditional publication with explicit recovery state. `deploy/vm_manager.py` exposes a narrow selected-index public-allocation helper reused by ordinary `prepare_network`; the conversion path passes only index one and never evaluates member zero. `schema.py`, `config_loader.py`, VM-HA apply, lifecycle, route, SSH, and controller code remain authoritative and are not given a second migration path. Raw YAML remains the persistence source; an expanded semantic view drives validation and new identity derivation but is never serialized over existing fields.
- Validation: Exercise ordinary admission and rejection, static/BGP and multi-connection transformation, one instance-one counterpart per existing tunnel, APIPA/name/PSK-reference uniqueness, placeholder preservation with environment sentinels present, structural allowlist failure, bounded redaction, peer-not-ready and cancellation paths, source/destination same-file and concurrent-write defenses, mode `0600`, exact-output idempotency, passive-only allocation reuse and rejection, real schema/config-loader/peer-merge acceptance, existing migration dry-run handoff, and unchanged `create-config`, `prep-network`, `validate-config`, apply, and default-disabled VM-HA behavior.
- Rollback: Remove the additive command and selected-index helper while retaining the existing create wizard, public `prep-network`, schema, and apply behavior. Source configurations and successfully published candidates are ordinary schema-v1 YAML files; no hidden wizard state or live deployment mutation requires rollback.

#### Alternatives considered

- Reusing `create-config` was rejected because it starts from a template and cannot prove preservation of an existing gateway configuration.
- Converting in place was rejected because interruption, peer delay, and comment normalization would make the customer's only configuration an unsafe transaction boundary.
- Saving a schema-invalid intermediate file was rejected because it creates a second draft lifecycle and permits accidental deployment of incomplete topology.
- Running broad `prep-network` was rejected because migration must not require, recreate, validate, or rewrite the serving member's attached public allocation.
- Mutating peer-provider resources or embedding deployment in the wizard was rejected because provider workflows vary and apply already owns the exact approved migration state machine.

#### Implementation evidence

- `vm_ha_config_wizard.py` implements raw/semantic-view separation, ordinary-source admission, deterministic collision-resistant member identities, credential-bundle preflight, counterpart tunnel prompting, structural allowlisting, redacted review, and complete-candidate validation without serializing expanded environment values.
- `cli.py` implements TTY admission, source and destination identity/fingerprint checks, a default-No passive reservation phase, secret-free handoff, and mode-`0600` no-clobber conditional publication with explicit recovery state. `VMManager.prepare_public_allocations` provides the selected-index seam and ordinary `prepare_network` retains its public behavior through that same internal path.
- Focused tests cover file races and recovery artifacts, credential mode/inode/TLS checks, typed and identity placeholders, long-name collision resistance, partial cloud-operation reporting and retry reuse, structural mutation rejection, and a generated candidate passed through real loading, peer merge, and the existing CLI migration dry-run. Ruff, mypy, all 1,015 unit tests, and all 46 integration tests passed offline; no live cloud readiness claim is made.

### FEAT-005: Canonical examples for the public CLI help surface

- Status: implemented
- Requirements: REQ-005
- Selected approach: Define one immutable mapping from every public command name to one or more practical invocations, render those examples through Typer command epilogs, and add a concise quick-start epilog to the root application. Keep the existing callback docstrings as command descriptions and retain the stable workflow ordering.
- Boundaries and interfaces: `cli.py` owns the example registry, formatting helper, root epilog, and command registration. The examples are static help text only: they do not execute commands, read configuration, authenticate, or alter callback behavior. Unit tests introspect the rendered Click command tree, require exact parity with the registry, and verify each visible command's help contains its own invocation despite Rich line wrapping. README and CHANGELOG describe help discovery without duplicating the complete command reference.
- Validation: Render root help and all 18 public command help pages with a fixed terminal width; require `Usage`, `Examples`, and command-specific invocation text; retain focused option/help and command-order tests; then run Ruff, mypy, full unit and isolated integration suites, Markdown lint, security review, and diff checks.
- Rollback: Remove the root and command epilogs, registry, and additive help tests. No CLI syntax, configuration, data, cloud resource, or migration rollback is required.

#### Alternatives considered

- Duplicating example strings across 18 callback docstrings was rejected because it has no complete ownership check and makes drift or copy/paste errors harder to detect.
- Executing example commands in tests was rejected because several commands intentionally authenticate or mutate infrastructure; rendered syntax and existing behavioral tests provide the safe contract boundary.
- Adding a documentation generator or new runtime dependency was rejected because Typer already supports application and command epilogs.

#### Implementation evidence

- `cli.py` owns one immutable, workflow-ordered command-example mapping. The root application and every public command render examples through Typer epilogs, and the existing command order is derived from the same mapping.
- Unit coverage compares the mapping with both Typer registration order and the rendered visible Click command tree, then renders root help and every command help page with a fixed terminal width and verifies its exact normalized invocation text.
- Ruff and mypy passed, all 1,052 unit tests and 46 isolated integration tests passed, README and changelog Markdown lint passed, the canonical specifications validated, and changed-scope security and diff-integrity reviews found no execution or safety-gate change.

### FEAT-006: Resource-scoped failover and failback command groups

- Status: implemented
- Requirements: REQ-005, REQ-006, TI-REQ-009, TI-REQ-011
- Selected approach: Register dedicated Typer subapplications for `failover` and `failback`, each configured to show help when invoked without a resource. Move the existing VM and tunnel callbacks onto `vm` and `tunnel` leaves rather than registering wrappers or duplicate root commands. Replace the flat example mapping with an immutable path-aware registry that owns root order, child order, group epilogs, and leaf epilogs; use a root command class that interleaves Typer groups with ordinary commands in the declared workflow order.
- Boundaries and interfaces: `cli.py` changes only command registration, help metadata, ordering, examples, and diagnostics that print an invocation. Existing callback bodies remain the canonical owners of configuration loading, tunnel selection, VM-HA planning, SSH, cloud, agent requests, prompts, and effects. The public parser accepts only `failover vm`, `failback vm`, `failover tunnel [TUNNEL_NAME]`, and `failback tunnel [TUNNEL_NAME]`; it has no aliases for the removed flat paths. Unit and integration tests recursively inspect the rendered Click tree and exercise nested routing plus parse-time rejection before mocked effect boundaries. README and the Unreleased changelog own the migration guidance; released history remains unchanged.
- Validation: First bind the recursive command tree, deterministic root and child order, all group/leaf help pages, path-aware registry parity, nested VM/tunnel callback routing, and zero-effect rejection for bare and removed paths. Then run existing VM-HA preflight/fencing and tunnel behavior tests, Ruff, mypy, full unit and isolated integration suites, Markdown lint, security review, canonical-spec validation, and diff checks.
- Rollback: Restore the four former flat registrations and flat example registry as one breaking-contract rollback. Do not retain parallel old/new routes or aliases. No configuration, persisted state, cloud resource, agent protocol, or live migration is involved.

#### Alternatives considered

- Keeping the four flat commands was rejected because it leaves one operation family split across resource-specific naming conventions.
- Retaining aliases or deprecation wrappers was rejected because the approved project policy is one fail-fast canonical path and the user explicitly requested a reduced command surface.
- A single callback with a manually parsed resource argument was rejected because Typer subapplications provide native per-resource help, validation, and completion without duplicating dispatch logic.

#### Implementation evidence

- `cli.py` registers `failover` and `failback` as Typer subapplications and moves the existing VM and tunnel callbacks directly to `vm` and `tunnel` leaves. A path-aware immutable example registry and the shared workflow-order group class own root, group, and leaf help without registering shadow root callbacks.
- Unit coverage recursively compares the rendered command tree with the registry, verifies root and child order, renders every help page, routes nested VM requests through the existing preparation and operator boundaries, preserves request-free same-owner behavior, and rejects bare and removed paths before configuration access. Integration coverage independently exercises all group and leaf help paths plus old-path rejection.
- Ruff and mypy passed, all 1,065 unit tests and 58 isolated integration tests passed, selected changed-document Markdown lint and diff-integrity checks passed, and changed-scope security review found no new trust, credential, network, or mutation boundary. No live cloud or gateway command was executed.

### FEAT-007: Authoritative integrated VM-HA status

- Status: implemented
- Requirements: REQ-005, REQ-007, TI-REQ-006, TI-REQ-011
- Selected approach: Remove the public `vm-ha-recover` callback and the duplicate private agent flag, retain private `--vm-ha-status` as the only agent read, and make ordinary `status` build one sanitized VM-HA projection from lifecycle state, structured cloud authority, and two strictly validated member records. Cloud/lifecycle evidence chooses the owner; member reports corroborate it. A pure classifier produces `BLOCKED`, `UNKNOWN`, `TRANSITIONING`, `DEGRADED`, or `HEALTHY` in that precedence. Render that aggregate in the title of one four-column member table instead of exposing a separate diagnostic summary. Project `Role` only from that authoritative current owner: `active` for the owner, `standby` for the other member, and `unknown` when no owner is proven; do not combine this runtime fact with configured preference.
- Boundaries and interfaces: `cli.py` owns structured authority collection, complete display validation, conservative classification, and one `Gateway`/`Role`/`mTLS`/`Ready` renderer. The Role projection consumes only the already-proven authoritative owner identity; configured role remains validated internal evidence and is not a fallback label. One status SSH context carries the configured management username and private key into every subprocess; explicit VM HA snapshots exact trust independently for each member so one missing pin becomes only that member's sanitized unavailable evidence. No status path enrolls trust or permits a permissive fallback. Its status-only loader policy preserves exact unresolved tunnel-PSK environment references because no status branch consumes them, while `config_loader.py` continues to reject every unresolved non-PSK placeholder and leaves mutating callers on the strict path. With resolved PSKs, status retains full local generation comparison. With unresolved PSKs, the status validator instead requires each agent's generation to equal its configuration digest, requires exact generation/digest parity between both available members, and still compares the locally derivable static-route and BGP-policy digests; it never hashes placeholder text as if it were the deployed secret-bearing generation. It always materializes both configured members and converts missing transport or invalid status into sanitized availability evidence. Exact cloud route authority requires the same non-empty managed-prefix set once per route target, all through the shared allocation. Authenticated controller reasons remain behind a closed identity-free normalization boundary. Pending controller effects are transitional only when their generated identity names a configured member and their encoded action kind matches the reported state; this includes an authoritative owner entering passive mode. Starting rearm is transitional, while terminal successful `running` rearm can participate in healthy evidence. The renderer uses neutral identity/role cells, green only for proven healthy aggregate/mTLS/readiness, and red for every non-good or unavailable semantic state. Raw payloads, exceptions, resource identities, revisions, generations, digests, locks, operations, epochs, fingerprints, timings, reasons, and recovery actions stay behind the projection boundary. Non-HA status and all VM-HA mutation paths remain unchanged.
- Validation: Unit-test the complete status-v1 validator and pure classifier across both ownership directions, including exact `active`/`standby` row reversal after ownership transfer and `unknown` roles without authority, plus expected and foreign locks, every controller/rearm state, cloud/member disagreement, missing or malformed evidence, standby-only unavailability, self-consistent unresolved-PSK generations, two-member generation parity, and non-secret digest mismatches. Exercise forced-color and no-color rendering to prove one aggregate title, exact four-column order, exactly two sanitized member rows, no configured-role suffixes, conservative readiness, identity/exception redaction, informational HA exits, fatal setup exits, no HA work for non-HA plans, and no mutation calls. Recursively prove the 17 executable operations plus two groups, parse-time rejection of removed public/private paths, and absence of replacement commands or focused flags, then run full project gates and changed-surface alignment.
- Rollback: Restore the prior summary-plus-member renderer as one source change only if the approved concise layout is explicitly reversed. No configuration, persisted runtime record, cloud resource, or live deployment migration is involved.

#### Alternatives considered

- Renaming `vm-ha-recover` to `vm-ha-status` or `vm-ha-state` was rejected because it would preserve a second public status surface without adding authority or capability.
- Keeping a compatibility alias or focused `--vm-ha-only` view was rejected because the unpublished command has no migration requirement and the selected interface is one canonical status path.
- Trusting member-reported owner/readiness without cloud correlation was rejected because two mutually consistent stale members can still disagree with authoritative allocation ownership.

#### Implementation evidence

- The Role projection now maps only the authoritative owner identity to
  `active`, the other configured member to `standby`, and an absent owner to
  `unknown`. Configured role remains part of strict agent evidence validation
  but is not rendered as a suffix. Focused regressions proved both ownership
  directions, missing authority, missing standby evidence, and the exact
  failover table without changing classifier, readiness, mTLS, cloud, or
  mutation behavior. All focused, unit, integration, Ruff, and mypy gates
  passed offline.
- `cli.py` retains structured lifecycle/cloud authority, complete status-v1
  validation, two configured-member materialization, conservative overall
  classification, unresolved-PSK handling, and identity-safe normalization.
  Its public renderer now emits only one aggregate-titled
  `Gateway`/`Role`/`mTLS`/`Ready` table; neutral identity/role cells and explicit
  green/red semantic cells preserve readable no-color text without rendering
  controller details, certificate metadata, timings, reasons, or actions.
- Pure tests cover both owner directions, expected and foreign locks, every
  controller/rearm state, cloud/member conflict, missing standby evidence,
  complete multi-target route coverage, production-shaped pending repair and
  transfer states, terminal rearm, reason deduplication, timing rendering, and
  closed-allowlist identity redaction. Mocked ordinary-status tests prove the
  HA path uses only observation/status boundaries and that non-HA skips them. A
  real-loader regression proves exact unresolved PSK references, including a
  short `${PSK}` name, cross schema validation without weakening operational
  placeholders or literal-PSK length validation. Recursive
  command tests prove the 17 operations plus two groups and parse-time absence
  of the removed, replacement, and focused-view surfaces. Ruff and mypy
  passed, all 1,098 unit tests and 62 isolated integration tests passed, and no
  live cloud or gateway command was executed.
- Focused presentation tests prove exact headers, one row per configured
  member, healthy and non-good styles, conservative globally gated readiness,
  missing-member `unknown` values, no ANSI dependency in non-color output, and
  absence of the former summary/detail headers. Full validation passed 1,116
  unit and 69 isolated integration tests plus Ruff and mypy without live
  Nebius, SSH, service, route, or gateway access.

### FEAT-008: VM-local direct-pinned mTLS identity lifecycle

- Status: implemented
- Requirements: REQ-008, TI-REQ-001, TI-REQ-002, TI-REQ-006, TI-REQ-008, TI-REQ-011
- Selected approach: Replace operator-supplied VM-HA CA/leaf/key bundles with one self-signed CA-false leaf generated independently on each VM. Exact-pinned management SSH is the enrollment and recovery authority: it invokes idempotent root-only node actions, returns only public certificate receipts, and cross-installs the peer leaf as an exact trust anchor. Keep mTLS identity generation independent from the VPN configuration generation, bootstrap it automatically during initial apply, regenerate only a fenced replacement member during apply, and reserve whole-cluster rotation for the explicit `set-vm-ha-mtls` transaction. Use a clean protocol-v2 peer envelope that binds a monotonic mTLS epoch to the certificate presented on that TLS connection.
- Boundaries and interfaces: `schema.py`, loaders, and both wizards remove `credential_sources` and retain only a member-scoped absolute `nebius_credentials_path`; stale HA-only shapes fail fast with conversion guidance. A new strict node-local mTLS state module owns ECDSA P-256 generation, X.509 profile validation, immutable object storage, atomic active snapshots, operation journals, and root-only internal actions. `agent/vm_ha/runtime.py` supplies a managed immutable credential snapshot for each fresh connection; the transport retains standard TLS certificate verification and additionally requires exact allowed DER fingerprints, DNS/URI identity, and epoch-to-fingerprint agreement. `ssh_push.py` owns public-receipt exchange and staged peer-pin installation without reading a private key. `cli.py` owns exact cloud/SSH admission, dry-run approval identity, passive-first orchestration, status projection, and recovery/resume; it receives no route, allocation, forwarding, Compute-start, or SSH-enrollment authority.
- State and crypto: Store identities, peer leaves, `active.json`, and transaction journals under the existing root-owned VM-HA state root using no-follow/no-clobber file creation, single-link checks, mode `0600`, file and directory fsync, and atomic rename. Each identity uses an unencrypted PKCS#8 ECDSA P-256 private key, random positive serial, SHA-256 self-signature, CA-false and digital-signature-only constraints, client/server EKUs, canonical node DNS and URI SANs, fixed `2000-01-01T00:00:00Z` `notBefore`, and `9999-12-31T23:59:59Z` `notAfter`. A receipt binds cluster, node, Compute identity, epoch, certificate/SPKI fingerprints, and operation identity; two member SPKIs must differ. Private key bytes never leave the node or enter status, manifests, logs, errors, or receipts.
- Enrollment and replacement: Initial apply stages both exact node
  configurations, proves generation parity, installs exact-generation apply
  locks non-owner-first, writes one lock-bound owner-adoption declaration on
  the independently observed current owner, installs and verifies the helper plus its
  cryptography/CFFI runtime, asks both nodes to generate identities, validates
  public receipts, and cross-installs direct peer leaves. It then activates
  non-owner-first under those locks, proves fresh bidirectional heartbeats,
  commits active snapshots, and only afterward releases the locks and enables
  the exact owner. Healthy reapply is a cryptographic no-op. Replacement first
  proves the former Compute stopped/absent and network-fenced; the survivor
  temporarily accepts old/new replacement leaves, the replacement trusts only
  the survivor's active leaf, and fresh epoch-bound handshakes precede commit
  and immediate old-leaf pruning. The survivor key remains unchanged.
- Rotation transaction: `set-vm-ha-mtls` dry-run binds a secret-free plan digest to the exact config, lifecycle, cluster, members, Compute identities, owner/allocation observation, current epochs/fingerprints, target epoch, and ordered phases. After interactive confirmation or exact noninteractive approval, acquire the shared writer lock and durably observe inhibition on both controllers. Prepare both pending identities, expand both peer trust sets, switch the passive local identity, switch the owner, independently reread active slots and served fingerprints, and require three consecutive fresh bidirectional epoch-bound heartbeats after connection draining before commit/prune. Before any new served leaf is observed the exact transaction may roll back; afterward it can only roll forward. Remote journals, not the CLI's last acknowledgement, decide recovery after any lost response or restart.
- Failure handling: A pending exact transaction is resumed rather than replaced. Fresh contexts disable session tickets/resumption and old connections are drained before pruning. Both exact members may rebuild an unusable old mTLS pair entirely over strict SSH when both are Running and cloud ownership is unambiguous. Missing SSH trust, a stopped member, identity/topology drift, an unfenced former member, conflicting writer state, corrupt cross-node receipts, or inability to prove inhibition blocks with a closed status reason. `status` is observation-only and `vm-ha-rearm` remains the sole Compute-start writer.
- Validation: Direct-leaf/profile tests cover exact self-signature, CA-false
  usage, the year-9999 sentinel, distinct SPKIs, direct-pinned handshakes, and
  wrong-leaf rejection. State, SSH, apply, replacement, rotation, heartbeat-v2,
  status, CLI, schema, wizard, runtime, rearm, and package tests cover the
  product workflow and private-key non-export boundary. Offline on 2026-08-19,
  Ruff and mypy passed, all 1,094 unit tests and 63 isolated integration tests
  passed, 14 focused build/release tests passed, and README/changelog Markdown
  lint passed. Supported Python/OpenSSL CI lanes remain the portability gate;
  a live two-VM trial remains separately authorized and cannot be inferred
  from offline proof.
- Rollback: Before release, rollback is source-only: remove the clean-slate managed mTLS path and restore the prior unreleased VM-HA implementation as one coherent change. Do not ship both trust models or a format adapter. After an mTLS transaction begins, operational recovery uses only its recorded rollback-before-switch or roll-forward-after-switch rule; it never restores private keys from the operator laptop.

#### Alternatives considered

- An operator-managed CA, cloud CA, Vault, KMS, or shared CA key on either VM was rejected because the product must remain self-contained and a shared signer would let one compromised member mint the peer identity.
- Trust-on-first-use, permissive key scanning, or accepting the certificate presented on an unauthenticated channel was rejected because it creates a circular bootstrap and permits interception.
- A literal certificate without validity dates was rejected because X.509 requires them; the year-9999 sentinel expresses the selected no-maintenance policy.
- Physical simultaneous rotation was rejected because two independent machines cannot atomically switch together; pre-expanded overlap trust plus a journaled logical commit preserves authenticated compatibility across each phase.
- Automatic timed rotation was rejected because the requested default is set-and-forget operation and every rotation adds a distributed availability-sensitive transaction.

### FEAT-009: Once-per-apply network selection progress

- Status: implemented
- Requirements: REQ-009
- Selected approach: Keep `_resolve_gateway_network` as the authoritative SDK-backed resolver on every call, but separate its user-facing selection notice from the read itself. VM-HA safety observations perform the same SDK lookups and validate the returned identity without rendering progress; the command's provisioning path requests presentation and the current `VMManager` emits each successful selection message only once. Render existing-instance discovery from the actual `recreate` mode instead of using unconditional recreation wording.
- Boundaries and interfaces: `deploy/vm_manager.py` owns network discovery, per-manager informational-message deduplication, and existing-instance progress text. `schema.py` and `config_loader.py` retain the optional `gateway_group.network_id` contract and existing discovery precedence. The change affects human-readable progress only; configuration parsing, SDK read cardinality, cloud selection, mutation authority, and exit behavior remain unchanged.
- Validation: Direct resolver tests call implicit and explicit selection twice, assert the SDK is still read twice, and assert each successful decision message is emitted once. Existing-instance tests cover both `recreate=false` and `recreate=true`; schema and loader regressions prove omission remains valid.
- Rollback: Remove the per-manager notice set and conditional wording; no configuration, cloud, persistence, or upgrade migration is involved.

#### Alternatives considered

- Requiring `gateway_group.network_id` was rejected because omission is an intentional supported schema-v1 contract.
- Caching the resolved SDK objects or network identity was rejected because it could weaken the authoritative rereads used by VM-HA lifecycle validation.
- Removing all network-selection output was rejected because one concise decision remains useful when reviewing an apply.

#### Implementation evidence

- `_resolve_gateway_network` retains its SDK-backed read on every invocation but renders successful selection progress only when the provisioning caller requests it; per-manager message deduplication limits that requested output to one copy.
- Focused tests prove the safety caller remains silent, the provisioning caller requests progress, implicit and explicit selection output is once-only, SDK call cardinality is unchanged, schema placement remains current, optional placeholders still work, and existing-instance wording follows recreate mode.
- Offline validation passed all 1,115 unit tests, Ruff, mypy across 48 source files, diff integrity, and Markdown lint for the changed README and changelog. No live cloud, SSH, service, route, or gateway mutation was performed.

### FEAT-010: Complete tunnel names in a compact VPN status table

- Status: implemented
- Requirements: REQ-010
- Selected approach: Remove the per-row Traffic State column from the primary VPN status table and let Rich's automatic layout allocate the released space to the remaining columns. Configure only the Tunnel column with folded overflow so complete names stay on one line when possible and wrap without ellipsis when the terminal is narrower.
- Boundaries and interfaces: `cli.py` retains configured-role, IPsec, BGP, peer, encryption, uptime, service, routing, ECMP, and Traffic Override collection. Every preferred and fallback/error row emits the same eight-cell contract. Remove only the now-unused traffic-state formatter and per-render carrying caches; retain the carrying-tunnel and BGP helpers used by runtime override detection.
- Validation: Render the pure table configuration with representative and 64-character names at wide and constrained console widths, assert exact headers and no ellipsis, exercise preferred and fallback/error row arity, and retain Traffic Override regressions. Run focused CLI tests followed by the full static, unit, integration, documentation, security, and alignment gates.
- Rollback: Restore the ninth column and matching row cells as one presentation-only source change; no configuration, state, cloud, or deployment migration is involved.

#### Alternatives considered

- Removing the column without changing overflow was rejected because valid long names would still ellipsize on narrower terminals.
- A fixed Tunnel width or `no_wrap` was rejected because it would compress or truncate other operational fields and would not adapt to terminal width.
- Removing runtime override detection was rejected because it carries distinct operator information even when the redundant per-row value is absent.

#### Implementation evidence

- `_vpn_gateway_status_table` owns the exact eight-column Rich table and gives
  only Tunnel folded overflow. Both StrongSwan parsers and every empty, timeout,
  parse-error, command-error, and exception branch now emit eight cells, while
  `_detect_connection_role_overrides` retains the existing runtime warning.
- Focused tests prove exact headers, lossless constrained-width rendering of a
  schema-valid 64-character name, no ellipsis, and retained Traffic Override
  behavior. An AST check proves all seven primary-table row sites have eight
  positional cells. Full Ruff, mypy, 1,116-unit, and 69-integration gates pass;
  no live cloud or gateway execution was required.

### FEAT-011: Owner-aware BGP export policy and observational route audit

- Status: implemented
- Requirements: REQ-011, TI-REQ-001, TI-REQ-002, TI-REQ-004, TI-REQ-005, TI-REQ-007
- Selected approach: Compile one explicit export decision per enabled BGP neighbor from connection policy plus runtime origination authority. Allowed peers share the normalized local-prefix list and retain their active/passive MED route-map; denied peers receive a common explicit deny-all route-map. Project both top-level `gateway` policy and resolved `connections` into the VM-HA controller view, then derive a peer-to-expected-prefix map from that same resolved node configuration and compare bounded Adj-RIB-Out evidence with a tri-state result so incomplete observation can never trigger repair. Passive and blocked transitions avoid a startup dependency on established sessions by accepting only the conjunction of an exact live peer set, empty Adj-RIB-Out for every already-established peer, and running FRR configuration that binds every expected peer exclusively to the exact deny-all map; ordinary readiness and audit remain `UNKNOWN` until all expected peers establish. A BGP policy with zero enabled peers still requires an empty live FRR peer set. Current VM-HA ownership remains a normalized projection of lifecycle, common owner/allocation/generation, each member's local Compute ownership epoch, forwarding, fencing, writer inhibition, pending-operation evidence, and local routing hygiene rather than configured role. Reuse the existing five-minute route-maintenance timer as the single periodic owner, but make its private admission and execution role-aware so a fenced passive receives only the narrow passive cleanup instead of the active route/sysctl reconciler.
- Boundaries and interfaces: `agent/frr_renderer.py` owns deterministic per-neighbor allow/deny rendering and no cloud authority. `agent/state_store.py` advances the render contract so an installed upgrade reapplies the policy, and a failed FRR activation prevents that render version from being persisted. `agent/main.py` projects the resolved top-level `gateway` and `connections` policy into the controller runtime without mutating the persisted or public format. `agent/vm_ha/runtime.py` derives expected exports from that complete resolved projection, omits disabled tunnels without skipping live peer observation, observes exact table-220 rule/route and broad-APIPA postconditions, incorporates mode-appropriate export and routing-hygiene parity into readiness, holds the routing lock across active preparation, exact verification, and forwarding, and durably invalidates an earlier same-boot materialization receipt before requesting a new agent reload so controller observation begins only after a fresh receipt and routing-lock handoff. `agent/routing_guard.py` owns role-aware periodic admission, rechecks the selected active or passive authority inside the existing routing lock, and keeps passive maintenance limited to table-220/broad-APIPA removal and conditional cache flush. `agent/fix_routes.py` selects that role-aware path for VM HA while preserving the ordinary reconciler. `agent/main.py` also exposes the matching private systemd condition and the existing forced reconcile; `nebius-vpngw-fix-routes.service` invokes only that condition before the periodic entrypoint. With `agent/routing_guard.py`, materialization remains one lock-held transaction whose firewall, FRR, table-220, and routing postconditions precede the receipt. `deploy/route_manager.py` owns pure advertisement inspection/comparison and explicit installed-config repair; `cli.py` detects rule-backed and route-only table-220 drift while keeping status and `list-routes-local` observational. Configuration deployment remains exclusively owned by `apply`. No YAML, public CLI, logical-manifest, status-table, heartbeat, or persisted-state schema changes.
- Failure handling: Missing peer output, malformed JSON, non-established expected sessions, an unexpected live peer, ambiguous ownership, lifecycle or member-local ownership-epoch transition, generation disagreement, forwarding/fencing disagreement, unavailable or inhibited writers, routing-lock failure, or unexpected operations produce `UNKNOWN`, proven `DRIFT`, or fail closed before an effect according to the evidence boundary. Listing reports but never repairs and downgrades mixed-time VM-HA observations to `UNKNOWN`. Explicit repair proceeds only from proven `DRIFT` under exact stable authority, refuses an absent, incomplete, stale, or concurrently inhibited on-node authority tuple, bypasses the unchanged-config short circuit without uploading configuration, and reports convergence only after every expected peer is re-observed exactly. Passive render, firewall, hygiene, export verification, or active pre-forward verification failure remains fenced and restores `BLOCKED` authority after re-proving deny-all; if that render cannot be proved, FRR is stopped until a required reload-or-restart reconcile succeeds.
- Validation: Cover active allow-only and passive deny-all rendering, mixed connection policies, enabled and disabled-only tunnel sets, MED preservation, exact/extra/missing Adj-RIB-Out and unexpected live peers, both ownership directions, distinct valid member-local epochs, epoch transitions, incomplete or changing authority, apply/mTLS writer contention, non-mutating listing, installed-config-only drift repair, failed FRR activation persistence, receipt-last materialization, routing-lock contention, periodic passive recurrence after initial success, route-only table-220 and broad-APIPA cleanup, active/passive/blocked condition admission, no unrelated passive mutation, peer-route preservation, routing-hygiene readiness degradation/recovery, active pre-forward verification, four-column status classification/redaction, non-HA compatibility, and composed failover paths before full static, unit, integration, documentation, security, packaging, and alignment gates.
- Rollback: Restore the previous artifact through the supported deployment workflow only if the owner loses its exact local advertisement, established/imported routes regress, forwarding/fencing changes, or VPC route/allocation state changes. Do not hand-edit FRR or restore the unsafe filterless passive behavior; keep the live target unchanged until a separately authorized non-production trial freezes owner and generation expectations.

#### Alternatives considered

- Omitting the outbound route-map when local origination is disabled was rejected because FRR can then export learned routes when `ebgp-requires-policy` is disabled.
- A single global allow/deny switch was rejected because mixed connections require different policies for different peers while BGP `network` statements remain process-wide.
- Treating missing advertised-route output as a match was rejected because absence of evidence cannot authorize a reload or a healthy status.
- Adding `--repair` to `list-routes-local` was rejected in favor of one observational list path and existing explicit mutating workflows.

#### Implementation evidence

- Every enabled peer now has one exact outbound allow or deny route-map, and
  the runtime compares normalized per-peer Adj-RIB-Out with mode-specific
  expectations. The shared routing lock spans active preparation through
  forwarding enablement; passive and blocked transitions prove empty exports,
  with stopped FRR as the retry-safe failure fallback.
- Renderer version 4 forces policy adoption after upgrade. Proven explicit
  repair uses the private authority-bound force-reconcile entrypoint against
  the installed configuration and holds the apply/rearm, mTLS-writer, and
  routing locks through the exact render, while `list-routes-local` calls only
  the tri-state audit. Focused safety tests,
  Ruff, mypy, full serial unit and isolated integration suites, changed-scope
  Markdown lint, CLI help, wheel build, security review, and a final no-blocker
  risk review passed without a live deployment.
- The existing route timer now uses one private role-aware condition and one
  lock-held dispatcher. Exact active and ordinary modes retain the full
  reconciler; exact current-boot passive mode with forwarding disabled invokes
  only table-220 and broad-APIPA cleanup; all ambiguous authority fails closed.
  Runtime and operator status observe both rules and route-only table-220 state,
  parse the selected table as an exact token without treating priority 220 or
  table 2200 as drift, reject failed reads, and degrade passive, cold-standby,
  transfer, promotion, heartbeat, and redundancy readiness until hygiene is
  restored. Status recommends the periodic owner and supported apply path, not
  the non-repairing rearm workflow. Focused
  safety regressions, Ruff, mypy, 1,335 unit tests, 69 isolated integration
  tests, wheel packaging, changed-scope documentation checks, security and
  alignment review, repaired final risk review, and diff integrity passed
  offline on 2026-08-21. No live deployment or gateway mutation was performed.

### FEAT-012: Topology- and mode-aware CLI execution policy

- Status: implemented
- Requirements: REQ-012, REQ-011, REQ-006, TI-REQ-001, TI-REQ-004, TI-REQ-005
- Selected approach: Classify the resolved plan as ordinary or explicit VM HA and as static or BGP, then evaluate one command applicability registry before any prompt, authentication, SSH, SDK mutation, or agent request. Keep public syntax unchanged. Route operations use a typed failure boundary and a canonical remote-prefix resolver. Ordinary `add-routes-local` retains direct VPC route ownership; VM-HA static route reconciliation remains exclusively controller-owned; VM-HA BGP route repair skips legacy VPC mutation and may force only exact proven Adj-RIB-Out drift after a read-only installed-agent capability handshake succeeds on every affected member.
- Boundaries and interfaces: `cli.py` owns public command identity, flag legality, pre-effect applicability evaluation, success rendering, and construction of one immutable VM-HA route SSH trust snapshot before authentication. `config_loader.py` owns normalized connection/tunnel remote-prefix resolution without cloud authority. `deploy/route_manager.py` owns route mutation outcomes, exact host-alias use, installed-agent capability observation, exact advertisement repair, and postcondition failures. `agent/main.py` exposes a private read-only machine-readable capability document alongside the existing authority-bound force action. VM-HA VPC route and shared-allocation ownership remain in the controller/lifecycle route path; ordinary route tables remain in the existing direct manager.
- Failure handling: Unsupported topology/mode/flag combinations raise one Typer-native sanitized usage failure before effects so current vendored-Click releases render the message and nonzero result. Missing or changed VM-HA SSH trust, transport failure, capability absence, malformed JSON, missing required features, route selection ambiguity, SDK or route-table failure, incomplete mutation, authority change, force-reconcile failure, or post-repair drift raises a typed route-management failure and prevents the completion banner. The capability probe discloses only a schema and fixed feature names; it does not load config or mutate state. Mixed ordinary routing queries only the members that own BGP policy, while an all-disabled BGP policy still checks for stale peers. Observational commands retain their no-write contract.
- Validation: Enumerate all executable leaves and relevant flags across the four configuration modes; assert zero external calls for rejected combinations; bind public option/alias manifests; cover tunnel-only static prefixes and member-scoped listing; verify all repair targets pass capability preflight before the first mutation; and prove nonzero results without false completion on every route/repair failure. Run focused and complete static, unit, integration, documentation, packaging, security, and alignment gates.
- Rollback: Revert the applicability registry, capability document, strict route outcomes, and shared prefix resolver as one coherent source change. Do not restore VM-HA direct member-primary route writes or success-on-failure behavior; if the installed agent is older, leave the target unchanged and redeploy the previous complete supported artifact through `apply`.

#### Implementation evidence

- `cli.py` owns an exact 18-leaf applicability registry, rejects VM-HA use of ordinary tunnel/destructive operations and static tunnel failover before effects through Typer's public error boundary, preserves the public command/flag tree, and routes `add-routes-local` through topology-aware ordinary or controller-owned VM-HA behavior.
- `config_loader.py` supplies the canonical enabled/member-scoped static-prefix union; `route_manager.py` supplies typed route failures, read-only installed-agent capability preflight, exact authority-bound advertisement repair, and route postcondition enforcement; `agent/main.py` supplies the fixed private capability document without loading configuration.
- Offline validation on 2026-08-20 passed the complete four-mode command matrix, zero-effect rejection sentinels, public option/alias manifest, focused route, mixed-mode, SSH-trust, and agent tests, Ruff, mypy, 1,283 unit tests, 69 isolated integration tests, changed-scope Markdown lint, security and code-quality review, wheel build/inspection, CLI help smoke, and diff-integrity checks. Live installed-package parity and route convergence remain deployment acceptance work.

#### Alternatives considered

- Teaching the legacy route target collector to pick one VM-HA member was rejected because member primary allocations are not the stable shared route next hop and laptop-side mutation would race the controller.
- Treating VM-HA static `add-routes-local` as a silent no-op was rejected because a successful exit would misrepresent route reconciliation.
- Attempting private agent flags and interpreting argparse failure afterward was rejected because route mutation may already have occurred and partial fleet skew would produce mixed state.
- Keeping print-and-return failures was rejected because callers and automation cannot distinguish convergence from partial or absent effects.

### FEAT-013: Per-deployment managed VM-HA SSH trust

- Status: implemented
- Requirements: REQ-013, REQ-008, REQ-012, TI-REQ-006
- Selected approach: Add a VM-HA-only operator-side trust store under `~/.ssh/nebius-vpngw/<scope-sha256>/`, with the scope digest bound to canonical tenant, project, region, gateway-group, and cluster identity. Store one versioned public-key receipt as authority with only stable member hostname pins, plus one derived OpenSSH projection that maps the same key to the stable hostname and exact current configured or discovered address aliases for older-release compatibility. Keep `VPNGW_SSH_KNOWN_HOSTS_FILE` as a strict highest-precedence import/override and never consult the general OpenSSH user database implicitly. Normalize exact configured or discovered address pins at the input boundary, reject alias conflicts, then use the stable member hostname as `HostKeyAlias` for every current transport.
- Boundaries and interfaces: `deploy/ssh_policy.py` owns scope identity, receipt schema, exact pin extraction, safe local path validation, immutable policy material, locking, atomic publication, and the shared OpenSSH/Paramiko enforcement while preserving generic non-HA behavior. `cli.py` supplies the resolved deployment scope and member aliases, keeps status and every non-apply command read-only, and gives only actual `apply` authority to publish after retained-member verification and before cloud mutation. No YAML, CLI flag, agent protocol, cloud resource, gateway filesystem, or mTLS wire format changes.
- State and recovery: The receipt binds schema, scope fields/digest, and exact raw key type/data/fingerprint records for each stable member; private host keys and credentials never enter it. The projection is a reproducible compatibility cache and may add current address aliases without changing receipt authority. Directories are owner-only and non-symlinked, files are owner-only regular single-link objects, and reads reject identity or content drift. Publication takes one per-scope lock, rechecks source hashes, atomically replaces and fsyncs the authoritative receipt first, then atomically replaces the projection. A crash can therefore leave a stale or missing projection but not a partial authoritative receipt; read-only commands derive their immutable snapshot from the receipt, and the next apply repairs the projection. Explicit-source failure never falls back. Without an override, a valid receipt is authoritative, and a missing retained pin may be derived only from that member's original unencrypted owner-only private host key. Conflicting authoritative sources fail. Fresh or approved replacement members derive the exact key that provisioning will install.
- Apply and migration flow: Dry-run resolves and validates an ephemeral complete policy and reports only `use`, `create`, `repair`, or `migrate`, with no persistent write. Actual apply performs read-only topology discovery, builds the complete candidate, verifies every retained member over exact pinned SSH, atomically publishes the candidate, and only then admits cloud mutation. A successful apply with an explicit source imports only the verified deployment members so later commands work with the variable unset; the source file is never modified. Existing managed receipt state also lets read-only consumers build an immutable temporary snapshot when the projection cache is missing, without performing persistent repair.
- Failure handling and observability: `status` retains member-isolated sanitized unavailability. Other VM-HA commands fail before authentication and direct recovery to `apply`. Diagnostics distinguish an invalid explicit override, missing managed state, receipt/projection corruption, conflicting keys, absent authoritative recovery material, and remote host-key rejection without printing key bytes, fingerprints, credentials, internal cloud identities, or raw parser errors. No path uses TOFU, `ssh-keyscan`, live-handshake enrollment, global known-hosts fallback, or disabled verification.
- Validation: Unit-test scope derivation, exact parser aliases/markers, receipt schema and redaction, file/link/mode/ownership defenses, immutable snapshots, atomic crash and concurrent writer behavior, source precedence, stable hostname aliases, address changes, dry-run/status no-write semantics, retained/fresh/mixed recovery, explicit migration, and no-evidence rejection. Exercise apply pre-mutation ordering and every route/status/mTLS/transfer consumer, preserve ordinary SSH and non-HA regressions, then run Ruff, mypy, full unit/integration, Markdown, canonical-spec, security, alignment, packaging, and diff-integrity gates. Live use remains a separately approved non-production trial.
- Rollback: Older releases ignore the managed receipt and remain usable by pointing `VPNGW_SSH_KNOWN_HOSTS_FILE` at the generated projection, whose current address aliases are regression-tested with the former address-based lookup contract. Reverting the resolver/store does not mutate gateway, cloud, configuration, or mTLS state; retain the public-key-only local files for forward recovery rather than deleting them automatically.

#### Alternatives considered

- Defaulting directly to `~/.ssh/known_hosts` was rejected because it shares ownership with unrelated SSH clients, can contain valid marker syntax unsupported by a whole-file Paramiko parse, and cannot be safely repaired or rewritten by this product.
- One global product known-hosts file was rejected because address reuse and multiple deployments would share one collision and recovery domain.
- Network scanning or accepting the key presented by the live SSH handshake was rejected because it is trust-on-first-use and cannot prove gateway identity.
- Making status repair the store was rejected because observation must remain non-intervening and member-isolated.

## Task Implementer Designs

### TI-DES-001: Separate VM-HA domain and configuration contract

- Status: implemented
- Requirements: TI-REQ-001, TI-REQ-002
- Selected approach: Add a default-disabled VM-HA block under the gateway-group contract, compile stable pre-provision cluster intent, then bind the provisioned shared allocation and authoritative node identities into secret-free node runtime manifests without changing the existing path when omitted.
- Boundaries and interfaces: `schema.py`, `config_loader.py`, `config_template.py`, examples, and their focused tests own configuration validation and pre-provision intent; `config_loader.py` also owns the typed post-provision binding for the shared allocation ID, both Compute instance and NIC identities, peer endpoint and absolute credential references, route-runtime identity, generation, and digests. VM role remains distinct from tunnel `ha_role`, and credential bytes never enter the manifest.
- Validation: Compare existing configuration golden outputs byte-for-behavior, validate exactly two stable members, and reject ambiguous or unsupported topologies before side effects.
- Rollback: Revert the additive schema and resolved-plan records; no persisted migration or compatibility wrapper is introduced.

#### Alternatives considered

- Reusing tunnel `ha_role` for VM ownership was rejected because tunnel selection is local to each VM and cannot express cloud fencing or shared allocation ownership.
- Inferring HA from `instance_count: 2` was rejected because existing multi-VM configurations are independent gateways and must remain unchanged.
- Supporting an arbitrary passive set was deferred because deterministic election and quorum semantics are not designed.

#### Implementation evidence

- `VPNGatewayConfig` keeps VM-level HA independent from tunnel `ha_role`, requires an explicit two-member topology, and preserves the existing per-instance plan when `vm_ha` is omitted or disabled.
- The resolved plan and post-provision binding carry stable cluster/node identities, the shared allocation and exact Compute/NIC identities, canonical route-table targets, generation and policy digests, peer endpoint, and absolute credential references without embedding credential bytes.
- Focused schema, loader, planning, template, and compatibility tests cover invalid topology, deterministic binding, and default-disabled behavior.

### TI-DES-002: Atomic generation store and authenticated peer state

- Status: implemented
- Requirements: TI-REQ-002, TI-REQ-005
- Selected approach: Add a narrow VM-HA state package that writes immutable revision directories, validates canonical JSON and checksums, fsyncs files and directories, atomically advances committed pointers, and exchanges authenticated monotonic peer observations through a concrete mutually authenticated transport.
- Boundaries and interfaces: The state package and `agent/state_store.py` own local durability; `agent/vm_ha/transport.py` owns bounded mTLS I/O, connects to the authoritative runtime private-IP endpoint, authenticates the TLS server against the stable configured node ID, and derives the peer node identity from the verified certificate URI SAN. A durable operation-and-generation apply lock is written and independently verified on both members before activation, blocks automatic failover through migration or update, survives crash and retry, and clears only after exact postconditions. Manifests carry only endpoint and credential-file references, and neither persistence nor transport may claim cloud ownership or decide promotion.
- Validation: Inject write, fsync, rename, truncation, restart, stale sequence, boot identity, authentication, and peer timeout failures.
- Rollback: Remove the additive state package and restore the prior state-store path before controller integration.

#### Alternatives considered

- Copying active-node files or runtime state was rejected because it creates a second configuration authority and cannot prove promotion compatibility.
- Using Object Storage or a new consensus service was rejected as unnecessary for the first fail-closed two-node design.
- Treating the append-only journal as consensus was rejected; it is recovery and audit evidence only.

#### Implementation evidence

- The canonical operator configuration remains the only configuration source; the runtime persists immutable generations, atomic committed pointers, controller checkpoints, transition journals, and effect receipts.
- The concrete bounded mTLS transport separates the dynamic private-IP connect address from the stable node-ID TLS server identity, verifies the exact node URI identity, rejects stale boot identities and replayed heartbeat sequences, and never treats peer state as cloud authority.
- Credential bundles are staged as immutable generations and revalidated close to use for canonical path, restrictive ownership and permissions, no-follow inode identity, certificate/key/CA validity, peer identity, and renewable Nebius credentials-file content.

### TI-DES-003: Strict cloud fencing and shared-allocation ownership adapter

- Status: implemented
- Requirements: TI-REQ-003, TI-REQ-005
- Selected approach: Give both members independent immutable primary private allocations and provision exactly one deterministic shared secondary private-alias allocation for an explicit VM-HA pair. Create the passive without changing the serving path, attach the alias additively only to the retained/configured active, and then stage and lock both members. Isolate Compute status plus exact alias detach, attach, and verification behind a strict adapter. Checkpoint before and after each idempotent side effect, bind every effect to an exhaustive normalized observation-path contract, persist accepted cloud-operation identities before bounded waits, and permit promotion success only after an authoritative stopped former owner and exact candidate ownership re-read.
- Boundaries and interfaces: `deploy/vm_manager.py` owns strict HA provisioning, stable double observations, the exhaustive effect registry, and the authoritative post-provision member/alias aggregate while preserving the retained active's Compute, disk, NIC, primary private, and public identities; its broad scaffold fallback remains available only to the ordinary non-HA path. `deploy/vm_ha_cloud.py` updates only `network_interfaces[].aliases`, preserves unrelated NIC state, journals the controller action and accepted SDK operation identities atomically, applies finite request/auth/retry/poll/overall deadlines, clears an accepted receipt only after explicit terminal success, and exposes typed stopped-state and alias operations plus a strict observation whose ownership revision is the exact attached candidate Compute `metadata.resource_version` read with the matching NIC alias. Allocation `resource_version` is not sufficient because assignment is status. Policy never imports SDK objects, and ordinary non-HA SDK calls retain their existing behavior.
- Validation: Use deterministic Compute and allocation fakes for every status, owner, API error, stale read, partial update, retry, and crash boundary.
- Rollback: Revert the dedicated adapter before controller wiring; existing non-HA provisioning remains the canonical fallback only for non-fencing operations.

#### Alternatives considered

- Promoting after missed heartbeats was rejected because a network partition cannot prove the former owner is unable to forward.
- Treating `Stopping`, `Error`, or an unavailable API as fenced was rejected because those observations do not prove the old data plane is inactive.
- Reusing broad SDK scaffold-mode fallback was rejected for fencing-critical calls because ambiguity must stop promotion.

#### Implementation evidence

- Explicit VM HA provisions one deterministic shared secondary alias and binds both member instances, NICs, and the initial owner while every member retains its own immutable primary address; ordinary non-HA provisioning retains its existing independent allocations and fallback behavior.
- The runtime uses bounded SDK calls, re-proves exact project/network/subnet/route-table membership, and rejects unavailable, transitional, error, foreign, stale, or changing ownership observations.
- Lifecycle v4 stores the trusted normalized cloud observation, pending effect's complete permitted path set, and accepted SDK operation identity. Quiescent v2/v3/v4 reads remain byte-preserving; a pending legacy effect is not guessed or rewritten, and older binaries cannot operate after the first v4 mutation.
- Promotion checkpoints typed transfer continuity across stop, detach, attach, confirm, routes, and enable: attach action, allocation, former/candidate nodes, generation/digests, ownership incarnation, and strictly advancing pre/post candidate revisions. Forwarding remains blocked until the former owner is authoritatively `Stopped`, its attachment is absent, the allocation is attached exactly to the candidate, and the candidate revision advances and is independently re-read.

### TI-DES-004: Owner-gated static and BGP route reconciliation

- Status: implemented
- Requirements: TI-REQ-004, TI-REQ-005
- Selected approach: Add a route-transition adapter that accepts already verified allocation ownership, renders static routes from the committed logical manifest, derives BGP readiness from the candidate's local FRR RIB, applies bounded takeover preservation and withdrawal rules, and persists a success receipt for the exact scheduled controller operation and complete reconciliation context.
- Boundaries and interfaces: `deploy/route_manager.py` continues to own VPC route operations, leaves the existing serving routes unchanged until active authority is proven, and compensates a failed managed-route replacement by restoring the exact removed route before reporting failure. `deploy/vm_ha_routes.py` owns transition policy and the managed-route ledger but never fences or infers ownership. `deploy/vm_manager.py` carries exact approval-bound route IDs, canonical managed names, revisions, prefixes, parents, and shared-alias next hops in both members' runtime bindings. `agent/vm_ha/runtime.py` may materialize an absent local ledger from that binding only after an exact independent cloud reread; it also owns the private route-mutation journal: v1 remains byte-preserving on read, while v2 stores the normalized rollback snapshot, mutation phase, and accepted delete/create/restore operation before the bounded wait. Restart follows the persisted phase, resumes only the same cloud operation, and clears the journal only after an exact postcondition plus durable ledger update. The agent runtime may re-observe route completion only when the receipt matches the exact operation ID, owner, alias allocation, attached-candidate ownership revision, generation, policy digests, route-runtime identity, and controller ownership incarnation. A durable receipt remains valid after the former owner rejoins as a guarded passive only when current alias ownership and every other bound identity still match. FRR and XFRM remain node-local authorities.
- Validation: Exercise wrong-owner denial, static and BGP readiness, allocation next-hop preservation, hold-down, stability observations, withdrawals, retries, partial failures, and non-HA regressions.
- Rollback: Revert the HA adapter and extensions; retain current conflicting-next-hop rejection for all non-HA configurations.

#### Alternatives considered

- Copying `ip route` or FRR RIB state from the active node was rejected because interface identities and learned reachability are node-local.
- Deleting all missing BGP routes immediately on promotion was rejected because normal convergence can transiently hide valid prefixes.
- Storing complete VPC route history locally was rejected because current VPC state plus committed static intent and local FRR truth are sufficient reconciliation inputs.

#### Implementation evidence

- Non-HA `_collect_remote_prefix_targets` retains its existing single-owner conflict checks.
- VM-HA planning expands committed logical intent across canonical exact route-table targets and fails the whole batch closed on foreign, undeclared, ambiguous, or changing targets before mutation.
- The concrete route runtime revalidates current cloud ownership and target membership, derives BGP readiness from current FRR/XFRM truth, applies bounded takeover preservation, and accepts only a freshly observed durable receipt bound to the complete controller operation and ownership context.

### TI-DES-005: Pure fail-closed VM-HA controller

- Status: implemented
- Requirements: TI-REQ-003, TI-REQ-004, TI-REQ-005
- Selected approach: Implement one deterministic controller over injected clock, versioned checkpoint persistence, peer, cloud, route, forwarding, and service-health ports, with an unconditional cold-start gate, a separate blocked-mode local-render authority, durable transfer continuity, and explicit normal, suspect, fencing, transfer, detach/reattach reproof, promotion, active, degraded, and blocked transitions. Local rendering establishes current-generation readiness but never grants active effects.
- Boundaries and interfaces: The boot guard blocks forwarding, cluster tunnel initiation, firewall mutation, route reconciliation, allocation transfer, and VPC effects before fresh authority exists. A narrow renderer may materialize and syntactically validate generation-owned strongSwan, FRR, and XFRM configuration while the guard remains blocked; the controller alone may enable a freshly proven passive or active data-plane mode, and adapters own typed observations and gated effects. After passive authority is durable and forwarding is fenced, passive preparation rebuilds UFW from the exact resolved peer set before routing hygiene, so bootstrap-wide IPsec rules cannot persist on the standby. Active preparation performs the same exact firewall rebuild before owner-only BGP origination and forwarding. The StrongSwan renderer owns only product-generated plugin settings: it removes the exact obsolete managed `xfrm_if.conf`, preserves a caller-owned file at that path, and disables StrongSwan's unrelated ClusterIP HA plugin while continuing to bind kernel XFRM interfaces through `if_id_in` and `if_id_out`.
- Validation: Use table-driven traces for healthy operation, clean two-node bootstrap, passive non-forwarding rendering, cold boot, process restart, automatic Compute recovery, stale passive, generation drift, heartbeat loss, dual suspicion, fencing failure, API outage, allocation races, route failure, and restart at every checkpoint. Negative controls must prove blocked rendering cannot enable forwarding or any cloud, firewall, tunnel-initiation, or route effect.
- Rollback: Remove the controller before CLI and service integration; additive lower-level ports remain inert while VM HA is disabled.

#### Alternatives considered

- Distributing policy across heartbeat, VM, route, and CLI callbacks was rejected because hidden temporal coupling would make recovery and split-brain reasoning unreliable.
- Requiring active or promotion readiness before materializing the local configuration needed to measure readiness was rejected because it creates a clean-bootstrap dependency cycle.
- Automatic failback was rejected because it adds an avoidable second ownership transfer during recovery.
- Allowing promotion with partial readiness was rejected; the safer outcome is a visible outage with an explicit blocked reason.

#### Implementation evidence

- `RecoverableController` implements deterministic suspicion, fencing, transfer, promotion, active, degraded, blocked, recovery, and manual-failback transitions over typed ports and durable checkpoints.
- Controller checkpoint v2 reads v1 without rewrite. Pending legacy attach can recover its starting revision, later legacy transfer states without durable continuity stay passive and re-enter through exact candidate detach/reattach, and stable pre-existing active state is retained without inventing transfer history.
- The VM-HA systemd unit establishes a current-boot blocked guard before releasing strongSwan, FRR, or the ordinary agent; failure, shutdown, stale readiness, or a durable active-state write failure restores the guard and rolls forwarding back.
- The production default factory composes the real cloud, peer, readiness, route, state, and effect ports only for an explicit VM-HA manifest. Omitted or disabled VM HA retains the existing non-HA service path.
- Focused preparation tests prove exact firewall reload precedes passive routing hygiene while forwarding is off. Renderer tests prove upgrade cleanup removes only the obsolete generated `xfrm_if` request, preserves caller-owned configuration, and emits the explicit unused-HA-plugin disablement; live startup had previously shown both warnings despite healthy kernel XFRM policies and established SAs.
- Live standby reboot exposed a bounded startup race in which systemd reported
  StrongSwan active before VICI accepted requests. The blocked guard now stops
  only the exact active StrongSwan unit when connection unloading is not yet
  observable, proves it inactive, and lets passive materialization restart it
  from the current generation. Renderer readiness now connects to the Unix
  socket instead of accepting a stale socket pathname.

### TI-DES-006: Passive-first apply and operator integration

- Status: implemented
- Requirements: TI-REQ-001, TI-REQ-002, TI-REQ-003, TI-REQ-004, TI-REQ-005, TI-REQ-006
- Selected approach: Select a cluster-aware apply path only for explicit VM-HA configuration. First classify topology and render an exact desired/current-state mutation and rollback document; ordinary-to-HA conversion requires interactive confirmation or its exact `--approve-vm-ha-migration DIGEST`, interrupted recovery requires a non-interchangeable `--recover-vm-ha-migration DIGEST`, and `--dry-run` is read-only. Reobserve immediately before mutation, persist and reread `PROVISIONING`, retain the active and its immutable addresses, create the passive with its own primary plus one shared secondary alias, and validate each Compute create against the submitted disk, single NIC, project, subnet, primary/public allocations, and pre-existing aliases. If that newly created passive cannot pass the SSH/bootstrap gate before staging begins, render one domain-separated replacement digest over the lifecycle predecessor, original migration approval, desired generation, exact active/passive resource observations, preserved allocations, shared-alias owner, routes, and passive-only action list. The explicitly approved replacement appends intent and accepted-operation receipts, retires only the exact failed passive Compute and task-created disk, keeps historical bindings immutable, appends replacement bindings, and then resumes normal provisioning with the same allocations and corrected enrollment cloud-init. An interrupted `ACTIVATING` transaction that was externally restored to the exact configured-active owner uses a separate approval document binding the lifecycle predecessor and normalized current cloud observation; only an exact identity-preserving, host-effects-only case may append a replacement `PROVISIONING` successor and re-enter the canonical passive-first workflow. Stage passive then active, install both exact-generation apply locks, install one current-owner adoption declaration bound to the owner lock and authoritative runtime binding, and activate both behind the guard. When a same-observation retry inherits a later host-only activation effect from a pre-adoption apply, durably rewind only that incomplete host effect, preserve every cloud binding, lock, and completed effect, journal adoption, and then replay the interrupted verification; never rewind a cloud effect or accepted cloud operation. The agent combines that declaration with an independent exact cloud-owner read to discard only a redundant current-generation takeover lineage, establish controller ownership continuity while forwarding remains fenced, and later write an `apply-owner-adoption` terminal promotion receipt after current-generation route and forwarding proof. The declaration cannot move the allocation, reconcile routes, or enable forwarding by itself. Keep `ACTIVATING` while releasing and proving the active's exact alias ownership and route receipt, then release and prove the passive unlocked and non-forwarding; write `ACTIVE` last. If that final write reports failure, accept only the exact `ACTIVE` successor after fresh node proof, or the exact `ACTIVATING` predecessor after passive-first then active relocking and verification; the passive must be non-forwarding while an already-active exact owner may continue forwarding only with the exact current route receipt. Unknown state is unsafe. Route timeouts resolve by exact reread and stable-key replay, and compensation restores the original only after terminal desired-create failure plus proof that the desired route is absent. Before reconciliation, the route runtime may retire a local ledger entry only when target-reverified, identical consecutive cloud listings prove the exact route identity absent; this changes local authority state only and never deletes a cloud route.
- Boundaries and interfaces: `cli.py` parses digest-bound migration, interrupted-migration recovery, externally fenced activation recovery, and failed-passive replacement approvals; performs topology classification and no-mutation previews; serializes explicit HA apply by canonical project and gateway; verifies only the retained active over SSH for the exact replacement lane while treating the passive as a new enrollment target; routes an unchanged exact v4 `ACTIVATING` retry to a dedicated non-provisioning recovery path; and admits the replacement recovery transaction only after exact desired-state, resource-binding, cloud-owner, alias, revision, pending-effect, and host-only-effect validation. Its approval envelope binds the lifecycle predecessor and embeds the trusted normalized cloud observation used by `VMManager` as the mutation precondition; legacy approvals without that observation retain their existing raw-digest check. For manual failback, `cli.py` reads the exact `ACTIVE` lifecycle and two-sided cloud state; when the configured-passive member is the running owner and the configured-active member is stopped and alias-free, it starts only that exact request target through `NebiusSDKCloudClient.start_instance` with a resource-revision-bound idempotency key, continuously reproves unchanged ownership, and waits for pinned SSH before issuing the existing on-node request. It performs no alias, route, or forwarding effect. Status convergence remains separate from malformed or foreign state, and one strict pinned-SSH policy covers staging, status, recovery, failback, locks, and deactivation. Ordinary apply with no valid lifecycle record never probes HA Compute, VPC, allocation, SSH, or runtime state. `deploy/vm_ha_lifecycle.py` owns the single fsynced v4 transaction, monotonic revision/predecessor CAS, fill-once historical and replacement bindings, path-level observation guards, accepted operation identity, strict byte-preserving v2/v3 reads with safe mutation-time successors, and the one structural `ACTIVATING`-to-`PROVISIONING` recovery successor. `deploy/vm_manager.py` owns exact retained-member preservation, cloud resource-version preconditions, shared-allocation shape/provenance validation, bounded stable HA rereads, exact Compute-create footprints, alias-only NIC mutation, and the dedicated failed-passive replacement method. It also emits only the exact route-authority label subset needed by status; complete foreign-cluster authority is ignored while partial or current-cluster drift remains blocking. The replacement method never calls the all-member recreation helper: it proves active/allocation/route invariance, deletes only the exact receipt-bound passive Compute and task-created disk, proves preserved allocations detached, recreates the passive with the same allocations and corrected enrollment cloud-init, and returns the normal runtime binding. The effective passive identity resolves to replacement bindings only after the matching retirement and replacement effects are complete. Unchanged activation-resume reconstruction still uses two stable observations plus the persisted allocation, members, route targets, and runtime binding and never calls `ensure_group`, provisioning finalization, or a second `ACTIVATING` transition. `deploy/route_manager.py` owns owner-gated reconciliation, stable idempotency identities, target-reverified stable absent-ledger retirement, outcome resolution, and separately keyed compensation. `deploy/ssh_push.py` owns atomic root-only apply locks and exact receipts. `agent/main.py` validates lock identity, strictly parses checkpoint types, migrates controller checkpoint v1 to v2 conservatively, persists a secret-free blocked projection after an effect failure when possible, and overlays the live writer inhibition and guard mode on status reads. Supported ordinary customer contracts remain unchanged.
- Validation: Run lifecycle record integrity and transition-tamper tests; replacement approval, stale-digest, exact-resource, active/allocation/alias/route-preservation, crash-replay, and foreign-resource tests at every passive retirement and recreation boundary; default-disabled operator-permission and service-account ordering tests; current-marker, pre-marker, retained-member, ordinary, mixed, denied, and incoherent discovery tests; exact allocation and repeated identity revalidation tests; two-consecutive ordinary-apply idempotency tests; CLI, exact-pin SSH, deactivation, IAM, systemd, packaging, build, and release tests; and the non-HA golden plus offline two-node apply/status/recovery traces.
- Rollback: Revert the integration layer; the schema remains inert when VM HA is omitted and no migration is required.

#### Alternatives considered

- Active-first apply was rejected because a failed second-node stage would leave the serving node ahead and silently remove failover readiness.
- Trust-on-first-use, disabled host verification, and separate OpenSSH/Paramiko trust defaults were rejected because they make first deployment vulnerable to host substitution and produce path-dependent identity checks.
- Replacing existing commands or defaults was rejected because VM HA is additive and current users require supported behavior to remain stable.
- Unverified broad IAM grants were rejected; exact actions and role mappings must be documented before a live trial.

#### Implementation evidence

- Explicit VM HA now follows the provision-bind-stage-activate path, installs immutable credential bundles separately from secret-free manifests, verifies the current remote generation and guard/controller readiness, and aborts on the first critical remote failure.
- `status` and `failback vm` expose or use the same durable controller authority; manual failback is a fenced request, not a direct role or route rewrite.
- Live static failover exposed that the configured-active request target is
  normally stopped after promotion. The operator now starts only that exact
  alias-free Compute after proving the configured-passive member remains the
  running owner, rereads the same invariants throughout startup, and waits for
  pinned SSH before submitting the unchanged fenced failback intent.
- Removing VM HA chooses the requested service-account credential before lifecycle-bound discovery and performs no HA-specific discovery when no valid lifecycle exists. Exact recorded allocation, Compute, NIC, owner, route, and repeated identity proof precede deactivation.
- Whole-record lifecycle integrity binds status and identity, forbids cached reversal, checkpoints removal before mutation, and writes a terminal `REMOVED` tombstone only after both members verify non-HA. Rejected confirmation and partial evidence leave the pair untouched, while a second ordinary apply performs no teardown.
- The migration lifecycle is one v4 CAS transaction spanning `PROVISIONING`, `ACTIVATING`, `ACTIVE`, removal, and the `REMOVED` tombstone. It checkpoints service-account preparation, allocation and member provisioning, alias attachment, route/runtime binding, staging, locks, activation, and both unlock proofs; legacy v2/v3 records are read without mutation and only quiescent approved state receives a guarded v4 successor at the next effect.
- Live replay proved that creation of an unattached shared allocation adds an
  explicit `owner: null` observation leaf. The shared-allocation effect guard
  now permits that exact leaf while the existing allocation-shape validator
  still requires no owner; unrelated changes remain blocked. Guard failures
  report only unapproved observation path names, never cloud values, so an
  interrupted v4 checkpoint can be diagnosed and resumed without reset. A
  checkpoint written by the defective guard receives the same exact scalar-leaf
  allowance during read; an attached-owner object still normalizes to forbidden
  child paths and fails closed.
- The current Nebius operation service may return typed `UNIMPLEMENTED` for an
  accepted operation lookup. VM-HA cloud and route effects treat only that
  exact code as a lookup-capability boundary: they resubmit the same mutation
  with the persisted idempotency key, require the same operation ID, and still
  prove the exact resource postcondition before completing the journaled
  effect. Other lookup failures remain fatal.
- Exact typed `ALREADY_EXISTS` responses for deterministic private or public
  allocations are recoverable only through an exact-name reread and complete
  project, subnet, address, shape, state, and unattached-owner validation.
  SDK enum observations use their symbolic names, and a standalone allocation
  binding may be temporarily unobservable through the aggregate member view
  only before that member Compute exists; the following resource-specific SDK
  reread remains mandatory.
- Crash replay of an already completed lifecycle effect accepts only an exact
  persisted resource-binding match and closes the matching journal entry
  without reopening its consumed observation guard.
- Provisioning replay accepts only strictly advancing numeric Compute revisions
  when the exact Compute, NIC, allocation, address, and alias identities remain
  unchanged; rollback, nonnumeric revision, or identity drift still fails
  closed. SDK message cloning preserves nested and repeated fields for both
  supported generated-message representations and rejects unknown layouts.
- Fresh-member SSH enrollment now proves that the selected mode-`0600`
  management private key derives the configured public key before any cloud
  mutation. The rendered cloud-init validates `sshd`, resets failed SSH units,
  activates the image's socket- or service-based SSH model, and requires a
  live port-22 listener before bootstrap can complete.
- An interrupted `ACTIVATING` retry rebuilds its runtime view only after two identical authoritative observations and exact lifecycle/resource-binding checks, then re-enters host staging and verification without replaying any cloud provisioning effect.
- A separately approved, externally fenced activation recovery appends one digest-linked `PROVISIONING` successor only when both member identities and all cloud/runtime/route bindings remain exact, revisions only advance, the configured active alone owns the shared alias, no cloud operation is pending, and every incomplete activation effect is host-only. The supported retry then reused both Compute instances and allocations, staged and locked the standby first, released the exact configured active only after route reconciliation, released the passive last, and wrote `ACTIVE` after independent proof.
- Activation polling retries only well-formed same-node generation, apply-lock, and predicate staleness. Malformed/foreign status and foreign operation identities abort immediately, while final lifecycle-write ambiguity is resolved from exact record hashes and independent node proof.
- Live replay of an interrupted activation proved the owner lock was removed before route reconciliation failed on a cloud-absent local ledger identity. Stable absent-ledger retirement let the ordinary strict-pinned apply resume without a cloud route deletion; both lock releases, exact route authority, managed mTLS, owner forwarding, and standby readiness then converged. Live status projected the current writer state rather than the earlier successful snapshot and ignored a fully labeled route owned by another cluster.
- The deactivation path disables the agent, health monitor, route-fix timer/service, IPsec, and FRR on a retired member. The reviewed IAM mapping remains action-derived rather than accepting caller-selected broad roles.
- The composed production factory has offline restart coverage across every controller effect boundary; final cross-component acceptance and legacy golden coverage remain owned by TI-DES-007.

### TI-DES-007: Deterministic two-node safety and compatibility proof

- Status: implemented
- Requirements: TI-REQ-001, TI-REQ-002, TI-REQ-003, TI-REQ-004, TI-REQ-005, TI-REQ-006
- Selected approach: Build composed offline tests with fake time, two agents, peer transport, filesystem faults, Compute, allocations, routes, FRR, XFRM, and forwarding, and retain a golden non-HA execution trace.
- Boundaries and interfaces: New integration tests own cross-component sequencing evidence; focused unit suites retain adapter and policy coverage; live validation remains a separately authorized product trial.
- Validation: Assert ordered traces and final state for retained-active ordinary-to-HA migration, alias-only NIC updates, clean two-node bootstrap, passive blocked rendering, both-member apply locks, route-replacement compensation, normal failover, stale passive, cloud ambiguity, path-level unrelated drift, accepted-operation restart, crash before/after attach confirmation, conservative legacy checkpoint reproof, typed stale/foreign status, final `ACTIVE` write ambiguity and passive-first relocking, hold-down, resynchronization, manual failback, lifecycle-bound removal, SSH trust and identity mismatch handling, and omitted VM-HA behavior. Keep these safety-critical composed cases and the canonical all-source mypy gate selected by the ordinary automated CI path; keep build jobs mutually exclusive so each lane builds once.
- Rollback: Remove only new test fixtures when all dependent implementation is reverted; never weaken negative safety expectations to retain a feature path.

#### Alternatives considered

- Adapter unit tests alone were rejected because they cannot prove absence of forwarding or route cutover before fencing and ownership confirmation.
- Wall-clock and live-cloud tests were rejected for the implementation gate because they are non-deterministic and no environment mutation is authorized.
- Reporting offline proof as live readiness was rejected; a later trial must independently observe cloud and data-plane postconditions.

#### Implementation evidence

- Deterministic two-node tests cover normal operation, heartbeat loss, stale generation parity, fencing failure, cloud outage, route hold-down, resynchronization, and manual failback using fake time and shared cloud truth.
- Crash injection after each takeover effect plus checkpoint persistence failure proves restart resumes the same operation without duplicating effects or enabling forwarding before exact ownership and route completion.
- Omitted and explicitly disabled VM HA retain the ordinary plan, while instance count and public-allocation shape do not infer VM HA. This completes the offline acceptance gate only; live readiness still requires a separately authorized non-production trial.

### TI-DES-008: Isolated GCP fixture and live VM-HA acceptance workflow

- Status: implemented
- Requirements: TI-REQ-007
- Selected approach: Extend the existing GCP helper with an explicit, additive VM-HA mode that plans one regional HA VPN gateway, one Cloud Router, two Nebius peer public IPs, and four tunnel/BGP sessions. Keep the existing mode byte-for-byte compatible at its public boundary. Freeze and validate the fixture before declaring a product trial, run the source candidate through its digest-approved HA migration path, and use cloud and host reads independent of the product status renderer for acceptance.
- Boundaries and interfaces: `misc/gcp-vpngw.sh` owns idempotent GCP fixture planning/apply/status and emits product configuration fragments without secrets; deterministic fake-`gcloud` tests own its API-call contract. GCP external VPN gateway resources are peer representations and do not alter the one-regional-HA-gateway invariant. `cli.py status` owns an additive VM-HA authority panel while retaining ordinary output. `agent/main.py` emits one structured secret-free start/completion/failure event around each controller effect, using monotonic duration and durable operation identity without exception text or cloud payloads. The existing lifecycle, cloud adapter, controller, route manager, and SSH staging boundaries remain authoritative for product-owned migration, fencing, alias transfer, route reconciliation, and failback. An opt-in runbook owns declarations, fixture checkpoints, independent observations, recovery classification, concurrent component journals, workload-VM ping sequence accounting, and rollback steps.
- Validation: Prove the legacy two-tunnel helper trace, the explicit four-tunnel plan, reversed peer-interface mappings required by the current GCP API shape, unique link-local ranges, active/passive advertised priorities, idempotent reruns, incompatible-resource rejection, and read-only status behavior. After offline gates, inventory the live resource graph, create the fixture additively, run a clean steady-state trial, trigger automatic failover through the product service boundary, and run manual failback separately. Verify former-owner `Stopped`, exact candidate alias ownership and Compute revision, owner-only forwarding, stable Nebius route next hops, GCP route preference, and bidirectional traffic at each terminal state.
- Rollback: Preserve the pre-trial resource inventory and config, stop at any ambiguous ownership or resource-shape observation, restore the last independently proven active owner through the supported fenced workflow, and remove only exact task-created GCP peer/tunnel/router-interface/BGP resources after dependency checks. Do not delete a second named resource until its regional-gateway versus external-peer type and dependents are known.

#### Alternatives considered

- Keeping two regional GCP HA VPN gateways was rejected because the selected multi-VM topology requires one regional HA gateway and four tunnels to the two Nebius peers.
- Using only two total tunnels was rejected because it does not exercise both GCP HA interfaces against both Nebius members or the requested member-level preference groups.
- Treating one GCP advertised-route priority as a bidirectional active/passive control was rejected because Cloud Router advertised priority affects routes sent toward Nebius; routes learned by GCP use their received BGP attributes.
- Externally stopping the active Compute instance as the failover stimulus was rejected for the clean product trial because it pre-satisfies the controller's required fencing transition. Stopping the active product controller service preserves the guarded data plane while allowing the peer to own the stop, transfer, and promotion chain.

#### Implementation evidence

- The GCP helper now delegates explicit VM-HA planning to a typed planner that
  preserves the legacy two-tunnel mode while adding exact four-tunnel topology,
  member-grouped advertised priorities, distinct link-local ranges, strict
  existing-resource validation, non-mutating status and dry-run behavior, and
  anonymous inherited-file-descriptor transport for environment-backed PSKs.
- VM-HA apply preflights all four PSKs before the first GCP mutation. For an
  explicit migration, `--psk-source-config` can reuse exactly four secrets from
  one private, regular, non-symlink VPNGW YAML file, selects the exact named
  connection and tunnels independent of YAML order, and removes the complete
  secret set from every `gcloud` child environment. Secret values never enter
  the process argument list or output; the helper fails on unsafe permissions,
  environment/source ambiguity, malformed topology, or identity drift.
- Generated GCP resource names preserve their distinguishing suffixes at the
  63-character limit. Existing regional gateways, tunnels, router interfaces,
  and BGP peers are adopted only after exact interface, IKEv2, ASN, priority,
  address, and binding validation; malformed observations fail as bounded
  helper errors rather than tracebacks or partial adoption.
- Ordinary status preserves its non-HA path and adds persisted lifecycle plus
  independently read Compute, NIC, shared-alias, and route authority for managed
  VM-HA gateways. Missing shared-alias routes block route-authority proof.
- Automatic and manual transfer effects now emit bounded structured lifecycle
  events for start, completion, and failure. Successful events record
  monotonic elapsed time; failure events expose only the error type so
  credentials, cloud values, and tunnel secrets cannot enter the journal.
- Deterministic fake-`gcloud` and focused status tests pass together with 812
  unit tests, 29 integration tests, Ruff, mypy, Bash syntax, ShellCheck, workflow
  parsing, wheel build, and diff-integrity checks.
- The authorized non-production fixture converged with one GCP regional HA VPN
  gateway, one Cloud Router, four established dynamic tunnels, four established
  BGP sessions with member-grouped priorities `0,100,100,0`, and one retained
  Classic gateway with two established static tunnels. The exact obsolete
  second regional gateway dependency set was removed only after inventory and
  dependency checks.
- A supported two-node apply produced one generation-identical active owner and
  one non-forwarding passive member. Independent cloud reads proved the shared
  alias and both managed Nebius VPC routes belonged only to the active Compute;
  host reads proved forwarding `1/0`, exact peer-only mTLS firewall rules, and
  established IPsec/BGP state on both members.
- The clean BGP automatic-failover trial restored five sustained workload-VM
  replies after 177.020 seconds GCP-to-Nebius and 176.890 seconds
  Nebius-to-GCP, losing 833 and 817 probes respectively at 5 Hz. The separate
  supported manual-failback trial restored five sustained replies after about
  181 seconds in each direction, losing 852 and 836 probes. Both trials proved
  the former Compute `Stopped`, exact alias transfer and re-read, route receipt,
  then forwarding in that order; intervened recovery attempts were excluded.
- The clean static automatic-failover trial used real workload VMs and the
  retained Classic fixture. It restored five sustained replies after 171.914
  seconds GCP-to-Nebius and 171.884 seconds Nebius-to-GCP, losing 815 and 799
  probes respectively at 5 Hz, then delivered 959 further replies in each
  direction without another loss. Independent route reads proved the exact
  Classic XFRM path and shared-alias Nebius route authority.
- Final failback left the configured-active member as the exact running owner
  and the configured-passive member running in passive mode. A controlled
  passive reboot under the VICI readiness fix produced no stale-socket guarded
  error and two simultaneous 400-packet workload probes completed with zero
  loss. The retained 0600 operator YAML contains only environment-backed PSK
  references backed by a separate private 0600 credential bundle.

### TI-DES-009: Current Nebius runtime identity enrollment

- Status: implemented
- Requirements: TI-REQ-006, TI-REQ-007
- Selected approach: Keep `apply --sa NAME` as the explicit identity-selection boundary, but replace the legacy IAM scaffold with the pinned SDK's generated service clients. Select or create the exact named service account and a same-name dedicated custom group, require the account to be the group's only member, and require the group to have exactly one `editor` access permit whose resource is the configured project. Obtain a short-lived token by invoking the installed Nebius CLI's supported service-account impersonation option with captured output, a finite timeout, and no token logging or persistence. The one renewable authorized-key credential used by the VM runtime is enrolled and staged separately; `--sa` never creates an additional key.
- Boundaries and interfaces: `vpngw_sa.py` owns exact IAM discovery, idempotent creation, operation waits, complete paginated membership/permit rereads, closed role validation, and token capture. `cli.py` treats an explicit `--sa` as authoritative for ordinary and VM-HA apply and aborts before cloud discovery or mutation when selection or impersonation fails. Generated `ServiceAccountServiceClient`, `GroupServiceClient`, `GroupMembershipServiceClient`, and `AccessPermitServiceClient` calls are the only IAM mutation path. The current Nebius permission model, not caller input, fixes the reviewed role to plain project-scoped `editor`; nonexistent `compute.editor`, `vpc.editor`, and `roles/editor` spellings are invalid.
- Validation: Unit-test existing and create paths with SDK-shaped fakes, paginated exact rereads, idempotent operation waits, role/resource/member drift, ambiguous or unavailable lookup, CLI timeout/nonzero/empty-token handling, token secrecy, and ordinary plus VM-HA no-fallback behavior. Independently inventory the authorized non-production project before and after normalization, prove exactly one project permit and one authorized key, then use the runtime identity in the clean live trials.
- Rollback: Stop before cloud mutation on any enrollment ambiguity. For the authorized fixture, restore only the previously inventoried exact permits if rollback is required; never delete a service account, group, membership, key, or unrelated permit as an implicit application rollback.

#### Alternatives considered

- Renaming only the role allowlist was rejected because the existing implementation also calls a legacy `client.iam()` facade and token-creation method absent from the pinned SDK.
- Keeping permissive non-HA fallback was rejected because an explicit `--sa` would claim one identity while silently performing the operation with broader ambient credentials.
- Creating a second authorized key during `--sa` was rejected because apply needs only a short-lived impersonated operator token and the runtime already consumes one separately enrolled renewable credential.
- Automatically deleting foreign group members or extra permits was rejected because normalization must not turn an ordinary apply into an unapproved IAM revocation workflow.

#### Implementation evidence

- Live role and access-permit probes in the authorized non-production project proved that project-level `editor` covers the required Compute and VPC mutations, while permits scoped directly to the route table and VPC network are unsupported.
- The pinned `nebius` Python SDK exposes generated IAM service clients and service-account credential readers but no `client.iam()` facade or `create_for_service_account` token method. The installed Nebius CLI exposes service-account impersonation as the supported short-lived operator-token boundary.
- The dedicated runtime service account, same-name group, sole membership, and
  exactly one authorized key exist in the fixture. Its permissions were
  normalized to one project-scoped `editor` permit, and read-only CLI
  impersonation successfully listed project instances. The source now uses the
  current generated SDK clients, exact paginated rereads, current role spelling,
  supported CLI impersonation, and fail-closed explicit `--sa`; 83 focused IAM
  and CLI tests plus targeted Ruff and mypy pass. The authorized live apply,
  failover, failback, and final status reads used this runtime identity without
  an ambient-credential fallback.

### TI-DES-010: Owner-bound repair-before-promote controller lane

- Status: implemented
- Requirements: TI-REQ-005, TI-REQ-006, TI-REQ-008
- Selected approach: Extend the pure VM-HA policy with explicit `DEGRADED_PATH`, `REPAIRING`, `REPAIR_EXHAUSTED`, and unsafe-local-authority outcomes. A fresh unhealthy owner receives one persisted monotonic five-second repair attempt only while fresh local and cloud observations bind the same cluster, allocation, owner revision, generation, boot, route authority, and initial failure fingerprint. Reserve the final second for a verified forwarding fence, require two complete healthy observations for success, and retain the consumed attempt until sixty seconds of continuous health or a new authoritative ownership incarnation. Repair exhaustion does not authorize promotion: the passive still performs the existing strict Compute-stop and allocation-transfer sequence.
- Boundaries and interfaces: `agent/vm_ha_controller.py` owns pure classification, repair admission, the immutable five-second deadline, flapping, candidate-readiness, and transition policy. `agent/vm_ha/runtime.py` owns the single bounded node-local repair port, canonical owner re-enable path, and an emergency forwarding fence that deliberately bypasses the ordinary routing lock; it never performs cloud or VPC-route effects. Heartbeat v1 remains unchanged: its existing service, route, and promotion-readiness flags distinguish a fresh unhealthy peer from a missing heartbeat without making repair state remote authority. `agent/main.py` owns checkpoint-v4 persistence with strict v1-v3 migration, remaining-budget command bounds, state-sensitive evaluation cadence, status, and structured effect timing. In VM-HA mode the tunnel monitor is observer-only; non-HA monitoring and explicit manual restart behavior are unchanged. The existing systemd stop and `ExecStopPost` guard remains authoritative for process shutdown. A short systemd watchdog is intentionally absent because the same controller also executes legitimate cloud operations that can exceed the local repair deadline.
- Validation: Injected monotonic clocks and bounded command runners prove one-attempt persistence, the four-second repair cutoff plus one-second fence reserve, a final deadline check after nominal command success, two-sample recovery, sixty seconds of continuous-health reset, fingerprint-churn/flapping behavior, direct emergency fencing without the routing lock, XFRM-before-BGP repair selection, and checkpoint-v1 through v4 migration. Prefix-aware readiness proves that one missing redundant BGP session remains forwarding only when all required prefixes retain learned and usable-XFRM coverage; loss of the sole usable path blocks readiness. Controller and composed two-node tests retain the strict stopped-former-owner transfer chain and prove that repair effects have no cloud or VPC-route authority. The focused controller, runtime, route, checkpoint, monitor, CLI, and composed failover matrix passes offline; the repair fault matrix has not yet been remeasured live.
- Rollback: Disable the repair branch and restore the former controller cadence without changing heartbeat v1, the strict Compute-stop transfer chain, allocation identity, route receipts, or forwarding guards. Checkpoint readers retain v1-v3 migration; a consumed v4 repair attempt remains fail-closed until the current owner is re-observed. Non-HA monitoring and public commands require no rollback.

#### Alternatives considered

- Immediate VM promotion for every unhealthy service was rejected because a local FRR, StrongSwan, XFRM, or forwarding repair can finish well before the existing cloud fencing and allocation-transfer path and because a common remote-side failure may leave the candidate equally unready.
- Repeated or component-specific long repair loops were rejected because they can mask flapping, race fencing, and indefinitely postpone a required transfer.
- Extending heartbeat v1 or adding a second repair report was rejected because local repair does not need remote authority; the existing authenticated health flags let a recovered owner cancel suspicion while mixed-version peers retain conservative fencing.
- Keeping the legacy tunnel monitor as an independent VM-HA repair writer was rejected because concurrent restarts defeat one-attempt accounting and can mutate tunnel state after the controller has fenced.
- Adding a three- or four-second systemd watchdog was rejected because it cannot distinguish a wedged local repair from a legitimate longer cloud fencing or allocation operation owned by the same process. Emergency local fencing remains direct and bounded; controller-hang watchdog separation requires a dedicated process boundary.

#### Implementation evidence

- The controller persists `RepairAttempt` and the repair action before execution, consumes an exact effect receipt, and never renews its original deadline. The runtime bounds each repair command to the remaining pre-fence budget, rejects nominal success observed after the cutoff, and directly disables and verifies forwarding on failure or timeout. Structured effect events retain the secret-free attempt identity, failure fingerprint, healthy-observation count, and remaining time, while public status reduces that evidence to aggregate and member readiness semantics. BGP readiness now uses required-prefix and usable-XFRM coverage rather than demanding every configured neighbor, and the VM-HA tunnel monitor is observer-only for both resolved and operator-facing config shapes. The final offline gates pass with 826 unit and 29 integration tests plus Ruff, mypy, changed-document Markdown, diff, and secret-signature checks; no new live latency or packet-loss claim is made for this optimization.

### TI-DES-011: Role-bound planned VM ownership transfer

- Status: superseded
- Requirements: TI-REQ-007, TI-REQ-009
- Selected approach: Add a distinct `vm-ha-failover` command whose operator-side preflight proves the exact configured-active owner and running alias-free configured passive, then writes a private planned-transfer request only on the configured passive. Feed that request into the existing pure controller transfer intent so it bypasses only fresh-peer suppression and suspicion timing while reusing the complete automatic failover effect chain.
- Boundaries and interfaces: `cli.py` owns exact lifecycle/member/cloud preflight, pinned SSH targeting, response schema and identity checks, and passive-only command routing. `agent/main.py` owns strict request persistence, role/generation validation, conflict rejection, runtime snapshot wiring, and request consumption only after exact local ownership. `agent/vm_ha_controller.py` owns role-confusion rejection and the single manual-transfer policy input; all former-owner Compute stop, allocation transfer, ownership confirmation, route reconciliation, and forwarding actions remain in their existing authoritative adapters.
- Validation: Unit tests cover accepted and rejected preflight shapes, exact request identity, conflicting intents, role confusion, post-promotion consumption, and healthy-peer bypass. A composed two-node test requires the exact canonical takeover effect order. Full unit/integration, Ruff, mypy, CLI-help, diff-integrity, and changed-scope security gates complete the offline proof; the live trial supplies independent workload and cloud postconditions.
- Rollback: Remove the additive public command, request file, and manual-failover snapshot input. The unchanged automatic failover and manual failback paths continue to use the canonical controller and no persisted configuration or lifecycle format changes.

#### Alternatives considered

- Reusing tunnel-level `failover` was rejected because it does not transfer VM ownership and would conflate two supported public contracts.
- Stopping the active Compute directly from the CLI was rejected because it would bypass the controller-owned fenced transition and contaminate the product proof.
- Reusing the failback request on the opposite role was rejected because distinct role-bound schemas make stale or confused operator intent fail closed.

#### Implementation evidence

- The additive command and private request were deployed through the supported VM-HA apply workflow. A clean planned failover restored five sustained replies after 21.864 seconds GCP-to-Nebius and 22.052 seconds Nebius-to-GCP, losing 67 probes in each direction at 5 Hz. Independent postconditions proved the former Compute stopped, exact candidate attachment and ownership re-read, reconciled routes, owner-only forwarding, and no apply lock.
- The separate existing `vm-ha-failback` workflow restored five sustained replies after 242.218 and 242.275 seconds, losing 860 and 844 probes respectively. Those times include safe startup and pinned SSH readiness of the stopped configured-active request target before the controller transfer.
- A separate automatic-failover trial stopped only the active product controller service. It restored five sustained replies after 155.992 and 156.125 seconds, losing 751 and 737 probes respectively, while independently proving the same stopped-former-owner and exact ownership chain.

### TI-DES-012: Exact configured-passive standby rearm

- Status: superseded
- Requirements: TI-REQ-007, TI-REQ-010
- Selected approach: Add a separate `vm-ha-rearm` command that starts only the exact stopped, alias-free configured passive while continuously proving the configured active remains the running exact shared-allocation owner. After pinned SSH readiness, poll the existing non-bypassing recovery/status path until the configured passive is a normal non-owner with passive data plane and no apply lock.
- Boundaries and interfaces: `cli.py` owns lifecycle/member binding, allocation/attachment/Compute observations, revision-bound start idempotency, owner continuity checks, pinned SSH, passive-only status polling, and terminal classification. `NebiusSDKCloudClient.start_instance` is the only permitted mutation. The controller, cloud transfer adapter, route manager, and forwarding port are observers only for rearm and receive no new authority.
- Validation: Unit tests prove exact stopped-passive startup, stable operation identity, pinned SSH, and rejection of a foreign owner before start. CLI help and the full changed-scope gates cover registration and wiring. The live command restored the stopped configured passive after final failback, after which authoritative status showed configured-active ownership, the passive reported `normal`/`passive`, and both workload directions completed 10 of 10 probes with zero loss.
- Rollback: Remove the additive command and helper. Existing transfer behavior is unchanged; operators would again need a separately supported standby-start workflow before automatic failover readiness can be claimed.

#### Alternatives considered

- Using ordinary `apply` was rejected because its pre-mutation pinned-SSH trust check correctly fails when an existing managed member is stopped, so it cannot safely serve as the standby-start owner.
- Automatically starting the former owner as part of failback completion was rejected because failback's safety contract terminally proves the former owner stopped; re-arming is a separate, explicit availability action.
- Starting a passive without exact active-owner and alias-free observations was rejected because it could revive an ambiguous or dual-authority topology.

#### Implementation evidence

- The live `vm-ha-rearm` command started only the stopped configured passive, preserved the configured active as the exact allocation owner throughout startup, and converged to `normal` controller state with passive data-plane mode, no local ownership, and no apply lock. Final independent traffic probes completed 10 of 10 packets in both directions with zero loss.

### TI-DES-013: Typed transfer lineage and independent warm-standby restoration

- Status: implemented
- Requirements: TI-REQ-003, TI-REQ-004, TI-REQ-005, TI-REQ-006, TI-REQ-007, TI-REQ-009, TI-REQ-011
- Selected approach: Replace controller request booleans with a typed `TransferIntent` for planned failover, planned failback, or automatic failover. Keep automatic suspicion cancellable until the first accepted external effect, then persist sticky lineage through terminal recovery while the existing checkpoint, pending action, and transfer continuity continue to drive the canonical stop/detach/attach/confirm/routes/forwarding engine. Persist a separate terminal promotion receipt only after every ownership, route, forwarding, request-consumption, pending-effect, and apply-lock postcondition is durable. Run a separate systemd rearm reconciler on both members; the exact stable owner uses the matching receipt to start only the stopped non-owner and establish one revision-bound, replayable logical operation. Make repeated transfers to an already-owning healthy role explicit no-ops; finalize retained accepted-start journals from exact operation status; consume each retry before its one logical attempt; serialize rearm, apply, and removal inhibition on one lock; poll standby convergence under one deadline; and make heartbeat-v1 promotion readiness valid for exact passive standby as well as the active owner.
- Boundaries and interfaces: `agent/vm_ha_controller.py` owns typed intent validation, pre-effect cancellation, post-effect stickiness, and cutover policy without changing action order. `agent/main.py` and strict private state modules own transfer-lineage, promotion, rearm request/checkpoint/journal/status, and passive-ready serialization while retaining heartbeat v1, lifecycle v4, checkpoint-v4 readers, and the existing private rearm record versions. The rearm cloud port exposes exact journal inspection and read-only terminal finalization separately from Compute start. A shared standard-library `fcntl` helper delivered through pinned SSH owns apply/removal inhibition transitions without depending on target `flock` availability or the newly installed package version. Removal phases every member through exact-operation inhibition, controller acknowledgement with no pending journal, and stopped rearm/controller services before the first deactivation; the lifecycle then checkpoints this global barrier so a partial retry skips unavailable agents and resumes idempotent deactivation. Deactivation preserves its root-only state directory and lock inode while clearing every sibling state entry under that same lock. `cli.py` returns a typed preparation result, emits an identity-free already-owner outcome without writing a request, strictly validates each planned-status identity and runtime binding, and applies one wall-clock deadline to Compute polling, bounded pinned-SSH probes and sleeps, and every repeated readiness read before rendering the existing redundancy panel. The dedicated rearm entry point remains the only Compute-start writer and never gains stop, allocation, route, firewall, or forwarding authority.
- Validation: Parameterize transfer policy and composed crash tests over all intent kinds; prove cancellation before effects, stickiness after effects, exact request consumption, and that no rearm start can precede promotion commitment. Inject rearm crashes and concurrency around request intake, owner checks, Compute states, accepted operation persistence/replay, revision changes, apply/removal locks, service inhibition, corrupt files, running-target adoption, and standby evidence. Retain v1-v4 checkpoint readers, mixed-version fail-closed behavior, disabled/non-HA goldens, package and systemd isolation, CLI/help, Ruff, mypy, full unit/integration, wheel, security, and changed-scope alignment gates. Live symmetry and packet-loss acceptance remains separate and does not convert offline proof into live readiness.
- Rollback: Stop and disable the independent rearm unit before rolling back the package. The safety controller remains independent and retains its existing strict transfer chain; older binaries ignore no changed public schema because new lineage, promotion, rearm, and standby files are private versioned records. Planned transfers then require the formerly supported explicit configured-role preparation path until the new package is restored.

#### Alternatives considered

- Starting Compute from `vm-ha-rearm` or planned-failback preparation was rejected because multiple start writers cannot provide one replayable operation journal or safe automatic restoration.
- Making rearm a controller dependency was rejected because standby availability must not reduce the safety controller's ability to fence, remain guarded, or report failure.
- Inferring promotion completion from current cloud topology was rejected because topology cannot prove route completion, forwarding durability, request consumption, or absence of pending effects and locks.
- Automatically moving ownership back to the configured active was rejected because it adds a second failure-sensitive transfer and violates the explicit planned-failback contract.

#### Implementation evidence

- `agent/vm_ha_controller.py` now admits one typed transfer intent and preserves cancellable pre-effect automatic suspicion while a durable lineage makes every post-effect replay sticky. The existing checkpoint, pending action, transfer-continuity, and ordered stop/detach/attach/confirm/routes/forwarding engine remain the execution authority.
- `agent/main.py` records strict transfer lineage before the first accepted cutover effect and replaces the local terminal promotion receipt only after request consumption, exact stopped-former/owner/route/forwarding proof, no pending effect, and no apply lock. A separate strict current-boot standby record is invalidated by the cold-start guard and binds passive data plane, exact generation and digests, non-ownership, route/XFRM readiness, and clear locks.
- Operator reads of that standby record use the controller monotonic clock and fail closed when its evidence is from the future or is at least 10 seconds old, so a stopped controller cannot leave same-boot readiness valid indefinitely.
- `agent/vm_ha_rearm.py` and `nebius-vpngw-vm-ha-rearm.service` form the independent sole-start-writer bulkhead. The stable owner must match both the receipt and its current ownership revision; stopped revisions receive one deterministic operation identity, accepted operations resume, running alias-free targets are adopted, and drift, ambiguous states, corrupt records, writer contention, apply/removal activity, or explicit inhibition report a safe blocked state. The safety controller has no dependency on this unit.
- Exact terminal OperationService lookup now compare-clears a retained accepted-start journal even for a matching earlier promotion or an already-adopted `running` checkpoint; unavailable, failed, unbound, or changed journals never resubmit or clear. Explicit retry requests are durably consumed before their one logical attempt, so a service restart cannot replay definite failure authority.
- `cli.py` contains one role-neutral preparation path for both planned directions and explicit rearm. It submits owner-side retry intent instead of starting Compute, waits for Running, pinned SSH, and fresh strict standby evidence, then rereads exact owner and target state before the unchanged role-bound transfer request. It deliberately does not enroll SSH trust, deploy a generation, clean local route hygiene, reconcile cloud routes, move the allocation, alter firewall state, or enable forwarding. Operator status composes exact member records into one identity-free VM-HA section and directs those failures to their owning setup or apply workflow.
- Live parity validation repaired two contract-restoring edge cases:
  current-owner `ENABLE_ACTIVE` reconciliation with no transfer intent now
  returns without manufacturing lineage, while a planned passive standby
  requires a fresh passive guard and intentionally absent active-owner
  `controller_ready_boot_id`. Both configured roles have focused regression
  coverage.
- A current live trial against a hybrid peer fixture proved the strict transfer
  and traffic-recovery chain, then failed steady-state acceptance after
  automatic rearm. Packet capture and independent GCP route inspection showed
  a configured-role Classic VPN static route sending both requests and replies
  to the non-forwarding standby even though the HA VPN and Cloud Router BGP
  view correctly followed the current owner. The configured-active owner was
  restored with zero-loss bidirectional probes. This hybrid static-route
  topology is not accepted as the role-neutral GCP warm-standby target; the
  supported target remains the four-tunnel HA VPN and Cloud Router topology.
- `ssh_push.py` serializes apply, retry, rearm, and removal through the same `fcntl` lock and retains that exact inode across deactivation. Ordinary HA removal now gates and drains both members before either is deactivated, persists the completed barrier, and resumes partial teardown without replaying agent commands on a member whose HA runtime is already gone. Package data, systemd contract tests, and wheel tests install, enable, independently verify, remove, and package the rearm unit. Focused controller, agent-runtime, rearm, CLI, deployment, systemd, and wheel tests plus Ruff and mypy pass offline. No new live failover timing or packet-loss claim is made; the clean-trial acceptance matrix remains separately authorized.

### TI-DES-014: Owner-only Classic tunnel lifecycle for isolated static VM HA

- Status: implemented
- Requirements: TI-REQ-003, TI-REQ-004, TI-REQ-005, TI-REQ-006, TI-REQ-009, TI-REQ-011, TI-REQ-012
- Selected approach: Keep BGP VM HA on the existing warm-tunnel path. Detect the exact static-only runtime from its committed non-empty static manifest and empty BGP-policy manifest, without adding a YAML flag or changing mixed-mode validation. In static-only passive mode, retain the running Compute, committed generation, services, firewall, passive route hygiene, and forwarding fence while unloading and terminating every IKE SA. Add one durable candidate-data-plane preparation action after candidate ownership confirmation and before route reconciliation; it reloads the exact committed strongSwan configuration while forwarding remains disabled. Final route and forwarding gates continue to require fresh established-IKE, usable XFRM/static prefixes, exact ownership, current route receipt, and no apply lock.
- Boundaries and interfaces: `agent/vm_ha/runtime.py` owns static-only classification, tunnel suspension, cold-standby readiness, bounded tunnel activation, and post-activation observation. `agent/vm_ha_controller.py` owns the new checkpointed action order and separates transfer admission from final route readiness without weakening the stop/detach/attach/confirm/routes/forwarding sequence. `agent/main.py` and status evidence distinguish a static cold tunnel from BGP warm readiness while retaining public commands and private record compatibility. A dedicated `misc` Classic helper owns GCP target gateways, addresses, forwarding rules, tunnels, and explicit routes; it does not enter the product runtime. The two ignored operator configs own distinct live resource identities and environment-backed PSK references.
- Validation: Controller and composed crash tests prove no static tunnel activation before the former owner is stopped and candidate ownership is confirmed, and no route or forwarding effect before fresh local tunnel readiness. Runtime tests cover passive unload/termination, idempotent cold rearm, bounded and failed activation, BGP unchanged behavior, and exact static-only classification. Fake-`gcloud` tests cover two Classic paths, explicit routes, secret-safe transport, idempotency, and incompatible-resource rejection. Clean non-production steady-state, planned failover/rearm, planned failback/rearm, and automatic failover/rearm trials independently proved the same ownership and effect order, one owner-aligned IKE SA, retained GCP graph completeness, successful workload request/reply traffic, and BGP-fixture non-interference.
- Rollback: Stop before live mutation if the Classic helper cannot prove a fully isolated resource graph. A source rollback removes the static-only preparation action and tunnel-cold classification while preserving the existing BGP path and public record readers; such a rollback restores static VM HA to explicitly unsupported warm-standby status. Live recovery uses only the supported fenced transfer back to the last independently proven owner and never deletes the retained review fixtures.

#### Alternatives considered

- Keeping both Classic tunnels established with different priorities was rejected because GCP resumes the lower-numbered configured-role route when that tunnel returns, even if its VM is the non-forwarding standby.
- Keeping both Classic tunnels established with equal priorities was rejected because GCP uses ECMP across established same-destination, same-priority tunnel routes.
- Updating GCP routes from a gateway VM was rejected because it adds cross-cloud credentials, permissions, and an external route writer to the safety-critical ownership boundary.
- Leaving the former owner stopped permanently was rejected because it does not satisfy guarded role-neutral Compute redundancy or clean failback preparation.

#### Implementation evidence

- The retained hybrid live trial localized the static failure to GCP Classic
  route selection after automatic rearm: both Classic IKE SAs were established,
  so the configured-role route returned to the non-forwarding standby while the
  independent HA VPN/BGP path continued to follow the current owner.
- Existing runtime inspection confirms that blocked mode already unloads
  strongSwan connections, while the current passive path reloads all
  connections. The selected change therefore extends the existing guarded
  data-plane boundary rather than introducing a second tunnel manager.
- `runtime.py` now classifies only a non-empty static manifest with an empty BGP
  manifest as tunnel-cold, terminates passive IKE state, observes zero standby
  SAs, and prepares the candidate while forwarding remains disabled.
  `vm_ha_controller.py` checkpoints that preparation only after exact stopped
  former-owner and shared-allocation confirmation and still gates routes and
  forwarding on full local readiness and the current route receipt.
- Agent and CLI status distinguish cold from warm standby evidence without
  changing the heartbeat-v1 or planned-status record schema. BGP and mixed-mode
  runtimes remain on their prior warm path.
- The dedicated Classic helper plans two target gateways, addresses, forwarding
  rule sets, tunnels, and explicit routes without Cloud Router or BGP resources;
  focused tests cover graph isolation, idempotency, incompatible resource
  rejection, preflighted secrets, anonymous-descriptor transport, and entrypoint
  delegation.
- The private BGP config resolves to one BGP-only connection with four tunnels;
  the separate private Classic config resolves to one static-only connection
  with two tunnels and distinct gateway, subnet, cluster, member, allocation,
  and peer identities. The retained Classic helper reports both paths and all
  explicit routes present without creating Cloud Router or BGP resources.
- Initial steady state and clean planned failover, planned failback, and
  automatic failover trials each observed the former Compute owner `Stopped`
  before detach, exact candidate ownership before tunnel preparation, a fresh
  route receipt before forwarding, exactly one owner-aligned Classic IKE SA,
  and a running tunnel-cold standby after rearm. The final workload probe
  completed five request/reply exchanges with zero loss.
- Live deployment also exposed and repaired exact-source defects: certificate
  URI identity is preflighted before mutation; baseline owner reconciliation
  does not manufacture transfer lineage; a promoted current owner recovers
  only from a terminal receipt for the unchanged ownership epoch; stale
  pre-reboot status fails closed against the actual current boot; and a cold
  standby's zero-SA status is rendered intentionally. The obsolete hybrid
  static route was withdrawn through supported BGP-only apply before the
  isolated static route was admitted.
- Final independent status showed the static pair with exact cloud/route
  authority, one active owner, one cold standby, and clear apply locks. The
  separate BGP fixture retained four established IPsec/BGP tunnels, exact
  owner/standby authority, and healthy routing. Both fixtures and all private
  operator configuration remain in place for review.

## Task Implementer Design Change Log

- 2026-08-17: Live-validated TI-DES-014 in the isolated non-production fixture.
  Steady state, both planned ownership directions, automatic failover, and all
  rearm paths passed the frozen stop/ownership/tunnel/route/forwarding order;
  traffic and BGP non-interference checks passed, causal live defects were
  repaired at their source boundaries, and review resources were retained.

- 2026-08-17: Implemented TI-DES-014 offline. Static-only passive runtimes now
  prove a tunnel-cold state and the controller performs one checkpointed,
  ownership-fenced candidate preparation before routes. Added the isolated
  Classic helper and focused regression coverage; live fixture and acceptance
  evidence remain pending.

- 2026-08-17: Added planned TI-DES-014 for an isolated static-only GCP Classic
  fixture and owner-only IKE lifecycle. The passive remains Compute-warm but
  tunnel-cold; a new checkpointed preparation effect runs only after ownership
  confirmation and before routes or forwarding, while BGP behavior remains
  unchanged.

- 2026-08-17: Reconciled TI-DES-013 after live parity validation repaired
  current-owner activation lineage and passive planned-status admission, then
  localized a rejected hybrid Classic static-route rearm trial and restored the
  configured-active steady state.
- 2026-08-17: Reconciled TI-DES-013 after removal-safety alignment added a
  two-member inhibition and quiescence barrier, stopped both mutation writers
  everywhere before deactivation, checkpointed that barrier for partial
  teardown replay, and made planned status reads enforce the complete runtime
  binding before request admission.
- 2026-08-17: Reconciled TI-DES-013 after final alignment: deactivation now
  retains the stable writer-lock inode while clearing sibling state, and one
  deadline bounds Compute observation, pinned-SSH probes and sleeps, and every
  planned-readiness subprocess call.
- 2026-08-17: Reopened TI-DES-013 to repair same-owner request admission,
  accepted-start journal finalization, one-shot retries, shared apply/removal
  exclusion, bounded readiness polling, and passive heartbeat redundancy
  semantics while preserving the public and persisted-version boundaries.
- 2026-08-17: Marked TI-DES-013 implemented after typed sticky lineage,
  terminal receipt replacement, ownership-revision-bound automatic rearm,
  strict current-boot standby evidence, shared planned preparation, independent
  systemd/package lifecycle, and additive redundancy reporting passed focused
  offline tests. Live symmetry and packet-loss acceptance remains separate.
- 2026-08-17: Superseded TI-DES-011 and TI-DES-012 with planned TI-DES-013,
  separating typed sticky transfer lineage and terminal promotion commitment
  from an independent role-neutral sole-start-writer rearm service, shared
  planned preparation, fresh standby evidence, and additive redundancy status.
- 2026-08-17: Added implemented TI-DES-011 and TI-DES-012 for the planned
  passive-targeted VM ownership request and exact standby rearm paths. Live
  planned failover, failback, automatic failover, final recovery, and
  bidirectional traffic evidence all preserved the strict ownership chain.
- 2026-08-17: Added planned TI-DES-010 for deterministic repair-before-promote:
  one owner-bound five-second attempt, prefix-aware classification, sole repair
  writer, and unchanged authoritative Compute-stop transfer safety. Marked it
  implemented after checkpoint, runtime, routing, monitor, CLI, and composed
  failover tests passed; retained heartbeat v1 and the existing systemd stop
  guard, and rejected a short watchdog on the combined local/cloud process.
- 2026-08-17: Marked TI-DES-008 and TI-DES-009 implemented after clean BGP and
  static workload trials, supported manual failback, independent cloud/route
  postconditions, passive cold-reboot validation, exact runtime-identity use,
  and retained secret-reference-only configuration all passed.
- 2026-08-17: Reconciled TI-DES-006 so manual-failback request preparation
  re-reads exact allocation and attachment ownership after pinned SSH readiness
  before it can submit the controller request.
- 2026-08-17: Reconciled TI-DES-005 with the live-proven StrongSwan/VICI boot
  race fix: exact blocked-mode service stop and connected-socket readiness
  preserve the cold-start fence without transient guarded failures.
- 2026-08-17: Reconciled TI-DES-006 with the live-proven manual-failback
  request-target startup boundary and its exact ownership, alias, idempotency,
  and pinned-SSH guards.
- 2026-08-17: Reconciled TI-DES-005 and TI-DES-006 with exact post-authority
  standby firewall preparation, StrongSwan plugin-warning cleanup, the
  implemented externally fenced activation-recovery successor,
  approval-bound normalized cloud observation, and canonical passive-first
  replay without weakening the unchanged `ACTIVATING` retry path.
- 2026-08-16: Added planned TI-DES-009 after live diagnosis proved that the
  former role allowlist and `client.iam()` scaffold do not match the current
  Nebius IAM or pinned SDK. The selected boundary uses exact generated-service
  enrollment, one project `editor` permit, separate one-key runtime credentials,
  supported CLI impersonation, and fail-closed explicit `--sa` semantics.
- 2026-08-16: Reconciled TI-DES-008 after implementing and validating the
  additive four-tunnel helper, secret-safe GCP invocation, fail-closed resource
  inspection, and authoritative VM-HA status. Live fixture migration,
  second-node creation, failover, and failback remain pending clean trials.
- 2026-08-16: Added planned TI-DES-008 for an additive four-tunnel GCP
  multi-VM fixture, authoritative VM-HA status, isolated clean product trials,
  and independent steady-state, failover, and failback evidence.
- 2026-08-16: Reconciled the post-implementation safety review into lifecycle
  v4 path guards and accepted-operation recovery, checkpoint-v2 transfer
  continuity and v1 reproof, typed status convergence, exact final-activation
  recovery with passive-first compensation, bounded HA-only SDK operations,
  exact Compute-create footprint validation, strict checkpoint parsing, and
  canonical CI type/build gates without changing the opt-in public surface.
- 2026-08-15: Closed the migration transaction-ordering review by replacing
  lifecycle rebinding with one revisioned v3 CAS transaction, exact
  desired/current approval plus isolated recovery digests, fill-once cloud
  identity checkpoints, stable-key route outcome resolution, and `ACTIVE`-last
  passive proof while retaining v2 reads and the ordinary path.
- 2026-08-15: Reopened TI-DES-002, TI-DES-003, TI-DES-004, TI-DES-006,
  and TI-DES-007 for the customer migration correction: replace primary-IP
  mutation with a movable secondary alias, add verified apply locks and
  digest-bound approval, preserve the retained gateway, require exact route
  completion with compensating replacement rollback, and remove HA probes from
  ordinary apply.
- 2026-08-14: Reconciled TI-DES-006 after the final compatibility correction:
  added the status-bound lifecycle record, service-account-first ordinary
  discovery, exact-pinned pre-sidecar runtime adoption, repeated cloud and
  identity proof, removal checkpoint, and verified idempotent tombstone.
- 2026-08-14: Reconciled TI-DES-006 after HA-removal review: durable
  two-member discovery and strict identity recheck now precede complete
  deactivation, abort paths leave the cluster untouched, retired product
  mutation services are disabled and verified, and ordinary mutation starts
  only after terminal non-HA proof.
- 2026-08-14: Marked TI-DES-005 through TI-DES-007 implemented after the
  retained serial correction passed 557 offline unit and integration tests,
  Ruff, mypy, diff integrity, and combined correctness and security review.
- 2026-08-14: Reopened TI-DES-005 through TI-DES-007 after integration review to separate blocked local rendering from active data-plane effects, establish one fail-fast pinned SSH trust policy shared by OpenSSH and Paramiko before provisioning, and select the composed bootstrap/trust proof in ordinary CI.
- 2026-08-13: Marked TI-DES-001 through TI-DES-006 implemented after the retained correction chain closed authoritative runtime binding, immutable credential installation, exact route targets, strict cloud fencing, current-truth route receipts, cold-start guard closure, production factory composition, guarded operator actions, and default-disabled compatibility. TI-DES-007 remains planned until the final composed acceptance wave completes.
- 2026-08-12: Reconciled TI-DES-001 through TI-DES-006 after retained integration review: added the post-provision runtime-binding phase, strict shared allocation provisioning, Compute resource revision as ownership epoch, concrete mTLS and route-receipt ownership, complete controller composition, guard closure across all forwarding writers, verified activation/deactivation, and IAM allowlist boundaries.
- 2026-08-11: Added TI-DES-001 through TI-DES-007 for additive two-node VM-level active/passive HA.

## Core Design Change Log

- 2026-08-21: Completed FEAT-011's periodic passive routing-hygiene path with
  role-aware systemd admission, lock-held authority rechecks, exact passive
  mutation bounds, recurring enforcement, exact table-token parsing that
  preserves unrelated rules, and fail-closed readiness/status observation with
  owning-workflow remediation without changing public CLI, configuration,
  heartbeat, or persisted-state schemas.

- 2026-08-21: Revalidated FEAT-002 on the current 1,284-test suite and removed a test-only two-second SDK polling delay while preserving the real SDK wait/update path, exact assertions, selection, outcomes, configuration, and production behavior.

- 2026-08-21: Reconciled FEAT-001 after the warning-free setuptools-scm
  migration centralized runtime configuration, nested tag matching, explicit
  build-time source version-file behavior, dependency/lock bounds, and focused
  warning-strict regressions; the full `make all` workflow passed.

- 2026-08-20: Implemented FEAT-012 with centralized command applicability, canonical static-prefix resolution, read-only installed-agent capability preflight, controller-owned VM-HA route handling, typed fail-before-success route operations, and complete offline matrix validation.

- 2026-08-20: Added planned FEAT-011 for per-neighbor allow/deny export rendering, mode-aware Adj-RIB-Out readiness, receipt-last passive cleanup, exact-authority tri-state audit, and separation of route inspection from repair.
- 2026-08-20: Marked the FEAT-007 Role correction implemented after the authoritative `active`/`standby`/`unknown` projection passed negative-control, focused, full-suite, static, documentation, security, and alignment checks.
- 2026-08-20: Reopened FEAT-007 to derive the public Role cell only from authoritative current ownership and remove the misleading configured-role suffix.
- 2026-08-20: Reopened FEAT-007 to replace summary-plus-member VM-HA rendering with one conservative four-column table while retaining authoritative classification and redaction.
- 2026-08-20: Added planned FEAT-010 for a compact primary VPN status table with complete folded tunnel names and preserved Traffic Override detection.
- 2026-08-20: Marked the FEAT-007 presentation revision and FEAT-010 implemented after exact table-shape, semantic color, unavailable-member, long-name, row-arity, full static, unit, integration, documentation, security, alignment, and diff-integrity checks passed without live execution.
- 2026-08-20: Marked FEAT-009 implemented after separating provisioning progress from authoritative VM-HA network rereads, adding recreate-aware VM discovery text, and passing focused and complete offline validation without a live gateway trial.
- 2026-08-19: Added planned FEAT-008 for clean-slate VM-local self-signed
  identities, exact peer-leaf enrollment over pinned SSH, automatic bootstrap
  and replacement, explicit crash-safe dual rotation, and epoch-bound peer
  protocol v2 without an external CA or HA compatibility path.
- 2026-08-19: Marked FEAT-008 implemented after direct-pinned managed
  identities, apply bootstrap/replacement, passive-first explicit rotation,
  heartbeat v2, secret-free status, complete offline suites, static checks,
  and package tests passed without a live gateway trial.

- 2026-08-19: Marked FEAT-007 implemented after structured authority,
  complete display validation, conservative classification, identity-safe
  rendering, hard parser removal, and complete offline test suites passed.
- 2026-08-19: Added planned FEAT-007 for hard removal of the unpublished
  recovery read, structured cloud/member correlation, strict status-v1 display
  validation, conservative HA classification, sanitized summary-plus-member
  rendering, and unchanged read-only/non-HA boundaries.

- 2026-08-18: Marked FEAT-006 implemented after the path-aware Typer groups,
  unchanged leaf routing, zero-effect old-path rejection, migration guidance,
  and complete offline validation passed.
- 2026-08-18: Added planned FEAT-006 for native `failover` and `failback`
  subapplications, VM/tunnel leaves, path-aware help ordering, no-alias parser
  rejection, unchanged callback bodies, and explicit migration guidance.
- 2026-08-18: Marked FEAT-005 implemented after all 18 public commands and the root help rendered canonical tested examples with no execution-path or safety-gate change.
- 2026-08-18: Added FEAT-005 for a canonical example registry, Typer root and command epilogs, complete rendered-help coverage, and aligned user documentation without changing execution behavior.
- 2026-08-18: Marked FEAT-004 implemented after offline verification of the dedicated conversion module, credential preflight, semantic placeholder handling, passive-only allocation seam, no-clobber publication and recovery boundary, and real migration dry-run handoff.
- 2026-08-18: Added FEAT-004 for an additive two-phase `configure-vm-ha` wizard, raw-YAML allowlisted conversion, safe candidate publication, passive-only allocation preparation, and handoff to the existing approved apply engine.
- 2026-08-18: Added FEAT-003 for the TTY-default wizard, exact noninteractive compatibility path, in-memory validation and atomic write, secret-reference/redaction policy, explicit VM-HA gating, and optional confirmed reuse of standalone network preparation.
- 2026-08-16: Marked FEAT-002 implemented after a test-only sleeper injection removed unrelated real retry delays from the seven-case crash-replay matrix, reducing the five-sample serial unit median by about 31% with all 682 outcomes preserved.
- 2026-08-16: Added FEAT-002 for measured, like-for-like pytest optimization with preserved selection, isolation, diagnostics, and correctness gates.
- 2026-08-16: Marked FEAT-001 implemented after adding the bounded direct Git version probe, focused Python-project contract tests, and local artifact hygiene without changing runtime or public interfaces.
- 2026-08-16: Added FEAT-001 for an additive, compatibility-preserving Python-project hardening pass over version discovery, regression contracts, and local artifact hygiene.
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD013 MD024 -->

# Nebius VPN Gateway (VM-Based) — Design Document

> Version: v0.5.1
> Designed by: Reza Bahmanzadeh, Nebius Professional Services, CX Org.
> Copyright 2025 Nebius B.V.
> Licensed under the Apache License, Version 2.0

## Table of Contents

- [XFRM Mode Summary (current, required)](#xfrm-mode-summary-current-required)
- [Purpose & Scope](#purpose--scope)
- [Goals & Non-Goals](#goals--non-goals)
- [Architecture Overview](#architecture-overview)
- [Nebius Networking Model](#nebius-networking-model)
- [Configuration Model](#configuration-model)
- [Workflows & CLI](#workflows--cli)
- [Routing Modes & Local Prefixes](#routing-modes--local-prefixes)
- [IPsec Configuration](#ipsec-configuration)
- [BGP Configuration](#bgp-configuration)
- [Failover](#failover)
- [Static Routes Configuration](#static-routes-configuration)
- [XFRM Routing Stack](#xfrm-routing-stack)
- [Security Hardening](#security-hardening)
- [Agent State Management](#agent-state-management)
- [Monitoring & Status](#monitoring--status)
- [Peer Config Import](#peer-config-import)
- [VM Management](#vm-management)
- [Development Workflow](#development-workflow)
- [Project Structure](#project-structure)
- [Tips & Troubleshooting](#tips--troubleshooting)

> Note: Legacy VTI support has been removed. XFRM interfaces are the only supported mode going forward.

## XFRM Mode Summary (current, required)

- XFRM netdevices (`xfrm0`, `xfrm1`, …) bound via `if_id` in strongSwan; no marks or updown scripts.
- strongSwan connections are loaded via `swanctl` (VICI). `ipsec.conf` is a minimal starter-only config; tunnel CHILD_SAs include `if_id_in/if_id_out` for deterministic XFRM binding.
- Traffic selectors: local side is scoped to the tunnel’s inner /30 plus `gateway.local_prefixes`; remote stays `0.0.0.0/0`. This keeps SSH/ping to the public IP off the tunnel while allowing any remote prefixes to traverse.
- Routing hygiene: table 220 is removed; the broad `169.254.0.0/16` DHCP route is removed while preserving metadata routes (`169.254.169.x`). Prevents policy routing and APIPA from stealing tunnel/management traffic.
- Sysctl: `rp_filter=0` on all/default/eth0 (required for XFRM), IP forwarding enabled, redirects off, ARP hardened.
- Firewall: UFW allows SSH from management CIDRs (or anywhere if not configured), IPsec (UDP 500/4500, ESP) from peer IPs, traffic from local VPC subnets for forwarding, ICMP for troubleshooting, and permits all traffic on tunnel interfaces (xfrm*). BGP (TCP 179) is reachable only over xfrm* between APIPA peers (169.254.x.x), which only exist after IPsec decryption; no TCP/179 on eth0. Everything else inbound on eth0 is denied.

> Public (eth0):   IKE / ESP only, SSH, ICMP
> Tunnel (xfrm*):  BGP (tcp/179), ICMP, routed traffic

- Interfaces must exist before IPsec brings up CHILD_SAs; agent creates XFRM devices and assigns inner IPs/routes before FRR reload.

## Purpose & Scope

Deliver a VM-based site-to-site VPN gateway for Nebius AI Cloud using IPsec (strongSwan) and routing (FRR for BGP, static as fallback). Provide a CLI orchestrator plus per-VM agent with idempotent configuration from a single YAML file, with optional peer-config import to generate that YAML. Support common cloud and on-premises peers (GCP HA VPN, AWS Site-to-Site VPN, Azure VPN Gateway, Cisco IOS).

This project is an open source, self-service, VM-based VPN gateway. It is not a managed Nebius VPN service.

## Goals & Non-Goals

**Goals:**

- IKEv2 default; IKEv1 optional (disabled by default), PSK authentication
- Strong cryptography: AES-256, SHA-256/384/512, DH groups 14/20/24
- BGP routing (preferred) with static routing fallback
- Repeatable, idempotent deployments with minimal operator state
- Stable public IP preservation across VM recreation
- Explicit, default-disabled two-node VM-level active/passive HA with
  authoritative cloud fencing and owner-only route reconciliation

**Non-goals:**

These features are not currently implemented but may be considered for future enhancements:

- **ECMP (Equal-Cost Multi-Path) in VPC route tables:**
  - *What it does:* Allows load balancing traffic across multiple gateway VMs for the same destination prefix
  - *Current limitation:* VPC routes point to a single next-hop (one gateway VM per route)
  - *Benefit:* Would enable automatic traffic distribution and higher aggregate throughput for high-bandwidth workloads
  - *Status:* Nebius VPC platform does not currently support ECMP routing

- **External NAT/Load balancing:**
  - *What it does:* Single public IP distributed across multiple gateway VMs for incoming VPN connections
  - *Current limitation:* Each gateway has its own public IP; peers must configure multiple tunnels
  - *Benefit:* Would simplify peer configuration and enable transparent gateway VM scaling
  - *Status:* Requires platform-level load balancer integration for IPsec traffic

- **Multi-NIC support:**
  - *What it does:* Multiple network interfaces per gateway VM for traffic separation (management, tunnel, internal)
  - *Current limitation:* Nebius platform currently limits VMs to 1 NIC with 1 public IP
  - *Benefit:* Would improve security isolation and enable dedicated high-throughput tunnel interfaces
  - *Status:* Configuration is future-ready (accepts `num_nics > 1`), awaiting platform support

## Architecture Overview

### Components

**Orchestrator CLI (`nebius-vpngw`):**

- Runs on operator laptop or CI/CD pipeline
- Reads YAML configuration; peer configs can be imported to generate YAML
- Manages VM lifecycle and IP allocations via Nebius SDK
- Pushes configuration to VMs over SSH
- Triggers agent reloads

**Gateway VM:**

- Ubuntu LTS with strongSwan, FRR, and Python
- Runs `nebius-vpngw-agent` systemd service
- Dedicated gateway subnet (default name: `vpngw-subnet`) for isolation

**Agent:**

- Single daemon per VM
- Renders strongSwan and FRR configurations
- Applies changes idempotently
- Persists state in `/etc/nebius-vpngw/last-applied.json`
- Reloads via SIGHUP

**Deployment Modes:**

- Single VM with multiple tunnels and multiple peer `connections` (current releases use active/passive per connection; the VM remains a SPOF)
- Gateway group without `vm_ha`: N independent VMs with per-tunnel pinning
- Gateway group with explicit `vm_ha.enabled: true`: exactly two stable members
  with one shared private allocation and controller-owned data-plane authority

**Current HA Boundary:**

- Tunnel `ha_role` remains per connection and VM; it never grants VM ownership.
- VM-level HA is selected only by the explicit two-member `gateway_group.vm_ha`
  contract. It is not inferred from instance count, tunnel roles, or public IPs.
- Promotion requires an authoritatively stopped former Compute owner, absent
  former attachment, exact shared-allocation attachment to the candidate, and a
  fresh candidate Compute revision before forwarding or route reconciliation.
- Active-active forwarding, ECMP, and more than two HA members are unsupported.

### Architecture Diagrams

#### Single VM with tunnel-level HA

![Single-VM Nebius VPN Gateway with tunnel-level HA](../images/nebius-vpngw-single-vm-tunnel-ha.svg)

Both tunnel roles terminate on one gateway VM. Tunnel failover protects the
IPsec path, not the VM, so this topology retains a VM single point of failure.

#### Explicit two-VM VM-level HA

![Two-VM Nebius VPN Gateway with VM-level and tunnel-level HA](../images/nebius-vpngw-two-vm-ha.svg)

The four-tunnel BGP topology assigns tunnels 0 and 3 to `nebius-vpngw0` and
tunnels 1 and 2 to `nebius-vpngw1`. Tunnel `ha_role` remains local to each VM;
it never grants VM ownership. Only the authoritative shared-private-allocation
owner forwards and reconciles routes. Promotion still requires the former
Compute owner to be `Stopped` and candidate allocation ownership to be
confirmed first. Static-only GCP Classic VM-HA instead keeps the passive member
tunnel-cold.

## Nebius Networking Model

### VPC and Subnets

- One VPC network selected via `gateway_group.network_id` (optional; see resolution logic below)
- Dedicated gateway subnet created automatically if missing
- Dedicated route table (`<gateway-subnet-name>-routing-table`) with default egress route
- Workload subnets remain separate for security isolation

**Network Resolution Logic:**

When `gateway_group.network_id` is not specified in the YAML config, the system auto-discovers the network using this priority order:

1. **Default network:** Looks for a network named `default-network` in the project
2. **Single custom network:** If no default network exists and exactly ONE custom network is found, uses that network
3. **Multiple networks:** If multiple custom networks exist (rare scenario), the deployment **fails** with an error asking the user to explicitly specify `gateway_group.network_id` in the YAML

This intelligent resolution handles the common case (default network or single VPC) while preventing ambiguity when multiple networks exist.

**Platform Constraint:** Currently 1 NIC per VM with 1 public IP. All tunnels share the same IP, differentiated by IKE/IPsec identifiers.

**Future-ready:** Configuration accepts `num_nics > 1` for when platform supports multi-NIC.

### Dedicated Subnet Rationale

- **Security isolation:** Limits blast radius of firewall misconfigurations
- **Routing clarity:** Simplifies HA failover and prevents asymmetric routing
- **IP hygiene:** Controlled CIDR for gateway infrastructure, separate from workloads
- **Policy separation:** Distinct egress controls without affecting application subnets
- **Operational safety:** Safer VM recreations with reduced ARP/ND noise
- **Capacity:** The gateway subnet can be pinned to an explicit private CIDR or auto-carved from the target VPC’s private pool. Auto-carving uses `gateway_group.subnet.prefix_length` (default `/24`). Explicit CIDRs can come from extended RFC1918 ranges after the network pool is updated.
- **Control-plane safety:** `add-routes-local` and `list-routes-local` target workload subnets whose effective CIDRs overlap `gateway.local_prefixes`. For explicit-pool subnets this comes from `spec.ipv4_private_pools`. For inherited-pool subnets (`use_network_pools=true`), the CLI uses `status.ipv4_private_cidrs` only after subtracting CIDRs explicitly owned by other subnets in the same network. This is a defensive workaround for a Nebius console/API status bug where inherited subnets can appear to own CIDRs that were actually carved out for explicit-pool subnets.

### Public IP Allocations

Configuration shape: `external_ips[instance_index][nic_index]` → IP string (flat lists are not supported)

**Behavior:**

- Omitted/empty: Auto-create IP allocations
- Provided: Use existing allocations
- Insufficient: Create missing allocations
- Auto naming: `{instance}-eth{N}-ip`

**Pre-allocation workflow:** `nebius-vpngw prep-network` can create the configured gateway subnet and reserve public IPs before peer setup. It is safe to rerun. If `gateway_group.external_ips` is empty, it allocates new IPs and writes them into the YAML. If `external_ips` is set, it resolves matching allocations by IP in the current project, reuses unattached matches only when they already belong to the target gateway subnet, and allocates the requested IP only when no match exists. If a matched allocation is still attached to another resource, the command fails instead of reusing it. If an IP was just released, it waits briefly (~10s) and retries before failing.

**Preservation:** Allocations are kept and reattached during VM recreation. No downtime for IP addresses, only for tunnel establishment.

**Subnet constraint:** Nebius does not allow changing an existing public allocation onto a different gateway subnet. In the generated SDK/proto contract, `IPv4PublicAllocationSpec.subnet_id`, `cidr`, and `pool_id` are marked immutable, and live same-project update probes reject rebinding across subnets. If you supply `external_ips` and the found allocation belongs to a different subnet than the target gateway subnet, `vpngw` fails fast without attempting a migration:

- Deploy in the original subnet/network so the allocation matches, **or**
- Remove the IP from `external_ips` to get a new allocation in the gateway subnet, **or**
- (Best effort) Manually release the old allocation and let the deployer request the same IP in the new subnet. If the pool allows it and the address is still free, it is reclaimed; otherwise the request fails or yields a different IP.

**Explicit-IP safety:** when `external_ips` contains a literal IP, stale CLI-owned allocation names do not override that request. The deployer only reuses a named allocation when it resolves to the same IP; otherwise it fails and asks the operator to fix the mismatch.

**Examples:**

```yaml
# Single VM, single NIC (auto-allocate)
external_ips: []

# Single VM, existing IP
external_ips: [["203.0.113.10"]]

# Two VMs, existing IPs
external_ips: [["203.0.113.10"], ["203.0.113.20"]]

# Two VMs, two NICs each (future multi-NIC example)
external_ips:
  - ["66.201.0.131", "66.201.0.132"]  # VM 0: NIC0, NIC1
  - ["66.201.0.133", "66.201.0.134"]  # VM 1: NIC0, NIC1
```

## Configuration Model

### YAML Structure

Single file `*.config.yaml` with four main sections:

1. **gateway_group:** VM infrastructure (instance count, specs, gateway subnet, IPs)
2. **gateway:** Routing identity (ASN, local prefixes, quotas)
3. **defaults:** Global VPN behavior (crypto, DPD, BGP settings)
4. **connections:** Peer gateways with tunnel definitions

### Merge Precedence

Tunnel settings override connection settings, which override peer-config, which override defaults.

### Environment Variables

Use `${VAR}` placeholders for secrets and environment-specific values. Missing variables are reported together before deployment.

### Guided and Template Generation

**Embedded template** in `config_template.py` is the source of truth, always aligned with schema:

```bash
# Guided configuration on a terminal
nebius-vpngw create-config my-vpn.config.yaml

# Explicit embedded-template compatibility path
nebius-vpngw create-config my-vpn.config.yaml --no-interactive
```

The interactive path builds and validates a complete candidate in memory before
atomic publication. Non-TTY calls and `--no-interactive` write the exact
embedded template with comprehensive comments and examples. Files with
`.config.yaml` extension are automatically git-ignored for security.

### Schema Validation

**Strict Pydantic-based validation** enforces configuration correctness:

**Features:**

- Rejects unknown fields (catches typos like `inner_ciddr`)
- Validates types (IPs, CIDRs, numbers, booleans)
- Enforces constraints (ASN ranges 64512-65534, /30 subnets, APIPA ranges)
- Checks logical consistency (BGP mode requires `bgp.remote_asn`)
- Verifies resource quotas

**API Versioning:**

- `version: 1` field required in all configs
- Future schema changes increment version number
- Backwards compatibility maintained

**CLI Integration:**

```bash
# Validate before deployment
nebius-vpngw validate-config my-vpn.config.yaml

# Validation runs automatically during apply
nebius-vpngw apply --local-config-file my-vpn.config.yaml
```

**Note:** The `validate-config` command takes the config file as a positional argument, not as `--local-config-file`. This is different from other commands like `apply` which use the flag syntax.

**Implementation:**

- Schema: `src/nebius_vpngw/schema.py` (Pydantic models)
- Validation: `src/nebius_vpngw/config_loader.py` (after env expansion)
- CLI command: `src/nebius_vpngw/cli.py` (`validate_config()`)

## Workflows & CLI

### Commands

**Configuration Creation:**

```bash
nebius-vpngw create-config <config-file>
```

Uses the guided schema-backed wizard on interactive terminals, or the exact
embedded template for non-TTY and `--no-interactive` calls. `--interactive`
forces the wizard. It warns when the filename does not end with `.config.yaml`
and requires `--force` to replace an existing file. An exact current template
remains a successful no-op. Wizard publication is validation-gated and atomic;
network preparation is a separate default-No confirmation after the file is
saved.

**Configuration Validation:**

```bash
nebius-vpngw validate-config <config-file>
```

Validates configuration against schema without deployment. Performs full validation including types, constraints, and logical consistency. Returns exit code 0 (valid) or 1 (invalid). Use before deployment to catch errors early.

**Network Preparation (pre-allocate public IPs):**

```bash
nebius-vpngw prep-network --local-config-file <file>
```

Ensures the configured gateway subnet and route table exist. If `gateway_group.external_ips` is empty, reserves public IPs, prints them, and writes them into the YAML.
If `gateway_group.network_id` is set, the command targets that existing Nebius VPC; otherwise it uses the same auto-discovery logic as `apply`. The command is safe to rerun.

**Deployment:**

```bash
nebius-vpngw apply --local-config-file <file>
```

Deploy or update gateway. Automatically validates schema before deployment. Typical flow: parse args → load YAML → validate schema → ensure network/subnet → ensure VMs + allocations → push config via SSH → reload agent → reconcile routes (static mode).
The command is safe to rerun and reuses matching infrastructure state.

Flags: `--recreate-gw`, `--project-id`, `--zone`

**Peer Config Import (generate YAML only):**

```bash
nebius-vpngw create-from-peer-config <output-config-file> \
  --peer-config-file ./gcp-ha-vpn.txt \
  --peer-config-file ./aws-vpn.xml
```

Creates a new YAML config by merging vendor peer configs into the embedded template.
No deployment is performed; review and validate before running `apply`.
If the generated output already matches the target file, rerunning the command is a no-op and exits successfully.

**Status & Monitoring:**

```bash
nebius-vpngw status --local-config-file <file>
nebius-vpngw list-routes-local --local-config-file <file>
nebius-vpngw list-routes-remote --local-config-file <file>
nebius-vpngw add-routes-local --local-config-file <file>
```

For explicit VM HA, `status` appends exactly one concise table titled
`VM-HA Status — <OVERALL>`. Its `Gateway`, `Role`, `mTLS`, and `Ready` columns
report exactly the two configured gateways without cloud resource identities
or controller diagnostics. The title, mTLS, and readiness values use green for
good and red for non-good states while keeping literal text; the section is
informational and performs no recovery or ownership mutation.

**Default Behavior:**

- With config present: shows status
- No config: creates template from embedded source

### Peer Import & Merging

Vendor parsers (GCP/AWS/Azure/Cisco) normalize peer templates. Peer import overlays parsed values onto the template while keeping topology intact. Peer values replace template defaults when present; missing fields remain for manual review.

## Routing Modes & Local Prefixes

### Modes

- **BGP (preferred):** Dynamic routing with FRR, automatic route learning
- **Static:** Manual route configuration, simpler but less flexible

Global default under `defaults.routing.mode`; override per connection/tunnel.

### Local Prefixes

`gateway.local_prefixes` is the **single source of truth** for Nebius-side networks.

**BGP mode:** Advertised to peers when `advertise_local_prefixes: true`

**Static mode:** Used for VPC route management and included in leftsubnet selectors

### Nebius Managed Kubernetes Notes

For a typical Nebius Managed Kubernetes deployment, use the stable worker-subnet CIDR in `gateway.local_prefixes`, not the current per-node Pod CIDRs.

- Worker nodes and Pod IPs commonly share the same Nebius VPC subnet CIDR; the per-node Pod `/24`s are dynamic allocator artifacts, not a stable routing contract.
- `add-routes-local` operates at the subnet route-table layer. Pods do not need custom routes if the worker subnet is selected by overlap with `gateway.local_prefixes`.
- `ClusterIP` remains a cluster-internal virtual IP. Even if service VIPs fall inside the same advertised subnet, remote networks should not use `ClusterIP` over VPN.
- Current Nebius MK8s clusters commonly use Cilium with `routing-mode: native`, `enable-endpoint-routes: true`, and `kube-proxy-replacement: true`.
- Cilium commonly exempts private destinations in `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, and `169.254.0.0/16` from masquerading. For those destinations, remote peers may see Pod IPs as the source and must route/allow the cluster subnet accordingly.
- For stable remote consumption, use Pod IPs directly or expose services through a routable frontend (`NodePort`, `LoadBalancer`, or Ingress/Gateway), not `ClusterIP`.

### Remote Prefixes

`connection.remote_prefixes` has different semantics depending on routing mode:

| Routing Mode | remote_prefixes Usage |
| ------------ | --------------------- |
| **BGP** | Optional - acts as inbound filter/whitelist. If omitted, accepts all BGP routes. |
| **Static** | Required - used for kernel route installation via XFRM interfaces. |

**BGP mode:**

- **Optional** - BGP learns routes dynamically from peer
- If specified: Acts as **inbound filter** (prefix-list) - only listed prefixes accepted
- If omitted: All routes advertised by peer are accepted
- Routes installed automatically by FRR BGP daemon
- No manual enumeration needed for 100+ remote networks

**Static mode:**

- **Required** (or in `tunnel.static_routes.remote_prefixes`)
- Used for kernel route installation via XFRM interfaces (rightsubnet stays 0.0.0.0/0)
- Each remote network must be explicitly listed
- No dynamic learning

## IPsec Configuration

### strongSwan

- Route-based VPN using XFRM interfaces (default)
- IKEv2 default, IKEv1 optional (disabled by default)
- PSK authentication
- DPD (Dead Peer Detection) for tunnel liveness

### IPsec Interface Modes

The gateway supports two interface modes for IPsec tunnels:

#### XFRM Interface Mode (Default, Recommended)

Modern kernel XFRM netdevs bound to strongSwan CHILD_SAs via `if_id`:

- Creates `xfrm0`, `xfrm1`, etc. interfaces
- Each tunnel bound via `if_id_in/if_id_out` parameters (e.g., 100, 101)
- No marks or updown scripts required
- **Eliminates packet duplication** issue with 0.0.0.0/0 traffic selectors
- BGP sessions run over XFRM interfaces using APIPA inner IPs
- Cleaner architecture, better performance

**Configuration:**

```yaml
gateway:
  local_asn: 65010
  local_prefixes:
    - "10.0.0.0/16"
  ipsec_mode: xfrm-interface  # Default
```

**XFRM Setup:**

- strongSwan config uses `if_id_in=100, if_id_out=100` (no marks)
- Agent creates XFRM devices: `ip link add xfrm0 type xfrm dev eth0 if_id 100`
- Inner APIPA addresses assigned to XFRM interfaces for BGP peering
- MTU set on XFRM interfaces to parent MTU minus IPsec/NAT-T overhead (default 64 bytes)
  (e.g., 1450 -> 1386; can be rounded down to 1380 for extra headroom)

**MTU and PMTU Validation (Operational Guidance):**

- Effective tunnel MTU is the largest IP packet that can traverse the XFRM interface without fragmentation.
- Example: `eth0 MTU = 1450`, `xfrm MTU = 1386` (1450 - 64).
- ICMP overhead is 28 bytes (20 IP + 8 ICMP), so the maximum safe `ping -s` payload is:
  `1386 - 28 = 1358`.

```bash
# PMTU sanity check (should succeed)
ping -M do -s 1358 <remote-ip>

# If you round xfrm MTU down to 1380, use:
# ping -M do -s 1352 <remote-ip>
```

**Best practice before bulk transfers:**

- Keep TCP MSS clamping enabled on the gateway so forwarded TCP traffic never exceeds the route MTU.
- This is the production-safe way to avoid fragmentation for workload traffic.

```bash
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
```

### Permanent MTU Strategy (XFRM)

The gateway enforces a conservative MTU policy so workloads don't rely on PMTUD alone:

- Always enable TCP MSS clamping on the gateway
- Enable TCP MTU probing
- Set XFRM MTU to parent MTU minus IPsec/NAT-T overhead (default 64 bytes)
- Keep eth0 MTU unchanged
- Expect PMTU ~1380-1386 for GCP HA VPN with NAT-T

**Rules applied by the agent:**

```bash
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
# nftables equivalent:
nft add rule ip mangle forward tcp flags syn tcp option maxseg size set rt mtu
```

**Sysctl (persistent):**

```bash
net.ipv4.tcp_mtu_probing = 1
```

**Why XFRM:**

- Modern Linux kernel interface (5.4+)
- No packet duplication with 0.0.0.0/0 traffic selectors
- Cleaner separation: if_id binding vs mark-based routing
- Better performance and maintainability

#### VTI Mode (Removed)

Legacy Virtual Tunnel Interface support has been removed due to packet duplication issues and operational complexity. All deployments must use XFRM interfaces.

### Route-Based VPN Architecture

#### IPsec Traffic Selectors vs Routing

`leftsubnet` and `rightsubnet` in strongSwan define **only the IPsec Traffic Selectors (TS)** exchanged during IKE negotiation. They do **NOT install routes** and do **NOT control which networks are routed through the tunnel**.

**For route-based VPN (XFRM), use:**

```text
leftsubnet=<inner /30 + gateway.local_prefixes>
rightsubnet=0.0.0.0/0
```

leftsubnet is a comma-separated list of the tunnel inner /30 plus `gateway.local_prefixes`.

This configuration:

- Allows any remote prefix for the configured local selectors (inner /30 + `gateway.local_prefixes`)
- Permits the tunnel to carry:
  - BGP APIPA traffic (169.254.x.x)
  - All dynamically learned remote prefixes
  - All local prefixes advertised through BGP
  - Any number of enterprise networks (scalable)
- Does NOT create policy-based routing restrictions
- Eliminates the need for hundreds of traffic selectors

**Routing is controlled exclusively by:**

1. **Linux routing table:** `ip route add <prefix> dev xfrm0`
2. **BGP daemon (FRR):** Learned routes installed dynamically
3. **Static routes:** Manual kernel routes (in static mode)
4. **XFRM interfaces:** Bound via `if_id` from strongSwan

**What determines which traffic enters the tunnel:**

- **The routing table** → what prefixes point to the XFRM interface
- **BGP daemon** → which routes FRR installs dynamically
- **NOT** leftsubnet/rightsubnet → these are IPsec selector allow-lists, not route selection

**Why this matters:**

- **BGP scalability:** No need to reconfigure IPsec when remote networks change
- **Dynamic routing:** BGP can learn/install 100+ prefixes without IPsec restarts
- **APIPA support:** BGP peering IPs (169.254.x.x) work seamlessly
- **Simplified config:** No tunnel-level prefix enumeration required
- **Peer compatibility:** GCP HA VPN and AWS VPN require 0.0.0.0/0 selectors

**Encryption decision:**

- strongSwan binds CHILD_SAs to XFRM interfaces via `if_id`
- `ip link add xfrm0 type xfrm dev eth0 if_id <id>`
- Any packet routed through xfrmX gets encrypted by strongSwan
- No policy database (SPD) restrictions on prefixes

### local_prefixes vs remote_prefixes

The configuration fields `local_prefixes` and `remote_prefixes` have different meanings depending on the VPN mode:

| Mode   | local_prefixes → Remote Peer                               | remote_prefixes → Nebius VM                             |
|--------|------------------------------------------------------------|---------------------------------------------------------|
| BGP    | Advertised by FRR to peer via `network` statements         | Learned dynamically from peer BGP; no YAML required     |
| Static | Installed as static routes to XFRM interfaces + VPC routes | Installed as static kernel routes to XFRM interfaces    |

**BGP Mode:**

- `local_prefixes`: Networks advertised to the remote peer via BGP `network` statements in FRR
- `remote_prefixes`: Optional filter list; actual routes learned dynamically from BGP peer
- FRR installs all learned routes automatically

**Static Mode:**

- `local_prefixes`: Networks reachable via Nebius VPC; installed as kernel routes and VPC routes
- `remote_prefixes`: Networks behind the remote peer; installed as kernel routes to XFRM interfaces
- Agent installs routes explicitly: `ip route replace <prefix> dev xfrm0`

**Key Differences:**

- BGP: Remote prefixes are **dynamic** (no YAML config needed)
- Static: Remote prefixes must be **explicitly configured** in YAML
- Both modes: rightsubnet is 0.0.0.0/0; leftsubnet includes the inner /30 plus `gateway.local_prefixes`

### Crypto Proposals

**IKE (Phase 1):**

- `aes256gcm16-prfsha256-modp2048` (modern AEAD)
- `aes256-sha256-modp2048` (compatible)

**ESP (Phase 2):**

- `aes256gcm16-modp2048` (modern AEAD)
- `aes256-sha256-modp2048` (compatible)

## BGP Configuration

### FRR Setup

- `bgpd` daemon for BGP routing
- Runs over XFRM interfaces using APIPA inner IPs
- Configurable timers with a baseline 3:1 ratio (hold time = 3 × keepalive)
- Optional BFD for sub-second failure detection (disabled by default; peer support is vendor/platform specific)
- Graceful restart is optional; disable for faster withdrawal if needed
- Install policy: use FRR 10.x from the official repo without pinning a single build (repo rotations can remove older builds). Apply performs a fallback install if FRR is missing.

### Timer Best Practices (BGP + DPD)

- **3:1 baseline:** `hold_time = 3 × keepalive`, `dpd_timeout = 3 × dpd_interval`
- **Faster convergence:** Enable BFD instead of pushing BGP timers too low
- **Vendor check first:** BFD support and timer floors vary by peer; verify the peer docs before enabling it
- **Routing-first failover:** Keep BGP hold shorter than DPD timeout so routes withdraw before IPsec cleanup
- **Noisy links:** Increase timers if you see route flapping from transient loss

### APIPA Inner IPs

- Must use /30 subnet in 169.254.0.0/16 range
- Example: `169.254.10.0/30` → usable IPs are `.1` and `.2`
- `.0` is network address, `.3` is broadcast (unusable)
- Each tunnel requires unique /30 subnet

### BGP Session Requirements

1. IPsec tunnels must be ESTABLISHED first
2. XFRM interfaces must be up with assigned inner IPs
3. BGP peer must be reachable via inner_remote_ip
4. ASN configuration must match on both sides
5. FRR 10.x recommended (8.4.4 has route installation bugs)

### Common BGP Issues

- **No OPEN messages:** IPsec tunnel not established or XFRM interface down
- **OPEN errors:** ASN mismatch between peers
- **Routes not installed:** FRR 8.4.4 bug, upgrade to 10.x
- **Policy errors:** Add `no bgp ebgp-requires-policy` to config

### Active/Passive HA Mode

**Design Philosophy:**

The Nebius VPN Gateway operates in **Active/Passive mode** by default to guarantee symmetric routing for customer workloads. This ensures compatibility with default Linux and Windows networking stacks without requiring any workload-side configuration changes.

**Problem with ECMP (Equal-Cost Multi-Path):**

When both tunnels have equal BGP preference, the kernel uses ECMP load balancing:

- Parallel TCP flows (`iperf3 -P 4`) get distributed across tunnels
- Return packets may arrive via a different tunnel than outbound packets went
- Workload VMs with default `rp_filter` settings drop asymmetric return packets
- Result: Connection hangs, packet loss, intermittent failures

**Active/Passive Solution:**

| Tunnel Role | BGP local-preference | IPsec Status | BGP Status | Traffic Handling |
| ----------- | ------------------- | ------------ | ---------- | ---------------- |
| **Active** | 200 (higher = preferred) | UP | ESTABLISHED | Carries all data traffic |
| **Passive** | 100 (lower = standby) | UP | ESTABLISHED | Hot standby for failover |

**How It Works:**

1. **Both tunnels are UP** (IPsec CHILD_SA established, BGP sessions active)
2. **FRR applies route-map** with `local-preference` based on `ha_role` config:

   ```text
   route-map SET-LOCAL-PREF-200 permit 10
    set local-preference 200

   route-map SET-LOCAL-PREF-100 permit 10
    set local-preference 100
   ```

3. **Kernel routing table** installs only the higher-preference route (active tunnel)
4. **All flows** use the same tunnel → symmetric return paths → no rp_filter drops
5. **Automatic failover:** BGP switches to passive within the hold timer; with BFD enabled this can be sub-second
6. **Traffic selectors** include the tunnel inner /30 plus `gateway.local_prefixes` on **both** active and passive tunnels so the passive tunnel can carry data immediately after failover.

**Configuration Example:**

```yaml
defaults:
  # Optional; defaults to active-passive if omitted
  ha_mode: "active-passive"

connections:
  - name: "gcp-ha-vpn"
    routing_mode: bgp
    tunnels:
      - name: "gcp-ha-tunnel-1"
        ha_role: "active"   # Primary tunnel (local-pref 200)
        # ... tunnel config ...
      - name: "gcp-ha-tunnel-2"
        ha_role: "passive"  # Standby tunnel (local-pref 100)
        # ... tunnel config ...
```

**Tunnel Mode Configuration Reference:**

| Desired Mode | Config Required | Description |
| ------------ | --------------- | ----------- |
| **active** | `ha_role: "active"` **OR** omit the field (default) | Primary tunnel with BGP local-preference 200. **Keep only one tunnel active at a time** to ensure symmetric routing. |
| **passive** | `ha_role: "passive"` (**must be explicit**) | Standby tunnel with BGP local-preference 100. Provides hot standby for automatic failover. |
| **disable** | `ha_role: "disable"` (**must be explicit**) | Tunnel is completely skipped (no IPsec, no BGP). Use for maintenance or cost optimization. |

**Important:** The Active/Passive design requires **exactly one active tunnel** per connection **per gateway instance** to guarantee symmetric routing. Schema validation enforces this, and `defaults.ha_mode` is **required** and locked to `"active-passive"` (the only supported mode in current releases). If you omit `ha_role` on multiple tunnels, they will all default to `"active"` and create ECMP routing, which defeats the purpose of this design.

**Gateway-group boundary:** This tunnel rule stops at the gateway-VM boundary.
Two independent VMs with the same active prefixes still create the same
multipath/asymmetric-routing risk. `gateway_group` alone remains an orchestration
grouping; only the explicit two-member `gateway_group.vm_ha` contract adds shared
allocation ownership, fencing, guarded forwarding, and owner-only route
reconciliation. VM HA remains independent from tunnel `ha_role`.

**Multi-connection note:** The Active/Passive rule is scoped per connection, not globally across the gateway VM. This is intentional for multi-site topologies where each connection usually represents a different remote site and a different set of prefixes. If two different active connections learn the same prefix, FRR can still install live multipath for that overlapping prefix. Current releases surface that condition as a warning in `nebius-vpngw status`; operators should treat it as a routing-domain overlap to fix, not as the intended steady state.

**Implementation note:** To allow the passive tunnel to carry data immediately after failover, **both tunnels** include `gateway.local_prefixes` in traffic selectors. This requires `if_id_in/if_id_out` binding via `swanctl` (VICI). The legacy `ipsec.conf` parser does **not** support `if_id_*`, so `swanctl` is mandatory for deterministic XFRM selection.

**Benefits:**

- ✅ **No workload VM changes required** (rp_filter stays at default)
- ✅ **Works with any OS** (Linux, Windows, RHEL, Ubuntu)
- ✅ **Fast failover** (BGP detects failure and switches routes)
- ✅ **Scalable** (handles `iperf3 -P 100` without packet loss)
- ✅ **Production-proven** (same design as AWS VGW, Azure VPN Gateway, Cisco/Juniper)

**Verification:**

After deployment, check that only one route is active:

```bash
# On VPN gateway:
ip route show 10.10.0.0/24
# Expected: Single nexthop via active tunnel
10.10.0.0/24 via 169.254.18.225 dev xfrm0 proto bgp metric 20

# On workload VM:
iperf3 -c 10.10.0.2 -t 10 -i 1 -P 4
# Expected: No packet loss, stable throughput
```

**Migration from ECMP:**

If you have existing ECMP configuration (both tunnels set to `ha_role: "active"`):

1. Change one tunnel to `ha_role: "passive"` in your YAML config
2. Deploy the updated configuration
3. BGP will converge within one hold-time period (default 6 seconds, or faster with BFD)
4. Verify with `ip route show` and test with `iperf3 -P 4`

### BGP MED (Multi-Exit Discriminator) for Peer-Side Path Selection

**What is MED?**

BGP MED (Multi-Exit Discriminator) is a BGP attribute that influences which path a remote peer chooses when multiple paths exist to the same destination. Unlike `local-preference` (which affects LOCAL routing decisions), MED is transmitted TO the peer and affects THEIR routing decisions.

- **Lower MED = preferred path** (opposite of local-preference where higher = preferred)
- **MED is non-transitive**: Not passed between AS boundaries (only visible to immediate peer)
- **Default MED = 0** if not set explicitly

**Active/Passive Design - Two Mechanisms Working Together:**

The Nebius VPN Gateway uses **both** local-preference and MED to enforce Active/Passive routing in **both directions**:

1. **Local-preference (inbound)**: Controls Nebius → GCP routing (Nebius egress traffic)
   - Applied to routes **received FROM** GCP
   - Active tunnel: local-pref 200 (Nebius prefers this path for outbound)
   - Passive tunnel: local-pref 100 (Nebius uses as backup)

2. **MED (outbound)**: Controls GCP → Nebius routing (GCP's return traffic)
   - Applied to routes **sent TO** GCP (Nebius local prefixes)
   - Active tunnel: MED=0 (GCP prefers this path for return traffic)
   - Passive tunnel: MED=100 (GCP uses as backup)

| Tunnel Role | Local-Pref (Inbound) | MED (Outbound) | Nebius Routing Decision | GCP Routing Decision |
| ----------- | -------------------- | -------------- | ----------------------- | -------------------- |
| **active** | 200 (prefer routes from GCP) | 0 (GCP prefers routes to Nebius) | Uses active tunnel for **egress** | Uses active tunnel for **return traffic** |
| **passive** | 100 (deprioritize routes from GCP) | 100 (GCP deprioritizes routes to Nebius) | Uses as backup | Uses as backup |

**Result**: **Symmetric routing** - both directions use the same tunnel, no ECMP, no asymmetric routing, no `rp_filter` issues.

**How It Works:**

1. **Inbound route-maps (local-preference)**: Control Nebius → GCP path selection

   ```text
   route-map SET-LOCAL-PREF-200 permit 10
    set local-preference 200  # Prefer routes learned from active tunnel

   route-map SET-LOCAL-PREF-100 permit 10
    set local-preference 100  # Deprioritize routes learned from passive tunnel

   neighbor 169.254.18.225 route-map SET-LOCAL-PREF-200 in   # Active tunnel
   neighbor 169.254.5.153 route-map SET-LOCAL-PREF-100 in    # Passive tunnel
   ```

2. **Outbound route-maps (MED)**: Control GCP → Nebius path selection

   ```text
   route-map ADVERTISE-ACTIVE permit 10
    match ip address prefix-list ADVERTISE-LOCAL
    set metric 0  # MED=0 sent to GCP (GCP prefers this path)

   route-map ADVERTISE-PASSIVE permit 10
    match ip address prefix-list ADVERTISE-LOCAL
    set metric 100  # MED=100 sent to GCP (GCP deprioritizes this path)

   neighbor 169.254.18.225 route-map ADVERTISE-ACTIVE out   # Active tunnel
   neighbor 169.254.5.153 route-map ADVERTISE-PASSIVE out   # Passive tunnel
   ```

3. **Peer behavior**: GCP Cloud Router receives Nebius routes with different MED values:
   - Route via active tunnel: `10.49.0.0/16` with MED=0 → **GCP prefers this path**
   - Route via passive tunnel: `10.49.0.0/16` with MED=100 → **GCP uses as backup**

4. **No GCP configuration needed**: GCP automatically uses MED for path selection - no manual configuration required on GCP side

**GCP Cloud Router Verification:**

GCP Cloud Router displays learned routes with their MED values converted to "priority" (lower = better):

```bash
gcloud compute routers get-status ROUTER_NAME --region=REGION --project=PROJECT_ID

# Example output:
bestRoutes:
- destRange: 10.49.0.0/16
  nextHopIp: 169.254.18.226  # Active tunnel
  priority: 0                 # MED=0 from Nebius

- destRange: 10.49.0.0/16
  nextHopIp: 169.254.5.154    # Passive tunnel
  priority: 100               # MED=100 from Nebius
```

**Verification Commands:**

**On Nebius Gateway - Check Outbound Advertisements (MED):**

```bash
# Verify MED values being sent TO GCP:
sudo vtysh -c "show bgp ipv4 unicast neighbors 169.254.18.225 advertised-routes"
sudo vtysh -c "show bgp ipv4 unicast neighbors 169.254.5.153 advertised-routes"

# Look for "metric" field - should see 0 for active, 100 for passive:
# *> 10.49.0.0/16     0.0.0.0                            0         32768 ?
#    Advertised to: 169.254.18.225
#    metric 0  <-- Active tunnel
```

**On Nebius Gateway - Check Inbound Routes (Local-Preference):**

```bash
# Verify local-preference for routes received FROM GCP:
sudo vtysh -c "show bgp ipv4 unicast"

# Look for routes with different LocPrf values:
# *>  10.10.0.0/24     169.254.18.225         100    200      0 65014 ?  # Active (LocPrf 200)
# *   10.10.0.0/24     169.254.5.153          100    100      0 65014 ?  # Passive (LocPrf 100)
```

**On GCP Cloud Router - Check Learned Routes (MED):**

```bash
# Verify GCP is receiving and using MED values from Nebius:
gcloud compute routers get-status ROUTER_NAME --region=REGION --project=PROJECT_ID

# Look for your Nebius prefixes with different priorities:
# Routes learned FROM Nebius (GCP's perspective):
# - destRange: 10.49.0.0/16
#   nextHopIp: 169.254.18.226  # Active tunnel
#   priority: 0                 # Best route (MED=0)
#
# - destRange: 10.49.0.0/16
#   nextHopIp: 169.254.5.154    # Passive tunnel
#   priority: 100               # Backup (MED=100)
```

**On GCP Console:**

Navigate to: **Hybrid Connectivity → VPN → Cloud Routers → [Your Router] → Details Tab → Learned Routes**

You should see your Nebius prefix (e.g., `10.49.0.0/16`) with:

- One route with **priority 0** or **MED 0** (active tunnel)
- One route with **priority 100** or **MED 100** (passive tunnel)

**Important Notes:**

- **No GCP configuration required**: Nebius sets MED outbound, GCP automatically uses it for path selection
- **Symmetric routing guaranteed**: Both directions use the same tunnel (active)
- **Automatic configuration**: MED and local-preference automatically derived from `ha_role`
- **BGP import-check disabled**: Uses `no bgp network import-check` to allow advertising `local_prefixes` without kernel routes

**Troubleshooting:**

If GCP still shows both routes with same priority:

1. **Verify MED is being sent**:

   ```bash
   sudo vtysh -c "show bgp ipv4 unicast neighbors <peer-ip> advertised-routes"
   ```

   Look for "metric" field in output

2. **Check BGP sessions are established**:

   ```bash
   sudo vtysh -c "show bgp summary"
   ```

   Both neighbors should show "Established" state

3. **Verify route-maps are applied**:

   ```bash
   sudo vtysh -c "show running-config" | grep -A 5 "route-map"
   ```

   Should see ADVERTISE-ACTIVE and ADVERTISE-PASSIVE with different metric values

4. **Check GCP learned routes**:

   ```bash
   gcloud compute routers get-status ROUTER_NAME --region=REGION
   ```

   Should show different priority values (0 vs 100)

5. **Test with tcpdump**: Confirm packets enter/exit via the same tunnel interface:

   ```bash
   sudo tcpdump -i xfrm0 -n icmp  # Should see both directions
   sudo tcpdump -i xfrm1 -n icmp  # Should see nothing or minimal backup traffic
   ```

## Failover

### Automatic Failover (Active/Passive)

- Both tunnels stay UP (IPsec + BGP), but only the **active** path is used for data.
- **Local selection:** FRR applies `local-preference` (active 200, passive 100) to pick the active path.
- **Peer selection:** MED (active 0, passive 100) nudges the peer to return on the same active tunnel.
- **Failure detection order (fastest → slowest):**
  - **BFD (optional):** sub-second detection when supported by the peer.
  - **BGP hold timer:** default 6s (keepalive 2s) when BFD is not active.
  - **DPD:** default 5s/15s (control-plane cleanup).

**Design rule:** Keep `BGP hold < DPD timeout` so routes withdraw before IPsec cleanup.

**BFD compatibility:** Treat BFD as an explicit peer capability, not a generic BGP feature. Enable it only when the peer vendor/platform docs say BFD is supported for that specific VPN/BGP workflow and the negotiated timers are compatible.

### Manual Failover (CLI)

Use the CLI to force traffic onto the passive tunnel by shutting down the active BGP neighbor (IPsec stays up):

```bash
# If exactly two tunnels exist, auto-select the passive tunnel
nebius-vpngw failover tunnel --local-config-file <file>

# If more than two tunnels exist, pass the passive tunnel name explicitly
# Multi-connection topologies normally fall into this explicit-selection path.
nebius-vpngw failover tunnel <passive-tunnel-name> --local-config-file <file>
```

The CLI resolves the selected tunnel back to its owning connection and
`gateway_instance_index`, then applies the BGP neighbor change only on that
gateway VM.

Tunnel names are required to be globally unique across the full config, so the
operator commands can safely target a tunnel by name without also requiring the
connection name.

The CLI help text is expected to mirror this model: operator commands should
describe tunnel-name selection in terms of the owning connection/instance, and
route-listing help should describe BGP output as scoped to the selected
connection on the owning gateway VM.

Configured active/passive roles remain declarative. `failover tunnel` is an
operational override that preserves the configured roles and lets
`failback tunnel` restore the configured steady state without rewriting YAML.

```bash
nebius-vpngw failback tunnel <active-tunnel-name> --local-config-file <file>
```

**Restore active tunnel:**

```bash
sudo vtysh -c "configure terminal" -c "router bgp <ASN>" -c "no neighbor <peer-ip> shutdown"
```

Or reapply config / restart FRR to reset running state.

## Static Routes Configuration

### VPC Route Management

Three route management commands with distinct purposes:

**1. Add local routes (Nebius VPC → Remote):**

```bash
nebius-vpngw add-routes-local --local-config-file <file>
```

Creates VPC route table entries for remote networks and selects the next-hop
from the gateway VM that owns each connection.

**Implementation Details:**

- **BGP mode**: Queries BGP-learned routes from the gateway VM(s) that own the target connection via SSH (`vtysh -c 'show bgp ipv4 unicast json'`)
  - Filters by `remote_prefixes` whitelist if configured
  - Filters out locally originated routes (next-hop 0.0.0.0)
  - Filters out overlapping local networks (from `gateway.local_prefixes`)
- **Static mode**: Uses `remote_prefixes` from YAML configuration
- Skips remote prefixes that overlap the target network's private pools before calling the VPC API
- Sanitizes inherited subnet status CIDRs against explicit CIDRs owned by other subnets before matching `gateway.local_prefixes`
- If the destination CIDR already exists in the route table with a different next-hop, warns and leaves that route unchanged
- `--summarize` only merges routes when they already form an exact larger CIDR
  block and use the same gateway next-hop allocation
  - Example: `10.0.0.0/24` + `10.0.1.0/24` -> `10.0.0.0/23`
  - It does not invent broader supernets when prefixes have gaps or different next-hops
- A later `add-routes-local` run without `--summarize` reconciles back to exact
  managed routes and prunes broader `vpngw-*` summaries only after the exact
  routes under them are confirmed installed
- `--swap-route-table` is an explicit blue/green mode:
  - creates a fresh custom route table for each selected subnet
  - copies only non-`vpngw-*` routes from the currently attached table
  - rebuilds managed VPN routes from the current YAML on the fresh table
  - validates preserved/manual routes and desired managed routes before cutover
  - reattaches the subnet only after the replacement table passes validation
  - writes a rollback spec file and prints a `nebius vpc subnet update --file ...`
    command that restores the previously attached route table
  - the live CLI `--help` text explicitly calls out the validation-before-cutover
    and rollback-command behavior for operators
- Finds workload subnets whose effective CIDRs match `gateway.local_prefixes`
- Resolves private IP allocations per gateway VM via Compute API
- Creates/reuses custom route tables for matching subnets
  - If subnet uses default route table: Creates custom RT and copies existing routes
  - Warns user about route table separation
- Large learned route sets can still hit Nebius per-route-table limits even
  when the tenant-wide `vpc.route.count` visible in the console is below quota. The API error
  `vpc.routetable.max-route-count` means the target subnet route table is full.
- Includes inherited parent-network subnets (`use_network_pools=true`) only after sanitizing status CIDRs against explicit CIDRs owned by other subnets
- Creates route entries: destination = remote prefix, next-hop = the owning gateway VM's private IP
- Implements idempotency (skips existing exact routes and cleans up prior
  broader `vpngw-*` summaries when plain exact-route reconciliation is requested)
- Requires explicit operator confirmation before `--swap-route-table` mutates any
  subnet attachments, and warns about brief traffic impact if the replacement
  table is incomplete or subnet reassignment converges slowly
- Keeps `list-routes-local` observational while `add-routes-local` may repair
  proven FRR/BGP advertisement drift against the already-installed config under
  exact stable owner, allocation, generation, and ownership-epoch authority

**2. List local routes (Nebius VPC → Remote):**

```bash
nebius-vpngw list-routes-local --local-config-file <file>
```

Lists VPC route table entries for workload subnets whose effective CIDRs match `gateway.local_prefixes`.

**Implementation Details:**

- Queries VPC API for workload subnets whose explicit or inherited effective CIDRs match `gateway.local_prefixes`
- Displays route table ID and routes for each subnet
- Shows destination CIDR and next-hop (resolves allocation IDs to IP addresses)
- Uses Rich tables for formatted output
- Compares bounded per-peer advertised-route evidence with the owner-aware
  expectation and reports `MATCH`, `DRIFT`, or `UNKNOWN`
- Performs no remote write, upload, service start/reload, or reconciliation wait;
  incomplete peer or VM-HA authority evidence remains `UNKNOWN`

**3. List remote routes (Remote → Nebius):**

```bash
nebius-vpngw list-routes-remote --local-config-file <file>
```

Lists routes on gateway VMs that direct traffic from remote sites to Nebius networks.

**Implementation Details:**

- **BGP mode**:
  - SSHs to gateway VMs and queries FRR: `vtysh -c 'show bgp ipv4 unicast json'`
  - Scopes displayed/imported paths to the selected connection's tunnel peer IPs on the owning gateway VM
  - When showing locally advertised routes, matches peer IPs against the owning gateway VM as well so repeated APIPA ranges on other instances do not mislabel connection/tunnel output
  - Extracts routes with next-hop IPs, AS paths, and status
  - Queries `ip route get <next-hop>` to determine outgoing XFRM interface
  - Filters out locally originated routes (next-hop 0.0.0.0)
  - Checks against `remote_prefixes` whitelist (shows allowed/not-allowed status)
  - Displays: Prefix, Next-Hop, Via (XFRM interface), AS Path, Status
- **Static mode**:
  - Agent installs kernel routes: `ip route replace <prefix> dev xfrmX` for each `remote_prefixes`
  - Compares YAML `remote_prefixes` with kernel routing table via `ip route show`
  - Shows installation status (installed/missing)
  - Routes installed automatically by strongSwan renderer after tunnel establishment

**Static Mode Route Installation:**

In static mode, the agent automatically installs kernel routes for all `remote_prefixes`:

```bash
# Example: For remote_prefixes: ["10.10.0.0/24", "10.11.0.0/16"]
ip route replace 10.10.0.0/24 dev xfrm0
ip route replace 10.11.0.0/16 dev xfrm0
```

This happens in `strongswan_renderer.py` after tunnel establishment.

**BGP vs Static Routing:**

| Aspect | BGP Mode | Static Mode |
| ------ | -------- | ----------- |
| **Traffic Selectors** | `0.0.0.0/0` (both sides) | `0.0.0.0/0` (both sides) |
| **Kernel Routes** | Installed by FRR BGP | Installed by agent from YAML |
| **remote_prefixes** | Optional filter/whitelist | Required, installed as routes |
| **Dynamic Learning** | Yes (via BGP) | No (manual YAML updates) |
| **Scalability** | 100+ networks, no config changes | Must enumerate each network |

## XFRM Routing Stack

### Architecture Overview

The VPN gateway uses a **multi-layer defense** strategy to ensure routing stability for XFRM (IPsec) tunnels:

1. **Dedicated Sysctl Configuration** (`/etc/sysctl.d/99-zzz-vpngw.conf`)
2. **Systemd Service Ordering** (UFW → strongSwan → FRR → agent)
3. **Self-Healing Routing Guard** (automatic sysctl enforcement)

This design **decouples routing correctness from UFW status**, making the gateway resilient to service failures and configuration changes.

### Critical Sysctl Settings

File: `/etc/sysctl.d/99-zzz-vpngw.conf`

```bash
# 1. IP Forwarding - Gateway must route packets
net.ipv4.ip_forward = 1

# 1.1 TCP MTU probing - recover when PMTUD is blocked
net.ipv4.tcp_mtu_probing = 1

# 2. Reverse Path Filtering - MUST BE DISABLED
# XFRM tunnels create asymmetric routing that strict rp_filter blocks
net.ipv4.conf.all.rp_filter = 0
net.ipv4.conf.default.rp_filter = 0
net.ipv4.conf.eth0.rp_filter = 0
net.ipv4.conf.lo.rp_filter = 0

# 3. ICMP Redirects - Disabled (cloud fabric shouldn't redirect)
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# 4. Source Routing - Disabled for security
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# 5. Martian Logging - Enabled for debugging
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1

# 6. IPv6 Hygiene
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_ra = 0
net.ipv6.conf.default.accept_ra = 0
```

**Why `99-zzz-vpngw.conf`?**

- The `zzz` prefix ensures it loads **after** `/etc/sysctl.conf` (via `99-sysctl.conf` symlink)
- This prevents the default Ubuntu sysctl settings from overriding our XFRM-specific values
- Cloud-init also comments out conflicting lines in `/etc/sysctl.conf`

### Systemd Service Ordering

**Boot Sequence:**

```text
network-online.target
    ↓
cloud-init.service
    ↓
sysctl --system (loads 99-zzz-vpngw.conf)
    ↓
ufw.service (firewall)
    ↓
nebius-vpngw-esp4-preflight.service
    ↓
strongswan-starter.service (IPsec)
    ↓
frr.service (BGP)
    ↓
nebius-vpngw-agent.service (routing guard + agent)
```

**Configuration Files:**

Each service has a systemd override in `/etc/systemd/system/<service>.d/override.conf`:

```ini
# /etc/systemd/system/ufw.service.d/override.conf
[Unit]
After=network-online.target cloud-init.service
Wants=network-online.target

# /etc/systemd/system/strongswan-starter.service.d/override.conf
[Unit]
After=nebius-vpngw-esp4-preflight.service ufw.service network-online.target
Wants=ufw.service
Requires=nebius-vpngw-esp4-preflight.service

# /etc/systemd/system/frr.service.d/override.conf
[Unit]
After=strongswan.service
Wants=strongswan.service

# /etc/systemd/system/nebius-vpngw-agent.service.d/override.conf
[Unit]
After=strongswan.service frr.service
Wants=strongswan.service frr.service
```

**Why This Ordering Matters:**

- **UFW after cloud-init**: Prevents cloud-init network changes from racing with UFW
- **ESP4 preflight before strongSwan**: Ensures the IPv4 ESP kernel module is
  loadable before strongSwan tries to install CHILD_SAs into XFRM
- **strongSwan after UFW**: Ensures netfilter framework is initialized before IPsec tunnels
- **FRR after strongSwan**: BGP needs XFRM interfaces created by strongSwan
- **Agent after FRR**: Routing guard validates routes installed by FRR

### Self-Healing Routing Guard

The `routing_guard.py` module enforces routing invariants on **every agent startup/reload**:

#### INVARIANT 0: Sysctl Enforcement

```python
# Automatically fixes sysctls if they get reset
net.ipv4.ip_forward = 1 (if currently 0)
net.ipv4.conf.all.rp_filter = 0 (if currently 1 or 2)
net.ipv4.conf.xfrm*.rp_filter = 0 (all XFRM interfaces)
```

#### INVARIANT 1: No Policy Routing

- Removes table 220 rules (cloud platforms sometimes add these)
- Flushes table 220 routes

#### INVARIANT 2: No Broad APIPA Routes

- Removes `169.254.0.0/16` route if present
- Keeps metadata-specific routes (`169.254.169.0/24`)

#### INVARIANT 3: No Scope Link Routes

- Removes `scope link` routes for local prefixes
- These mark prefixes as "directly connected" which breaks forwarding

#### INVARIANT 4: Clean Orphaned Routes

- Removes APIPA routes not defined in config
- Prevents leftover routes from old tunnels

#### INVARIANT 5: BGP Peer Routes

- Ensures `/32` routes for BGP peers via XFRM interfaces
- Required for correct source IP selection

**Logs Example:**

```text
[RoutingGuard] ✓ All invariants OK. BGP peer routes: 2
[RoutingGuard] Fixed 2 sysctls: net.ipv4.ip_forward, net.ipv4.conf.all.rp_filter
```

### Verification Commands

**Check Sysctl Settings:**

```bash
sysctl net.ipv4.ip_forward  # Must be 1
sysctl net.ipv4.conf.all.rp_filter  # Must be 0
sysctl net.ipv4.conf.eth0.rp_filter  # Must be 0
```

**Check Service Order:**

```bash
systemctl list-dependencies nebius-vpngw-agent.service | grep -E "ufw|strongswan|frr"
```

**Check Routing Guard Logs:**

```bash
sudo journalctl -u nebius-vpngw-agent -n 50 | grep RoutingGuard
```

**Check for Problematic Routes:**

```bash
# Should NOT exist:
ip route show table 220  # Empty
ip route show 169.254.0.0/16  # Empty or metadata-specific
ip rule show | grep 220  # Empty

# Should exist:
ip route show 169.254.169.0/24  # Metadata service OK
```

### Troubleshooting

#### Symptom: VMs can't reach remote networks

1. Check sysctl settings:

   ```bash
   sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter
   ```

   - If `ip_forward=0` or `rp_filter≠0`, routing won't work

2. Check UFW status:

   ```bash
   sudo ufw status
   ```

   - Must show `Status: active`

3. Check service ordering:

   ```bash
   systemctl status ufw strongswan frr nebius-vpngw-agent
   ```

   - All should be `active (running)` or `active (exited)` for UFW

4. Restart agent to enforce invariants:

   ```bash
   sudo systemctl restart nebius-vpngw-agent
   sudo journalctl -u nebius-vpngw-agent -n 30 | grep -E "RoutingGuard|sysctl"
   ```

#### Symptom: Sysctls reset after reboot

- Check `/etc/sysctl.d/99-zzz-vpngw.conf` exists and has correct settings
- Check `/etc/sysctl.conf` doesn't have conflicting `ip_forward` or `rp_filter` (should be commented)
- Run `sudo sysctl --system` to reload all sysctl files

#### Symptom: Services start in wrong order

- Check systemd overrides exist:

  ```bash
  ls -la /etc/systemd/system/{ufw,strongswan,frr,nebius-vpngw-agent}.service.d/
  ```

- Run `sudo systemctl daemon-reload` after creating overrides

## Security Hardening

### Applied via cloud-init at VM Creation

- SSH key-only authentication, root login disabled
- Fail2ban for SSH intrusion prevention
- UFW firewall (allows IPsec UDP 500/4500, ESP)
- auditd for command and config file monitoring
- Automated security updates (unattended-upgrades)
- ESP4 module preflight that removes only temporary `esp4` deny rules, leaves
  `esp6`/`rxrpc` policy untouched, and gates VPN services until `esp4` is
  loadable after any required reboot
- IP forwarding enabled, ICMP redirects disabled

### CRITICAL: UFW Must Be Active

**UFW (Uncomplicated Firewall) MUST be active and enabled for the VPN gateway to function correctly.**

**Why UFW is Required:**

1. **Netfilter Framework Initialization**: UFW activates the Linux netfilter framework, which is essential for proper packet forwarding through XFRM (IPsec) tunnels.

2. **VPC Fabric Integration**: Without UFW active, packets from the Nebius VPC fabric may not be correctly routed through the VPN gateway to remote networks, even with `net.ipv4.ip_forward=1` enabled.

3. **XFRM Tunnel Forwarding**: UFW's FORWARD chain rules are necessary for the kernel to properly handle packets destined for XFRM interfaces (xfrm0, xfrm1, etc.).

**Verification After Deployment:**

```bash
# Check UFW is active (REQUIRED)
sudo ufw status verbose

# Should show: Status: active
# If inactive, the VPN gateway will NOT forward traffic correctly
```

**Symptoms of Inactive UFW:**

- VMs in local subnets cannot reach remote networks via VPN
- Packets never reach the gateway VM (zero iptables counters)
- XFRM encryption counters don't increment
- BGP and IPsec tunnels work, but data plane fails

**Recovery if UFW is Inactive:**

```bash
# Re-apply firewall config via agent (preferred)
sudo systemctl restart nebius-vpngw-agent

# If UFW was disabled manually:
sudo ufw enable
```

### Firewall Management

**Default Firewall:** UFW (Uncomplicated Firewall) is the default and required firewall solution.

**Automatic Configuration:** The gateway VM automatically configures and enables UFW during deployment via cloud-init with the following rules:

**Required Ports:**

- **UDP 500** - IKE (Internet Key Exchange) for IPsec tunnel establishment
- **UDP 4500** - IPsec NAT-T (NAT Traversal) for ESP over UDP when behind NAT
- **ESP (IP Protocol 50)** - Encapsulating Security Payload for encrypted VPN data
- **TCP 179** - BGP for dynamic routing only (over xfrm* only; not exposed on public interface)
- **TCP 22** - SSH for management access (can be restricted to management CIDRs)
- **ICMP** - For path MTU discovery and troubleshooting

**TCP MSS Clamping (mandatory for XFRM):**

The gateway clamps MSS for forwarded TCP traffic to avoid oversized packets:

```bash
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
# nftables equivalent:
nft add rule ip mangle forward tcp flags syn tcp option maxseg size set rt mtu
```

**Traffic Rules:**

- **Default policy:** Deny incoming, allow outgoing
- **Loopback:** Unrestricted (localhost communication)
- **SSH access:** Restricted to management CIDRs when configured, otherwise from anywhere (protected by fail2ban)
- **IPsec protocols:** Allowed from peer gateway public IPs (UDP 500, 4500, ESP)
- **BGP:** Allowed only on tunnel interfaces (xfrm*); no TCP/179 on public interface
- **Local VPC subnets:** Traffic from `gateway.local_prefixes` allowed for forwarding through the gateway
- **Tunnel interfaces (xfrm*):** Unrestricted traffic allowed (BGP runs over these encrypted channels)
- **ICMP:** Allowed on public interface for troubleshooting
- **ICMP frag-needed:** Explicitly allowed (input/output) to support PMTUD when available

**BGP (TCP/179) scope:**

- BGP peers use APIPA inner IPs (e.g., `169.254.18.226 ↔ 169.254.18.225`)
- These APIPA addresses are assigned to xfrm interfaces (xfrm0, xfrm1, ...)
- They are reachable only after IPsec decryption; TCP/179 is never opened on eth0

**Interface-specific rules (conceptual):**

```text
eth0 (public):
  allow udp/500 from <peer_public_ips>
  allow udp/4500 from <peer_public_ips>
  allow esp from <peer_public_ips>
  allow tcp/22 from <management_cidrs> (or anywhere if unset)
  allow icmp

xfrm*:
  allow all (includes tcp/179 between APIPA peers)
```

**Peer Gateway Expectations:**

- **IPsec/IKE:** Allow UDP 500 and UDP 4500 plus ESP (IP protocol 50) between the peer gateway public IP(s) and Nebius gateway public IP(s)
- **Dynamic routing (BGP only):** Allow TCP 179 only on the tunnel interface between inner tunnel IPs (APIPA `169.254.x.x/30`)
- **Static routing:** No BGP/TCP 179 required; IPsec/IKE + workload rules are sufficient
- **ICMP (optional):** Allow ICMP between inner tunnel IPs if using ping-based tunnel health checks
- **Workload/application traffic:** Allow required application ports between private subnets on both sides (managed cloud VPNs typically only need these VPC firewall rules; e.g., GCP HA VPN/Cloud Router handles IKE/IPsec and BGP on the managed gateway when using dynamic routing)

**Routing note (shared routing domain):** If multiple Nebius gateways connect to the same routing domain (e.g., a single Cloud Router/VPC), ensure each gateway advertises distinct `gateway.local_prefixes`. Overlapping prefixes will conflict and only one path will be selected.

**Dynamic Updates:** The agent (`firewall_manager.py`) synchronizes UFW rules with active tunnels:

- Adds peer IPs dynamically as tunnels are configured
- Removes stale peer IPs when tunnels are removed
- Updates local prefix rules when configuration changes
- Maintains firewall state in `/etc/vpngw_peer_ips`, `/etc/vpngw_mgmt_cidrs`, and `/etc/vpngw_local_prefixes`

**Security Benefits:**

- Limits attack surface by denying all non-essential inbound traffic
- Protects against unauthorized access while allowing legitimate VPN traffic
- Prevents accidental exposure of management interfaces
- Enables traffic forwarding for VPC workloads without compromising security

### Routing Guard

Production-grade validation:

- Removes table 220 policy routes (causes asymmetric routing)
- Detects broad APIPA routes (169.254.0.0/16)
- Identifies orphaned routes (routes without active tunnels)
- Structured logging with metrics

## Agent State Management

### Idempotent Applies

Agent compares desired state with `/etc/nebius-vpngw/last-applied.json`:

- Only renders configs if state changed
- Only reloads services if configs changed
- Atomic file updates with temp files

### Service Management

- `nebius-vpngw-agent.service`: Main agent daemon
- `strongswan-starter.service`: IPsec daemon
- `frr.service`: Routing daemon

Reload triggers: `systemctl reload` (SIGHUP)

## Monitoring & Status

### Status Command

```bash
nebius-vpngw status --local-config-file <file>
```

**Reports:**

- Tunnel status (ESTABLISHED, CONNECTING, etc.)
- BGP session state and prefix counts
- Service health (agent, strongSwan, FRR)
- Routing table health (table 220, APIPA routes over XFRM interfaces, orphaned routes)

For multi-connection configs, `status` keeps configured role in the compact
primary table and prints a `Traffic Override` section when runtime behavior
differs from the configured active/passive preference. Tunnel names fold
instead of ellipsizing when the terminal is narrow. When FRR reports live
multipath for the same prefix across different active connections, `status`
also prints an `ECMP Warning` section that names the overlapping prefix and the
active tunnel names carrying it.

### Tunnel Status

Per-tunnel information:

- Gateway VM name
- Peer IP address
- Encryption algorithm
- Uptime
- BGP state (for BGP tunnels)

### System Health

Service status for each gateway VM:

- `nebius-vpngw-agent`: active/failed
- `strongswan-starter`: active/failed
- `frr`: active/failed

### Routing Health

Per-VM routing validation:

- Table 220 check: OK/WARNING (policy routes cause asymmetric routing)
- Broad APIPA detection: OK/WARNING (should be /30 subnets only)
- BGP peer routes: Shows APIPA routes over XFRM interfaces
- Orphaned routes count
- Overall health: Healthy/Degraded

### Tunnel Keepalive & Health Monitoring

**Purpose:** Detect and automatically recover from IPsec tunnel state desynchronization issues where tunnels appear ESTABLISHED in strongSwan but the XFRM interface drops all packets.

**Problem:** In rare cases (observed during load testing), IPsec CHILD_SAs enter a stale state where:

- `ipsec statusall` shows ESTABLISHED
- `xfrmX` interface exists with correct IPs
- BGP session remains up
- **All packets sent through the tunnel are dropped** (visible in `ip -s link show xfrmX` RX/TX errors)
- Connectivity is completely broken until tunnel restart

#### Multi-Layer Keepalive Strategy

The gateway uses a **defense-in-depth** approach with three keepalive mechanisms:

##### 1. NAT-T Keepalives (20-second interval)

File: `strongswan_renderer.py`

```python
# Per-tunnel configuration
keep_alive = 20s  # Send UDP keepalive every 20 seconds
```

- **Purpose:** Keep NAT mappings alive for tunnels behind NAT
- **Mechanism:** strongSwan sends UDP packets over the tunnel every 20s
- **Benefit:** Prevents NAT session timeouts
- **Limitation:** Does not detect data plane failures (keepalive packets may succeed while actual traffic fails)

##### 2. DPD (Dead Peer Detection) - 3:1 ratio (example: 5s / 15s)

File: `strongswan_renderer.py`

```python
# IKE SA configuration
dpd_action = restart
dpd_delay = 5s     # Check every 5 seconds
dpd_timeout = 15s  # Consider peer dead after 15s without response
```

- **Purpose:** Detect IKE SA failures and control plane issues
- **Mechanism:** strongSwan exchanges DPD messages with peer
- **Benefit:** Restarts tunnels if IKE SA becomes unresponsive
- **Limitation:** DPD operates at control plane; may not detect data plane failures where IKE still works but XFRM packet processing fails

##### 3. Automated Health Monitoring (10s checks, ~15s detection)

File: `tunnel_health_monitor.py`, systemd service: `nebius-vpngw-health-monitor.service`

- **Purpose:** Detect data plane failures by actively probing tunnel connectivity
- **Mechanism:** Periodic health checks using IPsec status, BGP state, XFRM error deltas, and optional ICMP ping to the BGP peer (controlled by `ping_enabled`)
- **Detection time:** ~15 seconds (10s initial check + 5s immediate re-check after first failure)
- **Recovery:** Automatic tunnel restart after 2 consecutive failures

**Why Three Layers?**

| Layer           | Detects             | Response Time    | Recovery Action   |
|-----------------|---------------------|------------------|-------------------|
| NAT-T Keepalive | NAT timeout         | N/A (preventive) | Keep NAT mappings |
| DPD             | IKE failures        | 5-15s            | Restart tunnel    |
| Health Monitor  | Data plane failures | ~15s             | Restart tunnel    |

- **NAT-T:** Prevents the problem (NAT timeouts)
- **DPD:** Catches IKE layer failures
- **Health Monitor:** Catches data plane failures that NAT-T and DPD miss

#### Health Monitoring Configuration

File: `nebius-gcp-ha-vpngw.config.yaml`

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

**Why `ping_enabled` may be disabled:** Some peers (notably GCP HA VPN) do not respond to ICMP on APIPA unless explicitly allowed by firewall rules. When ICMP is blocked, the monitor would falsely mark tunnels unhealthy and trigger restarts. In those environments, set `ping_enabled: false` and rely on IPsec/BGP state plus XFRM error counters.

**XFRM stale detection without ICMP:** The monitor compares `ip -s link show xfrmX` counters between checks and treats increases in `tx_dropped`, `tx_errors`, or `rx_errors` as a data-plane failure even if BGP stays up.

**Single-instance guard:** The monitor acquires a lock at `/run/nebius-vpngw/health-monitor.lock` to prevent accidental duplicate monitors (e.g., a manual `python -m ...` left running). systemd creates `/run/nebius-vpngw` via `RuntimeDirectory=nebius-vpngw` even with `ProtectSystem=strict`.

**Reactive vs Proactive Modes:**

| Mode                   | Behavior                                      | Downtime                  | Use Case                    |
|------------------------|-----------------------------------------------|---------------------------|-----------------------------|
| **Reactive (default)** | Detect failures, restart only when broken     | ~35s during failures      | 100% uptime priority        |
| **Proactive**          | Periodic restart every N hours (preventive)   | ~10-15s every N hours     | Prevent stale state buildup |

**Default: Reactive mode** (`proactive_refresh_enabled: false`) prioritizes zero planned downtime.

#### Failure Detection Timing

**Question:** With `max_failures_before_restart: 2` and `check_interval_seconds: 10`, does this mean 20 seconds of downtime (10s + 10s)?

**Answer:** No. The monitor uses **immediate re-check** after the first failure:

1. **t=0s:** Tunnel healthy (normal operation)
2. **t=10s:** First health check fails
   - Monitor logs failure
   - **Immediately waits only 5 seconds** (not 10s)
   - Runs second health check at t=15s
3. **t=15s:** Second health check
   - If **still failing:** Restart tunnel immediately
   - If **recovered:** Reset counter, continue monitoring
4. **t=25s:** Tunnel restarted, IKE/BGP negotiation begins
5. **t=35s:** Tunnel ESTABLISHED, traffic flows

**Total detection time: ~15 seconds** (10s initial + 5s re-check)
**Total recovery time: ~35 seconds** (15s detection + 20s restart)

This is **significantly faster** than waiting 20 seconds (10s × 2 failures).

**Code Implementation:**

File: `tunnel_health_monitor.py`, lines 397-465

```python
# After first failure, immediately re-check instead of waiting full interval
if not health.is_healthy:
    if consecutive_failures < max_failures_before_restart:
        print(f"[TunnelMonitor] 🔄 Immediate re-check in 5 seconds...")
        time.sleep(5)  # Immediate re-check, not full check_interval
        health_recheck = self.check_tunnel_health(...)
        if not health_recheck.is_healthy:
            consecutive_failures += 1  # Second failure confirmed
            # Check threshold and restart if max_failures reached
```

#### Manual Tunnel Restart

**Command:**

```bash
# Restart specific tunnel
nebius-vpngw restart-tunnel gcp-ha-tunnel-1

# Restart all tunnels (for all gateways)
nebius-vpngw restart-tunnel all

# With custom config file
nebius-vpngw restart-tunnel all --local-config-file my-config.yaml
```

For multi-VM topologies, `restart-tunnel <name>` targets only the gateway VM(s)
that own the named tunnel. `restart-tunnel all` still iterates over every
gateway VM that has at least one enabled tunnel.

**What it does:**

1. Loads deployment plan to get gateway VM IPs
2. SSHs to each gateway VM
3. Executes: `sudo systemctl restart nebius-vpngw-agent`
4. Agent restart triggers:
   - strongSwan tunnel teardown (`ipsec down <tunnel-name>`)
   - XFRM interface recreation
   - strongSwan reload (`ipsec reload`)
   - Tunnel re-establishment (`ipsec up <tunnel-name>`)
   - FRR BGP session reset

**Use cases:**

- Manual recovery after detecting connectivity issues
- Testing tunnel failover behavior
- Maintenance window operations

**Recovery time:** 10-15 seconds (tunnel establishment + BGP convergence)

#### Systemd Service Integration

The health monitor runs as a systemd service on each gateway VM:

**Service file:** `nebius-vpngw-health-monitor.service`

```ini
[Unit]
Description=Nebius VPN Gateway Health Monitor
After=network.target strongswan-starter.service frr.service
Wants=strongswan-starter.service frr.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.tunnel_health_monitor --config /etc/nebius-vpngw/config-resolved.yaml
Restart=on-failure
RestartSec=10
RuntimeDirectory=nebius-vpngw
RuntimeDirectoryMode=0755
ReadWritePaths=/var/log /run/nebius-vpngw

[Install]
WantedBy=multi-user.target
```

**Config source:** `/etc/nebius-vpngw/config-resolved.yaml` (per-VM resolved config deployed during `nebius-vpngw apply`).

**Management commands:**

```bash
# Check monitor status
sudo systemctl status nebius-vpngw-health-monitor

# View monitor logs
sudo journalctl -u nebius-vpngw-health-monitor -f

# Restart monitor
sudo systemctl restart nebius-vpngw-health-monitor
```

**Automatic deployment:** The monitor service is installed and enabled during `nebius-vpngw apply`.

## Peer Config Import

### Supported Vendors

- **GCP HA VPN:** Parses Cloud Router config exports
- **AWS Site-to-Site VPN:** Parses downloadable config files
- **Azure VPN Gateway:** Parses exported configurations
- **Cisco IOS:** Parses IOS config snippets

### Usage

```bash
nebius-vpngw create-from-peer-config ./nebius-vpngw.config.yaml \
  --peer-config-file ./gcp-ha-vpn.txt \
  --peer-config-file ./aws-vpn.xml
```

### Merge Behavior

Peer configs populate the template where values are available:

- PSKs (pre-shared keys)
- Remote public IPs
- Crypto proposals
- ASNs
- Inner IPs (for BGP)

Any fields not present in the peer export remain for manual review and editing.
Note: Cloud Router exports do not include PSKs or public IPs; those must be set manually.

## VM Management

### VM Lifecycle

- **Create:** Full provisioning with cloud-init
- **Update:** Config push + agent reload (no recreation)
- **Recreate:** Explicit `--recreate-gw` flag required

### VM Diff Detection

Agent compares desired vs actual VM specs:

- Platform, preset, disk size/type
- NIC count
- Public IPs (preserved across recreation)

### Public IP Preservation

During recreation:

1. Detach allocations from old VM
2. Delete old VM
3. Create new VM
4. Reattach allocations to new VM

**Downtime:** Tunnel establishment time only, IPs never change.

## Development Workflow

### Agent Development

1. Modify agent code in `src/nebius_vpngw/agent/`
2. Rebuild wheel: `python -m build --wheel --no-isolation`
3. Deploy: `nebius-vpngw apply` (uploads new wheel automatically)

Agent is installed on remote VMs, not in local virtualenv.
For pipx/release installs, `apply` first uses `VPNGW_AGENT_WHEEL` or a local wheel
(`./dist` or current directory), then falls back to the original wheel URL/file
recorded in `direct_url.json`; it does not require a source checkout or
`python -m build`.

### Testing Changes

```bash
# Validate schema
nebius-vpngw validate-config test.config.yaml

# Dry-run (hidden flag)
nebius-vpngw apply --local-config-file test.config.yaml --dry-run

# Deploy to test environment
nebius-vpngw apply --local-config-file test.config.yaml
```

### Dependency Upgrades

```bash
# Update pyproject.toml version constraints
# For transitive security advisories, add an explicit floor in [project].dependencies
uv lock
# Rebuild wheel (cleans old ones automatically)
python -m build --wheel --no-isolation

# Deploy with new dependencies
nebius-vpngw apply --local-config-file test.config.yaml
```

### Release Workflow

- `publish-release.sh` is the local helper for this service.
- `vpngw-ci.yml` is reserved for pull requests and manual CI runs.
- `vpngw-release.yml` is the dedicated tag-driven release workflow for `nebius-vpngw-v*`.
- Source/editable checkouts resolve runtime version from live SCM state instead
  of trusting a generated `_version.py` cache. They load the canonical
  `pyproject.toml` configuration through `setuptools-scm` without writing a
  runtime version file and fall back to bounded `git describe` when the
  dependency is unavailable. Wheel builds keep the package-local `_version.py`
  fallback used outside live SCM contexts.
- The service uses the current `setuptools-scm` `semver-pep440` scheme, nested
  tag matching, and an explicit build-time source-version-file policy so
  developer test/build flows do not emit deprecated scheme, tag, or
  implicit-write warnings.
- CI validates both `vpngw` workflow YAML files and runs the wheel-build regression test path before publication so workflow edits and packaging metadata regressions are caught before a tag-driven release runs.

Release sequence:

1. Run `./publish-release.sh --prep X.Y.Z` on your working branch to update `CHANGELOG.md`, commit it, and push the branch. If the branch has no upstream yet, the script sets `origin/<current-branch>` automatically on that first push. It also fails before editing anything if the target tag already exists locally or on `origin`, preserves markdownlint-safe blank lines between dated release sections, and is otherwise idempotent while the tag remains unreleased.
2. Merge the release preparation PR into `main`.
3. Run `./publish-release.sh --publish X.Y.Z` from a clean, synced `main`; the script verifies that the tagged source checkout resolves `nebius_vpngw.__version__ == X.Y.Z` before it pushes the tag. That verification works even when `setuptools-scm` is not installed in the current interpreter because the source checkout can derive the tagged version directly from Git metadata. Its clean-worktree check includes untracked files, and it fails locally if the target changelog section is empty.
4. The pushed tag triggers `vpngw-release.yml`, which checks out the tagged commit from `services/vpngw`, runs lint/tests, builds the wheel, verifies the artifact version, and creates the GitHub Release.

The local publish script does not build or upload release artifacts itself. Its job is only to create and push the annotated service tag.

## Project Structure

```text
├── nebius-vpngw.config.yaml              # User configuration (git-ignored)
├── publish-release.sh                    # Release helper (prep changelog commit, then create/push tag)
├── .github/workflows/
│   ├── vpngw-ci.yml                      # PR/manual CI workflow
│   └── vpngw-release.yml                 # Tag-driven GitHub Release workflow
├── src/nebius_vpngw/
│   ├── __main__.py                       # Python module entry point
│   ├── cli.py                            # CLI orchestrator (nebius-vpngw command)
│   ├── config_loader.py                  # YAML parser and peer config merger
│   ├── schema.py                         # Pydantic schema for YAML validation
│   ├── config_template.py                # Embedded YAML template (source of truth)
│   ├── build.py                          # Binary build utilities
│   ├── vpngw_sa.py                       # Service account management
│   ├── agent/
│   │   ├── main.py                       # On-VM agent daemon
│   │   ├── frr_renderer.py               # FRR/BGP config renderer
│   │   ├── strongswan_renderer.py        # strongSwan/IPsec config renderer
│   │   ├── xfrm_manager.py               # XFRM interface lifecycle (create, address, route)
│   │   ├── routing_guard.py              # Declarative route management & cleanup
│   │   ├── fix_routes.py                 # Standalone route cleanup utility (called by systemd timer)
│   │   ├── firewall_manager.py           # UFW firewall rule synchronization
│   │   ├── tunnel_iterator.py            # Centralized tunnel enumeration
│   │   ├── state_store.py                # Agent state persistence
│   │   ├── status_check.py               # Tunnel/BGP/service health checks
│   │   ├── sanity_check.py               # Routing invariant validation tool
│   │   └── tunnel_health_monitor.py      # Automated tunnel health monitoring with immediate re-check
│   ├── deploy/
│   │   ├── vm_manager.py                 # VM lifecycle (create/delete/recreate)
│   │   ├── vm_diff.py                    # VM configuration change detection
│   │   ├── route_manager.py              # VPC route management (static mode)
│   │   └── ssh_push.py                   # Package/config deployment over SSH
│   ├── peer_parsers/
│   │   ├── gcp.py                        # GCP HA VPN config parser
│   │   ├── aws.py                        # AWS Site-to-Site VPN config parser
│   │   ├── azure.py                      # Azure VPN Gateway config parser
│   │   └── cisco.py                      # Cisco IOS config parser
│   └── systemd/
│       ├── nebius-vpngw-agent.service          # Agent systemd unit
│       ├── nebius-vpngw-health-monitor.service # Tunnel health monitor systemd unit
│       ├── nebius-vpngw-fix-routes.service     # Service wrapper for route cleanup
│       ├── nebius-vpngw-fix-routes.timer       # Timer to enforce route cleanup periodically
│       └── setup-vpngw-firewall.sh             # UFW firewall initialization script
```

### Module Descriptions

**Orchestrator (runs on operator machine):**

- `cli.py`: Main CLI entry point, orchestrates VM provisioning and config deployment
- `config_loader.py`: Parses YAML, merges peer configs for generated configs, expands env vars, validates schema
- `schema.py`: Pydantic models for strict validation with type checking and constraints
- `config_template.py`: Embedded YAML template, source of truth, always aligned with schema
- `vpngw_sa.py`: Service account lifecycle for API authentication
- `build.py`: Utilities for building standalone binaries (PyInstaller)

**Agent (runs on gateway VM):**

- `main.py`: Agent daemon, renders configs, applies idempotently, handles SIGHUP reload
- `frr_renderer.py`: Generates FRR BGP configuration with Active/Passive HA support (local-preference and MED route-maps), advertises local prefixes, applies inbound/outbound filters
- `strongswan_renderer.py`: Generates strongSwan IPsec configuration with XFRM interfaces
- `routing_guard.py`: Enforces routing invariants, prevents problematic local_prefix routes that break packet forwarding, removes table 220, cleans APIPA routes
- `fix_routes.py`: Standalone utility invoked by systemd timer to periodically enforce routing invariants (calls routing_guard)
- `firewall_manager.py`: Synchronizes UFW rules with active tunnels
- `xfrm_manager.py`: Manages XFRM tunnel interfaces lifecycle (create, configure IP addresses, MTU, bring up/down)
- `tunnel_iterator.py`: Centralized tunnel enumeration for consistent indexing across all agent modules
- `state_store.py`: Persists last-applied state for idempotency checks
- `status_check.py`: Collects health metrics for status command (tunnel status, BGP sessions, routes)
- `sanity_check.py`: Standalone routing validation tool for troubleshooting
- `tunnel_health_monitor.py`: Automated tunnel health monitoring daemon with immediate re-check after first failure (~15s detection time), supports reactive and proactive modes, integrates with systemd for continuous monitoring

**Deployment:**

- `vm_manager.py`: VM lifecycle using Nebius SDK
- `vm_diff.py`: Detects VM changes requiring recreation
- `route_manager.py`: Manages VPC static routes (static mode only)
- `ssh_push.py`: Deploys agent package and config via SSH/SFTP

**Peer Config Parsers:**

- `gcp.py`, `aws.py`, `azure.py`, `cisco.py`: Parse vendor-specific configs

## Tips & Troubleshooting

### UFW Must Be Active for VPN to Work

**Problem:** VMs in local subnets cannot reach remote networks via VPN, even though IPsec tunnels are ESTABLISHED and BGP sessions are UP.

**Symptoms:**

- Tunnels show ESTABLISHED status
- BGP peers are connected and exchanging routes
- Routes appear in routing tables
- But: VMs cannot ping or connect to remote networks
- tcpdump on gateway shows zero packets from local VMs
- XFRM encryption counters don't increment for data traffic

**Root Cause:** UFW (firewall) is inactive. **UFW MUST be active for the VPN gateway to forward traffic correctly.**

**Why This Happens:**

- UFW activates the Linux netfilter framework
- Without netfilter active, the kernel doesn't properly integrate with XFRM (IPsec) tunnels for packet forwarding
- Even with `net.ipv4.ip_forward=1` set, packets from the VPC fabric won't be forwarded through XFRM without netfilter
- This is not about blocking traffic - it's about netfilter initialization being required for XFRM forwarding

**Diagnosis:**

```bash
# Check UFW status
sudo ufw status

# If it shows "Status: inactive", that's the problem!
```

**Solution:**

```bash
# Re-apply firewall config via agent (preferred)
sudo systemctl restart nebius-vpngw-agent

# If UFW was disabled manually:
sudo ufw enable

# Verify it's active
sudo ufw status verbose
# Should show: Status: active
```

**After Fix:**

- Test connectivity from VMs immediately - it should work instantly
- UFW is now enabled on boot, so this won't happen again after reboots

**Prevention:**

- Firewall setup runs automatically during VM creation (cloud-init) and is re-applied by the agent
- Always verify UFW is active after deploying a new gateway
- Include UFW check in monitoring/health checks

### Subnet CIDR Issues

**Problem:** When creating the dedicated gateway subnet, Nebius may show the network's parent CIDR (for example, `/13`) instead of the intended explicit subnet CIDR in the console, even though the code calculates and requests the correct CIDR.

**Root Cause:** The Nebius VPC API field `use_network_pools` defaults to `true`. When `true`, the subnet inherits the network's address pool instead of using the explicitly specified CIDR. The issue was caused by a subtle bug in subnet creation:

1. **Initial Creation:** Subnet is created correctly with `IPv4PrivateSubnetPools` containing the pools array and `use_network_pools=False`
2. **Route Table Attachment:** When attaching a route table via `UpdateSubnetRequest`, the code was creating a new `SubnetSpec` with only `network_id` and `route_table_id`
3. **Field Reset:** The missing `ipv4_private_pools` field in the update request caused the API to reset the subnet to default settings (`use_network_pools=true`)

**Solution:** When updating a subnet (e.g., to attach a route table), always preserve the existing `ipv4_private_pools` and `ipv4_public_pools` fields from the original subnet spec:

```python
# Get existing pool configuration
existing_ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
existing_ipv4_public_pools = getattr(subnet_spec, "ipv4_public_pools", None)

# Include in update request
update_req = UpdateSubnetRequest(
    metadata=ResourceMetadata(...),
    spec=SubnetSpec(
        network_id=subnet_network_id,
        route_table_id=rt_id,
        ipv4_private_pools=existing_ipv4_private_pools,  # Preserve!
        ipv4_public_pools=existing_ipv4_public_pools,    # Preserve!
    ),
)
```

**Verification:** After subnet creation, check that:

- `spec.ipv4_private_pools.use_network_pools` is `false` (or field is absent)
- `spec.ipv4_private_pools.pools[0].cidrs[0].cidr` shows the expected `/24` CIDR
- `status.ipv4_private_cidrs` contains the expected `/24` CIDR (what the console displays)

### Inherited Pool Status Bug

**Problem:** For subnets with `use_network_pools=true`, the Nebius console and API `status.ipv4_private_cidrs` can make an inherited subnet look like it owns CIDRs that were explicitly carved out for other subnets.

**Observed impact:** If tooling trusts those raw status CIDRs, it can target the wrong workload subnet route table when matching `gateway.local_prefixes`.

**Current mitigation in this project:** `add-routes-local` and `list-routes-local` sanitize inherited subnet status CIDRs by subtracting all explicit subnet CIDRs from other subnets in the same network before they match subnets to `gateway.local_prefixes`. This keeps shared-pool subnets usable while avoiding false positives from leaked explicit CIDRs in inherited subnet status.

**Operational note:** Treat raw inherited `status.ipv4_private_cidrs` as advisory, not authoritative, when debugging routing decisions.

### Packet Duplication Issue (Historical - Resolved)

**Problem (VTI mode only - now removed):** When using legacy VTI (Virtual Tunnel Interface) mode, pinging the VPN gateway from VMs in other subnets would show 60+ duplicate ICMP packets (marked as `DUP!` in ping output).

**Root Cause:** strongSwan with VTI mode intercepted all packets for IPsec policy evaluation. When packets were destined for the gateway's own IP (not through the tunnels), VTI processing would duplicate packets during policy lookups or tunnel path evaluation. With 2 active tunnels to GCP, each packet was evaluated against multiple tunnel policies, creating duplicates.

**Resolution:**
**Switching to XFRM interfaces completely eliminated this issue.** XFRM's `if_id` binding mechanism provides clean separation between tunnel interfaces and avoids the packet duplication problem that occurred with VTI mode. The current implementation uses XFRM interfaces exclusively and does not experience packet duplication.
