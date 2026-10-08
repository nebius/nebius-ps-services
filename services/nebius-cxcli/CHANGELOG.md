# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Make Nsight file-preservation tests independent of read-induced access-time
  changes while retaining content, inode, ownership, permissions, link count,
  and nanosecond modification/change timestamp checks.

- Align CI contract tests with merge-identity and result-evidence jobs, preserving
  Python compatibility, full Git history, and the one-build/many-consumer wheel
  graph. Check trusted result checkout before dependency setup.

- Inject the Kubernetes credential provider from the entrypoints, preserving the
  CLI dependency boundary and lightweight cache hits without changing token
  refresh, private-cache checks, or command output.

- Make editable uv tool installation the primary README setup flow, clarify
  Soperator path inputs, order profiling installation before access, and separate
  Helm-chart app upgrades from Grafana with matching navigation.

- Speed up Grafana imports by avoiding full CLI startup on Kubernetes
  credential-cache hits and redundant conversion of already-canonical dashboards.
  Preserve authentication, ownership and lease checks, version guards, serial
  recovery checkpoints, datasource selection and final readback. Keep the existing
  command and kubeconfig formats.

- Reorganize the README into a complete command-category index with selected
  installed-CLI examples and five focused operator/contributor guides. Remove
  historical experiment narratives and duplicate internals; correct recovery,
  storage-protection, observability, catalog, and command-effect guidance. Update
  both Soperator diagrams and check documentation examples, public command
  coverage, guide links, and catalog examples against the current implementation.

- Isolate Rich's cached ANSI styles in truecolor command-output tests so earlier
  standard-color consoles cannot alter their exact RGB assertions in CI.

- Simplify the README opening and put its complete table of contents immediately
  after the introduction. Group application updates, recovery and profiling with
  their workflows, remove course-specific setup prose, and link detailed Grafana
  and Nsight guidance from their existing guides.

- Make the Nsight installation highlighting test declare its simulated terminal
  capabilities so it also passes when invoked from a non-color terminal.

- Match generated MysteryBox bindings against the exact rendered ESO API version,
  rejecting malformed and alternate versions before application effects. Express
  Soperator CRD discovery as explicit exact-name comparisons to avoid CodeQL URL
  substring false positives while preserving resource selection.

- Preserve existing GitHub Environment branch/tag deployment restrictions during
  secret and variable synchronization; create an environment only after a
  confirmed not-found response.
- Wait for the current Flux resource generation before accepting Ready or
  terminal Stalled conditions, retaining the statusless OCI repository exception
  and Soperator's frozen-identity safety pauses and source-readiness checks.
- Keep complete Git history in release CI so historical tags pass main-branch
  ancestry checks and the tagged commit's parent remains available for validation.
- Reject ambiguous SSH jump-host and WireGuard component-type selectors;
  explicit instance IDs take precedence.
- Publish Soperator release identity pins atomically under the per-tag lock so
  interrupted writes do not leave partial records or prevent a valid retry.
- Load versioned local Helm chart directories directly and reject versions that
  disagree with their `Chart.yaml` metadata.
- Size missing boot disks using the selected disk type's allocation unit across
  MK8s node groups, MK8s defaults, and VM-style components; preserve explicit sizes.

- Validate full-stack Soperator campaign receipts on both read and write: bind
  identities and the ordered segment ledger to frozen intent, reject impossible
  maintenance/completion states, and preserve valid interruption recovery and
  completed-campaign final revalidation. Invalid receipts remain untouched and
  cannot trigger execution, archival or replacement; schema v6 is unchanged.
- Fail closed on rootfs inventory and cleanup inspection errors instead of
  accepting empty or partial evidence through a successful downstream shell
  command. Preserve the official image's POSIX shell and canonical digest format.
- Reject regular files selected as protected jail directories before admitting
  their retained storage bindings.
- Execute node-template and readiness stages once when catching up lagging node
  groups while changing OS or drivers without a control-plane version hop.

- Add `soperator profiling show CONFIG --target TARGET` to reconstruct two
  complete loopback HTTP/TURN forwards and one shared-password retrieval command
  from live, owned, ready Nsight viewers. Verify the recorded Soperator target and
  durable kubeconfig, preserve the current context and persistence opt-outs, and
  print exactly three highlighted commands without reading password values or
  changing cluster resources.

- Highlight copyable workflow commands and help examples with bold dark text on
  a light-gray background. Use one shared style across create/render/deploy,
  Grafana and Nsight access, quota, upgrade, recovery and connection instructions;
  preserve command text, color opt-outs and plain redirected output.

- Add `grafana show --config PATH --target TARGET` to verify live Grafana access
  and print copyable loopback port-forward and admin-password retrieval commands.
  Refresh verified local kubeconfig entries while preserving the current context
  and honoring persistence opt-outs; never read or print the password itself.
  Reject aliased username/password Secret keys before reading credentials.
- Include the same Grafana instructions and a `grafana show` reminder in successful
  deployment output and reports, including local-only observability and resumed
  or unchanged runs. Access handoff failures remain separate from deployment success.

- Wait for authenticated PostgreSQL queries after restart and volume reuse in
  the disposable Grafana persistence check, avoiding stale Grafana health-cache
  results without replaying imports or renewing the saved login session.
- Invalidate a Terraform root's cached initialization and validation before
  reinitializing it, including when a recreated directory reuses its inode or
  an initializer fails with different settings. Preserve other roots' reuse.
- Normalize ANSI styling in CLI flag assertions so the same checks work on
  color-enabled CI runners.
- Align workflow validation with the setup-uv v10.2.0 action pin from the base
  branch.
- Isolate mocked interactive progress consoles from the runner's terminal type
  so deployment, destroy, Grafana and Nsight tests also run under `TERM=dumb`.
  Align application-publication mocks with the current signature and returned
  manifest contract.

### Nsight installation progress

- Keep kubeconfig notices and handoff warnings on the progress console so they
  do not leave partial spinner lines beside the completed green-check row.
- Show descriptive spinners and elapsed time for profiling preparation, login
  Secret checks/creation, shared-jail package steps and final verification.
  Redirected output emits bounded `START`/`OK`/`FAILED` lines. Stop the display
  for credential input and hand terminal control to viewer deployment.

### Nsight login wizard

- Finish password-retrieval output with a newline so zsh does not display its
  partial-line `%` marker beside the password. Preserve the password bytes and
  decoder failure status.
- Default profiling installation to a login wizard for a missing Secret, with
  username `admin` and a masked, confirmed password. Retry blank passwords while
  preserving valid password characters. Existing credentials are reused without
  prompts or rotation; automation uses `--no-interactive` or `--password-stdin`.
- Print a copyable password-retrieval command after successful installation and
  on successful reruns, bound to the verified persistent kubeconfig and context.
  Cxcli never runs the command or prints the password itself.

### Native Soperator graph retirement and recovery

- Correct Grafana's native image digest value so the pinned chart emits one
  `sha256:` prefix. Reject malformed SHA-256 container image references during
  chart admission and assert the complete image in pinned-chart regressions.
- Publish resolved application files and compatibility metadata atomically so
  retries validate the same effective inputs. Recover stale private metadata only
  for checkpoint-proven target resources; retain strict authored replay, stage
  isolation and unchanged partial-install behavior.
- Admit native graph removals before checks and scheduling maintenance across
  deploy, Grafana installation and upgrade campaigns. Qualify the upstream
  telemetry token writer by exact ownership, source, Helm revision and workload
  inventory; reject unsupported removals before disruption.
- Resume the retiring child before its parent prunes it, using checkpointed
  identity-checked updates. Wait for Helm uninstall and Pod cleanup while retaining
  credentials and storage. Replay staged and stable parent publication safely.
- Recover an exact same-release failed-apply checkpoint through a sealed successor
  that preserves frozen inputs, controls, maintenance preimages and history.
  Recheck source writers and held-job identities; never use a same-release failure
  as fresh-install repair authority.
- Preserve the saved source configuration on resume instead of feeding generated
  Grafana and collector defaults back into admission. Carry native retirement
  authority through later campaign checks and catch-up recovery phases.
- Qualify later routing changes independently of completed retirement history;
  unfinished operations continue to require their exact recorded identities.
- Materialize explicit empty child values maps that server-side apply leaves null
  after pruning their old entries. Bind the conditional write to the suspended
  child and parent Helm publication, journal it before mutation, and recover
  interruptions before another parent publication. Other drift still fails closed;
  ordinary first-install staging is unchanged.
- Recover the corresponding typed VMAgent empty-map rejection after an exact
  failed SSA upgrade and successful rollback. Authenticate the desired manifest
  against the frozen chart and effective values, change only the owned field
  under suspension, and issue one journaled Flux reset. Recheck full resource
  identity and postimages across interruption; reject drift and repeated failure.
- Check active worker registration before maintenance restoration without
  requiring scheduling to be open prematurely. Preserve exact restoration
  ownership and strict schedulability checks before ordinary-user smoke.
- Accept retained Fast Dev/Test bootstrap checks without requiring native hook
  reports from waived diagnostic jobs. Reuse completed receipts, verify current
  passive policy and worker coverage, and retain mandatory final ordinary-user
  smoke. Standard diagnostic evidence requirements remain unchanged.
- Preserve identical frozen compatibility observations across midnight during
  lifecycle recovery while still running fresh support checks. Changed evidence
  remains subject to admission. Keep Fast readiness receipts outside render
  replacement so later replay retains their original proof.
- Preserve the outer operation authority during nested Grafana datasource
  verification, avoiding recursive fence callbacks while still stopping on
  authority loss.

- Compare effective Soperator settings during initial deployment observation,
  reusing final verification's selector and restored-partition semantics. Avoid
  needless reconciliation from unresolved outputs or equivalent default syntax
  while preserving real drift, storage checks and first-install ordering.

### Grafana chart admission

- Use the pinned Grafana chart's Pod IP binding without generating a duplicate
  environment variable that blocks Helm installation. Keep alerting peer addresses
  and database credentials unchanged.
- Reject duplicate environment names in native rendered workload containers
  before deployment, with resource-scoped errors that omit values. Preserve
  optional empty lists, separate container scopes and custom resource data.

- Admit a failed first PostgreSQL-backed Grafana install with no Deployment only
  after proving its exact terminal Helm revision, stored backend and retained
  credentials. Preserve SQLite rejection and ordinary retry ownership; do not
  reset Helm, alter history or recreate credentials.

### Terraform failure diagnostics

- Explain token-exchange DNS failures with connectivity guidance instead of
  recommending module repairs based only on a resource's source filename.
  Remove terminal color escapes from errors and preserve separate diagnoses
  when Terraform reports multiple failures. Deployment and retry behavior are
  unchanged.

### Grafana release-source reuse

- Reuse verified Soperator datasource defaults across one Grafana installation,
  including configuration reloads, saving and pre-render normalization. Avoid
  redundant GitHub lookups after successful preview verification; clear the
  context when the command finishes, fails or is cancelled. Preserve full
  required-package admission, release identity checks and frozen deploy snapshots.

### Deployment verification and timing output

- Remove the redundant full Soperator graph-readiness check during final
  observation. The desired-state verifier retains readiness and receipt freshness
  checks before storage, settings and final acceptance verification.
- Remove the fifteen-minute target-overrun message from deployment output. Keep
  elapsed duration, outcome, report path and structured timing measurements.

### Soperator wizard worker counts

- Default CPU and GPU worker totals to two hosts in the MK8s wizard and bundled
  Soperator profiles, including two of each shape for mixed profiles. Preserve
  explicit worker counts and existing nodes-per-group and autoscaling settings.

### Bounded deployment execution

- Reuse one Terraform observation for drift classification and admission, and
  cache successful initialization/static validation within an unchanged execution
  root. Restore recovery inputs before preflight; retain fresh execution plans,
  resource-scope checks and independent convergence verification.
- Reuse immutable release candidate preparation without caching authentication or
  live authority. Display same-version changes as reconciliation, and reserve
  dry-run wording for user previews.
- Stop permanent and unknown failures promptly instead of repeating whole release
  or campaign validation. Keep forward-only recovery, scheduling journals and
  original operation identities; retry only recognized safe transport reads with
  three attempts. Report sanitized causes before command/path context.
- Record interrupted campaigns as recovery-required while preserving active
  maintenance and completed work. Keep the original interruption if stop reporting
  fails, and resume without repeating completed mutations.
- Separate filenames and contents in preparation fingerprints so different
  Terraform file sets cannot share an ambiguously encoded input identity.

### Deployment input delivery and progress

- Fix supervised Helm rendering timing out when a values payload cannot fit in
  stdin during the first polling interval. Drain input and both output pipes
  together while retaining cancellation, deadlines and process cleanup.
- Keep the purpose in numbered Terraform plan inspection rows and show elapsed
  progress during Soperator observation, frozen-input checks, admission rendering
  and recovery replay. Plan numbers count snapshots, not failed attempts.

### Soperator Grafana routing identity validation

- Fix `grafana install` failing with `invalid final identity` when a collector
  routing patch precedes its child-release rename. Normalize both patches to the
  rendered child name, preserve routing values and order, and still reject
  missing, duplicate, or incorrect graph renames.

### Soperator required artifact admission

- Resolve verified source defaults before the `soperator create` wizard and admit
  the completed selection before saving. Validation, render, and Grafana use the
  same required-chart selection; disabled unused NFS charts cannot block them.
- Verify selected packages, dependencies, adapter consumers, operation phases,
  and final patched child values against official source without masking registry
  differences. Reject missing artifacts and graph drift.
- Bind v3 snapshots and the short-lived admission cache to each target and request.
  Deploy/retry use captured digests and patches. Reject older snapshots without
  migration; finish unfinished operations with the previous binary, or rerender
  when none remains.

### Current configuration deployment and local recovery

- Remove shared S3 deployment checkpoints, execution leases, `operation status`
  and `--lease-wait`. Terraform keeps native S3 state and locking. Obsolete cxcli
  backend objects are ignored without migration or deletion.
- Render current configuration with overwrite confirmation and preserve local
  command checkpoints. Deploy the latest complete rendered snapshot against fresh
  infrastructure and cluster observations. Matching local attempts resume;
  changed artifacts or semantic options select independent local attempts.
- Preserve dedicated Soperator upgrade, migration, reconciliation and destroy
  checkpoints and recovery rules. Serialize local writers with kernel locks and
  supervise contained processes. Use CI or operator scheduling across machines.
- Run Grafana installation end to end as save, render and normal project deploy,
  including pending changes and normal approvals. Unchanged setup reruns converge
  again without rewriting saved configuration.
- Keep completed profiling and ordinary Apps aligned with later deployments,
  preserve unselected targets in local completion reports, and keep malformed
  optional reports from failing a completed deployment. Recover pre-promotion
  Nsight failures from their exact local deployment attempt and failed Job UID.

### Soperator deployment profile clarity

- Explain Standard and Fast choices and show compact summaries of saved intent,
  published Soperator renders, and frozen deployment inputs. Distinguish native
  defaults from recorded overrides and reduced diagnostic coverage; Fast warnings
  no longer claim that waived passive diagnostics resume.
- Reject unsupported Standard one-GPU workers before saving project configuration
  or rendering. Keep existing profile policy, flags, defaults and recovery formats.

- Bind deployment release admission and execution to the generation snapshot,
  including its exact digest and embedded content. Prevent same-release
  reconciliation from looking up changed OCI tags before application rendering,
  and retain source/package, identity, and downgrade checks.

### Frozen deployment release identity

- Preserve every enabled Soperator target's frozen source and chart snapshot
  when deployment resolves application outputs, including ordinary reconciliation
  and target-scoped stages. This prevents unchanged version tags from selecting
  different packages during deploy; exact source/package verification stays
  mandatory and invalid snapshot evidence never falls back to discovery.

### Guided Grafana installation

- Explain selected native collectors, automatically configure datasource
  connections, and preserve stable datasource identity and authentication on reruns.
- After confirmation, always render current settings and run normal project
  deployment. A previous cancelled deployment is not a prerequisite.

### Fast deployment orchestration

- Avoid recovery-cache capture and backend publication for timing-only updates in
  an explicitly frozen, exclusively selected fast Dev/Test Soperator deployment.
  Keep ordinary/standard and mixed-target behavior, real receipt checkpoints,
  authority checks and required readiness unchanged.

### Developer quality checks

- Resolve Soperator test source by exact top-level function name instead of
  imported line numbers, preventing wrong-function assertions after source lines
  shift. Preserve lifecycle assertions and reject missing or duplicate definitions.
- Clarify the documented quality gates and remove the stale formatting-backlog
  count. Align deployment and observability formatting with Ruff and make
  observability's local types explicit without changing runtime behavior or
  weakening the quality baselines.

### CLI confirmation policy

- Share the fast-deploy configuration prompt between Soperator creation and the
  generic wizard while preserving the generic wizard's Yes default and explicit
  action approvals without defaults. Classify confirmation-policy tests by named
  owner instead of global call count, with regressions for blank input,
  cancellation and both callers.

### Soperator creation mode

- Default non-interactive `soperator create` to Standard unless a mode flag or
  values-file profile selects Fast. Interactive creation always asks, using a
  supplied mode as its initial selection and saving the final answer. Without
  a supplied mode, blank Enter reprompts. Update command help and preserve other
  commands, generic wizard behavior and existing deployment configurations.

### Deployment regression fixes

- Make fast Dev/Test admission independent of release-version and chart-digest
  allowlists. Verify rendered active/passive suppression and retain setup,
  operational hooks and immutable source identities. Allow unrelated custom
  Slurm settings while rejecting conflicting controls. Regression coverage includes
  4.1.9, 4.1.11 and future-version/image/disabled-script changes.
- Remove the generated health-check enable override that conflicted with new
  fast deployment defaults; standard deployments retain upstream health checks
  and explicit conflicting user overrides still fail validation.
- Validate target selection before writing timing reports and preserve original
  deployment errors when timing publication also fails. Restore timing context
  after failed writes so subsequent operations remain independent.
- Update wizard input, readiness callback and frozen-value fixtures for the
  current deployment-profile contract without bypassing production validation.
- Derive app target references before observability reconciliation so configuring
  another cluster does not duplicate existing cluster app rows.

### Local-first Grafana observability

- Add `grafana install --config PATH --target TARGET` and shared Apps wizard
  configuration for independent local, remote or dual metrics/logs/traces storage.
  Persist typed read connections separately from write destinations and auth.
- Add VictoriaMetrics, VictoriaLogs, VictoriaTraces, conditional OpenTelemetry
  collectors and optional Pushgateway as separate catalog Apps. Reuse native
  Soperator telemetry and frozen service identities; keep course files unchanged.
- Replace VMAgent destinations completely, including inherited URL/auth flags,
  and reject ambiguous positional queue transitions. Guard installation against
  unrelated changes and resume matching frozen operations without rerendering.
- Verify reconciled VMAgent URLs and Grafana connections. Document storage,
  collector, dashboard import and explicit cutover boundaries in
  [Observability](docs/observability.md).

- Apply shared private/local defaults to headless Grafana selection and existing
  standalone backend configurations. Remove wizard-added routing and dependencies
  when observability is disabled by backtracking.
- Allow explicit Grafana Apps selection to save native routing intent while keeping
  full deployment admission mandatory. Honor Pushgateway service overrides and
  remove stale external node-log pipelines when selecting local-only storage.

### Transient cloud provisioning events

- Keep waiting when a reconciling node group reports a recurrent
  `ComputeInstanceCreationFailed / UNAVAILABLE` event. Show the provisioning
  retry in deploy status instead of aborting Terraform before the cloud retries.
  Permanent errors, quota failures and existing operation timeouts remain enforced.

### Fast Soperator deployment for Dev/Test

- Replace the one-GPU diagnostic exception with `values.deploymentProfile:
  fast-dev-test|standard`. `soperator create --fast-deploy/--no-fast-deploy`
  selects a mode explicitly; non-interactive creation defaults to Standard and
  interactive creation always asks. Existing missing profiles remain standard;
  retired keys fail clearly.
- Keep required setup and operational hooks while disabling reviewed active and
  passive diagnostics in fast mode. Fresh installs avoid four full graph policy
  sweeps and finish with an owned ordinary-user Slurm smoke on active workers.
  Frozen recovery preserves profile, graph, coverage and exact job ownership.
  Bind active-check waivers to the reviewed chart bundle and reject custom
  diagnostic execution. Coordinated updates defer smoke to the parent after
  scheduling restoration; completed proof verification never submits or cancels.
- Derive vmagent write queues from worker capacity for both profiles, preserving
  explicit overrides and resizing new rendered generations without changing
  authored values. Keep telemetry alerting enabled.
- Persist structured deployment timings outside the execution cache, including
  nested exclusive duration and failure/interruption outcomes. The fifteen-minute
  two-worker target is measured, not an installation deadline.
- Preserve standard eight-GPU diagnostic coverage and one/eight-GPU Slurm GPU
  allocation checks. Recognize empty optional GPU-cluster maps during destroy.

### Managed worker GPU device defaults

- Derive missing Slurm GPU device lists from the selected worker preset instead
  of retaining an eight-device template on one-GPU workers.
- Track generated device lists alongside worker sizing defaults so wizard preset
  changes update them. Preserve explicit or untracked lists, registered topology
  and frozen deployment inputs; this does not add interrupted-install recovery.

### Deployment IAM identity recovery

- Defer service-account naming in the real Soperator creation wizard until the
  cluster name is selected, preventing temporary `mk8s` names from becoming saved
  project-wide identities.
- Check planned service-account creates for existing project identities before
  applying infrastructure. Report exact addresses and IDs for verified state
  recovery, and stop on inconclusive lookups without adopting or deleting accounts.
- Clarify backend retention through teardown and why deleting a cluster or state
  bucket does not clean up separate IAM resources.

### Soperator deployment recovery

- Offer exact-attempt interactive retirement for expired, unresolved fast smoke
  submissions after fresh queue/accounting checks. Preserve the original intent
  and an audited unknown outcome, checkpoint retirement before a new job, and
  require fresh smoke proof with no late active retired work before acceptance.
- Accept equivalent wait and refresh duration spellings during deploy recovery
  without rewriting saved controls or relaxing actual execution policy changes.
- Resolve the ordinary user's actual account home for fast Slurm smoke output
  and its explicit job working directory. Preserve canceled, unsubmitted intents
  during recovery; reject changed identities or homes for submitted job evidence.
- Start the fast-smoke scheduling budget after workspace preparation and the
  submission authority check. Avoid redundant authority checks around read-only
  polling while retaining fenced proof publication and cancellation. Record a
  proven pre-transport deadline rejection without reclassifying uncertain saved
  submissions or extending their deadlines.
- Recheck smoke-job submission authority after checkpoint publication so a
  delayed checkpoint cannot submit work after the operation loses its fence.
- Publish resolved, admitted application files directly during deploy recovery,
  avoiding drift from a second render of mutable execution configuration.
  Preserve target isolation, immutable chart bindings and publication checks.
- Restore frozen OCI chart bindings before validating recovered lifecycle files,
  including checkpoints captured between native rendering and artifact binding.
- Accept ordering-only differences in Soperator node-filter `In`/`NotIn`
  selector values during final deployment verification. Keep rejecting changed
  membership, selector keys/operators and unrelated value drift.
- Include the reviewed Soperator 4.1.9 and 4.1.11 monitoring-dashboard charts in the existing
  digest-bound source adapter. Preserve all seven upstream JSON dashboards while
  avoiding the invalid document separators rejected by Helm 4.1.1 post-rendering.
- Admit the exact pre-main dashboard repair through shared deploy checkpoints.
  Authenticate predecessor files and cluster admission, preserve failed history,
  and recover interruption between file and application-journal publication.
- Remove both generated dashboard child patches during that repair, validating
  ownership labels against the frozen graph and release. Keep unrelated wiring
  unchanged and reject missing, duplicated or altered child patch pairs.
- Admit the canonical failed pre-main dashboard apply checkpoint as well as an
  interrupted apply. Validate failure evidence and bind its stage-plan hash to
  the frozen standard or fast Dev/Test profile.
- Name the blocking HelmRelease and bounded Ready reason in stage timeout errors,
  without including raw condition messages.

### Deployment Object Storage transport

- Replace per-request AWS CLI processes with persistent Boto3 workers and
  independent lease/state connections. Bound startup, IPC and body reads by
  request deadlines; stop timed-out workers before ownership-aware recovery.
  Disable hidden SDK retries and keep conditional writes, object keys, generation
  identities and Terraform's native state/lock protocol unchanged.
- Reject filesystem buckets and anonymous policies overlapping backend authority;
  allow disjoint public prefixes and warn on disabled versioning without changing
  bucket settings. Clear inherited session tokens when exporting static Nebius
  credentials, including for Terraform, and report sanitized structured S3 errors.

### Persistent PostgreSQL-backed Grafana

- Pin Grafana chart 13.2.5/application 13.2.2 and add PostgreSQL chart 0.20.6 with
  PostgreSQL 18.6, a retained 10Gi RWO PVC, private SCRAM connectivity and
  NetworkPolicy. Automatically select/order PostgreSQL with Grafana per target.
- Default Grafana to two replicas with shared database/encryption credentials,
  disruption budget and alerting peer discovery. Bootstrap runtime-only Secrets,
  reuse owned credentials and reject missing credentials against retained state.
  Standalone PostgreSQL receives its own bootstrap. Reject existing SQLite;
  provide no migration or compatibility path.
- Make imported API dashboards editable. Restore missing dashboards during deploy
  while preserving existing UI edits; explicit import --overwrite replaces edits.
  Qualify pinned charts, APIs and disposable PostgreSQL replica persistence.
- Reject alternate Grafana startup/configuration paths and mismatched pending
  HelmRelease bindings before mutation; inspect the actually mounted configuration
  so unused ConfigMaps cannot mask another database.

### Explicit Kubernetes target selection

- Require explicit Kubernetes target contexts across kubectl, Helm and Flux
  process boundaries; remove ambient current-context and inherited GPU context
  fallbacks, reject targetless cluster workflows and ambiguous context history,
  and verify reused contexts against the selected cluster's API endpoint and CA.
  Preserve source-relative credential paths in isolated kubeconfig copies.
- Identify the failed Flux controller on rollout timeout and retain bounded,
  sanitized diagnostics from both output streams. Preserve existing wait limits.

### GPU capacity handling

- Let deploy continue through temporary GPU Capacity Dashboard shortages while
  enforcing actual tenant/project quota allowances, including shared GPU demand
  and unavailable capacity telemetry. Show recognized provisioning schedule
  timeouts as pending cloud capacity without aborting Terraform early; retain
  terminal API errors, execution deadlines and truthful readiness acceptance.

### Soperator offline regression fixtures

- Restore onboarding and upgrade tests after source-owned registry handling
  changed: supply registry metadata, use typed frozen snapshots, and include the
  frozen repository in interrupted-admission fixtures. Preserve region,
  ownership, publication, authority-loss and recovery assertions without
  changing production validation or enabling network access.

### Authentication retry diagnostics

- Render SDK token-refresh timeouts as concise warnings during commands,
  including Python timeouts with empty messages. Preserve SDK retry limits,
  terminal request failures, and unrelated error diagnostics; restore logging
  filters on completion or interruption. Stale GPU capacity remains unresolved.

### Adaptive Soperator release discovery

- Accept the reviewed ActiveChecks waiter after a registry hostname change,
  fixing manifest generation for Soperator 4.1.9. Keep the image repository and
  version, executable template and complete install/upgrade hook inventory
  strictly checked without changing the frozen upstream chart.
- Follow the selected upstream release's verified OCI registry through creation,
  validation, upgrade and immutable Flux sources. Fix the 4.1.9 backup chart
  being misclassified after upstream changed its registry hostname.
- Discover additive source-owned and third-party charts from the verified release
  graph and select enabled children from its Helm render with effective values.
  Preserve adapter roles, immutable artifacts, required structural interfaces,
  dependency checks and frozen recovery; unused source-only charts are not pulled.
- Include default-enabled children hidden by optional features and remote nested
  chart dependencies in artifact discovery. Keep alternative configurations'
  dependency ordering separate during effective graph validation.

### Automatic project authentication

- Make normal project commands, including `create`, reconcile confirmed managed
  IAM permission drift automatically through the operator's IAM authority.
  Reuse the canonical service account and healthy authorized key, verify the
  repaired permissions with canonical credentials, and keep healthy reruns free
  of IAM writes. Preserve strict ownership checks and read-only preview/CI import
  behavior; provider failures do not trigger permission repair.

- Keep normalized configuration in memory during context loading; persist changes
  only through explicit writers.

### Grafana dashboard workflows

- Allow `grafana import --overwrite` to update editable classic file-provisioned
  dashboards while preserving their UID, folder and management/source metadata.
  Warn that provisioning may replace the edit; reject other managers, denied
  edits, folder moves and managed catalog attachment before prompts or writes.
  Save managed cluster copies as manual-only declarations with bound provenance;
  exclude them from catalog suppression and automatic deployment replay.
- Keep identical imports free of saved API writes, version bumps and local file
  rewrites. Preserve completed receipts on no-ops, start fresh version-bound intent
  after subsequent remote edits, and retain interrupted-write conflict guards.
- Reuse renewable Kubernetes authentication setup
  across selection and fetch discovery inventory once; retain fresh locked
  admission, credentials, Grafana connection and datasource/ownership checks.
- Show Grafana import stages with a spinner and elapsed time during access,
  validation, publication and installation. Pause for datasource selection;
  keep redirected progress on stderr and report success after session cleanup.
- Recognize `KeyAlreadyExists` conditional-create responses for Deployment
  leases so expired leases reach guarded takeover and active owners remain blocked.
- Replace free-text datasource UID prompts during interactive API import with a
  searchable selection of compatible existing datasources. Show names, types and
  UIDs, require Enter for a single choice, reuse mappings across the batch and
  recheck availability before publication. Preserve explicit mappings for
  automation and cancel cleanly before new-batch writes.
- Add eight labeled examples to `grafana --help`, covering file/directory imports
  with and without attachment, external token and manual SSO import, cluster
  export, and offline validation. Explain target IDs and immediate installation
  independently of catalog attachment.
- Replace the old Grafana export/attach flags and top-level dashboard validator with `grafana import`, `grafana export`, and `grafana validate`, without aliases.
- Install file or directory imports immediately; cluster imports save project desired state and optionally attach reusable catalog JSON. Bind cluster access to accepted identity and the existing admin Secret; external APIs require explicit token selection, while interactive SSO prepares a manual browser import.
- Guard dashboard ownership and versions, preserve mixed datasources, recover interrupted local publication, reconcile repeated imports, and replay frozen declarations after application readiness. Use persistent PostgreSQL for new managed installations as described above.
- Add digest-pinned Grafana API qualification and command/publication/replay regression coverage.
- Fix CI and release workflow tests to account for the Grafana API qualification
  command and verify its locked execution and release condition.
- Reject attachments that would duplicate an existing catalog UID under a different provider or key, including folder moves, before publishing project state or installing dashboards.
- Honor the catalog-selected Grafana component ID when validating, saving and rendering dashboard imports. Add copyable cluster, token and SSO examples to Grafana command help.

- Preserve rendered Grafana dashboard exports as JSON identical to the installed
  ConfigMap data. Restrict ordinary-app ownership annotations and YAML
  serialization to Kubernetes resources in the Flux bundle.

- Bound portable recovery caches to current project state and the staged files
  required by a pending transaction, preserving local historical copies and the
  64 MiB object limit. Retain ordinary-app acceptance baselines and rendered
  dashboard JSON during recovery,
  reject incomplete pending generations, and allow completed journals to verify
  current targets without historical staged directories.

- Carry the admitted infrastructure plan digest into the Soperator no-op and
  completed-release application handoffs, so full deployment can verify and seal
  the operation after release reconciliation without weakening identity checks.

- Exclude retained failed Nsight attempts from Soperator service readiness only
  after verifying their admitted Job/Pod identity and a terminal successful
  successor for the same operation, stage and PVC. Keep unresolved attempts and
  unrelated unhealthy services blocking, without deleting diagnostic history.

- Keep sanitized command failure output visible in bounded Soperator supervisor
  messages even when the failed patch command exceeds the display limit.

- Preserve the mutable compatibility-admission report outside render generations,
  so fresh validation does not invalidate a committed Soperator upgrade on retry.
  Resume completed render publications using their original transaction and
  preimage digests, independently checking current render-owned files without
  rewriting operation identity or replaying completed deletions. Carry the checks
  proposal from the immutable deployment inputs through release recovery.
  Recheck the accepted jail projection on resume, matching the effective inputs
  admitted on the first run before restoring mutable execution files.
  Apply the existing shared-resource rules when combining protected and ordinary
  desired-resource inventories, preserving the protected namespace declaration
  and rejecting conflicting labels, annotations, or specifications.
  Compare unbound Soperator Helm values independently of YAML mapping order,
  while preserving scalar types, sequence order and bound bundle identities.

- Derive new managed Soperator worker CPU and memory defaults from the selected
  VM preset using the Nebius reference deployment's system and sidecar reserves.
  Preserve explicit numeric settings, reject impossible allocations, and describe
  physical CPU topology independently of the container quota. Remove the small
  fixed worker defaults and obsolete sizing fallback; registered workers and
  frozen recovery generations retain their existing ownership.

- Apply ordinary Flux bundles server-side with kubectl's default field manager so
  large Grafana dashboard ConfigMaps do not exceed the client-side apply
  annotation limit while retaining client-side ownership migration. Preserve
  ownership checks and fail on field conflicts
  without forcing another manager's changes.

- Align installation recovery guards: finish omission repair with verification
  only, reject missing accepted OCI consumers, and recheck ownership fences after
  completion preparation and historical-record reads before cluster mutation.
  Cover composed repair execution and authority loss at those boundaries.

- Make repeated profiling installation reuse completed stages, reserve bounded
  successors for proven terminal standalone installer failures, and restore only
  receipt-owned missing regular files from admitted cached archives. Preserve
  credentials, report data, conflicting files and failed attempt history.
- Reconcile stale accepted initial-install records inside existing profiling and
  ordinary app workflows after fresh identity, ownership, storage, readiness and
  writer checks. Resume partial metadata updates through immutable backend
  receipts without inventing historical success. Bind future terminal operation
  receipts to the exact desired application bundle before deployment acceptance.
  Preserve running read-only report viewers while rejecting unknown shared-root
  writers and verifying persistent Slurm adapter ownership.
- Preserve chart-source ownership metadata when binding OCI digests. Ordinary
  apply may reuse an unchanged, ready, accepted digest-pinned source as a read-only
  prerequisite only through the same accepted and owned Helm consumers; it never
  adopts or relabels that source. Reject changed or ambiguous sources.

- Fix ordinary app deployment on accepted Soperator clusters by calling the
  baseline validator from its owning module during live target observation.
  Cover the real observation path in public application-dispatch regressions.
- Seal the Soperator reconcile operation anchor after target postconditions
  under the current deployment fence, before completing installation. Reject
  missing anchors and preserve incomplete status after validation or fencing
  failure, so successful deploys do not leave ordinary apps blocked by an
  unfinished operation record.

- Support HTTP/HTTPS Helm repositories alongside OCI in shared deployment and
  application admission. Require exact HTTP chart identity and compare a fresh
  download with the rendered content snapshot before effects; reject drift and
  unavailable sources. Preserve native Flux ownership and OCI digest pinning.
  Document HTTP's publisher-trust boundary after admission; no registry mirror
  or new credentials are required for public HTTP repositories.

- Use Grafana's official community OCI chart at the existing pinned version,
  allowing shared deployment admission to bind its assessed artifact digest.
  Support mixed HTTP and OCI app bundles through their respective source checks.

- Add Nsight Systems and Compute viewer Apps with guided PVC/Secret selection,
  private ClusterIP defaults and complete laptop port-forward output. Add
  `soperator profiling install` for pinned shared CLI installation, fenced
  recovery and separate customization verification before jail promotion.
  Preserve administrator profile symlinks, reject missing recorded Jobs before
  replacement, and direct active profiling recovery to its original command.
  Add explicit `soperator profiling recover` for exact terminal failed Jobs in
  initial installation and passive jail customization, with read-only admission,
  immutable three-Job stage budgets and original-command continuation. Freeze
  package/profile preimages, authenticate partial package states and use cached
  archives only. Tighten viewer values and naming; add official-chart rendering
  and native Ubuntu/architecture package CI. Missing Jobs remain blocked.
  Name missing viewer login Secrets and required data keys in prerequisite
  errors, distinguish read failures without exposing subprocess output, and
  document existing credentials in profiling install help. Add a masked
  `--interactive` login wizard, `--password-stdin` automation and default username
  `admin`, preserving existing credentials on repeated installation. Configure
  AppArmor explicitly for private mount setup and add an exact, history-preserving
  `--repair runtime-mounts` recovery for pre-admission mount denial.
  Pin the main package-job runtime to Ubuntu independently of the population
  image and add exact `--repair runtime-image` recovery for its BusyBox mismatch.
  Native package CI now uses that production runtime and capability boundary.
  Activate pinned tools after Soperator's CUDA PATH setup through one owned late
  shell hook; add exact `--repair profile-order` recovery without changing frozen
  package installer bytes. Test native fixtures with competing CUDA tool paths.
  Accept standard node topology labels added to scheduled protected Job Pods
  while preserving checks on declared labels and execution settings.
  Remove Helm 4's obsolete `list --all` flag from the viewer/app ownership check;
  require Helm 4 before inventory so older clients cannot omit pending releases.
  Retain checks against releases in every status before applying resources.

- Bind upgrade planning to its original configuration and reject concurrent edits
  before desired-state publication. Keep discovery provider errors sanitized,
  remove unreachable legacy discovery helpers, and direct interrupted campaign
  recovery through `deploy` consistently in status, runtime guidance and docs.
  Status distinguishes known frozen Slurm options from deployment controls that
  cannot be reconstructed from local receipts.

- Align the canonical deploy help snapshot with guarded recovery, retain failed
  outcomes after final deployment verification or acceptance, and preserve the
  primary exception if report publication or footer output fails.
- Extract protected-directory observation from the CLI without changing storage
  guards, and include every integration-marked test in the Make integration lane.

- Show activity during final live-cluster and acceptance verification, and keep
  follow-up commands pointed at the original generated bundle after execution
  cache cleanup.

- Scope completed observability repair history to its original operation.
  Verify the sealed completed successor and exact later install inputs before
  resuming a separate installation without reattaching the old repair.

- Keep Slurm action identities stable across tuple/list JSON persistence and
  repair only the proven node-tuple hash defect during exact install recovery.
  Require the frozen install receipt, matching journals and gate scope, retain original events, and persist
  the correction through the fenced journal owner before restoration.
- Restore diagnostic reservations through their recorded owner, including
  install reconciliations using the existing scheduling journal. Preserve the
  accepted checks and release intent on resume.
- Keep known unsupported passive policies enabled from the first maintenance
  apply, fail immediately on enabled-policy drift, and restore the native
  acceptance phase label after staged configuration.
- Compare Helm values using client-side apply's null-map deletion semantics
  while still rejecting extra keys and stale non-null settings. Pause live
  progress while prompting for extended acceptance, then restore it on answer
  or interruption.
- Preserve frozen ordinary OCI chart sources during the post-Terraform refresh
  and reject unrelated application drift before cluster writes, allowing sealed
  install recovery to retain its exact replacement-file identity.
- Resume one proven initial Docker image-download connection reset per worker
  through the existing Soperator deployment, preserving the failed native Job,
  operation, reservation and successful peer evidence. Verify ownership, runtime
  health and exact drain attribution before one distinct native replacement.
- Replace repeated acceptance poll lines with elapsed heartbeats, distinguish
  application resolution phases, expose terminal Slurm job identity/state/exit,
  avoid duplicate terminal completion redraws, and retain projected deployment
  summaries after failure cleanup.
- Label skipped GPU probes accurately and recognize statusless OCI
  HelmRepository objects without bypassing readiness for other Flux sources.

- Change the Soperator acceptance hint to "Ctrl+G interrupts extended testing
  safely" and color Ctrl+G green and Ctrl+C red in color-capable terminals.

- Use frozen upstream Soperator observability defaults and its DCGM exporter.
  Accept native `values.observability`; reject the removed
  `values.soperator-dcgm-exporter` subtree. Keep bundled Grafana disabled and
  remote Nebius Grafana as the default UI.
- Keep the extra observability agent opt-in on Soperator, default its
  infrastructure scraping off, preserve explicit signal/custom-target settings,
  and isolate GPU exporter and node-label handling from ordinary MK8s targets.
- Report upstream Soperator telemetry configuration separately from optional
  apps and unverified live readiness/ingestion.

- Publish onboarding app-only acceptance after successful deployment registration,
  bind the exact generation, and keep admission closed after a concurrent source edit.

- Support guarded Soperator ordinary-app rendering and Flux apply without Terraform
  reconciliation or Slurm maintenance, fenced by accepted deployment and target identity.
- Support catalog-declared internal Prometheus datasources without cloud headers and
  private Grafana API checks through temporary loopback port-forwards.
- Bind ordinary baseline publication to the executed generation and reject stale
  bundle preimages before publishing app-only acceptance.

### Added

- Ask whether to protect additional rootFS directories on every interactive
  `soperator upgrade`. Preserve existing folders through retained PVC mounts
  without copying or moving data. Pin physical rootFS generations that contain
  protected folders and allocate fresh backing when their logical slots are
  reused. Freeze selections and storage identities through shared deployment
  and recovery; dry-run leaves desired configuration unchanged.

- Consolidate MK8s deletion under `destroy CONFIG --target CLUSTER_ID`, for
  managed and onboarded clusters with or without Soperator. Require the immutable
  Nebius cluster ID even for one cluster; support `--yes` after identical checks.
  Remove the nested Soperator teardown command with no alias or compatibility
  layer. Preserve generic teardown only for projects without MK8s, and guard
  low-level Terraform destroy against MK8s config, generated targets and state.
- SDK-only `destroy` for managed and onboarded MK8s clusters, with one
  cluster-delete request and no Kubernetes teardown dependency. Delete dedicated
  GPU clusters and cluster-owned attached/detached PVC disks through the SDK by
  default; `--preserve-pvc-disks` retains PVC disks. Block shared GPU references,
  unproven disk ownership, protection and locks before cluster deletion; bound
  disk deletion to eight in-flight operations with resumable receipts. Preserve SFS by
  default; `--delete-sfs` deletes only confirmed attached, dedicated storage after
  detachment, while respecting deletion protection. Add backend-authoritative `nebius-cxcli.destroy.v1`
  recovery, operation correlation, durable mutation admission and frozen normal
  Terraform reconciliation before publishing the remaining project generation.
  Reconcile SDK-deleted GPU clusters as absent in Terraform. Support only current-format
  destroy receipts; reject older local/backend records in every status without
  compatibility, import, archival or migration.

- `soperator status --show-checks` for full recorded check history. Default output
  summarizes outcomes and shows at most five recorded failures or cancellations;
  unavailable history warns without changing verified current health or exit status.

- Soperator status progress with a spinner, current activity and elapsed time;
  colored component readiness and bounded read-only Slurm health queries; and
  a final overall-health summary. Healthy details are empty, while other rows
  describe issues and operational impact. Partial or unhealthy live reports exit
  nonzero; idle lifecycle wording and one installed-release line remove ambiguity.

- Shared Soperator `--acceptance readiness|full` profiles, a default-No terminal
  prompt after required readiness, and an unattended full default. Ctrl+G
  durably requests safe finishing across targets; Ctrl+C preserves interruption.
  Exit success remains gated on required work, restored Active/Passive checks,
  reopened scheduling and released maintenance. Record skipped/cancelled tests
  separately from passed diagnostics.
- Packaged `compatibility-matrix.yaml` with exact profile version sets, strict
  evidence rules, native Helm dependency constraints, Terraform/provider
  admission and intermediate upgrade assessments. Unknown support warns;
  known incompatibility, missing hard evidence and integrity failure block.
- Frozen selected catalog/chart inputs for output hydration and recovery, OCI
  digest binding, and operation-owned compatibility reports. HTTP chart
  admission verifies exact identity and freshly fetched contents against the
  rendered snapshot; Git chart execution remains unsupported.

### Fixed

- Format the Nsight protected-job calls without changing their Python AST.
  Refresh REQ-028's criterion-level offline evidence and verify FEAT-030,
  with regression coverage for fresh prerequisite ordering, upstream Grafana
  preservation, and absence of implicit dashboard API imports. Keep live and
  native AMD64 qualification separate from source and package checks.

- Reject obsolete Soperator values in generated deployment bundles before
  authentication, backend setup, and Terraform preflight. Retain strict native
  values validation and require a fresh render after correcting source inputs.

- Reject changed frozen Soperator bundles on interrupted install recovery even
  before the first application journal entry. Replay saved inputs without
  current Terraform outputs and preserve deterministic NFS and secret bindings.

- Preserve the saved application bundle when resuming an installation without
  retained rootfs generations. Omit unused retention metadata instead of
  changing the adapter-state hash and incorrectly entering Docker-storage
  repair admission. Exact repair and checkpoint validation remain unchanged.

- Preserve activated jail storage through campaign growth, final publication,
  recovery and subsequent upgrade intake. Seal effective application bytes before
  publication, persist immutable effective generations before atomic acceptance,
  and reject conflicting storage edits or missing accepted jail-state authority.
  Verify both local and NFS-backed mounts without copying protected data.
  Align the shared deployment design with campaign v8 and receipt v6.

- Reconstruct the admitted physical rootFS allocation when resuming an upgrade
  that replaces a retained inactive slot. Validate the reconstructed transition
  against its frozen receipt instead of switching back to the retained PVC.

- Keep deploy progress visible after Terraform apply or a no-change plan while
  resolving application manifests and compatibility, refreshing Flux inputs,
  connecting to targets, acquiring authority and admitting application inputs.
  Use timed terminal spinners and plain stderr outcomes; failures and Ctrl+C
  clear the active display without reporting success.

- Admit the immutable rendered generation before restoring an interrupted deploy's
  execution cache, preventing hydrated or maintenance values from causing repeated
  compatibility rerender errors. Preserve fresh admission evidence and prepare
  runtime inputs plus Terraform initialization/validation from the restored state.

- Wait for controller-created Soperator check CronJobs during initial readiness
  before restoring scheduling; report exact deferral resource/field conflicts
  and retain strict maintenance guards. Evaluate deploy job policy in the Slurm
  workload namespace rather than the Helm storage namespace.
- Drain Terraform JSON events without a remote fencing call per queued line,
  retaining periodic and final authority checks and subprocess cancellation.
  Coalesce apply status bursts, suppress elapsed-only duplicates, preserve
  resource instance keys and stop aging completed API operations. Reuse the
  initialized Terraform root for post-apply application output reads.

- Show each deploy Terraform plan's purpose and elapsed progress while inspecting
  saved plans. Reuse completed preflight inputs in the same execution and skip
  the ineffective verification plan before a simple stage has executed. Retain
  execution refresh, stage admission, fencing and independent convergence checks.
  Reuse the prepared backend and remove duplicate campaign
  Terraform initialization. Describe no-change plans without claiming an
  unobserved completed checkpoint.

- Keep required native Slurm smoke enabled in new CPU, GPU and mixed Soperator
  configurations. Set `ensure-healthy-nodes.runAfterCreation` to `true` in every
  profile and retain its enabled state for CPU workers.

- Apply bounded chart-download retries to OCI package capture during render
  manifest preparation. Retain verified bytes through capture, clean every
  attempt, recognize subprocess timeouts, and keep consumer and integrity
  failures outside transport retries.

- Accept native Helm-rendered CRD enum values containing bare `=` during ordinary
  and bundled Soperator chart inspection. Keep safe YAML parsing and immutable
  chart packages unchanged.
- Show render stages with a terminal spinner and elapsed time after pre-render
  validation, with plain-text stage outcomes on stderr in captured logs. Preserve
  inherited progress ownership, report failures and skipped provider locks, and
  clean up unfinished staging artifacts on errors or Ctrl-C during preparation.

- Keep component compatibility tables and routine assessment, transition and
  provider summaries internal across validation, deployment, upgrade and status.
  Preserve admission checks, complete stored evidence, progress, operational
  plans and actionable blocking errors; plain validation adds no report files.

- Accept omitted or empty optional Helm `kubeVersion` constraints during render,
  including metadata projected from enabled child charts. Record them as
  undeclared; malformed or incompatible declared constraints still block.

- Keep `render` independent of destroy receipts and remote lifecycle state.
  Normalize source configuration in memory and publish generated artifacts under
  the local project lock; deploy retains backend admission and remote fencing.
  Align render help and SSH-key documentation with source-preserving rendering.

- Keep saved projects and next-step guidance when advisory post-create validation
  fails, including `soperator create`; explicit validation still rejects unresolved
  sources. Bound Helm metadata, values, and chart-download retries to three
  attempts for connection resets and timeouts, and redact signed download URLs
  and raw timeout chains. Isolate and clean up each download attempt, avoid
  caching materialization failures, and omit unsupported source-check bypass
  advice from `soperator create` errors.

- Canonicalize private execution roots before Terraform saved-plan checks. Destroy
  can resume managed cleanup after cluster/SFS deletion on systems with aliased
  temporary directories, while retaining plan containment and identity safeguards.

- Show the actual cluster or filesystem ID in MK8s destroy's deletion wait
  progress. Keep provider operation IDs in the receipt for polling and recovery.

- Accept Terraform refresh observations for exact same-identity resources already
  scheduled for Soperator deletion, including ancillary refresh after SDK deletion.
  Keep retained-resource drift, changed identities and unapproved actions blocked.
  Capture destroy's Terraform output and suppress raw provider failure diagnostics
  while retaining phase progress, saved-plan checks and execution fencing.

- Rebuild unapproved current-format destroy previews with fresh inventory and confirmation.
  Remove legacy preview recognition and reject unsupported receipt schemas before
  cloud work. Preserve previews on planning failure and reject concurrent cache
  changes before replacement.

- Keep validation and quota diagnostics independent of destroy mutation authority.
  Treat an explicitly missing backend bucket as uninitialized, while refusing
  permission and transport failures. Recover pending project generations only
  inside the local write lock, and reject source changes during acquisition
  or recovery before publication. Align the remaining receipt and ownership
  documentation with SDK destroy v1, default GPU/PVC cleanup, and independent
  PVC preservation and SFS deletion flags.

- Retain destroy's terminal-interactivity check from its paused confirmation
  boundary. Resuming Rich progress no longer causes a false noninteractive
  rejection after accepting the exact phrase; first approval still requires a
  terminal and the matching inventory phrase unless explicit `--yes` is supplied.

- Refresh unapproved destroy previews from cloud inventory before confirmation;
  freeze approved cluster, node-group and SFS scope while allowing worker turnover.
  Dry-run is optional and does not grant mutation authority. Resume interrupted
  publication by restoring the approved final files even after local cache loss.

- Align installed-wheel status smoke with sanitized failure output. Verify exact
  callback reachability, nonzero exit and overall Error independently, and
  reject raw exception disclosure instead of requiring it for smoke success.

- Show MK8s destroy phase progress from startup through inventory and
  cleanup, with a terminal spinner and elapsed time or plain stderr records.
  Pause the display for inventory and confirmation, and close it on failure.

- Resolve destroy identity consistently from exact target metadata, deployment
  handoff and the selected managed Terraform resource; mismatched IDs stop before
  deletion. Cluster access and Kubernetes storage bindings are no longer required.

- Match Soperator operator and NodeConfigurator ownership against the Helm target
  namespace instead of Flux's storage namespace. Keep missing or ambiguous
  expected NodeConfigurators visible as Unknown. Label check timestamps by their
  actual meaning and omit missing timestamps.

- Align command help, flag descriptions, and example comments with current
  quota, Terraform lock, Flux target selection, Grafana export, bootstrap,
  upgrade, discovery, and Soperator recovery behavior. Add status/destroy
  examples and document conditional flags and local preview receipts.
- Keep sentence punctuation and explanatory prose outside displayed command
  examples. Check every public help surface and parse all displayed examples
  without executing product callbacks.

- Reject chart upgrade preparation if its loaded manifest no longer matches
  the publication snapshot, preserving concurrent manifest edits.
- Reject unresolved rendered NFS StorageClass and MysteryBox secret bindings
  before application effects. Check local chart resources and every declared
  secret mapping while preserving explicit NFS opt-out and target scope.
- Admit direct `flux apply` against frozen application inputs before any target
  effects, then execute captured artifacts using the inspected cluster context.
- Stage and assess generic Helm upgrades before atomic publication. Dry-run now
  performs compatibility and operator-transition checks; retries retain artifact
  and cluster identities. Preserve Soperator protected files and ordinary app
  evidence, and bind shared OCI sources to their exact chart artifact URLs.
- Check Helm Kubernetes constraints against the control plane during rolling
  upgrades, while retaining node versions in support assessment.
- Require worker and GPU readiness smoke from the desired NodeSet inventory,
  even when optional diagnostic creation flags are disabled.
- Authenticate optional pending checks against frozen CronJob, Job, Pod and
  Slurm script execution before exempting them from readiness. Preserve only
  recognized Kubernetes admission defaults; auxiliary pending work stays gated.

- Retain campaign workload authority during final maintenance restoration after
  all upgrade segments complete. Preserve callback-owned authority and recovery
  evidence across restoration events, interruptions, and terminal receipt writes.
- Keep passive worker evidence in the private lifecycle-report namespace so
  receipt writes do not invalidate configuration authority and render or recovery
  cannot drop accepted worker progress. Preserve operation and policy bindings.
- Compare passive worker resource-health and volume-mount status using Kubernetes
  map-list keys, preventing harmless entry reordering from looking like a worker
  replacement. Preserve checks for actual health, identity, readiness, and resource
  changes, and reject malformed or duplicate identities.
- Keep coordinated application prerequisite rendering in a disposable resolved
  bundle; leave generated-file publication with the receipt-owned release
  workflow so Terraform output hydration does not invalidate parent authority.
- Identify mismatched recovery controls, including target selection and job
  timeouts, instead of reporting every mismatch as a job-policy error.
- Store shared deployment plans in the excluded Terraform runtime directory so final
  Soperator reconciliation does not mistake its own Terraform plan for config
  drift. Preserve strict configuration checks and private plan-file permissions.
- Exclude Terraform reads and no-op events from completed resource-change
  counts, and count replacements consistently with planned adds and removals.

- Extend the digest-bound dashboard source adapter to the reviewed Soperator
  4.1.8 package whose malformed document separators fail Flux rendering. Keep
  all seven dashboards and their verified upstream JSON content.

- Bind coordinated-stage preflight manifests to their temporary Flux directory
  through the portable generation owner, preserving frozen content identity
  and strict target-path validation.

- Export canonical source configuration for coordinated deployment admission,
  release handoff, and stage publication. Preserve frozen component values and
  instance identity while removing derived runtime aliases; keep public source
  validation strict.
- Remove the release-child retry hint that omitted required platform selectors
  from the public coordinated upgrade command.

- Preserve the admitted release snapshot when a `latest` selection is handed
  to an exact-release upgrade stage. Verify its original digest and resolved
  version without rewriting selector provenance.

- Accept official release root Markdown aliases as inert, Git-verified text
  during source acquisition and recovery. Keep filesystem links, runtime
  aliases, hard links, submodules, and unsafe archive paths rejected.

- Compare restored Slurm partition settings and Kubernetes storage quantities
  semantically during final deployment verification. Recognize API-added
  workload defaults and omitted empty values while retaining strict checks for
  owned settings, list membership, removed fields, and resource identities.

- Verify current Soperator passive scheduler, mounted policy, and prolog/epilog
  restoration at install and upgrade completion, including enabled fallback and
  resumed handoffs. Freeze rendered chart defaults and recognize indexed native
  hooks without rerunning previously accepted diagnostics.

- Retain sealed install-repair receipts in portable deployment recovery. Accept
  only the final SIGTERM/SIGKILL accounting signal for an already recorded
  timeout, preserving exact job identity and requiring fresh native acceptance.
- Preserve each worker's private Docker image cache when compiling persistent
  jail mounts; reject conflicting mount identities. Add sealed first-install
  recovery for the exact shared Docker metadata defect, preserving terminal
  native outcomes, storage, and maintenance before fresh acceptance.
- Resolve live Soperator status from exact remote deployment identity during
  interrupted installation, without requiring a final local handoff or reading
  Terraform state; reject conflicting identities and report unpublished identity.
- Report unavailable Slurm workers before native acceptance submission and
  during result polling; preserve existing diagnostic jobs, health drains and
  scheduling isolation for recovery instead of waiting for a generic timeout.
- Show release-stage progress during Soperator maintenance, acceptance, and
  scheduling restoration, including interrupted deployment recovery.
- Bind passive diagnostic probes to the selected Slurm worker when invoked
  through `kubectl exec`, where Slurm hook environment variables may be absent;
  retain conflicting-identity and pod/container replacement checks.

- Enforce exact Helm chart versions in both catalog validation and rendering;
  reject ranges, wildcards, incomplete versions, and `latest` before writing a
  deployment bundle.
- Preserve the bound infrastructure identity when resuming an interrupted
  Soperator install after a fresh no-op Terraform plan; retain complete
  immutable-input checks against the existing scheduling journal.
- Order managed cert-manager readiness before Security Profiles Operator in
  staged Soperator installation and upgrade, including downstream consumers.
- Wire the Soperator application runtime ownership guard into the CLI so
  deployment and upgrade can continue from infrastructure to application setup.
- Preserve saved zero-surge rollout settings through shared Soperator upgrade
  admission without passing the resolved zero as a safe-surge-only CLI option.
- Bound generated Soperator filesystem mount tags to the virtio-fs device's
  36-byte UTF-8 limit, which is stricter than Compute API admission, and
  reject overlong explicit tags in the wizard and configuration validation.
  Count stored whitespace in authored configuration; preserve wizard trimming
  before validating the saved value.
- Allow fresh Soperator deployment and preview through the public loader without
  requiring an ordinary-application baseline from an earlier installation.
- Include recreated Terraform dependencies in coordinated stage admission and bind
  recovery to original and replacement resource identities.
- Publish desired values during onboarded Soperator campaigns; verify exact
  maintenance-phase settings and recheck restored settings before completion.
- Deploy every selected application target after Soperator scheduling restoration,
  retain incomplete progress on failure, and preserve unselected acceptance evidence.
- Apply and verify ordinary sibling bundles, handle statusless OCI HelmRepository
  objects, and resolve Terraform-derived application values during no-op retries.
  Reject unsupported whole-resource removals before deployment begins.

- Recheck the shared deployment fence after planning and apply preflight, before
  Terraform starts, and during streamed apply. Preserve that fence for ordinary
  targets as well as Soperator targets.
- Align Soperator error guidance and customer CI documentation with the
  create/configuration and common validate/render/deploy workflow.

### Changed

- Record completed Soperator 4.1.5/Kubernetes 1.35 installation and 4.1.8/1.36
  upgrade through supported resumes, with independently verified checks,
  scheduling, node groups, and preserved storage. Retain earlier failed trials,
  operational recovery, and documented vendor compatibility limits separately.

- Document a proposed shared `compatibility-matrix.yaml` with exact version
  sets, distribution-scoped evidence, intermediate upgrade checks, and frozen
  recovery. Include the MK8s/Soperator implementation plan and current YAML
  ownership inventory; distinguish chart application metadata from controller
  image tags and verified binary versions. Runtime integration is not
  implemented yet.

- Split Soperator configuration authoring into `soperator create --release latest|X.Y.Z`; remove `soperator install`.
- Route complete rendered Soperator deployments through `deploy`, with optional preview,
  private local generations, automatic matching recovery, and coordinated topology/platform stages.
- Use the common validate/render/validate-generated/deploy pipeline in generated CI workflows,
  including config changes and non-cancelling deployment concurrency.

- Scope generated Soperator node service-account names to the configured MK8s
  cluster and node group, deferring allocation while the wizard has no cluster
  name. Preserve saved account mappings when surviving worker groups are rebuilt
  for scaling changes; do not adopt colliding accounts or rewrite frozen plans.
- Require missing root SSH key selection before optional Soperator customization,
  so accepting default `n` completes SSH setup and passes the existing save guard.
  Preserve explicit key lists and cancellation behavior, and avoid a duplicate
  key prompt when detailed customization follows selection.
- Default upstream Soperator configuration to `n` during installation, so Enter
  retains the previewed settings and frozen release while `y` opens the fields.
- Fix the Soperator wizard grouping test to request backtracking from the current
  required-integration customization prompt and verify that the step is revisited.
- Default required Soperator integration customization to `n`, keeping GPU and
  Network Operators enabled with their previewed settings unless explicitly
  customized. Compile completed fresh-install values before project publication.
- Give the adapter sole ownership of generated jail and controller-spool volume
  sources. Normalize install, adoption, and slot-switch storage intent without
  generating conflicting aliases; remove the obsolete upgrade alias-stripping
  handoff while preserving strict custom-source conflict checks.

- Ask Create new or Use existing before configuring each Soperator accounting,
  controller-spool, and jail filesystem. Require project-list selection for
  reuse, skip irrelevant properties and lookups, preserve visible backtracking,
  and summarize creation or reuse before leaving SFS configuration. Incomplete
  fresh-install wizards now stop before config publication or planning even when
  defaults satisfy required fields.
- Reject unfinished root-key selection before saving or planning a fresh
  Soperator installation, and reject check mount-path aliases that could hide
  retained home directories.
- Select root login keys during fresh Soperator installation, defaulting to the
  preferred local public key while preserving explicit lists, including empty
  lists. Persist the selection for render, resume, and upgrades; remove implicit
  inheritance from MK8s node keys.
- Retain `/opt/soperator-home` alongside the other mandatory jail directories in
  fresh managed installations. Bind bootstrap, ActiveChecks, and auxiliary jobs
  to the same retained storage, reject conflicting consumer mounts and incomplete
  saved layouts, and keep upstream named-user home locations.

- Match ExternalSecret readiness by its exact Kubernetes API group and verify
  removed install options in both colored and plain CLI output.
- Reject FIFO/device inputs for Soperator values and SSSD runtime files before
  reading, and reject malformed feature enablement or disabled required checks
  during input validation.
- Add fresh Soperator install `--values-file`, wizard prefill and saved explicit
  value provenance, preserving false, default-equal and list choices through
  normalization and upgrades while retaining generic catalog separation.
- Complete conditional backup destination, schedule, retention and Secret-reference
  inputs; route them through upstream `backup.config.values` and validate frozen
  child chart renders before installation.
- Complete shared SSSD Secret/optional CA references and runtime file delivery.
  Resolve runtime namespaces from rendered consumers, require explicit target
  context and lifecycle authority for each create, reuse complete objects and
  reject incomplete objects without overwriting credentials. Runtime paths and
  contents remain outside saved configuration and receipts.

- Separate Soperator maintenance, fresh diagnostic acceptance and final user
  admission. Preserve all existing job-policy/TUI choices, pause reviewed passive
  diagnostics only after isolation, and keep unsupported passive behavior at its
  verified desired policy with a warning. Required bootstrap and operational
  hooks remain active.
- Keep ordinary scheduling closed while native acceptance and recurring schedule
  restoration complete. Journal partition/hold ownership and reservation release
  separately, preserve original closed partitions and ACLs, and resume interrupted
  handoff without releasing unrelated holds or recreating maintenance.
- Bind passive acceptance to fresh native logs, worker identity and the desired
  rendered configuration, with bounded transport and incremental private worker
  receipts. New checks receipts require the matching operation executable.
- Require positive native GPU-health, boot-disk and memory measurements for
  applicable passive acceptance. Freeze GPU-busy and optional NVMe proof as
  supporting-only, report their limitations, and prevent native completion or
  skipped discovery from becoming measured PASS evidence. Bound and recheck
  child-log reads. Align wizard and upgrade-plan guidance with phase-based
  diagnostic suppression and the selected job policy.
- Respect the desired Slurm health-check node-state selector during passive
  acceptance, recording inapplicable periodic runs without inventing PASS or
  skipping native job-hook evidence. Preserve partial baseline progress across
  busy workers and resume without requiring a cluster-wide quiet sweep.

- Keep parent-owned graph-validation selection in the campaign module, preserving
  its ownership checks while satisfying the CLI architecture gate.

- Update lifecycle validation documentation with the completed managed install
  recovery and full-stack upgrade, retaining the clean-install and scale limits.

- Bind final upgrade check-policy readiness and catch-up recovery to durable
  campaign main-workload authority, fixing a wait that could time out despite
  the exact Slurm HelmRelease already being Ready.
- Resume interrupted final check-policy stages before full-graph readiness,
  prove source adoption after target writers resume, retain campaign authority
  evidence, and keep fresh diagnostics after
  runtime validation. Accepted restoration resumes reuse verified acceptance
  without recreating maintenance reservations or diagnostic jobs.

- Verify effective diagnostic deferral before install infrastructure restoration
  and between upgrade segments, including CronJobs and in-flight Kubernetes and
  Slurm work. Preserve verified bootstrap and owned acceptance recovery; require
  exact cron expressions and time zones when restoring desired schedules.
- Retain Nebius provider node-group IDs in GPU inventory and use those exact IDs
  for campaign validation, independently of shared logical Kubernetes labels.
- Fetch cold OCI chart caches by the frozen manifest digest and verify both the
  manifest and package digests, so moved version tags cannot replace approved charts.

- Keep complete Soperator graph validation in the parent upgrade after frozen
  source refresh. Managed node-template children retain node, GPU and other
  deployment checks without deadlocking on suspended HelmChart artifacts lost
  during system-node replacement. Standalone required checks remain mandatory.

- Preserve Terraform caches and local state across renders and exclude them from
  generated-configuration authority. Upgrade validation can initialize Terraform
  without invalidating its applied generation; configuration and the provider
  dependency lock remain fingerprinted. Interrupted commits still require exact
  transaction recovery, while applied stages require their recorded postimage
  through the same check at campaign admission and per-stage recovery.

- Resolve protected local SFS through retained PV/PVC bindings and exact Nebius
  node-group attachments for both managed and onboarded upgrades. Declaring SFS
  in configuration no longer incorrectly requires CSI volume handles. Match
  configured mount tags exactly instead of assuming they equal protected role
  names; reject duplicate or missing tag assignments.

- Retain approved Soperator release snapshots by digest and pass the campaign's
  frozen identity to checks and release execution. Resume no longer re-resolves
  chart tags after discovery cache expiry; missing or altered content fails
  before maintenance without changing the approval receipt.

- Preserve native check lifecycle and full-stack source, target and catch-up receipts as lifecycle
  evidence during rendering. Their creation or progress cannot change the
  admitted generated-configuration fingerprint or block maintenance entry or
  final release readiness.

- Initialize upgrade checks after creating the durable campaign receipt and
  bind source-check compilation to the rendered Slurm cluster name. Preserve
  the original initialization failure in resumable supervisor reporting.

- Resolve a managed Soperator upgrade's immutable cluster ID from its exact
  generated Terraform output before preparing renewable Kubernetes credentials.
  Read the existing backend without initialization; external targets retain
  their registered identity and never read Terraform state.

- Capture Slurm reservation identity with standard UTC timestamps and complete
  canonical fields. Reject incomplete check-reservation times before accepting
  ownership; preserve exact preimages for upgrade recovery.

- Validate Soperator deployments through the authoritative upstream release graph.
  Replace legacy jail-object smoke lookups with the same exact storage, workload
  and required-check readiness used by reconciliation; retain canonical reports
  and show pending readiness progress without validation-side recovery mutations.

- Release the verified diagnostic reservation before restoring recurring upstream
  checks during install and upgrade. Close temporary diagnostic authorization
  first, retain the independent admission barrier and existing customer job
  holds until the final policy handoff, and journal reservation release
  intent/completion for interruption recovery. Recover proven native catch-up
  submission failures through the staged workflow and fresh checks, preserving
  failed evidence. Recognize a retained Flux retry only after the exact admitted
  checks writer has acknowledged suspension and completed native rollback, with
  independent source artifact verification. Report policy restoration stages
  during resume.

- Keep terminal native scheduled-check history from blocking Soperator service
  readiness after successful recovery. Validate the complete Kubernetes owner
  chain and matching Helm-owned ActiveCheck; retain required check-result gates
  and strict readiness for service Pods and unproven Job ownership.
  Bind the auxiliary extensive-check workload to its cluster's existing Slurm
  ConfigMap and munge Secret in staged and steady rendering. Recover only a
  proven never-started stale auxiliary Job through a journaled, UID-bound native
  deadline after writer suspension; preserve accepted checks and frozen inputs.

- Require actual native health-checker PASS reports and validated NCCL results
  before accepting Slurm diagnostics, including output revalidation before maintenance release. After release, verify
  the bound diagnostic verdict/digest plus live Job and accounting evidence so
  upstream local-log retention cannot invalidate a completed acceptance.
  Accept the native CUDA sample report with null subchecks only when all four
  exact sample commands succeeded and the overall report is PASS.
  Correct the managed eight-H200, 128-vCPU worker topology without converting
  its Kubernetes CPU-time quota into a physical CPU mask. Preserve the quota,
  retain native affinity failures and replay fresh acceptance through sealed
  values repairs. Verify reservation resource totals during physical topology
  repair; removing a generated CPU mask preserves the complete reservation.

- Bind GPU workers to the upstream Docker Supervisor configuration and daemon
  settings, with a pod-local disposable image cache under the existing storage
  allowance. Recover an initial native Docker NCCL missing-socket failure through
  an exact sealed values repair, retaining failed jobs and the original
  reservation and requiring fresh upstream checks.
  Recover the exact drains left by those failed checks after proving native
  Docker readiness. Quiesce only an attributable queued bootstrap probe and
  resume its native Job after drain restoration and check authorization;
  preserve failed history and reject unrelated drains or workloads.

- Declare an Enroot-specific AppArmor user-namespace profile through the
  upstream Security Profiles Operator. Wait for installation on all ready
  nodes before opening the Slurm workload, preserving the host-wide
  restriction. Recover a proven initial Enroot namespace denial through a
  sealed additive adapter repair and fresh acceptance under the original
  reservation, retaining the failed job and its accounting.

- Correct the GPU wizard `ephemeralStorage` default from 10Gi to 55Gi, matching the
  pinned upstream GPU examples. Fill missing GPU allowances while preserving explicit settings.
  Recover an initial native image import interrupted by a proven worker
  storage eviction through an exact sealed values repair. Retain its failed
  accounting and require fresh checks under the original reservation. Bind
  durable kubelet termination and eviction cursors to the node boot identity
  and the native passive checker's over-limit storage report instead of relying
  on short-lived Kubernetes Events.

- Pin native acceptance jobs with supported Slurm allocation directives and
  bind their projected script contents to executable identity. Preserve the
  upstream diagnostic body and entrypoint. Retain exact terminal submissions
  made with unsupported worker-selection variables, then require fresh jobs
  with independently verified worker and GPU coverage on resume.

- Materialize upstream runtime script mounts and the node-local job metrics
  directory for both installed and registered NodeSets. Verify the normalized
  Pod mount paths emitted by the upstream renderer and the target-file permissions
  of projected ConfigMap links. Recover an exact initial
  acceptance blocked by missing mounts through a sealed values repair, native
  probe quiescence and verified restoration of only its attributable prolog
  drains. Preserve failed evidence and require fresh checks after replay.

- Match the upstream `<cluster>-munge` Secret in native check verification while
  retaining the distinct `munge-key` volume name, exact key paths and permissions.

- Include check reservation binding and initial partition restoration in both
  normal execution and interrupted recovery, and verify both before accepting
  the combined restoration step. Repair the exact missing-GPU install frontier
  with a sealed successor that retains the original reservation and failed
  history, changes only absent `Gres` counts, and replays from declarative apply.
  Authenticate both full-values policy identities through that sealed delta
  before carrying reservation ownership into the successor.

- Validate unlimited check reservations using Slurm's native 365-day duration
  output, preserving active-state, resource coverage, authorization and
  fingerprint checks.

- Bind the upstream jail-log collector to the approved system placement and
  active jail storage, including slot switches. Preserve upstream configuration
  and readiness, and support an exact pre-acceptance install repair with native
  HelmChart identity and uninstall verification.
  Recover a healthy corrected collector stalled without a rollback target using
  one source-bound native retry per configuration; preserve waits and stop on
  another failure.

- Bind native bootstrap check login commands to the declared cluster Service
  using exact verified upstream script bytes. Reject arbitrary command
  changes, and admit interrupted installs through a sealed two-file repair
  that preserves source, storage and earlier repair evidence.
  Recognize the recorded failed-apply checkpoint as well as an interrupted
  apply, retaining exact pending-intent and pre-acceptance checks.

- Keep the orchestration-only Soperator umbrella from waiting on suspended
  child releases during install and upgrade; retain child waits, hooks and all
  staged acceptance gates. Resolve checks handoff through the same rendered
  umbrella identity and require acknowledged source-writer suspension.

- Populate missing Slurm GPU counts even when worker CPU topology already fits.
  Reject changes to existing IAM permit settings during failed-install recovery.

- Require the upstream Slurm REST service for configuration reconciliation,
  including its existing JWT startup gate and OpenMetrics handling. Repair an
  omitted dependency during interrupted install through a sealed successor
  that preserves all worker, storage, source and check inputs.

- Recognize completed uninstall remediation as quiescent for the exact admitted
  checks-install repair, even when Flux retains a queued retry condition.

- Bind native Soperator checks and the reservation CronJob to the active jail
  PVC; suspend and verify restoration of the auxiliary schedule across install
  and upgrade. Resume interrupted first installs through a sealed render repair
  and exact old wait-hook deadline, preserving source, storage and failed history.

- Preserve the frozen absent source on install resume after the target main
  release appears, instead of inferring a no-op from live discovery.

- Apply temporary Soperator check policy to the umbrella HelmRelease inline
  values that Helm consumes, as well as the matching values record. Reject
  missing, ambiguous, or drifted umbrella values before mutation.

- Extend the upstream dashboard source adapter to the pinned Soperator 4.1.5
  chart. Interrupted installs can resume its exact pre-main separator failure
  with cluster-bound repair evidence, preserved compiled values and failed
  history, and a fenced successor operation.

- Use the supported project `editor` role for upstream observability ingestion
  instead of assigning permission names as IAM roles. Failed install replanning
  refreshes only Terraform root wiring with frozen-input checks and may replace
  only uncreated grants for retained groups; existing resources remain protected.
  Recovery compares desired values using provider schemas so normal status
  changes after node creation do not prevent resuming a partial apply.

- Keep the Soperator strategy guard inside its release branch so ordinary Flux
  app deployment remains independent of Soperator lifecycle policy.

- Require canonical project admin permissions for Terraform-owned IAM and MK8s
  service-account attachment. Cached commands verify permissions read-only;
  explicit targeted `auth` reconciles managed project roles without rotating a
  healthy key or granting tenant access.
- Recover failed Soperator infrastructure applies through `install --resume
  --dry-run --replan`. Preserve failed evidence, infrastructure and shared-group addresses,
  known identities, and frozen inputs; reject destructive changes and require
  a new approval fingerprint before resuming.

- Include exact renderer-owned Soperator observability IAM instances in the
  install plan ownership check. Reject unrelated accounts and resource names,
  destructive actions, and fresh IAM-only plans without infrastructure changes.

- Repair the MK8s service-account output used by Soperator observability IAM:
  account keys remain known in the first Terraform plan while IDs resolve at
  apply time. No targeted bootstrap apply is needed.

- Normalize Object Storage lease metadata header casing so Soperator install
  retains valid ownership on Nebius responses. Reject ambiguous duplicate keys
  and keep exact ETag, owner, expiry, and cluster-binding checks. Suppress the
  generic deploy hint during install's internal render step.

- Fix fresh Soperator rendering before Terraform state exists and resolve the
  managed cluster output without duplicating its target prefix. Restore the
  required fabric prompt when regional capacity advice is missing, and report
  absent regional coverage as unknown instead of confirmed zero capacity.

- Keep upstream Soperator checks enabled while deferring scheduling-dependent
  diagnostics during install and the full upgrade campaign. Run fresh native
  acceptance on every required worker under retained maintenance, verify all
  required GPU allocations and executable/script identity, and restore schedules
  and authorization before customer handoff. Freeze policy changes in upgrade
  plans, including equal-release campaigns, reject unsupported execution knobs,
  and preserve exact submission/restoration evidence across interruption.

- Align the install wizard with upstream Soperator ownership: show infrastructure,
  dedicated upstream Soperator settings, and required platform/integration
  components. Remove optional-app questions, generic observability prompts, and
  `soperator install --app`; optional apps use `component add` afterward. Preserve
  placement and storage mappings, with separate autoscaling and ephemeral-worker
  choices. Require explicit
  collector selection on each Soperator target, keep Grafana independent, retain
  protected ownership during ordinary dependency filtering, and report ordinary
  telemetry signals from effective collector configuration. Saved install/resume
  state and existing upstream observability remain unchanged.

- Fix ordinary app reapplication when namespaces already exist: remove their
  matching Kustomization references from the private apply snapshot while
  preserving the saved generated bundle and live namespaces.
- Use public Nebius Grafana by default for Soperator installs. Local
  Grafana plus Gateway and the additional app telemetry agent are independent
  optional apps selected afterward through `component add`.
  Ordinary catalog apps use the existing component, render, deploy, Flux apply,
  and Helm upgrade commands on Soperator MK8s targets. Publish and apply only
  ordinary app resources, preserve protected infrastructure and the upstream
  graph, verify target identity and resource ownership, and fence concurrent
  lifecycle operations. Preserve ordinary app generations through upgrade and
  recovery; omission does not uninstall live releases. Render the upstream
  logging endpoint for the selected region and retain cxcli Grafana dashboards.

- Refactor `soperator install` to use the shared typed project-creation workflow
  directly, fixing the `Invalid --app-version value '4'` failure. Select MK8s,
  SFS, and Soperator automatically while preserving their full field wizards
  and the one frozen upstream release. Apply the worker profile before
  materializing topology, reconcile GPU helpers after field changes, and keep
  cert-manager exclusively upstream-owned. Report release, authentication,
  service-account, provider, render, and plan progress using the same terminal
  presentation as upgrade, with stable stderr records when redirected. Keep
  cached lookups quiet and report failed validation or provider requests as
  failures even when the wizard handles their errors.
- Read supported Nebius subnet status pools without SDK deprecation tracebacks,
  preserving explicit versus inherited subnet ownership. Reuse complete project
  platform inventories for preset metadata, and propagate CPU/GPU selections
  to managed node groups before refreshing disk recommendations. This avoids
  repeated failed requests for unavailable prior defaults without replacing
  explicit selections or caching failed inventory reads.
- Align workflow regression tests with the existing pinned setup-uv v10.0.1
  release and download-artifact v8 action.
- Remove the downstream Soperator Helm chart family, its verifier workflow, and
  its publication entry. Soperator is no longer declared in the generic source
  or CLI-settings catalogs; the six lifecycle commands now combine a dedicated
  packaged install-policy contract with exact official `nebius/soperator` GitHub
  and OCI release authority. Remove the three downstream-only QoS/preemption
  profiles and reject `qosConfiguration` and `schedulingConfig` without a
  compatibility path. Release and wheel verification also reject any
  reintroduced Soperator entry in either bundled generic catalog, and
  credential-bearing GitHub API requests reject redirects outside the official
  API authority before following them.
- Replace the node-shaped `soperator discover` output with one bounded customer
  summary and one complete support-safe schema-v2 JSON inventory. Screen output
  and `report.md` now show fixed component rows plus one node-group row with
  Ready/Actual/Target counts, while `report.json` retains every in-scope node,
  provider group, Soperator/GPU component, storage/topology/health record, and
  explicit collection-lane outcome. Exact ID-first group correlation,
  deterministic mixed-value summaries, dedicated public allowlists, and
  serialized Markdown-first/JSON-last pair publication prevent guessed
  attribution, data leakage, unbounded terminal output, and mixed concurrent
  report pairs.

- Normalize cross-version Slurm `AllocNodes=ALL` state during authenticated
  partition restoration so upgraded controllers continue accepting jobs from
  login nodes even when the visible partition record is unchanged.
- Normalize output-only unlimited partition-memory sentinels to Slurm's
  explicit numeric-zero representation so Slurm 25.11 does not treat them as
  unsatisfiable per-CPU requests or inherit a finite cluster-wide default.

- Make Soperator upgrade final validation compatible with fully allocated GPU
  workers and the canonical Soperator report contract. A saturated target now
  proves `nvidia-smi` and CUDA Driver API initialization inside the exact Ready
  Slurm worker pod, while canonical smoke reports are archived under a unique
  attempt identity before campaign evidence is accepted, including a fresh
  failed report written before its validator raises.
- Isolate each Soperator release reconciliation attempt from its caller-owned
  source payload. Full-stack supervisor retries now recompute admission from
  the same frozen input instead of inheriting rootfs-adoption and chart-version
  mutations from a prior attempt. Recovery also preserves the sealed legacy
  rollback PVC authority after the active rootfs has switched to a slot, so an
  exact checkpoint replay no longer falsely pauses on config-generation drift.
- Make uv the single dependency and environment authority for repository
  development and the cxcli CI/release workflows. `make env` now rejects stale
  lock state and unsafe, whitespace-containing, or unrecognized VENV paths
  before uv runs, serializes exact locked synchronization, disables automatic
  Python downloads, and runs every Python-backed target through locked uv
  execution. Wheel builds constrain isolated PEP 517 dependencies with hashes
  exported from the same lock and install the one exact artifact into a
  temporary uv environment for CLI and dependency verification; the former
  pip/stamp/checker path and public `dev` extra are retired.
- Materialize one canonical active/passive Jail rootfs source before Soperator
  release execution. An omitted `adoption.activeSource` on an already
  `activePassive` installation now remains slot-backed, so a populated inactive
  slot uses the exact write-ahead recycle journal instead of being
  misclassified as a fresh legacy-adoption PVC and retried indefinitely.
- Give `soperator upgrade` one terminal-owned progress surface across release,
  provider, compatibility, writer-authority, Slurm-maintenance, Flux, and
  readiness phases. The frozen Kubernetes plan now spells out adjacent hops
  from the observed source minor, and provider rows are labeled as target
  compatibility instead of looking like upgrade transitions. After the plan,
  execution visibly reports cluster-Lease acquisition, bounded expired-writer
  quiescence, partition/job maintenance, reservation setup, and barrier
  convergence. A spinner is replaced
  in the same status column by a green check on success, while non-TTY progress
  is stable and stderr-only with capped, deduplicated `INFO` milestones. Flux
  and campaign completion rows are committed to terminal scrollback with their
  elapsed time while the live surface retains only the active phase; supervisor
  retry waits remain active spinner rows with elapsed time instead of becoming
  static notices, and the direct-upstream release plan is printed only once per
  invocation. Flux
  maintenance progress pauses while a nested Slurm table, dashboard, or prompt
  owns the terminal, then resumes without overlapping live renderers. Flux
  apply, rollout, and migration subprocess
  chatter is captured and summarized by disposition, controller count, and
  resource kind; bounded sanitized failures retain authoritative command
  status. Campaign compatibility output now uses one row per node group with
  the complete per-hop OS/driver path and ready/target count instead of scaling
  terminal output with nodes or duplicate hop rows.
- Regenerate Soperator upgrade kubeconfig handoffs from the immutable cluster
  identity with the current Python module and renewable exec authentication
  instead of copying a context that may contain a stale checkout-local launcher.
  If an external edit removes the running cxcli environment anyway, the forward
  supervisor now exits with a resumable local-runtime error rather than retrying
  Kubernetes authentication forever; the same approved command reloads the
  existing campaign receipt and continues from its earliest unproved boundary.
- Remove the redundant `soperator upgrade --allow-provider-api-upgrade` and
  `--zero-size-gpu-validation` options. The approved campaign now derives its
  single Terraform or provider-API backend from proven target ownership, while
  retaining the internal authority and zero-policy fields in v3 receipts for
  exact recovery. Fresh campaigns update zero-capacity groups and verify two
  stable desired-template observations automatically; readiness follows live
  capacity changes, desired-positive GPU groups still require scoped CUDA
  validation, and GPU operator readiness remains required. Provider `auto`
  selection now resolves the latest OS and then the latest compatible Nebius
  drivers preset independently per node group and Kubernetes hop, prints every
  frozen API tuple, and safety-pauses if that tuple disappears immediately
  before its individual group mutation. Global GPU selectors ignore driverless
  groups, per-group driverless overrides and ambiguous aliases fail, and
  provider inventory ordering is canonical. Final readiness now refreshes and
  proves the Flux graph before runtime checks, requires one non-skipped scoped
  CUDA report per active GPU group, then re-proves the graph and unchanged all-
  group capacity/resource identity. Attempt-unique validation reports are bound
  by SHA-256. Completed campaign evidence remains visible in status, while
  failed final-only reproofs retain last-known-good evidence and surface their
  own recovery lifecycle.
- Make `soperator upgrade` show its fixed `pause-all-active` Partition Policy
  before a structured Job Policy selector and use guarded
  `requeue-hold-all` as the universal omitted default. Requeue and hold only
  identity-stable eligible batch jobs, wait for completing or unproven jobs,
  and reserve per-job human decisions for explicit `interactive` mode. Close
  the scheduling barrier after reservation creation, journal exact hold intent,
  applied state, and tombstones, then release exact operation holds while
  partitions remain paused, remove the operation reservation, and restore
  partitions last. Re-prove the operation lease immediately before every
  individual partition mutation so authority loss cannot spill into later
  partitions. Official chart pulls now retry three isolated times only for
  transient transport failures; authentication, certificate, not-found,
  digest, and identity failures remain immediate, and exhausted public errors
  omit raw transport cause chains.
- Identify every cxcli-owned Nebius SDK client with the versioned
  `nebius-cxcli/<runtime-version>` user-agent prefix, removing the SDK's
  repeated future-mandatory constructor warnings.
- Restore the Nebius SDK's native event-loop and shutdown ownership instead of
  lending it an application-owned loop. This removes the competing task drain,
  background reaper, and recovered-error log filter that could cancel SDK 0.6.4
  runtime finalization and emit repeated `CancelledError` tracebacks during
  read-only Soperator discovery. Native shutdown failures remain visible, and
  synchronous SDK helpers retain the SDK's fail-fast async-context contract.
  The project now requires SDK 0.6.4 or newer within the supported 0.x series.
- Repurpose `soperator discover` as a config-independent, information-only
  pre-onboarding command with parser-required tenant/project/cluster identity,
  optional verified region/context/access, and atomic JSON/Markdown reports.
  Align the five-command help order, paired interactive flags, existing-config
  onboard validation, region semantics, documentation, tests, installed-wheel
  smoke, and the v4 CLI contract with explicit option order and conditional
  requirements. Keep onboarding discovery evidence internal and independently
  collected; public driver presets, images, chart metadata, and labels are not
  reported as runtime GPU-driver or CUDA proof. Public discovery now converts
  ambiguous lifecycle identity, failed storage inventory, and unavailable
  Slurm health probes into a printed and written partial report while onboarding
  remains fail-closed. The exact saved Markdown projection is rendered on
  screen for complete and incomplete outcomes; terminal, bidi/format, line
  separator, and Markdown control characters from live-derived values are
  visibly escaped before persistence or display. Shared Markdown contains only
  the relative artifact path rather than the operator's absolute local path. Deployments-root
  onboarding also keeps an omitted region live-derived across interactive use
  and failed scaffold retries through an owner-only, config-hash-bound bootstrap
  marker instead of treating the temporary default as operator intent. The
  marker and initial config are published under the config lock with
  create-only semantics, then re-proved after discovery and under that lock;
  established configured and explicit region assertions must agree
  independently. Public discovery uses the schema-v2 bounded-summary and
  complete-inventory contract described above.
- Retire the ownerless downstream `soperator-upstream-verifier` workflow with
  its local chart, lock, and synchronization inputs. The surviving cxcli CI and
  release workflows now share the canonical quality and exact-wheel contract,
  explicitly provision Helm, and keep official upstream Soperator artifacts as
  the single supported delivery path.
- Make full-stack completion prove the retained Flux release graph, not only
  healthy live workloads. Completed/no-op declarative-release replay and final
  readiness now refresh every frozen digest-bound source before their graph
  waits, so controller restarts cannot leave stale-positive artifact status;
  final readiness resolves the selected target's Flux directory, re-freezes
  HelmChart artifacts, and waits for all graph members to acknowledge their
  current generation as Ready. An exact replay of a
  completed campaign reruns only this final postcondition without reopening
  maintenance or repeating provider mutations.
- Recognize the provider-native `nebius.com/node-group-id` label when final GPU
  inventory validates an onboarded cluster, while keeping the rendered
  `nebius.com/node-group` name authoritative when both labels exist.
- Preserve generated runtime inventory, deploy-smoke, acceptance, benchmark,
  GPU-stack, and CUDA-visibility reports as lifecycle evidence outside the render-owned
  project snapshot so their creation cannot safety-pause upgrade recovery.
- Align final Soperator smoke with the official 4.1.7 graph: validate the ready
  `sconfigcontroller` deployment and the adapter-owned read-only GPU driver-root
  mount, leaving CUDA/NVML library and device proof to the runtime Jail check.
- Fix interrupted protected Soperator recovery after the target release has
  adopted its prepared jail-rootfs slot. Pre-apply recovery still requires the
  passive PVC to be empty and unconsumed, while post-apply recovery verifies
  the sealed materialization receipt and exact completed Jobs instead of
  incorrectly applying the pre-write consumer gate to the now-active PVC.
- Align the Soperator README and design contract with the current code: use
  registration v3 and destroy receipt v1, keep hardware replacement exclusively
  under `migrate node-group`, document disabled and partial lifecycle-marker
  guards, state the real Helm/kubectl requirements, replace the drifting manual
  test list with the complete Soperator lane, and distinguish current onboarded
  live evidence from the pending cxcli-managed/Terraform live trial. Refresh
  both Soperator SVGs to show the full-stack parent campaign, exclusive backend
  authority, content-free target-wins rootfs transition, current protected
  storage variants, and the separate non-gating observability status action.
- Make `soperator upgrade --to-release latest|X.Y.Z` a durable full-stack
  campaign. Its wizard dynamically queries Nebius for the highest reachable
  Kubernetes endpoint, freezes sequential minor hops and per-group OS/GPU
  driver compatibility, keeps whole-campaign Slurm maintenance, upgrades the
  official release plus MK8s and Jail CUDA, and requires final provider and
  generated workload readiness. Maintenance entry is event-journaled before
  each Slurm mutation, and one-minor-lagging node groups are caught up before
  later control-plane hops. Managed targets keep Terraform authoritative;
  onboarded targets require explicit provider-API upgrade authority. Freeze the
  exact ownership/backend digest with no fallback on recovery, supervise the
  whole parent campaign instead of nesting child release retry ownership,
  journal complete config-plus-generated generations, and project the parent
  campaign through `soperator status`. Reservation recovery now recreates a
  missing reservation only from durable operation intent and rejects a live
  same-name reservation that the campaign did not record. Recovery also binds
  the initial render-owned project snapshot and exact provider node-group ID
  set, control-plane readiness requires stable actual-status convergence, and
  zero-sized groups retain stable desired-template verification even when live
  GPU/CUDA workload proof is explicitly waived. Full-stack planning now also
  accepts provider-default node groups whose empty desired Kubernetes version
  inherits the control plane by normalizing the provider-reported actual node
  version at the SDK boundary. Forward the parent campaign's proven Lease
  fencing authority into the child release reconciler instead of discarding
  the returned fencing token during campaign-state validation, reuse the
  frozen resolved max-surge count during recovery, and translate its non-safe
  zero to the command-neutral Terraform child's no-override representation
  instead of revalidating it as a fresh CLI override. Apply the same
  inherited-version normalization and resolved zero-surge contract inside
  provider node-group updates so a resumed control-plane-first hop can advance
  its node groups, and omit an empty GPU settings message for driverless groups
  so the provider does not reject the request as an incomplete driver preset.
  Persist protected controller-spool migration checkpoints in the parent
  campaign receipt and forward them to the release child without creating a
  second Slurm maintenance owner, and address the legacy Kruise
  AdvancedStatefulSet through its canonical plural API resource. After the
  target SlurmCluster opens, re-prove its shared spool projection, remove a
  claim template reintroduced by the restored old controller, and require the
  owned Pod to adopt the target PVC and pass its mount receipt. When the
  template-only update leaves the existing Pod on the legacy claim, verify its
  exact owner, UID, resource version, and source claim before requesting
  controller-managed recreation through Kruise's `specified-delete` label.
  Repeat that
  post-open proof even when a completed migration is clean before the main
  release opens, rather than short-circuiting finish and missing regression
  caused by the opening itself. Completed outer declarative-release replay also
  invokes this spool convergence before Flux readiness instead of skipping the
  inner stage callbacks. Keep Kruise
  webhook configuration objects under Flux drift correction while delegating
  only their controller-managed webhook lists and `template` certificate
  snapshots, so a missing configuration is recreated instead of ignored as an
  entire document. Recover Soperator-owned
  Kruise StatefulSets admitted during that outage by restoring only a missing
  rolling-update partition to the upstream default before dependency health.
- Replace `upgrade node-group` with the canonical `migrate node-group` command
  and no compatibility alias. The completed executor creates a permanent
  replacement group, validates it, performs Soperator placement cutover,
  retires the source, restores autoscaling, records forward-only recovery after
  cutover, rejects legacy checkpoints, and proves final provider and Terraform
  no-op state. Keep migration independent from Soperator upgrade receipts and
  status, with its own source-worker Slurm maintenance, staged placement apply,
  recovery ledger, report, and config-generation authority. A crash after
  journaling reservation intent resumes by creating the missing reservation;
  migration never records a foreign or absent reservation as applied.
- Fix live Soperator status for cxcli-owned Flux installs by projecting the
  unique current-generation Ready main HelmRelease into the release view and
  failing closed on incomplete or version-conflicting graph evidence.
- Fix equal-release Soperator replay over a staged cxcli Flux graph by
  routing observation through the frozen graph's declared release and product
  gates, including its retained suspended namespace owner, instead of applying
  direct-upstream discovery and ActiveCheck defaults to staged `chartRef`
  releases.
- Fix native Soperator ActiveCheck readiness by following the installed API's
  check-type-specific `Complete` status and creation-check filter instead of
  waiting for an `Available` condition that its controller does not publish.
- Add `soperator status --verify-observability` as the explicit read-only
  current-evidence verifier. It reuses validated live target context, accepts
  only the operator's Nebius CLI/SSO token, polls direct Prometheus and Loki,
  and writes a separate sanitized owner-only receipt. Remove telemetry phases,
  observability credentials, IAM/static-key handling, and Kubernetes Secret
  handling from every Soperator upgrade and no-op plan so upgrade completion
  ends at authoritative product and scheduling readiness.
- Fixed protected Soperator checkpoint replay after legacy HelmRelease retirement by using Kubernetes' machine-readable optional-object contract instead of parsing human NotFound text.
- Fix first Soperator rootfs-slot adoption to recognize the admitted `/home`
  persistent-PVC rebind as a mount-transport transition while still requiring
  the exact protected PV/PVC, consumer, storage, and Secret identities.
- Fix final Soperator product readiness to accept the canonical namespace
  HelmRelease only in its permanently suspended, Ready, current-generation
  state while continuing to reject suspension on every active child release.
- Preserve graph-derived AppArmor, MariaDB, and Prometheus controller
  capability flags when a partial Soperator operator override activates
  upstream's replacement semantics. Validate Soperator v1 SlurmCluster
  readiness through its native phase and component conditions, and v1alpha1
  NodeSet readiness through its Ready phase and exact desired/ready replica
  counts, instead of generic status fields those APIs do not publish.
- Treat the canonical passive-slot inventory as a sealed pre-activation image
  proof. After activation, allow target-owned Soperator runtime changes outside
  selected persistent mounts while continuing to verify exact protected
  PV/PVC and consumer identities, without creating another whole-rootfs
  inventory Job.

- Repair adopted Soperator worker and REST activation during protected
  upgrades. Render-only topology hydration now canonicalizes legacy and current
  GPU resource aliases against the discovered per-node capacity, emits matching
  GRES/static topology, and rejects conflicts or over-capacity requests before
  apply. REST-enabled controllers now wait for the protected jail's JWT
  directives before Slurm starts, breaking the SConfig REST-before-reconfigure
  bootstrap cycle without user action. An interrupted exact failed-adoption
  repair may resume from its fully proved `running` receipt while preserving
  the completed passive-rootfs Jobs; unrelated receipt or render drift remains
  fail-closed. The `disabled` topology profile now emits the explicit empty
  plugin override required by upstream 4.1.7, so GPU workers do not enter the
  topology ConfigMap wait path when no topology producer was selected.
- Repair live Soperator onboarding and upgrade discovery for older official
  releases: preserve authoritative default partition topology, keep Helm
  storage and Slurm workload namespaces distinct, preserve the process
  environment for live subprocesses, and use a bounded official GitHub release
  list fallback after transient tag-endpoint failures. Resolve onboarded
  physical SFS IDs from exact `READ_WRITE` MK8s node-group attachments, exclude
  dynamic compute-disk CSI handles, require retained local PV bindings, and
  verify each resolved filesystem's immutable live Nebius identity before
  upgrade mutation without requiring or changing the independent optional
  `forbid_deletion` provider control. Default managed Soperator SFS profiles to
  `false` while preserving an explicit user choice. Fall back from the login
  container to the controller `slurmctld` container for read-only protected
  Slurm-state capture only on the known configless/DNS failure, and resolve
  retained protected PVC/PV identities from exact physical-SFS receipt bindings
  when similarly named dynamic PVCs coexist. Advance registration to v3 so its
  fingerprint binds observed source provenance without binding the mutable
  desired app version. Reuse a matching owner-only sealed release snapshot for
  up to 15 minutes across dry-run and execution after revalidating its digest,
  first-seen tag identity, and content-addressed source, avoiding redundant
  unauthenticated GitHub resolution. Allow official GitHub API reads to use an
  out-of-band `GH_TOKEN` or `GITHUB_TOKEN` without logging or persisting it.
  Normalize the protected-upgrade handoff so current-chart storage aliases
  cannot collide with the adapter-owned aliases regenerated from admitted PVC
  identities. Rebase an exact project-root path alias such as macOS `/tmp` to
  its physical root before crash-safe generation containment checks while still
  rejecting symlinks below the project root, and validate upgrade admission at
  the selected target's staged Flux Kustomization rather than the multi-target
  parent. Verify official source/package render equivalence with the same
  adapter-compiled upstream umbrella values used by that staged render instead
  of passing cxcli's raw `nodesets` list into the upstream chart, and read the
  protected adapter/rootfs-capacity manifest from that same selected target
  subtree rather than the multi-target Flux root. Move live registration
  observation and release-neutral topology
  projection out of the Typer composition root into focused services. Treat
  Slurm's successful no-reservations status as an empty admission inventory
  instead of a malformed reservation record, and encode rootfs preflight
  operation fingerprints as Kubernetes-safe hexadecimal label tokens while
  retaining the full digest in sealed evidence. Replace source/reference
  rootfs classification with a content-free target-wins admission that binds
  the exact target image, selected persistent paths, active PVC, and staged
  passive-PVC storage contract without creating a scratch PVC or writing
  customer storage. After commit, authenticate the exact passive PVC as empty,
  populate it once from the digest-pinned official image, inventory it once,
  and seal that materialization receipt for recovery. Run rootfs inventory and cleanup Jobs
  through the official populate-jail image's POSIX `/bin/sh` contract, and poll
  their exact authenticated Job state so terminal failures surface immediately
  instead of waiting for the completion timeout. Admit a newly formatted
  rootfs as logically empty only when its authenticated inventory is empty or
  contains exactly one empty `/lost+found` directory, while rejecting every
  file, link, child, or additional path. Treat each selected persistent path as
  a customer-data boundary whose retained PVC intentionally shadows official
  image content at that subtree, while all unselected rootfs content remains
  target-owned. Execute Slurm probes issued through the login workload inside
  its mounted `/mnt/jail` rootfs, retain only the bounded controller fallback,
  and report the exact target release stage currently being installed. Disable
  only the uninstall CRD-cleanup helper in the exact digest-pinned staged
  `victoria-metrics-k8s-stack` 0.39.4 raw child, guarded by its exact identity
  and complete source-value shape, whose implicit kubectl image is unavailable;
  keep that same exact child's validation webhook fail-closed while using Flux
  `RetryOnFailure` at a 30-second interval so its initial operator endpoint can
  become Ready without an uninstall/reinstall race. Return any unexpected
  non-main staged `Stalled` condition immediately to the forward supervisor for
  exact-graph retry instead of consuming the full stage timeout; every other
  artifact, child, value shape, and install shape fails closed. Parse
  Slurm reservation records by quote-aware field boundaries so login-shell
  timestamp values containing unquoted spaces remain complete and canonically
  shell-quoted instead of blocking recovery. Prompt a
  terminal-driven upgrade for its durable Slurm job policy before admission,
  default that choice to guarded `requeue-hold-all`, and require an interactive
  terminal only when an explicitly interactive policy actually encounters
  blocking jobs. Refresh the adapter mount-gate image to
  the registry-resolved immutable digest and invalidate recent release snapshots
  whose recorded adapter image no longer matches that authority. Make an
  admitted target-apply repair a receipt-linked successor that imports and
  live-verifies the predecessor prefix, reuses its sealed passive-rootfs
  materialization, and never repeats rootfs population or inventory. Recover
  an older pre-fix replay only by write-ahead deletion of its exact incomplete
  read-only duplicate inventory Job, with both UID and resource version as API
  preconditions; completed, writable, foreign, or later-stage workloads fail
  closed. Re-prove repair storage from the authenticated predecessor receipt
  and authoritative Nebius filesystem reads instead of a globally clean
  post-apply discovery snapshot, and isolate optional Kruise workload collection
  so an absent optional API cannot erase required Kubernetes inventory.
- Generalize the canonical CLI contract from Soperator to the complete root,
  every public group and leaf, all parameters, hidden surfaces, and help text.
  Verify every public help/version/callback boundary from one exact built wheel
  across Python 3.12-3.14 CI using the same downloaded artifact.
- Make quality, coverage, and CLI-architecture baselines monotonic against the
  merge base: type ceilings may only fall, Ruff allowlists may only shrink,
  coverage floors may only rise, and new service/domain definitions may not
  accumulate in the Typer composition root.
- Require project-local, independently verified SSH host keys for WireGuard and
  SSH jump-host day-2 commands. Both commands now use strict host-key checking,
  reject missing or unsafe trust files before remote work, disable global
  known-hosts fallback, and ignore only `generated/ssh_known_hosts` in managed
  deployment repositories.
- Restrict `soperator install --resume --dry-run --replan` to stale,
  never-executed owner-only plan receipts. Reject every started, failed,
  partially applied, completed, corrupt, or authority-drifted receipt before
  replanning; validate a replacement Terraform plan separately, publish a new
  approval fingerprint only after validation, and preserve the prior saved
  plan/receipt if replacement planning or publication fails. Bind the exact
  five-command order and exercise every callback from the installed wheel.
- Make SecretStash primary-version promotion snapshot the canonical config,
  generated manifest, and tfvars before Terraform output reads, then commit the
  exact changed subset with compare-and-swap preimages so concurrent operator
  edits fail without partial writes. Rework staged Soperator reconciliation to
  establish final child specifications under a suspended outer release, remove
  child suspension fields stage by stage, and freeze exact main-workload
  authority before interpreting `Stalled` or `Ready`; only an authenticated
  main `Stalled` condition can terminate a started upgrade.
- Complete the remaining review remediations: make Soperator upgrade and
  destroy promote config plus the full render-owned generated postimage in one
  v2 transaction with deletion tombstones and reject non-canonical generation
  identifiers or digests; freeze the exact main HelmRelease
  UID/generation in the operation anchor so only that component can terminate a
  started upgrade and generic Flux waits cannot authenticate terminal evidence;
  add tri-state credential delivery recovery with durable cache/Kubernetes
  Secret markers and parent-directory fsync for cache commits, treat destination absence as ambiguous,
  sanitize credential-provider errors, and never let a completed generation
  fence later operator edits; and make
  `validate-sources` resolve official Soperator `latest` to an exact version
  while rejecting mutable versions for every other chart.
- Add crash-safe project bundle generations and credential-only IAM
  compensation, with content-free owner-only journals and foreign-edit/link
  rejection; identify SecretStash in user-facing surfaces while retaining the
  required `mysterybox` service identifier. Extract Soperator configuration
  materialization from the Typer root and add Python 3.12-3.14 offline CI,
  installed-wheel verification, Ruff, format/mypy debt ratchets, and global
  plus safety-critical branch-coverage gates.
- Harden architecture-preserving Soperator and local trust boundaries: keep a
  started upgrade retrying within the same invocation except for a typed main
  workload terminal failure; bind explicit Kubernetes contexts to the exact
  target UID; verify complete cached-source receipts; bound decompressed chart
  archives; require HTTPS release and chart authorities; classify Kubernetes
  absence only from structured `Status` objects; validate resume evidence;
  bind Terraform apply to an owner-only saved-plan digest; make normalized
  config writes atomic; and close the synchronous Nebius SDK client.
- Harden Soperator recovery and teardown safety: use persistent file locks,
  reject malformed Kubernetes and Object Storage lease expiry metadata, renew
  writer authority during takeover quiescence, revalidate Slurm journal action
  identities, bind managed destroy plans to exactly one approved cluster ID,
  restore exact config bytes after failed cleanup rendering, fail live status
  on incomplete collection, and omit raw Helm failure output from discovery.
- Close the canonical Soperator CLI proof boundary with shared target-selection,
  onboarding fail-before-write, explicit cluster-identity, invalid-redaction,
  and per-command option-rejection regressions. Verify from the installed wheel
  that a missing non-interactive install release fails before configuration or
  provider work, and align FEAT-015 with read-only discover/status behavior.
- Finalize one root group with exactly five public commands and pin every group,
  command, argument, and option description in the built-wheel contract v3;
  cover fresh-install forwarding, replan, upgrade job-control and approval
  forwarding, and both managed command routes. Make `--no-interactive` select a
  non-prompting upgrade job policy even in a TTY, keep raw collector output out
  of discovery bundles, durably publish first-seen release-tag identities, and
  bound downloaded chart metadata inspection before YAML parsing. Make live `soperator status`
  reuse registered onboarded cluster ID/access through a scoped temporary
  kubeconfig when no context is stored, retain nonpersistent managed handoff,
  and project only allowlisted stable install, upgrade, and destroy
  classifications. Reject selected Slurm job IDs and policies that do not
  match before config, cloud, or Kubernetes access. Bind the discovery rerun
  and bundle identity to one durable kube-context input while keeping temporary
  collection contexts out of both.
- Make new interactive Soperator install and upgrade operations show a
  runtime-resolved `latest(X.Y.Z)` release default, while non-interactive
  fresh installs and every non-interactive upgrade require an explicit exact
  version or `latest` before any resolver, cloud, or Kubernetes access. Preserve
  frozen recovery without re-resolving `latest`; interactive upgrade recovery
  may omit its selector while non-interactive recovery must repeat the frozen
  requested selector. Reject install-resume release, identity, profile, network,
  subnet, and overwrite overrides, enforce the complete official-release capability matrix
  through `make test-integration`, and verify the exact five-command option/help
  contract from the built wheel during `make all`.
- Reject protected Terraform, Flux teardown, and component-target
  removal paths for every Soperator app row or registration marker, including
  disabled and partial state. Run the lifecycle guard before canonical auth,
  generated tfvars/report materialization, or provider work so rejected generic
  commands are side-effect free.
- Complete cluster retirement with global
  `destroy CONFIG --target CLUSTER_ID [--dry-run] [--yes] [--delete-sfs]` for
  managed and onboarded clusters. Use immutable backend receipts, exact cloud
  destroy/preserve inventories, exact confirmation or `--yes`, one SDK cluster-delete
  request and constrained ancillary Terraform reconciliation. Default to SFS
  preservation; explicit SFS deletion requires dedicated, detached, unprotected
  storage. Preserve independent VM-NFS resources, fence concurrent operations,
  and bind the final project generation for recoverable publication. Route generic
  destructive paths to `destroy`.
- Move Soperator registration and protected-storage evidence to fail-fast v2
  contracts. Onboarding now proves verified official Helm-render equivalence
  plus live persistent object identity and rendered-field equivalence while
  persisting only digests and redacted PVC/PV/CSI identities. Upgrades use
  physical SFS as the canonical storage
  model, verify immutable live Nebius filesystem identities, and retain the explicit VM-NFS
  variant.
- Make `soperator status` report active install, upgrade, safety-pause, and
  destroy recovery state plus the canonical resume command without mutation;
  keep `soperator discover` support-bundle-only. Remove the unused mutation
  ConfigMap APIs and the duplicate outer upgrade retry supervisor.
- Restore the accepted pre-project-lifecycle `nebius-cxcli` workflow,
  catalog, Flux, observability, configuration, command, authentication,
  development, and security documentation in `README.md` and `docs/design.md`.
  Keep the lifecycle-managed upstream Soperator contract authoritative, add
  final task-oriented contents, and bind general documentation restoration to
  its current heading and body contract.
- Align Soperator documentation and CLI help with the exact five-command
  surface, distinguish the four lifecycle-changing commands from read-only discovery and
  status, and clarify that interrupted upgrades recover through the same
  approved upgrade command rather than a separate resume command or flag.
- Make protected rootfs admission resumable from one sealed content-free
  target-wins decision. Admission creates no reference/scratch PVC and performs
  no customer-storage write; after commit, cxcli authenticates the exact
  passive slot, populates it once from the official target image, inventories
  it once, and binds the resulting materialization receipt to recovery. Bind
  the complete admitted Slurm job, partition, and reservation preimage into the
  fenced cluster recovery journal.
- Preserve the official populate-jail runtime contract in custom active/passive
  Jobs by forcing overwrite only after the target precondition passes and by
  adding the upstream-required `SYS_ADMIN` and `SETFCAP` capabilities.
- Treat selected persistent paths as customer-data ownership boundaries and
  every unselected rootfs path as target-owned. Do not derive a historical
  source image or block on package/system drift; the single digest-pinned
  official target image is authoritative outside retained mounts.
- Make `soperator upgrade --execute --approve` freeze an authoritative inline
  admission receipt before Kubernetes or Slurm mutation, pause every live `UP`
  partition under exact full-record ownership, inventory jobs cluster-wide even
  when no worker Pod is discoverable, offer guarded automatic requeue-and-hold
  or interactive job handling, and supervise the admitted operation forward to
  completion without a terminal ordinary-failure budget. Login continuity is
  best-effort advisory evidence; authority, protected-state, journal,
  single-writer, and ambiguous mutate-once conditions enter a re-proving safety
  pause instead of terminating or rolling back the upgrade. Preserve the
  expired-takeover quiescence obligation across retries, recover the exact held
  Lease after transient renewal outages, reject partial Slurm partition
  inventories, and describe login evidence as sampled EndpointSlice plus TCP/22
  observations rather than continuous SSH monitoring.
- Add `soperator install --release latest|X.Y.Z` as the only fresh Soperator
  birth path. It creates the role-separated MK8s and SFS topology, freezes an
  exact infrastructure and official-upstream release plan, and requires the
  reviewed fingerprint for execution.
- Make `soperator onboard` adoption-only and target-free: it registers one
  unambiguous existing Nebius MK8s plus official Soperator installation without
  choosing an upgrade release.
- Make `soperator upgrade --to-release latest|X.Y.Z` use one capability-based
  operation planner for cxcli-installed and onboarded targets. Equal releases
  are observation-only, downgrades and unknown transitions fail before
  mutation, and execute freezes `latest` exactly once. Protected transitions
  keep the same MK8s cluster, adopt exact protected storage and SSH identity,
  classify and populate an active/passive jail rootfs, and use a single-writer
  Flux ownership handoff without recreating the control plane or NFS data disk.
- Resolve official release tags, Git identities, source archives, chart graphs,
  scripts, image references, and direct first- and third-party package digests
  into an immutable resumable snapshot. Persist the first fully verified
  repository/tag commit and tree identity and reject a moved tag. Remove the
  bundled target release lock.
- Make the digest-addressed frozen upstream populate-jail image the sole target
  rootfs authority. Reject `jailRootfs.targetImage`, mutable references, and
  competing image inputs, and bind the official image across rendering,
  operation identity, recovery journal, classification, resume, and receipts.
- Retain `/home`, `/data`, `/scripts`, and `/models` through mandatory
  path-specific PVCs, let the first-adoption upgrade wizard approval-bind
  additional existing data directories without copying them, and treat every
  unselected rootfs path as target-owned. Reject newly introduced optional paths
  after slot adoption rather than silently redirecting live data.
  Recycle a non-empty inactive slot only through an identity-bound, journaled,
  unconsumed-PVC cleanup stage on later active/passive upgrades.
- Classify releases from required source structure and values contracts rather
  than chart names, and fail closed when a required CRD, SlurmCluster template,
  Flux graph marker, or rootfs values surface is absent or incompatible.
- Reconcile verified upstream product resources plus a thin Nebius adapter;
  keep Terraform limited to out-of-cluster infrastructure.
- Use verified official upstream artifacts plus the thin Nebius adapter as the
  only Soperator product-delivery path. No OCI mirror, proxy, fallback registry,
  offline authority, or release-unpacking installer is supported.
- Keep migration planning, protected-state handling, scaling decisions, and
  recovery in the common capability-based operation engine.
- Keep the canonical `soperator create`, `soperator onboard`,
  `soperator upgrade`, `soperator discover`, `soperator status`, and global
  `destroy` implementation in runtime and package artifacts.
- Harden Soperator operation, release, recovery, and destroy receipts with one
  owner-only atomic writer and symlink-rejecting reader; align generic render,
  destroy, Flux teardown, lifecycle diagrams, and job-monitoring guidance with
  the five-command Soperator surface and global destroy.
- Preserve an onboarded cluster's non-secret, release-neutral live Slurm
  topology through an explicit projection and render its generated handoff
  immediately after the registration config is committed. Target profile
  defaults hydrate that projection only in render memory; arbitrary live
  images, environment values, init containers, annotations, and volume payloads
  are not copied into cxcli configuration.
- Bind reconciliation to an immutable operation specification, operation
  anchor, infrastructure identity, renewable lease, and exact protected-state
  and Slurm journals.
- Treat the first target declarative apply as the forward-only cutover boundary;
  restore source ownership only before that boundary and only while no target
  owner exists. Hold an exact operation-owned Slurm maintenance reservation
  through product readiness, remove it immediately before releasing requeued
  jobs, keep observability verification outside the operation, and leave every
  pre-existing reservation untouched.
- Refuse passive-rootfs cleanup until a context-pinned, identity-bound operation
  journal proves the exact PVC empty and unconsumed; bind cleanup and population
  Jobs to their full workload specification and selected Kubernetes context,
  and never recreate a completed stage whose checkpointed Job is missing or
  replaced.
- Persist a cluster-bound frozen release intent before upgrade config/render
  mutation, reuse it instead of re-resolving `latest` after interruption, and
  bind source/target capability plus rendered/reconcile stage-plan fingerprints
  into operation, anchor, and receipt authority.
- Disable upstream bundled Grafana, avoid duplicate collection of
  Soperator-owned namespaces, and grant configured Soperator node-account groups
  project-scoped editor access for metrics and log ingestion.
- Align offline rendering tests with frozen official-upstream release fixtures,
  add direct hostile-archive/cache, fencing, recovery-journal, rootfs race, and
  observability-verifier negative coverage, verify the single delivery path in
  CI and wheel contents, and keep two accessible SVG sources of truth for the
  protected workflow and jail storage design.
- Harden protected-upgrade recovery by accepting the canonical captured `/home`
  digest, rotating an interrupted operation anchor only to a strictly newer
  lease authority through Kubernetes CAS, rejecting stale Slurm-journal
  writers, rechecking suspended source HelmReleases, and making completed
  source retirement replay-safe.

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
