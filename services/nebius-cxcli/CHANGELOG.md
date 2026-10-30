# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Block direct Terraform apply while Tailscale gateways remain in state, including
  after removing or disabling their configuration. Resolve linked SecretStash
  credentials for policy commands without changing reviewed policy intent.
- Match managed MK8s project validation and infrastructure readiness to
  `inputs.cluster.parent_id`, retaining the project default when it is omitted.

- Enforce current Tailscale approval during enrollment recovery and route approval
  while preserving safe cleanup. Permit retirement after a draft tailnet changes
  only with durable proof that activation never began. Emit one JSON result from
  noninteractive onboarding configuration and correct journal-version guidance.

- Add infrastructure-first Tailscale onboarding with an exact administrator request, guided approval fingerprint, metadata-only SecretStash custody and SDK-only credential import/read. Bind unfinished enrollment to its original OAuth version, distinguish infrastructure from gateway readiness, and add explicit renewable Kubernetes workload identity without requiring ESO.

- Make Tailscale enrollment resumable through versioned operations, protected
  original keys and authenticated guest receipts. Add exact member/operation
  recovery, fenced unused-key replacement and ambiguous-issuance diagnostics.
  Retire partially provisioned gateways without capacity checks or enrollment,
  with independent key, device and cloud-removal confirmation. Reject older
  journal formats. Live lifecycle/HA qualification remains separate.

- Recheck Tailscale peer health on every VM-replacement retry and permit normal
  management CIDR rule removal without treating it as gateway retirement. Correct
  administrator-key check wording, diagnose incompatible source-build Go versions,
  and align lifecycle test fixtures.

- Add opt-in `tailscale-gw` with two persistent Nebius routers by default and
  customer-owned tailnet configuration. Integrate protected enrollment, exact
  ownership, readiness, staged maintenance and retirement into the ordinary
  lifecycle. Add `tailscale` auth, reviewed policy, route approval, status, doctor,
  client verification, bounded failover and stopped-writer recovery commands.
  Keep Tailscale credentials outside Terraform and preserve strict SSH trust.
  Local qualification is separate from live deployment and failover qualification.

- Fix WireGuard deployment reconciliation for fresh gateways without bootstrap
  clients. Align network-mode CLI help and contract coverage, and resolve type
  checks in the client verification paths.

- Keep WireGuard server and client MTUs within the detected uplink limit and
  persist scoped TCP MSS clamps. Reconcile guest networking without replacing
  the VM, boot disk, address, keys or peers; freeze the original bootstrap payload
  in the owned backend. Add explicit client MTU refresh and private Kubernetes
  verification, including automatic connected-client checks after deployment.

- Fix first WireGuard deployment falsely reporting a concurrent secret change
  when the SDK returns an unset primary version. Resume the recorded version
  without regenerating credentials; preserve idempotency and real conflict checks.

- Ask for each new infrastructure resource name only once during interactive
  `component add`. Reuse the initial answer in the field wizard, including
  WireGuard gateways and nested scalar names, while preserving name editing in
  `create` and fields not already confirmed.

- Replace WireGuard's free-text-only `local_subnets` prompt with live SDK subnet
  choices scoped to the selected project/VPC. Show names, IDs, destination CIDRs
  and shared-pool labels; preserve saved/manual routes and navigation, with no
  automatic new destinations. Initialize both WireGuard SDK authentication paths
  with cxcli's versioned user-agent to prevent the SDK `FutureWarning`.

- Align the full WireGuard wizard with its module and access design: an explicit
  tunnel-only action clears saved destinations; skipping a fresh gateway also
  removes its newly generated access component. Bound generated dependency IDs
  for long gateway names. Reject reserved SSH usernames, fractional/boolean UDP
  ports and invalid default keepalive values before Terraform execution.
  Strict validation now accepts the metadata-only access containers while
  rejecting payload/version fields there; ordinary secrets still require payloads.

- Expose the existing per-target ESO sync choice at the beginning of ordinary
  MysteryBox configuration. Default to No when inactive, support explicit ESO
  activation and namespace selection, preserve unselected targets, and include
  newly enabled ESO in the Apps wizard and component-add summary. Back/quit
  preserves completed work without committing unfinished target setup.

- Show MysteryBox Kubernetes Secret naming and ESO version-policy prompts only
  when an eligible target has synchronization enabled. Preserve selected-ESO
  defaults, explicit sync opt-out, existing metadata and WireGuard isolation;
  standalone secret entry no longer writes sync metadata or claims synchronization.

- Leave ESO selection to the user. Preserve automatic application-secret sync
  for selected ESO targets, honor explicit sync opt-out, and exclude WireGuard
  access containers from generic payload and Kubernetes sync workflows.

- New WireGuard deployments expose a dedicated metadata-only `mysterybox`
  component and exact `access.secretstash_ref` in config. A separate Terraform
  access root owns the containers and narrow IAM; the SDK owns payload versions.
  Include both roots in capture, recovery, validation and output lookup. Retain
  the access root and disabled owner after verified VM destruction; reject old
  SDK-owned generations instead of adopting them. Preserve live IAM group selection.
  Remove personal-key and private-host-key Terraform paths. Gate guest SSH on
  pinned identity, publish verified connection references, support login-key
  rotation/recovery, and retire access before destroy. Client issuance reads
  SecretStash directly without Terraform, Kubernetes or deployer-local keys.
  Align client connection examples and Terraform workflow restrictions with this
  lifecycle. Identify guest HTTP requests with the cxcli user-agent to avoid
  API edge rejection of Python's default urllib client before authorization.

- Preserve infrastructure registries when a component is named `components` or
  `external-clusters`, and handle null observability blocks consistently in
  scoped routing reads and writes.
- Require configuration v2 and remove the top-level `deploy` section. Move cluster
  and VM policy to infrastructure owners, external clusters to
  `infra.external_clusters`, and Soperator lifecycle policy to its application.
  Align create, component changes, validation, rendering, deployment, upgrades,
  status and recovery. No legacy parser or compatibility shim is provided.
- Generate manifest v3 with deployment metadata under `execution`. Reject old
  embedded source and manifest contracts before recovery publication. Finish old
  attempts with their matching CLI, then use full render/deploy to establish the
  new baseline; historical records are preserved.
- Fix NAT route activation rejecting route-only Terraform plans when Nebius
  recomputes subnet metadata and inherited pool defaults. Keep identity, CIDRs,
  configured pool selection, unrelated changes and replacements protected.

### Fixed

- Document the minimum Tailscale administrator handoff and the lifecycle steps
  automated through scoped OAuth, keeping company-specific setup outside cxcli.
- Bind administrator Tailscale key IDs before delivery, preserve started enrollment
  after rejected key replacement, and block destroy when any owned gateway is
  omitted from configuration. Validate recovery bindings before retrying issuance.

- Use the shared source-inspection helper for the Soperator onboarding ordering
  test, preventing stale imported line numbers from truncating the inspected
  function while preserving every lock, materialization and persistence check.
- Refresh the WireGuard CLI contract after the SecretStash command/help changes,
  restoring source and installed-wheel verification in `make all`. Align the
  documentation inventory and explicit SSH trust guidance with that lifecycle.
- Update CLI, inventory, schema and SSH error-redaction fixtures for WireGuard's
  required administrator group and lifecycle-owned keys. Keep personal-key
  rejection covered and verify that redaction tests reach the SSH runner.
- Align WireGuard SSH-option help, command modes, and day-2 design guidance with
  direct SecretStash access. Update strict-validation and client fixtures to
  enforce the new group-based access contract without deploy/Terraform fallback.
- Preserve complete SecretStash metadata during primary-version publication and
  accept an omitted resource version without mistaking it for a primary conflict.
- Resume owned secret versions by their recorded operation result, with exact
  parent and active-state checks, instead of depending on unretained labels.
- Compress WireGuard's embedded cloud-init files to fit the Compute user-data
  limit; qualify final size and lossless decoding through native Terraform in CI.
- Keep quota observation read-only even inside an active WireGuard deployment;
  prepare SecretStash and IAM explicitly before the final deployment plan.
- Report direct broad operator-group grants without changing them, and preserve
  exact grant context when provisioning is denied. IAM group selection remains
  live and identity-based, with no fixed group name.
- Reject existing client output before remote peer allocation, protect concurrent
  output writers, and identify the requested client after ambiguous failures.
- Assess retained filesystem quota independently of gateway bootstrap, preserving
  verified storage credit while a new WireGuard gateway has no SecretStash
  references. New gateway resources still require their full quota.
- Preserve WireGuard's journal owner when preparation is retried in the same
  deployment, so pre-effect failures cannot collide with or leak their own lock.
- Use the SDK's membership-specific listing method and response collection when
  provisioning or resuming the gateway service account's group membership.
- Omit unsupported names and labels from IAM membership and permit requests;
  prove ownership through journaled operation/resource IDs and exact specifications.
- Recover a lost membership or permit acknowledgement by resending its frozen public request
  with the original idempotency key under explicit stopped-writer ownership.
  Record definite request rejection before retry; preserve secret-write recovery
  restrictions and report only safe RPC status codes.
- Preserve the first sanitized provisioning error across same-deployment retries,
  so quota preflight cannot replace a permission denial with an ambiguity message.
- Preserve NAT's protected SSH host-identity generation, authenticated Compute
  recovery and atomic trust enrollment. WireGuard uses the separate SecretStash
  lifecycle described above.
- Reject missing or unusable gateway host entries before SSH and client-output
  mutation. Disable inherited trust bypasses and suppress secret-bearing remote
  errors; retain trust files with spaces or literal percent characters.

- Prepare NAT runtime inputs through its read-only preview owner during retained
  quota checks. Credit proven retained WireGuard and NAT address allocations so
  validation and rendering do not count existing public IPs twice. Keep full
  quota requirements for new, replaced or unproven resources, and reject preview
  before interrupted NAT recovery can mutate routes.

- Record executed Nebius API readiness checks for private infrastructure-only
  MK8s targets and require their saved report to pass before deployment succeeds.
  This fixes a successful deployment ending with a failed validation summary.

- Retain prepared NAT host-key inputs across repeated preflights and coordinated
  source/stage planners. Check matching NAT configuration and module identities
  before reusing those inputs, without copying unrelated credentials or recording
  private keys in recovery state. Recover retained identities before interrupted
  route restoration, without generating keys for absent or removed VMs.
- Prepare pinned Ed25519 host identities for fresh NAT gateways before provisioning
  and recover them through authenticated Compute metadata for strict SSH readiness,
  without serial-log enrollment. Keep private keys in protected runtime storage
  and sensitive provisioning state. Preserve existing pins across render and
  retain existing VMs; guest-generated identities continue to require independently
  supplied trust.
- Run NAT egress verification entirely through automated private test VMs and
  strict SSH through the gateway, including no-change deployments. Remove serial
  logging access checks and result collection. Configure temporary host pins and
  client access automatically, bound connection lifetimes and output, sanitize
  provisioning errors, and retain exact cleanup and route restoration.
  Run the isolated real-SSH probe integration fixture in a dedicated CI step
  that requires Docker rather than allowing a skipped test to pass.

- Include retained shared filesystems in source validation and shared quota
  assessment, using the fresh-plan check already used by deployment. An unchanged
  filesystem no longer requires its capacity twice; growth, replacements and
  unresolved evidence retain conservative quota checks.

### Observability workflow

- Default ordinary MK8s to the named Nebius-managed observability choice, with
  in-cluster and disabled alternatives. Align focused `grafana install` defaults,
  collector ownership and target-scoped configuration with the main workflow.
- Separate generic Database-group PostgreSQL from Grafana's database dependency;
  reject implicit old bindings with explicit identity-preserving migration guidance.
- Add a stable OTLP/gRPC trace address, application Pod metric discovery, retained
  inactive collectors and resumable staged handover without competing producers.
- Require temporary workload evidence through each backend and Grafana query path;
  report failures instead of readiness-only success. Managed preflight uses an
  explicit human Nebius CLI profile and performs no portal mutations.
- Preserve per-signal collection opt-outs and saved routes for re-enabling them.
  Explicit managed-mode selection resets custom writes and reserved query sources
  to Nebius defaults. Show Soperator's actual trace gateway, clear stale reports
  before deployment preparation, and include probe cleanup errors in reports.

### NAT gateway refactor

- Remove the normal NAT activation/preparation question. Wizard completion saves
  routing enabled and shows an informational route summary; deployment provisions
  the gateway, verifies readiness, activates routes, then runs enabled tests.
  Explain external attachment delegation before subnet selection. Test opt-out
  still activates routes; explicit YAML routing disable retains operational
  preparation/restoration. No compatibility path or new deployment command.

- Shorten NAT subnet rows by removing ownership annotations while preserving
  exact bindings. Remove `admin_cidrs` and adopt any-source IPv4, non-root
  key-only SSH with fail2ban (3 failures in 10 minutes, 1-hour ban). Select the
  exact client key and verify effective SSH, listener and ban-service readiness
  before cutover. Add isolated Ubuntu service/socket regressions to CI and
  document the detached matching-version upgrade for existing gateways.

- Preserve gateway choices and original workload routes when exact VPC identity
  resolves a saved ID to its managed binding. Reject incomplete VPC inventory,
  and keep the gateway excluded from workload choices across identity refreshes.
- Show eligible existing and planned gateway subnets immediately after VPC
  selection, alongside dedicated creation and refresh. Remove the separate
  create/reuse prompt, preserve creation defaults and valid saved selections,
  and backtrack through displayed steps without changing the configuration format.
- Fix empty workload selections looping through refresh, planned private-only gateway eligibility and route-snapshot loss during backtracking. Prevent component removal from bypassing pending probe cleanup. Bound probe transport with supervised processes so slow responses reach cleanup.
- Refactor the NAT wizard with live/planned network and subnet selectors, local backtracking, create/reuse placement and a 40 GiB new disk default. Give every workload subnet its own preserved-route table. Add default-selected private probes, per-activation restoration and durable cleanup/reconciliation; unavailable trusted SSH or gateway forwarding blocks testing before cutover.

- Use fresh Terraform plan evidence for standalone filesystem quota admission
  during generated deployment preflight. Credit retained storage so unchanged
  reruns do not require its quota twice; preserve growth, new-resource and
  replacement quota checks, and grant no credit for unresolved plan evidence.

- Verify managed CPU-only MK8s deployments without enabled in-cluster features
  through the Nebius SDK, including private-only clusters without local Kubernetes
  access. Check complete provider readiness again at final acceptance, label
  infrastructure evidence explicitly, and retain Kubernetes ownership checks for
  application, GPU, external and locally app-bound targets.

- Preserve the containing Terraform package for local WireGuard, SSH jump-host,
  NFS and NAT gateway modules so their sibling VM dependency initializes without
  a module-package escape error during render provider-lock generation.

- Preserve saved disabled collector rows when backtracking telemetry setup.
  Reconcile WireGuard IP reuse/create choices, validate keepalive as an integer
  from 0 to 65535, and allow resetting an allocation-name override to automatic.

- Default new ordinary MK8s creation/component-add targets to Nebius telemetry
  collection with local Grafana and storage disabled. Separate collection from
  local installation, show regional destinations and trace ingestion guidance,
  and preserve existing targets and dedicated Grafana/Soperator defaults.
- Replace bucket-label YAML entry with guided name/value editing. Show effective
  WireGuard public-IP, keepalive and NAT defaults, explain reuse/return routes,
  and keep automatic public-IP names derived from the gateway name.

- Configure SFS before ordinary MK8s node groups and display shared storage by
  name in each group's attachment checklist. Preselect all eligible filesystems,
  support explicit opt-out and text `none`, and confirm names instead of internal
  keys. Preserve the saved configuration schema and Soperator storage flow.

- Always show Infra then Apps multi-selection in `component add --config` when
  selectors are omitted, with the full field wizard for additions. Preserve
  confirmed choices on backtracking and resolve text IDs, numeric indexes,
  `all` and `none` against selectable components. Retain existing settings,
  target/dependency checks and configuration-only saves.

- Reconcile configured VPC choices with exact Terraform state and live resource
  identities. Show existing managed subnets once, preserve their bindings, and
  block automatic selection when ownership or discovery is unresolved.
  Reject malformed module inventories and prevent filtered discovery results
  from implying resource absence; keep resolution in the VPC module.
- Add per-node-group None, Label, Taint, and Both choices to the generic MK8s
  creation wizard. Persist native labels and taints, validate authored values,
  and show Pod placement examples without modifying workloads.

- Complete infrastructure-only deployments without requiring a cluster target
  during ordinary-app baseline publication. Preserve generation checks and
  explicit target-selection errors. Distinguish absent API watcher coverage
  from failed API access in Terraform progress.

- Strengthen NAT gateway address-space validation with pool-scoped occupancy,
  contiguous CIDR fits and combined fresh-plan demand. Block new reservations
  on insufficient or unresolved capacity while allowing well-formed draft
  saves. Preserve retained-subnet and route-restoration recovery, and verify
  realized gateway range and allocation ownership before activating routes.
  Require positive dedicated-pool evidence, preserve repeated prefix demand,
  and check derived-pool expansion against every affected upstream pool.

- Migrate all Project and Tenant discovery calls directly to IAM v2 before
  their v1 retirement on December 16, 2026. Preserve project-scoped access,
  optional name lookup and paginated choices; reject missing project identities
  and sanitize scope-validation failures. Keep the SDK dependency unchanged
  and retain the supported versions of other services, including AccessKey v2.

- Update the supported uv toolchain to `>=0.13.5,<0.14` and align cxcli CI,
  release, Nsight, and setup guidance so `make all` accepts uv 0.13.5 while
  retaining locked dependencies and hash-constrained wheel builds.
- Refresh Terraform status-test fixtures for NAT state preflight while keeping
  lifecycle guards active, and isolate token-test fixtures from ambient
  credentials and CLI profiles.

- Add the opt-in `nat-gateway` Terraform component and guided CPU-only SDK
  discovery, preferred `cpu-d3` / `4vcpu-16gb`, live quota admission, dedicated
  subnet and preserved routing policies. Deployment stages guest verification
  before cutover and records shared conditional recovery/restoration state.
  Add NAT inspection, route refresh and explicit recovery commands, plus strict
  SSH trust options on deploy. Direct Terraform mutations reject managed NAT.
  Validate provisioning even when routing is disabled, recover interrupted
  attachments from Terraform state, retain exact rendered module identities,
  and finalize retirement after a no-op retry. Preserve indexed configuration
  errors for malformed component rows.
- Update the locked certificate bundle to `certifi 2026.10.19` and development
  tooling to `setuptools 84.1.0`, including their upstream security fixes.

- Align the documentation contract inventory test with the verified profiling
  report destination requirement and feature.

- Normalize shared protected resources on both sides of profiling candidate
  comparison, avoiding false unrelated-application failures after a full render
  while retaining rejection of real application changes.

- Fix deployment preview and application resolution to consume frozen Grafana
  dashboard assets instead of reopening authored paths absent from private
  execution directories. Preserve UID and content/folder hash checks and reject
  missing or altered frozen inputs.

- Publish and reconcile a verified nonsecret Nsight report destination contract
  for automatic course delivery. Bind it to accepted storage and installation
  identity, gate new admissions during changes, preserve report data and
  credentials on reruns, and reject unsafe existing root permissions explicitly.

- Validate setup-uv by repository and full immutable SHA rather than a duplicated
  release hash, retaining exact tool/cache inputs, conditions and setup ordering.
  Dependabot action updates no longer require matching test-only hash edits.

## [nebius-cxcli-v0.1.9] - 2026-10-08

- Keep documentation checks valid after release preparation by checking the
  changelog and its linked detailed release notes together.

- Expand Soperator configuration, installation, upgrades, checks, recovery,
  storage protection, and profiling workflows, with frozen release identities
  and resumable, validated execution.
- Improve Grafana provisioning, dashboard imports, authentication, access
  instructions, and PostgreSQL persistence qualification.
- Harden Terraform, Flux, Helm, MK8s, SSH, secret synchronization, and deployment
  reconciliation, including resource selection and readiness checks.
- Improve CLI progress, diagnostics, command contracts, and operator guides;
  make editable `uv tool` installation the primary README setup path.
- Reduce Grafana import and Kubernetes credential-cache startup overhead while
  preserving authentication, ownership checks, and recovery behavior.
- Strengthen offline tests, Python compatibility, architecture and quality
  checks, installed-wheel verification, and release artifact validation.

The release includes the Python wheel and its pinned component-sources catalog.
See the [complete 0.1.9 change history](https://github.com/nebius/nebius-ps-services/blob/nebius-cxcli-v0.1.9/services/nebius-cxcli/docs/releases/0.1.9.md)
for detailed features, fixes, and operational constraints.

## [nebius-cxcli-v0.1.8] - 2026-03-23

- Fixed the `nebius-cxcli` CI and release workflows to run `nebius_cxcli.release_catalog` checks with the repo `.venv/bin/python` created by `make all`, avoiding bare-runner Python import failures under GitHub Actions.
- Hardened `tests/test_setup_build.py` against ambient GitHub Actions build env leakage so setup/build source-selection and release-ref rewrite tests stay deterministic in CI.

## [nebius-cxcli-v0.1.7] - 2026-03-23

- Removed the standalone `nebius` CLI dependency from MK8s kubeconfig handoff and token retrieval; `deploy`, `flux apply`, `flux bootstrap`, and generated customer workflows now use Nebius SDK-backed exec kubeconfig entries through `nebius-cxcli` itself.
- Generated customer workflows no longer install the standalone `nebius` CLI before Flux bootstrap.
- Aligned the main `nebius-cxcli` CI and release workflows to run the same local `make all` verification contract before wheel verification and release publication.
- Aligned CLI help/doc wording for auth profile/config flags and MK8s handoff behavior with the SDK-based contract.
- Tightened `bootstrap-ci` help/docs so the command and flag contract explicitly matches runtime behavior: target `config.yaml` must already be inside the customer git repo, `--github-repo` is only an auth-bootstrap override, and `--github-token-env` only affects GitHub bootstrap/secrets sync.
- Clarified in help/docs that `--cli-ref` selects the `nebius-cxcli` source ref used by the generated customer workflow, not the branch of the customer target repo; kept the option display aligned with Typer's default `TEXT` metavar.
- Fixed runtime version resolution for source/editable checkouts so `nebius-cxcli` now prefers live `setuptools-scm` git state over a generated `_version.py` cache, and `publish-release.sh --publish` now verifies local runtime version/tag alignment before pushing the release tag.
- Clarified MK8s node-readiness behavior before Flux work: `deploy`, `flux apply`, and `flux bootstrap` now probe first and only announce a wait when nodes are actually not `Ready` yet.
- Kept the local Flux phase under one continuous spinner after MK8s handoff so `deploy`/`flux apply` no longer stop and restart the spinner between cluster reachability, Flux API discovery, manifest apply, and rendered-resource readiness checks.
- Added a non-interactive fallback for those Flux phase updates so GitHub Actions and other non-TTY logs get stable printed phase lines instead of relying on transient spinner frames.

## [nebius-cxcli-v0.1.6] - 2026-03-23

- Simplified `bootstrap-ci` so reruns automatically reconcile the CLI-managed customer workflow to the latest generated contract; `--auth-bootstrap` remains enabled by default and workflow-only runs are now the explicit opt-out via `--no-auth-bootstrap`.
- Added regression coverage that `bootstrap-ci --help` and the command surface keep `--auth-bootstrap` enabled by default.
- Fixed customer-side Terraform plan/apply flows for private repos by persisting rendered tfvars in the generated manifest and recreating ignored `generated/infra/terraform.auto.tfvars.json` from that manifest before Terraform runs, both in CLI-generated bundle commands and generated customer workflows.
- Clarified and tested that `deploy <generated-dir>` remains a local/customer-side bundle operation only and does not auto-run `bootstrap-ci` or mutate GitHub CI workflow/environment state.

## [nebius-cxcli-v0.1.5] - 2026-03-22

- Added PR-side coverage for `bootstrap-ci` workflow generation across both development (`main`) and stable tagged (`nebius-cxcli-v<version>`) default CLI refs.
- Hardened `bootstrap-ci` to fail before writing the customer workflow when GitHub auth-bootstrap prerequisites are missing, and documented `--github-repo` as an override over target-repo auto-detection.
- Added explicit render profiles: generator-side `validate` and `render` now default to portable output, while `--render-profile local-dev` keeps checked-out Terraform module paths for workstation testing.
- Hardened generated-bundle validation and customer workflows with `validate-generated --portable`, so PR/apply pipelines reject non-portable local Terraform module sources before plan/apply.
- Simplified wheel/release packaging to bundle the portable catalog via the build override path instead of rewriting the working-tree root catalog during GitHub Actions builds.
- Aligned the generated customer workflow with the example repo by using a shared Python-version env and compact JSON discovery output for deterministic GitHub Actions matrix handoff.
- Added repo-side coverage that the checked-in local and portable catalogs stay semantically aligned except for Terraform module source addresses.
- Added direct tests for the `validate-sources` CLI command surface and GitHub environment-secret bootstrap helpers so those paths no longer rely only on indirect coverage.

## [nebius-cxcli-v0.1.4] - 2026-03-22

- Fixed packaged/bundled `component_sources.yaml` to always use the portable Git-backed catalog so source installs and customer CI no longer fall back to repo-local Terraform module paths.
- Added `bootstrap-ci --cli-ref` so generated customer workflows can be pinned explicitly to a branch, tag, or commit when validating nebius-cxcli changes end to end.
- Stabilized Flux bootstrap fallback coverage so tests no longer depend on live local `kubectl` state when asserting the bootstrap path.

## [nebius-cxcli-v0.1.3] - 2026-03-21

- Hardened release publishing so tagged wheels use the exact tag version and verify bundled portable component sources through shared release-catalog helpers.
- Limited release catalog ref rewriting to this monorepo's module sources and now fail release validation when external module sources are left on floating refs or local paths.
- Added PR-side coverage for release catalog rendering and wheel verification so release packaging errors are caught before tagging.
- Fixed `publish-release.sh --prep` changelog rewriting so moved release notes preserve Markdownlint-safe blank lines around lists and headings.

## [nebius-cxcli-v0.1.2] - 2026-03-20

- Prepare release `v0.1.2`.

## [nebius-cxcli-v0.1.1] - 2026-03-20

- Split the workflow model into generator-side commands for `config.yaml` and customer-side commands for deploying the rendered `generated/` artifacts.
- Added generated bundle manifests, stricter render reset guardrails, and customer-side validation for portable deployment bundles.
- Hardened local deploy and Flux apply/bootstrap flows with better readiness checks, clearer status output, and safer Flux recovery behavior.
- Aligned release packaging and GitHub workflows so published wheels bundle the rewritten portable release catalog instead of local development sources.

## [nebius-cxcli-v0.1.0] - 2026-02-22

- Initial scaffold for `nebius-cxcli`.
- Added `config.yaml` schema validation and deterministic renderers.
- Added Terraform, Flux, discover, inventory, and email commands.
