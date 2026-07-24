# Changelog

All notable changes to the reusable Codex skills are tracked here.

## [Unreleased]

### Changed

- Narrowed `install-grafana-mcp-for-nebius` to explicit installation,
  configuration, authentication repair, external-Grafana setup, and
  datasource-list readiness validation. Routine datasource, dashboard,
  PromQL, LogQL, and trace-tool questions now hand off to the read-only
  `nebius-grafana-query` skill without a legacy query path in the installer.
- Aligned the public Codex config template, local preflight, global-context
  setup guidance, fixture validation, and installed skill copies on
  `agents.max_threads = 16`.
- Updated the public Codex config baseline to use `gpt-5.6-sol`, `xhigh`
  reasoning for normal and Plan modes, and the Fast service tier by default.
- Refactored the read-only `code-info` skill to accept explicit project-folder
  paths and report concise project descriptions, documented features, CLI
  command hierarchies through three levels, separated code/test/docs/config LOC,
  project packages, direct and statically selected/resolved dependencies, and
  approximate pinned Redis/SQLite size comparisons. Added temporary-fixture
  unit coverage and kept project code, manifests, package managers, builds,
  and tests unexecuted during inspection.

### Fixed

- Scoped the optional SDLC PreToolUse policy to active SDLC runs and removed its
  misleading static status message from the registration, so ordinary tasks no
  longer receive SDLC decisions after the hook's active-run check. Documented a
  validated, non-forced cleanup path for task-owned temporary trees because
  Codex command safety handles forced removal separately from SDLC hooks, and
  aligned the managed-instruction and hook-registration refresh contracts for
  existing installations.
- Hardened `agent-nebius-auth-diagnose` to carry one task-authoritative project
  into every later Nebius-sensitive Bash payload, place exactly one selector at
  the start of the entire outer payload, and retry shape-only hook denials once
  without rediscovery, verification, or setup. Later explicit project choices
  replace the carried selection, conflicting evidence still asks, and mixed
  local/Nebius payloads are split so unrelated local commands remain outside
  the injected auth context.
- Fixed explicit Nebius agent-auth setup after operator deletion of the managed
  service account. A canonical credential whose old account ID receives an
  exact provider-classified `NotFound` now triggers one human-profile-authenticated
  rebootstrap: create or reuse a distinct fixed account, reconcile the exact IAM
  shape, generate and validate one authorized-key credential, make one
  mode-`0600` backup, atomically replace the stale credential, and rebind the
  profile. Permission, authentication, transient, parse, generic `not found`,
  and unclassified failures remain fail-closed, and a failed replacement never
  generates a second key.
- Simplified Nebius agent auth setup to an explicit-only, single-pass workflow
  with no second confirmation or plan digest. The canonical service account now
  joins one tenant-parented group containing exactly project `admin` and tenant
  `viewer`; missing permits are added, extra or duplicate permits fail closed,
  extra or duplicate members also fail closed, and old groups remain separate
  cleanup. The group name now depends only on the immutable project ID hash,
  and the service-account name is fixed to `codex-agent-sa`; the unverifiable
  custom-name option was removed. A broken matching credential may be backed up
  and replaced once during the same explicit invocation only after a classified
  authentication failure; profile-write and transient token errors do not
  rotate keys. Runtime verification now binds both profile identity and the
  project account lookup to the canonical credential, and a healthy setup uses
  one profile token-and-identity probe instead of repeating it. Empty or
  malformed membership IDs now fail closed.
- Fixed Nebius auth reconciliation for newly created groups by accepting the
  CLI's successful empty JSON object as an empty access-permit or membership
  list, while retaining fail-closed handling for unsupported non-empty response
  shapes.
- Fixed Nebius auth quota readiness with fixed tenant `viewer`, which is
  read-only but broader than quota alone, plus a real read-only tenant quota
  list verification call. Tenant write roles stay forbidden.
- Fixed Nebius auth group-membership reconciliation to parse the current CLI
  `memberships` collection, preventing false absence and failed create-conflict
  convergence when the service account is already in the group.
- Refactored Nebius agent auth around one canonical credential parser and a
  generic project-selector runtime contract. Every selected Bash, Python,
  CLI, API, Terraform, or SDK command now receives renewable profile/project/
  credential-file context without a hard-coded executable allowlist or a
  universal bearer token. Added a child-scoped just-in-time token helper with
  bounded idempotent retry, read-only runtime verification that needs no admin
  profile, explicit `blocked-admin-auth` IAM-plan reporting, and true no-op
  profile convergence. Legacy/ambiguous credential identities fail closed;
  setup continues to preserve service accounts and credentials rather than
  deleting or revoking them automatically. Cloud lookup failures now stop
  instead of masquerading as missing resources, create conflicts require a
  successful convergence read, credentials are validated in private
  same-directory temporary files before atomic replacement. Profile-list failures
  and unsupported output now fail closed, while membership reconciliation
  follows every returned page token rather than assuming one page.

- Hardened Nebius auth recovery so correctable selector/profile/environment and
  token-exposure denials explicitly retry without running setup. Added a
  protected one-refresh/one-retry helper for explicitly idempotent operations,
  empty-token failure handling, recursive secret-output policy enforcement for
  wrapped commands, exact helper-name matching, and denial of token-bearing
  transport tracing. Repair-lease issuance now also requires verified basic
  project access, and lease validation rejects noncanonical JSON field types
  without leaking a traceback. Runtime credential validation now shares the
  lease helper's bounded-file contract. Runtime command detection now follows
  supported process wrappers structurally and ignores inert token text or
  Nebius-looking test paths instead of over-injecting credentials, while
  permitting non-output shell comparisons of injected token state.
- Fixed the SDLC `PreToolUse` secret classifier so public Nebius profile,
  project, and credential-file path assignments no longer trigger false
  authentication-policy denials, while all other long Nebius assignments
  remain fail-closed. The agent auth hook now directs ordinary commands through
  its automatically selected profile and reserves direct token minting for the
  exact `/dev/null` verification form.
- Tightened `task-implementer-test` integration-task guidance to require
  long-form service network object maps with empty option objects, matching the
  pre-Compose ownership validator instead of allowing ambiguous list syntax.
- Finalized successful cleanup reports with an accurate no-action result, and
  made a later explicit destroy promote the cleanup stage to PASS instead of
  leaving stale retained-run wording or a NOT_RUN cleanup row.
- Fixed Task Implementer post-integration correction handling so a cleaned
  final wave can append a new isolated correction tail before finalization,
  and clarified that the replacement worker—not the coordinator—must invoke
  `task-recover` so heartbeat and finish ownership bind to the right session.

### Added

- Added the implicitly invokable `nebius-grafana-query` skill for bounded
  read-only Nebius Grafana datasource, dashboard, metrics, logs, and available
  trace-tool questions. Added reciprocal trigger evals and a static cross-skill
  contract test that keep MCP setup and authentication repair explicit.
- Added a global remediation-budget contract backed by `troubleshoot` and an
  optional `PreToolUse`/`Stop` hook bundle. After the first failed repair, the
  agent records a stable blocker and distinct remediation ledger in private
  task state, reports failures 1 and 2, and stops after three failed repairs or
  60 active minutes with a complete troubleshooting report. Only an explicit
  user instruction starts another bounded tranche; raw command failures and
  diagnostic experiments do not consume attempts.
- Added project/account/credential-bound local-repair leases for
  unattended Nebius tasks. Leases expire within 24 hours and authorize only
  mode-`0600` correction and exact CLI-profile rebuilding; IAM, credential
  generation or rotation, identity, and hook changes remain excluded from the
  lease and require their separate explicit workflows.
- Added `agent-nebius-auth-diagnose` as an implicitly selectable, read-only
  current-session project discovery and authentication triage skill. It routes
  hook failures without creating or repairing IAM, credentials, profiles,
  selectors, or hooks.
- Added a shared global-context task-state helper with collision-resistant
  session paths, private empty-scaffold initialization, read-only nested
  permission auditing, and an explicit content-preserving permission repair.
  Added `install-skills.sh --refresh-hook-registrations` for targeted replacement
  of a differing registration with the same event/script and identical handler
  list while preserving unrelated `hooks.json` content.
- Added the implicitly invokable `troubleshoot` skill for controlled causal
  investigation and repair across application code, shell scripts, CI,
  installed software, services, containers, networks, storage, and
  infrastructure. The workflow preserves a stable failure contract, separates
  incident stabilization from diagnosis, maintains competing hypotheses and
  discriminating experiments, localizes the earliest divergence, requires
  causal proof before closure, and applies the narrowest durable repair. It
  permits bounded reversible live changes only in confirmed non-production,
  keeps production and unconfirmed targets read-only without exact approval,
  and requires action-specific authorization for destructive, credential,
  IAM, data, public-exposure, deletion, material-cost, and
  material-availability changes. Bundled Python helpers collect sanitized local
  evidence, measure repeated-command behavior and signature clusters, and
  compare known-good and known-bad snapshots with deterministic tests and
  process/trigger evaluation cases.
- Added the explicit-only `task-implementer-test` verifier with a no-flag
  lightweight Task Implementer contract and temporary-fixture profile plus
  opt-in `--create`, `--create --keep`, and idempotent `--destroy` modes for one
  replace-on-create, exactly owned local frontend/API/PostgreSQL fixture. The
  live contract isolates Task Implementer private state, generation-fences
  mutations, digest-pins Compose input, validates exact dual-label runtime
  ownership and loopback/database exposure, derives frontend/API/database and
  restart evidence through fenced helpers, and gates PASS on canonical
  dependency-wave, Git, unchanged-prompt, application, and lifecycle evidence.
  It preserves sanitized reports and lifecycle history and fails closed rather
  than creating a second instance. The deterministic gate also runs the
  verifier's own helper suites, validates the exact folder/frontmatter identity
  and two-command surface, and preserves distinct PARTIAL, FAIL, and
  OWNERSHIP_BLOCKED lifecycle classifications. Live Compose inspection parses
  Docker's documented JSON Lines output as well as single-document output, and
  preserved reports are finalized from lifecycle-owned cleanup results. The
  Docker boundary now rejects every non-allowlisted Compose/build key before
  invoking Compose, locally built images require exact generation-plus-project
  labels, collection holds one lifecycle lease through HTTP/database/restart
  mutations, and an atomically moved deletion tombstone resumes exact cleanup
  after partial filesystem deletion without re-adopting a damaged project.
  Canonical Compose validation now accepts only Docker Compose's injected null
  `command` and `entrypoint` plus empty network `ipam` defaults after strict
  raw-source validation, while continuing to reject authored or non-empty
  runtime/network overrides.
  Reports now use a generation-fenced ordered stage ledger and show stage
  totals, individual tier/wave/runtime outcomes, explicit bounded failure
  analysis, downstream NOT_RUN stages, and the next action. Failure-path report
  synthesis uses the same matrix instead of reducing the run to one overall
  sentence.
- Hardened `sdlc-workflow-test` live cleanup with canonical Docker identity
  deduplication, cumulative idempotent retry reporting, and inspect-proven
  already-absent handling. Replaced Edge/fallback browser reuse with one fresh
  verifier-owned Chrome process group, isolated profile, verification marker,
  marker-gated Computer Use, and exact identity-scoped close behavior.
- Hardened `sdlc-workflow-test` with resumable retained failed/partial
  lifecycles, v4 immutable phase-time Git identity and ancestry-bound canonical
  phase results, structured Computer
  Use readiness state, and a v3
  seven-lane and exact 20-skill evidence contract with identity/head binding,
  explicit lightweight/three-tier/deterministic/safety provenance, owner-local
  assertion artifacts, profile source-schema artifacts, a fail-closed bounded
  evidence collector, canonical phase/handoff assertions, and SHA-256
  verification, plus verifier-derived deterministic and merge-safety profiles
  that live manifests cannot self-assert and strict retained three-tier source
  validation while lightweight claims remain fail-closed; preserved partial semantic
  progress; explicit retained-resource reporting; and fail-closed rejection of
  placeholder or self-attested PASS artifacts. The merge skill is verified by
  explicit-authorization safety evidence only and never by a real merge.
- Added an opt-in real-life profile to `sdlc-workflow-test`: `--create` builds,
  locally ships, and semantically tests an owned task-board GUI,
  Django/Gunicorn API server, and PostgreSQL database through the normal
  Agentic SDLC phases; `--create --keep` retains the exact application; and
  `--destroy` safely removes it. The unchanged no-flag verifier remains the
  default. The new lifecycle includes two-container Compose ownership labels,
  loopback-only dynamic web ports, internal-only PostgreSQL, computer-use GUI
  UAT with API/database correlation and restart persistence, complete sanitized
  reports, resumable fail-closed cleanup, and retained audit reports.
- Completed the Agentic SDLC prompt and execution coordinator with private
  editor-backed prompt creation/history, exact-rename repair, enforced
  initialized-folder scope, hashed worker ownership and interrupted recovery,
  resource-free future-wave replanning, staged secret/private-endpoint gates,
  automatic sequential ephemeral `codex exec` fallback, and generic v2
  Task Implementer/Agentic SDLC outer-worktree leases while preserving the
  public two-command `$sdlc-start` interface.
- Added the explicit-only `sdlc-prepare-execution` phase and private Agentic
  SDLC execution engine for schema-v4 feature integration worktrees,
  deterministic dependency waves, immutable task assignments/results, exact
  Git identity checks, retained worker/merge commits, ff-only promotion,
  non-force cleanup, and disposable real-Git lifecycle coverage.
- Added the explicit-only `worktree` skill for project-scoped monorepo work in
  sibling full-repository Git worktrees based on `origin/main`, with generated
  collision-resistant branch names, preservation of unrelated primary-checkout
  changes, `commit-push` and `create-pr` handoffs, exact merged-PR/head cleanup
  proof, durable private ownership manifests, primary-checkout-only non-force
  cleanup with immutable retry proof, bounded interrupted-setup recovery,
  atomic expected-SHA local-ref deletion, exact-lease remote branch deletion,
  and offline real-Git lifecycle tests.
- Added hook file provenance to `install-skills.sh` hook payload sync so
  source and target hashes are recorded, and differing existing hook payloads
  are backed up before being refreshed under
  `$CODEX_HOME/.install-hooks-state/backups/`.
- Added the `agent-nebius-auth` setup-only skill for bootstrapping and
  repairing Codex Agent Nebius service-account authentication, including an
  idempotent setup script and a `PreToolUse` hook that injects short-lived
  Nebius token environment variables into matching Bash commands without
  returning token material as model context, fail-closed token disclosure
  guardrails, and local hook regression tests.
- Added `docs/agentic-sdlc-design.md` to document the Agentic SDLC
  architecture, requirements/design templates, local run state, hook
  boundaries, MCP role, and skill-by-skill lifecycle.
- Added the Agentic SDLC skill set: `sdlc-create-requirements`, `sdlc-start`,
  `sdlc-gather-context`, `sdlc-create-design`, `sdlc-create-plan`,
  `sdlc-prepare-execution`, `sdlc-tdd`, `sdlc-implement-plan`, `sdlc-validate-codes`,
  `sdlc-unit-tests`, `sdlc-evaluate`, `sdlc-classify-failure`,
  `sdlc-auto-steering`, `sdlc-gui-test`, `sdlc-tui-test`,
  `sdlc-update-documents`, `sdlc-commit`, `sdlc-uat-tests`,
  `sdlc-merge-pr`, and `sdlc-align-specs`. These skills keep committed
  product truth in `docs/requirements.md` and `docs/design.md`, keep execution
  state under `~/.codex/sdlc-runs`, record mid-run prompts in private steering
  state, update project-facing docs from implemented evidence, and model the
  SDLC loop as skill-selected phases rather than a workflow CLI. The SDLC flow
  reuses existing `create-pr` and `review-pr` as PR creation and review
  handoff phases.
- Added SDLC templates for requirements, design, context packs, locked feature
  plans, validation/test/evaluation evidence, local commit evidence, and UAT
  reports, plus state-schema and failure-taxonomy references.
- Added a source bundle for optional Agentic SDLC PreToolUse and Stop hooks
  under `sdlc-start/assets/hooks/`, including shared hook helpers and local
  unit tests for continuation, write-policy, authorization, steering, and
  short-alias behavior.
- Added this `skills/CHANGELOG.md` so reusable-skill release notes live with
  the skills instead of in the monorepo root changelog.
- Added the `align` Codex skill for end-to-end project review and repair passes
  that inspect a codebase broadly before editing, then fix inconsistencies
  across implementation, tests, CI, CLI/help output, documentation, and
  formatting instead of only reporting them.
- Added the `align-skill` Codex skill for reviewing and standardizing Codex or
  Agent Skill folders, including `SKILL.md`, references, assets, scripts,
  official vendor-doc verification, safety guardrails, canonical structure, and
  validation evidence.
- Added the implicitly invokable `app-stack` skill for selecting, reviewing,
  simplifying, modernizing, and coordinating implementation of application
  technology stacks from application archetypes and quality attributes. It
  includes an opinionated Python web profile, explicit required/conditional/
  deferred/rejected decisions, specialist-skill routing, trigger evals, and
  current official vendor references.
- Added the `brainstorm` Codex skill for evidence-first, chat-only ideation
  before implementation, with source priority across the current project
  folder, sibling repo folders, related skills, internal Confluence/Slack/Jira
  sources when available, and official vendor docs.
- Added the `create-learning-course` Codex skill for creating public-safe
  course workspaces, syllabi, lessons, exercises, glossaries, learning records,
  reusable assets, and publication review checkpoints from a learner mission
  and trusted sources, using a mission-led learning model inspired by the
  public teach-skill pattern with explicit redaction and
  sensitive-information guardrails.
- Added the `research` Codex skill for senior-engineer technical due
  diligence on technologies, APIs, RFCs, protocols, architecture patterns,
  products, and feature requirements, with ranked sources, internals,
  operations, limitations, alternatives, and actionable recommendations.
- Added the `design` Codex skill for general software design before
  implementation, covering brownfield and greenfield paths, requirement
  understanding, code/docs inspection, `research`-backed topic and technology
  due diligence, component and architecture design, alternatives evaluation,
  and Codex `/plan` handoff.
- Added the `code-review` Codex skill for strict implementation-quality audits
  of the current branch, local diff, changed files, or provided patches,
  focused on maintainability, abstraction quality, modularity, type boundaries,
  file-size growth, spaghetti branches, and missed structural simplifications.
- Added the `system-design-rules` Codex skill for applying a refined 100-rule
  software system design checklist to architecture proposals, ADRs, design
  docs, API and data model choices, reliability, security, observability,
  performance, cost, migration, and team-ownership decisions before
  implementation.
- Added the `task-implementer` Codex skill for narrow brownfield implementation
  loops that order prompt-derived work as `task-1` through `task-n`, keep one
  write task active at a time, and hand off between fresh Codex sessions
  through private markdown checkpoints.
- Added the `sdlc-workflow-test` Codex skill for safely verifying the Agentic
  SDLC workflow from outside the workflow with required phase-skill discovery,
  hook configuration inspection, disposable PreToolUse and Stop hook fixture
  tests, disposable golden-path guidance, idempotency and failure-loop checks,
  and a report under `~/.codex/sdlc-verification/`.
- Added the `apply-security` Codex skill for security scans, remediation
  plans, safe patching, verification, and explanation across Terraform,
  Kubernetes, Helm, CI/CD, Bash, Python, Java, JavaScript, TypeScript, and Rust
  codebases.
- Added the `attach-ubuntu` Codex skill with a Bash helper that creates or
  reuses a per-project Ubuntu container, mounts the current project at
  `/workdir`, updates VS Code attached-container defaults, bootstraps Ubuntu
  build tools and Python project dependencies inside the container, isolates
  them in a container-specific virtual environment, preserves Git metadata for
  monorepo subprojects, and best-effort opens a new Dev Containers window for
  the running container.
- Added the `commit-push` Codex skill for committing all current feature-branch
  changes with `git add -A`, pushing the branch to `origin`, and reporting
  final worktree cleanliness without opening a pull request.
- Added the `commit` Codex skill for fast local commits on the current branch:
  it stages the complete repository diff with repo-root `git add -A`, runs
  lightweight staged validation, commits with normal hooks, and intentionally
  does not push, create PRs, or participate in Agentic SDLC state.
- Added the `code-info` Codex skill for copy/paste-friendly project code
  metrics, including LOC by language and component, repo size and link,
  test-file counts, CLI command detection, module/package counts, artifact
  sizes, and available coverage artifacts. It supports local folders and
  remote GitHub repositories that are not cloned locally by reading a temporary
  repository archive with local GitHub token discovery when needed.
- Added the `create-pr` Codex skill for branch-safe GitHub PR creation.
- Added the `merge-pr` Codex skill for non-SDLC GitHub PR merging that verifies
  PR metadata, checks, reviews, mergeability, base branch, and head SHA before
  calling `gh pr merge --match-head-commit` without admin bypass.
- Added the `config-codex` Codex skill for bootstrapping public-safe local
  Codex runtime setup, including global `AGENTS.md`, `config.toml` features
  and MCP servers, hooks, task-state layout, read-only custom agents, and a
  design README.
- Added the `global-context-management` Codex skill for keeping complex Codex
  sessions focused and recoverable with durable task state, concise
  parent-thread discipline, bounded read-only subagents, local-only hook
  templates, final risk review guidance, a local-only template validation
  helper, and a skill-local design README.
- Added the `install-grafana-mcp-for-nebius` Codex skill for installing the
  official Grafana MCP server, wiring it into Codex, refreshing a
  Nebius-managed Grafana token file, discovering Grafana datasources, and
  keeping external Grafana static-key setup guarded and optional.
- Added the explicit-only `nebius-audit-log` Codex skill for bounded,
  read-only Nebius Control Plane Audit Logs queries by resource or current
  subject, with a sanitized CLI helper, official Nebius reference notes, and
  fake-CLI unit tests.
- Added skill-local `README.md` files across reusable skills so each skill
  folder has human-facing architecture, workflow, core concept, and file
  responsibility documentation.
- Added the `publish-helm` Codex skill for OCI Helm chart publishing.
- Added the `review-pr` Codex skill for GitHub-backed PR review and
  merge-readiness work.

### Removed

- Removed the legacy `agent-nebius-auth` skill name, `--project-name` setup
  selector, global default-project writes, and runtime fallback to inherited
  project environment/default files/single credential filenames.
- Removed the `onboard-nebius-cxcli` and `release-generator` skills.

### Changed

- Renamed setup to `agent-nebius-auth-setup` and made it explicit-only. Setup
  now discovers the project from current-session evidence,
  resolves authoritative project metadata before mutation, accepts only
  `--project-id` with an optional tenant assertion, verifies existing
  credential ownership, and supports an optional real dry-run mutation plan.
  Runtime Nebius Bash calls now require exactly one leading
  `CODEX_NEBIUS_PROJECT_ID=<project-id>` selector. Setup sanitizes inherited
  auth variables and uses collision-resistant project-bound groups; the hook
  rejects conflicting profiles, unsafe environment wrappers, indirect process
  launchers, managed-auth unsets, and unsafe local credential files. Exact
  token verification now requires the canonical credential to pass the same
  local safety checks.
- Changed global-context task-state lifecycle ownership: normal startup remains
  lazy, compaction and the first complex prompt create only an empty `0600`
  scaffold below `0700` directories, and the parent agent remains responsible
  for the semantic rolling summary. Unsafe, overlong, dot, and dot-dot session
  IDs now use collision-resistant derived segments, and managed symlink paths
  fail closed.
- Updated `design` to delegate undecided or reconsidered application-stack and
  layer technology choices to `app-stack`, including frontend, API, backend,
  data, asynchronous-work, deployment, and observability decisions, while
  retaining ownership of the complete cross-layer design and `/plan` handoff.
  Designs with an already approved stack now explicitly skip stack reselection,
  and scoped `app-stack` handoffs return to the active design workflow without
  recursive routing.
- Changed Task Implementer worker assignments to the hard-cut v7 schema and
  coordinator state to v4. Dependency-free `standard` workers receive
  240/300-second read-only warning/timeout budgets, while dependent
  `integration` workers receive 360/420-second budgets. Assignments now bind
  implementation steps and end-to-end validation so the assignment plus
  incoming handoff is complete worker context and full-prompt/coordinator-state
  rereads are unnecessary. Assignments embed the exact helper and workspace
  manifest paths needed for startup. Workers make `task-start` their first
  private transition after immediate Git/cwd verification, passing the embedded
  digest unchanged for the helper's authoritative canonical validation instead
  of guessing JSON serialization,
  before incoming-handoff reading or deeper preflight consumes the 60-second
  start budget. The hard cut has no v3 coordinator or v6 assignment
  compatibility path. Worker assignments retain
  immutable default guardrails: workers stay inside their assigned worktree and
  private state; required installed skill instructions/helpers and standard
  local executables are read/execute-only. Workers cannot modify installed
  files, intentionally write unrelated paths, or access network, credentials,
  external services, or live runtimes unless the exact assignment explicitly
  authorizes that action. Task decomposition must also repeat applicable prompt
  and repository constraints in every task's stop context. Mutable task-plane
  v5 now distinguishes queued assignments from explicitly armed worker slots,
  records 30-second worker heartbeats, and exposes a coordinator watch check
  that fails closed when fresh assignment-only workers miss a 60-second armed
  start deadline, exceed their profile-specific read-only/no-file budget, a
  240-second stale heartbeat, or the immutable total worker budget. The live
  verifier stops a stalled disposable run and continues from successful
  workspace validation directly into runtime evidence and report generation.
  It now escalates the profile-specific read-only warning before the hard cutoff and
  forbids background or autonomous heartbeat loops. `task-start` is single-use,
  only in-claim mutations count as progress, and out-of-claim mutations stop as
  `WORKER_SCOPE_VIOLATION` rather than extending worker budgets.
- Changed Task Implementer planned-tail replanning to remove superseded waves
  from the active coordinator index while preserving their blocked private
  history. Final completion and semantic validation now see only completed
  waves plus the replacement schedule, preventing duplicate task ownership
  and impossible cleanup demands after safe pre-resource correction tasks.
- Changed `task-implementer-test --create` planning to require four explicit,
  self-contained verifier task contracts before dispatch, including frontend
  port 80, API-owned idempotent schema setup without bind mounts, and the exact
  strict Compose ownership/allowlist model. Workers no longer need forbidden
  full-prompt rereads to discover runtime constraints.
- Renamed the external workflow verifier skill to `sdlc-workflow-test` across
  its folder, invocation, metadata, ownership markers and labels, Compose
  prefix, reports, tests, and documentation. This is a hard cut: there is no
  alias, compatibility reader, or migration path for retained old-format
  verifier state.
- Changed `sdlc-workflow-test --create`, including `--create --keep`, to
  serialize lifecycle mutation and replace the previous active exactly owned
  three-tier environment through fail-closed cleanup before creating a fresh
  one. Cleanup combines recorded inventory with resources discovered by both
  exact ownership labels; immutable verification-generation fencing covers
  every later helper mutation and holds the lifecycle lock through each owned
  Compose action. Ambiguity or stale generations block mutation so multiple
  live stacks are not intentionally started; the no-flag invocation remains
  lightweight and does not create or inspect a Docker/browser application.
- Changed standalone `sdlc-workflow-test --destroy` to leave all browser tabs
  untouched and treat recorded tab state as audit metadata only, while retaining
  exact project, lifecycle, Git, verification-ID, Compose, and dual-label
  ownership checks before deleting verifier-owned application resources.

- Hardened `sdlc-workflow-test` Computer Use verification with just-in-time
  capture gates before GUI evaluation and UAT, unlocked-session checks unless
  locked Computer Use is explicitly enabled, visible foreground current-Space
  window requirements, accurate pre-navigation
  `ENVIRONMENT_DEFECT` reporting, actionable retained-runtime guidance, and a
  hard stop on further Computer Use calls after a hang or shared-service
  response loss. Default cleanup now fails closed as resumable
  `CLEANUP_FAILED` when the unhealthy service prevents safe dedicated-tab
  closure.

- Tightened the Agentic SDLC three-tier verifier so keep mode rejects a recorded
  closed browser tab, reports retain the exact custom verification root in the
  destroy command, migration evidence is mandatory, GUI artifacts must be
  recognizable PNG or JPEG files, the readiness matrix includes the harness,
  PASS requires a non-empty all-passing validation set, and per-run reports
  record validation commands, truthful retention state, and issue/fix guidance.
  Validation records reject named secret arguments, authorization headers,
  user-password flags, and credential-bearing URLs before persistence. Clean
  baseline identity can be recorded before promotion so failed runs no longer
  lose their known starting SHA in the report.
- Hardened the opt-in Agentic SDLC three-tier harness after a real application
  run: managed prompt rendering now preserves canonical starter identity and is
  covered by real intake, public base-image pulls use a bounded owned Docker CLI
  config, runtime inventory verifies explicit web/database Compose roles,
  computer-use preflight requires a capturable browser state, and UAT failures
  update the sanitized environment report. The SDLC PreToolUse hook now applies
  dangerous-shell matching only to Bash payloads, with regression coverage for
  harmless Dockerfile command text in patches.
- Enforced one never-reused worker session per implementation task in both
  Task Implementer and Agentic SDLC, added immutable digest-bound dependency
  handoffs with structured prior-wave/prior-batch evidence, and made both
  workflows' capacity batches authoritative. Agentic worker authorization now derives
  identity from `CODEX_THREAD_ID` instead of caller-supplied session tokens;
  every older execution schema fails with `WORKFLOW_UPGRADE_REQUIRED`; there
  is no legacy read path or compatibility shim.
- Aligned the `worktree` contract with its implementation and current official
  docs: ownership state now explicitly follows fetch preflight but precedes
  managed branch/worktree creation, the Unix/Python and authenticated GitHub
  CLI prerequisites and cooperative-lock boundary are documented, and cleanup
  references the `gh pr list` command it actually uses. New generated names use
  a constant public-safe prefix instead of copying a potentially confidential
  repository-relative project scope into local and remote branch identities.
- Aligned `sdlc-workflow-test` with the completed coordinator: deterministic
  capability-level prompt/execution/worktree/hook regressions, required
  source-installed parity, a composed nested managed-outer lease test,
  verifier self-tests, private v1 live-evidence ingestion, and fail-closed
  PASS/PARTIAL/FAIL readiness reporting. Hardened the verifier against
  symlinked, broad, or unowned verification roots; unowned or remote-backed
  disposable repositories; malformed or wrong-path hook registration;
  misclassification of Codex-managed hook-state metadata;
  installed-root, hook-payload, and report-path redirects; invalid UTF-8;
  synthetic no-change live PASS; lane-path/schema drift; skipped or colorized
  test-output false passes; and private or out-of-scope paths deleted before
  the final evidence head.

- Refactored Agentic SDLC entry and steering around two explicit public
  `sdlc-start` actions: idempotent `workspace init [project-folder]` and
  prompt-bound `run <prompt-path-or-unique-filename>`. Private managed prompts,
  immutable revisions, exact-once steering linkage, prompt-bound Stop
  continuation, terminal rerun semantics, non-Git greenfield identity, and
  fail-closed unfinished legacy handling now preserve user prompt history
  without adding a public workflow CLI or coupling to `task-implementer` state.
- Refactored Agentic SDLC end to end around task-level parallelism: locked plans
  now define stable `TASK-*` dependencies, write claims, and conflict domains;
  TDD and downstream evidence use a persistent integration checkout;
  `sdlc-implement-plan` dispatches one fresh agent/branch/worktree per safe task
  and integrates waves in stable order; `sdlc-commit` seals and exactly
  fast-forwards the unchanged project branch. Registered external worktrees now
  remain under hook policy, unsafe resources are retained, unfinished schema v1
  fails closed without a migration shim, and the verifier covers scheduler,
  real-Git, registered-worktree, recovery, and golden-path behavior.
- Composed `worktree` and `task-implementer` with fail-closed private lifecycle
  coordination: nested task workers now base on and promote only to the exact
  managed outer worktree branch, declare and clean internal resources per wave,
  retain the outer lease through final alignment, and block outer publication
  or removal until release. `worktree` push and PR handoffs now use resumable
  action-bound reservations to close check-to-mutation races. Added strict
  scope containment, interruption recovery, malformed-state blocking, and
  composed offline real-Git coverage without adding public commands or legacy
  migration/force-clear paths.

- Changed hook installation status output to render nonzero `updated` file
  labels in bold green on interactive terminals, including the per-source and
  summary lines; non-interactive and `NO_COLOR` output remains plain text.
- Hardened `agent-nebius-auth` runtime policy so authenticated status and log
  commands can use ordinary `echo`/`printf`, strict-mode setup, non-secret
  exports, and scoped `env` wrappers without false disclosure denials, while
  executable token minting, token-variable output, tracing, and full
  environment dumps remain fail-closed. Generated wrappers no longer resemble
  literal Nebius secret assignments to sibling policy hooks, manual token
  verification is restricted to one agent-profile command whose sole stdout
  destination is `/dev/null`, and troubleshooting now distinguishes local hook
  policy failures from actual credential or project-access failures before any
  repair or browser-auth decision.
- Changed `agent-nebius-auth` to grant its custom Codex Agent group the
  project-level Nebius `admin` role by default instead of `editor`; the
  existing `--role` override remains available for explicitly narrower setups.

- Refactored the explicit-only `task-implementer` from sequential
  execution-plane v1 into a resumable coordinator/worker v3. One unchanged
  `run <prompt-path-or-unique-filename>` now builds deterministic dependency
  waves from locked write claims and conflict domains, dispatches isolated
  full-repository worktrees in capacity-sized batches, verifies one reviewed
  direct-child commit per worker, integrates task branches in stable order,
  promotes only by verified fast-forward, and cleans reachable temporary
  resources without force. Shared specs/docs remain coordinator-owned;
  failures retain recovery state without mutating the project branch; and
  unfinished v1 runs fail closed with `WORKFLOW_UPGRADE_REQUIRED` without a
  compatibility path.

- Refactored the explicit-only `task-implementer` to two public actions:
  idempotent `workspace init [project-folder]` and retry-safe
  `run <prompt-path-or-unique-filename>`. Initialization creates a starter only
  when needed and preserves all prompt/run history; running privately creates,
  continues, or reconciles immutable revisions while implementing one task per
  fresh session. Stable prompt filenames are ordered in command output by
  private `last_invoked_at`, and users never supply internal IDs.
- Added a durable per-task execution plane to `task-implementer`: the Skill now
  atomically claims one dependency-ready task in planning, requires and hashes a
  complete plan and queue before authorizing product edits, requires a clean
  claim-time Git baseline, validates exact changed-path and commit evidence,
  rejects multiple task commits, retains all recovery-session participants,
  preserves reconciliation-safe completed checkpoint digests, enforces a
  bijection across completed tasks/checkpoints/stopped planes, and requires a
  distinct runtime Codex session before every other task in the scope.
- Extended `task-implementer` with same-prompt steering and incremental
  specifications without adding a public command. Accepted edits are immutable
  ordered revisions with private dispositions; implementation-time steering is
  queued without changing the locked plane and is reconciled in the next fresh
  session. The workflow now normalizes stable `TI-REQ-nnn` requirements before
  task creation, records just-in-time `TI-DES-nnn` designs, and validates
  byte-preserving managed regions in project `docs/requirements.md` and
  `docs/design.md` inside the affected task's single commit. Agentic SDLC
  ownership and unsafe paths, markers, mappings, or document-envelope drift
  fail closed.
- Lightened all repo-owned runtime `SKILL.md` files by replacing the repeated
  long learning-loop section with a concise equivalent, and moved detailed
  command, checklist, and standards material from `align`, `align-skill`,
  `create-pr`, `review-pr`, and `terraform` into on-demand `references/`
  files while preserving each skill workflow and validation contract.
- Updated `brainstorm` to route unresolved recommendation-changing source
  conflicts through a bounded `research` pass, and strengthened `design`,
  `task-implementer`, and Agentic SDLC design/plan/implementation skills to
  prefer vertical end-to-end slices for serial multi-layer application work,
  with verifier coverage for the new SDLC template and phase contracts.
- Aligned downstream Agentic SDLC skills with the vertical-slice architecture:
  context packs now capture layer-boundary facts, TDD and unit-test phases
  carry planned slice coverage, validation records locked-slice boundary
  checks, evaluation records end-to-end slice observation, documentation
  requires evaluated slice evidence for multi-layer behavior, and
  `sdlc-align-specs` checks the slice across specs, plans, tests,
  implementation, docs, and evidence.
- Hardened `task-implementer` so complex sequential loops explicitly require
  per-task context gathering, `design` routing for non-trivial choices, a short
  implementation plan before edits, and handoff fields for context, design,
  plan, validation, review, fixes, and `$commit` evidence; added a local static
  smoke test for the workflow contract.
- Deduped and shortened `global-context-management/SKILL.md` so the runtime
  skill load keeps one canonical task-state section and one canonical
  delegation/lifecycle section while preserving the existing validators and
  subagent authorization contract.
- Relaxed `global-context-management` read-only subagent delegation wording so
  a user-enabled local hook policy request is treated as sufficient current-turn
  authorization when the runtime and active instructions permit delegation. The
  parent agent should dynamically spawn useful targeted read-only helpers
  without asking for another user prompt only because the original prompt did
  not mention subagents.
- Changed hook payload installation so selected source bundles refresh
  differing existing hook files by default after backup, while hook
  registration is preflighted before payload sync and still preserves existing
  `hooks.json` entries unless `--replace-hooks-json` is explicitly set.
- Aligned `publish-helm`, `publish-image`, and `publish-release` project-local
  shell helper templates with their canonical doer scripts: generated helpers
  now use `--mode prep|publish|verify`, require explicit `--tag`, reject the
  old `--prep`/`--publish` interface, keep setup defaults overridable by flags,
  include artifact-specific verify modes, and check duplicate release tags before
  the branch-sync fetch can alter local tag refs.
- Hardened `publish-release`, `publish-image`, and `publish-helm` so release
  prep and publish phases must start from a clean, synced default branch; prep
  now creates and pushes `release/<tag>` from that branch, and the old
  non-default-branch publish escape hatch fails fast.
- Refreshed `align-skill` against current official OpenAI Codex skill guidance,
  separating the portable `SKILL.md` minimum from this repository's stricter
  source-owned skill structure, clarifying optional upstream
  `agents/openai.yaml` versus required repo policy, documenting optional
  `references/`, `scripts/`, `assets/`, and repo-local `evals/` surfaces, and
  updating the OpenAI metadata template. The repo standard preserves
  `agents/openai.yaml` for every source-owned skill so invocation policy and
  interface metadata stay reviewable.
- Updated `align-skill` so every target skill alignment must apply
  `code-review` in review-only mode and `apply-security` in advisory or scan
  mode before claiming the target is aligned, with report templates recording
  fixed, deferred, skipped, incomplete, or blocking review-lane findings.
- Tightened `brainstorm` source gathering so source-ranked context must be
  relevant to the exact topic, question, or problem statement and limited to
  information that can answer the question, resolve the challenge, close a
  named gap, or change the recommendation.
- Hardened `config-codex` laptop setup so existing `AGENTS.md` and
  `config.toml` are treated as merge targets by default, exact template checks
  are explicit audit modes, default validation rejects empty or stale managed
  `AGENTS.md` blocks, and reviewed hook payload sync backs up differing
  existing hook files before refreshing them from source.
- Enhanced `align` into a changed-scope post-change quality gate that
  coordinates mandatory cross-code validation, `code-review`, `linter`,
  `apply-security`, and focused test/build lanes while keeping safe-only
  remediation, incremental scope expansion, and explicit approval for risky
  security or public-contract changes.
- Expanded `code-review` from strict structural implementation-quality review
  into a neutral, evidence-based findings-first review skill that also covers
  bugs, regressions, meaningful test gaps, reliability and operations risk,
  security-adjacent implementation issues, owner-review needs, and severity
  decisions while preserving boundaries with `review-pr`, `align`,
  `apply-security`, and `system-design-rules`.
- Hardened `task-implementer` into an explicit-only sequential implementation
  loop where each task session validates the active task, runs `code-review`,
  fixes scoped findings, commits through `$commit`, records review and commit
  evidence in the private handoff, stops, and hands the markdown context to the
  next fresh Codex session.
- Changed `apply-security` to implicit invocation so it can act as a general
  security adviser during design, implementation, review, and validation
  sessions, while keeping patching constrained to authorized low-risk
  remediations.
- Clarified that `align` coordinates the `apply-security` security lane by
  resolving and reading the `apply-security/SKILL.md` contract directly when it
  is absent from the initial skills list because of budget, installation, or
  discovery limits.
- Refined `align` wording so its child-skill coordination, mandatory
  `apply-security` lane, and high-risk security approval boundaries are clearer
  and less repetitive.
- Clarified that `align` separates active changed scope from unrelated dirty
  worktree files and reports unrelated changes instead of silently treating
  them as alignment scope.
- Extended `agent-nebius-auth` runtime injection for long-running Bash commands
  by exporting `NEBIUS_AUTH_CREDENTIALS_FILE`, defining a
  `nebius_refresh_token` Bash helper through a restricted temporary `BASH_ENV`
  file for child Bash processes, denying common nested environment dumps, and
  documenting the credential-file or per-operation refresh pattern for raw
  Nebius API calls.
- Fixed `nebius_refresh_token` so every refresh clears existing token
  environment variables before invoking the Nebius CLI profile token command,
  preventing later refreshes from reusing a stale `NEBIUS_IAM_TOKEN`.
- Hardened `install-grafana-mcp-for-nebius` local config checks so an existing
  Codex MCP server that points at a byte-identical source or installed wrapper
  copy is treated as matching state instead of a replacement-required
  mismatch.
- Hardened `install-grafana-mcp-for-nebius` token safety so the Nebius wrapper
  refuses external Grafana URLs, config checks redact token-like MCP fields,
  arbitrary byte-identical wrapper copies are not trusted, shell startup scans
  block persisted token exports, and token refresh intervals must stay below
  the Nebius token lifetime.
- Aligned `install-grafana-mcp-for-nebius` metadata and trusted source-wrapper
  checks so project/resource query prompts use PromQL-compatible datasource
  wording and source checkouts are recognized by skill-relative path rather
  than a user-specific repository path.
- Expanded `install-grafana-mcp-for-nebius` usage docs with a bounded
  datasource-discovery, label-discovery, and project/resource metric-query
  workflow for Nebius-managed Grafana.
- Simplified `install-grafana-mcp-for-nebius` usage prompts around
  user-facing Grafana questions, added a redacted MK8s GPU usage example, and
  documented the Codex-to-MCP-to-Grafana query flow.
- Updated `brainstorm` so major design or architecture decisions consult the
  installed `design` and `system-design-rules` skills as advisory context when
  accessible while keeping brainstorming chat-only until the user asks to
  switch workflows.
- Set `create-learning-course` to explicit-only invocation because course
  creation can create or revise many local files.
- Tightened the `design` skill so topic, feature-requirement, architecture,
  product, standard, and technology due diligence routes through `research`
  when that skill is available, leaving `design` focused on synthesis,
  alternatives, and `/plan` handoff.
- Extended the `design` skill so standard, deep, architecture-heavy, or
  hard-to-reverse solution designs apply `system-design-rules` as an advisory
  checklist before final `/plan` handoff while leaving checklist-only reviews
  owned by `system-design-rules`.
- Strengthened `sdlc-create-design` with the same phased design discipline used
  by `design` while preserving Agentic SDLC ownership of `docs/design.md`: it
  now records existing-system evidence, selected and rejected options,
  implementation boundaries, validation/test/evaluation plans, rollout,
  rollback, and context-gap routing before a feature can move to planning.
- Strengthened `sdlc-validate-codes` so mechanical build/lint/type/import,
  dependency, and configuration checks are followed by `code-review` in
  review-only mode; blocking review findings now keep the feature out of the
  `validated` state and route repair to the responsible SDLC phase.
- Extended the Agentic SDLC requirements, start, evaluation, and UAT skills
  with an optional Live Experiment Environment contract so users can provide a
  confirmed non-production or disposable target, safe connection references,
  allowed actions, reset instructions, approvals, and evidence limits for later
  agent experiments without storing secret values.
- Updated the `research` skill source order so organization-tied research
  searches internal Slack and Confluence sources through available connectors
  first, uses MCP or app-tool access for internal systems when connectors are
  unavailable, verifies technical claims with official vendor or authoritative
  external sources, and labels organization-specific guidance separately from
  vendor facts and industry practice.
- Simplified `install-skills.sh --install-all-hooks --register-hooks` output
  into a single per-source `Hooks status` report and only shows the restart and
  trust warning when hook files or registrations changed.
- Simplified `agent-nebius-auth` setup inputs so operators pass `--tenant-id`
  plus exactly one project selector, either `--project-id` or
  `--project-name`; the setup script now resolves the missing project metadata
  through Nebius project lookup and fails fast when both selectors are passed.
- Aligned the `config-codex` global `AGENTS.md` template with the compact live
  Codex policy and synced mirrored prompt-hook delegation wording across
  `config-codex` and `global-context-management` validation.
- Added bounded same-workspace prior task-state candidate discovery to the
  `global-context-management` prompt hook and mirrored `config-codex` hook
  template, exposing candidate paths only while keeping historical task-state
  contents out of model context and preserving the current session
  `current.md` as the write target.
- Hardened `global-context-management` and mirrored `config-codex` guidance so
  parent agents must close every spawned subagent handle that is completed or
  no longer needed before finalizing when close controls are available, and
  must report any unavailable or failed cleanup instead of leaving helper
  sessions open silently.
- Strengthened `global-context-management` and `config-codex` task-state
  guidance so `$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md`
  is documented, templated, and hook-advertised as a compact rolling summary
  rather than an append-only transcript.
- Reworked the root `README.md` with a short table of contents and a complete
  grouped skill catalog covering every live source skill, its invocation
  policy, and a concise description.
- Hardened `align-skill` authoring guidance and validator coverage for current
  OpenAI Codex skill metadata: `agents/openai.yaml` is documented as optional
  upstream but required by this repository's source-skill convention, the
  OpenAI metadata template now includes invocation policy, and validator tests
  cover wrong metadata paths plus explicit-only policy derived from `SKILL.md`.
- Added explicit `agents/openai.yaml` invocation policies across all source
  skills: operational setup, commit, PR, merge, publish, security, code-info,
  container, Grafana MCP, `sdlc-workflow-test`, and every `sdlc-*` workflow
  skill now disable implicit invocation, while the remaining non-SDLC skills
  opt in to implicit matching. The Agentic SDLC Stop hook now emits explicit
  `$sdlc-start` continuation prompts, and the skill-structure validator checks
  invocation policy drift.
- Updated the `config-codex` global `AGENTS.md` template to include compact Git
  workflow defaults, explicit implicit-skill invocation policy, narrower
  mutating skill-script permission wording, and a broader changed-surface
  `$align` rule for code, config, and documentation changes.
- Hardened `agent-nebius-auth` setup idempotency so Nebius profile
  create/update restores the previously active human/admin CLI profile, setup
  serializes global profile mutations, agent service-account profiles are not
  treated as human/admin IAM repair sessions, and fake Nebius CLI regressions
  cover profile drift on rerun, effective `NEBIUS_PROFILE` selection,
  interrupted profile creation, and concurrent setup.
- Changed `agent-nebius-auth` so the setup script no longer writes an inline
  Codex hook block to `$CODEX_HOME/config.toml`. The unsupported
  `--install-hook` flag now fails fast and points operators to the canonical
  `install-skills.sh --install-hooks agent-nebius-auth/assets/hooks
  --register-hooks` path for payload sync and `hooks.json` registration, while
  setup records the selected project under `~/.nebius` for the generic hook to
  read locally.
- Removed the installer-side `agent-nebius-auth` inline `config.toml`
  migration cleanup. The canonical path is now fail-fast setup plus root
  installer hook sync/`hooks.json` registration only; stale inline hook entries
  are rejected before hook files or `hooks.json` are changed and must be removed
  manually instead of being migrated.
- Added a skill-local `agent-nebius-auth/README.md` with the setup-only
  workflow, installer-managed hook registration, runtime hook behavior,
  idempotency checks, and validation commands.
- Clarified `publish-helm` required release inputs so callers must provide an
  explicit tag and publish destination or be asked for them, documented the
  OCI repository base versus provider registry-ID workflow split, and made
  verify mode fail fast without requiring local chart files.
- Realigned the `publish-helm` setup helper template with the maintained
  chart-local helper behavior for prerelease tags, branch pushes, dependency
  validation, fallback changelog notes, and explicit missing-input diagnostics.
  Generated helpers now show help and argument errors before Helm/Python
  readiness checks.
- Added an explicit `install-skills.sh --replace-hooks-json` opt-in for hook
  registration cleanup so normal `--register-hooks` preserves hand-written
  entries while the intentional clean-reset mode backs up and replaces
  `$CODEX_HOME/hooks.json` from the selected source manifest or manifests.
- Added a hook-registration guardrail that refuses multiple registrations for
  the same hook event and Python hook filename, preventing stale entries such
  as duplicate `Stop` hooks for `stop_sdlc_continue.py` from being merged.
- Standardized the `agent-nebius-auth` hook payload under
  `agent-nebius-auth/assets/hooks/` so `--install-all-hooks` discovers and
  registers it with the other reviewed hook bundles.
- Relaxed the Agentic SDLC PreToolUse write-target policy so file targets are
  no longer blocked by path, including outside-repo files, credential
  directories, Codex runtime files, global `AGENTS.md`, locked SDLC plans, and
  private SDLC state. Ordinary outbound network commands are allowed while
  secret-bearing payloads, dangerous shell patterns, and guarded Git/GitHub
  actions remain blocked.
- Packaged the Agentic SDLC hook helper library with `lib/__init__.py` and
  explicit `lib.*` imports so direct script execution and Pyright/Pylance
  import resolution stay aligned.
- Refactored `publish-helm`, `publish-image`, and `publish-release` from
  generator-first guidance into doer-first release skills that can run setup,
  prep, publish, or complete end-to-end flows from the current project folder.
- Added deterministic skill-owned publish helper scripts for Helm charts,
  container images, and GitHub Releases, including strict clean-worktree checks,
  tag normalization, duplicate tag prevention, changelog release-section
  validation, upstream push setup, and artifact-specific publish verification.
- Hardened the skill-owned publish helpers so rerunning prep for an existing
  unreleased tag section merges new `Unreleased` bullets into that section
  instead of clearing them.
- Hardened publish helper scripts and setup templates to reject prep on the
  default branch and push feature branches explicitly to `origin`.
- Updated Helm chart prep to include generated dependency lock/archive outputs
  in the release-prep commit when `helm dependency update` creates them.
- Aligned the Helm setup helper template with the canonical doer so generated
  project-local helpers build or update chart dependencies before linting.
- Clarified `publish-helm` chart-version ownership so `Chart.yaml` updates are
  release-prep changes that must be merged before the publish/tag phase, with
  publish helpers now pointing mismatched versions back to prep.
- Fixed Helm release prep to avoid passing ignored generated chart archive
  directories to `git commit` when no dependency archives are staged.
- Added image publish workflow tag ancestry validation and removed shallow
  history from release publish ancestry checks.
- Aligned `merge-pr` with GitHub merge-queue behavior by using the no-strategy
  `gh pr merge --match-head-commit` path for merge-queue branches instead of
  treating a ready queue as an admin-bypass case.
- Expanded `docs/agentic-sdlc-design.md` with an explicit SDLC workflow
  verification procedure, including quick preflight usage, full disposable
  golden-path testing, report status interpretation, and fix/rerun policy for
  `sdlc-workflow-test`.
- Hardened `sdlc-workflow-test` hook fixture execution so subprocesses disable
  Python bytecode writes and do not create `__pycache__` artifacts in skill
  source folders.
- Aligned `global-context-management` and `config-codex` local setup guidance
  to syntax-check rendered hooks without bytecode writes, and clarified that
  installable global-context hook bundle sync is owned by `config-codex`.
- Strengthened `sdlc-workflow-test` design-contract preflight so it verifies the
  Agentic SDLC design document includes the workflow verification procedure and
  report path, not only the core SDLC lifecycle terms.
- Updated `align` so alignment decisions first synthesize the active thread,
  relevant Agent Memory, and durable task-state or workflow state files, while
  treating those sources as leads that must be verified against current
  repository or runtime evidence before safe fixes.
- Clarified `sdlc-commit` boundaries so ordinary local commits use `commit`
  and publish commits use `commit-push`; `sdlc-commit` remains Agentic
  SDLC-only.
- Tightened `create-pr` so dirty PR work runs safe formatting, whitespace,
  lint, build, and focused test checks before `git add -A` and commit, waits
  for local tests to finish, and uses `git fetch origin` plus
  `git merge --no-edit origin/<base>` before PR creation so branches receive
  the latest base updates without rewriting history. Pushing now uses explicit
  refspecs, and available GitHub checks must reach a terminal state before the
  PR is reported ready.
- Relaxed `global-context-management` subagent guidance so, after a prompt or
  user-enabled local hook policy authorizes delegation, Codex should choose
  useful targeted read-only helper roles instead of waiting for the prompt to
  name them, while still treating runtime tool availability and active
  instructions as hard gates; `SKILL.md` now also has explicit stateful
  workflow sections for inputs, required reads, writes, idempotency, failure
  handling, must-not rules, process, and completion criteria.
- Aligned optional hook-assisted subagent delegation so the opt-in policy
  template is enabled, hook context explicitly asks the parent Codex agent to
  dynamically spawn useful read-only helpers, the local idempotency preflight
  flags present-but-disabled policy files, and docs keep hook-direct tool calls
  out of scope.
- Updated `config-codex` output guidance so local setup runs report aligned,
  not-aligned, and locally blocked surfaces explicitly, including exact manual
  out-of-band remediation steps when a PreToolUse or permission guard blocks a
  safe patch, and added disposable fixture coverage for local idempotency
  policy-file edge cases.
- Hardened the Agentic SDLC verifier path resolution so installed skill copies
  can locate the repo design doc when checking the current checkout.
- Renamed SDLC-only workflow skills and the coordinator to `sdlc-*` names, and
  made their front matter descriptions start with
  `Use only as part of the Agentic SDLC workflow;` so tool discovery separates
  SDLC phases from ordinary commands and general-purpose skills.
- Hardened the Agentic SDLC Stop hook source so continuation prompts emit
  `sdlc-start` and canonical `sdlc-*` next-skill names, normalize short
  phase aliases only as input, and recognize pause or PR-control steering such as
  `Pause after the current feature. Do not create a PR.`
- Allowed the Agentic SDLC PreToolUse hook source to write
  `$CODEX_HOME/task-state` files so `global-context-management` can persist
  session-scoped continuity notes while unrelated `$CODEX_HOME/hooks` writes
  remain blocked.
- Updated `install-skills.sh` with an explicit
  `--install-hooks <source_hook_dir>` option that copies hook files from a
  selected hook source directory into `${CODEX_HOME:-$HOME/.codex}/hooks`,
  keeping runtime hook sync separate from normal skill installation and from
  `hooks.json` trust decisions.
- Added `install-skills.sh --install-all-hooks` to install every reviewed
  skill-owned hook bundle from `*/assets/hooks` under the source skills folder,
  with destination collision checks and idempotent unchanged reporting for hook
  files.
- Extended `install-skills.sh` hook installation with an explicit
  `--register-hooks` option that semantically merges source `hooks.json` or
  `hooks.json.template` registrations into `$CODEX_HOME/hooks.json`, preserving
  existing hook entries and backing up the file before changes.
- Added an idempotent, report-only hook drift summary to `install-skills.sh`
  that lists extra installed hook files and `hooks.json` registrations not
  present in the selected source manifests, with manual cleanup guidance
  instead of automatic deletion.
- Aligned `config-codex` idempotency checks with the current local setup model:
  exact global `AGENTS.md` parity when requested, required global hook
  registrations as a `hooks.json` subset, and required public-safe MCP servers
  while preserving extra workflow hooks and machine-specific MCP entries.
- Strengthened `global-context-management` wording so authorized read-only
  subagents are preferred for useful read-heavy sidecar work when the prompt or
  local hook policy authorizes delegation and the current runtime permits it.
- Updated `align-skill` with an optional stateful-workflow profile for
  state-machine skills that manage local state, locked plans, evidence,
  continuation, retries, or failure routing, including a reusable template and
  `--profile stateful-workflow` validator support.
- Clarified the `align-skill` README definition of stateful workflow skills,
  including when to use the profile and a concise SDLC coordinator example.
- Updated `create-pr` and `review-pr` with Agentic SDLC handoff guidance so
  they can read local SDLC evidence when invoked in that workflow while
  preserving their general PR behavior.
- Hardened `sdlc-start` so coordinator reruns resume from explicit
  checkpoints, repair partial pointer writes from the newest complete
  checkpoint, avoid duplicate history on unchanged resumes, and keep locked
  plans append-only by superseding instead of editing in place.
- Documented the runtime hook authorization handoff for Agentic SDLC:
  `sdlc-commit`, `create-pr`, and `sdlc-merge-pr` now write short-lived local
  authorization files under the active run before guarded Git actions, and
  `sdlc-start` state schema records the `permissions/` directory contract.
- Clarified hook ownership boundaries: non-SDLC global-context hooks own
  `SessionStart` and lightweight `UserPromptSubmit` context, while Agentic
  SDLC uses only separate `PreToolUse` and `Stop` hooks.
- Tightened global-context hook rules so `SessionStart` is limited to stable
  global context and `UserPromptSubmit` is limited to lightweight prompt-time
  hints, with no SDLC routing, requirements parsing, workflow skill selection,
  run-state creation, or large-document injection.
- Reduced global-context hook payloads so `SessionStart` and `UserPromptSubmit`
  no longer repeat detailed workflow or subagent lifecycle instructions already
  owned by `global-context-management`.
- Hardened `apply-security` validation guidance so lockfile maintenance,
  cache/build directory writes, external registry submissions,
  package-manager auto-install behavior, and live-credential checks require
  explicit scope and safety review.
- Updated `align-skill` so each alignment run captures evidence-backed,
  public-safe reusable learnings back into the target skill's local source
  materials before completion, and reports any source updates that were skipped.
- Updated `align-skill` so it can be triggered as a helper for hardening
  scaffolded or draft skills and applies safe, secure, fast authoring guidance
  without replacing `skill-creator` for initial scaffolding.
- Updated `align-skill` to recognize optional `evals/` folders for reusable
  trigger or quality-evaluation prompts, matching the existing `helmchart`
  trigger-eval convention, and added a validator self-test for the `evals/`
  acceptance plus unknown-folder warning behavior.
- Added a standard `## Learning Loop` rule to every reusable skill so durable,
  public-safe, evidence-backed learnings can be captured by the task skill that
  is already loaded, and taught `align-skill` validation to enforce the rule on
  target skills while preserving read-only and report-only task contracts.
- Tightened the `code-info` skill contract so invoking it is explicitly
  read-only information gathering: it reports from existing files only and does
  not edit, format, build, test, install, generate coverage, or stage files.
  Its helper also disables Git optional locks for Git inspection commands.
- Expanded `install-grafana-mcp-for-nebius` with a Nebius-managed Grafana
  token-refresh wrapper, Codex registration examples that launch the wrapper,
  and clearer tenant/project input guidance. The default path now avoids
  Nebius static-key and service-account setup unless the user explicitly asks
  to connect external Grafana to Nebius read endpoints.
- Hardened `install-grafana-mcp-for-nebius` for repeated laptop runs with an
  idempotent setup contract, check/apply local-config helper, managed shell
  defaults, check-before-add Codex MCP registration, and no duplicate
  static-key issuance unless rotation is explicitly requested.
- Aligned `install-grafana-mcp-for-nebius` with the current Grafana MCP docs
  and the latest released/Homebrew binary behavior: the wrapper now prefers
  token-file auth when supported, falls back to startup-only inline token auth
  for released binaries that lack token-file support, and the config helper now
  reports mismatched existing Codex MCP servers instead of silently reusing
  them.
- Minimized the global AGENTS template to durable always-on policy, moved
  detailed workflow guidance back to skills, preserved the explicit `$align`
  post-edit rule, and shortened high-impact skill descriptions so implicit
  routing front-loads trigger keywords.
- Tightened `config-codex` idempotency guidance so existing laptop
  `AGENTS.md` and `config.toml` files are inspected before backup or patching,
  template-only config settings are not backfilled on already working setups,
  and a read-only local preflight can verify that no changes are required.
- Clarified that `commit-push` always commits the whole Git repository from
  the Git root with `git add -A`, regardless of the service, package, chart, or
  project directory where the agent starts. Project-folder, current-directory,
  and pathspec-limited staging remain out of scope for this skill.
- Tightened `commit-push` so remote branch refresh uses the full
  `refs/heads/<branch>:refs/remotes/origin/<branch>` refspec and staged
  validation can repair small mechanical whitespace blockers, such as trailing
  whitespace or an extra blank line at EOF, before committing. Conflict markers,
  unresolved conflicts, broad formatter churn, semantic changes, dependency
  updates, and branch divergence still stop the workflow.
- Expanded the `nebius` skill's VPC networking guidance with the current
  Nebius network-pool/subnet model: explicit subnet CIDRs use
  `use_network_pools=false`, inherited subnets do not own every displayed
  status CIDR, subnet suggestions must account for explicit peer subnets and
  live allocations, existing-network out-of-parent CIDRs must extend an
  attached compatible private pool first, and new-network pool choices should
  hide assigned or empty private pools.
- Aligned `global-context-management` subagent guidance around the configured
  `max_threads = 16` budget: use `repo_mapper` and `test_strategist` early only
  when useful, close helpers after consolidation, and reserve `risk_reviewer`
  for near-final review of non-trivial or risky changes.
- Clarified `global-context-management` subagent fallback guidance so, when
  delegation is already authorized and useful but controls are not visible,
  agents first use `tool_search` to look for deferred multi-agent/subagent tools
  before reporting delegation unavailable.
- Clarified the `global-context-management` and `config-codex` contracts so
  subagent delegation is described as runtime- and instruction-dependent,
  noisy parent-thread output is called out as the main context-growth risk, and
  hook prompts now provide a narrow-read fallback when subagents are not
  available or permitted. Task-state files are now explicitly described as
  continuity notes that must be read at task start, resume, or compaction
  boundaries when prior context may matter, then updated at checkpoints. The
  shared hook registration template now consistently resolves scripts through
  `${CODEX_HOME:-$HOME/.codex}`, and the skill READMEs plus `SKILL.md`
  contracts now state the same installer-versus-runtime split.
- Clarified that `multi_agent`, configured `[agents.*]` roles, hooks, and skill
  activation make subagent delegation possible but do not directly spawn
  helpers. Current runtime probes can ask Codex to use or spawn subagents, use
  delegation, or run parallel agents, or rely on a user-enabled local hook
  policy that injects a bounded read-only delegation request; the `codex exec`
  examples now use the current `--sandbox read-only` flag instead of the
  obsolete `--ask-for-approval` option.
- Added opt-in hook-assisted read-only subagent delegation for
  `global-context-management`: the `UserPromptSubmit` hook can now discover
  configured read-only agents from `$CODEX_HOME/config.toml` and
  `$CODEX_HOME/agents` when a local `$CODEX_HOME/hooks/global_context_policy.json`
  policy enables it, while keeping public templates free of personal paths.
- Clarified `global-context-management` and `config-codex` delegation guidance
  so a prompt request and a user-enabled local hook policy request are both
  treated as valid subagent authorization sources, while hooks still only
  inject model-visible context and do not directly call subagent tools.
- Clarified the read-only subagent lifecycle: the parent agent should ask
  helpers for concise final summaries, wait for results, consolidate them, and
  close spawned subagent handles when close controls are available once they
  are completed or no longer needed. When multiple helpers are running, the
  parent should close each completed handle as its terminal result arrives,
  continue waiting on the remaining handles, and report any unavailable or
  failed cleanup before finalizing.
- Documented the parent/subagent mental model in the `config-codex` and
  `global-context-management` READMEs.
- Clarified that direct live hook probes with synthetic `session_id` values can
  leave scaffold-only task-state directories, and removed the no-`session_id`
  manual fallback path from the task-state hook contract.
- Extended `global-context-management` local template validation to prove an
  existing nonempty `current.md` task-state file is preserved for the agent to
  read instead of being overwritten or injected into hook context, and tightened
  hook templates so reused task-state files are chmod-repaired to `0600`.
- Changed task-state hook behavior to advertise-only: `SessionStart` injects
  the session-scoped path without creating a missing `current.md`, while
  `UserPromptSubmit` only advertises or reuses the same path for complex
  prompts and does not create task-state, SDLC run state, or workflow state.
- Tightened the `global-context-management` `SKILL.md` non-goals so a missing
  global task-state path no longer permits repo-local or manual fallback state;
  repo-local task-state files remain explicit-user-request only.
- Aligned the `config-codex` AGENTS template and skills README with the global
  script-execution policy: read-only inspection or validation scripts may run
  when they do not modify files, services, external systems, credentials, or
  persistent state, while mutating scripts still require explicit Run/Execute
  wording.
- Updated the `global-context-management` hook template so hook commands
  resolve scripts through `${CODEX_HOME:-$HOME/.codex}` and work with
  non-default local Codex homes.
- Tightened the `config-codex` skill contract so existing user `AGENTS.md` and
  `config.toml` files are patched in place rather than replaced, with
  conflicts reported instead of silently overwritten.
- Tightened the `helmchart` Codex skill with more accurate `appVersion`
  guidance, dependency-aware strict validation, safer securityContext and RBAC
  mutation rules, trigger-scope metadata, a reusable chart validation helper,
  and lightweight trigger eval prompts.
- Clarified the `align` Codex skill guidance and metadata so alignment passes
  operate as cautious senior code-review style sweeps: audit-first,
  evidence-driven, wiring-aware, conservative around public contracts and
  business logic, and still focused on fixing verified low-risk gaps across
  code, tests, docs, workflows, CLI/help output, config, and applicable project
  skills.
- Updated the `create-pr` guidance and metadata so Codex treats explicit
  user-supplied PR titles and bodies as authoritative instead of inferring a
  generic title from the branch name.
- Tightened the `create-pr` current-feature-branch path so invoking it from a
  non-default branch reuses that branch, stages existing work with
  `git add -A`, validates the staged diff, commits before switching or
  merging, pushes the branch, and opens or reuses the PR without creating
  another branch.
- Updated the `create-pr` guidance and metadata so PR commits always stage
  current dirty work from the repository root with `git add -A` instead of
  narrowing commits to selected paths.
- Expanded `create-pr` so Codex can prepare one or more conflict-free PR
  branches against the default branch, use the current branch as the target
  when no branch is provided, check ordered multi-branch merge paths, preserve
  one PR per branch, and report the manual merge order.
- Tightened `create-pr` so known branch-owned validation, build, lint, test,
  or GitHub check failures are repaired when safe before the PR link is
  presented as the completed outcome.
- Expanded `review-pr` so Codex can review PRs by number, URL, or current
  branch, distinguish user-owned, same-repository, forked, writable, and
  externally owned branches, resolve safe base-branch conflicts only when
  permissions allow, and report exact blockers when a contributor branch cannot
  be updated.
- Updated `install-skills.sh` so source-owned renamed or removed skills
  converge on reinstall and target-only skills are listed at the end with a
  short default-target `--remove-skill` hint instead of being silently ignored.
  The installer help and README now state the no-argument install and default
  removal targets, plus the custom-destination removal form.
- Aligned the reusable skill set with current skill and workflow conventions:
  `align-skill`, `attach-ubuntu`, and `nebius` now state the repository
  script-execution policy explicitly, while `github-workflows`,
  `publish-image`, and `python-project` templates use current action major
  pins verified from upstream action repositories, and skill reference docs pass
  focused Markdown lint checks.
- Refined `create-pr` and `review-pr` to follow stronger Git and GitHub PR
  best practices: `create-pr` refreshes base-branch context, avoids opening
  PRs from a dirty tree alone, and prefers draft PRs for incomplete work, while
  `review-pr` distinguishes safe local rebases from shared-branch cases that
  should use non-destructive updates, preserves unresolved reviewer concerns as
  explicit blockers, and routes selectively to sibling skills.
- Expanded `skills/README.md` with explicit Codex chat prompt examples,
  including copy-pastable `create-pr` and `review-pr` usage plus the GitHub
  prerequisites those skills expect before they can act.
- Updated the shared `python-project` skill to explain when a minimal
  compatibility `setup.py` shim still makes sense alongside `pyproject.toml`.
- Updated `create-pr` so local-work PR creation explicitly stages the complete
  monorepo diff with `git add -A` before committing, and stops instead of
  path-limiting the commit when repo-wide staging would include work that
  should not be part of the PR.
