<!-- markdownlint-disable MD001 MD013 MD024 MD041 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
<!-- REQUIREMENT: REQ-001 status=active priority=P1 type=feature -->
### REQ-001: Preserve the supported Python project contract during hardening

#### User Story

Maintain the existing Python package, CLI, test, build, and release contracts while applying conservative project hardening for current users. Constraints: Preserve supported public import paths, console scripts, CLI syntax and exit behavior, Python 3.10 through 3.12 support, configuration schemas and defaults, persisted formats, packaged systemd assets, SCM-derived versions, release tags, and upgrade paths, except for the explicitly approved VM-HA clean break in REQ-008 and the explicitly approved region-terminology and pre-adoption VM-HA command cleanup in REQ-015. Keep one canonical implementation rather than adding compatibility shims. Non-goals: Replacing the established project scaffold, changing runtime cloud or networking behavior, raising the Python floor, adopting a new framework or build backend, or performing broad dependency upgrades.

#### Acceptance Criteria

- PEP 621 metadata, the `src/nebius_vpngw` layout, Typer entrypoints, Pydantic configuration, setuptools/setuptools-scm packaging, split unit/integration tests, Ruff, mypy, pytest, coverage, and current CI lanes remain the canonical project structure.
- Source-checkout version discovery has a finite direct Git-probe timeout and falls back through the existing metadata/generated/unknown sequence without changing successful version results.
- Regression tests bind the supported Python range, console-script mappings, SCM tag/version-file contract, package discovery, and Makefile verification/build targets.
- SCM tag matching uses the supported nested configuration model, and source-checkout runtime version discovery emits no dependency deprecation warnings while preserving the established tag format and resolved versions.
- Unit tests remain isolated from real networks and cloud APIs; integration tests remain explicitly marked and separated from the fast unit lane.
- Standard local coverage, tox, and nox artifacts are ignored without hiding project source or public examples.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run the focused runtime-version and Python-project contract tests, a warning-strict source-checkout version probe, repository-native Ruff and mypy checks, full unit and integration suites, CLI help/version smoke tests, wheel-build regressions, and the canonical `make all` workflow.

#### Test Method

- Run the focused runtime-version and Python-project contract tests, a warning-strict source-checkout version probe, repository-native Ruff and mypy checks, full unit and integration suites, CLI help/version smoke tests, wheel-build regressions, and the canonical `make all` workflow.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=satisfied priority=P1 type=feature -->
### REQ-002: Reduce pytest feedback time without weakening correctness

#### User Story

Improve the established pytest unit-test feedback loop only where repeatable measurements identify a cumulative bottleneck and a like-for-like optimization is safe. Constraints: Preserve collected tests, outcome counts, assertions, failure diagnostics, unit-test network isolation, registered markers, integration classification, serial debugging, coverage, and the complete correctness gate. Keep the existing Python 3.10 through 3.12 support and avoid new dependencies unless separately justified and approved. Non-goals: Hiding failures with skips, xfails, or reruns; deleting or combining tests for timing; relabeling integration behavior as unit behavior; accessing live cloud or shared services; or treating a reduced selection as a full-suite speedup.

#### Acceptance Criteria

- Baseline and candidate measurements use the same frozen non-candidate source state, with the exact candidate patch as the only intentional difference, plus the same interpreter, pytest/plugin configuration, cache policy, selection, instrumentation, collected count, and outcome counts. Record identities for both compared test-file states.
- Startup/collection and setup/call/teardown costs are distinguished, and changes target ranked cumulative cost rather than only the single slowest test.
- Any accepted optimization has at least five comparable before/after samples with non-overlapping or clearly material timing evidence; inconclusive candidates are reverted or left unadopted.
- The canonical local serial unit command remains available, CI parallelism remains bounded by validated isolation, and full unit/integration correctness checks remain authoritative.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Record median and range for comparable unit-suite samples, run duration diagnostics, then rerun the complete unit and isolated integration lanes plus Ruff, mypy, and diff-integrity checks after any change.

#### Test Method

- Record median and range for comparable unit-suite samples, run duration diagnostics, then rerun the complete unit and isolated integration lanes plus Ruff, mypy, and diff-integrity checks after any change.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=active priority=P1 type=feature -->
### REQ-003: Guide configuration creation without breaking automation

#### User Story

Make interactive `nebius-vpngw create-config CONFIG_FILE` use a guided wizard that produces an ordered, schema-valid VPN gateway configuration and explains each required input, while preserving the current deterministic template generator for existing automation. Constraints: Preserve the positional output path, `--force`, non-`.config.yaml` warning, exact-template no-op, noninteractive output and exit behavior, configuration schema v1, public `prep-network` command, and explicit default-disabled VM-HA semantics. A non-TTY invocation or explicit `--no-interactive` uses the current template path; `--interactive` forces the wizard. The wizard may collect a literal PSK only through hidden input and a mode-`0600` output file; it must never echo or render that value, write an incomplete candidate, infer VM-HA, or perform cloud mutation before a separate explicit confirmation. Non-goals: Removing or deprecating `prep-network`; introducing a second configuration schema, resumable draft format, live provider-discovery dependency, new prompt framework, or implicit network/IAM mutation; changing apply, deployment, tunnel-HA, or VM-HA runtime behavior.

#### Acceptance Criteria

- The wizard guides project context, gateway/network/routing, repeatable connections and tunnels, and an optional advanced phase; invalid typed or cross-field values are explained and reprompted, and back/quit controls never write a partial candidate.
- The completed in-memory candidate passes the existing Pydantic schema before an atomic file replacement. A hidden PSK answer matching the uppercase environment-name grammar is stored as `${NAME}`; any other value of at least eight characters is stored literally, while blank accepts the generated environment placeholder for later completion. Validation and failure output omit Pydantic input values and all PSK bytes, and interrupted or rejected overwrite attempts preserve the original file byte-for-byte.
- Fresh wizard output uses provider-neutral `site-N`, `generic`, tunnel, and PSK-variable defaults. Routing mode is selected before routing-specific fields; the gateway local ASN is asked once on the first BGP connection and is not prompted for a static-only candidate.
- After a valid file is written, network preparation is offered with default No and an explicit description of authentication, subnet, route-table, public-allocation, and YAML effects. Both entrypoints use one internal preparation path. Standalone `prep-network` supports TTY-default `--interactive/--no-interactive` allocation selection while noninteractive automation remains prompt-free.
- Network preparation is convergent and fail-closed: it reuses an exact-name explicit-CIDR subnet when CIDR is omitted, treats the configured prefix as creation-only, verifies or repairs one exact default-egress route without changing unrelated routes, selects or creates one distinct public allocation per VM/NIC, conditionally publishes the complete YAML matrix, and succeeds only after authoritative rereads. Observation failures, shared/conflicting route tables, foreign allocation bindings, or concurrent YAML changes exit nonzero without claiming completion.
- VM-HA questions appear only after explicit enablement, require the existing exact two-member contract, and are never selected from instance count, tunnel roles, or public IP shape.
- Static, BGP, and mixed-routing wizard transcripts, hybrid-PSK and non-leakage paths, noninteractive compatibility, cancellation, overwrite safety, allocation selection, partial/rerun network reconciliation, route conflicts, YAML publication races, and omitted/disabled VM-HA behavior have regression coverage.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused wizard, CLI, schema/config-loader, network-preparation, allocation, and VM-HA compatibility tests; CLI help smoke checks; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; and diff-integrity checks.

#### Test Method

- Run focused wizard, CLI, schema/config-loader, network-preparation, allocation, and VM-HA compatibility tests; CLI help smoke checks; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; and diff-integrity checks.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-003 -->

<!-- REQUIREMENT: REQ-004 status=superseded priority=P1 type=feature -->
### REQ-004: Guide an ordinary gateway into explicit VM-HA

#### User Story

Add `nebius-vpngw configure-vm-ha --local-config-file SOURCE [--output DEST] [--force]` as a guided, two-phase conversion from one supported ordinary single-VM configuration to a new, schema-valid explicit two-member VM-HA candidate. Constraints: Preserve `SOURCE` byte-for-byte and preserve the supported `create-config`, `validate-config`, `prep-network`, and `apply` contracts. Admit only schema-v1 configurations with `instance_count: 1`, VM-HA omitted or explicitly disabled, and every tunnel owned by instance zero. VM-HA remains explicit and default-disabled. Treat raw YAML as the persistence authority so environment references and PSK references are never expanded into output. Never overwrite in place, follow a source or destination symlink, write through a hard link to the source, expose secret values, or publish an incomplete candidate. Any optional cloud operation requires a separate default-No confirmation and may prepare only the deterministic passive public allocation needed for the peer handoff. Non-goals: Mutating the peer provider, deploying or activating VM-HA, discovering or adopting an existing multi-VM topology, inferring VM-HA from instance count, public IPs, or tunnel roles, replacing `prep-network`, saving a schema-invalid draft, automating post-activation removal, or changing apply's approval, recovery, fencing, and lifecycle authority.

#### Acceptance Criteria

- The wizard preserves every existing member-zero and unrelated configuration value semantically, changes `instance_count` from one to two, adds the exact explicit active/passive VM-HA block, appends one instance-one counterpart for every existing instance-zero tunnel, appends only the passive external-IP row, and increases `max_tunnels` only when required. A mechanical structural allowlist rejects any other candidate mutation.
- The first phase derives deterministic member-one names, PSK environment references, and unique APIPA networks. It never asks for a runtime credential path and never creates an operator credential, directory, IAM resource, or key. Its summary displays the future managed credential location with a literal `~`; only a separately approved apply resolves the operator home and provisions runtime credentials. The user may then supply a passive public IP or explicitly request a passive-only Nebius reservation. The wizard prints a secret-free peer handoff and exits successfully without a candidate when the peer is not ready; a rerun reuses the exact deterministic allocation.
- The second phase requires the peer-provided remote public and inner tunnel endpoints, validates the complete in-memory candidate through the existing schema, presents a bounded redacted summary, and publishes only after explicit confirmation. The source remains unchanged on cancellation, EOF, interruption, validation failure, cloud failure, or publication failure.
- Candidate publication rejects canonical same-path, symlink, and same-inode source/destination relationships; detects concurrent source or destination changes; never clobbers a racing writer; and enforces mode `0600` regardless of process umask. A new destination is published atomically with a no-clobber link. Replacing an expected destination first quarantines that exact file in a private sibling directory, then publishes without clobbering; interruption can therefore leave explicit manual recovery state rather than claiming a single atomic replacement. Exact already-published output is an idempotent no-op only when its file safety invariants still hold.
- Passive allocation preparation selects only instance index one, preserves and does not query or validate instance-zero allocation state, uses the deterministic `<gateway>-1-eth0-ip` identity, rejects attached or foreign allocations, and never creates VMs, shared private aliases, managed routes, lifecycle state, host configuration, or deployment approval.
- A published candidate passes configuration loading, peer merging, and the existing `apply --dry-run` migration preview, which retains the ordinary active member and leaves all cloud and host mutation behind apply's existing explicit approval boundary.
- Static, BGP, multi-connection, multiple-tunnel, placeholder, redaction, cancellation, file-safety, passive-allocation retry, generated-candidate dry-run, CLI help, and unchanged ordinary-command behavior have regression coverage without live cloud access.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused conversion-wizard, CLI, schema/config-loader, selected-index allocation, migration dry-run, and VM-HA compatibility tests; CLI integration smoke checks; Ruff; mypy; the complete unit and isolated integration suites; Markdown lint; security/redaction review; and diff-integrity checks.
- Offline implementation evidence on 2026-08-18: Ruff and mypy passed, all 1,015 unit tests and 46 integration tests passed, focused generated-candidate tests exercised real configuration loading, peer merging, and the existing `apply --dry-run` migration boundary, and changed-scope Markdown and diff-integrity checks passed. No live Nebius authentication or cloud mutation was used.

#### Test Method

- Run focused conversion-wizard, CLI, schema/config-loader, selected-index allocation, migration dry-run, and VM-HA compatibility tests; CLI integration smoke checks; Ruff; mypy; the complete unit and isolated integration suites; Markdown lint; security/redaction review; and diff-integrity checks.
- Offline implementation evidence on 2026-08-18: Ruff and mypy passed, all 1,015 unit tests and 46 integration tests passed, focused generated-candidate tests exercised real configuration loading, peer merging, and the existing `apply --dry-run` migration boundary, and changed-scope Markdown and diff-integrity checks passed. No live Nebius authentication or cloud mutation was used.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-004 -->

<!-- REQUIREMENT: REQ-005 status=active priority=P1 type=feature -->
### REQ-005: Make every public command self-explanatory

#### User Story

Make the top-level `nebius-vpngw --help` output and every public command-group and executable-command help page provide accurate, practical invocation examples for the supported CLI workflows. Every operational command that selects a local configuration through `--local-config-file` must also accept the short `-c` spelling. Constraints: Preserve the 16 executable operations and two non-executable command groups, workflow-oriented command order, existing long option spellings, arguments, prompts, approvals, exit behavior, and compatibility contracts except for the additive operational `-c` alias, the explicitly approved failover/failback route migration in REQ-006, removal of the unpublished `vm-ha-recover` command in REQ-007, the clean-slate managed-mTLS interface in REQ-008, and command/region/rotation cleanup in REQ-015. Examples must use supported syntax, avoid secrets and environment-specific identifiers, and must not suggest bypassing confirmation, VM-HA migration approval, fencing, or other safety gates. Non-goals: Adding a root configuration option; adding `-c` or `--local-config-file` to positional `create-config CONFIG_FILE` or `validate-config CONFIG_FILE`; executing example commands; changing cloud, host, configuration, or persistence behavior; documenting the separate agent entrypoint; or replacing the README with generated CLI reference output.

#### Acceptance Criteria

- Top-level help contains a short quick-start sequence for configuration creation, validation, and a non-mutating apply preview.
- Top-level help identifies `--local-config-file` and `-c` as equivalent command-local spellings for operational commands, while `create-config` and `validate-config` retain their required positional `CONFIG_FILE` arguments. No root-level configuration option is registered.
- Every visible public command-group and executable-command help page contains an `Examples` section with at least one path-specific invocation whose syntax matches the registered arguments and options.
- The example contract is owned centrally and regression coverage compares it with the rendered public command tree, so a new visible command cannot be added without an example and an example cannot silently reference the wrong command.
- Mutating workflow examples retain the command's ordinary interactive confirmation or explicit approval boundary; no example contains credential material, customer data, live resource identifiers, or hidden bypass flags.
- README discovery guidance and the Unreleased changelog describe the aligned help surface without changing existing command semantics.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Render top-level help and every visible command help page through Typer's test runner; assert registry parity, successful rendering, command-specific example text, exact option aliases, equivalent long/short parsing, and rejection of root or positional-command `-c`. Run focused CLI tests, Ruff, mypy, the full unit and isolated integration suites, Markdown lint, security review, and diff-integrity checks.
- Offline implementation evidence on 2026-08-18: the root help and all 18 visible command help pages rendered with their canonical examples; Ruff and mypy passed; all 1,052 unit tests and 46 integration tests passed; README and changelog Markdown lint, canonical spec validation, changed-scope security review, and diff-integrity checks passed. No example command was executed against Nebius or a gateway VM.

#### Test Method

- Render top-level help and every visible command help page through Typer's test runner; assert registry parity, successful rendering, command-specific example text, exact option aliases, equivalent long/short parsing, and rejection of root or positional-command `-c`. Run focused CLI tests, Ruff, mypy, the full unit and isolated integration suites, Markdown lint, security review, and diff-integrity checks.
- Offline implementation evidence on 2026-08-18: the root help and all 18 visible command help pages rendered with their canonical examples; Ruff and mypy passed; all 1,052 unit tests and 46 integration tests passed; README and changelog Markdown lint, canonical spec validation, changed-scope security review, and diff-integrity checks passed. No example command was executed against Nebius or a gateway VM.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-005 -->

<!-- REQUIREMENT: REQ-006 status=active priority=P1 type=feature -->
### REQ-006: Organize failover and failback by resource

#### User Story

Replace the four flat failover/failback entry points with the canonical resource-scoped commands `nebius-vpngw failover vm`, `nebius-vpngw failback vm`, `nebius-vpngw failover tunnel [TUNNEL_NAME]`, and `nebius-vpngw failback tunnel [TUNNEL_NAME]`. Constraints: Remove `vm-ha-failover`, `vm-ha-failback`, flat `failover`, and flat `failback` without aliases or compatibility shims. Preserve every leaf argument, option, callback behavior, VM-HA request schema, ownership and fencing gate, tunnel selection rule, prompt, side effect, and exit behavior. Bare `failover` and `failback` groups must render help and exit nonzero without loading configuration, authenticating, opening SSH, querying cloud state, or contacting an agent. Removed or invalid paths must fail during parsing before any such effect. Non-goals: Renaming internal VM-HA intent or request types; changing automatic failover, rearm, status, tunnel HA, VM-HA configuration, cloud transfer, route, forwarding, readiness, or recovery semantics; adding aliases, deprecation wrappers, or a generic resource dispatcher.

#### Acceptance Criteria

- Root help exposes exactly one `failover` group and one `failback` group in their established workflow position; each group exposes `vm` before `tunnel`, and the old four paths are absent.
- The `vm` leaves invoke the unchanged planned VM-HA preparation and operator-request paths, including former-owner `Stopped`, candidate allocation ownership, route reconciliation, forwarding, generation, apply-lock, and readiness gates.
- The `tunnel` leaves retain the optional tunnel-name argument, `--local-config-file`/`-c`, automatic single-tunnel selection, multi-tunnel diagnostics, and existing tunnel failover/failback execution behavior. Runtime guidance names the new resource-scoped syntax.
- One path-aware example registry owns root, group, and leaf help ordering and examples. Recursive tests prove registry/tree parity, deterministic ordering, successful help rendering, nested callback routing, and parse-time zero-effect rejection of removed and incomplete paths.
- README and the Unreleased changelog provide the exact old-to-new migration mapping and do not imply any compatibility alias.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused recursive CLI-tree, help, routing, zero-effect rejection, tunnel, and VM-HA safety tests; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; security review; canonical-spec validation; and diff-integrity checks. Live cloud or gateway execution is not required because the approved change is limited to command routing and static guidance.
- Offline implementation evidence on 2026-08-18: recursive command-tree and parser tests proved the four nested leaves, deterministic `vm`-before-`tunnel` ordering, path-specific help, unchanged VM operator routing, request-free same-owner behavior, and effect-free rejection of bare and removed paths. Ruff and mypy passed, all 1,065 unit tests and 58 isolated integration tests passed, selected changed-document Markdown lint and diff-integrity checks passed, and changed-scope security review found no new trust, credential, network, or mutation boundary. No command was executed against Nebius or a gateway VM.

#### Test Method

- Run focused recursive CLI-tree, help, routing, zero-effect rejection, tunnel, and VM-HA safety tests; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; security review; canonical-spec validation; and diff-integrity checks. Live cloud or gateway execution is not required because the approved change is limited to command routing and static guidance.
- Offline implementation evidence on 2026-08-18: recursive command-tree and parser tests proved the four nested leaves, deterministic `vm`-before-`tunnel` ordering, path-specific help, unchanged VM operator routing, request-free same-owner behavior, and effect-free rejection of bare and removed paths. Ruff and mypy passed, all 1,065 unit tests and 58 isolated integration tests passed, selected changed-document Markdown lint and diff-integrity checks passed, and changed-scope security review found no new trust, credential, network, or mutation boundary. No command was executed against Nebius or a gateway VM.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-006 -->

<!-- REQUIREMENT: REQ-007 status=active priority=P1 type=feature -->
### REQ-007: Consolidate VM-HA status into the ordinary status command

#### User Story

Make `nebius-vpngw status` the only public status interface and render one concise, authoritative VM-HA section when explicit VM HA is configured. Constraints: Remove the unpublished `vm-ha-recover` command and its private duplicate agent flag without an alias, replacement command, deprecation shim, or focused-view flag. Preserve the private canonical `--vm-ha-status` agent read, public `vm-ha`, `failover vm`, and `failback vm`, ordinary non-HA status output, VM-HA fencing and mutation boundaries, and existing fatal setup/configuration/authentication exits. Unresolved environment references used only as tunnel PSKs are not setup errors for this read-only command; all non-secret placeholders and secret requirements of mutating commands remain strict. After successful command setup, HA health and member-observation failures remain informational. Non-goals: Adding `vm-ha-status`, `vm-ha-state`, `--vm-ha-only`, a machine-readable status schema, new cloud or gateway mutations, a metrics exporter, or changing any ownership-transfer, route, forwarding, rearm, configuration, or lifecycle record.

#### Acceptance Criteria

- Explicit VM HA renders exactly one table titled `VM-HA Status — <OVERALL>` with the columns `Gateway`, `Role`, `mTLS`, and `Ready`, plus exactly one row for each configured member; non-HA status performs no HA observation and renders no HA section.
- `Role` reports only the current operational relationship to authoritative cloud ownership: the exact owner is `active`, the other member is `standby`, and every member is `unknown` when no owner is proven. Configured active/passive preference is not rendered in this column and never overrides current ownership.
- Cloud and lifecycle evidence select the authoritative owner. Member records are validated as supporting evidence and cannot report healthy redundancy when they disagree with cloud ownership, aliases, identities, generations, required digests, locks, or forwarding authority. Every exact route target must expose the same non-empty managed-prefix set exactly once through the shared allocation; missing, duplicate, partial, or foreign-next-hop coverage blocks exact authority.
- Overall state uses conservative precedence: proven unsafe contradiction is `BLOCKED`; missing required evidence is `UNKNOWN`; an exact expected lifecycle, transfer, repair, rearm, or standby-policy operation is `TRANSITIONING`; an exact committed operator maintenance inhibition is `MAINTENANCE`; a safely serving owner without ready redundancy is `DEGRADED`; and only an exact ready owner/standby pair with enabled standby restoration is `HEALTHY`. A pending controller effect qualifies as expected only when its generated identity names a configured member and its encoded action kind is valid for the reported controller state.
- The aggregate title is green only for `HEALTHY` and yellow for `MAINTENANCE` or `TRANSITIONING`; `DEGRADED`, `BLOCKED`, and `UNKNOWN` are red. Per-member `mTLS` is green only for an uninhibited `healthy` state, and `Ready` is green only for `yes`; every other semantic value is red. Gateway and Role remain neutral, and literal values remain visible without color.
- `Ready=yes` requires exact authority, role-specific safe readiness, and an aggregate state of `HEALTHY` or `DEGRADED`. `BLOCKED` and `TRANSITIONING` render `no`; unavailable evidence renders `unknown`. Missing member IP, trust, SSH, JSON, or valid status produces one sanitized unavailable row rather than omitting the member.
- Every status SSH probe uses the configured management username and private key. Explicit VM HA additionally resolves one immutable exact-pin policy per member, so a missing pin affects only that member's sanitized semantic row and never falls back to trust-on-first-use or disabled host verification.
- Cloud route authority distinguishes an inexact target set, malformed or duplicate managed records, missing or inconsistent prefix coverage, and a managed next hop that does not equal the shared allocation. Authority is scoped by the complete product-managed label set for the current cluster: a well-formed foreign-cluster record is ignored, while partial, malformed, current-cluster, target, kind, or allocation-label drift fails closed and prevents a healthy aggregate projection.
- The HA renderer exposes the configured member names, current operational roles, closed mTLS states, and conservative readiness in the existing four-column table, followed by one concise identity-free `Redundancy`/`Identity`/`Auto-healing`/`Action` summary. `Auto-healing` reports the closed two-member standby-restoration policy as `enabled` or `disabled` and reports `transitioning`, `blocked`, or `unknown` when exact committed agreement is unavailable; it never coerces incomplete evidence to a committed value. Rearm evidence remains internal classification authority, and maintenance state is expressed through Redundancy, Auto-healing, and Action rather than a permanent `Rearm` row. When Auto-healing is disabled for committed maintenance, Action renders the exact shell-quoted `nebius-vpngw vm-ha --local-config-file <current-config> --standby-auto-healing enabled` command so the operator can re-enable restoration after maintenance. Only that disabled-maintenance Action may reproduce the local configuration path supplied to this status invocation; every other config-bearing action retains the `<file>` placeholder, and the summary never exposes configured role preference, cloud resource, allocation, node, generation, digest, revision, operation, epoch, fingerprint, raw agent/cloud payload, raw exception, or any other environment-specific command argument.
- `status` accepts a schema-valid local configuration whose tunnel PSKs remain exact unresolved environment references, never expands or renders those unused secret values, and still rejects unresolved project, topology, credential-path, or other operational placeholders required by its observation path. When PSKs are unresolved, each available member must report a self-consistent generation, both available members must agree exactly on generation and digests, and the locally derivable static-route and BGP-policy digests must still match; status must not synthesize an expected full configuration digest from placeholder text.
- `vm-ha-recover` and private `--vm-ha-recover` fail at argument parsing before configuration, authentication, SSH, cloud reads, or mutation. No public replacement or focused-view option is registered.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

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

#### Test Method

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

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-007 -->

<!-- REQUIREMENT: REQ-008 status=active priority=P1 type=feature -->
### REQ-008: Manage VM-HA mutual TLS without operator PKI

#### User Story

Make VM-HA mutual TLS completely product-managed: each member generates and retains an independent self-signed identity, exact peer leaf certificates are exchanged only through pre-established exact-pinned SSH, initial enrollment and member replacement are automatic under `apply`, and both identities rotate only through the explicit `vm-ha --rotate-mtls` operation. Constraints: VM-HA has no compatibility obligation because no user depends on its existing configuration, credential bundle, persisted mTLS, or peer-wire formats. Replace those HA-only contracts without aliases, legacy readers, dual modes, or migration shims; a stale configuration receives only actionable conversion guidance. Preserve explicit default-disabled VM HA, all non-HA behavior, exact SSH host verification, former-owner fencing, cloud ownership, routes, forwarding, rearm authority, and secret-redaction boundaries. Do not use an external CA, CA private key, Vault, KMS, trust-on-first-use, certificate discovery from an untrusted channel, automatic renewal, or scheduled rotation. Non-goals: Repairing or enrolling SSH host trust during mTLS rotation; learning SSH identity from a gateway or unauthenticated scan; starting a stopped member; changing Compute, allocation, VPC route, firewall, or forwarding state during mTLS rotation; exporting VM private keys; supporting more than two members; preserving mixed mTLS protocol versions; or claiming a physically simultaneous two-machine key switch. REQ-013 assigns bounded operator-side SSH trust creation and repair only to `apply`.

#### Acceptance Criteria

- VM-HA member configuration contains only stable node identity, instance index, and role; it has no credential path, CA, certificate, private-key, or generic `credential_sources` field. Both wizards and examples use that single public shape. Apply derives one operator-side credential source and manifests/runtime bindings contain only the two protected node-local installed references; removed public credential fields fail schema validation without a compatibility reader.
- Each member generates an unencrypted PKCS#8 ECDSA P-256 key from the OS CSPRNG and a SHA-256 self-signed CA-false certificate with a random positive serial, fixed historical `notBefore`, `9999-12-31T23:59:59Z` `notAfter`, digital-signature usage, client/server EKUs, the exact node DNS SAN, and URI SAN `urn:nebius-vpngw:node:<node_id>`. Member SPKI fingerprints must differ.
- Root-owned node-local state uses immutable identity and peer-certificate objects plus atomic active and transaction records. Private keys are mode-restricted, no-follow, single-link files and never cross SSH, enter YAML/manifests/status/logs, or return from an agent command. Only validated public certificates, fingerprints, transaction receipts, and secret-free status may leave a VM.
- TLS retains `CERT_REQUIRED`, peer hostname/URI identity validation, and exact DER leaf-fingerprint validation. Every new transport connection selects one immutable managed snapshot, carries a protocol-v2 mTLS epoch bound to the actually presented certificate, and disables reusable old sessions so trust pruning cannot preserve an obsolete authenticated channel.
- Initial HA apply generates both identities, cross-installs direct peer pins, proves fresh bidirectional TLS, and only then activates HA. An unchanged healthy apply performs no mTLS mutation. Exact member replacement generates only the replacement identity after the former Compute is authoritatively stopped or absent and network-fenced, preserves the survivor identity, uses temporary old/new overlap trust, proves fresh replacement traffic, and prunes the former leaf.
- `nebius-vpngw vm-ha --rotate-mtls --local-config-file FILE [--dry-run] [--approve PLAN_DIGEST]` rotates both members and has no target flag. The option is an exclusive action mode: candidate `--output`, `--force`, `--standby-auto-healing`, explicit `--region`, and JSON output are rejected before conversion, authentication, SSH, cloud reads, or mutation. Dry-run performs no key, journal, lock, or inhibition write. Interactive execution confirms a secret-free plan; noninteractive execution requires a digest bound to current config, cluster, members, Compute identities, owner/allocation observation, fingerprints, epochs, and exact phases, and any drift invalidates it. Default output is concise human-readable text: state the expected passive-first availability behavior at startup, show a bounded plan summary and exact digest without raw preview/result JSON, animate active work on an interactive terminal, and leave one terminal success or failure row.
- Rotation requires ACTIVE lifecycle, exact cloud/member ownership, both members Running and reachable through exact-pinned SSH, one owner and one alias-free non-owner, and no competing writer. Before rendering an executable plan or requesting approval, it requires both installed agents to advertise the exact split rotation-inhibition/controller-quiescence capability through the fixed read-only capability document and requires the same capability in status persisted by each running controller process; missing, malformed, unavailable, or restart-skewed evidence fails before inhibition with actionable same-version `apply` guidance and no mixed-version fallback. Under the shared writer lock it durably inhibits the passive first, waits for the exact controller-processed operation with no pending controller or cloud/rearm effect and the former owner still Running, then applies and proves the same barrier on the owner before preparing either identity. Controller transfer effects, rearm, apply locking, and inhibition installation share the node-local writer lock and re-read apply/mTLS inhibition inside that critical section, so a controller decision made before inhibition cannot execute afterward. Apply and rotation inhibition are mutually exclusive. Rotation then prepares pending identities, expands trust to old/new, switches passive then owner, and commits only after independently reread active slots plus three consecutive fresh epoch-and-fingerprint-bound authenticated observations in both directions. Rotation inhibition is distinct from an apply lock: it blocks competing transfer and rearm effects but does not fence the healthy current owner, change forwarding or routes, withdraw BGP exports, or suspend tunnels. Independent cloud-ownership and safety failures retain authority to fence. Barrier drift before any identity prepare releases the exact inhibition and exits retriably; once prepare may have begun, failure retains inhibition for journal-based resume. After verification, rotation drains old connections, prunes obsolete public and private material, and releases inhibition.
- Rollback eligibility comes from independently reread served fingerprints and active slots, never the CLI's last acknowledgement. Before either new leaf is observed serving, rollback is permitted; afterward every retry rolls forward under overlap trust. The same operation and monotonic epoch resume idempotently after CLI, SSH, service, or host restart, including an inhibition-only interruption before prepare; status and the plan renderer classify that exact state as resumable rotation rather than an apply lock or transaction conflict.
- Broken current mTLS may be rebuilt through exact-pinned SSH when both exact members are Running and ownership is unambiguous. Missing SSH trust, a stopped member, a foreign or unfenced former member, corrupt cross-node identity, ambiguous ownership, or a competing apply/rearm/transfer blocks before mutation with a closed reason and safe action.
- Ordinary `status` remains read-only and reports each member's closed mTLS health state in the concise VM-HA table without epochs, fingerprints, transaction details, keys, internal paths, or cloud identities. Public `vm-ha` delegates standby restoration to the internal sole Compute-start workflow and gains mTLS authority only when the operator explicitly selects `--rotate-mtls`; a bare invocation never rotates identities.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run direct-leaf TLS/profile/fingerprint tests across supported Python/OpenSSL lanes; state and SSH fault injection at every durable effect and lost acknowledgement; initial apply, no-op apply, member replacement, broken-mTLS recovery, approval drift, concurrency, and private-key non-export tests; composed old/overlap/mixed/new/pruned runtime tests; controller tests proving rotation inhibition blocks transfer without changing the active/passive dataplanes while a real apply lock still fences; concise-output and interactive-spinner tests; CLI/help/schema/wizard/package tests; Ruff, mypy, full unit/integration, Markdown, security, and changed-scope alignment gates. Live mutation remains a separately approved non-production acceptance trial.
- Offline implementation evidence on 2026-08-19: Ruff and mypy passed, all
  1,094 unit tests and 63 isolated integration tests passed, and 14 focused
  build/release tests passed. Direct-leaf handshakes, certificate profiles,
  private-key non-export, state recovery, inhibition, apply bootstrap/no-op/
  replacement, digest drift, passive-first rotation, heartbeat-v2 binding,
  status projection, and stale-schema rejection were exercised locally on
  Python 3.12/OpenSSL. README and changelog Markdown lint passed. No live
  Nebius, SSH, service, cloud, route, or gateway mutation was performed.
- Additional offline implementation evidence on 2026-08-25: full Ruff and
  mypy across 52 source files passed, all 1,627 unit tests and 79 isolated
  integration tests passed, and the independent final risk review found no
  remaining blocking correctness or availability issue. Focused regressions
  exercise controller-dispatch/inhibition interleaving, exact quiescence,
  pre-prepare cleanup, inhibition-only resume/status, human output, and
  progress cleanup. README, changelog, and requirements Markdown lint and diff
  integrity passed; `docs/design.md` retains its pre-existing first-heading
  and line-length baseline. No live cloud, SSH, service, route, or gateway
  operation was performed.
- Offline command-consolidation evidence on 2026-08-29: the public tree and
  built-release integration path expose rotation only as
  `vm-ha --rotate-mtls`; the old standalone command fails during parsing, and
  exclusive-mode regressions prove rejected facade options cannot reach the
  rotation handler while bare `vm-ha` cannot dispatch it. Ruff, mypy, all
  1,925 unit tests and 84 isolated integration tests passed. Focused approval,
  progress, controller, rearm, status-guidance, help, documentation, security,
  and diff-integrity checks found no introduced blocker. No live cloud, SSH,
  service, route, gateway, or mTLS mutation was performed.

#### Test Method

- Run direct-leaf TLS/profile/fingerprint tests across supported Python/OpenSSL lanes; state and SSH fault injection at every durable effect and lost acknowledgement; initial apply, no-op apply, member replacement, broken-mTLS recovery, approval drift, concurrency, and private-key non-export tests; composed old/overlap/mixed/new/pruned runtime tests; controller tests proving rotation inhibition blocks transfer without changing the active/passive dataplanes while a real apply lock still fences; concise-output and interactive-spinner tests; CLI/help/schema/wizard/package tests; Ruff, mypy, full unit/integration, Markdown, security, and changed-scope alignment gates. Live mutation remains a separately approved non-production acceptance trial.
- Offline implementation evidence on 2026-08-19: Ruff and mypy passed, all
  1,094 unit tests and 63 isolated integration tests passed, and 14 focused
  build/release tests passed. Direct-leaf handshakes, certificate profiles,
  private-key non-export, state recovery, inhibition, apply bootstrap/no-op/
  replacement, digest drift, passive-first rotation, heartbeat-v2 binding,
  status projection, and stale-schema rejection were exercised locally on
  Python 3.12/OpenSSL. README and changelog Markdown lint passed. No live
  Nebius, SSH, service, cloud, route, or gateway mutation was performed.
- Additional offline implementation evidence on 2026-08-25: full Ruff and
  mypy across 52 source files passed, all 1,627 unit tests and 79 isolated
  integration tests passed, and the independent final risk review found no
  remaining blocking correctness or availability issue. Focused regressions
  exercise controller-dispatch/inhibition interleaving, exact quiescence,
  pre-prepare cleanup, inhibition-only resume/status, human output, and
  progress cleanup. README, changelog, and requirements Markdown lint and diff
  integrity passed; `docs/design.md` retains its pre-existing first-heading
  and line-length baseline. No live cloud, SSH, service, route, or gateway
  operation was performed.
- Offline command-consolidation evidence on 2026-08-29: the public tree and
  built-release integration path expose rotation only as
  `vm-ha --rotate-mtls`; the old standalone command fails during parsing, and
  exclusive-mode regressions prove rejected facade options cannot reach the
  rotation handler while bare `vm-ha` cannot dispatch it. Ruff, mypy, all
  1,925 unit tests and 84 isolated integration tests passed. Focused approval,
  progress, controller, rearm, status-guidance, help, documentation, security,
  and diff-integrity checks found no introduced blocker. No live cloud, SSH,
  service, route, gateway, or mTLS mutation was performed.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-008 -->

<!-- REQUIREMENT: REQ-009 status=active priority=P1 type=feature -->
### REQ-009: Keep network selection progress accurate and concise

#### User Story

Keep `gateway_group.network_id` optional in schema-v1 local configurations and make `apply` report the selected gateway network accurately without repeating the same informational decision for internal safety rereads. Constraints: Preserve the established `default-network`, single-custom-network, and ambiguous-network selection order; preserve every authoritative cloud reread used by VM-HA fencing and lifecycle validation; preserve explicit `network_id` validation and failures; and do not infer, persist, or require a network identifier merely to silence output. Non-goals: Changing the YAML layout, removing safety rereads, caching mutable cloud observations, changing network/subnet selection, or adding a compatibility path.

#### Acceptance Criteria

- A schema-v1 file with `gateway_group.network_id` omitted remains valid and uses the documented discovery order.
- One `VMManager` reports a successful implicit or explicit network selection once while repeated internal resolutions still perform their authoritative SDK reads and return the current network identity.
- Existing-instance discovery never says the instances were found "for recreation" when `recreate` is false.
- Focused tests cover omitted and explicit network selection, repeated resolution output cardinality, preserved SDK call cardinality, and recreation wording without live cloud access.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused gateway-subnet, VM-manager, configuration-loader, and CLI-output tests, then Ruff, mypy, and the full unit lane. No live apply is required for source verification.

#### Test Method

- Run focused gateway-subnet, VM-manager, configuration-loader, and CLI-output tests, then Ruff, mypy, and the full unit lane. No live apply is required for source verification.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-009 -->

<!-- REQUIREMENT: REQ-010 status=active priority=P1 type=feature -->
### REQ-010: Keep the primary VPN status table concise and complete

#### User Story

Keep the primary `VPN Gateway Status` table compact and temporally truthful, display every configured tunnel name completely, and label its mode-aware tunnel/session duration as `Uptime` rather than `BGP Uptime`. Constraints: Preserve configured tunnel roles, IPsec/BGP/peer/encryption/uptime values and selection precedence, every success and error row, the separate `Traffic Override` warning, ECMP warnings, service/routing sections and displayed order, read-only behavior, and exit semantics. Keep the existing Rich-based responsive layout without assigning a brittle fixed tunnel width. Never infer recovery of one tunnel or service probe from a different VM-HA observation. Non-goals: Removing runtime override detection, changing tunnel selection or health classification, adding a replacement status flag, changing configuration or cloud state, or introducing a machine-readable status schema.

#### Acceptance Criteria

- The primary table has exactly `Tunnel`, `Configured Role`, `Gateway VM`, `IPsec`, `BGP`, `Peer IP`, `Encryption`, and `Uptime` in that order. BGP tunnels prefer current BGP-neighbor uptime and retain the existing IPsec-SA fallback when BGP uptime is unavailable; Static tunnels use IPsec-SA uptime. VM boot uptime is not reported.
- Tunnel values use folded overflow: the complete value remains on one line when space permits and wraps without an ellipsis on narrower terminals, including schema-valid 64-character names.
- Preferred, fallback, empty, timeout, parse-error, and exception paths emit exactly eight cells per row. Error rows render `-` in Uptime and place bounded sanitized diagnostics in status notes rather than a semantic uptime cell.
- Status buffers primary observations until one final reconciliation pass. When an exact standby tunnel or service probe failed and later VM-HA evidence indicates expected restoration or current readiness, status re-runs only that exact failed command once within its existing timeout. A tunnel retry replaces the stale aggregate error only when the identical command succeeds and returns recognizable established-SA evidence; empty, no-SA, connecting-only, or malformed output leaves the original failure visible. A service retry requires the expected active result. Only that semantically valid same-probe success may emit `Recovered during this status check`, while the independent VM-HA aggregate retains its own conservative state.
- Runtime role differences remain visible through the existing `Traffic Override` panel even though the per-row Traffic State value is removed.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused table-construction, long-name rendering, preferred/fallback row, Traffic Override, and no-color tests, followed by Ruff, mypy, full unit and isolated CLI integration suites, Markdown lint, canonical-spec validation, security review, and diff-integrity checks. Live cloud or gateway execution is not required.
- Offline implementation evidence on 2026-08-20: the pure table constructor
  exposes the exact eight-column contract with folded Tunnel overflow; a
  64-character constrained-width regression proves lossless wrapping, and an
  AST row-arity check proves all seven preferred, fallback, and error branches
  emit eight cells. Traffic Override regressions remain green. Full Ruff and
  mypy passed, all 1,116 unit tests and 69 isolated integration tests passed,
  and changed-scope documentation, security, alignment, and diff-integrity
  checks found no introduced blocker.

#### Test Method

- Run focused table-construction, long-name rendering, preferred/fallback row, Traffic Override, and no-color tests, followed by Ruff, mypy, full unit and isolated CLI integration suites, Markdown lint, canonical-spec validation, security review, and diff-integrity checks. Live cloud or gateway execution is not required.
- Offline implementation evidence on 2026-08-20: the pure table constructor
  exposes the exact eight-column contract with folded Tunnel overflow; a
  64-character constrained-width regression proves lossless wrapping, and an
  AST row-arity check proves all seven preferred, fallback, and error branches
  emit eight cells. Traffic Override regressions remain green. Full Ruff and
  mypy passed, all 1,116 unit tests and 69 isolated integration tests passed,
  and changed-scope documentation, security, alignment, and diff-integrity
  checks found no introduced blocker.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-010 -->

<!-- REQUIREMENT: REQ-011 status=active priority=P1 type=feature -->
### REQ-011: Enforce owner-aware BGP export safety in VM-HA

#### User Story

Prevent a non-forwarding VM-HA member from exporting locally learned remote routes, make active/passive BGP export and routing-hygiene parity part of authoritative readiness, complete and periodically re-enforce passive routing hygiene after materialization, and make `list-routes-local` strictly observational. Constraints: Preserve established BGP sessions and imported remote routes on a warm passive member; preserve `gateway.local_prefixes`, connection-level `advertise_local_prefixes`, tunnel MED ordering, optional `remote_prefixes`, existing CLI arguments, route-table display columns, exit behavior, the four-column VM-HA status table, non-HA behavior, configuration schema, logical manifest format, and persisted runtime records. Only an explicit mutating workflow may upload configuration or reload a gateway. Gateway-level role labels must use authoritative current VM-HA ownership and remain distinct from configured per-tunnel active/passive preference. Non-goals: Requiring an inbound prefix whitelist, adding a list-command repair flag or compatibility alias, changing cloud ownership or VPC route semantics, enabling active-active forwarding, introducing a second BGP configuration owner, or treating configured active/passive preference as current ownership.

#### Acceptance Criteria

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
- For explicit VM HA, `list-routes-local` derives every gateway heading from the same exact authority snapshot that survives the advertisement audit's post-read recheck: the owner is labeled `active` and rendered green, the other configured member is labeled `standby`, and both are labeled `unknown` when authority is absent or changes. It never infers ownership from route presence, BGP state, or tunnel `ha_role`; non-VM-HA gateway headings remain unchanged.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

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
- Offline gateway-heading implementation evidence on 2026-08-21: the listing
  retains only the exact VM-HA authority that survives the advertisement
  audit's post-read recheck, renders that owner as green `ACTIVE`, renders the
  other member as `STANDBY`, and renders both as `UNKNOWN` when authority is
  unavailable or changes. Focused coverage proves both ownership directions,
  forced-color output, literal no-color labels, configured tunnel-role
  independence, unchanged non-VM-HA headings, and the preserved public audit
  return contract. The 181 route-manager selection and CLI route tests, Ruff,
  mypy, changed-scope Markdown lint, security review, and diff-integrity checks
  passed without live gateway, SSH, route, or cloud access.

#### Test Method

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
- Offline gateway-heading implementation evidence on 2026-08-21: the listing
  retains only the exact VM-HA authority that survives the advertisement
  audit's post-read recheck, renders that owner as green `ACTIVE`, renders the
  other member as `STANDBY`, and renders both as `UNKNOWN` when authority is
  unavailable or changes. Focused coverage proves both ownership directions,
  forced-color output, literal no-color labels, configured tunnel-role
  independence, unchanged non-VM-HA headings, and the preserved public audit
  return contract. The 181 route-manager selection and CLI route tests, Ruff,
  mypy, changed-scope Markdown lint, security review, and diff-integrity checks
  passed without live gateway, SSH, route, or cloud access.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-011 -->

<!-- REQUIREMENT: REQ-012 status=active priority=P1 type=feature -->
### REQ-012: Align every public command with topology and routing mode

#### User Story

Give every public `nebius-vpngw` command, subcommand, and flag one explicit, tested applicability contract across ordinary single-VM versus explicit VM-HA configurations and static versus BGP routing, and fail before authentication or mutation when a requested combination is unsupported or the installed gateway agent lacks the required private repair capability. Constraints: Preserve the existing public command tree, arguments, option names and aliases, configuration schema, ordinary single-VM workflows, VM-HA controller ownership, and observational list/status behavior. Preserve connection-level and tunnel-level static prefixes. A rejected tunnel restart/failover/failback applicability request exits exactly `1` with one plain action-specific stderr line and no loading banner, usage panel, generic error prefix, traceback, or success output. A failed route or advertisement operation exits nonzero and never prints a success-style completion message. Non-goals: Adding compatibility aliases or public repair flags, making list commands mutating, bypassing VM-HA authority with direct VPC route writes, using tunnel failover as VM ownership failover, silently accepting an older installed agent, or adding a second route or configuration owner.

#### Acceptance Criteria

- One centralized applicability policy covers all 16 executable leaves and the action-specific `vm-ha --rotate-mtls` constraints before authentication, prompts, SSH, cloud mutation, or agent requests. VM-only operations reject ordinary configurations; `destroy` is topology-aware and accepts ordinary or explicit VM-HA configurations through one canonical workflow. Tunnel restart rejects every explicit VM-HA configuration with one topology-first message that identifies the gateway as VM-HA-enabled, states that tunnel recovery is controller-owned, directs health inspection to `status`, and limits `apply` guidance to configuration convergence. Tunnel failover/failback retain their corresponding topology-first `failover vm`/`failback vm` ownership-only guidance. Ordinary Static routing remains a distinct failover/failback rejection; ordinary tunnel restart remains supported for both Static and BGP.
- `add-routes-local` retains direct VPC route management for ordinary static and BGP configurations. For explicit VM HA, it never derives next hops from member primary allocations or mutates controller-owned VPC routes. Static-only mode may wait for the autonomous controller to reconcile only the already-installed exact generation, while BGP-only mode may repair only exact proven advertisement drift under the existing VM-HA authority contract; mixed VM-HA routing remains unsupported by this command.
- `--summarize`, `--swap-route-table`, and `--yes` are rejected for VM-HA route repair; `--yes` is also rejected when no route-table swap was requested. Rejection is parse- or plan-time and precedes every external effect.
- Any workflow that depends on a private installed-agent behavior or can invoke a private installed-agent action first performs a bounded read-only capability handshake on every affected gateway. Missing, malformed, or incomplete capability evidence reports installed/source skew, directs the operator to the supported apply workflow, and prevents all route or agent mutation.
- Every VM-HA route audit or repair freezes the exact immutable per-member SSH host-pin policy before authentication and uses the lifecycle hostname as the host-key alias for capability, status, FRR, and private repair requests. Missing, changed, or mismatched trust evidence fails before the first gateway request.
- Route target selection and remote-route listing use one canonical normalized remote-prefix resolver. Static prefixes are the union of connection-level prefixes and enabled, instance-scoped tunnel `static_routes.remote_prefixes`; BGP prefixes remain learned from the configured peers.
- Mixed ordinary static/BGP plans capability-probe and query FRR only on gateway members that own a configured BGP policy. A BGP policy whose tunnels are all disabled still verifies that its member has no stale live peers.
- Mutating route helpers return typed success or raise typed failure. Prerequisite, SDK, route-table, SSH, authority, repair, and postcondition failures propagate to a nonzero CLI result; success is rendered only after the complete requested workflow converges.
- VM-HA static convergence requires an `ACTIVE` lifecycle with no pending lifecycle effect, exact two-member installed-generation and policy-digest parity with the local plan, one stable owner and shared allocation, no writer inhibition, and an installed controller capability on both members. Route drift may temporarily remove active forwarding under the controller's existing fail-closed transition; the CLI neither requests a second writer nor promises disruption-free repair.
- VM-HA static convergence succeeds immediately when a fresh agent receipt and independent cloud observation already prove the installed static manifest. Otherwise it polls the unchanged authority for at most 120 seconds while the existing controller converges. Every expected static prefix must exist exactly once per declared target through the shared allocation, current-cluster managed static extras must be absent, an exact-prefix foreign occupancy blocks, and unequal foreign overlaps such as a more-specific `/32` remain untouched.
- Rendered help identifies `restart-tunnel`, `failover tunnel`, and `failback tunnel` as commands for regular gateways (non-HA). It keeps the routing distinction explicit: restart supports regular Static and BGP gateways, while tunnel failover and failback support only regular gateways using BGP and continue to exclude Static routing.
- Matrix tests cover every executable leaf and relevant flag across ordinary-static, ordinary-BGP, VM-HA-static, and VM-HA-BGP plans, including exact topology-first one-line VM-HA tunnel restart and transfer rejection output, exact ordinary-Static transfer rejection output, exit `1`, empty stdout, absence of loading/usage/generic-error/traceback noise, ordinary-Static IPsec-only restart, valid tunnel-only static prefixes, installed-agent skew, zero-effect rejection, and no false-success output.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused applicability, static-prefix, route selection, advertisement repair, installed-agent parser/capability, help/flag-manifest, and four-mode CLI tests; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; canonical-spec validation; security and alignment review; wheel build; and diff-integrity checks. Offline checks do not prove that an installed gateway has been upgraded or that a live route repair converges.
- Offline implementation evidence on 2026-08-23: static-only explicit VM-HA
  no-op, autonomous convergence, timeout, authority-race, exact receipt,
  exact-prefix conflict, managed-extra, and unequal-overlap regressions passed.
  Ruff, mypy, 1,451 unit tests, 70 isolated integration tests, wheel
  construction, CLI help rendering, changed-scope Markdown and diff checks,
  and changed-scope security and code-quality review passed. No live cloud,
  SSH, gateway, or route mutation was performed.
- Offline tunnel-transfer UX evidence on 2026-08-26: exact-output regressions
  covered failover and failback across VM-HA Static and VM-HA BGP, with and
  without a tunnel name, plus the distinct ordinary-Static rejection. Every
  case exits `1` with empty stdout, one stderr line, and zero authentication or
  SSH effects. Supported ordinary-BGP execution and both source help pages
  remained green. Ruff, full mypy, 1,641 unit tests, 79 isolated integration
  tests, canonical-spec validation, changed-document Markdown lint,
  changed-scope security/code review, and diff integrity passed. The existing
  `docs/design.md` first-heading and long-line baseline remains outside this
  change. No live cloud, SSH, gateway, route, or failover operation ran;
  installed-artifact parity was not separately claimed.
- Offline tunnel-restart UX evidence on 2026-08-26: exact-output and
  zero-effect regressions passed across VM-HA Static and VM-HA BGP with `all`
  and a named tunnel. Ordinary Static remained an IPsec-only restart without
  an FRR command, while the existing ordinary-BGP neighbor-reset and target
  selection tests remained green. The 107-test focused matrix, 1,645 unit
  tests, 79 isolated integration tests, targeted Ruff, mypy across 49 source
  files, source CLI help, changed-document Markdown lint, changed-scope
  security/code review, and diff integrity passed. The existing
  `docs/design.md` first-heading and long-line baseline remains outside this
  change. No live cloud, SSH, gateway, route, tunnel, or failover operation
  ran; installed-artifact parity was not separately claimed.
- Offline implementation evidence on 2026-08-20: the complete 18-leaf/four-mode applicability matrix, zero-effect rejection sentinels, capability-skew, static-prefix, mixed ordinary routing, exact VM-HA SSH trust, strict route outcome, public flag-manifest, and help regressions passed. Ruff, mypy, 1,283 unit tests, 69 isolated integration tests, changed-scope Markdown lint, security and code-quality review, wheel build/inspection, and diff-integrity checks passed. No live cloud, SSH, gateway, or route mutation was performed.

#### Test Method

- Run focused applicability, static-prefix, route selection, advertisement repair, installed-agent parser/capability, help/flag-manifest, and four-mode CLI tests; Ruff; mypy; the full unit and isolated integration suites; Markdown lint; canonical-spec validation; security and alignment review; wheel build; and diff-integrity checks. Offline checks do not prove that an installed gateway has been upgraded or that a live route repair converges.
- Offline implementation evidence on 2026-08-23: static-only explicit VM-HA
  no-op, autonomous convergence, timeout, authority-race, exact receipt,
  exact-prefix conflict, managed-extra, and unequal-overlap regressions passed.
  Ruff, mypy, 1,451 unit tests, 70 isolated integration tests, wheel
  construction, CLI help rendering, changed-scope Markdown and diff checks,
  and changed-scope security and code-quality review passed. No live cloud,
  SSH, gateway, or route mutation was performed.
- Offline tunnel-transfer UX evidence on 2026-08-26: exact-output regressions
  covered failover and failback across VM-HA Static and VM-HA BGP, with and
  without a tunnel name, plus the distinct ordinary-Static rejection. Every
  case exits `1` with empty stdout, one stderr line, and zero authentication or
  SSH effects. Supported ordinary-BGP execution and both source help pages
  remained green. Ruff, full mypy, 1,641 unit tests, 79 isolated integration
  tests, canonical-spec validation, changed-document Markdown lint,
  changed-scope security/code review, and diff integrity passed. The existing
  `docs/design.md` first-heading and long-line baseline remains outside this
  change. No live cloud, SSH, gateway, route, or failover operation ran;
  installed-artifact parity was not separately claimed.
- Offline tunnel-restart UX evidence on 2026-08-26: exact-output and
  zero-effect regressions passed across VM-HA Static and VM-HA BGP with `all`
  and a named tunnel. Ordinary Static remained an IPsec-only restart without
  an FRR command, while the existing ordinary-BGP neighbor-reset and target
  selection tests remained green. The 107-test focused matrix, 1,645 unit
  tests, 79 isolated integration tests, targeted Ruff, mypy across 49 source
  files, source CLI help, changed-document Markdown lint, changed-scope
  security/code review, and diff integrity passed. The existing
  `docs/design.md` first-heading and long-line baseline remains outside this
  change. No live cloud, SSH, gateway, route, tunnel, or failover operation
  ran; installed-artifact parity was not separately claimed.
- Offline implementation evidence on 2026-08-20: the complete 18-leaf/four-mode applicability matrix, zero-effect rejection sentinels, capability-skew, static-prefix, mixed ordinary routing, exact VM-HA SSH trust, strict route outcome, public flag-manifest, and help regressions passed. Ruff, mypy, 1,283 unit tests, 69 isolated integration tests, changed-scope Markdown lint, security and code-quality review, wheel build/inspection, and diff-integrity checks passed. No live cloud, SSH, gateway, or route mutation was performed.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-012 -->

<!-- REQUIREMENT: REQ-013 status=active priority=P1 type=feature -->
### REQ-013: Manage per-deployment gateway SSH host trust

#### User Story

When `VPNGW_SSH_KNOWN_HOSTS_FILE` is unset, resolve exact ordinary and VM-HA SSH host trust from a deterministic per-deployment operator-side store and let `apply` create or repair that store from previously validated exact pins, authoritative local key material, a safely snapshotted exact legacy pin in the user's default `~/.ssh/known_hosts`, or authenticated product provisioning evidence. For a genuinely fresh ordinary or VM-HA member, apply must pre-pin a product-owned server identity and install that exact private key through cloud-init before its first SSH connection. When `VPNGW_SSH_HOST_KEYS_DIR` is unset, use the deterministic per-deployment host-key namespace under the managed SSH product root, prepare missing private host keys only for genuinely fresh members, and recover an original retained or recreated product key only from exact matching persisted provisioning evidence. An exact approved `active-standby-replacement` may generate a new identity for its authoritatively absent non-owner and rotate only the product-managed pin when the original managed private key is gone. As the named `legacy-ordinary-network-enrollment-v1` exception, actual ordinary `apply` may instead pin the unchanged current Ed25519 host key of one exact retained pre-branch ordinary VM when no authoritative trust source remains. Constraints: Preserve exact host-key verification, immutable per-operation snapshots, fail-closed behavior, read-only status, explicit default-disabled VM HA, supported non-HA command behavior, the existing configuration schema and CLI surface, and explicit absolute environment overrides as highest precedence. Before an ordinary deployment has managed trust, non-apply consumers retain their existing system-known-hosts behavior. After apply publishes managed trust, every supported SSH consumer uses that scoped authority. An explicitly configured but invalid override never falls back. Never modify the user's general `~/.ssh/known_hosts`, use `ssh-keyscan`, disable verification, treat transport reachability alone as identity proof, or allow a recyclable management address to become the persistent identity owner. The named legacy enrollment admits the residual risk of an active transparent network attacker only for that first bounded transaction and never authorizes automatic relearning. Non-goals: Rotating or replacing the SSH identity of a present Compute, generating keys in an explicit operator-owned directory, modifying an explicit operator-owned trust file, accepting network-only evidence outside the named legacy ordinary enrollment, making dry-run, status, or route/list/transfer/mTLS commands persistent trust writers, automatically replacing a retained gateway when trusted evidence is absent, or changing VM-to-VM mTLS identity.

#### Acceptance Criteria

- The managed store is `~/.ssh/nebius-vpngw/<scope-sha256>/`, where the scope digest is derived deterministically from canonical tenant, project, region, gateway-group, and topology identity: the VM-HA cluster identity or the fixed ordinary topology discriminator. Its canonical v2 public-key-only receipt binds each stable member hostname and exact key to a closed authority kind, optional exact Compute-binding digest, and optional ordinary-predecessor receipt digest. A derived OpenSSH `known_hosts` projection carries that same key under the stable hostname plus the exact current configured or discovered management-address aliases needed by supported older releases; neither file contains private key bytes, credentials, peer certificates, or secret configuration. VM-HA is pre-adoption, so no v1 receipt reader or mixed receipt mode exists.
- Managed trust authority is keyed by each stable gateway hostname and carried to the discovered management address through `HostKeyAlias`; address changes do not silently create a new identity. An explicit source may name the exact hostname or current configured/discovered address, but all matching aliases must resolve to one unambiguous key before normalization. The non-authoritative projection may change when `apply` observes a new address, while the receipt identity remains unchanged.
- `VPNGW_SSH_KNOWN_HOSTS_FILE` remains the highest-precedence source. A missing, unreadable, malformed, symlinked, empty, incomplete, revoked, ambiguous, or mismatched explicit source fails without reading the managed store. A successful actual `apply` verifies retained members through those pins before importing only the exact member keys; it never rewrites the explicit file.
- When the explicit source is unset and a retained member lacks a managed pin, actual `apply` may read literal `~/.ssh/known_hosts` only as a one-time migration candidate. The source is opened without following symlinks, must be a current-user-owned single-link regular file with no group or other write access, and is held as an immutable content-and-file-identity snapshot. Only an exact stable hostname or exact current configured/discovered management-address record, including the equivalent OpenSSH hashed form, is eligible. An exact revoked record blocks; unrelated malformed records, wildcards, certificates, and other aliases grant no authority. One unambiguous raw Ed25519 candidate must be bound to the exact current ordinary Compute identity, or to the exact VM-HA Compute plus lifecycle or product-provisioning identity, verified through strict SSH, and re-read unchanged before only that normalized stable-hostname pin is imported. The general file is never created, repaired, rewritten, or used by non-apply commands.
- `VPNGW_SSH_HOST_KEYS_DIR` remains the highest-precedence private host-key directory when explicitly set; it must already exist as an absolute non-symlink directory and is never created or populated by the product. Otherwise the effective default is `~/.ssh/nebius-vpngw/host-keys/<gateway-group>/<scope-sha256>`, using the validated gateway-group name and the complete deployment-scope digest without mutating the process environment. Actual apply creates the missing default hierarchy as current-user-owned, owner-only, non-symlink directories and atomically prepares a missing unencrypted Ed25519 `<gateway-hostname>.key` only for a genuinely fresh member. Same-named gateways in different tenant, project, region, or cluster scopes never share private keys or a preparation lock. Every consumed key remains a current-user-owned, owner-only, single-link regular file matching the exact pin. An explicitly empty or invalid environment value fails instead of using the default.
- With no override, `apply` may recover public trust from a valid managed receipt/projection, a verified default-known-hosts migration candidate, or the public identity derived from exact authenticated product cloud-init. Cloud recovery accepts the current product key path and marker, or the legacy product path only when a separately hardened lifecycle record binds the exact project, gateway, cluster, member, Compute ID, topology, and allowed active state. A retained present member whose available authoritative sources disagree, or for which no authority remains, blocks before cloud mutation; apply never generates a replacement identity for it. A genuinely fresh member may reuse its valid persisted default key or receive one newly generated default key that is both pinned locally and installed by the existing provisioning path. An approved replacement or recreation with existing public authority still requires matching original private material unless it is the exact authoritatively absent non-owner in an `active-standby-replacement`, both trust and key storage use product-managed defaults, and the approval binds the old fingerprint, trust scope, predecessor trust-file digests, lifecycle predecessor, cloud observation, and rotation action. Explicit trust or key-directory overrides never enter this exception. Cloud-recovered private material may satisfy the ordinary requirement only when its derived public key matches every existing authoritative pin exactly.
- The named legacy ordinary enrollment is eligible only when exact read-only discovery finds one retained ordinary member, no explicit, managed, default-known-hosts, local private-key, or product-provisioning authority succeeds, no recreation is requested or required, and the same project/gateway writer lock covers the invocation. It observes Ed25519 H1, re-reads the complete Compute signature, observes identical H2, connects with H1 pinned and only the configured client identity, correlates guest IMDS identity or the bounded cloud-init instance ID to that Compute, and re-reads Compute again. Any member, key, Compute, address, guest, client-key, or configuration-change drift fails without publication or cloud/host mutation. A successful strict-policy rebuild publishes one `legacy-ordinary-network-enrollment-v1` authority and never relearns it automatically.
- Every SSH operation whose configuration includes `ssh_public_key` resolves exactly that public identity from the explicit owner-only `ssh_private_key_path`, one matching `ssh-agent` key, or one matching owner-only supported default private key. Paramiko and OpenSSH disable unrelated-key discovery, password, keyboard-interactive, and prompt fallback. Zero, multiple, encrypted, insecure, or mismatched candidates fail without logging key material, fingerprints, or local key paths.
- Every retained ordinary member's exact Compute ID, name, project, revision, management address, and cloud-init digest are bound during read-only discovery and rechecked for explicit, managed-receipt, managed-projection, default-file migration, and cloud-recovery trust before publication, cloud mutation, or later policy use. Apply's connectivity, cloud-init, ESP4, and package health probes use the configured management SSH username rather than a hard-coded image default.
- Before ordinary-source conversion may prompt, publish a candidate, or reserve a passive allocation, `vm-ha` requires the ordinary managed receipt and directs the operator to actual ordinary `apply` when it is absent. During approved migration, apply imports the retained active's exact ordinary pin into the HA v2 receipt with `ordinary-migration-v1`, the ordinary predecessor receipt digest, and a freshly proved Compute-binding digest. That authority-bearing desired receipt digest is part of the public approval plan. After product-owned alias attachment and passive creation, apply must rediscover both exact members and rebuild the immutable SSH policy against their fresh Compute revisions while retaining the same managed pins before any staging SSH. An explicit candidate with no recoverable source path gives generic `<ordinary-source>` apply guidance and performs no migration effect.
- `apply --dry-run` validates and reports a secret-free trust action without creating directories, private keys, receipts, or persistent pins; it may use an ephemeral preview identity for a genuinely fresh member or an eligible absent-non-owner rotation. When the named legacy ordinary enrollment is required, dry-run reports that exact action and exits nonzero without observing a network key. Actual apply may persist fresh-member default keys during preflight, verifies every retained present member, then publishes an owner-only authoritative receipt and derived projection with locked compare-and-swap and atomic per-file replacement before the first cloud mutation. For approved absent-non-owner rotation, the lifecycle journal first records the secret-free authorization, then stages one operation-derived retry-stable private key, binds its new fingerprint and successor receipt/projection digests, publishes the canonical key and managed trust, and marks publication complete before any replacement disk or Compute effect. The receipt is committed first, so an interruption can leave only a stale or missing reproducible projection, never partial authoritative receipt content; retries reuse the bound key and exact successor digests. Concurrent, symlink, hard-link, ownership, mode, predecessor-drift, or invalid existing-key failures block key preparation or publication without overwriting an existing foreign object.
- `status` and every non-apply ordinary or VM-HA command may consume an existing managed receipt through the same immutable snapshot policy but never create, import, repair, or persist trust. Missing trust remains isolated to the affected status member; mutating commands fail before authentication and direct the operator to `apply`.
- An existing deployment may migrate without changing YAML or setting an environment variable when its exact current member pin already exists in the safe default user known-hosts file and the deployment binding remains authoritative. Otherwise it may migrate through an explicit trust file, retained original private host key, authenticated product cloud-init, or the named bounded ordinary enrollment. Ordinary `apply` is the sole writer for all four paths and must complete before `vm-ha` conversion. Older releases can consume the generated OpenSSH projection by explicitly pointing `VPNGW_SSH_KNOWN_HOSTS_FILE` to it; compatibility is verified against exact current address lookup rather than inferred from hostname-only content.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Cover deterministic ordinary and VM-HA scope identity, v2 authority and no-v1-reader validation, explicit and default private-key directory precedence, exact client-key resolution and fallback refusal, stable alias normalization, selective default-known-hosts parsing including hashed aliases and marker handling, source snapshot ownership/mode/link and change detection, cloud-init and lifecycle binding, H1/cloud/H2/guest/cloud enrollment ordering, active-MITM warning, atomic publication and concurrency, dry-run and status no-write behavior, retained/fresh/recreated recovery, approved absent-non-owner rotation, interruption at each rotation checkpoint, explicit-override rejection, stale approval and trust-predecessor rejection, ordinary-receipt conversion gating and HA import, mismatch rejection, Static/BGP parity, pre-cloud-mutation ordering, ordinary and VM-HA SSH consumers after publication, route and mTLS trust consumers, documentation, and packaging. Run Ruff, mypy, focused and full unit/integration suites, canonical-spec validation, Markdown lint, security review, alignment review, and diff-integrity checks. A live trust migration or gateway mutation remains separately authorized non-production acceptance work.
- Offline implementation evidence on 2026-08-22: focused literal and hashed
  default-known-hosts import, immutable-source reread, current and legacy
  Compute/cloud-init recovery, bounded duplicate-rejecting parsing, hardened
  lifecycle binding, deferred recovered-key publication, retained/fresh key
  separation, Static/BGP shared-path, pre-mutation, and exact CI-selection
  regressions passed. The final `make check` passed Ruff, mypy across 48 source
  files, and all 1,397 unit tests; all 70 isolated integration tests and the
  final wheel build/inspection also passed. Task-owned documentation changes
  have no Markdown diagnostics; unrelated existing `docs/design.md`
  diagnostics keep the repository-wide Markdown gate non-green. Focused
  security and code-quality review findings were corrected and revalidated.
  Installed-command and live gateway, SSH, or cloud validation were not run.
- Offline implementation evidence on 2026-08-30: focused coverage proves
  write-free rotation planning, approval binding to the managed predecessor,
  explicit-override refusal, traversal-safe intent validation, retry-stable key
  reuse, stale-trust rejection, receipt/projection publication, and lifecycle
  refusal to begin trust publication or cloud creation out of order. All 1,987
  unit tests and 84 isolated integration tests passed. Ruff, mypy across 57
  source files, VM-HA help rendering, changed-scope Markdown lint, and diff
  integrity passed; the pre-existing design-document heading and later
  long-line findings remain outside this change. No live gateway, SSH trust, or
  cloud mutation was performed.

#### Test Method

- Cover deterministic ordinary and VM-HA scope identity, v2 authority and no-v1-reader validation, explicit and default private-key directory precedence, exact client-key resolution and fallback refusal, stable alias normalization, selective default-known-hosts parsing including hashed aliases and marker handling, source snapshot ownership/mode/link and change detection, cloud-init and lifecycle binding, H1/cloud/H2/guest/cloud enrollment ordering, active-MITM warning, atomic publication and concurrency, dry-run and status no-write behavior, retained/fresh/recreated recovery, approved absent-non-owner rotation, interruption at each rotation checkpoint, explicit-override rejection, stale approval and trust-predecessor rejection, ordinary-receipt conversion gating and HA import, mismatch rejection, Static/BGP parity, pre-cloud-mutation ordering, ordinary and VM-HA SSH consumers after publication, route and mTLS trust consumers, documentation, and packaging. Run Ruff, mypy, focused and full unit/integration suites, canonical-spec validation, Markdown lint, security review, alignment review, and diff-integrity checks. A live trust migration or gateway mutation remains separately authorized non-production acceptance work.
- Offline implementation evidence on 2026-08-22: focused literal and hashed
  default-known-hosts import, immutable-source reread, current and legacy
  Compute/cloud-init recovery, bounded duplicate-rejecting parsing, hardened
  lifecycle binding, deferred recovered-key publication, retained/fresh key
  separation, Static/BGP shared-path, pre-mutation, and exact CI-selection
  regressions passed. The final `make check` passed Ruff, mypy across 48 source
  files, and all 1,397 unit tests; all 70 isolated integration tests and the
  final wheel build/inspection also passed. Task-owned documentation changes
  have no Markdown diagnostics; unrelated existing `docs/design.md`
  diagnostics keep the repository-wide Markdown gate non-green. Focused
  security and code-quality review findings were corrected and revalidated.
  Installed-command and live gateway, SSH, or cloud validation were not run.
- Offline implementation evidence on 2026-08-30: focused coverage proves
  write-free rotation planning, approval binding to the managed predecessor,
  explicit-override refusal, traversal-safe intent validation, retry-stable key
  reuse, stale-trust rejection, receipt/projection publication, and lifecycle
  refusal to begin trust publication or cloud creation out of order. All 1,987
  unit tests and 84 isolated integration tests passed. Ruff, mypy across 57
  source files, VM-HA help rendering, changed-scope Markdown lint, and diff
  integrity passed; the pre-existing design-document heading and later
  long-line findings remain outside this change. No live gateway, SSH trust, or
  cloud mutation was performed.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-013 -->

<!-- REQUIREMENT: REQ-014 status=active priority=P1 type=feature -->
### REQ-014: Keep VM-HA peer credential rotation provider-neutral

#### User Story

Allow an explicit two-member VM-HA deployment to stage any supported IPsec peer credential change at one successful, exact-generation, passively fenced checkpoint, independently of peer vendor and routing mode, before an operator updates the external peer and completes convergence with ordinary `apply`. Constraints: Preserve the existing configuration schema, supported vendor values, ordinary apply behavior, VM-HA owner and forwarding fences, explicit local-config requirement, non-VM-HA rejection, and migration/recovery/replacement approval isolation. The gateway appliance must not require peer-cloud credentials, call a peer-provider API, expose PSKs, infer a provider from routing mode, or add a second activation path. Existing provider helpers remain optional operator-side adapters. Non-goals: Automating every peer platform's credential API, promising zero downtime across peer products, adding a preparation path for ordinary single-VM gateways that lack the two-member lock/fencing contract, changing peer-side resources during ordinary apply, or weakening provider-specific helper validation.

#### Acceptance Criteria

- `apply --prepare-vm-ha-peer-rotation` admits an explicit local two-member VM-HA config for every supported `connection.vendor` value and for static-only, BGP-only, or schema-valid mixed routing; no vendor or routing mode controls admission.
- Preparation reuses canonical apply through bootstrap, staging, exact-generation locks, cloud-owner adoption, activation, exact locked-passive observations, and managed mTLS finalization, then returns before either lock is released or any active tunnel, BGP, route-receipt, or forwarding convergence is accepted.
- The successful output and CLI help describe an IPsec peer credential checkpoint without naming GCP or static routing. Repeating preparation with the same config is safe, and incompatible VM-HA approval flags still fail before effects.
- Peer mutation remains an explicit operator-owned step chosen for the peer platform. Provider adapters may update in place, replace one tunnel, recreate a tunnel, or reload local credentials, but must not become a dependency of the appliance core or receive authority from this flag.
- Ordinary `apply` with the same private config is the sole supported continuation. It re-establishes exact locks, releases only the cloud-selected owner first, requires the existing mode-appropriate IKE/XFRM/BGP/static-route receipt and forwarding gates, then releases and verifies the passive member.
- The GCP Classic rotation helper retains its exact GCP/static topology, retained-resource, confirmation-time identity, private-file, and fail-closed route-cleanup requirements; its narrower peer mutation contract does not narrow the appliance checkpoint.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Parameterize admission and the full preparation trace across supported vendor labels and static, BGP, and mixed routing. Preserve zero-effect rejection tests for implicit config, non-VM-HA plans, and every incompatible approval flag. Prove preparation clears no lock, ordinary continuation clears owner then passive and reaches `ACTIVE`, CLI help is provider-neutral, the GCP helper remains narrowly validated, and focused Ruff, mypy, unit, integration, documentation, security, packaging, and diff-integrity gates pass. Live AWS, Azure, Cisco, generic-appliance, and cross-mode peer trials remain separate acceptance evidence.
- Offline implementation evidence on 2026-08-23: the focused checkpoint oracle passed 21 tests across all supported vendor labels and static, BGP, and mixed routing, including repeated preparation, ordinary owner-first continuation, and direct fail-before-effects coverage for every incompatible approval flag; 67 existing GCP adapter tests passed unchanged. The final `make all` passed Ruff, mypy across 48 source files, all 1,433 unit tests, and wheel construction; all 70 isolated integration tests passed. The built wheel's `cli.py` SHA-256 matched the source exactly, focused diff integrity passed, and changed-scope security and code-quality review found no blocking issue. Task-owned README, requirements, and changelog additions have no Markdown diagnostics; unrelated existing `docs/design.md` diagnostics keep the repository-wide Markdown command non-green. Installed-command and live AWS, Azure, Cisco, generic-device, or mixed-routing gateway trials were not run.

#### Test Method

- Parameterize admission and the full preparation trace across supported vendor labels and static, BGP, and mixed routing. Preserve zero-effect rejection tests for implicit config, non-VM-HA plans, and every incompatible approval flag. Prove preparation clears no lock, ordinary continuation clears owner then passive and reaches `ACTIVE`, CLI help is provider-neutral, the GCP helper remains narrowly validated, and focused Ruff, mypy, unit, integration, documentation, security, packaging, and diff-integrity gates pass. Live AWS, Azure, Cisco, generic-appliance, and cross-mode peer trials remain separate acceptance evidence.
- Offline implementation evidence on 2026-08-23: the focused checkpoint oracle passed 21 tests across all supported vendor labels and static, BGP, and mixed routing, including repeated preparation, ordinary owner-first continuation, and direct fail-before-effects coverage for every incompatible approval flag; 67 existing GCP adapter tests passed unchanged. The final `make all` passed Ruff, mypy across 48 source files, all 1,433 unit tests, and wheel construction; all 70 isolated integration tests passed. The built wheel's `cli.py` SHA-256 matched the source exactly, focused diff integrity passed, and changed-scope security and code-quality review found no blocking issue. Task-owned README, requirements, and changelog additions have no Markdown diagnostics; unrelated existing `docs/design.md` diagnostics keep the repository-wide Markdown command non-green. Installed-command and live AWS, Azure, Cisco, generic-device, or mixed-routing gateway trials were not run.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-014 -->

<!-- REQUIREMENT: REQ-015 status=active priority=P1 type=feature -->
### REQ-015: Use one VM-HA command and canonical Nebius region terminology

#### User Story

Expose `nebius-vpngw vm-ha` as the only public VM-HA setup, convergence, verification, standby-restoration, and explicit managed-mTLS rotation command. Each invocation must idempotently repair every currently recoverable HA issue through the canonical effect owners until the deployment is healthy, or stop at an exact approval, authority, safety, or external-prerequisite boundary with a typed reason and actionable next step. Use `--region` as the only public location override across `vm-ha`, `apply`, `prep-network`, `status`, and `destroy`. Constraints: Remove the unpublished pre-adoption setup, rearm, and standalone mTLS-rotation command names and every public `--zone` option without aliases, deprecation wrappers, migration shims, or public migration guidance. Preserve the ordinary source, conversion file-safety and redaction rules, separate default-No passive-IP reservation, apply approvals, exact rearm authority, controller budgets, internal rearm module/service/schema names, and all non-HA behavior except the explicitly approved option spelling. Retain the persisted `region_id` and optional `gateway_group.region` keys as region values. Non-goals: Removing or duplicating the internal sole Compute-start writer, changing failover or failback, adding availability-zone selection, renaming persisted configuration keys, running a failover drill, or documenting a migration for VM-HA surfaces that have not shipped.

#### Acceptance Criteria

- Root and leaf help expose exactly 16 executable operations; the three removed
  command names and `--zone` fail during parsing before configuration,
  authentication, SSH, cloud reads, or mutation.
- `vm-ha` accepts ordinary or explicit VM-HA input and owns the existing
  conversion, exact-candidate reuse, passive-only allocation reservation,
  apply convergence, health proof, exact non-owner rearm delegation, and the
  exclusive explicit `--rotate-mtls` transaction. Rotation dispatches before
  ordinary conversion or convergence and retains its existing text-only,
  passive-first, exact-digest approval contract. For
  every state it classifies as recoverable, the selected canonical owner drives
  that repair to its verified checkpoint and the façade re-observes authority
  before claiming health. Repeating the command from any durable checkpoint
  resumes without duplicating an effect, and an already healthy deployment is
  a verified no-op. A recognized planner, controller, apply, or rearm blocker
  is never collapsed into a generic convergence failure; terminal output
  preserves its sanitized typed reason and exact recovery action. `--force`
  repairs only an exact candidate and never replaces conflicting YAML.
- A VM-HA-owned convergence plan is classified `vm-ha-required`; the existing
  `apply-required` classification remains reserved for a genuinely separate
  apply workflow. When an interactive text invocation proves that the current
  non-owner Compute is absent, `vm-ha` asks one default-No confirmation and,
  on `y`, creates and converges that member in the same invocation. That prompt
  does not expose the automation digest. JSON, non-TTY, and dry-run output stay
  noninteractive and retain the exact digest-bound `--approve` contract.
- The normal absent-non-owner transaction keeps the serving owner forwarding,
  does not reinstall or restart owner services, creates only the fresh standby
  disk and Compute, activates only that standby, and publishes its exact new
  Compute identity through managed mTLS plus an atomic owner runtime-binding
  update. If the installed owner lacks the live-peer replacement capability,
  the plan instead binds one combined owner-upgrade/restart plus replacement
  approval and truthfully reports possible traffic interruption.
- Owner replacement inhibition is acknowledged with a bounded 60-second poll
  that spans one complete 30-second cloud-read deadline and controller retry,
  followed by one final exact observation when the last probe crosses the
  deadline.
  Only the typed not-yet-quiescent response is retried; identity, capability,
  or malformed-evidence failures stop immediately. Exhaustion preserves the
  exact lifecycle checkpoint and reports
  `standby-replacement-inhibition-not-ready` with owner-controller guidance.
- If the approved capability refresh restarts the exact authoritative owner
  behind an already installed replacement inhibition, the controller may
  replay only that owner's local dataplane preparation, route reconciliation,
  and forwarding restoration. The inhibition remains exact-operation-bound,
  every ownership-changing action stays fenced, and cloud replacement cannot
  begin until the owner is active and controller-quiescent.
- Before starting the fresh replacement's control services, policy adoption may
  accept the retained owner's exact deterministic initial committed-enabled
  record without a peer acknowledgement, because that acknowledgement belonged
  to the authoritatively absent member. The fresh default-initialized member
  acknowledges only that exact decision, then `vm-ha` runs the canonical
  two-member enabled-policy transaction using it as the predecessor before
  replacement inhibition is released. An interrupted prepare or commit resumes
  only that deterministic successor transaction. Any non-default
  unacknowledged owner record remains blocking.
- A missing-non-owner resume paused before owner inhibition accepts only the
  exact guarded owner, route receipt, lifecycle operation, and pre-cloud
  frontier. It refreshes any owner lacking the current inhibition capability,
  then retires under the existing writer lock only a structurally valid
  terminal restoration superseded by a same-authority apply-owner-adoption
  receipt. Active, malformed, foreign, or differently owned authority remains
  blocked, and the original command resumes without an out-of-band state edit.
- `--region` overrides the optional group region and required top-level region
  for the affected command invocation. Without an override, the optional
  group value precedes the top-level value. Missing or unresolved region
  authority fails before cloud mutation; code and examples never synthesize an
  availability-zone suffix.
- The configuration wizard asks for one Nebius region, defaults examples to
  `eu-north1`, and does not add a separate VM-zone prompt. Existing schema
  field names and version remain unchanged.
- README, CLI help, requirements, design, and Unreleased changelog present only
  the final command tree and region terminology. They contain no public
  old-to-new migration mapping and no `eu-north1-c` region example.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run exact command-tree and option-manifest tests, zero-effect parser
  rejection, conversion/passive-allocation/rearm tests, region precedence and
  propagation tests, wizard/schema/default regressions, help and documentation
  stale-reference checks, retained internal rearm/systemd/package tests, Ruff,
  mypy, full unit and integration suites, wheel inspection, Markdown lint,
  security review, and diff-integrity checks. Live cloud or gateway execution
  remains a separate authorization boundary.
- Offline implementation evidence on 2026-08-26: the public tree contains 16
  executable leaves, both removed command names and every public `--zone`
  path fail during parsing, and `vm-ha` owns the separately confirmed passive
  allocation callback plus sanitized reserved-or-may-exist continuation. The
  focused CLI/conversion/region/VM-manager suites, all 1,638 unit tests,
  all 80 isolated integration tests, Ruff, mypy across 49 source files, and
  wheel construction passed. README, changelog, and requirements Markdown
  passed; the design document retains its pre-existing first-heading and long-
  line diagnostics. No cloud, SSH, gateway, route, or failover mutation ran.

#### Test Method

- Run exact command-tree and option-manifest tests, zero-effect parser
  rejection, conversion/passive-allocation/rearm tests, region precedence and
  propagation tests, wizard/schema/default regressions, help and documentation
  stale-reference checks, retained internal rearm/systemd/package tests, Ruff,
  mypy, full unit and integration suites, wheel inspection, Markdown lint,
  security review, and diff-integrity checks. Live cloud or gateway execution
  remains a separate authorization boundary.
- Offline implementation evidence on 2026-08-26: the public tree contains 16
  executable leaves, both removed command names and every public `--zone`
  path fail during parsing, and `vm-ha` owns the separately confirmed passive
  allocation callback plus sanitized reserved-or-may-exist continuation. The
  focused CLI/conversion/region/VM-manager suites, all 1,638 unit tests,
  all 80 isolated integration tests, Ruff, mypy across 49 source files, and
  wheel construction passed. README, changelog, and requirements Markdown
  passed; the design document retains its pre-existing first-heading and long-
  line diagnostics. No cloud, SSH, gateway, route, or failover mutation ran.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-015 -->

<!-- REQUIREMENT: REQ-016 status=active priority=P1 type=feature -->
### REQ-016: Destroy the exact configured gateway topology safely

#### User Story

Make `nebius-vpngw destroy` the single idempotent teardown workflow for ordinary and explicit VM-HA gateways. It must delete only the exact CLI-owned gateway Compute members, boot disks, managed route entries, member private allocations, and VM-HA shared private allocation, while retaining public allocations and VPC, subnet, and route-table containers. Constraints: Preserve the existing command name, options, default-No prompt, and `--yes/-y` automation. Serialize every topology through the canonical project/gateway writer lock, bind resources before effects, checkpoint every accepted cloud operation, fail closed on foreign routes or identity drift, and report success only after two agreeing fresh absence observations. Preserve local configuration, lifecycle receipts, trust material, peer-side resources, IAM, public allocations, and foreign routes. Do not add aliases, a separate legacy-cleanup mode, a second destroy command, or a dry-run/approval-digest mode. Non-goals: No additional non-goal was recorded in the canonical v1 record.

#### Acceptance Criteria

- Ordinary selection uses only configured member, disk, and allocation names;
  a gateway-name prefix never authorizes an extra resource. Existing ordinary
  routes are adopted only when their supported `vpngw-` management name and
  exact next-hop allocation agree; every other reference blocks allocation
  deletion and remains untouched.
- VM-HA route ownership accepts either complete exact HA authority labels or,
  when no HA authority label is present, the exact canonical ordinary
  `vpngw-<destination>` product name plus an exact lifecycle-bound private
  next-hop allocation. Partial or conflicting HA labels, noncanonical names,
  and foreign allocations remain blocking. This lets one destroy transaction
  remove product routes left across an ordinary-to-HA lifecycle without
  granting name-prefix authority over unrelated routes.
- VM-HA selection requires the current v4 lifecycle identity. A destruction
  successor may begin from provisioning, activating, active,
  removal-in-progress, or removed state. An accepted predecessor cloud
  operation is resolved first. Terminal success and terminal failure both
  release that predecessor checkpoint for a fresh exact inventory; any returned
  resource identity joins lifecycle authority, while an in-progress or
  unreadable outcome remains blocking. A guarded but unrecorded cloud effect
  also remains blocking. Missing lifecycle authority permits only a verified
  already-absent no-op.
- Member guest health, SSH reachability, controller readiness, and Compute
  runtime state are not teardown prerequisites. An already-stopped member is a
  sufficient fence; every other exact lifecycle-bound member is deleted by ID
  and proved absent before route deletion. Current, replacement, and retired
  Compute identities recorded by the lifecycle are all teardown targets after
  exact ID/name reproval. Managed routes are deleted before their private
  allocations; any remaining stopped Compute is deleted before its boot disk;
  the shared allocation is deleted only after members and route references are
  absent. Every private allocation must also be authoritatively detached at its
  delete submission boundary; a reassignment to any foreign Compute blocks
  teardown.
- Ordinary teardown uses an owner-controlled config-adjacent receipt. VM-HA
  teardown uses the canonical lifecycle transaction with approval kind
  `destruction` and terminal status `DESTROYED`. Reruns wait for accepted
  operations when lookup is supported; otherwise they replay only the exact
  checkpoint-bound idempotency key and require the same cloud operation
  identity. A submit, wait, or resume error completes an effect only when an
  exact authoritative reread already proves its postcondition. If a
  destroy-owned accepted operation is proven terminally failed and that
  postcondition is still false, its exact operation identity is durably
  superseded and the same effect advances to a new sequence-qualified
  idempotency key. In-progress or ambiguous operations remain bound and are
  never resubmitted. Reruns accept `NOT_FOUND` only for the exact bound identity
  and never delete a same-name replacement. A later
  ordinary recreation starts a new receipt operation for its new exact IDs; a
  later VM-HA apply starts a clean resource-empty provisioning successor from
  `DESTROYED` without inheriting deleted cloud identities. Each terminal
  observation must also prove every retained public allocation still has its
  exact identity and is stably detached, so a successful destroy leaves those
  allocations ready for immediate same-config apply. The clean provisioning
  successor must carry only those exact retained public-allocation bindings
  alongside its fresh route-target and credential bindings; deleted Compute,
  disk, private-allocation, shared-allocation, and route-runtime identities
  remain forbidden. Reuse must not depend on `external_ips` being explicit.
- Decline or EOF performs no destruction. Any failed stage exits nonzero,
  retains its checkpoint, prints no success message, projects a closed
  identity-free phase-specific destroy reason, and names a safe rerun.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Cover ordinary and VM-HA static/BGP admission, prompt behavior, exact scope,
  retention, route ownership, every lifecycle source state, operation resume,
  same-name identity replacement, canonical unlabeled product routes,
  noncanonical and partially labeled foreign routes, all Compute runtime
  states, present retired Compute, terminal failed predecessor operations,
  unsupported operation lookup replay, failure-after-effect postcondition
  recovery, terminal failed destroy-operation supersession for Compute and
  another resource class in both ordinary and VM-HA journals, absence of
  guest/controller readiness dependencies, failure at each ordered boundary,
  stable terminal verification,
  retained-public-allocation detachment, terminal rerun, and
  `DESTROYED`-to-apply convergence with explicit or implicit public IP
  selection, without config mutation or deleted resource bindings. Run
  focused destroy/lifecycle/route tests, Ruff, mypy, full unit and integration
  suites, release build, documentation alignment, security review, and diff
  integrity. Live cloud destruction requires separate action-specific
  authorization.

#### Test Method

- Cover ordinary and VM-HA static/BGP admission, prompt behavior, exact scope,
  retention, route ownership, every lifecycle source state, operation resume,
  same-name identity replacement, canonical unlabeled product routes,
  noncanonical and partially labeled foreign routes, all Compute runtime
  states, present retired Compute, terminal failed predecessor operations,
  unsupported operation lookup replay, failure-after-effect postcondition
  recovery, terminal failed destroy-operation supersession for Compute and
  another resource class in both ordinary and VM-HA journals, absence of
  guest/controller readiness dependencies, failure at each ordered boundary,
  stable terminal verification,
  retained-public-allocation detachment, terminal rerun, and
  `DESTROYED`-to-apply convergence with explicit or implicit public IP
  selection, without config mutation or deleted resource bindings. Run
  focused destroy/lifecycle/route tests, Ruff, mypy, full unit and integration
  suites, release build, documentation alignment, security review, and diff
  integrity. Live cloud destruction requires separate action-specific
  authorization.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: REQ-016 -->

<!-- REQUIREMENT: TI-REQ-001 status=active priority=P1 type=feature -->
### TI-REQ-001: Add opt-in two-node VM-level active/passive HA

#### User Story

Allow one gateway group to operate as exactly two stable VM members with one active owner and one passive candidate, independently of the existing per-tunnel active/passive roles. Constraints: VM-level HA must be explicit and default-disabled; omitting it must preserve supported configuration, CLI, allocation naming, planning, deployment, status, and route behavior for existing users. Non-goals: Active-active forwarding, ECMP, more than two HA members, legacy aliases, migration shims, or changes to existing tunnel-level HA semantics.

#### Acceptance Criteria

- A valid VM-HA configuration resolves two deterministic node identities and one shared cluster identity.
- After provisioning, each node receives one secret-free runtime binding that names the single shared secondary private-alias allocation, both authoritative Compute instance and NIC identities, peer endpoint and credential file references, and the route-runtime identity needed by the controller.
- Migrating one supported ordinary gateway retains its Compute instance, boot disk, NIC, primary private allocation, public allocation, and serving route attachments; it adds one passive member and one movable secondary private alias without rewriting either member's immutable primary address.
- Invalid member counts, ambiguous roles, or VM-HA and tunnel-role conflation fail before cloud or host mutation.
- Representative configurations without VM HA produce the same resolved plan and observable command behavior as before this feature.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused schema, template, and configuration-loader tests, including omitted-field golden regressions and invalid-topology cases.

#### Test Method

- Run focused schema, template, and configuration-loader tests, including omitted-field golden regressions and invalid-topology cases.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-001 -->

<!-- REQUIREMENT: TI-REQ-002 status=active priority=P1 type=feature -->
### TI-REQ-002: Apply one immutable cluster generation to both nodes

#### User Story

Compile canonical operator configuration into one cluster generation and digest, two node manifests, logical static-route and BGP-policy manifests, checksums, and node-local rendered artifacts. Constraints: Apply must stage and validate the passive before the active, commit each node durably and atomically, recover partial cross-node progress explicitly, and permit automatic failover only while both nodes report the same committed generation and required policy digests. Non-goals: Treating the active VM, observed kernel routes, or copied peer state as canonical configuration; introducing a second configuration owner.

#### Acceptance Criteria

- Each apply produces deterministic logical manifests and node-specific renderings from the same canonical input.
- A failure after one node commits leaves the serving generation unchanged, marks the newer node non-promotable, and recovers idempotently while retaining current, previous, and last-known-good generations.
- A generation becomes activation-eligible only after both nodes independently acknowledge the same committed generation and required policy digests.
- An ordinary-to-HA migration requires an exact desired-and-current-state plan plus interactive approval or `--approve-vm-ha-migration DIGEST`. The digest binds desired generation, topology, policies, resource names, mutations, rollback intent, retained cloud identities and revisions, shared-allocation state, and exact managed routes. It is recomputed immediately before durable intent; unchanged retries resume only the same checkpointed operation without creating duplicate resources.
- A durable operation-and-generation apply lock is installed and verified on both members before either HA runtime is activated. The lifecycle becomes `ACTIVE` only after exact node parity, active alias ownership, passive non-forwarding readiness, route cutover, and independent postcondition checks succeed.
- Each fenced apply declares the one cloud-observed current owner on that owner only. The declaration binds the exact lock operation, cluster, members, allocation, generation, and policy digests; the agent accepts it only while independent cloud observation confirms that same local owner. This declaration may establish ownership continuity for a promoted configured-passive owner across a generation change, but it grants no allocation movement or forwarding authority. A generation-current terminal receipt replaces it only after the ordinary owner, route, forwarding, no-lock, and no-pending-effect gates pass; malformed, foreign, mismatched, or orphaned declarations block safely.
- If the final `ACTIVE` persistence reports failure, apply re-reads the exact lifecycle record. It accepts only the exact `ACTIVE` successor plus fresh active/passive status proof, or the exact `ACTIVATING` predecessor followed by passive-first and then active exact-operation relocking and independent blocked/non-promotable proof. Missing, malformed, foreign, or other successor state is an unsafe blocker and is never reported as successful or safely locked.
- Generation or required-policy mismatch keeps the active serving, marks the passive non-promotable, and disables automatic failover until parity is restored.
- An explicitly authorized emergency active-only update also disables automatic failover until both nodes are synchronized.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run deterministic manifest, digest, passive-first apply, corruption, interrupted-write, and resynchronization tests using injected filesystem and node failures.

#### Test Method

- Run deterministic manifest, digest, passive-first apply, corruption, interrupted-write, and resynchronization tests using injected filesystem and node failures.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-002 -->

<!-- REQUIREMENT: TI-REQ-003 status=active priority=P1 type=feature -->
### TI-REQ-003: Prevent split brain with authoritative fencing and allocation ownership

#### User Story

Permit promotion only after Nebius Compute authoritatively reports the former owner stopped, the former secondary-alias attachment is absent, and the shared private alias allocation is independently confirmed on the candidate. Constraints: Peer heartbeat, local role, route state, transition journals, timeouts, and process failure are advisory only; ambiguous, unavailable, transitional, running, stopping, or error cloud states must block promotion. Non-goals: Consensus claims, lease authority derived only from the two VMs, simultaneous forwarding, or promotion based on loss of peer connectivity alone.

#### Acceptance Criteria

- Exactly one node may enable forwarding and owner-only reconciliation for each authoritative allocation snapshot.
- The enforced transition order is former owner stopped, former attachment absent, new attachment exact, ownership re-read exact, then candidate promotion.
- Ownership continuity is keyed by the exact attached candidate Compute resource revision read after assignment; allocation status alone and locally synthesized journals, hashes, or counters are not authoritative ownership epochs.
- Every HA member starts with forwarding and cluster tunnel initiation fail-closed; a boot, process restart, or automatic Compute recovery requires fresh role and cloud-ownership proof before the appropriate passive or active data plane is enabled.
- Every external side effect has durable before-and-after checkpoints and can be retried without skipping fencing or duplicating an unsafe mutation.
- Every provisioning effect declares an exhaustive normalized cloud-observation path set. Recovery accepts only the unchanged pre-state or that exact effect's permitted result; partial outcomes, unrelated drift, unstable rereads, unregistered effects, and extra changes fail closed before another mutation.
- Every accepted HA cloud mutation persists its exact cloud-operation identity before a bounded wait and resumes that operation after restart. The receipt clears only after the SDK reports terminal success; terminal failure or unavailable success status retains it and blocks. Request, authentication, retry, polling, and overall operation waits are finite and use the same replay-stable idempotency identity; ordinary non-HA SDK behavior is unchanged.
- Allocation transfer updates only the exact HA secondary alias and preserves both members' immutable primary addresses and all unrelated NIC fields and aliases.
- Fencing-critical SDK errors never enter permissive scaffold or best-effort fallback behavior.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run fake Compute and allocation tests for stopped, running, stopping, error, unavailable, permission, timeout, stale-read, foreign-owner, detached, partial-update, and crash-replay cases.

#### Test Method

- Run fake Compute and allocation tests for stopped, running, stopping, error, unavailable, permission, timeout, stale-read, foreign-owner, detached, partial-update, and crash-replay cases.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-003 -->

<!-- REQUIREMENT: TI-REQ-004 status=active priority=P1 type=feature -->
### TI-REQ-004: Reconcile routes only from authoritative desired and local learned state

#### User Story

Keep VPC route next hops bound to the shared private allocation while the verified owner reconciles static routes from the committed logical manifest and BGP routes from its own local FRR RIB. Constraints: A non-owner must not mutate managed VPC routes; takeover must preserve existing managed BGP routes during a configurable convergence window and resume withdrawal only after bounded stability observations. Non-goals: Copying kernel routes, FRR routes, or learned next hops from the active node to the passive; using the transition journal as route truth.

#### Acceptance Criteria

- Static logical-route digests match across nodes while node-local XFRM interface renderings may differ.
- BGP promotion readiness requires configured sessions, required prefixes, current import policy, and usable local XFRM next hops; optional-prefix parity is informational.
- Promotion preserves existing managed BGP routes during takeover hold-down, allows newly valid routes, and reconciles static routes from the committed manifest.
- Route completion is durable only when the runtime re-observes a success receipt bound to the exact controller operation ID and full current owner, allocation, ownership revision, generation, policy-digest, and ownership-incarnation context.
- Existing non-HA conflicting-next-hop rejection remains unchanged.
- Ordinary-to-HA keeps the existing serving routes unchanged while both nodes are staged and locked. Owner-gated reconciliation advances only after exact active authority; a failed managed-route replacement restores the exact removed route before the controller reports failure.
- Every managed-route delete, create, and restore persists a pending mutation before its request and uses a replay-stable idempotency identity. A timeout is resolved by authoritative reread and same-identity replay; restoration occurs only after terminal create failure and exact proof that the desired route is absent. Duplicate, stale, or conflicting outcomes remain blocked and operator-visible.
- The route-mutation v2 record stores exact rollback content, mutation phase, and accepted cloud-operation identity. Legacy v1 records remain readable without rewrite; recovery may upgrade a replacement only while the original route is still exactly observable, and blocks when neither original nor desired outcome can be proven.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run owner-gating, static-manifest, local-FRR, hold-down, stability, withdrawal, partial-failure, retry, and existing non-HA route-selection tests.

#### Test Method

- Run owner-gating, static-manifest, local-FRR, hold-down, stability, withdrawal, partial-failure, retry, and existing non-HA route-selection tests.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-004 -->

<!-- REQUIREMENT: TI-REQ-005 status=active priority=P1 type=feature -->
### TI-REQ-005: Recover a deterministic fail-closed HA controller

#### User Story

Implement one explicit controller for heartbeat evaluation, readiness, suspicion, fencing, ownership transfer, promotion, degradation, recovery, and manual failback. Constraints: Persist immutable revisions and transition checkpoints atomically; authenticate peer traffic; reject stale boot identities and heartbeat sequences; install a cold-start data-plane guard before strongSwan, FRR, or the gateway agent can use stale HA state; permit only deterministic node-local rendering and validation while that guard is blocked so clean members can establish readiness without enabling forwarding, tunnel initiation, firewall, route, allocation, or VPC effects; use bounded timers and injected clocks. Non-goals: Automatic failback, distributed consensus storage, Object Storage as a correctness dependency, or the append-only journal as ownership authority.

#### Acceptance Criteria

- The controller exposes normal, suspect, fencing, ownership-transfer, promoting, active, degraded, and blocked outcomes with explicit prerequisites.
- On every boot or restart, the controller begins behind the cold-start guard, re-reads Compute and allocation ownership, and enables only the data-plane mode justified by fresh authoritative state.
- On a clean two-node bootstrap, both members can materialize and validate the current generation behind the blocked guard without depending on promotion readiness; the passive and any node without fresh ownership proof remain non-forwarding and effect-free.
- Automatic failover requires generation parity plus required static, BGP, XFRM, service-health, and cloud-ownership readiness.
- Restart at any checkpoint reconstructs the next safe action from committed local state and current cloud truth without enabling forwarding early.
- Controller checkpoint v2 durably binds each ownership transfer to the attach action, allocation, former and candidate nodes, generation and policy digests, ownership incarnation, and strictly advancing pre/post candidate Compute revisions. V1 checkpoints remain readable; legacy in-flight states without sufficient continuity stay guarded and require exact detach/reattach reproof, while a stable pre-existing active baseline remains adoptable without fabricating historical transfer proof.
- Authenticated heartbeats report role, owner observation, generation, policy digests, service health, route readiness, and promotion readiness without carrying secrets.
- Every forwarding writer, route timer, agent startup path, and service dependency remains behind the current-boot guard until the controller durably records and exposes the justified data-plane mode; controller stop, failure, or stale readiness restores the guard.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run table-driven state-machine, stale-heartbeat, boot-change, timeout-boundary, dual-suspicion, filesystem-fault, cloud-failure, route-failure, and restart tests.

#### Test Method

- Run table-driven state-machine, stale-heartbeat, boot-change, timeout-boundary, dual-suspicion, filesystem-fault, cloud-failure, route-failure, and restart tests.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-005 -->

<!-- REQUIREMENT: TI-REQ-006 status=active priority=P1 type=feature -->
### TI-REQ-006: Provide safe operations, security, and offline proof

#### User Story

Expose generation parity, observed owner, promotion readiness, fencing progress, degraded reasons, safe operator guidance, and manual failback through the existing operator workflow when VM HA is enabled. Constraints: Use the narrowest current Nebius permission boundary that covers the required Compute ownership and VPC allocation/route mutations, keep secrets out of manifests, journals, status, and logs, package all required services, and perform no live cloud mutation without a separately approved non-production trial. Every operator-command and staging SSH path must use one exact operator-configured or REQ-013 product-managed host-key trust source that is validated before any cloud mutation; trust-on-first-use, disabled host authentication, and permissive fallback are unsupported. Non-goals: Renaming or silently changing existing non-HA commands, proactive preference-based automatic failback, production validation, or claiming live readiness from offline tests alone.

#### Acceptance Criteria

- Non-HA command syntax, defaults, output meaning, and exit behavior remain supported.
- When VM HA is omitted or disabled and no valid lifecycle record exists, ordinary apply performs no HA-specific Compute, VPC, allocation, SSH, or runtime discovery. Permission or availability failures in those HA-only APIs therefore cannot block a never-HA customer.
- The first ordinary-to-HA apply presents a mutation and rollback preview, requires confirmation or the exact shown `--approve-vm-ha-migration DIGEST`, and keeps the existing gateway and routes serving until the new pair is independently ready for reversible cutover. An interrupted no-lifecycle/two-VM topology requires the separately domain-bound `--recover-vm-ha-migration DIGEST`; the migration and recovery digests are never interchangeable. `--dry-run` produces the same plan without lifecycle or cloud mutation.
- Removing explicit VM HA first selects the requested service-account credential when configured, or the operator credential otherwise, so a default-disabled ordinary apply never requires broader operator Compute or VPC read authority merely to prove that no HA teardown is needed.
- Current managed HA state is selected by one secret-free v4 lifecycle transaction whose whole-record digest binds schema, monotonic revision, predecessor, status, project, gateway, approval and operation identities, effect checkpoints, path-level observation guards, accepted cloud-operation identity, route runtime, allocation, and both complete member identities. Writes are fsynced, reread, and compare-and-swapped under a canonical project-and-gateway apply lock. V2 and v3 records remain readable without rewrite on read or no-op; a quiescent approved transaction gains a v4 successor only before a new mutation, while a pending legacy effect blocks until its exact outcome is resolved. Activation persists `ACTIVATING` after exact provisioning and advances to `ACTIVE` only after active ownership/routes and passive unlocked non-forwarding are independently proven; removal advances `ACTIVE` or `ACTIVATING` to `REMOVAL_IN_PROGRESS`. A verified `REMOVED` tombstone makes later ordinary applies teardown-free and idempotent.
- An unchanged v4 `ACTIVATING` retry validates stable authoritative cloud and runtime identities and resumes only the host activation workflow. It does not re-enter VM provisioning, finalize provisioning again, or write a second `ACTIVATING` transition.
- If recovery outside the product workflow has returned an interrupted v4 `ACTIVATING` transaction to the exact configured-active cloud owner, ordinary retry remains blocked until the operator accepts a separate `--recover-vm-ha-migration DIGEST` preview. That approval may append one replacement `PROVISIONING` successor only when the desired generation and every project, gateway, cluster, allocation, member, disk, NIC, subnet, primary/public address, role, runtime, and route-target identity remain exact; Compute revisions have only advanced; the configured active alone owns the exact shared alias; no cloud effect or accepted operation is pending; and only host activation effects remain incomplete. The successor then resumes the canonical passive-first ensure, stage, lock, and activation workflow. An ordinary same-observation retry may durably rewind an incomplete later host-only activation effect solely to insert the newly required exact-lock-bound owner-adoption declaration; it never marks the interrupted effect complete, changes cloud bindings, clears a remote lock, or supersedes a cloud effect, and it replays that host verification after adoption. Any other drift or pending cloud work remains blocked.
- A v4 `PROVISIONING` transaction whose newly created passive cannot pass the SSH/bootstrap gate may expose one explicit passive-replacement preview and a domain-separated `--replace-failed-vm-ha-passive DIGEST` approval. The replacement must append exact intent, accepted-operation, retirement, and replacement receipts to the existing transaction; retain the active Compute, disk, NIC, revision, forwarding state, both members' primary/public allocations, the shared allocation and owner, route targets, desired generation, and original migration approval; delete only the receipt-bound passive Compute and task-created boot disk; and resume the ordinary passive-first provisioning path with the replacement identity. A stale or foreign digest, completed staging or activation effect, non-passive target, ambiguous resource, active/shared-allocation drift, or unrelated cloud change stops before mutation. Generic `--recreate-gw` is never this recovery path.
- The failed-passive replacement preview additionally requires a cycle-specific completed bootstrap-timeout effect written only after final health reads prove the active ready and only the passive unready. A resumed `PROVISIONING` checkpoint without that evidence must reuse the exact existing passive and continue the normal readiness wait.
- During ordinary route cutover, initial HA label synchronization may be deferred only for the exact migration-approval-bound predecessor whose identity, target, prefix, revision, and old ordinary-allocation next hop all match the lifecycle record. The mutation closure must reread and consume the ledger persisted by same-cycle migration adoption. The verified route plan must create the shared-allocation successor and then synchronize its complete authority labels before committing route state; every unrelated or partial mismatch fails closed.
- Every VM-HA Compute create accepts only the submitted boot disk, single NIC, project, gateway subnet, primary/public allocations, and pre-existing alias set; unrelated alias or resource substitution inside the nominal create footprint fails closed. Every HA-only Compute, VPC, allocation, operation-resume, and route-target observation uses the finite request/auth/retry policy, and long-running operation polling owns only the SDK poll-specific timeout and retry arguments.
- Direct explicit VM-HA `apply` suppresses only Nebius SDK diagnostics that
  announce an internal retry. If a bounded cloud request ultimately fails with
  typed `DEADLINE_EXCEEDED`, the command exits nonzero with one sanitized
  timeout and `vm-ha` recovery guidance instead of a traceback. It does not
  reset the request deadline, add another retry loop, change cloud authority,
  or alter ordinary non-HA errors and output.
- Apply passes an explicitly supplied `NEBIUS_IAM_TOKEN` directly to every
  manager SDK client and never replaces it with broader ambient credentials.
  Without an explicit token, apply uses one SDK-native bearer backed by the
  supported current-profile `nebius iam get-access-token` command. Token
  acquisition is bounded, non-interactive, ignores an ambient
  `NEBIUS_IAM_TOKEN`, shares the acquired token across one manager SDK, and
  permits at most one forced refresh for a request rejected as
  `UNAUTHENTICATED`. The CLI profile may still select SDK endpoint context but
  is never asked to resolve credentials. No acquired token is exported into
  process environment state. Typed `UNAUTHENTICATED` during serialized VM-HA
  apply exits nonzero with redacted refresh guidance and never becomes
  member-absence or generic topology-classification evidence.
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
- On the current Nebius IAM surface, VM-HA derives the dedicated service-account and group name `<gateway>-vm-ha`. That service account is the group's sole member and the group has exactly one project-scoped `editor` access permit. Apply also owns exactly one `<gateway>-vm-ha-runtime-key` non-expiring RSA-4096/RS256 authorized key. This is the minimum available permission boundary that covers Compute ownership transfer and VPC allocation/route mutation; unsupported service-specific editor roles and permits scoped directly to VPC route-table or network resources are rejected.
- VM-HA rejects `apply --sa`; ordinary gateways retain that option. Before approval, read-only inventory binds each deterministic labeled account, group, membership, permit, and authorized key to an exact create or reuse action and includes every observed reused identity in the plan digest. Reuse is complete and mutation-free; incomplete unjournaled state fails closed. After approval, current generated Nebius SDK resource clients execute only those planned creates, wait for each long-running operation to finish, and reread the exact graph. Journal-bound partial creation may resume, and a validated final credential may clean either sole enrollment residue after an interrupted cleanup. Missing impersonation, source drift, ambiguous or foreign identity, ownership-label drift, extra member/permit/key, unsupported role, or enrollment/readback failure stops before product cloud mutation and never silently falls back to a different identity.
- Offline two-node tests prove no forwarding or VPC-route mutation occurs before authoritative fencing and exact allocation ownership.
- The ordinary automated CI path selects the composed clean-bootstrap, passive non-forwarding, SSH trust preflight, and host-key mismatch regressions rather than leaving them manual-only.
- The ordinary CI lint gate runs the canonical all-source mypy check exactly once, and each mutually exclusive workflow lane builds the release wheel exactly once.
- A later live-ready claim requires a separately authorized non-production trial with independently observed cloud, allocation, forwarding, and route postconditions.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused CLI, IAM, systemd, packaging, build, release, security, and deterministic composed failover tests, followed by the full unit and integration suites.
- Cover a nested typed exhausted deadline, retry-diagnostic suppression and
  filter restoration, explicit-token and renewable CLI-bearer SDK selection,
  bounded acquisition and refresh, ambient-token isolation, typed
  authentication redaction, and unchanged ordinary non-HA exception
  propagation.

#### Test Method

- Run focused CLI, IAM, systemd, packaging, build, release, security, and deterministic composed failover tests, followed by the full unit and integration suites.
- Cover a nested typed exhausted deadline, retry-diagnostic suppression and
  filter restoration, explicit-token and renewable CLI-bearer SDK selection,
  bounded acquisition and refresh, ambient-token isolation, typed
  authentication redaction, and unchanged ordinary non-HA exception
  propagation.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-006 -->

<!-- REQUIREMENT: TI-REQ-007 status=active priority=P1 type=feature -->
### TI-REQ-007: Live-validate the supported GCP multi-VM HA topology

#### User Story

Exercise the explicit two-node VM-HA product path against the authorized non-production GCP and Nebius projects, repair causal product defects, and independently prove steady state, automatic failover, and manual failback without creating a Nebius VPC ECMP data plane. Constraints: Freeze the candidate artifact and declaration before each trial; keep GCP fixture setup, environment recovery, product execution, and independent verification separate; retain one regional GCP HA VPN gateway and one Cloud Router for the target topology; preserve supported legacy single-peer helper behavior; and never expose tunnel secrets or credential material in commands, files, logs, status, or evidence. Non-goals: Production validation, active-active Nebius forwarding, proactive preference-based automatic failback, treating fixture repair as product proof, deleting an unclassified GCP peer resource, or claiming a clean trial from a run whose product-owned transition was pre-satisfied externally.

#### Acceptance Criteria

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

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run deterministic fake-`gcloud` helper tests, focused VM-HA CLI/status tests, all repository unit and integration gates, and an opt-in live runbook with independently captured GCP, Nebius Compute/allocation/route, host service/forwarding, and traffic postconditions.

#### Test Method

- Run deterministic fake-`gcloud` helper tests, focused VM-HA CLI/status tests, all repository unit and integration gates, and an opt-in live runbook with independently captured GCP, Nebius Compute/allocation/route, host service/forwarding, and traffic postconditions.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-007 -->

<!-- REQUIREMENT: TI-REQ-008 status=active priority=P1 type=feature -->
### TI-REQ-008: Repair fresh unhealthy owners before VM transfer

#### User Story

Distinguish a fresh unhealthy owner heartbeat from a missing heartbeat and allow exactly one bounded node-local repair attempt when the current owner, generation, guard, route authority, and shared-allocation ownership remain exact. Constraints: A repairable full data-plane outage receives one absolute five-second budget with one second reserved for verified local fencing; a repair attempt is bound to the exact cluster, allocation, owner, ownership revision, generation, boot, and first failure fingerprint; no command, new failure, process restart, or transient healthy sample may extend or reset that deadline. The VM-HA controller is the only repair writer in VM-HA mode. Heartbeat and repair state remain advisory, every wait requires an independent fresh cloud read proving the same owner and ownership revision, candidate promotion readiness remains mandatory, and automatic transfer still requires the former Compute owner to be authoritatively `Stopped` before alias transfer, route reconciliation, or forwarding. Non-goals: Repeated self-healing loops, treating tunnel count as path coverage, repairing cloud allocations or VPC routes, promoting to an unready candidate, using repair state as ownership authority, weakening missing-heartbeat fencing, or changing non-HA tunnel monitoring.

#### Acceptance Criteria

- Health classification separates redundant-path degradation, repairable full outage, unsafe local authority, and unreachable peer outcomes across StrongSwan/IPsec, FRR/BGP, XFRM, static routes, forwarding consistency, current route receipt, current-boot guard, durable state, and cloud ownership.
- One failed tunnel or BGP neighbor remains a tunnel-level event only when every required prefix and traffic selector still has an equivalent usable path. Loss of the sole usable path for any required prefix is a full outage.
- A repairable outage persists one idempotent attempt before executing the smallest currently supported node-local action: FRR reload or restart for a BGP-only failure, StrongSwan and FRR restart followed by StrongSwan reload for a service or XFRM failure, or gateway-agent reload or restart for a remaining local static-materialization failure. Loss of forwarding is already a physical fence and follows the canonical owner-verified passive-materialization and active-enable path. Repair never stops a VM, moves an allocation, or mutates a VPC route.
- Repair commands receive the remaining monotonic budget, are individually bounded, and stop early enough to disable and verify kernel forwarding by the absolute deadline. The emergency guard bypasses the ordinary routing lock and physically disables forwarding before best-effort persistence. The ordinary systemd stop path retains its existing forwarding guard; a short watchdog that could terminate legitimate long-running cloud effects is not part of this repair boundary.
- Successful repair requires two complete fresh healthy observations. The consumed attempt resets only after sixty seconds of continuous health or a new authoritative ownership incarnation; recurrence, fingerprint churn, or an added failure before then is treated as flapping and cannot obtain another repair window.
- A fresh authenticated heartbeat that reports unhealthy service, route, or promotion readiness starts the existing passive suspicion window while the exact owner uses the matching five-second local budget. A fresh healthy heartbeat cancels suspicion; no repair report, timeout extension, or additional takeover authority crosses the wire. Missing, stale, mixed-version, changed-owner, ambiguous-cloud, or retired-boot evidence receives no grace.
- Repair exhaustion never authorizes alias transfer directly. The owner disables forwarding by the reserved deadline, while a ready passive follows the existing strict Compute-stop, detach, attach, ownership-reread, route-receipt, and forwarding sequence after its suspicion window. If the candidate is not ready, no transfer occurs; the exact owner remains degraded or blocked and the repair attempt stays consumed.
- Structured effect events expose secret-free repair operation, owner revision, failure fingerprint, healthy-observation count, remaining budget, transition state, and action duration. Public VM-HA status exposes only the aggregate title and per-member role, mTLS, and readiness projection defined by REQ-007.
- VM-HA peers use the clean-break protocol-v2 heartbeat that binds the mTLS epoch to the presented leaf. Mixed protocol versions fail closed before transfer admission; local repair still grants no extra remote grace or ownership authority.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run deterministic fault-matrix, exact-deadline, emergency-fence, checkpoint-migration, crash-replay, flapping, prefix-coverage, candidate-readiness, no-cloud-effect, and two-node transfer tests with injected clocks and bounded command fakes. Live acceptance separately stops FRR and StrongSwan, removes redundant and sole required paths, and disables forwarding while recording bidirectional loss, repair duration, fencing order, authoritative ownership, and whether VM transfer occurred. A controller-hang trial remains outside this boundary until a watchdog can distinguish local repair work from legitimate long-running cloud effects.

#### Test Method

- Run deterministic fault-matrix, exact-deadline, emergency-fence, checkpoint-migration, crash-replay, flapping, prefix-coverage, candidate-readiness, no-cloud-effect, and two-node transfer tests with injected clocks and bounded command fakes. Live acceptance separately stops FRR and StrongSwan, removes redundant and sole required paths, and disables forwarding while recording bidirectional loss, repair duration, fencing order, authoritative ownership, and whether VM transfer occurred. A controller-hang trial remains outside this boundary until a watchdog can distinguish local repair work from legitimate long-running cloud effects.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-008 -->

<!-- REQUIREMENT: TI-REQ-009 status=active priority=P1 type=feature -->
### TI-REQ-009: Expose a safe planned VM ownership failover

#### User Story

Provide an explicit `failover vm` operator command that moves VM-HA ownership from the configured active member to the configured passive member through the same fenced controller path as automatic failover. Constraints: The command is distinct from tunnel-level `failover tunnel`; requires the exact active lifecycle, configured member identities, a running configured-active exact allocation owner, and a running alias-free configured passive; targets only the configured passive; and may bypass only fresh-peer suppression and the automatic suspicion delay. Generation parity, apply-lock, candidate readiness, former-Compute-`Stopped`, allocation detach/attach, ownership re-read, route reconciliation, and forwarding gates remain mandatory. Non-goals: Direct allocation, route, forwarding, or Compute mutation from the request path; role reversal in configuration; proactive preference-based automatic failback; or changing omitted, disabled, non-HA, or tunnel-HA behavior.

#### Acceptance Criteria

- The operator preflight is read-only and fails before request submission on lifecycle, member, role, Compute-state, allocation-owner, attachment, SSH-trust, or generation mismatch.
- The configured passive persists one strict cluster-, node-, role-, and generation-bound request. A conflicting failback request or a request on the configured active fails closed.
- The controller consumes the request only after the configured passive is the exact promoted owner and retains the canonical former-owner stop, allocation transfer, ownership confirmation, route, and forwarding order.
- Deterministic controller and composed two-node tests prove that a healthy peer does not suppress the planned request and that every ordinary promotion gate remains unchanged.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused request-schema, role-confusion, stale-identity, request-consumption, controller, CLI preflight, and composed failover tests, then the full unit and integration suites and a clean live planned-failover trial with bidirectional workload probes and independent cloud, route, and forwarding postconditions.

#### Test Method

- Run focused request-schema, role-confusion, stale-identity, request-consumption, controller, CLI preflight, and composed failover tests, then the full unit and integration suites and a clean live planned-failover trial with bidirectional workload probes and independent cloud, route, and forwarding postconditions.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-009 -->

<!-- REQUIREMENT: TI-REQ-010 status=superseded priority=P1 type=feature -->
### TI-REQ-010: Restore an exact stopped passive standby without promotion

#### User Story

Provide an explicit `vm-ha-rearm` operator command that restores the configured passive Compute to a verified running, non-owner, non-forwarding standby after a fenced transfer leaves it stopped. Constraints: Require the exact active lifecycle and member bindings, the configured active as the running exact allocation owner, and the configured passive to remain alias-free. Start only the stopped configured passive with a resource-revision-bound idempotency key, continuously re-prove the unchanged owner during startup, require pinned SSH, and finish only when the passive controller reports `normal`/`passive`, non-owner authority, and no apply lock. Non-goals: Moving the shared allocation, changing routes, enabling forwarding, accepting a foreign owner, re-arming an ambiguous topology, or using ordinary apply as an out-of-band recovery shortcut.

#### Acceptance Criteria

- Already-running safe passive members are verified without a Compute start; stopped safe passive members receive exactly one stable start operation for their current resource revision.
- Any owner, attachment, Compute-state, lifecycle, identity, or SSH-trust drift aborts before further progress and never mutates allocation, route, or forwarding state.
- A successful command leaves the configured active as the exact owner and the configured passive running in `normal` controller state with passive data-plane mode, no local ownership, and no apply lock.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused exact-owner, foreign-owner, stopped/running passive, stable-idempotency, pinned-SSH, and terminal-status tests, then prove the command live after failback and re-check bidirectional traffic and authoritative ownership.

#### Test Method

- Run focused exact-owner, foreign-owner, stopped/running passive, stable-idempotency, pinned-SSH, and terminal-status tests, then prove the command live after failback and re-check bidirectional traffic and authoritative ownership.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-010 -->

<!-- REQUIREMENT: TI-REQ-011 status=active priority=P1 type=feature -->
### TI-REQ-011: Restore role-neutral warm standby after every committed promotion

#### User Story

Unify planned and automatic VM ownership transfer around typed, durable intent, then automatically restore the exact non-owner Compute as a guarded warm standby after a terminally committed promotion. Constraints: Preserve the public `failover vm`, `failback vm`, and `vm-ha` commands, both role-bound transfer-request schemas, deployment lifecycle v4, controller checkpoint v4, strict former-owner `Stopped` fencing, and the single canonical allocation/route/forwarding cutover engine. Replace heartbeat v1 with the clean-slate epoch-bound protocol v2 required by REQ-008 and reject mixed versions. Automatic owner-loss takeover is role-neutral, but never moves a healthy owner merely to restore configured-role preference. Rearm is enabled whenever explicit VM HA is enabled, has no YAML setting, is independently inhibitable, and is the only Compute-start writer. Private transfer lineage, promotion, rearm, standby, and mTLS records are strict and separately versioned. Non-goals: Keeping the former owner running during allocation transfer, proactive or preference-driven automatic return to the configured active, changing detection or repair windows, adding active-active behavior, granting the rearm service stop/allocation/route/firewall/forwarding authority, inferring promotion from cloud topology, exposing resource identities, or adding a metrics exporter.

#### Acceptance Criteria

- Controller admission resolves exactly one `planned-failover`, `planned-failback`, or `automatic-failover` intent with trigger-specific request validation. Planned intents remain configured-role-bound. Automatic admission is role-neutral: whichever healthy exact non-owner detects that the current exact owner is stale or stopped may become the candidate after the unchanged suspicion, parity, readiness, and fencing gates. Automatic suspicion remains cancellable before the first accepted external effect; afterward the exact former-owner and candidate lineage is sticky until terminal recovery even if the initiating suspicion disappears. Repeating the same planned command after that first effect may reattach only by reusing the exact identity-valid request that predates and matches the same typed lineage; it never republishes the request or changes its fingerprint. Request publication and transfer-effect dispatch share the VM-HA writer lock, and dispatch revalidates the observed typed intent and effect-started state under that lock; a change after the controller decision skips the stale dispatch and forces a fresh observation before checkpoint replay. A missing or later request, an opposite or automatic intent, or invalid, stale, or foreign lineage evidence remains fail-closed.
- Every transfer uses the unchanged ordered engine: stop former owner, detach its shared alias, attach the candidate, authoritatively reread exact ownership, reconcile routes, and enable forwarding. Existing checkpoint, pending-action, and transfer-continuity evidence remain execution authority.
- Every controller and rearm Compute mutation keeps the complete internal action operation ID as checkpoint and accepted-operation identity, but the sole Nebius SDK metadata boundary must encode it as a provider-valid idempotency key. An already valid `[A-Za-z0-9-]+` ID remains unchanged; an ID containing any other character becomes the deterministic lowercase SHA-256 hex digest of the complete UTF-8 ID. The same logical action therefore retains one replay-stable provider key without weakening or rewriting durable controller identity, and no idempotency header is added when no action operation ID exists.
- A promotion receipt becomes durable only after exact candidate ownership, route receipt, active forwarding, matching transfer-request consumption when applicable, no pending effect, and no apply lock are all durable. Rearm never infers terminal promotion from topology alone.
- An independent systemd rearm service runs on both members without becoming a `Requires=` dependency of the safety controller. It acts only on the exact stable owner with a matching promotion receipt and has read/start capability only. Before its synchronous idempotent Compute-start call, it durably publishes the receipt-bound `starting` checkpoint and status so observers remain truthful while the provider operation is in flight; crash replay retains the same logical operation identity.
- The rearm service submits at most one idempotent logical start for each promotion receipt and stopped-resource revision, resumes an accepted cloud operation after crash, prevents retry storms, and adopts an already-running alias-free target without mutation. Unknown, stopping, error, ownership drift, apply/removal activity, ambiguous operations, corrupt evidence, or service inhibition block safely.
- Repeating planned failover or failback when the requested role is already the exact healthy owner succeeds as an explicit identity-free no-op, writes no transfer request, and leaves forwarding unchanged. An unhealthy or ambiguous same-owner observation fails without mutation.
- Planned failover and failback are two-milestone synchronous operator workflows: safely committed service cutover, then restored VM redundancy. Preparation, request-to-cutover observation, and post-cutover redundancy restoration receive independent fixed internal budgets of 300, 600, and 300 seconds respectively; time consumed by an earlier phase never reduces a later phase's budget, no observation or progress transition resets or extends a deadline, and every human progress and success line retains one monotonic total elapsed time from command start. Both commands expose one optional `--output-format text|json` selector whose default is `text`; invalid values fail during CLI parsing before authentication or any product effect. For a real transfer, default text mode suppresses the request record, emits the exact role-specific start message plus phase-labelled bounded elapsed-time progress to stderr, and does not print its terminal success message until both milestones are independently proven. TI-REQ-013 refines progress to exact lineage-bound phases when current evidence is available; otherwise it preserves `<Operation> in progress: <elapsed>s elapsed, cutting over...` before cutover and uses independently observed rearm and Compute state to report `starting former owner as standby...` or `waiting for standby readiness...` after cutover, with `restoring standby...` only when no more specific current restoration evidence is available. When the current exact transfer record reports `effect-failed` for the same operation that the controller checkpoint still owns as pending, the CLI reports that forwarding remains fenced and continues its existing bounded wait while the controller retries that durable operation; the CLI never submits the retry, restarts a service, invokes rearm, or treats progress as transfer authority. Missing, malformed, foreign-lineage, stale-boot, or pending-operation-mismatched retry evidence fails immediately with identity-free status and controller-journal guidance. If the exact controller retry remains pending through the cutover deadline, the command exits nonzero with its safe phase and the same guidance. An already-owner no-op in default text mode emits exactly `Failover not needed: the passive VM already owns the gateway.` or `Failback not needed: the active VM already owns the gateway.` to stderr and submits no request. Explicit `--output-format json` is the automation migration path and preserves the former sorted request or already-owner record bytes on stdout, including the request record when later observation fails; human start, progress, success, and failure output remains on stderr. Cutover requires a newly committed exact promotion receipt that proves the former Compute owner was `Stopped` and alias-free before transfer, plus fresh active status and cloud evidence that admit the former member already being automatically rearmed; the candidate exclusively owns the shared allocation, route reconciliation matches the current generation and ownership epoch, forwarding is active only on that owner, and no apply lock or controller effect is pending. Redundancy restoration then requires the former owner to be `Running` and alias-free, the current owner to report terminal `running` rearm with `redundancy_ready`, the returned member to report fresh guarded standby readiness, and a final stable agent/cloud reread. `failover vm` names the configured passive target and ends with `Failover to the passive VM is done successfully in <elapsed>s.`; `failback vm` names the configured active target and uses the corresponding active-VM text. A cutover timeout exits nonzero without a success message. A post-cutover restoration timeout or blocked rearm also exits nonzero, but explicitly reports cutover elapsed time, restoration elapsed time, and total elapsed time, states that standby restoration did not finish, and names `vm-ha` as the supported retry. Request schemas, controller behavior, exit status, and all fencing and terminal redundancy gates remain unchanged.
- Read-only terminal agent timeouts, transient SSH/status failures, and typed
  retryable or ambiguous cloud-read failures are observation loss only: the
  CLI retries them inside the current cutover or restoration phase deadline
  without republishing a request or driving any controller, rearm, cloud,
  route, or forwarding effect. Permanent or malformed agent/cloud evidence and
  unexplained well-formed terminal drift still fail immediately. A validated
  target record may instead continue the bounded cutover observation when it
  proves the submitted request fingerprint, current runtime and member
  identities, a non-committed promotion, no apply lock, and only the closed
  controller-owned ownership-reproof path: candidate self-fencing, passive
  re-entry, detach-for-reproof, attach, ownership confirmation, tunnel
  preparation when required, route reconciliation, or forwarding enablement.
  That evidence authorizes observation only; it cannot complete the transfer,
  drive an effect, or change the fixed deadline. A terminal-looking agent read
  followed by cloud or final-agent disagreement continues only when one fresh
  validated target read proves that exact reproof path. If exact reproof is
  still the latest blocker through the cutover deadline, the command reports
  that cutover is not yet verified and may still be completing instead of
  claiming an operation failure. If the latest blocking
  observation remains unavailable through the cutover deadline, the command
  exits nonzero and reports that the outcome is not yet verified and that the
  controller may still be completing the transfer; after proven cutover it
  instead reports that standby restoration is not yet verified and may still
  be continuing in the background. Neither path exposes the underlying
  exception, claims product failure, or emits success, and an observation that
  completes after its phase deadline cannot authorize a cutover or
  terminal-success line.
- An explicit rearm retry request authorizes at most one logical start attempt. Its exact request identity is durably consumed before the cloud call; service restart may resume only the same accepted operation, while a definite failure requires a new request. A retained accepted-operation journal is cleared only after exact operation-status success, including when it belongs to the matching checkpoint from an earlier promotion; foreign, unbound, failed, or unavailable operation evidence remains blocked.
- Rearm, retry submission, apply-lock installation and removal, and VM-HA removal inhibition share one writer lock. The enabled marker and apply lock are rechecked immediately before Compute start. Removal installs and proves the same exact-operation gate on both members, stops both mutation services everywhere before any deactivation, checkpoints that barrier for crash-safe deactivation-only replay, retains the stable root-only lock inode and state directory, and clears every other rearm state entry while still holding that lock.
- Public `vm-ha` delegates role-neutral restoration to the internal rearm engine, which submits an explicit retry request for whichever exact member is currently the non-owner and never starts Compute directly. Planned failover and failback share one preparation path that requests rearm for a stopped target, observes a starting target, waits for fresh `standby_ready` when running, then reproves ownership and readiness immediately before submitting the unchanged role-bound transfer request.
- Rearm is not a general setup reconciler. Missing SSH trust, stale or mismatched deployed generation, local route-hygiene drift on an already-running member, inexact cloud route authority, allocation drift, firewall drift, or forwarding drift fails outside its start-only authority and must be reconciled through the owning setup or apply workflow.
- Planned preparation uses one bounded deadline across Compute startup, pinned SSH, and repeated same-target standby-status reads. Valid not-yet-ready evidence may converge; malformed, mixed-identity, generation, digest, owner, or alias evidence fails immediately. The final request follows a fresh cloud reread and target-readiness reread.
- Preparation and remote-agent failures cross one closed public-command boundary. Text and JSON invocations emit one bounded identity-free stderr diagnostic without raw SSH, provider, remote-process, traceback, token, or local-variable content; JSON stdout remains empty before request publication and retains only the already-published stable request record after publication.
- Fresh `standby_ready` evidence binds the current boot guard, exact generation and required digests, passive data plane, running non-owner and alias-free state, route/XFRM readiness, and absence of apply locks or pending effects. Mixed-version, stale, malformed, or identity-drifting evidence fails closed.
- Heartbeat v2 preserves the existing health and `promotion_ready` semantics while adding the authenticated mTLS epoch. `promotion_ready` still represents either an exact active owner with active data plane or an exact alias-free non-owner with passive data plane, both with current service and route readiness; automatic promotion retains its independent readiness and fencing gates.
- Operator status integrates `redundancy_ready`, standby readiness reasons, rearm phase, inhibition or failure reason, and preparation, cutover, and redundancy-restoration durations into one concise VM-HA section without changing existing status meanings or revealing cloud resource identities. The controller and cold-start guard units retain writable current-boot runtime state through controller `ExecStopPost`, and VM-HA service ordering must remain acyclic while still starting the guard and controller before FRR can advertise routes.
- Automatic owner-loss takeover works in either ownership direction and retains heartbeat failure, bounded local repair, suspicion expiry, parity/readiness, and fencing admission. It never transfers away from a healthy current owner. Planned failback remains the only preference-driven ownership return to the configured active.
- The last accepted mTLS-authenticated peer heartbeat is persisted with a checksum and is valid after controller-process restart only when the durable anti-replay boundary still covers its exact peer boot and sequence. Restarted controllers may use that record as stale generation/digest parity evidence, but never as fresh peer liveness, health, readiness, or cancellation evidence. Missing, corrupt, wrong-peer, retired-boot, or replay-inconsistent records fail closed before transfer.
- TI-REQ-017 refines post-promotion restoration: when the cluster-wide policy is
  committed enabled, every planned or automatic transfer must capture exact
  replay-protected agreement before its first effect and carry a single-use
  authorization through committed cutover, Compute start, and fresh standby
  readiness. A stopped former owner's expired heartbeat after cutover cannot
  invalidate that exact authorization.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run unit and composed tests for trigger-role confusion, pre-effect cancellation, post-effect stickiness, exact same-request reattachment without republication, request-versus-first-effect serialization, all transfer crash points, promotion-receipt ordering, both ownership directions, synchronous terminal waiting, exact default-text stderr progress and elapsed-time output with empty stdout, exact no-op sentences, explicit-JSON request/failure/no-op byte parity, invalid-format pre-effect rejection, timeout and cloud/status drift, direct/automatic rearm concurrency, one-writer serialization, idempotency, accepted-operation replay, owner drift, transitional/error/flapping state, explicit retry, inhibition, corrupt state, apply/removal races, service isolation, mixed-version fail-closed behavior, checkpoint v1-v4 reads, packaging, and rollback with the rearm unit stopped. Follow with Ruff, mypy, full unit/integration, CLI/help, systemd, wheel, and changed-scope alignment gates.
- Cover one-shot and persistent terminal observation loss at every cutover and
  restoration agent/cloud read, permanent evidence and well-formed drift
  counterexamples, JSON stdout stability, redaction, and post-read deadline
  crossing without deadline reset, duplicate request, CLI-side effect, or
  false success.
- Prove provider-valid deterministic idempotency-key encoding for every stop,
  start, alias-attach, and alias-detach mutation, including accepted-operation
  replay, while the original action ID remains the durable journal identity.
- Cover exact current-lineage `effect-failed` plus same-pending-operation recovery through retry, committed cutover, and standby restoration. Counterexamples must prove that absent, malformed, foreign-request, stale, or pending-operation-mismatched progress cannot extend the wait; all paths remain deadline-bounded and identity-free.
- Live acceptance remains a separately authorized non-production workflow with at least three clean trials each for planned failover plus rearm, warm failback plus rearm, automatic failover plus rearm, repair success without transfer, and repair exhaustion followed by transfer. Probe bidirectionally at 5 Hz; recovery is five consecutive successes in the slower direction and loss is the exact missing sequence count. Report preparation, detection/repair, common cutover, total recovery, and redundancy-restoration separately. Under the same fixture, planned failover and warm failback median common-cutover time and directional loss, and automatic post-admission cutover, must remain within 20 percent; rearm must cause zero packet loss and no forwarding, route, or allocation mutation.
- Implementation evidence on 2026-08-27: exact same-request reattachment reuses the unchanged original fingerprint, while request publication and effect dispatch serialize through the existing writer lock. Focused negative control reproduced stale post-decision dispatch for planned failback and failover before the intent recheck was added, then passed for both directions. The complete 1,715-test unit suite, 83-test integration suite, Ruff, mypy, CLI help, and scoped diff-integrity checks passed. Whole-file formatter and Markdown checks retain unrelated pre-existing drift outside the added lines. Installed-package parity, deployment, and a clean live failback replay remain separately authorized and unverified.
- Implementation evidence on 2026-08-27: sanitized live status and a bounded
  controller-journal sample proved an automatic failover retained its pending
  `stop-former-owner` action while Nebius rejected the colon-delimited action
  ID as an idempotency key on every retry. The common SDK metadata boundary now
  deterministically encodes provider-invalid IDs while the durable action and
  accepted-operation journal identity remain unchanged. Focused stop, start,
  alias attach/detach, and replay tests passed along with all 1,722 unit tests,
  83 integration tests, Ruff, mypy, changed-scope security review, and diff
  integrity. Deployment and a clean live controller recovery remain separately
  authorized and unverified.
- Implementation evidence on 2026-08-24: Ruff, mypy, 1,474 unit tests,
  70 integration tests, CLI help, wheel build, changed-scope documentation,
  security, alignment, and diff-integrity gates passed. One clean planned static
  failover completed cutover in 216.5 seconds and terminal redundancy
  restoration in 271.9 seconds total; one clean planned static failback
  completed those milestones in 207.1 and 261.4 seconds. Independent status
  after each trial proved both Computes and required services running, exact
  single allocation ownership, one forwarding/tunnel owner, one alias-free
  guarded tunnel-cold standby, healthy routes, terminal rearm, and ready
  redundancy without manual VM start or recovery. Two earlier observer-invalid
  trials exposed and then drove repairs for a missing canonical controller
  state, stale prior-receipt rearm projection, and incomplete terminal runtime
  binding; they are excluded from clean acceptance evidence. The full
  three-trial-per-scenario live matrix remains outstanding.
- Additional implementation evidence on 2026-08-24: 1,507 unit tests, 70
  integration tests, full mypy, Ruff, and exact-wheel packaging passed after
  independent preparation/cutover/restoration budgets, restoration phase
  projection, pre-call rearm status durability, controller runtime-directory
  ownership, and acyclic service ordering were implemented. On the retained
  non-production static fixture, clean failover completed cutover in 213.8
  seconds and full redundancy in 270.1 seconds; clean failback completed those
  milestones in 210.7 and 261.2 seconds. Both commands displayed all strict
  cutover phases plus `starting former owner as standby` and `waiting for
  standby readiness`. Independent final status proved two running healthy
  members, exact active/standby readiness, healthy routes, and ready
  redundancy. `systemd-analyze verify` passed on both members after each exact
  wheel deployment and stop/start cycle, and current-boot controller/FRR
  journals contained none of the prior ordering-cycle, missing-runtime, or
  read-only-runtime errors. The full repeated acceptance matrix remains
  outstanding.
- Additional implementation evidence on 2026-08-24: 1,513 unit tests, 77
  integration tests, 362 focused VM-HA tests, full-source mypy, targeted Ruff,
  wheel packaging, and diff-integrity gates passed after role-neutral
  automatic admission and replay-covered peer-heartbeat persistence were
  added. On the retained static test fixture, a configured-passive current
  owner became unreachable; the configured-active survivor completed the
  canonical automatic stop, detach, attach, ownership-confirmation, tunnel,
  route, and forwarding sequence in 171.8 seconds from its first accepted
  effect, then automatically restarted the former owner. Final independent
  status proved two running healthy members, exact single allocation
  ownership, one forwarding/IPsec owner, one alias-free guarded standby,
  healthy routes, terminal rearm, and ready redundancy. Incident recovery and
  the failed external stop request are excluded from traffic-loss evidence;
  the optional observer VM was not accessible with the available pinned SSH
  identities, so no workload-traffic acceptance claim is made.
- Additional implementation evidence on 2026-08-25: a focused deterministic
  regression proved one exact current-request controller effect failure can
  remain fenced, retry the same durable pending operation, complete cutover,
  and restore standby redundancy without CLI-side mutation. Foreign request,
  mismatched pending operation, mislabeled operation action, absent progress,
  and bounded-timeout counterexamples remained fail-closed. Ruff, full mypy,
  1,548 unit tests, 78 integration tests, and the wheel build passed. The
  installed CLI/agent pair and a clean live replay were not changed or
  validated in this source-only repair.

#### Test Method

- Run unit and composed tests for trigger-role confusion, pre-effect cancellation, post-effect stickiness, exact same-request reattachment without republication, request-versus-first-effect serialization, all transfer crash points, promotion-receipt ordering, both ownership directions, synchronous terminal waiting, exact default-text stderr progress and elapsed-time output with empty stdout, exact no-op sentences, explicit-JSON request/failure/no-op byte parity, invalid-format pre-effect rejection, timeout and cloud/status drift, direct/automatic rearm concurrency, one-writer serialization, idempotency, accepted-operation replay, owner drift, transitional/error/flapping state, explicit retry, inhibition, corrupt state, apply/removal races, service isolation, mixed-version fail-closed behavior, checkpoint v1-v4 reads, packaging, and rollback with the rearm unit stopped. Follow with Ruff, mypy, full unit/integration, CLI/help, systemd, wheel, and changed-scope alignment gates.
- Cover one-shot and persistent terminal observation loss at every cutover and
  restoration agent/cloud read, permanent evidence and well-formed drift
  counterexamples, JSON stdout stability, redaction, and post-read deadline
  crossing without deadline reset, duplicate request, CLI-side effect, or
  false success.
- Prove provider-valid deterministic idempotency-key encoding for every stop,
  start, alias-attach, and alias-detach mutation, including accepted-operation
  replay, while the original action ID remains the durable journal identity.
- Cover exact current-lineage `effect-failed` plus same-pending-operation recovery through retry, committed cutover, and standby restoration. Counterexamples must prove that absent, malformed, foreign-request, stale, or pending-operation-mismatched progress cannot extend the wait; all paths remain deadline-bounded and identity-free.
- Live acceptance remains a separately authorized non-production workflow with at least three clean trials each for planned failover plus rearm, warm failback plus rearm, automatic failover plus rearm, repair success without transfer, and repair exhaustion followed by transfer. Probe bidirectionally at 5 Hz; recovery is five consecutive successes in the slower direction and loss is the exact missing sequence count. Report preparation, detection/repair, common cutover, total recovery, and redundancy-restoration separately. Under the same fixture, planned failover and warm failback median common-cutover time and directional loss, and automatic post-admission cutover, must remain within 20 percent; rearm must cause zero packet loss and no forwarding, route, or allocation mutation.
- Implementation evidence on 2026-08-27: exact same-request reattachment reuses the unchanged original fingerprint, while request publication and effect dispatch serialize through the existing writer lock. Focused negative control reproduced stale post-decision dispatch for planned failback and failover before the intent recheck was added, then passed for both directions. The complete 1,715-test unit suite, 83-test integration suite, Ruff, mypy, CLI help, and scoped diff-integrity checks passed. Whole-file formatter and Markdown checks retain unrelated pre-existing drift outside the added lines. Installed-package parity, deployment, and a clean live failback replay remain separately authorized and unverified.
- Implementation evidence on 2026-08-27: sanitized live status and a bounded
  controller-journal sample proved an automatic failover retained its pending
  `stop-former-owner` action while Nebius rejected the colon-delimited action
  ID as an idempotency key on every retry. The common SDK metadata boundary now
  deterministically encodes provider-invalid IDs while the durable action and
  accepted-operation journal identity remain unchanged. Focused stop, start,
  alias attach/detach, and replay tests passed along with all 1,722 unit tests,
  83 integration tests, Ruff, mypy, changed-scope security review, and diff
  integrity. Deployment and a clean live controller recovery remain separately
  authorized and unverified.
- Implementation evidence on 2026-08-24: Ruff, mypy, 1,474 unit tests,
  70 integration tests, CLI help, wheel build, changed-scope documentation,
  security, alignment, and diff-integrity gates passed. One clean planned static
  failover completed cutover in 216.5 seconds and terminal redundancy
  restoration in 271.9 seconds total; one clean planned static failback
  completed those milestones in 207.1 and 261.4 seconds. Independent status
  after each trial proved both Computes and required services running, exact
  single allocation ownership, one forwarding/tunnel owner, one alias-free
  guarded tunnel-cold standby, healthy routes, terminal rearm, and ready
  redundancy without manual VM start or recovery. Two earlier observer-invalid
  trials exposed and then drove repairs for a missing canonical controller
  state, stale prior-receipt rearm projection, and incomplete terminal runtime
  binding; they are excluded from clean acceptance evidence. The full
  three-trial-per-scenario live matrix remains outstanding.
- Additional implementation evidence on 2026-08-24: 1,507 unit tests, 70
  integration tests, full mypy, Ruff, and exact-wheel packaging passed after
  independent preparation/cutover/restoration budgets, restoration phase
  projection, pre-call rearm status durability, controller runtime-directory
  ownership, and acyclic service ordering were implemented. On the retained
  non-production static fixture, clean failover completed cutover in 213.8
  seconds and full redundancy in 270.1 seconds; clean failback completed those
  milestones in 210.7 and 261.2 seconds. Both commands displayed all strict
  cutover phases plus `starting former owner as standby` and `waiting for
  standby readiness`. Independent final status proved two running healthy
  members, exact active/standby readiness, healthy routes, and ready
  redundancy. `systemd-analyze verify` passed on both members after each exact
  wheel deployment and stop/start cycle, and current-boot controller/FRR
  journals contained none of the prior ordering-cycle, missing-runtime, or
  read-only-runtime errors. The full repeated acceptance matrix remains
  outstanding.
- Additional implementation evidence on 2026-08-24: 1,513 unit tests, 77
  integration tests, 362 focused VM-HA tests, full-source mypy, targeted Ruff,
  wheel packaging, and diff-integrity gates passed after role-neutral
  automatic admission and replay-covered peer-heartbeat persistence were
  added. On the retained static test fixture, a configured-passive current
  owner became unreachable; the configured-active survivor completed the
  canonical automatic stop, detach, attach, ownership-confirmation, tunnel,
  route, and forwarding sequence in 171.8 seconds from its first accepted
  effect, then automatically restarted the former owner. Final independent
  status proved two running healthy members, exact single allocation
  ownership, one forwarding/IPsec owner, one alias-free guarded standby,
  healthy routes, terminal rearm, and ready redundancy. Incident recovery and
  the failed external stop request are excluded from traffic-loss evidence;
  the optional observer VM was not accessible with the available pinned SSH
  identities, so no workload-traffic acceptance claim is made.
- Additional implementation evidence on 2026-08-25: a focused deterministic
  regression proved one exact current-request controller effect failure can
  remain fenced, retry the same durable pending operation, complete cutover,
  and restore standby redundancy without CLI-side mutation. Foreign request,
  mismatched pending operation, mislabeled operation action, absent progress,
  and bounded-timeout counterexamples remained fail-closed. Ruff, full mypy,
  1,548 unit tests, 78 integration tests, and the wheel build passed. The
  installed CLI/agent pair and a clean live replay were not changed or
  validated in this source-only repair.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-011 -->

<!-- REQUIREMENT: TI-REQ-012 status=active priority=P1 type=feature -->
### TI-REQ-012: Isolate and live-validate GCP Classic static VM HA

#### User Story

Support and independently validate a static-only two-node VM-HA deployment against an isolated GCP Classic VPN fixture without sharing gateway members, cluster identity, peer resources, routes, or configuration with the BGP deployment. Constraints: Preserve existing non-HA static behavior, BGP VM-HA warm-tunnel behavior, supported mixed-connection configuration, public commands, and record formats. The static-only VM-HA standby is Compute-warm but tunnel-cold because GCP considers a Classic tunnel usable whenever its IKE SA is established. Only the exact forwarding owner may keep the Classic IKE SA established. Every static promotion must retain former-Compute-`Stopped`, shared-allocation transfer and reread, route, and forwarding gates; candidate tunnel activation occurs only after exact ownership confirmation and while forwarding remains fenced. Do not place GCP credentials or a GCP route writer on gateway VMs. Keep PSKs and cloud identities out of committed files and public evidence; an ignored, regular, non-symlink, mode-`0600` operator config may hold literal PSKs for an explicitly requested rotation. Non-goals: Hybrid BGP/static live validation, changing the validity of existing mixed-mode configs, GCP route mutation from a Nebius gateway, proactive preference-based automatic failback, active-active or ECMP forwarding, treating fixture recovery as product proof, production validation, deleting the retained gateway/address/forwarding-rule fixture, or rotating tunnels implicitly during ordinary idempotent apply.

#### Acceptance Criteria

- `nebius-gcp-ha-new-vpn.config.yaml` resolves only BGP connections. A separate ignored mode-`0600` `nebius-gcp-classic-vpn.config.yaml` resolves only static connections, may retain its two PSKs as literal values rather than environment references, and uses a distinct Nebius gateway name, VM-HA cluster and members, subnet/allocation identities, public addresses, and GCP resource names.
- A dedicated Classic helper plans, previews, applies, and reports two one-to-one GCP Classic gateway/tunnel paths plus explicit static routes without a Cloud Router, BGP peer, or HA VPN gateway. Ordinary apply remains idempotent and non-deleting. An explicit `--psk-source-config` opens one private mode-`0600` VPNGW YAML through a no-follow file descriptor, proves that the opened inode is the inspected regular file, reads exactly the two planned named tunnel PSKs without printing them, and topology-binds the helper plan to that config's enabled two-member VM-HA declaration, GCP/static connection identity, local and remote prefixes, member public endpoints, tunnel-to-member bindings, inner links, and observed GCP peer addresses before rotation. Explicit `--rotate-existing-tunnels` is required before any compatible existing tunnel is recreated.
- Rotation validates both replacement PSKs and requires every retained address, target gateway, and forwarding rule to be present and compatible before the first mutation; only planned tunnels and routes may be absent as retry state. It requires a successful explicit static VM-HA peer-rotation preparation, re-reads and identity-binds the exact graph immediately after operator confirmation, deletes only the planned static routes and planned existing tunnels, recreates both tunnels before restoring any route, and preserves the retained infrastructure. Any failure after mutation begins, including deletion or final authoritative verification failure, removes every observed planned route and refuses a retryable result unless their absence is proven, so no newly routed one-path graph is exposed.
- The GCP Classic workflow invokes the provider-neutral `apply --prepare-vm-ha-peer-rotation` checkpoint from REQ-014 with its explicit local static-only config. The checkpoint performs normal bootstrap, staging, exact-generation lock, declared-owner adoption, and activation work; verifies both members passively fenced under the exact locks; and exits before releasing the owner lock or attempting tunnel, route, or forwarding convergence. This fixture remains static-only even though the core checkpoint also admits other supported vendors and routing modes. Ordinary `apply` retains its existing behavior and is the only supported continuation after peer recreation: it re-establishes the exact locks, releases only the current-owner lock, and requires current-owner IKE/XFRM readiness plus the exact route receipt before forwarding.
- A static-only passive member keeps forwarding disabled, preserves generation and passive materialization evidence, and has no established IKE SA or XFRM path. That tunnel-cold state is promotable only for the static-only contract and is reported distinctly from BGP warm-tunnel readiness.
- Static promotion preserves the canonical stop, detach, attach, and ownership-confirmation chain, then performs one checkpointed candidate-tunnel preparation effect while forwarding is disabled. Route reconciliation requires fresh established-IKE, XFRM, and static-prefix readiness; forwarding still occurs only after the exact current route receipt.
- Automatic rearm starts or adopts the exact non-owner Compute without re-establishing its Classic tunnel. Planned failover, planned failback, and automatic failover therefore leave exactly one established Classic tunnel aligned with the current forwarding owner.
- Clean live trials independently prove initial steady state, planned failover plus rearm, planned failback plus rearm, and automatic failover plus rearm. Each trial verifies former owner `Stopped` before transfer, exact allocation ownership before tunnel preparation, current-owner-only IKE, route receipt before forwarding, GCP selected next hop, unchanged BGP fixture state, and bidirectional workload recovery.
- Any out-of-band GCP route/tunnel repair or Nebius ownership intervention marks that trial intervened. Recovery is performed separately and the affected criterion is replayed from a newly proven checkpoint.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run static-only schema/config goldens, controller crash and effect-order tests, tunnel-cold passive/rearm tests, strongSwan activation and fencing tests, fake-`gcloud` Classic helper tests, CLI/status/help, Ruff, mypy, full unit/integration, packaging, security, and changed-scope alignment gates. Follow with the separately authorized clean non-production trials and independent cloud, host, route, tunnel, and traffic postconditions.
- The explicit rotation path is implemented and covered offline by focused fake-`gcloud` tests for retained-resource admission, private-config VM-HA/topology binding, post-confirmation resource-identity drift, every mutation-phase cleanup boundary, and retry convergence, plus Ruff, mypy, Bash and ShellCheck validation, changed-document Markdown validation, canonical spec validation, and diff-integrity checks. The retained private Classic config contains two distinct literal replacement PSKs, no PSK environment references, and remains mode `0600`.
- An explicitly authorized isolated recovery run completed the new preparation checkpoint, the two-tunnel peer recreation, and the ordinary apply continuation. Independent verification proved exact installed-source parity on both members, one unlocked active owner with current route receipt and established XFRM state, one unlocked tunnel-cold passive member with zero XFRM state, healthy services and routing, and five bidirectional workload probes whose ten packet increments appeared only on the owner. This recovery lineage is not evidence for the still-required clean failover/failback trials. The post-confirmation and injected-failure hardening is offline-proven only; independent inventory also found older route records outside the helper's planned names, and their cleanup was not authorized or performed.

#### Test Method

- Run static-only schema/config goldens, controller crash and effect-order tests, tunnel-cold passive/rearm tests, strongSwan activation and fencing tests, fake-`gcloud` Classic helper tests, CLI/status/help, Ruff, mypy, full unit/integration, packaging, security, and changed-scope alignment gates. Follow with the separately authorized clean non-production trials and independent cloud, host, route, tunnel, and traffic postconditions.
- The explicit rotation path is implemented and covered offline by focused fake-`gcloud` tests for retained-resource admission, private-config VM-HA/topology binding, post-confirmation resource-identity drift, every mutation-phase cleanup boundary, and retry convergence, plus Ruff, mypy, Bash and ShellCheck validation, changed-document Markdown validation, canonical spec validation, and diff-integrity checks. The retained private Classic config contains two distinct literal replacement PSKs, no PSK environment references, and remains mode `0600`.
- An explicitly authorized isolated recovery run completed the new preparation checkpoint, the two-tunnel peer recreation, and the ordinary apply continuation. Independent verification proved exact installed-source parity on both members, one unlocked active owner with current route receipt and established XFRM state, one unlocked tunnel-cold passive member with zero XFRM state, healthy services and routing, and five bidirectional workload probes whose ten packet increments appeared only on the owner. This recovery lineage is not evidence for the still-required clean failover/failback trials. The post-confirmation and injected-failure hardening is offline-proven only; independent inventory also found older route records outside the helper's planned names, and their cleanup was not authorized or performed.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-012 -->

<!-- REQUIREMENT: TI-REQ-013 status=active priority=P1 type=feature -->
### TI-REQ-013: Make planned VM transfer phases and traffic recovery observable

#### User Story

Report the current safety-critical phase during planned VM failover and failback, and provide an opt-in test-only workload probe that measures end-to-end recovery without participating in promotion authority. Constraints: Preserve the public commands, default text and explicit JSON output modes, sorted JSON record bytes, request schemas, exit behavior, monotonic total elapsed-time origin, fixed independent 300-second preparation, 600-second cutover, and 300-second restoration deadlines, controller checkpoint and transfer-lineage formats, strict former-owner `Stopped` fence, exact candidate allocation reread, owner-only static tunnel activation, route receipt, forwarding gate, and sole rearm Compute-start writer. Fine progress is private, additive, sanitized, bounded, and presentation-only. Exact current-request progress may corroborate a closed controller-owned ownership-reproof state only to keep the observer inside the already-fixed cutover window; it never extends that window or gains completion/effect authority. Missing, stale, malformed, or mixed-version progress falls back to the existing coarse cutover phase and cannot block, accelerate, replay, or complete a transfer; it does not suppress independently valid restoration phases. Test endpoints, credentials, host pins, and raw evidence are never product defaults or committed artifacts. Non-goals: Using ICMP as a health signal or forwarding gate, adding test-VM knowledge to the gateway product, enabling forwarding before authoritative tunnel and route readiness, prewarming the static standby tunnel, parallelizing stop/detach/attach, changing automatic failover policy, or claiming one-way diagnostic traffic as bidirectional live acceptance.

#### Acceptance Criteria

- The candidate controller publishes a strict private transfer-progress projection bound to the exact cluster, candidate and former member, generation and digests, allocation, controller boot, ownership incarnation, planned-request fingerprint, transfer-lineage first operation, and current action operation. It retains enough ordered transitions for a five-second CLI poller to observe short phases without becoming execution authority.
- An action becomes visible as attempting only after its pending checkpoint is durable and before its external effect begins. Completion is visible only after the existing authoritative postcondition clears that exact action. The projection contains closed phase and state values plus bounded timing and sanitized failure classification, never exception text, credentials, cloud payloads, or public resource details.
- Planned failover and failback render role-specific progress for stopping the current owner, unassigning the shared IP, assigning it to the candidate, confirming ownership, establishing the VPN when required, reconciling routes, enabling forwarding, starting the former owner as standby, and waiting for standby readiness. Each newly observed phase is emitted immediately and the current phase repeats approximately every five seconds using the existing monotonic total elapsed clock. Restoration phase selection is independent of whether exact cutover progress was ever available.
- Default text output keeps stdout empty. Explicit `--output-format json` preserves the existing request or no-op record bytes on stdout. Start, progress, cutover, partial-completion, failure, no-op, and terminal-success semantics remain on stderr and retain the existing terminal authority checks.
- An exact current-request ownership reproof after an apparent activation is
  rendered as continued bounded progress and cannot emit a cutover or terminal
  success milestone. Missing or foreign request/runtime binding, an unexpected
  blocked reason or pending action, an apply lock, malformed evidence, or any
  state outside the closed reproof path remains an immediate safe failure.
- A generic opt-in live-trial helper outside the installed product runtime can run a declared one-way workload probe while an operator invokes an exact existing failover or failback command separately. It requires a literal target, pinned known-hosts file, and explicit current-user-owned private identity with no group or other permissions; disables ambient SSH configuration, identities, proxies, password, and keyboard authentication; records source timestamps and the complete transmitted sequence domain; and reports timestamped unique replies, five-success recovery after the final loss, and exact missing sequences. It neither invokes nor reads the product command; trial analysis may correlate its timestamps with independently captured product phase, cutover, and redundancy-restoration output.
- Probe loss or recovery is diagnostic evidence only. The product command never reads it. SSH failure, missing terminal summary, malformed or localized output, send failure, incomplete sequence accounting, excessive timing uncertainty, or out-of-band recovery invalidates the trial instead of becoming packet loss or product success evidence.
- Latency changes are admitted only after at least five comparable before and after samples identify a dominant local delay and show a material improvement without weakening any safety or terminal postcondition. Provider long-running-operation time and genuine IKE establishment remain measured rather than bypassed.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused private-record, status-validation, action-order, crash-replay, phase-rendering, JSON-parity, mixed-version, independent phase-budget, timeout, rearm-start publication, systemd ordering/runtime-directory, and deterministic probe-parser tests. Follow with Ruff, mypy, full unit/integration, CLI/help, packaging, security, documentation, and changed-scope alignment gates.
- In the separately authorized non-production fixture, run clean planned failover and failback trials with the diagnostic probe active before CLI invocation. Independently verify Compute fencing, exclusive allocation ownership, owner-only IKE/XFRM, route receipt before forwarding, restored two-VM redundancy, and bidirectional workload recovery before making a live acceptance or optimization claim.
- Offline implementation evidence on 2026-08-24: strict request fingerprint, bounded progress transition, exact pending-checkpoint projection and terminal suppression, postcondition-observer ordering, role-specific immediate phase rendering, stale-evidence coarse fallback, JSON/text output, exact-identity SSH, and ping runtime-error rejection passed together with focused VM-HA controller/agent/CLI regressions, Ruff, and mypy. No live cloud, gateway, or traffic trial was run, so no latency reduction or live acceptance claim is made.

#### Test Method

- Run focused private-record, status-validation, action-order, crash-replay, phase-rendering, JSON-parity, mixed-version, independent phase-budget, timeout, rearm-start publication, systemd ordering/runtime-directory, and deterministic probe-parser tests. Follow with Ruff, mypy, full unit/integration, CLI/help, packaging, security, documentation, and changed-scope alignment gates.
- In the separately authorized non-production fixture, run clean planned failover and failback trials with the diagnostic probe active before CLI invocation. Independently verify Compute fencing, exclusive allocation ownership, owner-only IKE/XFRM, route receipt before forwarding, restored two-VM redundancy, and bidirectional workload recovery before making a live acceptance or optimization claim.
- Offline implementation evidence on 2026-08-24: strict request fingerprint, bounded progress transition, exact pending-checkpoint projection and terminal suppression, postcondition-observer ordering, role-specific immediate phase rendering, stale-evidence coarse fallback, JSON/text output, exact-identity SSH, and ping runtime-error rejection passed together with focused VM-HA controller/agent/CLI regressions, Ruff, and mypy. No live cloud, gateway, or traffic trial was run, so no latency reduction or live acceptance claim is made.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-013 -->

<!-- REQUIREMENT: TI-REQ-014 status=active priority=P1 type=feature -->
### TI-REQ-014: Bind VM-HA runtime credentials to one authenticated identity

#### User Story

Give every VM-HA gateway one product-managed renewable Nebius service-account credential source on the operator machine, install a separate protected copy on each member during apply, and prove that exact identity before apply and at every controller start. Constraints: Remove runtime credential paths from public YAML and both wizards without a legacy reader. Derive the operator path as `~/.config/nebius-vpngw/credentials/<project>/<gateway>/nebius-credentials.json`, displaying literal `~` but resolving the actual operator home with `Path.home()`. Derive the service-account/group name `<gateway>-vm-ha` and authorized-key name `<gateway>-vm-ha-runtime-key`, with deterministic bounded names when needed. VM-HA rejects `apply --sa`; ordinary gateways retain it. Keep credential bytes out of YAML, manifests, lifecycle records, status, logs, errors, and approval records; retain root-owned mode-`0600` immutable on-node bundles; use bounded SDK authentication and request timeouts; and do not weaken any stopped-owner, shared-allocation, route, or forwarding gate. Non-goals: Migrating to instance-attached service accounts, adding periodic identity probes after startup, automatic key rotation or account rebinding, deleting managed credentials or IAM state during destroy, provisioning credentials during either wizard, weakening IAM to accommodate drift, exposing identity or digest values in public status, or introducing a second runtime-auth path.

#### Acceptance Criteria

- `create-config` and ordinary-to-VM-HA conversion ask no credential question,
  create no credential directory/file, and perform no IAM/key provisioning.
  Their redacted summary contains one line showing the derived managed path.
- Pre-approval apply inspection is read-only. Its exact secret-free plan records
  `create` or `reuse`, project, deterministic resource names, display path,
  RSA-4096, RS256, and no expiry. Dry-run and declined approval have zero local
  credential or IAM effects.
- After approval, current generated Nebius SDK clients create or reuse exactly
  one product-labeled service account, same-name group, sole membership, one
  project `editor` permit, and one non-expiring RSA-4096/RS256 authorized key.
  Foreign or incomplete labels, identities, members, permits, keys, or key data
  fail closed; apply never deletes or silently adopts them.
- New enrollment atomically publishes one current-user-owned mode-`0600`
  credential file below owner-only managed directories. A protected pending
  private key and secret-free journal make crashes resumable against the same
  cloud public key. Missing, unpaired, unsafe, or mismatched resume state blocks
  without generating a replacement identity.
- Before lifecycle, cloud, SSH, or gateway mutation, apply reads the one managed
  source, obtains its service-account and authorized-key identifiers through
  the supported SDK credential reader, and performs a forced-renewal
  `SDK.whoami()` with finite timeouts. Both member projections must share the
  exact source, byte digest, service-account ID, authorized-key ID, and project.
- A provisioning, activating, or active cluster cannot silently change its
  bound account, key, or source digest. Missing or mismatched operator state
  blocks apply; there is no automatic repair, rotation, or rebinding.
- The service-account ID, authorized-key ID, canonical installed paths, and
  one shared credential digest are secret-free immutable inputs to the runtime
  binding, apply operation, staging receipts, and lifecycle resource bindings.
  Apply installs separate generation- and node-bound copies on both members;
  staging rereads the source and rejects digest or identity drift.
- After the current-boot guard and before cloud observation, route mutation, or
  forwarding admission, each controller proves `whoami()` against its exact
  installed bundle and immutable binding. Normal systemd startup may reuse only
  a recent current-boot, current-generation, node-, path-, and digest-bound
  secret-free attestation; direct controller start must perform the same proof.
- Missing, malformed, expired, unauthenticated, wrong-project, wrong-account,
  wrong-key, or drifted identity evidence keeps the node fenced, emits only a
  closed reason, exposes an additive identity-safe runtime status state, and
  relies on the existing service retry policy without cloud or forwarding
  effects. Destroy retains the local managed source and managed IAM resources.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused managed-enrollment, credential-reader, SDK-whoami,
  apply/lifecycle binding, staging-TOCTOU, startup-attestation, status,
  wizard/config, packaging, and composed VM-HA tests. A separately authorized
  non-production apply is required before claiming live IAM enrollment,
  two-member installation, or end-to-end health for this revised contract.

#### Test Method

- Run focused managed-enrollment, credential-reader, SDK-whoami,
  apply/lifecycle binding, staging-TOCTOU, startup-attestation, status,
  wizard/config, packaging, and composed VM-HA tests. A separately authorized
  non-production apply is required before claiming live IAM enrollment,
  two-member installation, or end-to-end health for this revised contract.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-014 -->

<!-- REQUIREMENT: TI-REQ-015 status=active priority=P1 type=feature -->
### TI-REQ-015: Provide one idempotent VM-HA lifecycle command

#### User Story

Add a top-level `vm-ha` command that safely converges an ordinary single-VM gateway into managed warm-standby VM HA and repeatedly verifies or heals an existing VM-HA deployment through the product's authoritative conversion, apply, status, controller, and rearm boundaries. Constraints: Require an explicit local configuration file; preserve an ordinary source file and publish a deterministic separate VM-HA candidate; expose no separate setup or rearm command; keep failover and failback as separate explicit operations; never reset or duplicate the controller's one-attempt repair budget; never mutate IAM, SSH trust, peer-provider infrastructure, shared-allocation ownership, routes, or forwarding outside their existing owners; and require an exact digest-bound approval when the exact plan is destructive, may interrupt VPN traffic, or creates cloud resources. A plan may run without a second confirmation only when its typed impact explicitly proves no destructive change, no expected VPN traffic interruption, and no resource creation; missing or unknown impact fails closed to approval. Retain the existing exact non-owner rearm authority. The private VM-HA lifecycle and request formats are not yet customer-supported and may make one explicitly approved clean break to a single canonical format. REQ-015 owns the approved command and region-option break; YAML field names remain compatible. Non-goals: Exercising failover as a health check, claiming failover was tested from passive inspection, inventing missing credentials or trust, silently normalizing IAM or external peer resources, bypassing current stopped-owner/allocation/route/forwarding gates, adding an `ensure` subcommand, or removing the internal sole-start-writer rearm engine.

#### Acceptance Criteria

- `nebius-vpngw vm-ha --local-config-file FILE` accepts an ordinary or explicit
  VM-HA configuration. Ordinary input is converted with the existing
  allowlisted semantics; the source is never overwritten, the default
  candidate name is deterministic, `--output` selects another destination,
  `--region` selects the Nebius region used by passive allocation preparation,
  and `--force` replaces only an exact safely classified candidate.
- A noninteractive or JSON invocation that needs conversion answers reports
  `action-required` without prompting or mutating. An exact existing candidate
  is reused, while conflicting, foreign, stale, ambiguous, or unsafe files
  fail closed.
- Interactive conversion completes configuration-resolution progress before
  entering the blocking wizard and never keeps an animated progress renderer
  active while waiting for terminal input. A separately confirmed passive-IP
  cloud preparation uses its own progress phase; cancellation or interruption
  before that confirmation performs no cloud or candidate-publication effect.
- Every exact material plan carries typed impact metadata that separately states
  whether it is destructive, whether VPN traffic may be interrupted, and
  whether it creates cloud resources, plus
  one concise operator-facing impact sentence. Apply-plan impact is digest-bound;
  the safe standby-policy impact is fixed by its closed policy kind and exact
  revalidation. Impact is never inferred by parsing effect names. The
  `artifact-standby-recovery` sentence states that VPN traffic may be briefly
  interrupted while the serving owner is upgraded and that no gateway VM or
  disk is deleted.
- An interactive text invocation prompts only when the exact plan requires
  approval. It displays the exact approval kind, digest, concise impact, and
  sanitized effect list, then asks once with a default-No `[y/N]`
  confirmation. Explicit `y` continues in the same invocation through the
  existing lock-bound digest revalidation; No performs no mutation, reports
  `operator-declined-approval`, and exits `3`. A plan explicitly classified as
  non-destructive with no expected VPN interruption and no resource creation
  skips the prompt and uses the same lock, exact replan, artifact verification,
  execution, and terminal health proof. `--approve`, `--dry-run`, JSON, and noninteractive invocations
  never prompt; JSON and noninteractive executions automatically run only that
  explicitly safe class and otherwise return the approval-required result.
- The command performs strict authoritative inspection, classifies the next
  product-owned action, executes at most one safe action at a time, and
  reinspects until healthy, blocked, failed, or exact operator approval/input
  is required. A bounded transition count and repeated-state detector prevent
  self-healing loops. When exact cloud authority proves that a non-owner still
  reports active forwarding, the continuously running controller remains the
  sole safety-fence owner. `vm-ha` classifies the state as a controller
  transition, recognizes only its exact `blocked:disable-active` operation
  targeting that reporting non-owner, and observes bounded progress without
  prompting or executing apply, rearm,
  route, allocation, or direct forwarding effects. Traffic incorrectly using
  that non-owner may be interrupted by the safety fence; the authoritative
  owner is preserved. Repeated unchanged evidence stops with controller-journal
  guidance.
- A stopped exact non-owner is restored only through the existing sole-start-
  writer rearm engine. Existing controller repair or route reconciliation is
  observed rather than restarted. Missing credentials, pinned trust, IAM, or
  external peer inputs produce sanitized owner-specific guidance and no
  automatic mutation. For an authoritatively absent replacement member whose
  product-managed public pin has no matching managed private key, the exact
  `active-standby-replacement` plan includes a managed identity rotation and
  `vm-ha --approve DIGEST` owns the checkpointed repair. If either SSH source
  is explicitly operator-owned or the managed predecessor is not exact, the
  command instead reports `replacement-ssh-identity-unavailable`, directs the
  operator to restore the matching key, and never classifies the condition as
  authentication failure.
- Agent-artifact approval requires one existing regular non-symlink project
  wheel whose metadata, package record, actual agent capability entry point,
  file identity, and digest agree. Planning artifact failures have zero effects.
  An artifact failure after execution begins states that convergence effects
  may have started and directs the next invocation to re-inspect and resume from
  durable checkpoints; it is never reported as authentication failure.
- Initial standby creation and interrupted provisioning resume through the
  existing apply transaction. For an already-active lifecycle, `vm-ha` may
  replace exactly one authoritatively absent current non-owner after proving the
  other member is the stable shared-allocation owner. The exact cloud-missing
  member identity overrides any stale configured public IP and prevents an SSH
  probe of that absent member. It emits the closed
  `active-standby-replacement` creation plan. Interactive text asks one
  default-No confirmation and, on `y`, continues in the same invocation without
  displaying the digest; JSON, non-TTY, and dry-run output retain the exact
  digest and `--approve DIGEST` automation contract. The durable transaction
  first re-proves the frozen owner and detached retained allocations. When the
  absent member's original managed private SSH key is unavailable, the approval
  additionally binds the exact trust predecessor and permits one product-managed
  key rotation. The journal stages and binds the new key fingerprint and
  successor public-trust digests, publishes that trust, and re-resolves strict
  SSH before any cloud creation. It then installs a capability-gated,
  operation-bound transfer inhibition that keeps the serving owner's forwarding
  active during the cloud replacement effects while quiescing transfer/rearm
  writers. A live-peer-capable owner is not reinstalled or restarted: only the
  replacement standby is installed and activated, managed mTLS publishes its
  new Compute identity live, and the owner config is committed atomically
  without reload. An older owner selects a distinct combined approval that first
  upgrades and restarts its control services and truthfully reports possible
  brief VPN interruption. The transaction creates a configured-name Compute
  with a new deterministic cycle-qualified boot disk, reuses the retained
  primary and public allocations, and resumes the canonical passive-first apply.
  Accepted cloud-operation result IDs bind every same-name disk or Compute
  adopted after a lost acknowledgement. It never selects the target from
  configured role and never reads, adopts, reuses, or deletes an old disk. The
  old Compute and disk IDs remain private lifecycle audit evidence. Interrupted
  retries resume the same approved transaction without a second approval, and
  the owner's exact inhibition is released through a durable idempotent receipt
  only after both members pass terminal verification.
- Material plans expose one closed approval kind, exact effect summary, typed
  impact, and approval-required decision. Apply-plan digests bind the effective
  configuration, lifecycle state, authoritative cloud revisions, proposed
  effects, and impact metadata; the safe standby-policy impact is invariant for
  its closed kind. `--approve DIGEST` authorizes only the unchanged risky domain;
  a reread mismatch, including apply-plan impact drift, rejects it before
  mutation. Dry-run always remains read-only, including for safe plans.
- Text output is human-oriented. A healthy terminal result emits only
  `VM-HA is healthy now.` after progress, without repeating its classification,
  health, effective config, or passive verification scope. Other text outcomes
  retain the context needed for action. `--output-format json` writes one stable
  `nebius-vpngw/vm-ha-result-v1` object to stdout and progress only to stderr;
  it retains the complete outcome, health, passive verification scope,
  effective config path, actions, reasons, optional approval with impact and
  approval-required metadata, and next action without cloud identifiers,
  secrets, raw exceptions, or provider payloads.
- Progress reports every meaningful inspection, plan, healing mutation,
  managed-service action, authority check, and verification phase with at most
  one persistent row per phase. Interactive terminals may update the current
  started or waiting row in place with an animated spinner rather than a
  literal ellipsis; noninteractive streams emit only the terminal row. The
  spinner is removed when the phase ends and becomes one green `✓` success row
  or one fully red `✗` failure row. Nested unfinished phases close once as
  failures. Long waits update the same live row at a bounded cadence rather
  than adding one line per poll. A step is completed only after its
  authoritative checkpoint succeeds. While a spinner is active, routine raw
  apply, Rich, provider, VM-manager, SSH-push, per-member readiness, and
  per-member package-preparation output never writes through or concatenates
  with the managed row. Both members' successful config-push readiness is
  coalesced into one concise green completion row, and exact agent-package
  preparation likewise completes as one managed row. Ordinary `apply` keeps
  its existing diagnostic output; VM-HA warnings and failures remain available
  through the sanitized terminal result after the spinner is stopped.
- A required remote activation-command failure preserves one bounded,
  sanitized command class and exit result while raw apply, provider, SSH, and
  remote output remains suppressed. Apply-owned activation failures classify
  as safe convergence failures with status and service-journal guidance;
  only failures originating at the authentication boundary use authentication
  recovery guidance. Retriable Nebius SDK diagnostics, including their raw
  exception tracebacks, never interrupt or leak into the managed progress row.
  The suppression is scoped to known retry records and never hides the final
  exception or changes SDK retry, deadline, or authority behavior.
- Manual failover and failback admission rejects a new request whenever an
  exact durable transfer lineage already exists. During upgrade recovery, the
  controller may retire only an exact contradictory manual request whose
  request time proves it was written after that lineage began; an earlier,
  malformed, stale, or foreign conflict remains fail-closed.
- Every service that shares `/run/nebius-vpngw` preserves that systemd runtime
  directory across stops and restarts. A controller failure or restart cannot
  remove the routing-lock directory from the guard or another running service.
- Exit status is `0` for healthy or a successful dry-run plan, `3` for
  operator input or approval required, `1` for blocked or failed convergence,
  `2` for usage errors, and `130` for interruption.
- Terminal success requires two consecutive fresh exact observations that
  agree on lifecycle, cloud owner/allocation, generation, mTLS and runtime
  identity, routes, forwarding, configured IPsec/BGP/static readiness, and the
  warm standby. It reports `verification_scope=passive-current-state-v1` and
  `failover_tested=false`.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run deterministic command-planner and CLI tests for ordinary conversion,
  exact-candidate reuse, no-op health, dry-run, rearm, in-progress controller
  observation, domain-bound approvals, stale approval rejection, bounded-loop
  detection, stable JSON and text output, redaction, interrupts, and every
  exit class. Run focused conversion/apply/status/rearm regressions followed by
  Ruff, mypy, the complete unit/integration suites, packaging, CLI/help,
  security, and documentation alignment gates. Treat installed parity and a
  separately authorized non-production convergence run as later proof levels.
- Implementation evidence on 2026-08-25: Ruff, mypy across 52 source files,
  1,604 unit tests, 79 integration tests, focused command/status/apply tests,
  CLI/help smoke checks, and wheel construction/content inspection passed.
  Alignment regressions proved exact-candidate-only force, same-invocation
  apply-plan binding, raw engine-output suppression, and canonical provisioning
  from a verified removed tombstone. The final approval matrix additionally
  proved default-No refusal, explicit acceptance, explicit-approval and dry-run
  no-prompt behavior, JSON stdout purity, interruption safety, truthful failed
  phases, and bounded elapsed updates from the underlying reachability, pinned
  SSH, bootstrap, rearm, and agent-status polling loops. Security and
  correctness review found no unresolved blocking issue. That initial gate did
  not perform installed-parity or live cloud/gateway convergence.
  Follow-up progress and activation regressions proved one persistent terminal
  row per phase, full-row red and green rendering, bounded PTY failure
  classification, apply-owned convergence guidance, and unchanged
  authentication guidance.
  A separately authorized live static VM-HA recovery then proved the retained
  controller conflict, deployed the request-admission and shared-runtime fixes
  to both members, completed the exact `vm-ha` resume transaction, and
  completed a separate clean `apply`. Independent current-boot checks found
  both controllers, guards, rearm services, and agents healthy with no restart
  loop; the owner was forwarding with exact routes and the peer was passive and
  standby-ready. Failover was not exercised.
  The spinner and retry-diagnostic refinement then passed 50 focused VM-HA
  command tests, Ruff, mypy across 52 source files, 1,612 unit tests, 79
  integration tests, wheel construction, and a read-only interactive
  `vm-ha --dry-run` against the retained healthy deployment. The live command
  visibly replaced each spinner with one green terminal row and exited
  healthy; deterministic command tests injected the installed and current SDK
  retry wordings with exception tracebacks and proved both successful retry
  suppression and preserved red, nonzero terminal failure handling. No
  failover or gateway mutation was part of this presentation-only proof.
  The 2026-08-28 presentation-boundary repair first reproduced Rich replacing
  the process streams while a status was active, then proved the non-proxying
  live display preserves the facade capture across nested spinner transitions.
  Regression coverage also proved one completed row for two-member config-push
  readiness, one for exact agent-package preparation, and no leaked apply,
  provider, VM-manager, or SSH-push chatter. The 703-test affected VM-HA
  matrix, Ruff format/check, mypy, CLI help, and an isolated wheel build passed.
  Markdown lint retained only pre-existing `docs/design.md` heading and
  line-length debt. Installed-package parity, live convergence, and failover
  were not run.

#### Test Method

- Run deterministic command-planner and CLI tests for ordinary conversion,
  exact-candidate reuse, no-op health, dry-run, rearm, in-progress controller
  observation, domain-bound approvals, stale approval rejection, bounded-loop
  detection, stable JSON and text output, redaction, interrupts, and every
  exit class. Run focused conversion/apply/status/rearm regressions followed by
  Ruff, mypy, the complete unit/integration suites, packaging, CLI/help,
  security, and documentation alignment gates. Treat installed parity and a
  separately authorized non-production convergence run as later proof levels.
- Implementation evidence on 2026-08-25: Ruff, mypy across 52 source files,
  1,604 unit tests, 79 integration tests, focused command/status/apply tests,
  CLI/help smoke checks, and wheel construction/content inspection passed.
  Alignment regressions proved exact-candidate-only force, same-invocation
  apply-plan binding, raw engine-output suppression, and canonical provisioning
  from a verified removed tombstone. The final approval matrix additionally
  proved default-No refusal, explicit acceptance, explicit-approval and dry-run
  no-prompt behavior, JSON stdout purity, interruption safety, truthful failed
  phases, and bounded elapsed updates from the underlying reachability, pinned
  SSH, bootstrap, rearm, and agent-status polling loops. Security and
  correctness review found no unresolved blocking issue. That initial gate did
  not perform installed-parity or live cloud/gateway convergence.
  Follow-up progress and activation regressions proved one persistent terminal
  row per phase, full-row red and green rendering, bounded PTY failure
  classification, apply-owned convergence guidance, and unchanged
  authentication guidance.
  A separately authorized live static VM-HA recovery then proved the retained
  controller conflict, deployed the request-admission and shared-runtime fixes
  to both members, completed the exact `vm-ha` resume transaction, and
  completed a separate clean `apply`. Independent current-boot checks found
  both controllers, guards, rearm services, and agents healthy with no restart
  loop; the owner was forwarding with exact routes and the peer was passive and
  standby-ready. Failover was not exercised.
  The spinner and retry-diagnostic refinement then passed 50 focused VM-HA
  command tests, Ruff, mypy across 52 source files, 1,612 unit tests, 79
  integration tests, wheel construction, and a read-only interactive
  `vm-ha --dry-run` against the retained healthy deployment. The live command
  visibly replaced each spinner with one green terminal row and exited
  healthy; deterministic command tests injected the installed and current SDK
  retry wordings with exception tracebacks and proved both successful retry
  suppression and preserved red, nonzero terminal failure handling. No
  failover or gateway mutation was part of this presentation-only proof.
  The 2026-08-28 presentation-boundary repair first reproduced Rich replacing
  the process streams while a status was active, then proved the non-proxying
  live display preserves the facade capture across nested spinner transitions.
  Regression coverage also proved one completed row for two-member config-push
  readiness, one for exact agent-package preparation, and no leaked apply,
  provider, VM-manager, or SSH-push chatter. The 703-test affected VM-HA
  matrix, Ruff format/check, mypy, CLI help, and an isolated wheel build passed.
  Markdown lint retained only pre-existing `docs/design.md` heading and
  line-length debt. Installed-package parity, live convergence, and failover
  were not run.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-015 -->

<!-- REQUIREMENT: TI-REQ-016 status=active priority=P1 type=feature -->
### TI-REQ-016: Control automatic standby restoration for maintenance

#### User Story

Add an explicit, durable, cluster-wide maintenance policy that lets an operator disable and later re-enable automatic starting of the exact VM-HA non-owner standby through `nebius-vpngw vm-ha --standby-auto-healing enabled|disabled` while keeping new deployments enabled by default. Constraints: Preserve automatic owner-loss failover, former-owner fencing, exact shared-allocation ownership, route and forwarding authority, the rearm service as the sole Compute-start writer, existing `vm-ha` approval/output/exit contracts, and the current no-YAML policy boundary. The option is canonical with no alias. Disabling never stops Compute, never cancels an accepted provider operation, and never authorizes maintenance until both members agree and accepted start work is quiescent. Missing, corrupt, stale, foreign, unsupported, or split policy inhibits ordinary starts rather than enabling them; only an explicit digest-bound `enabled` recovery may initialize a missing exact-owner record and arm the single-use rearm exception. The pre-adoption private policy protocol may clean-break to one canonical v2 format with no legacy reader, mixed-version fallback, or migration shim. Non-goals: Disabling automatic owner-loss failover, adding scheduled expiry, a YAML setting, a second public maintenance command, direct CLI Compute start/stop authority, a one-shot policy override, changing controller transfer action order, or claiming availability while the standby is intentionally stopped.

#### Acceptance Criteria

- Initial VM-HA activation atomically installs an explicit committed `enabled` policy on both members before either rearm service can start. Ordinary apply, package restart, and same-deployment regeneration preserve and rebind the exact committed policy; member replacement installs it before activation. Removal clears it only after the existing two-member mutation-service quiescence barrier.
- Policy change uses one strict operation-bound two-member compare-and-swap transaction. A deterministic coordinator derived from the stable member identities prepares first and commits last; the peer prepares second and commits first. Every new transaction proves the same committed predecessor on both members, while a partial transaction admits only the exact same operation and desired state. Prepared state inhibits ordinary starts immediately, and terminal success requires both members plus a fresh current-owner reread to agree on deployment, generation, desired state, operation, phase, predecessor, and digest. A partial or conflicting transaction remains blocked and recoverable without a permissive fallback.
- Rearm rechecks the committed local policy and fresh authenticated matching peer-policy evidence under the existing writer lock immediately before Compute start. Automatic transfer admission and fencing remain unchanged; only standby restoration is inhibited.
- An enabled-policy transfer authorization accepted before the first automatic
  or planned transfer effect remains authoritative only for that exact
  promotion and standby restoration. A partially prepared disable cannot
  revoke an already accepted restoration after automatic promotion wins the
  authority race; the interrupted policy transaction must re-plan after
  redundancy returns. A transfer begun from committed disabled, missing,
  corrupt, or split policy never receives that authorization.
- `disabled` produces terminal maintenance-ready success only after both members commit disabled, ownership remains stable, and no accepted rearm cloud operation or transitional target remains. An earlier accepted start is observed to a bounded terminal result or returns nonzero action-required guidance; it is never cancelled or compensated.
- `enabled` may plan from the exact Running owner plus either its jointly acknowledged disabled policy or an unambiguously missing generation-bound local record when the non-owner is proven alias-free and `Stopped`. The missing-record plan predicts the canonical enabled initialization transaction from the exact cluster, generation, and member set and lists both member initializations as material effects. After exact approval, it initializes only the owner, verifies that receipt against the predicted transaction, and then persists one single-use recovery intent bound to the transaction, owner/allocation authority, stopped target revision, policy predecessor, and approval digest. The existing rearm service consumes that intent and remains the sole Compute-start writer; the CLI never starts Compute directly. After the peer reaches fresh current-boot standby readiness, the command idempotently initializes the peer, requires the same predicted transaction, waits for accepted-start quiescence and fresh two-member agreement, and clears recovery state only after terminal agreement. Corrupt, stale, foreign, prepared, or conflicting policy evidence remains blocked and is never bootstrapped. If final clear is interrupted after enabled agreement commits, the next explicit non-dry-run `enabled` or `disabled` invocation recognizes every present exact member-local completed recovery associated with that committed enabled operation. A recovery may carry either writer-valid policy binding: the committed decision when armed during the same enabled operation, or that decision's predecessor when armed to exit maintenance. Cleanup requires the two current policy records to agree on cluster, generation, members, operation, coordinator, predecessor, and decision; sends one full observed recovery-record digest to each member that owns a completed record; requires every requested compare-and-clear to succeed; rereads both policy records; and then completes the requested same-state no-op or opposite-state transaction in that invocation. Dry-run never clears recovery state and instead reports cleanup plus the requested policy outcome as one ordered no-effect plan. Any approval digest for such a combined plan binds the pre-cleanup authority, exact sorted recovery digest set, ordered cleanup effects, and requested policy effects, so a stale or clean-state approval cannot authorize cleanup. Missing, changed, foreign, inconsistent, active, or otherwise unsafe cleanup evidence remains blocked without starting the requested transition. The same bounded recovery path may resume an already-started partial policy transaction after a member loss, but only with a fresh authority-bound approval for that exact operation and desired state. Omitted policy input never changes policy; ordinary `vm-ha` may report an already-ready no-op but cannot start while disabled. Planned failover/failback rejects disabled maintenance before effects; automatic failover remains enabled and retains the disabled or partial policy after any emergency promotion.
- Policy preparation, commit, and recovery admission execute under the existing node-local writer lock and compare exact lifecycle generation, policy predecessor, apply/removal lock, mTLS inhibition, accepted rearm work, and pending or accepted controller-effect evidence before every write. Apply, replacement, removal, and mTLS admission symmetrically reject prepared policy or active recovery state. No persistent policy lock may inhibit automatic owner-loss transfer; if automatic transfer wins a race, the policy command stops before its next cross-node effect and requires a newly bound approval to resume.
- The option accepts only `enabled|disabled`, rejects invalid values before authentication, permits the existing `--dry-run`, `--approve`, `--output-format`, config, and region surfaces, and rejects candidate-conversion `--output` or `--force`. Interactive execution retains default-No confirmation; JSON and noninteractive execution never prompt. Exact plans bind desired policy, both member states, ownership, generation, in-flight operation evidence, and proposed effects. A same-state request exits successfully and says that standby auto-healing is already enabled or disabled. A completed opposite-state request exits successfully and says that standby auto-healing was enabled or disabled successfully. After the direct two-member transaction proves terminal agreement, the command may perform a bounded read-only reread while the public VM-HA projection's sole divergence is `standby-auto-healing-policy-invalid` under the exact owner and frozen cloud observation digest captured before the transaction. Every observed sample, including an otherwise terminal projection, must retain that owner and digest; this admits peer-heartbeat propagation but never repeats a policy mutation. Any authority change, additional reason, or exhausted observation budget fails closed. Human output does not expose internal recovery-cleanup steps; structured output retains bounded action codes for automation.
- Status exposes the closed policy through an `Auto-healing` summary value of `enabled` or `disabled`, preserves `transitioning`, `blocked`, or `unknown` when committed agreement is unavailable, and renders the exact config-bound enable command only for committed disabled maintenance. In that maintenance projection, the second-column `Redundancy` value `maintenance` and `Auto-healing` value `disabled` render in red so the intentionally reduced redundancy is conspicuous; the `Identity` value, explanatory details, Action command, status classification, and JSON remain unchanged. That maintenance Action is emitted on its own non-ellipsizing soft-wrapped line so the complete shell-quoted command remains copy/pasteable at narrow terminal widths. It never publishes internal policy records, operation identities, digests, raw exceptions, cloud identifiers, or paths other than the shell-quoted local configuration path in that disabled-maintenance Action; every other config-bearing action is parsed as shell syntax, replaces the complete config argument structurally with `<file>`, and never exposes a quoted path suffix. Local policy transitions emit bounded sanitized journal diagnostics without secrets or environment-specific identities.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Run focused CLI, planner, agent, rearm, status, apply, replacement, removal, heartbeat, and composed-runtime tests for parsing, approvals, idempotency, offline stopped-member enable, single-use recovery, every recovery and prepare/commit crash point, partial writes, opposite and concurrent operations, ownership/allocation/target-revision change, accepted-start overlap, missing/corrupt/stale policy, reboot and generation change, mTLS/apply/removal exclusion, and proof that automatic failover and fencing remain unchanged. Run Ruff, mypy, complete unit/integration suites, packaging, help, security, and documentation alignment. Installed parity and a separately authorized non-production maintenance cycle remain distinct proof levels.
- Offline implementation evidence covers the private v2 policy schema, deterministic coordinator-first/last CAS order, fresh authority checks before every remote CAS mutation, stable authority-independent transaction identity, exact approval-bound cancellation of an unconsumed recovery after pre-start authority drift, single-use recovery consumption and stopped-revision rejection, rearm crash-safe resume, mTLS/apply/removal writer exclusion, capability advertisement, strict enum parsing, maintenance/status behavior, and mode-neutral uptime. The terminal offline run passed 229 focused tests, 1,688 unit tests, 83 integration tests, Ruff, mypy, wheel construction, CLI/agent help checks, scoped diff integrity, and changed-line security review. Follow-up alignment added public dry-run and approved-policy coverage, exact completed-recovery cleanup retry and dry-run/opposite-transition guards, and established-SA retry validation; `make check` passed 1,701 unit tests with Ruff and mypy across 53 source files, all 83 integration tests passed, and wheel/help, spec, diff, and security checks remained green. Repository-wide Markdown format checking still reports pre-existing long-line and first-heading findings outside this task-owned section; no installed or live gateway behavior is inferred from local tests.
- Final remediation evidence passed Ruff, mypy, all 1,915 unit tests, all 84
  integration tests, focused narrow-terminal path-redaction and
  authority-drift projection regressions, diff integrity, and scoped security
  review. The editable
  installed CLI then completed a live disable/repeat-disable/status and
  enable/repeat-enable/status cycle on the reachable HA configuration: both
  opposite-state requests reported successful completion, same-state requests
  reported already set, disabled status printed the complete config-bound
  enable command, and final status was healthy with Auto-healing enabled and
  Action none. The second supplied configuration remained read-only because
  exact SSH trust was unavailable; no failover was exercised.

#### Test Method

- Run focused CLI, planner, agent, rearm, status, apply, replacement, removal, heartbeat, and composed-runtime tests for parsing, approvals, idempotency, offline stopped-member enable, single-use recovery, every recovery and prepare/commit crash point, partial writes, opposite and concurrent operations, ownership/allocation/target-revision change, accepted-start overlap, missing/corrupt/stale policy, reboot and generation change, mTLS/apply/removal exclusion, and proof that automatic failover and fencing remain unchanged. Run Ruff, mypy, complete unit/integration suites, packaging, help, security, and documentation alignment. Installed parity and a separately authorized non-production maintenance cycle remain distinct proof levels.
- Offline implementation evidence covers the private v2 policy schema, deterministic coordinator-first/last CAS order, fresh authority checks before every remote CAS mutation, stable authority-independent transaction identity, exact approval-bound cancellation of an unconsumed recovery after pre-start authority drift, single-use recovery consumption and stopped-revision rejection, rearm crash-safe resume, mTLS/apply/removal writer exclusion, capability advertisement, strict enum parsing, maintenance/status behavior, and mode-neutral uptime. The terminal offline run passed 229 focused tests, 1,688 unit tests, 83 integration tests, Ruff, mypy, wheel construction, CLI/agent help checks, scoped diff integrity, and changed-line security review. Follow-up alignment added public dry-run and approved-policy coverage, exact completed-recovery cleanup retry and dry-run/opposite-transition guards, and established-SA retry validation; `make check` passed 1,701 unit tests with Ruff and mypy across 53 source files, all 83 integration tests passed, and wheel/help, spec, diff, and security checks remained green. Repository-wide Markdown format checking still reports pre-existing long-line and first-heading findings outside this task-owned section; no installed or live gateway behavior is inferred from local tests.
- Final remediation evidence passed Ruff, mypy, all 1,915 unit tests, all 84
  integration tests, focused narrow-terminal path-redaction and
  authority-drift projection regressions, diff integrity, and scoped security
  review. The editable
  installed CLI then completed a live disable/repeat-disable/status and
  enable/repeat-enable/status cycle on the reachable HA configuration: both
  opposite-state requests reported successful completion, same-state requests
  reported already set, disabled status printed the complete config-bound
  enable command, and final status was healthy with Auto-healing enabled and
  Action none. The second supplied configuration remained read-only because
  exact SSH trust was unavailable; no failover was exercised.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-016 -->

<!-- REQUIREMENT: TI-REQ-017 status=active priority=P1 type=feature -->
### TI-REQ-017: Durably restore redundancy after every enabled-policy promotion

#### User Story

After any planned failover, planned failback, or automatic owner-loss promotion admitted while the cluster-wide standby auto-healing policy is committed enabled, automatically return the exact stopped former owner as a guarded warm standby without requiring another operator command. Constraints: Preserve the controller's stopped-former-owner, exclusive allocation, route, and forwarding order; keep rearm as the only Compute-start writer; keep `vm-ha` as the one public conversion, recovery, and convergence command; and preserve the existing planned-command phase budgets, result schema and exit codes, default-No approvals, configuration schema, and public status table. An artifact-changing approval adds the exact local wheel SHA-256 to its existing approval object without adding a command or flag. Do not widen the peer-heartbeat freshness window, use stale heartbeat liveness as authority, derive authority from presentation progress, add a compatibility reader, or let apply-owner adoption arm restoration. Non-goals: Making committed-disabled maintenance self-heal, retrying a permanent or identity-ambiguous start forever, hiding a safe cutover failure, adding a new public command or flag, changing automatic-failover admission, or granting rearm stop, allocation, route, firewall, or forwarding authority.

#### Acceptance Criteria

- A strict replay-protected policy-agreement certificate is created only from
  the exact committed enabled local policy and a fresh accepted peer heartbeat
  whose cluster, members, generation, policy digest, boot identity, sequence,
  and authenticated mTLS identity remain covered by the durable replay
  boundary. Planned requests capture it under `rearm.lock`; automatic transfer
  dispatch captures it immediately before the first accepted effect.
- Before the first external transfer effect, the controller durably arms one
  `standby-restoration-authorization-v1` bound to the exact intent, optional
  planned-request fingerprint, candidate and former owner, first effect,
  cluster, member set, generation and configuration digests, allocation,
  policy decision, and agreement certificate. It writes the terminal promotion
  receipt first, then advances that same authorization to committed before
  releasing the writer boundary. Restart recovery may advance only an exact
  receipt-plus-armed pair. Apply-owner adoption never creates an authorization.
- The authorization advances monotonically through `ARMED`, `COMMITTED`,
  `START_ACCEPTED`, `RUNNING`, `AWAITING_STANDBY`, and `COMPLETED`, or terminal
  `BLOCKED`. `START_ACCEPTED` is durable after the rearm `start-requested`
  checkpoint and before the `starting` checkpoint or provider call. One logical
  restoration retains one operation identity and provider idempotency key
  across restart and retry.
- While that authorization is active, rearm may bypass only the peer-policy
  heartbeat age check. Planned failover, planned failback, and operator retry
  must still reprove the exact local committed-enabled policy and decision
  digest. Only an already committed automatic-failover authorization may also
  retain authority through an exact prepared-disable successor whose
  predecessor is that accepted enabled-policy digest; an armed authorization,
  prepared enable, committed disabled policy, another predecessor, or another
  restoration source fails closed. Every admitted path still reproves the
  receipt, owner and allocation epoch, target identity and alias absence,
  lifecycle generation, apply/removal/mTLS quiescence, and accepted cloud-
  operation lineage immediately before start. Missing, corrupt, foreign,
  mismatched, superseded, or replay-invalid evidence fails closed.
- Retriable and ambiguous provider start failures use the same logical
  operation with at most five durable submissions per restoration cycle and
  delays of 5, 15, 30, and 60 seconds. A permanent failure blocks immediately;
  the fifth transient failure becomes `automatic-retry-exhausted`. Once Compute
  is Running, fresh standby readiness has an independent 300-second deadline.
  Expiry becomes terminal `BLOCKED`; it never moves ownership or disables the
  serving owner.
- A planned command exits `0` only after committed cutover and completed fresh
  standby restoration. If its restoration wait expires first, it exits nonzero
  and reports that safe cutover completed while durable background retries
  continue. The CLI deadline never cancels or consumes the authorization.
  `vm-ha` joins and observes active recovery; for an exact blocked or exhausted
  enabled-policy authorization it may render a digest-bound default-No
  operator-restoration plan. Missing, corrupt, disabled, or split policy
  requires the existing explicit `--standby-auto-healing enabled` recovery.
- Status is `TRANSITIONING` while authorized restoration or retry is active,
  `DEGRADED` after a safe blocked or timed-out restoration, and `HEALTHY` only
  after exact current-receipt `COMPLETED` plus the existing two-sample fresh
  owner/standby proof. Logs emit secret-free phase transitions and retry
  decisions without one-message-per-loop noise. The public status rows and JSON
  schemas remain unchanged.
- Every running controller status used to report `vm-ha` healthy or admit a
  planned transfer advertises `vm-ha-standby-restoration-v2`, and the fixed
  installed-agent capability document advertises the same feature. Missing or
  stale capability is apply-owned drift: `vm-ha` must not report healthy and a
  planned failover/failback must stop before its request or any transfer effect.
  Matching package versions or configuration generations never substitute for
  this runtime proof. VM-HA package preparation must reject fallback to the
  artifact recorded by the existing installation, bind the selected local wheel
  by SHA-256, verify the uploaded bytes before installation, and require a fresh
  installed-agent capability document containing the restoration feature.
- Every `vm-ha` approval that installs an agent artifact must select one
  existing non-fallback local wheel before approval, publish its exact SHA-256,
  and bind that digest into the approval domain. Planning must not build,
  delete, or replace a wheel. Revalidation under the canonical apply lock must
  rehash the same regular file before the first effect, and every member upload
  must verify those exact bytes. A missing, changed, ambiguous, symlinked, or
  capability-incomplete artifact fails before gateway mutation and requires a
  fresh plan.
- An exact `ACTIVE` deployment with one Running allocation owner, one
  alias-free Stopped non-owner, and a same-generation owner whose only runtime
  discrepancy is missing `vm-ha-standby-restoration-v2` must receive one
  approval-bound artifact-first recovery plan instead of entering the ordinary
  all-member SSH preflight deadlock. Under the canonical apply lock, that plan
  installs and proves the approved artifact on the owner, refreshes only the
  VM-HA guard/controller/rearm services, strictly revalidates unchanged cloud,
  lifecycle, generation, route, forwarding, policy, and writer authority, then
  asks the existing owner-side rearm writer to start the exact stopped peer.
  Before the artifact upgrade, the exact legacy projection
  `rearm=inhibited/standby-auto-healing-peer-policy-unavailable` or the known
  pre-v2 `rearm=inhibited/standby restoration policy authority changed`
  projection is admissible with no peer agreement and no accepted start. The
  first reason additionally requires blocked auto-healing and is limited to a
  bound committed-enabled local policy whose live peer proof is unavailable;
  the second requires transitional auto-healing, proving that the durable
  authorization still matches policy and only the mutable latest-agreement
  cache advanced during cutover. After the upgrade, transitional auto-healing
  is admissible only while the sole rearm writer owns an accepted start.
  After Compute reaches Running and exact-pinned SSH is ready, the same
  artifact is passed to the ordinary non-owner-first apply engine, which owns
  configuration, locks, activation, routes, forwarding, and terminal
  two-sample health proof. The CLI never starts Compute or mutates routes in
  the bootstrap lane. Any additional owner discrepancy, alias, ambiguous
  ownership, policy conflict, pending writer, or artifact drift fails closed.
- Active authorization blocks conflicting apply, removal, mTLS, policy, and
  replacement writers. `COMPLETED` is inert. `BLOCKED` grants no automatic
  start authority and can be retired only by an exact `vm-ha` recovery or a
  superseding current-authority plan, so it cannot deadlock maintenance
  indefinitely.

#### Negative Criteria

- No additional negative criterion was recorded before migration.

#### Validation Method

- Add strict store tests for certificate replay binding, exact identities,
  authorization transitions, corruption, retirement, and every crash point.
  Exercise the production policy path for planned failover and failback with a
  stopped-peer heartbeat older than 30 seconds, both ownership directions,
  automatic failover under enabled/disabled/missing/partial policy, typed
  retries and exhaustion with one provider key, CLI timeout with continuing
  service recovery, `vm-ha` join/operator recovery, writer races, status, logs,
  packaging, and service wiring. Run focused suites, Ruff, mypy, complete unit
  and integration suites, wheel/help checks, security review, documentation
  alignment, and diff integrity. Installed parity and clean non-production live
  planned/automatic trials remain separately authorized proof levels.
- Offline source/static implementation evidence on 2026-08-30: four frozen
  v1 receipt-digest goldens, exhaustive durable receipt/authorization tamper
  rejection, automatic-failure disablement across consecutive controller
  steps, deterministic SDK cleanup, nested command lifetimes, and tokenized
  Ruff-root parity passed with all 1,963 unit tests and 84 isolated integration
  tests. Ruff passed over `src tests misc`; cache-free mypy passed 56 source
  files; `uv lock --check`, workflow parsing, ten wheel/release-build checks,
  diff integrity, and changed-scope correctness/security/alignment review were
  green. README and changelog Markdown lint passed; repository-wide design lint
  retains the pre-existing first-heading and post-contract long-line baseline
  outside these task-owned additions. An isolated wheel build/install smoke
  proved the new internal module was packaged and the expected capability was
  executable, but it is not installed gateway parity. No live cloud, gateway,
  route, credential, or failover operation was run.

#### Test Method

- Add strict store tests for certificate replay binding, exact identities,
  authorization transitions, corruption, retirement, and every crash point.
  Exercise the production policy path for planned failover and failback with a
  stopped-peer heartbeat older than 30 seconds, both ownership directions,
  automatic failover under enabled/disabled/missing/partial policy, typed
  retries and exhaustion with one provider key, CLI timeout with continuing
  service recovery, `vm-ha` join/operator recovery, writer races, status, logs,
  packaging, and service wiring. Run focused suites, Ruff, mypy, complete unit
  and integration suites, wheel/help checks, security review, documentation
  alignment, and diff integrity. Installed parity and clean non-production live
  planned/automatic trials remain separately authorized proof levels.
- Offline source/static implementation evidence on 2026-08-30: four frozen
  v1 receipt-digest goldens, exhaustive durable receipt/authorization tamper
  rejection, automatic-failure disablement across consecutive controller
  steps, deterministic SDK cleanup, nested command lifetimes, and tokenized
  Ruff-root parity passed with all 1,963 unit tests and 84 isolated integration
  tests. Ruff passed over `src tests misc`; cache-free mypy passed 56 source
  files; `uv lock --check`, workflow parsing, ten wheel/release-build checks,
  diff integrity, and changed-scope correctness/security/alignment review were
  green. README and changelog Markdown lint passed; repository-wide design lint
  retains the pre-existing first-heading and post-contract long-line baseline
  outside these task-owned additions. An isolated wheel build/install smoke
  proved the new internal module was packaged and the expected capability was
  executable, but it is not installed gateway parity. No live cloud, gateway,
  route, credential, or failover operation was run.

#### Evaluation Method

Evaluate the acceptance criteria using the recorded verification method.

<!-- /REQUIREMENT: TI-REQ-017 -->
<!-- REQUIREMENT: REQ-017 status=active priority=P0 type=reliability -->
### REQ-017: Complete fail-closed Nebius collection discovery

#### User Story

As an operator, I need every `nebius-vpngw` command to classify the complete
Nebius resource set before it reports status or authorizes a mutation, so a
resource located after the first API page cannot be overlooked, duplicated,
or contradicted by a partial inventory.

#### Acceptance Criteria

- AC-001: Every production list-based Nebius discovery path used by a public
  command requests pages until the provider returns an empty continuation
  token, while preserving the request's parent, filter, and retry parameters.
- AC-002: A missing or non-iterable item collection, a non-string continuation
  token, a repeated token, a bounded-page exhaustion, or a duplicate stable
  identity fails the whole inventory with a sanitized context-specific error;
  no partial result is returned.
- AC-003: Discovery of a configured resource with a known parent and name uses
  the provider's exact-name lookup when available, validates the returned ID,
  name, and parent binding, and treats only typed `NOT_FOUND` as absence.
- AC-004: Read-only commands buffer a complete inventory before rendering it
  and distinguish unavailable evidence from an empty collection. Mutation
  commands acquire every decision-relevant inventory before their first
  effect, and any later inventory failure stops subsequent effects and cannot
  produce a success result.
- AC-005: Regression coverage exercises later-page matches and conflicts,
  cyclic and malformed pagination, provider failure after an earlier page,
  exact-name identity mismatch, and the affected status, preparation, apply,
  route, destroy, VM-HA, failover, and failback command paths.

#### Negative Criteria

- NC-001: The change does not add or alter public commands, flags,
  configuration keys, or persisted-state schemas.
- NC-002: The change does not add a legacy pagination path, SDK compatibility
  shim, or best-effort fallback from exact lookup to partial enumeration.
- NC-003: No create, update, delete, route reconciliation, or ownership
  transfer may be authorized from a partial or ambiguous collection.

#### Validation Method

Audit every public command leaf and every reachable Nebius collection read,
then run focused pagination and command regression tests plus the complete
unit, integration, Ruff, mypy, documentation, packaging, and alignment gates.

#### Test Method

Use deterministic multi-page and failing-page fakes around the shared helper
and each affected orchestration boundary. Assert complete request sequences,
stable sanitized errors, zero effect calls before full preflight, and no false
empty or success projection.

#### Evaluation Method

Map all public command leaves to either a hardened collection path, an exact
lookup, or a documented no-collection path, and confirm that every affected
test demonstrates the fail-closed postcondition independently of live cloud
state.

<!-- /REQUIREMENT: REQ-017 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD013 MD024 MD041 -->
