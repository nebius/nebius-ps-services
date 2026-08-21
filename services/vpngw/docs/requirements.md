<!-- markdownlint-disable MD001 MD013 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v1 -->
# Project Requirements

## Core Requirements

### REQ-001: Preserve the supported Python project contract during hardening

- Status: active
- Requirement: Maintain the existing Python package, CLI, test, build, and release contracts while applying conservative project hardening for current users.
- Constraints: Preserve supported public import paths, console scripts, CLI syntax and exit behavior, Python 3.10 through 3.12 support, configuration schemas and defaults, persisted formats, packaged systemd assets, SCM-derived versions, release tags, and upgrade paths, except for the explicitly approved VM-HA-only clean break in REQ-008. Keep one canonical implementation rather than adding compatibility shims.
- Non-goals: Replacing the established project scaffold, changing runtime cloud or networking behavior, raising the Python floor, adopting a new framework or build backend, or performing broad dependency upgrades.

#### Acceptance criteria

- PEP 621 metadata, the `src/nebius_vpngw` layout, Typer entrypoints, Pydantic configuration, setuptools/setuptools-scm packaging, split unit/integration tests, Ruff, mypy, pytest, coverage, and current CI lanes remain the canonical project structure.
- Source-checkout version discovery has a finite direct Git-probe timeout and falls back through the existing metadata/generated/unknown sequence without changing successful version results.
- Regression tests bind the supported Python range, console-script mappings, SCM tag/version-file contract, package discovery, and Makefile verification/build targets.
- SCM tag matching uses the supported nested configuration model, and source-checkout runtime version discovery emits no dependency deprecation warnings while preserving the established tag format and resolved versions.
- Unit tests remain isolated from real networks and cloud APIs; integration tests remain explicitly marked and separated from the fast unit lane.
- Standard local coverage, tox, and nox artifacts are ignored without hiding project source or public examples.

#### Verification

- Run the focused runtime-version and Python-project contract tests, a warning-strict source-checkout version probe, repository-native Ruff and mypy checks, full unit and integration suites, CLI help/version smoke tests, wheel-build regressions, and the canonical `make all` workflow.

### REQ-002: Reduce pytest feedback time without weakening correctness

- Status: active
- Requirement: Improve the established pytest unit-test feedback loop only where repeatable measurements identify a cumulative bottleneck and a like-for-like optimization is safe.
- Constraints: Preserve collected tests, outcome counts, assertions, failure diagnostics, unit-test network isolation, registered markers, integration classification, serial debugging, coverage, and the complete correctness gate. Keep the existing Python 3.10 through 3.12 support and avoid new dependencies unless separately justified and approved.
- Non-goals: Hiding failures with skips, xfails, or reruns; deleting or combining tests for timing; relabeling integration behavior as unit behavior; accessing live cloud or shared services; or treating a reduced selection as a full-suite speedup.

#### Acceptance criteria

- Baseline and candidate measurements use the same frozen non-candidate source state, with the exact candidate patch as the only intentional difference, plus the same interpreter, pytest/plugin configuration, cache policy, selection, instrumentation, collected count, and outcome counts. Record identities for both compared test-file states.
- Startup/collection and setup/call/teardown costs are distinguished, and changes target ranked cumulative cost rather than only the single slowest test.
- Any accepted optimization has at least five comparable before/after samples with non-overlapping or clearly material timing evidence; inconclusive candidates are reverted or left unadopted.
- The canonical local serial unit command remains available, CI parallelism remains bounded by validated isolation, and full unit/integration correctness checks remain authoritative.

#### Verification

- Record median and range for comparable unit-suite samples, run duration diagnostics, then rerun the complete unit and isolated integration lanes plus Ruff, mypy, and diff-integrity checks after any change.

### REQ-003: Guide configuration creation without breaking automation

- Status: active
- Requirement: Make interactive `nebius-vpngw create-config CONFIG_FILE` use a guided wizard that produces an ordered, schema-valid VPN gateway configuration and explains each required input, while preserving the current deterministic template generator for existing automation.
- Constraints: Preserve the positional output path, `--force`, non-`.config.yaml` warning, exact-template no-op, noninteractive output and exit behavior, configuration schema v1, public `prep-network` command, and explicit default-disabled VM-HA semantics. A non-TTY invocation or explicit `--no-interactive` uses the current template path; `--interactive` forces the wizard. The wizard must not collect or display secret bytes, write an incomplete candidate, infer VM-HA, or perform cloud mutation before a separate explicit confirmation.
- Non-goals: Removing or deprecating `prep-network`; introducing a second configuration schema, resumable draft format, live provider-discovery dependency, new prompt framework, or implicit network/IAM mutation; changing apply, deployment, tunnel-HA, or VM-HA runtime behavior.

#### Acceptance criteria

- The wizard guides project context, gateway/network/routing, repeatable connections and tunnels, and an optional advanced phase; invalid typed or cross-field values are explained and reprompted, and back/quit controls never write a partial candidate.
- The completed in-memory candidate passes the existing Pydantic schema before an atomic file replacement. PSKs are written as environment-variable references, sensitive summaries are redacted, and interrupted or rejected overwrite attempts preserve the original file byte-for-byte.
- After a valid file is written, network preparation is offered with default No and an explicit description of authentication, subnet, route-table, public-allocation, and YAML effects. Both entrypoints use one internal preparation path, while the standalone command retains its supported options and observable behavior.
- VM-HA questions appear only after explicit enablement, require the existing exact two-member contract, and are never selected from instance count, tunnel roles, or public IP shape.
- Static and BGP wizard transcripts, noninteractive compatibility, cancellation, overwrite safety, redaction, network-preparation failure boundaries, and omitted/disabled VM-HA behavior have regression coverage.

#### Verification

- Run focused wizard, CLI, schema/config-loader, network-preparation, allocation, and VM-HA compatibility tests; CLI help smoke checks; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; and diff-integrity checks.

### REQ-004: Guide an ordinary gateway into explicit VM-HA

- Status: active
- Requirement: Add `nebius-vpngw configure-vm-ha --local-config-file SOURCE [--output DEST] [--force]` as a guided, two-phase conversion from one supported ordinary single-VM configuration to a new, schema-valid explicit two-member VM-HA candidate.
- Constraints: Preserve `SOURCE` byte-for-byte and preserve the supported `create-config`, `validate-config`, `prep-network`, and `apply` contracts. Admit only schema-v1 configurations with `instance_count: 1`, VM-HA omitted or explicitly disabled, and every tunnel owned by instance zero. VM-HA remains explicit and default-disabled. Treat raw YAML as the persistence authority so environment references and PSK references are never expanded into output. Never overwrite in place, follow a source or destination symlink, write through a hard link to the source, expose secret values, or publish an incomplete candidate. Any optional cloud operation requires a separate default-No confirmation and may prepare only the deterministic passive public allocation needed for the peer handoff.
- Non-goals: Mutating the peer provider, deploying or activating VM-HA, discovering or adopting an existing multi-VM topology, inferring VM-HA from instance count, public IPs, or tunnel roles, replacing `prep-network`, saving a schema-invalid draft, automating post-activation removal, or changing apply's approval, recovery, fencing, and lifecycle authority.

#### Acceptance criteria

- The wizard preserves every existing member-zero and unrelated configuration value semantically, changes `instance_count` from one to two, adds the exact explicit active/passive VM-HA block, appends one instance-one counterpart for every existing instance-zero tunnel, appends only the passive external-IP row, and increases `max_tunnels` only when required. A mechanical structural allowlist rejects any other candidate mutation.
- The first phase derives deterministic member-one names, PSK environment references, unique APIPA networks, and one absolute node-scoped `nebius_credentials_path` per member. It never asks for, reads, or writes operator-local mTLS material; managed identities are generated on the VMs only after the separately approved apply begins. The user may then supply a passive public IP or explicitly request a passive-only Nebius reservation. The wizard prints a secret-free peer handoff and exits successfully without a candidate when the peer is not ready; a rerun reuses the exact deterministic allocation.
- The second phase requires the peer-provided remote public and inner tunnel endpoints, validates the complete in-memory candidate through the existing schema, presents a bounded redacted summary, and publishes only after explicit confirmation. The source remains unchanged on cancellation, EOF, interruption, validation failure, cloud failure, or publication failure.
- Candidate publication rejects canonical same-path, symlink, and same-inode source/destination relationships; detects concurrent source or destination changes; never clobbers a racing writer; and enforces mode `0600` regardless of process umask. A new destination is published atomically with a no-clobber link. Replacing an expected destination first quarantines that exact file in a private sibling directory, then publishes without clobbering; interruption can therefore leave explicit manual recovery state rather than claiming a single atomic replacement. Exact already-published output is an idempotent no-op only when its file safety invariants still hold.
- Passive allocation preparation selects only instance index one, preserves and does not query or validate instance-zero allocation state, uses the deterministic `<gateway>-1-eth0-ip` identity, rejects attached or foreign allocations, and never creates VMs, shared private aliases, managed routes, lifecycle state, host configuration, or deployment approval.
- A published candidate passes configuration loading, peer merging, and the existing `apply --dry-run` migration preview, which retains the ordinary active member and leaves all cloud and host mutation behind apply's existing explicit approval boundary.
- Static, BGP, multi-connection, multiple-tunnel, placeholder, redaction, cancellation, file-safety, passive-allocation retry, generated-candidate dry-run, CLI help, and unchanged ordinary-command behavior have regression coverage without live cloud access.

#### Verification

- Run focused conversion-wizard, CLI, schema/config-loader, selected-index allocation, migration dry-run, and VM-HA compatibility tests; CLI integration smoke checks; Ruff; mypy; the complete unit and isolated integration suites; Markdown lint; security/redaction review; and diff-integrity checks.
- Offline implementation evidence on 2026-08-18: Ruff and mypy passed, all 1,015 unit tests and 46 integration tests passed, focused generated-candidate tests exercised real configuration loading, peer merging, and the existing `apply --dry-run` migration boundary, and changed-scope Markdown and diff-integrity checks passed. No live Nebius authentication or cloud mutation was used.

### REQ-005: Make every public command self-explanatory

- Status: active
- Requirement: Make the top-level `nebius-vpngw --help` output and every public command-group and executable-command help page provide accurate, practical invocation examples for the supported CLI workflows.
- Constraints: Preserve the 18 executable operations and two non-executable command groups, workflow-oriented command order, leaf flags, arguments, prompts, approvals, exit behavior, and compatibility contracts except for the explicitly approved failover/failback route migration in REQ-006, removal of the unpublished `vm-ha-recover` command in REQ-007, and clean-slate `set-vm-ha-mtls` addition in REQ-008. Examples must use supported syntax, avoid secrets and environment-specific identifiers, and must not suggest bypassing confirmation, VM-HA migration approval, fencing, or other safety gates.
- Non-goals: Executing example commands; changing cloud, host, configuration, or persistence behavior; documenting the separate agent entrypoint; or replacing the README with generated CLI reference output.

#### Acceptance criteria

- Top-level help contains a short quick-start sequence for configuration creation, validation, and a non-mutating apply preview.
- Every visible public command-group and executable-command help page contains an `Examples` section with at least one path-specific invocation whose syntax matches the registered arguments and options.
- The example contract is owned centrally and regression coverage compares it with the rendered public command tree, so a new visible command cannot be added without an example and an example cannot silently reference the wrong command.
- Mutating workflow examples retain the command's ordinary interactive confirmation or explicit approval boundary; no example contains credential material, customer data, live resource identifiers, or hidden bypass flags.
- README discovery guidance and the Unreleased changelog describe the aligned help surface without changing existing command semantics.

#### Verification

- Render top-level help and every visible command help page through Typer's test runner; assert registry parity, successful rendering, and command-specific example text. Run focused CLI tests, Ruff, mypy, the full unit and isolated integration suites, Markdown lint, security review, and diff-integrity checks.
- Offline implementation evidence on 2026-08-18: the root help and all 18 visible command help pages rendered with their canonical examples; Ruff and mypy passed; all 1,052 unit tests and 46 integration tests passed; README and changelog Markdown lint, canonical spec validation, changed-scope security review, and diff-integrity checks passed. No example command was executed against Nebius or a gateway VM.

### REQ-006: Organize failover and failback by resource

- Status: active
- Requirement: Replace the four flat failover/failback entry points with the canonical resource-scoped commands `nebius-vpngw failover vm`, `nebius-vpngw failback vm`, `nebius-vpngw failover tunnel [TUNNEL_NAME]`, and `nebius-vpngw failback tunnel [TUNNEL_NAME]`.
- Constraints: Remove `vm-ha-failover`, `vm-ha-failback`, flat `failover`, and flat `failback` without aliases or compatibility shims. Preserve every leaf argument, option, callback behavior, VM-HA request schema, ownership and fencing gate, tunnel selection rule, prompt, side effect, and exit behavior. Bare `failover` and `failback` groups must render help and exit nonzero without loading configuration, authenticating, opening SSH, querying cloud state, or contacting an agent. Removed or invalid paths must fail during parsing before any such effect.
- Non-goals: Renaming internal VM-HA intent or request types; changing automatic failover, rearm, status, tunnel HA, VM-HA configuration, cloud transfer, route, forwarding, readiness, or recovery semantics; adding aliases, deprecation wrappers, or a generic resource dispatcher.

#### Acceptance criteria

- Root help exposes exactly one `failover` group and one `failback` group in their established workflow position; each group exposes `vm` before `tunnel`, and the old four paths are absent.
- The `vm` leaves invoke the unchanged planned VM-HA preparation and operator-request paths, including former-owner `Stopped`, candidate allocation ownership, route reconciliation, forwarding, generation, apply-lock, and readiness gates.
- The `tunnel` leaves retain the optional tunnel-name argument, `--local-config-file`/`-c`, automatic single-tunnel selection, multi-tunnel diagnostics, and existing tunnel failover/failback execution behavior. Runtime guidance names the new resource-scoped syntax.
- One path-aware example registry owns root, group, and leaf help ordering and examples. Recursive tests prove registry/tree parity, deterministic ordering, successful help rendering, nested callback routing, and parse-time zero-effect rejection of removed and incomplete paths.
- README and the Unreleased changelog provide the exact old-to-new migration mapping and do not imply any compatibility alias.

#### Verification

- Run focused recursive CLI-tree, help, routing, zero-effect rejection, tunnel, and VM-HA safety tests; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; security review; canonical-spec validation; and diff-integrity checks. Live cloud or gateway execution is not required because the approved change is limited to command routing and static guidance.
- Offline implementation evidence on 2026-08-18: recursive command-tree and parser tests proved the four nested leaves, deterministic `vm`-before-`tunnel` ordering, path-specific help, unchanged VM operator routing, request-free same-owner behavior, and effect-free rejection of bare and removed paths. Ruff and mypy passed, all 1,065 unit tests and 58 isolated integration tests passed, selected changed-document Markdown lint and diff-integrity checks passed, and changed-scope security review found no new trust, credential, network, or mutation boundary. No command was executed against Nebius or a gateway VM.

### REQ-007: Consolidate VM-HA status into the ordinary status command

- Status: satisfied
- Requirement: Make `nebius-vpngw status` the only public status interface and render one concise, authoritative VM-HA section when explicit VM HA is configured.
- Constraints: Remove the unpublished `vm-ha-recover` command and its private duplicate agent flag without an alias, replacement command, deprecation shim, or focused-view flag. Preserve the private canonical `--vm-ha-status` agent read, public `vm-ha-rearm`, `failover vm`, and `failback vm`, ordinary non-HA status output, VM-HA fencing and mutation boundaries, and existing fatal setup/configuration/authentication exits. Unresolved environment references used only as tunnel PSKs are not setup errors for this read-only command; all non-secret placeholders and secret requirements of mutating commands remain strict. After successful command setup, HA health and member-observation failures remain informational.
- Non-goals: Adding `vm-ha-status`, `vm-ha-state`, `--vm-ha-only`, a machine-readable status schema, new cloud or gateway mutations, a metrics exporter, or changing any ownership-transfer, route, forwarding, rearm, configuration, or lifecycle record.

#### Acceptance criteria

- Explicit VM HA renders exactly one table titled `VM-HA Status — <OVERALL>` with the columns `Gateway`, `Role`, `mTLS`, and `Ready`, plus exactly one row for each configured member; non-HA status performs no HA observation and renders no HA section.
- `Role` reports only the current operational relationship to authoritative cloud ownership: the exact owner is `active`, the other member is `standby`, and every member is `unknown` when no owner is proven. Configured active/passive preference is not rendered in this column and never overrides current ownership.
- Cloud and lifecycle evidence select the authoritative owner. Member records are validated as supporting evidence and cannot report healthy redundancy when they disagree with cloud ownership, aliases, identities, generations, required digests, locks, or forwarding authority. Every exact route target must expose the same non-empty managed-prefix set exactly once through the shared allocation; missing, duplicate, partial, or foreign-next-hop coverage blocks exact authority.
- Overall state uses conservative precedence: proven unsafe contradiction is `BLOCKED`; missing required evidence is `UNKNOWN`; an exact expected lifecycle, transfer, repair, or rearm operation is `TRANSITIONING`; a safely serving owner without ready redundancy is `DEGRADED`; and only an exact ready owner/standby pair is `HEALTHY`. A pending controller effect qualifies as expected only when its generated identity names a configured member and its encoded action kind is valid for the reported controller state.
- The aggregate title is green only for `HEALTHY`; `DEGRADED`, `TRANSITIONING`, `BLOCKED`, and `UNKNOWN` are red. Per-member `mTLS` is green only for an uninhibited `healthy` state, and `Ready` is green only for `yes`; every other semantic value is red. Gateway and Role remain neutral, and literal values remain visible without color.
- `Ready=yes` requires exact authority, role-specific safe readiness, and an aggregate state of `HEALTHY` or `DEGRADED`. `BLOCKED` and `TRANSITIONING` render `no`; unavailable evidence renders `unknown`. Missing member IP, trust, SSH, JSON, or valid status produces one sanitized unavailable row rather than omitting the member.
- Every status SSH probe uses the configured management username and private key. Explicit VM HA additionally resolves one immutable exact-pin policy per member, so a missing pin affects only that member's sanitized semantic row and never falls back to trust-on-first-use or disabled host verification.
- Cloud route authority distinguishes an inexact target set, malformed or duplicate managed records, missing or inconsistent prefix coverage, and a managed next hop that does not equal the shared allocation. Authority is scoped by the complete product-managed label set for the current cluster: a well-formed foreign-cluster record is ignored, while partial, malformed, current-cluster, target, kind, or allocation-label drift fails closed and prevents a healthy aggregate projection.
- The HA renderer exposes only configured member names, current operational roles, closed mTLS states, and conservative readiness. It never exposes configured role preference, cloud resource, allocation, node, generation, digest, revision, operation, epoch, fingerprint, timing, raw agent/cloud, raw exception, or recovery-guidance details.
- `status` accepts a schema-valid local configuration whose tunnel PSKs remain exact unresolved environment references, never expands or renders those unused secret values, and still rejects unresolved project, topology, credential-path, or other operational placeholders required by its observation path. When PSKs are unresolved, each available member must report a self-consistent generation, both available members must agree exactly on generation and digests, and the locally derivable static-route and BGP-policy digests must still match; status must not synthesize an expected full configuration digest from placeholder text.
- `vm-ha-recover` and private `--vm-ha-recover` fail at argument parsing before configuration, authentication, SSH, cloud reads, or mutation. No public replacement or focused-view option is registered.

#### Verification

- Run pure validation/classification/render tests, forced-color and no-color rendering, mocked read-only status orchestration, two-member availability and redaction tests, command-tree/help and parse-time rejection tests, unchanged non-HA regressions, Ruff, mypy, full unit and isolated integration suites, Markdown lint, canonical-spec validation, security review, and diff-integrity checks. Live cloud or gateway execution is not required for this read-only presentation change.
- Offline implementation evidence on 2026-08-19: focused validation,
  classification, rendering, command-tree, public/private parser rejection,
  availability, redaction, exact short PSK-placeholder loading, and unchanged
  operator-path coverage passed. Ruff and mypy passed, all 1,098 unit tests and
  62 isolated integration tests passed, and no command was executed against
  Nebius or a gateway VM.
- Offline presentation-revision evidence on 2026-08-20: the renderer emits one
  aggregate-titled four-column table and exactly two semantic member rows;
  forced-style and no-color tests prove the green/red contract, missing-member
  projection, and absence of the former summary/details fields. Full Ruff and
  mypy passed, all 1,116 unit tests and 69 isolated integration tests passed,
  and no live cloud, SSH, service, route, or gateway operation was performed.
- Offline Role-correction evidence on 2026-08-20: five focused regressions
  failed on configured-preference suffixes, then passed with authoritative
  `active`/`standby` reversal in both ownership directions and `unknown` roles
  without an owner. All 26 focused VM-HA status tests, 1,116 unit tests, and 69
  integration tests passed with Ruff, mypy, compilation, documentation, and
  diff-integrity checks. No live gateway command was run.

### REQ-008: Manage VM-HA mutual TLS without operator PKI

- Status: active
- Requirement: Make VM-HA mutual TLS completely product-managed: each member generates and retains an independent self-signed identity, exact peer leaf certificates are exchanged only through pre-established exact-pinned SSH, initial enrollment and member replacement are automatic under `apply`, and both identities rotate only through an explicit `set-vm-ha-mtls` operation.
- Constraints: VM-HA has no compatibility obligation because no user depends on its existing configuration, credential bundle, persisted mTLS, or peer-wire formats. Replace those HA-only contracts without aliases, legacy readers, dual modes, or migration shims; a stale configuration receives only actionable conversion guidance. Preserve explicit default-disabled VM HA, all non-HA behavior, exact SSH host verification, former-owner fencing, cloud ownership, routes, forwarding, rearm authority, and secret-redaction boundaries. Do not use an external CA, CA private key, Vault, KMS, trust-on-first-use, certificate discovery from an untrusted channel, automatic renewal, or scheduled rotation.
- Non-goals: Repairing or enrolling SSH host trust during mTLS rotation; learning SSH identity from a gateway or unauthenticated scan; starting a stopped member; changing Compute, allocation, VPC route, firewall, or forwarding state during mTLS rotation; exporting VM private keys; supporting more than two members; preserving mixed mTLS protocol versions; or claiming a physically simultaneous two-machine key switch. REQ-013 assigns bounded operator-side SSH trust creation and repair only to `apply`.

#### Acceptance criteria

- Each VM-HA member configuration contains one absolute node-scoped `nebius_credentials_path` and no CA, certificate, private-key, or generic `credential_sources` field. The wizard, examples, loader, manifests, runtime binding, README, and help use that one canonical shape; the removed shape fails before authentication or mutation with identity-safe guidance.
- Each member generates an unencrypted PKCS#8 ECDSA P-256 key from the OS CSPRNG and a SHA-256 self-signed CA-false certificate with a random positive serial, fixed historical `notBefore`, `9999-12-31T23:59:59Z` `notAfter`, digital-signature usage, client/server EKUs, the exact node DNS SAN, and URI SAN `urn:nebius-vpngw:node:<node_id>`. Member SPKI fingerprints must differ.
- Root-owned node-local state uses immutable identity and peer-certificate objects plus atomic active and transaction records. Private keys are mode-restricted, no-follow, single-link files and never cross SSH, enter YAML/manifests/status/logs, or return from an agent command. Only validated public certificates, fingerprints, transaction receipts, and secret-free status may leave a VM.
- TLS retains `CERT_REQUIRED`, peer hostname/URI identity validation, and exact DER leaf-fingerprint validation. Every new transport connection selects one immutable managed snapshot, carries a protocol-v2 mTLS epoch bound to the actually presented certificate, and disables reusable old sessions so trust pruning cannot preserve an obsolete authenticated channel.
- Initial HA apply generates both identities, cross-installs direct peer pins, proves fresh bidirectional TLS, and only then activates HA. An unchanged healthy apply performs no mTLS mutation. Exact member replacement generates only the replacement identity after the former Compute is authoritatively stopped or absent and network-fenced, preserves the survivor identity, uses temporary old/new overlap trust, proves fresh replacement traffic, and prunes the former leaf.
- `nebius-vpngw set-vm-ha-mtls --local-config-file FILE [--dry-run] [--approve PLAN_DIGEST]` rotates both members and has no target flag. Dry-run performs no key, journal, lock, or inhibition write. Interactive execution confirms a secret-free plan; noninteractive execution requires a digest bound to current config, cluster, members, Compute identities, owner/allocation observation, fingerprints, epochs, and exact phases, and any drift invalidates it.
- Rotation requires ACTIVE lifecycle, exact cloud/member ownership, both members Running and reachable through exact-pinned SSH, one owner and one alias-free non-owner, and no competing writer. Under the shared writer lock it durably inhibits rearm/failover on both members, prepares pending identities, expands trust to old/new, switches passive then owner, and commits only after independently reread active slots plus three consecutive fresh epoch-and-fingerprint-bound authenticated observations in both directions. It then drains old connections, prunes obsolete public and private material, and releases inhibition.
- Rollback eligibility comes from independently reread served fingerprints and active slots, never the CLI's last acknowledgement. Before either new leaf is observed serving, rollback is permitted; afterward every retry rolls forward under overlap trust. The same operation and monotonic epoch resume idempotently after CLI, SSH, service, or host restart.
- Broken current mTLS may be rebuilt through exact-pinned SSH when both exact members are Running and ownership is unambiguous. Missing SSH trust, a stopped member, a foreign or unfenced former member, corrupt cross-node identity, ambiguous ownership, or a competing apply/rearm/transfer blocks before mutation with a closed reason and safe action.
- Ordinary `status` remains read-only and reports each member's closed mTLS health state in the concise VM-HA table without epochs, fingerprints, transaction details, keys, internal paths, or cloud identities. `vm-ha-rearm` remains the sole Compute-start workflow and gains no mTLS authority.

#### Verification

- Run direct-leaf TLS/profile/fingerprint tests across supported Python/OpenSSL lanes; state and SSH fault injection at every durable effect and lost acknowledgement; initial apply, no-op apply, member replacement, broken-mTLS recovery, approval drift, concurrency, and private-key non-export tests; composed old/overlap/mixed/new/pruned runtime tests; CLI/help/schema/wizard/package tests; Ruff, mypy, full unit/integration, Markdown, security, and changed-scope alignment gates. Live mutation remains a separately approved non-production acceptance trial.
- Offline implementation evidence on 2026-08-19: Ruff and mypy passed, all
  1,094 unit tests and 63 isolated integration tests passed, and 14 focused
  build/release tests passed. Direct-leaf handshakes, certificate profiles,
  private-key non-export, state recovery, inhibition, apply bootstrap/no-op/
  replacement, digest drift, passive-first rotation, heartbeat-v2 binding,
  status projection, and stale-schema rejection were exercised locally on
  Python 3.12/OpenSSL. README and changelog Markdown lint passed. No live
  Nebius, SSH, service, cloud, route, or gateway mutation was performed.

### REQ-009: Keep network selection progress accurate and concise

- Status: active
- Requirement: Keep `gateway_group.network_id` optional in schema-v1 local configurations and make `apply` report the selected gateway network accurately without repeating the same informational decision for internal safety rereads.
- Constraints: Preserve the established `default-network`, single-custom-network, and ambiguous-network selection order; preserve every authoritative cloud reread used by VM-HA fencing and lifecycle validation; preserve explicit `network_id` validation and failures; and do not infer, persist, or require a network identifier merely to silence output.
- Non-goals: Changing the YAML layout, removing safety rereads, caching mutable cloud observations, changing network/subnet selection, or adding a compatibility path.

#### Acceptance criteria

- A schema-v1 file with `gateway_group.network_id` omitted remains valid and uses the documented discovery order.
- One `VMManager` reports a successful implicit or explicit network selection once while repeated internal resolutions still perform their authoritative SDK reads and return the current network identity.
- Existing-instance discovery never says the instances were found "for recreation" when `recreate` is false.
- Focused tests cover omitted and explicit network selection, repeated resolution output cardinality, preserved SDK call cardinality, and recreation wording without live cloud access.

#### Verification

- Run focused gateway-subnet, VM-manager, configuration-loader, and CLI-output tests, then Ruff, mypy, and the full unit lane. No live apply is required for source verification.

### REQ-010: Keep the primary VPN status table concise and complete

- Status: satisfied
- Requirement: Remove the redundant `Traffic State` column from the primary `VPN Gateway Status` table and display every configured tunnel name completely.
- Constraints: Preserve configured tunnel roles, IPsec/BGP/peer/encryption/uptime values, every success and error row, the separate `Traffic Override` warning, ECMP warnings, service/routing sections, read-only behavior, and exit semantics. Keep the existing Rich-based responsive layout without assigning a brittle fixed tunnel width.
- Non-goals: Removing runtime override detection, changing tunnel selection or health classification, adding a replacement status flag, changing configuration or cloud state, or introducing a machine-readable status schema.

#### Acceptance criteria

- The primary table has exactly `Tunnel`, `Configured Role`, `Gateway VM`, `IPsec`, `BGP`, `Peer IP`, `Encryption`, and `BGP Uptime` in that order.
- Tunnel values use folded overflow: the complete value remains on one line when space permits and wraps without an ellipsis on narrower terminals, including schema-valid 64-character names.
- Preferred, fallback, empty, timeout, parse-error, and exception paths emit exactly eight cells per row.
- Runtime role differences remain visible through the existing `Traffic Override` panel even though the per-row Traffic State value is removed.

#### Verification

- Run focused table-construction, long-name rendering, preferred/fallback row, Traffic Override, and no-color tests, followed by Ruff, mypy, full unit and isolated CLI integration suites, Markdown lint, canonical-spec validation, security review, and diff-integrity checks. Live cloud or gateway execution is not required.
- Offline implementation evidence on 2026-08-20: the pure table constructor
  exposes the exact eight-column contract with folded Tunnel overflow; a
  64-character constrained-width regression proves lossless wrapping, and an
  AST row-arity check proves all seven preferred, fallback, and error branches
  emit eight cells. Traffic Override regressions remain green. Full Ruff and
  mypy passed, all 1,116 unit tests and 69 isolated integration tests passed,
  and changed-scope documentation, security, alignment, and diff-integrity
  checks found no introduced blocker.

### REQ-011: Enforce owner-aware BGP export safety in VM-HA

- Status: active
- Requirement: Prevent a non-forwarding VM-HA member from exporting locally learned remote routes, make active/passive BGP export and routing-hygiene parity part of authoritative readiness, complete and periodically re-enforce passive routing hygiene after materialization, and make `list-routes-local` strictly observational.
- Constraints: Preserve established BGP sessions and imported remote routes on a warm passive member; preserve `gateway.local_prefixes`, connection-level `advertise_local_prefixes`, tunnel MED ordering, optional `remote_prefixes`, existing CLI arguments, route-table display columns, exit behavior, the four-column VM-HA status table, non-HA behavior, configuration schema, logical manifest format, and persisted runtime records. Only an explicit mutating workflow may upload configuration or reload a gateway.
- Non-goals: Requiring an inbound prefix whitelist, adding a list-command repair flag or compatibility alias, changing cloud ownership or VPC route semantics, enabling active-active forwarding, introducing a second BGP configuration owner, or treating configured active/passive preference as current ownership.

#### Acceptance criteria

- Every enabled BGP neighbor has exactly one outbound route-map. A peer whose connection policy and current runtime authority both allow origination receives only the normalized `gateway.local_prefixes` with the existing MED tier; every other enabled peer receives an explicit deny-all policy. Disabled tunnels create no neighbor or policy, but a BGP policy with no enabled tunnels still verifies that the live FRR peer set is empty and treats any stale peer as drift.
- A passive or blocked VM-HA member advertises an empty prefix set to every configured peer while retaining required BGP sessions and learned remote routes during normal operation. If exact deny-all installation cannot be proved at a failure boundary, stopping FRR is the fail-closed fallback until a required reconcile restores the warm sessions. The exact active owner advertises precisely the allowed local prefix set and never re-advertises peer-learned routes.
- Active enablement verifies exact owner exports while forwarding remains disabled. Passive materialization verifies the current-boot passive guard and disabled forwarding, refreshes the exact firewall, renders deny-all exports, removes table-220 rules/routes and only the broad `169.254.0.0/16` route while preserving peer `/32` routes, verifies postconditions, and writes its receipt last. Before requesting a new agent materialization in the same boot and generation, the controller durably invalidates the prior receipt and accepts only a newly written receipt after routing-lock handoff. Any failure leaves no success receipt and returns authority to `BLOCKED`.
- The controller derives its expected per-peer export set from the same resolved node `gateway.local_prefixes`, connections, and connection-level origination flags consumed by rendering. Missing or inconsistent resolved policy must fail closed; it must not be projected as an empty expected prefix set.
- Passive and blocked transition safety may prove zero exports before every peer establishes only when the live peer set is exact, every established peer has an empty Adj-RIB-Out, and the running FRR configuration binds every expected peer exclusively to the exact deny-all route-map. Missing or additional bindings, nonempty established-peer exports, unexpected peers, or unavailable running configuration still fail closed. Readiness and observational audit retain their existing `UNKNOWN` result for any non-established expected peer.
- The periodic routing owner accepts either exact current-boot active readiness or an exact current-boot passive guard with forwarding disabled, then rechecks that authority under the routing lock. Its passive branch may remove only table-220 rules/routes and the broad `169.254.0.0/16` route plus flush the route cache when those removals changed state; it must not enable forwarding, rewrite sysctls, create peer/local-prefix routes, render services, mutate cloud/VPC state, or acquire active-owner authority. Blocked, stale, mismatched, or lock-contended authority skips or fails without routing mutation.
- A DHCP/network renewal or other external writer that reintroduces table-220 state or the broad APIPA route after passive materialization is removed within the configured timer cadence. Route-only table-220 state is equivalent to a policy rule for cleanup and health. Until the exact postcondition is restored, local transfer/promotion readiness, heartbeat readiness, `standby_ready`, redundancy health, and operator status must remain degraded rather than reporting the member healthy.
- Bounded per-peer Adj-RIB-Out evidence is compared as `MATCH`, `DRIFT`, or `UNKNOWN`. Missing or malformed output, a non-established expected peer, or unavailable/transitioning HA authority is `UNKNOWN`, never a match and never repair authority.
- Ordinary VM-HA `status` retains its exact table shape and redaction contract. Proven active-owner export drift is unsafe and blocks readiness; standby-only export or local routing-hygiene drift degrades an otherwise safe owner; unavailable owner/standby evidence follows the existing conservative `UNKNOWN`/`DEGRADED` precedence. Its routing table detects both a table-220 policy rule and route-only table-220 contents without performing repair.
- `list-routes-local` performs no remote write, upload, service start/reload, or reconciliation wait. It preserves its route tables and exit semantics while reporting owner-aware advertisement `MATCH`, `DRIFT`, or `UNKNOWN` and directing repair to an explicit mutating workflow. Repair operates only on proven drift under one stable common owner, allocation, and generation plus each target member's stable local Compute ownership epoch. It reconciles only the already-installed configuration while holding the apply/rearm, mTLS-writer, and routing locks from the on-node authority recheck through rendering and persistence; forces a render even when the configuration hash is unchanged; and requires exact authority and advertisement rechecks after reconciliation. Configuration installation remains owned by `apply`.

#### Verification

- Run focused FRR-renderer, mixed-connection, VM-HA runtime/readiness, passive-materialization, routing-hygiene, route-manager audit/repair, CLI/status/redaction, configuration compatibility, and composed-runtime tests; Ruff; mypy; full unit and isolated integration suites; Markdown lint; canonical-spec validation; security and alignment review; wheel build; and diff-integrity checks. Offline checks do not authorize or prove a live deployment.
- Offline implementation evidence on 2026-08-20: focused export-safety and composed-runtime coverage passed, including renderer-version upgrade, same-config forced reconcile, malformed FRR evidence, passive failure withdrawal, stopped-FRR recovery, and lock-held active promotion regressions. Ruff, mypy, the full serial unit and isolated integration suites, changed-scope Markdown lint, CLI help, wheel build, security review, and final risk review passed. No live cloud or gateway mutation was performed.
- Offline implementation evidence on 2026-08-21: the existing five-minute route
  timer now dispatches under the shared routing lock to ordinary, exact active,
  or narrowly fenced passive maintenance and rechecks authority before every
  mutating branch. Focused regressions prove recurring passive cleanup,
  exact tokenized table-220 detection that preserves unrelated priority-220
  and table-2200 rules, broad-APIPA detection, active/passive/blocked
  admission, forwarding and current-boot fencing, cold-standby readiness, and
  fail-closed local and remote status observation. Final risk review found and
  the implementation repaired priority/table-prefix false positives and an
  unsupported rearm recommendation. Ruff, mypy, all 1,335 unit
  tests, all 69 isolated integration tests, the wheel build, changed-scope
  documentation checks, security and alignment review, and diff-integrity
  checks passed. No live gateway, cloud, route, or SSH mutation was performed.

### REQ-012: Align every public command with topology and routing mode

- Status: active
- Requirement: Give every public `nebius-vpngw` command, subcommand, and flag one explicit, tested applicability contract across ordinary single-VM versus explicit VM-HA configurations and static versus BGP routing, and fail before authentication or mutation when a requested combination is unsupported or the installed gateway agent lacks the required private repair capability.
- Constraints: Preserve the existing public command tree, arguments, option names and aliases, configuration schema, ordinary single-VM workflows, VM-HA controller ownership, and observational list/status behavior. Preserve connection-level and tunnel-level static prefixes. A failed route or advertisement operation exits nonzero and never prints a success-style completion message.
- Non-goals: Adding compatibility aliases or public repair flags, making list commands mutating, bypassing VM-HA authority with direct VPC route writes, using tunnel failover as VM ownership failover, silently accepting an older installed agent, or adding a second route or configuration owner.

#### Acceptance criteria

- One centralized applicability policy covers all 18 executable leaves before authentication, prompts, SSH, cloud mutation, or agent requests. VM-only operations reject ordinary configurations; ordinary-only tunnel restart, tunnel failover/failback, and destructive removal reject explicit VM-HA configurations with canonical alternatives. Tunnel failover/failback also reject static routing.
- `add-routes-local` retains direct VPC route management for ordinary static and BGP configurations. For explicit VM HA, it never derives next hops from member primary allocations or mutates controller-owned VPC routes; static mode exits with controller-owned guidance, while BGP mode may repair only exact proven advertisement drift under the existing VM-HA authority contract.
- `--summarize`, `--swap-route-table`, and `--yes` are rejected for VM-HA route repair; `--yes` is also rejected when no route-table swap was requested. Rejection is parse- or plan-time and precedes every external effect.
- Any workflow that can invoke a private installed-agent action first performs a bounded read-only capability handshake on every affected gateway. Missing, malformed, or incomplete capability evidence reports installed/source skew, directs the operator to the supported apply workflow, and prevents all route or agent mutation.
- Every VM-HA route audit or repair freezes the exact immutable per-member SSH host-pin policy before authentication and uses the lifecycle hostname as the host-key alias for capability, status, FRR, and private repair requests. Missing, changed, or mismatched trust evidence fails before the first gateway request.
- Route target selection and remote-route listing use one canonical normalized remote-prefix resolver. Static prefixes are the union of connection-level prefixes and enabled, instance-scoped tunnel `static_routes.remote_prefixes`; BGP prefixes remain learned from the configured peers.
- Mixed ordinary static/BGP plans capability-probe and query FRR only on gateway members that own a configured BGP policy. A BGP policy whose tunnels are all disabled still verifies that its member has no stale live peers.
- Mutating route helpers return typed success or raise typed failure. Prerequisite, SDK, route-table, SSH, authority, repair, and postcondition failures propagate to a nonzero CLI result; success is rendered only after the complete requested workflow converges.
- Matrix tests cover every executable leaf and relevant flag across ordinary-static, ordinary-BGP, VM-HA-static, and VM-HA-BGP plans, including valid tunnel-only static prefixes, installed-agent skew, zero-effect rejection, and no false-success output.

#### Verification

- Run focused applicability, static-prefix, route selection, advertisement repair, installed-agent parser/capability, help/flag-manifest, and four-mode CLI tests; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; canonical-spec validation; security and alignment review; wheel build; and diff-integrity checks. Offline checks do not prove that an installed gateway has been upgraded or that a live route repair converges.
- Offline implementation evidence on 2026-08-20: the complete 18-leaf/four-mode applicability matrix, zero-effect rejection sentinels, capability-skew, static-prefix, mixed ordinary routing, exact VM-HA SSH trust, strict route outcome, public flag-manifest, and help regressions passed. Ruff, mypy, 1,283 unit tests, 69 isolated integration tests, changed-scope Markdown lint, security and code-quality review, wheel build/inspection, and diff-integrity checks passed. No live cloud, SSH, gateway, or route mutation was performed.

### REQ-013: Manage per-deployment VM-HA SSH host trust

- Status: active
- Requirement: When `VPNGW_SSH_KNOWN_HOSTS_FILE` is unset, resolve exact VM-HA SSH host trust from a deterministic per-deployment operator-side store and let `apply` create or repair that store only from authoritative local key material or previously validated exact pins.
- Constraints: Preserve exact host-key verification, immutable per-operation snapshots, fail-closed behavior, read-only status, explicit default-disabled VM HA, all ordinary SSH and non-HA behavior, the existing configuration schema and CLI surface, and the absolute environment override as highest precedence. An explicitly configured but invalid override never falls back. Do not implicitly read or modify the user's general `~/.ssh/known_hosts`, learn a key from `ssh-keyscan` or the live SSH handshake, disable verification, or treat transport reachability as identity proof.
- Non-goals: Generating or rotating SSH server private host keys, repairing trust from network-only evidence, making status or route/list/transfer/mTLS commands persistent trust writers, automatically replacing a retained gateway when trusted evidence is absent, or changing VM-to-VM mTLS identity.

#### Acceptance criteria

- The managed store is `~/.ssh/nebius-vpngw/<scope-sha256>/`, where the scope digest is derived deterministically from canonical tenant, project, region, gateway-group, and VM-HA cluster identity. It contains one versioned public-key-only receipt keyed only by stable member hostname and one derived OpenSSH `known_hosts` projection carrying that same key under the stable hostname plus the exact current configured or discovered management-address aliases needed by supported older releases; neither contains private key bytes, credentials, peer certificates, or secret configuration.
- Managed trust authority is keyed by each stable gateway hostname and carried to the discovered management address through `HostKeyAlias`; address changes do not silently create a new identity. An explicit source may name the exact hostname or current configured/discovered address, but all matching aliases must resolve to one unambiguous key before normalization. The non-authoritative projection may change when `apply` observes a new address, while the receipt identity remains unchanged.
- `VPNGW_SSH_KNOWN_HOSTS_FILE` remains the highest-precedence source. A missing, unreadable, malformed, symlinked, empty, incomplete, revoked, ambiguous, or mismatched explicit source fails without reading the managed store. A successful actual `apply` verifies retained members through those pins before importing only the exact member keys; it never rewrites the explicit file.
- With no override, `apply` may recover from a valid managed receipt/projection or derive a missing member pin from the original unencrypted, owner-only private host key that will be or was installed for that exact member. A retained member whose available trusted sources disagree, or for which no authoritative source remains, blocks before cloud mutation. Fresh or explicitly approved replacement members still require their exact private host-key material and existing provisioning authority.
- `apply --dry-run` validates and reports a secret-free trust action without creating directories, receipts, or persistent pins. Actual apply verifies every retained member first, then publishes an owner-only authoritative receipt and derived projection with locked compare-and-swap and atomic per-file replacement before the first cloud mutation. The receipt is committed first, so an interruption can leave only a stale or missing reproducible projection, never partial authoritative receipt content. Concurrent, symlink, hard-link, ownership, or mode failures block publication.
- `status` and every non-apply VM-HA command may consume an existing managed receipt through the same immutable snapshot policy but never create, import, repair, or persist trust. Missing trust remains isolated to the affected status member; mutating commands fail before authentication and direct the operator to `apply`.
- An existing deployment may migrate without changing YAML: run one successful `apply` with its existing explicit trust file or retained original private host keys, then unset the environment variable. Older releases can consume the generated OpenSSH projection by explicitly pointing `VPNGW_SSH_KNOWN_HOSTS_FILE` to it; compatibility is verified against exact current address lookup rather than inferred from hostname-only content.

#### Verification

- Cover deterministic scope identity, source precedence, stable alias normalization, exact OpenSSH parsing including hashed aliases and marker handling, receipt validation, file ownership/mode/link checks, atomic publication and concurrency, dry-run and status no-write behavior, retained/fresh/mixed recovery, explicit-source migration, mismatch rejection, pre-cloud-mutation ordering, unchanged ordinary SSH/non-HA behavior, route and mTLS trust consumers, documentation, and packaging. Run Ruff, mypy, focused and full unit/integration suites, canonical-spec validation, Markdown lint, security review, alignment review, and diff-integrity checks. A live trust migration or gateway mutation remains separately authorized non-production acceptance work.

## Task Implementer Requirements

### TI-REQ-001: Add opt-in two-node VM-level active/passive HA

- Status: active
- Requirement: Allow one gateway group to operate as exactly two stable VM members with one active owner and one passive candidate, independently of the existing per-tunnel active/passive roles.
- Constraints: VM-level HA must be explicit and default-disabled; omitting it must preserve supported configuration, CLI, allocation naming, planning, deployment, status, and route behavior for existing users.
- Non-goals: Active-active forwarding, ECMP, more than two HA members, legacy aliases, migration shims, or changes to existing tunnel-level HA semantics.

#### Acceptance criteria

- A valid VM-HA configuration resolves two deterministic node identities and one shared cluster identity.
- After provisioning, each node receives one secret-free runtime binding that names the single shared secondary private-alias allocation, both authoritative Compute instance and NIC identities, peer endpoint and credential file references, and the route-runtime identity needed by the controller.
- Migrating one supported ordinary gateway retains its Compute instance, boot disk, NIC, primary private allocation, public allocation, and serving route attachments; it adds one passive member and one movable secondary private alias without rewriting either member's immutable primary address.
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
- An ordinary-to-HA migration requires an exact desired-and-current-state plan plus interactive approval or `--approve-vm-ha-migration DIGEST`. The digest binds desired generation, topology, policies, resource names, mutations, rollback intent, retained cloud identities and revisions, shared-allocation state, and exact managed routes. It is recomputed immediately before durable intent; unchanged retries resume only the same checkpointed operation without creating duplicate resources.
- A durable operation-and-generation apply lock is installed and verified on both members before either HA runtime is activated. The lifecycle becomes `ACTIVE` only after exact node parity, active alias ownership, passive non-forwarding readiness, route cutover, and independent postcondition checks succeed.
- Each fenced apply declares the one cloud-observed current owner on that owner only. The declaration binds the exact lock operation, cluster, members, allocation, generation, and policy digests; the agent accepts it only while independent cloud observation confirms that same local owner. This declaration may establish ownership continuity for a promoted configured-passive owner across a generation change, but it grants no allocation movement or forwarding authority. A generation-current terminal receipt replaces it only after the ordinary owner, route, forwarding, no-lock, and no-pending-effect gates pass; malformed, foreign, mismatched, or orphaned declarations block safely.
- If the final `ACTIVE` persistence reports failure, apply re-reads the exact lifecycle record. It accepts only the exact `ACTIVE` successor plus fresh active/passive status proof, or the exact `ACTIVATING` predecessor followed by passive-first and then active exact-operation relocking and independent blocked/non-promotable proof. Missing, malformed, foreign, or other successor state is an unsafe blocker and is never reported as successful or safely locked.
- Generation or required-policy mismatch keeps the active serving, marks the passive non-promotable, and disables automatic failover until parity is restored.
- An explicitly authorized emergency active-only update also disables automatic failover until both nodes are synchronized.

#### Verification

- Run deterministic manifest, digest, passive-first apply, corruption, interrupted-write, and resynchronization tests using injected filesystem and node failures.

### TI-REQ-003: Prevent split brain with authoritative fencing and allocation ownership

- Status: active
- Requirement: Permit promotion only after Nebius Compute authoritatively reports the former owner stopped, the former secondary-alias attachment is absent, and the shared private alias allocation is independently confirmed on the candidate.
- Constraints: Peer heartbeat, local role, route state, transition journals, timeouts, and process failure are advisory only; ambiguous, unavailable, transitional, running, stopping, or error cloud states must block promotion.
- Non-goals: Consensus claims, lease authority derived only from the two VMs, simultaneous forwarding, or promotion based on loss of peer connectivity alone.

#### Acceptance criteria

- Exactly one node may enable forwarding and owner-only reconciliation for each authoritative allocation snapshot.
- The enforced transition order is former owner stopped, former attachment absent, new attachment exact, ownership re-read exact, then candidate promotion.
- Ownership continuity is keyed by the exact attached candidate Compute resource revision read after assignment; allocation status alone and locally synthesized journals, hashes, or counters are not authoritative ownership epochs.
- Every HA member starts with forwarding and cluster tunnel initiation fail-closed; a boot, process restart, or automatic Compute recovery requires fresh role and cloud-ownership proof before the appropriate passive or active data plane is enabled.
- Every external side effect has durable before-and-after checkpoints and can be retried without skipping fencing or duplicating an unsafe mutation.
- Every provisioning effect declares an exhaustive normalized cloud-observation path set. Recovery accepts only the unchanged pre-state or that exact effect's permitted result; partial outcomes, unrelated drift, unstable rereads, unregistered effects, and extra changes fail closed before another mutation.
- Every accepted HA cloud mutation persists its exact cloud-operation identity before a bounded wait and resumes that operation after restart. The receipt clears only after the SDK reports terminal success; terminal failure or unavailable success status retains it and blocks. Request, authentication, retry, polling, and overall operation waits are finite and use the same replay-stable idempotency identity; ordinary non-HA SDK behavior is unchanged.
- Allocation transfer updates only the exact HA secondary alias and preserves both members' immutable primary addresses and all unrelated NIC fields and aliases.
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
- Ordinary-to-HA keeps the existing serving routes unchanged while both nodes are staged and locked. Owner-gated reconciliation advances only after exact active authority; a failed managed-route replacement restores the exact removed route before the controller reports failure.
- Every managed-route delete, create, and restore persists a pending mutation before its request and uses a replay-stable idempotency identity. A timeout is resolved by authoritative reread and same-identity replay; restoration occurs only after terminal create failure and exact proof that the desired route is absent. Duplicate, stale, or conflicting outcomes remain blocked and operator-visible.
- The route-mutation v2 record stores exact rollback content, mutation phase, and accepted cloud-operation identity. Legacy v1 records remain readable without rewrite; recovery may upgrade a replacement only while the original route is still exactly observable, and blocks when neither original nor desired outcome can be proven.

#### Verification

- Run owner-gating, static-manifest, local-FRR, hold-down, stability, withdrawal, partial-failure, retry, and existing non-HA route-selection tests.

### TI-REQ-005: Recover a deterministic fail-closed HA controller

- Status: active
- Requirement: Implement one explicit controller for heartbeat evaluation, readiness, suspicion, fencing, ownership transfer, promotion, degradation, recovery, and manual failback.
- Constraints: Persist immutable revisions and transition checkpoints atomically; authenticate peer traffic; reject stale boot identities and heartbeat sequences; install a cold-start data-plane guard before strongSwan, FRR, or the gateway agent can use stale HA state; permit only deterministic node-local rendering and validation while that guard is blocked so clean members can establish readiness without enabling forwarding, tunnel initiation, firewall, route, allocation, or VPC effects; use bounded timers and injected clocks.
- Non-goals: Automatic failback, distributed consensus storage, Object Storage as a correctness dependency, or the append-only journal as ownership authority.

#### Acceptance criteria

- The controller exposes normal, suspect, fencing, ownership-transfer, promoting, active, degraded, and blocked outcomes with explicit prerequisites.
- On every boot or restart, the controller begins behind the cold-start guard, re-reads Compute and allocation ownership, and enables only the data-plane mode justified by fresh authoritative state.
- On a clean two-node bootstrap, both members can materialize and validate the current generation behind the blocked guard without depending on promotion readiness; the passive and any node without fresh ownership proof remain non-forwarding and effect-free.
- Automatic failover requires generation parity plus required static, BGP, XFRM, service-health, and cloud-ownership readiness.
- Restart at any checkpoint reconstructs the next safe action from committed local state and current cloud truth without enabling forwarding early.
- Controller checkpoint v2 durably binds each ownership transfer to the attach action, allocation, former and candidate nodes, generation and policy digests, ownership incarnation, and strictly advancing pre/post candidate Compute revisions. V1 checkpoints remain readable; legacy in-flight states without sufficient continuity stay guarded and require exact detach/reattach reproof, while a stable pre-existing active baseline remains adoptable without fabricating historical transfer proof.
- Authenticated heartbeats report role, owner observation, generation, policy digests, service health, route readiness, and promotion readiness without carrying secrets.
- Every forwarding writer, route timer, agent startup path, and service dependency remains behind the current-boot guard until the controller durably records and exposes the justified data-plane mode; controller stop, failure, or stale readiness restores the guard.

#### Verification

- Run table-driven state-machine, stale-heartbeat, boot-change, timeout-boundary, dual-suspicion, filesystem-fault, cloud-failure, route-failure, and restart tests.

### TI-REQ-006: Provide safe operations, security, and offline proof

- Status: active
- Requirement: Expose generation parity, observed owner, promotion readiness, fencing progress, degraded reasons, safe operator guidance, and manual failback through the existing operator workflow when VM HA is enabled.
- Constraints: Use the narrowest current Nebius permission boundary that covers the required Compute ownership and VPC allocation/route mutations, keep secrets out of manifests, journals, status, and logs, package all required services, and perform no live cloud mutation without a separately approved non-production trial. Every operator-command and staging SSH path must use one exact operator-configured or REQ-013 product-managed host-key trust source that is validated before any cloud mutation; trust-on-first-use, disabled host authentication, and permissive fallback are unsupported.
- Non-goals: Renaming or silently changing existing non-HA commands, automatic failback, production validation, or claiming live readiness from offline tests alone.

#### Acceptance criteria

- Non-HA command syntax, defaults, output meaning, and exit behavior remain supported.
- When VM HA is omitted or disabled and no valid lifecycle record exists, ordinary apply performs no HA-specific Compute, VPC, allocation, SSH, or runtime discovery. Permission or availability failures in those HA-only APIs therefore cannot block a never-HA customer.
- The first ordinary-to-HA apply presents a mutation and rollback preview, requires confirmation or the exact shown `--approve-vm-ha-migration DIGEST`, and keeps the existing gateway and routes serving until the new pair is independently ready for reversible cutover. An interrupted no-lifecycle/two-VM topology requires the separately domain-bound `--recover-vm-ha-migration DIGEST`; the migration and recovery digests are never interchangeable. `--dry-run` produces the same plan without lifecycle or cloud mutation.
- Removing explicit VM HA first selects the requested service-account credential when configured, or the operator credential otherwise, so a default-disabled ordinary apply never requires broader operator Compute or VPC read authority merely to prove that no HA teardown is needed.
- Current managed HA state is selected by one secret-free v4 lifecycle transaction whose whole-record digest binds schema, monotonic revision, predecessor, status, project, gateway, approval and operation identities, effect checkpoints, path-level observation guards, accepted cloud-operation identity, route runtime, allocation, and both complete member identities. Writes are fsynced, reread, and compare-and-swapped under a canonical project-and-gateway apply lock. V2 and v3 records remain readable without rewrite on read or no-op; a quiescent approved transaction gains a v4 successor only before a new mutation, while a pending legacy effect blocks until its exact outcome is resolved. Activation persists `ACTIVATING` after exact provisioning and advances to `ACTIVE` only after active ownership/routes and passive unlocked non-forwarding are independently proven; removal advances `ACTIVE` or `ACTIVATING` to `REMOVAL_IN_PROGRESS`. A verified `REMOVED` tombstone makes later ordinary applies teardown-free and idempotent.
- An unchanged v4 `ACTIVATING` retry validates stable authoritative cloud and runtime identities and resumes only the host activation workflow. It does not re-enter VM provisioning, finalize provisioning again, or write a second `ACTIVATING` transition.
- If recovery outside the product workflow has returned an interrupted v4 `ACTIVATING` transaction to the exact configured-active cloud owner, ordinary retry remains blocked until the operator accepts a separate `--recover-vm-ha-migration DIGEST` preview. That approval may append one replacement `PROVISIONING` successor only when the desired generation and every project, gateway, cluster, allocation, member, disk, NIC, subnet, primary/public address, role, runtime, and route-target identity remain exact; Compute revisions have only advanced; the configured active alone owns the exact shared alias; no cloud effect or accepted operation is pending; and only host activation effects remain incomplete. The successor then resumes the canonical passive-first ensure, stage, lock, and activation workflow. An ordinary same-observation retry may durably rewind an incomplete later host-only activation effect solely to insert the newly required exact-lock-bound owner-adoption declaration; it never marks the interrupted effect complete, changes cloud bindings, clears a remote lock, or supersedes a cloud effect, and it replays that host verification after adoption. Any other drift or pending cloud work remains blocked.
- A v4 `PROVISIONING` transaction whose newly created passive cannot pass the SSH/bootstrap gate may expose one explicit passive-replacement preview and a domain-separated `--replace-failed-vm-ha-passive DIGEST` approval. The replacement must append exact intent, accepted-operation, retirement, and replacement receipts to the existing transaction; retain the active Compute, disk, NIC, revision, forwarding state, both members' primary/public allocations, the shared allocation and owner, route targets, desired generation, and original migration approval; delete only the receipt-bound passive Compute and task-created boot disk; and resume the ordinary passive-first provisioning path with the replacement identity. A stale or foreign digest, completed staging or activation effect, non-passive target, ambiguous resource, active/shared-allocation drift, or unrelated cloud change stops before mutation. Generic `--recreate-gw` is never this recovery path.
- Every VM-HA Compute create accepts only the submitted boot disk, single NIC, project, gateway subnet, primary/public allocations, and pre-existing alias set; unrelated alias or resource substitution inside the nominal create footprint fails closed. Every HA-only Compute, VPC, allocation, operation-resume, and route-target observation uses the finite request/auth/retry policy, and long-running operation polling owns only the SDK poll-specific timeout and retry arguments.
- With no valid lifecycle record, ordinary apply does not infer or discover former HA state. Missing-record recovery or cleanup requires an explicit operator command and exact identity evidence; allocation or member names alone never authorize adoption or teardown.
- Coherent two-member HA runtime evidence drives an exact allocation read plus repeated Compute, NIC, attachment, owner, and runtime identity checks. The migration persists and rereads `ACTIVE` provenance before change analysis or teardown; rejected change or confirmation paths leave both members untouched.
- An approved removal checkpoints `REMOVAL_IN_PROGRESS`, revalidates the complete evidence immediately before mutation, and installs the same exact-operation inhibition on both members under the shared writer lock. Both controllers must acknowledge the gate with no pending or accepted effect, then both rearm and safety-controller services must be stopped and proved inactive on every member before the first deactivation. A durable post-stop checkpoint makes a crash resume at idempotent deactivation without contacting an already-deactivated agent. Removal then clears HA-only systemd state and credential references and writes `REMOVED` only after both terminal non-HA states are independently verified. Ordinary provisioning begins only after that terminal proof, apart from selecting or creating the requested service account needed for the read-only migration probe.
- VM-HA status explains why a passive is promotable or blocked and names the safe operator recovery action.
- VM-HA status validates pinned transport, cluster, node, role, generation, manifest digest, operation identity, alias attachment, route-cutover state, and apply-lock state before reporting authority.
- Activation polling retries only well-formed same-node generation, apply-lock, and predicate convergence. Malformed status JSON/schema, foreign cluster/node/role/runtime binding, and foreign lock operations abort immediately, and a timeout retains the last typed stale diagnostic.
- Agent status projects the current durable writer inhibition and forwarding guard over the last successful controller snapshot. If a controller effect fails after a lock transition, the agent restores the guard and persists a closed, secret-free failure reason when possible, so polling cannot misclassify the failure as a stale lock state.
- Before owner route reconciliation, an obsolete local route-ledger identity may be retired only after its exact route target is reverified on each read and two identical authoritative route listings prove that identity absent. This local authority cleanup performs no cloud route deletion; unstable observations, malformed backend results, or a still-present mismatched route fail closed.
- If final `ACTIVE` persistence is ambiguous and the exact `ACTIVATING` predecessor remains, recovery restores and verifies the passive then active exact-operation apply locks. The passive must be non-forwarding; the exact owner may remain active only when its current owner and route-runtime receipt are independently exact.
- Manual failback follows the same fencing, ownership-transfer, readiness, and route-reconciliation invariants as automatic failover.
- HA activation aborts on the first critical remote failure, revalidates the remote generation and digest immediately before installation, and never reports success from stale staging acknowledgements or unverified guard/controller state.
- Apply rejects absent, unreadable, empty, malformed, ambiguous, conflicting, or unusable SSH trust when REQ-013 cannot reconstruct the complete exact policy from authoritative evidence. OpenSSH and Paramiko consume the same validated policy, and host-key rejection is reported distinctly from transport reachability failure.
- Manifests, status, journals, and logs contain only absolute credential references; credential material is installed separately with restrictive permissions, and HA IAM grants are selected only from a reviewed action-to-role allowlist.
- On the current Nebius IAM surface, the dedicated VPNGW runtime service account is the sole member of a dedicated custom group with exactly one project-scoped `editor` access permit. This is the minimum available permission boundary that covers both Compute ownership transfer and VPC allocation/route mutation: unsupported service-specific editor role names and unsupported permits scoped directly to VPC route-table or network resources are rejected. Exactly one renewable authorized-key credential is enrolled separately for the runtime.
- An explicit `apply --sa NAME` uses the current Nebius SDK resource APIs to select or create that exact service account, group, membership, and reviewed project permit, then obtains a short-lived impersonated token through the supported Nebius CLI. Any missing token, ambiguous identity, foreign group member, extra permit, unsupported role, or enrollment/readback failure stops before product cloud mutation; the command never silently falls back to ambient operator credentials.
- Offline two-node tests prove no forwarding or VPC-route mutation occurs before authoritative fencing and exact allocation ownership.
- The ordinary automated CI path selects the composed clean-bootstrap, passive non-forwarding, SSH trust preflight, and host-key mismatch regressions rather than leaving them manual-only.
- The ordinary CI lint gate runs the canonical all-source mypy check exactly once, and each mutually exclusive workflow lane builds the release wheel exactly once.
- A later live-ready claim requires a separately authorized non-production trial with independently observed cloud, allocation, forwarding, and route postconditions.

#### Verification

- Run focused CLI, IAM, systemd, packaging, build, release, security, and deterministic composed failover tests, followed by the full unit and integration suites.

### TI-REQ-007: Live-validate the supported GCP multi-VM HA topology

- Status: active
- Requirement: Exercise the explicit two-node VM-HA product path against the authorized non-production GCP and Nebius projects, repair causal product defects, and independently prove steady state, automatic failover, and manual failback without creating a Nebius VPC ECMP data plane.
- Constraints: Freeze the candidate artifact and declaration before each trial; keep GCP fixture setup, environment recovery, product execution, and independent verification separate; retain one regional GCP HA VPN gateway and one Cloud Router for the target topology; preserve supported legacy single-peer helper behavior; and never expose tunnel secrets or credential material in commands, files, logs, status, or evidence.
- Non-goals: Production validation, active-active Nebius forwarding, automatic failback, treating fixture repair as product proof, deleting an unclassified GCP peer resource, or claiming a clean trial from a run whose product-owned transition was pre-satisfied externally.

#### Acceptance criteria

- The GCP helper retains its existing single-Nebius-peer, two-tunnel mode and adds an explicit VM-HA mode for two Nebius public peer IPs, four unique HA VPN tunnels, four Cloud Router interfaces, four BGP peers, and distinct APIPA `/30` ranges.
- The VM-HA helper plan uses one regional GCP HA VPN gateway and one Cloud Router. It models the external peer resources required by the current GCP API separately from regional gateway count and fails closed on incompatible existing resource shape.
- GCP routes advertised toward the configured active Nebius member use a lower numeric Cloud Router advertised-route priority than the configured passive member. The helper documents that this affects GCP-to-Nebius advertisements only; Nebius-to-GCP path selection is independently owner-gated by the product.
- Only the authoritative Nebius VM owner forwards traffic or reconciles static and locally learned BGP routes into the Nebius VPC. The shared secondary alias remains the stable VPC route next hop and equal-best active Nebius route writers are never enabled.
- Ordinary `status` preserves non-HA output and, for a managed VM-HA gateway, conservatively reduces independently authenticated lifecycle, cloud, generation, ownership, forwarding, route-cutover, and promotion-readiness evidence to the aggregate title and four per-member semantic fields defined by REQ-007.
- Live configuration uses environment-backed secret references and restrictive local permissions. Every node receives exact pinned SSH trust and renewable Nebius credential references before product mutation; its mTLS private key is generated and retained locally through the REQ-008 enrollment transaction.
- A clean steady-state trial independently observes one active owner, one blocked passive, established tunnel/BGP health, active-pair route preference, shared-alias route targets, and bidirectional traffic.
- A clean automatic-failover trial observes the product fence the former owner to Compute `Stopped`, transfer and re-read the shared alias on the candidate, reconcile routes, enable forwarding only afterward, and restore bidirectional traffic. Manual failback repeats the same invariant chain as a separate trial.
- Each live trial records bounded convergence duration, candidate identity, approved migration digest, authoritative cloud and host observations, rollback checkpoint, and any intervening recovery action. The automatic-failover trial also records stimulus-to-five-sustained-replies, the exact lost ICMP sequence count from real workload VMs in both directions, and secret-free start/completion/failure timing for every product-owned transfer effect. Failed or intervened trials are reported as such and replayed from a known-good supported checkpoint.
- Successful and failed VM-HA effects emit structured, secret-free lifecycle events that identify the action, durable operation identity, acting node, result, and monotonic duration without including credentials, tunnel secrets, cloud values, or exception text.
- Manual failback keeps the request on the configured-active node. If that exact Compute is `Stopped` while the configured-passive member is the exact running shared-alias owner, the operator workflow starts only the alias-free configured-active Compute with a resource-revision-bound idempotency key, continuously reproves the unchanged passive owner through startup, waits for its pinned SSH identity, and then submits the existing fenced request. It never attaches the alias, enables forwarding, or mutates routes before the controller transfer.

#### Verification

- Run deterministic fake-`gcloud` helper tests, focused VM-HA CLI/status tests, all repository unit and integration gates, and an opt-in live runbook with independently captured GCP, Nebius Compute/allocation/route, host service/forwarding, and traffic postconditions.

### TI-REQ-008: Repair fresh unhealthy owners before VM transfer

- Status: active
- Requirement: Distinguish a fresh unhealthy owner heartbeat from a missing heartbeat and allow exactly one bounded node-local repair attempt when the current owner, generation, guard, route authority, and shared-allocation ownership remain exact.
- Constraints: A repairable full data-plane outage receives one absolute five-second budget with one second reserved for verified local fencing; a repair attempt is bound to the exact cluster, allocation, owner, ownership revision, generation, boot, and first failure fingerprint; no command, new failure, process restart, or transient healthy sample may extend or reset that deadline. The VM-HA controller is the only repair writer in VM-HA mode. Heartbeat and repair state remain advisory, every wait requires an independent fresh cloud read proving the same owner and ownership revision, candidate promotion readiness remains mandatory, and automatic transfer still requires the former Compute owner to be authoritatively `Stopped` before alias transfer, route reconciliation, or forwarding.
- Non-goals: Repeated self-healing loops, treating tunnel count as path coverage, repairing cloud allocations or VPC routes, promoting to an unready candidate, using repair state as ownership authority, weakening missing-heartbeat fencing, or changing non-HA tunnel monitoring.

#### Acceptance criteria

- Health classification separates redundant-path degradation, repairable full outage, unsafe local authority, and unreachable peer outcomes across StrongSwan/IPsec, FRR/BGP, XFRM, static routes, forwarding consistency, current route receipt, current-boot guard, durable state, and cloud ownership.
- One failed tunnel or BGP neighbor remains a tunnel-level event only when every required prefix and traffic selector still has an equivalent usable path. Loss of the sole usable path for any required prefix is a full outage.
- A repairable outage persists one idempotent attempt before executing the smallest currently supported node-local action: FRR reload or restart for a BGP-only failure, StrongSwan and FRR restart followed by StrongSwan reload for a service or XFRM failure, or gateway-agent reload or restart for a remaining local static-materialization failure. Loss of forwarding is already a physical fence and follows the canonical owner-verified passive-materialization and active-enable path. Repair never stops a VM, moves an allocation, or mutates a VPC route.
- Repair commands receive the remaining monotonic budget, are individually bounded, and stop early enough to disable and verify kernel forwarding by the absolute deadline. The emergency guard bypasses the ordinary routing lock and physically disables forwarding before best-effort persistence. The ordinary systemd stop path retains its existing forwarding guard; a short watchdog that could terminate legitimate long-running cloud effects is not part of this repair boundary.
- Successful repair requires two complete fresh healthy observations. The consumed attempt resets only after sixty seconds of continuous health or a new authoritative ownership incarnation; recurrence, fingerprint churn, or an added failure before then is treated as flapping and cannot obtain another repair window.
- A fresh authenticated heartbeat that reports unhealthy service, route, or promotion readiness starts the existing passive suspicion window while the exact owner uses the matching five-second local budget. A fresh healthy heartbeat cancels suspicion; no repair report, timeout extension, or additional takeover authority crosses the wire. Missing, stale, mixed-version, changed-owner, ambiguous-cloud, or retired-boot evidence receives no grace.
- Repair exhaustion never authorizes alias transfer directly. The owner disables forwarding by the reserved deadline, while a ready passive follows the existing strict Compute-stop, detach, attach, ownership-reread, route-receipt, and forwarding sequence after its suspicion window. If the candidate is not ready, no transfer occurs; the exact owner remains degraded or blocked and the repair attempt stays consumed.
- Structured effect events expose secret-free repair operation, owner revision, failure fingerprint, healthy-observation count, remaining budget, transition state, and action duration. Public VM-HA status exposes only the aggregate title and per-member role, mTLS, and readiness projection defined by REQ-007.
- VM-HA peers use the clean-break protocol-v2 heartbeat that binds the mTLS epoch to the presented leaf. Mixed protocol versions fail closed before transfer admission; local repair still grants no extra remote grace or ownership authority.

#### Verification

- Run deterministic fault-matrix, exact-deadline, emergency-fence, checkpoint-migration, crash-replay, flapping, prefix-coverage, candidate-readiness, no-cloud-effect, and two-node transfer tests with injected clocks and bounded command fakes. Live acceptance separately stops FRR and StrongSwan, removes redundant and sole required paths, and disables forwarding while recording bidirectional loss, repair duration, fencing order, authoritative ownership, and whether VM transfer occurred. A controller-hang trial remains outside this boundary until a watchdog can distinguish local repair work from legitimate long-running cloud effects.

### TI-REQ-009: Expose a safe planned VM ownership failover

- Status: active
- Requirement: Provide an explicit `failover vm` operator command that moves VM-HA ownership from the configured active member to the configured passive member through the same fenced controller path as automatic failover.
- Constraints: The command is distinct from tunnel-level `failover tunnel`; requires the exact active lifecycle, configured member identities, a running configured-active exact allocation owner, and a running alias-free configured passive; targets only the configured passive; and may bypass only fresh-peer suppression and the automatic suspicion delay. Generation parity, apply-lock, candidate readiness, former-Compute-`Stopped`, allocation detach/attach, ownership re-read, route reconciliation, and forwarding gates remain mandatory.
- Non-goals: Direct allocation, route, forwarding, or Compute mutation from the request path; role reversal in configuration; automatic failback; or changing omitted, disabled, non-HA, or tunnel-HA behavior.

#### Acceptance criteria

- The operator preflight is read-only and fails before request submission on lifecycle, member, role, Compute-state, allocation-owner, attachment, SSH-trust, or generation mismatch.
- The configured passive persists one strict cluster-, node-, role-, and generation-bound request. A conflicting failback request or a request on the configured active fails closed.
- The controller consumes the request only after the configured passive is the exact promoted owner and retains the canonical former-owner stop, allocation transfer, ownership confirmation, route, and forwarding order.
- Deterministic controller and composed two-node tests prove that a healthy peer does not suppress the planned request and that every ordinary promotion gate remains unchanged.

#### Verification

- Run focused request-schema, role-confusion, stale-identity, request-consumption, controller, CLI preflight, and composed failover tests, then the full unit and integration suites and a clean live planned-failover trial with bidirectional workload probes and independent cloud, route, and forwarding postconditions.

### TI-REQ-010: Restore an exact stopped passive standby without promotion

- Status: superseded
- Requirement: Provide an explicit `vm-ha-rearm` operator command that restores the configured passive Compute to a verified running, non-owner, non-forwarding standby after a fenced transfer leaves it stopped.
- Constraints: Require the exact active lifecycle and member bindings, the configured active as the running exact allocation owner, and the configured passive to remain alias-free. Start only the stopped configured passive with a resource-revision-bound idempotency key, continuously re-prove the unchanged owner during startup, require pinned SSH, and finish only when the passive controller reports `normal`/`passive`, non-owner authority, and no apply lock.
- Non-goals: Moving the shared allocation, changing routes, enabling forwarding, accepting a foreign owner, re-arming an ambiguous topology, or using ordinary apply as an out-of-band recovery shortcut.

#### Acceptance criteria

- Already-running safe passive members are verified without a Compute start; stopped safe passive members receive exactly one stable start operation for their current resource revision.
- Any owner, attachment, Compute-state, lifecycle, identity, or SSH-trust drift aborts before further progress and never mutates allocation, route, or forwarding state.
- A successful command leaves the configured active as the exact owner and the configured passive running in `normal` controller state with passive data-plane mode, no local ownership, and no apply lock.

#### Verification

- Run focused exact-owner, foreign-owner, stopped/running passive, stable-idempotency, pinned-SSH, and terminal-status tests, then prove the command live after failback and re-check bidirectional traffic and authoritative ownership.

### TI-REQ-011: Restore role-neutral warm standby after every committed promotion

- Status: active
- Requirement: Unify planned and automatic VM ownership transfer around typed, durable intent, then automatically restore the exact non-owner Compute as a guarded warm standby after a terminally committed promotion.
- Constraints: Preserve the public `failover vm`, `failback vm`, and `vm-ha-rearm` commands, both role-bound transfer-request schemas, deployment lifecycle v4, controller checkpoint v4, strict former-owner `Stopped` fencing, and the single canonical allocation/route/forwarding cutover engine. Replace heartbeat v1 with the clean-slate epoch-bound protocol v2 required by REQ-008 and reject mixed versions. Rearm is enabled whenever explicit VM HA is enabled, has no YAML setting, is independently inhibitable, never performs automatic failback, and is the only Compute-start writer. Private transfer lineage, promotion, rearm, standby, and mTLS records are strict and separately versioned.
- Non-goals: Keeping the former owner running during allocation transfer, automatic return to the configured active, changing detection or repair windows, adding active-active behavior, granting the rearm service stop/allocation/route/firewall/forwarding authority, inferring promotion from cloud topology, exposing resource identities, or adding a metrics exporter.

#### Acceptance criteria

- Controller admission resolves exactly one `planned-failover`, `planned-failback`, or `automatic-failover` intent with trigger-specific role and request validation. Automatic suspicion remains cancellable before the first accepted external effect; afterward the typed lineage is sticky until terminal recovery even if the initiating request or suspicion disappears.
- Every transfer uses the unchanged ordered engine: stop former owner, detach its shared alias, attach the candidate, authoritatively reread exact ownership, reconcile routes, and enable forwarding. Existing checkpoint, pending-action, and transfer-continuity evidence remain execution authority.
- A promotion receipt becomes durable only after exact candidate ownership, route receipt, active forwarding, matching transfer-request consumption when applicable, no pending effect, and no apply lock are all durable. Rearm never infers terminal promotion from topology alone.
- An independent systemd rearm service runs on both members without becoming a `Requires=` dependency of the safety controller. It acts only on the exact stable owner with a matching promotion receipt and has read/start capability only.
- The rearm service submits at most one idempotent logical start for each promotion receipt and stopped-resource revision, resumes an accepted cloud operation after crash, prevents retry storms, and adopts an already-running alias-free target without mutation. Unknown, stopping, error, ownership drift, apply/removal activity, ambiguous operations, corrupt evidence, or service inhibition block safely.
- Repeating planned failover or failback when the requested role is already the exact healthy owner succeeds as an explicit identity-free no-op, writes no transfer request, and leaves forwarding unchanged. An unhealthy or ambiguous same-owner observation fails without mutation.
- An explicit rearm retry request authorizes at most one logical start attempt. Its exact request identity is durably consumed before the cloud call; service restart may resume only the same accepted operation, while a definite failure requires a new request. A retained accepted-operation journal is cleared only after exact operation-status success, including when it belongs to the matching checkpoint from an earlier promotion; foreign, unbound, failed, or unavailable operation evidence remains blocked.
- Rearm, retry submission, apply-lock installation and removal, and VM-HA removal inhibition share one writer lock. The enabled marker and apply lock are rechecked immediately before Compute start. Removal installs and proves the same exact-operation gate on both members, stops both mutation services everywhere before any deactivation, checkpoints that barrier for crash-safe deactivation-only replay, retains the stable root-only lock inode and state directory, and clears every other rearm state entry while still holding that lock.
- `vm-ha-rearm` is role-neutral and submits an explicit retry request for whichever exact member is currently the non-owner; it never starts Compute directly. Planned failover and failback share one preparation path that requests rearm for a stopped target, observes a starting target, waits for fresh `standby_ready` when running, then reproves ownership and readiness immediately before submitting the unchanged role-bound transfer request.
- Rearm is not a general setup reconciler. Missing SSH trust, stale or mismatched deployed generation, local route-hygiene drift on an already-running member, inexact cloud route authority, allocation drift, firewall drift, or forwarding drift fails outside its start-only authority and must be reconciled through the owning setup or apply workflow.
- Planned preparation uses one bounded deadline across Compute startup, pinned SSH, and repeated same-target standby-status reads. Valid not-yet-ready evidence may converge; malformed, mixed-identity, generation, digest, owner, or alias evidence fails immediately. The final request follows a fresh cloud reread and target-readiness reread.
- Fresh `standby_ready` evidence binds the current boot guard, exact generation and required digests, passive data plane, running non-owner and alias-free state, route/XFRM readiness, and absence of apply locks or pending effects. Mixed-version, stale, malformed, or identity-drifting evidence fails closed.
- Heartbeat v2 preserves the existing health and `promotion_ready` semantics while adding the authenticated mTLS epoch. `promotion_ready` still represents either an exact active owner with active data plane or an exact alias-free non-owner with passive data plane, both with current service and route readiness; automatic promotion retains its independent readiness and fencing gates.
- Operator status integrates `redundancy_ready`, standby readiness reasons, rearm phase, inhibition or failure reason, and preparation, cutover, and redundancy-restoration durations into one concise VM-HA section without changing existing status meanings or revealing cloud resource identities.
- Automatic failover remains configured active to configured passive and retains heartbeat failure, bounded local repair, suspicion expiry, parity/readiness, and fencing admission. Planned failback remains the only supported ownership return to the configured active.

#### Verification

- Run unit and composed tests for trigger-role confusion, pre-effect cancellation, post-effect stickiness, all transfer crash points, promotion-receipt ordering, both ownership directions, direct/automatic rearm concurrency, one-writer serialization, idempotency, accepted-operation replay, owner drift, transitional/error/flapping state, explicit retry, inhibition, corrupt state, apply/removal races, service isolation, mixed-version fail-closed behavior, checkpoint v1-v4 reads, packaging, and rollback with the rearm unit stopped. Follow with Ruff, mypy, full unit/integration, CLI/help, systemd, wheel, and changed-scope alignment gates.
- Live acceptance remains a separately authorized non-production workflow with at least three clean trials each for planned failover plus rearm, warm failback plus rearm, automatic failover plus rearm, repair success without transfer, and repair exhaustion followed by transfer. Probe bidirectionally at 5 Hz; recovery is five consecutive successes in the slower direction and loss is the exact missing sequence count. Report preparation, detection/repair, common cutover, total recovery, and redundancy-restoration separately. Under the same fixture, planned failover and warm failback median common-cutover time and directional loss, and automatic post-admission cutover, must remain within 20 percent; rearm must cause zero packet loss and no forwarding, route, or allocation mutation.

### TI-REQ-012: Isolate and live-validate GCP Classic static VM HA

- Status: satisfied
- Requirement: Support and independently validate a static-only two-node VM-HA deployment against an isolated GCP Classic VPN fixture without sharing gateway members, cluster identity, peer resources, routes, or configuration with the BGP deployment.
- Constraints: Preserve existing non-HA static behavior, BGP VM-HA warm-tunnel behavior, supported mixed-connection configuration, public commands, and record formats. The static-only VM-HA standby is Compute-warm but tunnel-cold because GCP considers a Classic tunnel usable whenever its IKE SA is established. Only the exact forwarding owner may keep the Classic IKE SA established. Every static promotion must retain former-Compute-`Stopped`, shared-allocation transfer and reread, route, and forwarding gates; candidate tunnel activation occurs only after exact ownership confirmation and while forwarding remains fenced. Do not place GCP credentials or a GCP route writer on gateway VMs. Keep PSKs and cloud identities out of committed files and public evidence.
- Non-goals: Hybrid BGP/static live validation, changing the validity of existing mixed-mode configs, GCP route mutation from a Nebius gateway, automatic failback, active-active or ECMP forwarding, treating fixture recovery as product proof, production validation, or deleting the resulting review fixture.

#### Acceptance criteria

- `nebius-gcp-ha-new-vpn.config.yaml` resolves only BGP connections. A separate `nebius-gcp-classic-vpn.config.yaml` resolves only static connections and uses a distinct Nebius gateway name, VM-HA cluster and members, subnet/allocation identities, public addresses, and GCP resource names.
- A dedicated Classic helper plans, previews, applies, and reports two one-to-one GCP Classic gateway/tunnel paths plus explicit static routes without a Cloud Router, BGP peer, or HA VPN gateway. It is idempotent, rejects incompatible existing resource shapes, keeps PSKs out of argv/output/disk, and never deletes the review fixture.
- A static-only passive member keeps forwarding disabled, preserves generation and passive materialization evidence, and has no established IKE SA or XFRM path. That tunnel-cold state is promotable only for the static-only contract and is reported distinctly from BGP warm-tunnel readiness.
- Static promotion preserves the canonical stop, detach, attach, and ownership-confirmation chain, then performs one checkpointed candidate-tunnel preparation effect while forwarding is disabled. Route reconciliation requires fresh established-IKE, XFRM, and static-prefix readiness; forwarding still occurs only after the exact current route receipt.
- Automatic rearm starts or adopts the exact non-owner Compute without re-establishing its Classic tunnel. Planned failover, planned failback, and automatic failover therefore leave exactly one established Classic tunnel aligned with the current forwarding owner.
- Clean live trials independently prove initial steady state, planned failover plus rearm, planned failback plus rearm, and automatic failover plus rearm. Each trial verifies former owner `Stopped` before transfer, exact allocation ownership before tunnel preparation, current-owner-only IKE, route receipt before forwarding, GCP selected next hop, unchanged BGP fixture state, and bidirectional workload recovery.
- Any out-of-band GCP route/tunnel repair or Nebius ownership intervention marks that trial intervened. Recovery is performed separately and the affected criterion is replayed from a newly proven checkpoint.

#### Verification

- Run static-only schema/config goldens, controller crash and effect-order tests, tunnel-cold passive/rearm tests, strongSwan activation and fencing tests, fake-`gcloud` Classic helper tests, CLI/status/help, Ruff, mypy, full unit/integration, packaging, security, and changed-scope alignment gates. Follow with the separately authorized clean non-production trials and independent cloud, host, route, tunnel, and traffic postconditions.

## Task Implementer Open Questions

- No architecture-blocking questions remain. Current official API metadata defines Compute `resource_version` as a positive monotonic revision for instance specification changes, which is the ownership-revision source after an exact attachment re-read. Current IAM role names, SDK service boundaries, and the authorized non-production allocation-transfer behavior have been verified.

## Task Implementer Requirements Change Log

- 2026-08-18: Reconciled TI-REQ-009 and TI-REQ-011 with the approved
  resource-scoped `failover vm` and `failback vm` command paths while retaining
  the unchanged role-bound request schemas and complete ownership fencing.

- 2026-08-17: Fulfilled TI-REQ-012 after isolated non-production steady-state,
  planned failover/rearm, planned failback/rearm, and automatic failover/rearm
  trials proved stopped-former fencing, exact candidate ownership before
  tunnel preparation, owner-only Classic IKE, route-before-forwarding order,
  retained GCP graph completeness, workload request/reply traffic, and an
  unchanged healthy BGP-only fixture.

- 2026-08-17: Added TI-REQ-012 for completely isolated BGP and static live
  fixtures. Static-only VM HA uses a Compute-warm, tunnel-cold standby because
  GCP Classic route eligibility follows IKE establishment rather than peer
  forwarding; candidate tunnel preparation is fenced between ownership
  confirmation and route reconciliation, with no GCP credentials on gateways.

- 2026-08-17: Reconciled TI-REQ-006 and TI-REQ-011 after final removal-safety
  review: both members must acknowledge one exact inhibition, both mutation
  services must be stopped everywhere before any deactivation, and a durable
  barrier checkpoint resumes partial removal without contacting a removed
  agent. TI-REQ-011 is implemented offline; live symmetry remains separate.
- 2026-08-17: Marked TI-REQ-011 implemented after focused and complete offline
  validation covered request-free same-owner transfers, exact accepted-operation
  finalization, one-shot retries, stable-inode writer exclusion, bounded repeated
  readiness, and role-neutral heartbeat readiness. Live symmetry trials remain
  separately authorized and are not claimed by the offline result.
- 2026-08-17: Refined TI-REQ-011 after alignment found unsafe same-owner
  request admission, stale accepted-start journals, replayable explicit retries,
  an apply/removal race, single-sample planned readiness, and owner-side
  redundancy that could never become ready for a healthy passive heartbeat.
- 2026-08-17: Superseded configured-passive-only TI-REQ-010 with TI-REQ-011:
  typed sticky transfer intent, terminal promotion commitment, role-neutral
  automatic rearm through an independent sole-start-writer service, shared
  planned preparation, fresh standby readiness, and additive redundancy
  status while preserving public commands and record versions.
- 2026-08-17: Added TI-REQ-009 and TI-REQ-010 for the live-proven, role-bound
  `vm-ha-failover` planned transfer and the separate non-promoting
  `vm-ha-rearm` standby recovery path, preserving every existing fencing,
  allocation, route, forwarding, compatibility, and default-disabled gate.
- 2026-08-17: Added TI-REQ-008 for one owner-bound five-second local repair
  attempt on a fresh unhealthy heartbeat, with prefix-aware fault
  classification, absolute forwarding-fence deadline, sole-writer behavior,
  heartbeat-v1 mixed-version conservatism, and unchanged authoritative
  Compute-stop promotion fencing. Reconciled the implemented boundary to use
  the existing systemd stop guard without a short watchdog.
- 2026-08-17: Added the fail-closed manual-failback request-target recovery
  contract after live static failover proved the controller path but left the
  configured-active request target stopped.
- 2026-08-17: Added the exact digest-bound recovery contract for an interrupted
  activation that was externally returned to the configured-active owner;
  unchanged activation retries remain non-provisioning and all identity,
  alias, revision, pending-effect, and host-only-effect checks fail closed.
- 2026-08-16: Refined TI-REQ-007 live evidence to require real workload-VM
  bidirectional ping-loss and sustained-recovery measurements plus structured,
  secret-free timing for every product-owned failover effect.
- 2026-08-16: Added the approved failed-`PROVISIONING` passive replacement
  contract: one digest-bound, append-only recovery lane may replace only the
  exact failed passive Compute and task-created boot disk while retaining the
  serving active, allocations, alias ownership, routes, generation, and
  original migration approval.
- 2026-08-16: Reconciled TI-REQ-006 and TI-REQ-007 with the accepted live IAM
  boundary: one dedicated runtime service account and custom group, exactly one
  project-scoped `editor` permit, exactly one separately enrolled authorized
  key, current generated SDK resource APIs, supported CLI impersonation, and
  fail-closed `--sa` behavior with no ambient-credential fallback.
- 2026-08-16: Added TI-REQ-007 for the authorized non-production GCP and
  Nebius live trial: preserve the legacy helper mode, add the four-tunnel
  multi-VM fixture, expose authoritative HA status, keep Nebius routing
  single-owner, secure live credentials, and prove clean failover/failback
  independently from fixture setup and recovery.
- 2026-08-16: Closed the post-implementation VM-HA safety review with v4
  path-level lifecycle guards and accepted-operation recovery, checkpoint-v2
  transfer continuity with conservative v1 reproof, typed status convergence,
  exact final-activation persistence recovery, bounded HA-only SDK operations,
  exact Compute-create footprint validation, strict checkpoint parsing, and
  canonical CI type/build gates while preserving default-disabled ordinary
  behavior and v2/v3 read compatibility.
- 2026-08-15: Hardened the migration contract with a single CAS-protected v3
  transaction, complete current-state approval binding, domain-separated
  recovery, fill-once effect identities, timeout-safe route reconciliation,
  strict v2 read compatibility, and terminal passive proof before `ACTIVE`.
- 2026-08-15: Reconciled TI-REQ-001 through TI-REQ-006 for customer-safe
  ordinary-to-HA migration: retain the existing gateway and immutable primary
  addresses, use a movable secondary alias, require digest-bound approval,
  lock both members through activation, verify owner-gated route completion and
  compensate failed route replacement, and remove implicit HA discovery from
  the ordinary path.
- 2026-08-14: Reconciled TI-REQ-006 after retained compatibility review. HA
  removal now selects the requested credential before no-sidecar discovery,
  adopts only coherent exact-pinned two-member runtime and exact allocation
  evidence, binds lifecycle status into whole-record integrity, and persists a
  verified removed tombstone so repeated ordinary apply remains idempotent.
- 2026-08-14: Strengthened TI-REQ-006 so HA removal independently discovers,
  authenticates, deactivates, and verifies both former members before any
  ordinary mutation, while abort and confirmation paths leave the live cluster
  untouched and retired members retain no product mutation service.
- 2026-08-14: Clarified TI-REQ-005 and TI-REQ-006 after integration review: clean HA members may render and validate node-local configuration while the data plane remains blocked, and every SSH operator/staging path requires one prevalidated pinned trust source before any cloud mutation. Added automatic CI selection for the composed bootstrap and trust regressions.
- 2026-08-13: Reconciled TI-REQ-001 through TI-REQ-006 after implementation: the explicit two-node path now has secret-free authoritative bindings, immutable credential bundles, strict stopped-owner fencing, exact target-aware route reconciliation, a production-composed current-boot guard/controller, guarded recovery and failback, and preserved omitted/disabled behavior. Live readiness remains a separately authorized non-production gate.
- 2026-08-12: Reconciled TI-REQ-001 through TI-REQ-006 with the proven post-provision runtime-binding, authoritative ownership-revision, exact route-receipt, guard-closure, fail-closed deactivation, credential-reference, IAM-allowlist, and activation-verification requirements.
- 2026-08-11: Added TI-REQ-001 through TI-REQ-006 for additive two-node VM-level active/passive HA.

## Core Requirements Change Log

- 2026-08-21: Fulfilled the periodic passive routing-hygiene extension of
  REQ-011 offline with one role-aware timer owner, lock-held current-authority
  rechecks, narrow passive mutation bounds, recurring cleanup, exact route-only
  health detection without priority/table-prefix false positives, and
  fail-closed readiness/status with owning-workflow remediation. Live
  deployment and recurrence observation remain separately authorized
  acceptance work.

- 2026-08-21: Reconciled REQ-001 after migrating SCM tag matching to the
  supported nested configuration, making runtime lookup reuse the canonical
  project configuration without version-file writes, explicitly preserving
  build-time source version-file generation, and completing warning-strict
  runtime/build plus full `make all` validation.

- 2026-08-20: Added REQ-012 for a complete topology/routing-mode CLI applicability matrix, controller-owned VM-HA route handling, pre-effect installed-agent capability checks, canonical static-prefix resolution, and strict route-operation failure propagation.

- 2026-08-20: Added REQ-011 for explicit per-peer BGP export policy, owner-aware Adj-RIB-Out readiness, transactionally complete passive materialization, tri-state advertisement evidence, and read-only `list-routes-local` behavior without a configuration or persisted-format change.
- 2026-08-20: Fulfilled the REQ-007 Role correction offline after the old projection failed five ownership-label regressions and the authoritative `active`/`standby`/`unknown` projection passed focused, full-unit, integration, static, documentation, security, and alignment gates.
- 2026-08-20: Reopened REQ-007 to make `Role` an unambiguous projection of authoritative current ownership: `active` owner, `standby` non-owner, and `unknown` without proven ownership; configured preference remains internal.
- 2026-08-20: Reopened REQ-007 to replace the verbose VM-HA summary/member presentation with one conservative four-column table while preserving the authoritative classifier and read-only evidence boundaries.
- 2026-08-20: Added REQ-010 for removal of the redundant primary-table Traffic State column and complete folded tunnel-name rendering without weakening Traffic Override detection.
- 2026-08-20: Fulfilled the REQ-007 presentation revision and REQ-010 offline after exact table-shape, semantic color, unavailable-member, long-name, row-arity, full static, unit, integration, documentation, security, alignment, and diff-integrity checks passed without live execution.
- 2026-08-20: Fulfilled REQ-009 offline after network selection progress was separated from authoritative VM-HA rereads, existing-instance wording became recreate-aware, and focused plus complete static and unit validation passed without a live apply.
- 2026-08-19: Added REQ-008 for clean-slate VM-local self-signed mTLS,
  direct peer-leaf pins enrolled over exact SSH, automatic apply/replacement,
  explicit two-member rotation, epoch-bound heartbeat v2, crash-safe overlap,
  and no HA compatibility path because VM-HA has no users.
- 2026-08-19: Fulfilled REQ-008 offline after the managed identity store,
  direct-pinned transport, heartbeat v2, apply bootstrap/replacement,
  digest-approved rotation, status projection, full unit/integration suites,
  static checks, and package tests passed without live execution.

- 2026-08-19: Fulfilled REQ-007 offline after the canonical status read,
  authoritative cloud/member classification, sanitized two-member rendering,
  no-alias parser removal, and complete unit and isolated integration suites
  passed without live execution.
- 2026-08-19: Added REQ-007 for hard removal of the unpublished
  `vm-ha-recover` path and one authoritative, concise, identity-safe VM-HA
  section inside ordinary `status`; reconciled REQ-005 to 17 executable
  operations plus two command groups while preserving historical evidence.

- 2026-08-18: Fulfilled REQ-006 offline after the resource-scoped command tree,
  nested handler routing, zero-effect parser rejection, migration guidance, and
  complete unit and isolated integration suites passed without live execution.
- 2026-08-18: Added REQ-006 for the breaking no-alias migration from four flat
  failover/failback commands to resource-scoped VM and tunnel subcommands, with
  parse-time rejection, recursive help ownership, and unchanged leaf behavior.
- 2026-08-18: Reconciled REQ-005 so canonical examples cover command groups and
  executable leaves while preserving the 18 operations across the REQ-006
  route migration.
- 2026-08-18: Reconciled REQ-005 after all 18 public command help pages and the top-level quick start gained tested examples without changing execution behavior or safety gates.
- 2026-08-18: Added REQ-005 so top-level help and every public command help page provide accurate, practical, secret-safe examples without changing CLI behavior or safety gates.
- 2026-08-18: Reconciled REQ-004 after implementation and offline verification of credential preflight, raw-placeholder preservation, allowlisted two-member conversion, passive-only allocation reuse, race-safe conditional publication, and the existing approved apply handoff.
- 2026-08-18: Added REQ-004 for a safe two-phase `configure-vm-ha` wizard that preserves the ordinary source, keeps VM-HA explicit, reserves only the passive public IP when separately confirmed, and publishes only a complete owner-only candidate.
- 2026-08-18: Added REQ-003 for a TTY-default guided configuration wizard with an exact noninteractive template compatibility path, explicit secret handling, atomic output, optional confirmed network preparation, and preserved standalone `prep-network` behavior.
- 2026-08-16: Added REQ-002 for measurement-driven pytest feedback optimization that preserves test selection, outcomes, isolation, diagnostics, and complete correctness gates.
- 2026-08-16: Added REQ-001 to make conservative Python-project hardening explicitly preserve the existing supported package, CLI, configuration, persistence, build, release, and Python-version contracts.
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD013 MD024 -->
