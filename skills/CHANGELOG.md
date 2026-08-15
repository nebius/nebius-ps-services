# Changelog

All notable changes to the reusable Codex skills are tracked here.

## [Unreleased]

### Fixed

- Fixed the Task Implementer aggregate verifier's per-suite timeout so the
  measured full wave and linked-worktree crash matrix completes within its
  bounded validation window instead of being reported as a false timeout.
- Fixed project-instruction terminal sealing after another authorized workflow
  refreshed only the portable marker projection. A stale active receipt may be
  re-adopted only with explicit approval of the exact current target digest and
  unchanged project, Git root, scope, target path, and managed-body identity;
  subject drift, body drift, and missing or stale approval remain blocked.
- Fixed final Task Implementer cleanup after a sealed terminal lifecycle commit
  carries the validated requirements, design, or project-instruction overlay.
  Prompt-impact settlement now accepts that exact journal-bound direct child in
  addition to the ordinary coordinator documentation commit; unrelated or
  unsealed promoted spec drift remains blocked. The final-wave settlement also
  accepts the same revision and intent when its immutable impact still records
  the original request as `replan_required`, because no remaining wave exists;
  non-final material impact continues to require replanning. Replay also
  recognizes the exact partially published state where the new impact receipt
  is current but its plan-basis binding is stale.
- Fixed Task Implementer correction-contract preparation under an active
  project lifecycle. A new hidden, journaled `coordinator-stage` transition
  admits only the complete canonical requirements/design pair in the exact
  registered integration worktree, and the existing coordinator commit owner
  now creates or replays one clean direct-child prepared-contract commit after
  current spec validation and project-instruction rendering. The lifecycle
  hook authenticates both transitions during reconciliation while continuing
  to deny direct staging, direct commits, partial specs, extra paths, stale
  worktrees, and lookalike helpers.
- Fixed Task Implementer resume deadlock when a resource-free `wave-prepare`
  publishes newer prompt-impact evidence that invalidates its plan. Resume now
  detects the exact stale impact, retires only a no-effect prepare intent with
  no worktree, assignment, or pending Git journal, and routes to a fresh
  digest-bound `wave-replan`; partial-resource states remain retained.
- Fixed the project-contract lifecycle false-positive on validated task-owned
  temporary cleanup. The exact literal
  `find /tmp/<task-owned-tree> -depth -delete` form now reports its sole
  system-temp descendant as a recursive external target, while temporary roots,
  project or control-plane coverage, symlinks, dynamic paths, globs, multiple
  roots, and alternate `find` actions remain fail-closed. Managed instructions
  now require submitting the resolved literal so they match the hook parser;
  independent destructive-action policy still owns deletion approval.
- Fixed project-contract terminal decision recovery so an apply command that
  returns without independently verified final state conservatively invalidates
  the plan, advances the write epoch, and reopens reconciliation. The
  current-session decision can then be corrected and replanned instead of
  remaining irrecoverably stuck in `planned`.
- Fixed Task Implementer promotion-pending resource recovery so a missing
  registered integration checkout can be safely rehydrated when immutable task
  history contains both merged corrections and superseded no-op predecessors.
  Every active or failed non-superseded task state still blocks recovery.
- Fixed Task Implementer source integration after combined review requests
  changes. The exact rejected two-parent candidate is now digest-bound to the
  blocking findings, archived behind compare-and-set proof, and released
  through an interruption-safe private journal before the lane becomes
  correction-ready; the source branch remains unchanged. The same explicit
  integration workflow owns the serial correction continuation and repeats all
  validation and review gates. Wave guidance now treats shared semantic
  invariants, not only changed paths, as serialization boundaries. Lifecycle
  authorization now also accepts the sole safe resume-controlled `wave-plan`
  form only when its private token and capacity exactly match the active
  mode-0600 resume journal. Resume also detects a changed resource-free task
  contract and forces `wave-replan` before creating integration or worker
  resources. A hidden atomic sequential-worker path now launches fresh
  ephemeral `codex exec` inside `task-arm` or `task-rearm`, preserving managed
  worktree visibility across host process-lifetime boundaries without
  reconstructing resources or transferring work to the coordinator. Resume
  now also routes hard worker-guard outcomes directly to confirmed recovery
  instead of waiting for a fresh final heartbeat to become stale. The matching
  hidden atomic `run-resume` recovery launch preserves the exact recovery cwd
  and fresh-session ownership across the same host process-lifetime boundary.
  Atomic normal launches now pin medium reasoning effort and recovery-only
  launches pin low effort so a single worker turn cannot consume the hard
  heartbeat-staleness lease and cause a false recovery loop.
  Review rejection now also records an owner-only receipt for the exact
  rejected candidate, findings digest, and unchanged source/lane heads. Its
  completed-follow-up checkpoint may reopen only the current zero-write
  `non-project` promotion waiver for normal reconciliation, preventing the
  documented same-invocation correction from deadlocking in terminal waived
  lifecycle state. The Worktree owner can idempotently re-read an already
  terminal rejection while that correction has only a pending generation
  checkpoint, allowing interrupted receipt publication to recover without
  advancing integration or changing either head.
  A sequential child exit now succeeds only when the assignment's exact
  immutable result exists, preventing a start-only or recovery-only exit from
  being misreported as completed work.
  Worker-result publication now accepts only canonical `committed` or
  `REPLAN_REQUIRED` status. Exact pre-fix `COMPLETED` evidence already retained
  in a blocked failed state has a bounded strict migration after all commit,
  tree, claim, digest, and evidence checks pass; new publication fails fast.
  Worker-watch output now identifies the bounded changed and violating paths
  needed for an evidence-based claim correction without exposing file bodies.
  Recovery of such dirty scope expansion is now reporting-only and supplies no
  commit authorization, allowing a fresh worker to publish terminal
  `REPLAN_REQUIRED` evidence without silently discarding or adopting the dirt.
- Fixed Task Implementer promotion after an adopted selected-lane contract was
  legitimately reconciled by the one claim-bound final coordinator commit.
  Resume now recognizes the exact unchanged lane overlay through its immutable
  adoption ancestry, promotion clears it for fast-forward, and failed promotion
  restores the original adopted bytes rather than the newer reconciled tip.
  Cleanup now settles the advanced spec impact before deleting the promoted
  integration. Recovery and completion recognize a completed promotion journal
  as historical only when its exact done wave and promoted ancestry precede the
  active wave. An interrupted post-fast-forward replay may defer stale spec-
  impact rejection only when the clean selected lane exactly equals the sealed
  integration target; cleanup still performs exact reconciliation.
  Unrelated or materially plan-changing drift stays blocked.
- Fixed Task Implementer lifecycle authorization after worker integration. The
  coordinator can now validate its documented requirements, design, README,
  and changelog reconciliation at the sealed `promotion_pending` head, while
  product edits, untracked or deleted files, symlinks, and head drift remain
  rejected. A journaled private `coordinator-commit` now stages and creates the
  single claim-bound direct-child documentation commit, closing the same
  selected-lifecycle hook boundary without permitting raw Git mutation.
- Fixed Task Implementer correction recovery for terminal
  `REPLAN_REQUIRED` results. Resume now accepts and replays the immutable
  terminal result idempotently; an exact clean no-op failed task can be
  retained as first-class `superseded` history, have only its proven worker
  resource cleaned, and be followed by a corrected immutable assignment.
  Exact tracked dirty evidence at the base can now be quarantined behind a
  compare-and-set internal ref, tree-byte/path/parent verified on replay, and
  restored by exact path before correction; the ref survives promotion and is
  deleted only by successful cleanup. Untracked, committed, mismatched, or
  unverifiable dirt remains blocked.
- Fixed Task Implementer worker-result completion in two related failure
  boundaries: commands without a `--json` option no longer crash during common
  output emission after publication, and `changed_paths` are now canonicalized
  as a sorted unique set so claim-order output cannot be misclassified as a
  scope expansion. An exact digest-and-commit-bound retry can recover only the
  prior ordering-only false-rejection state without rewriting private run data.
- Fixed retained Task Implementer current-v7 workers becoming unreadable when
  canonical worker guardrail wording grew after successful `task-start`.
  Unstarted assignments still require current guardrails; already-started
  assignments remain bound to their exact accepted digest and receive current
  recovery, commit, and result-publication contexts without private-state
  rewriting or an older-schema compatibility path.
- Fixed Task Implementer delegated commits failing when a worker's canonical
  Python version differed from the lifecycle hook runtime. Hook and adapter
  trust now accept only the hook interpreter or an exact PATH-canonical
  `python3`/`python3.N`, while retaining exact helper, digest, action, evidence,
  repository, and owner checks. `task-start` and base-state `task-recover` now
  return a transient canonical commit context with exact prepare argv and the
  raw worker session; the separately named session fingerprint remains
  persisted evidence and can no longer be mistaken for `--session-id`.
- Fixed worker-result publication by returning an exact transient private
  result path and explicit external publication working directory, preventing
  selected-project lifecycle misclassification.
- Renamed the worker-facing task-arm and task-rearm deadline output to the
  canonical `start_lease` consumed by `task-start`, removing the ambiguous
  internal `dispatched_at` name.
- Added exact assignment-derived start and recovery contexts with canonical
  cwd and argv, preventing coordinator path transcription from launching a
  worker in a visually similar but nonexistent private checkout.
- Added a claim-bound worker-result publisher that validates the complete draft,
  computes the canonical final digest, and atomically publishes the immutable
  result from its private external working directory.
- Fixed explicit Task Implementer `run` failing after a package-manager Python
  upgrade removed the resolved interpreter recorded in the generated VS Code
  workspace. The exact stale-launcher failure now refreshes only that generated
  file through canonical workspace initialization and revalidates the complete
  workspace; all other mismatches and non-mutating `workspace reuse` remain
  fail-closed.

### Added

- Added crash-safe Task Implementer `run-summary-v1` completion evidence with
  immutable replay, evidence-backed source movement, full-repository and scoped
  Git file/line/binary statistics, queue status, exact integration readiness,
  and sanitized structured next actions. The generated workspace now labels
  `CODE — MANAGED PERSISTENT LANE` explicitly and provides a manual read-only
  pending-lane report without adding a sixth public action or hook privilege.
- Added fail-closed generated-workspace classification. `workspace init` and
  `run` migrate only the exact previous shape; non-mutating `workspace reuse`
  requests an upgrade, and forged helpers, arguments, folders, or tasks fail
  before lane creation, refresh, chmod, or other mutation.
- Added authoritative idempotent resume control for long-running Task
  Implementer runs. Repeating `run` now audits coordinator-v7, every indexed
  wave/task/immutable artifact, Git and Worktree identity, the exact generation
  lease, and lifecycle binding before returning one execute, wait,
  stop-confirmation, blocked, or complete outcome. Private
  `resume-control-v1` journals one digest-bound transition and fences stale or
  concurrent execution. It stores canonical transition arguments, binds
  capacity, task, evidence, stop authority, and final alignment, and completes
  effect/state/projection phases without repeating an already-applied mutation;
  the first controlled transition bootstraps the sidecar under both owning
  locks, every successful transition returns the next authoritative resume
  plan/token, and current journal-less v7 runs adopt only at validated
  stable boundaries. Machine state now owns effective execution status after
  coordinator creation, handoff is a CAS-protected human projection, atomic
  private writes sync their parent directories, and terminal handoff/queue
  publication follows proven external generation release and local interop
  reconciliation. Promotion-review corrections stage immutable,
  digest-addressed, resume-epoch-bound `pending-plan-v1` bytes before
  coordinator or wave publication, so retries continue from staged bytes
  instead of consuming a changed handoff task definition.
  Handoff projection now keeps unpromoted committed or merged tasks in the
  accepted `in_progress` vocabulary until promotion. A hidden, caller-digest-
  bound current-v7 recovery journals and rewrites only the proven historical
  `committed` projection so an affected retained run can repeat normal resume
  without accepting a second handoff status or changing machine state.
  Coordinator-v1 through v6 remain unsupported and no public resume action or
  flag was added.
- Raised the Task Implementer lightweight verifier's bounded per-suite default
  from 300 to 600 seconds so the full disposable linked-worktree interruption
  suite is not falsely reported as timed out.
- Added a fail-closed Task Implementer correction transition for blockers found
  by combined review before wave promotion. An exact clean
  `promotion_pending` integration can append one independent worker-owned
  correction round without advancing the persistent lane, rewriting prior
  tasks, or discarding their commits. New workers branch from the sealed
  integrated head, historical assignments remain bound to their own task-plane
  bases, future-work dependencies are rejected, generation claims extend
  before state, and the whole corrected wave must repeat combined validation
  and review before one fast-forward promotion. Its rendered project contract
  carries to the correction base only when the authoritative receipt differs
  solely by an ancestral Git head and all spec, traceability, project,
  validator, and instruction identities remain exact.
- Added owner-validated prompt-revision impact evidence for Task Implementer
  and Agentic SDLC. Existing exact-byte and normalized-intent digests continue
  to detect editable prompt changes without a self-hash header; a shared pure
  validator now requires one bounded disposition for every extracted statement
  occurrence, reparses current requirements and design mappings, and derives
  retain versus replan. Both workflows publish append-only private attempts,
  serialize publication, compare-check ledger replacement, preserve and skip
  crash-orphaned attempts, gate steering settlement and execution transitions,
  separate the latest settled revision from the execution plan basis, and
  require a distinct plan identity for material forward reconciliation. They
  preserve terminal history without fabricated evidence and expose only a
  sanitized public impact classification. Matching spec bytes or an
  unsubstantiated `no_effect` label no longer unlocks progression.
- Added evidence-preserving recovery for Task Implementer active-wave paths
  that disappear while their Worktree lease and locked Git registrations
  remain. A confirmed-stopped internal coordinator action now verifies the
  exact active generation, resource tuple, branch and registered head,
  administrative HEAD, clean index, and contract/base ancestry before using
  Git's locked-worktree rehydration form; it journals and revalidates the
  checkout, keeps the lease `present`, reports lost filesystem-only state, and
  leaves task and promotion state unchanged for normal fresh-session
  `task-recover`. Promotion-pending integration checkouts can now be restored
  only at the recorded integrated head so combined validation can resume
  without reconstructing refs or inferring promotion. Staged state, index
  locks, broken paths, branch drift, and ambiguous registrations fail closed.
  Worktree anchor discovery now skips
  unrelated missing registrations before strictly resolving the current
  managed checkout. The lifecycle bridge also attests exact active worker
  commit transactions against the worker root when the hook is selected on
  the outer lane. It now carries the command-derived worker session validated
  by the Task adapter instead of requiring the nested command to equal the
  outer lifecycle payload session, without admitting raw Git, future batches,
  mismatched sessions, or sibling roots.
- Added a fail-closed Task Implementer lifecycle adapter for the active
  prepared integration checkout. The Task helper now attests the exact run,
  wave, clean Git identity, selected project, co-located
  project-instructions helper, command digest, and run-owned evidence paths;
  the lifecycle hook independently rebinds that evidence to its selected lane
  and records a successful first `wave-plan` checkpoint before dispatch.
  The prepared-wave exception admits only inspect and render during
  reconciliation; apply and verify retain the ordinary terminal seal path.
  Exact integration identity permits either a clean checkout or only the
  selected-project requirements and design staged, while every unstaged,
  untracked, deleted, symlinked, sibling, or other staged path fails closed.
  Arbitrary bundles, helpers, projects, flags, stale worktrees, and failed
  commands remain denied. The bridge is automatic and requires no receipt
  copying or user-managed lifecycle IDs. Its hook-only authorization command
  is parsed outside discoverable helper subcommands, so CLI help exposes
  neither the private action nor its flags.
- Added `$task-implementer workspace reuse [project-folder]` as an explicit
  existing-only reopen path. It resolves the same workspace from the primary
  source project or owning managed lane, validates the generated VS Code
  workspace, exact caller root, existing source ref, and authoritative live
  lane identity, tolerates dirty or active live state without claiming
  readiness, rejects unrelated worktree aliases, and never refreshes, repairs,
  checkpoints, migrates, queues, runs, integrates, removes, or creates state.
  Missing, unsafe, removed, or mismatched state fails closed, while editor
  launch failures remain non-fatal after validation.
- Added a built-in Task Implementer pre-run checkpoint for resource-free
  persistent lanes. `run` now compiles planned claims, reserves the exact tree
  and path digest, pauses dirty candidates for complete review, claims every
  changed path, and uses a review-token-bound Worktree transaction to run
  repository-root `git add -A`, normal hooks, and one fixed
  direct-child checkpoint before opening the generation. Root, selected-project,
  and sibling-project changes stay coherent; clean lanes remain commit-free;
  the primary checkout stays untouched; and the resulting head becomes the
  lane, lease, interop, coordinator, and first-wave baseline. Private journals
  and compact run receipts recover exact staged or post-commit interruption
  windows without duplicate history, while conflicts, Git operations,
  gitlinks, tracked symlinks, hook-modified trees pending explicit review, and
  dirt after generation open fail closed without reset, stash, clean, amend,
  or unstage behavior. Hook-modified commits rotate the review token and cannot
  be adopted by a blind retry.
- Added a one-command, whole-repository `$commit` transaction for monorepos.
  A leading `$commit` or bounded leading directive such as `run`, `apply`, or
  `execute $commit` now mints a one-shot private authorization, while casual
  mentions, questions, quotations, and help remain inert;
  the digest-pinned helper previews repo-root `git add -A` in a temporary index,
  binds the reviewed tree to a durable claim, revalidates drift under a shared
  repository lock, runs normal commit hooks, and proves the exact direct-child
  tree and clean result. Raw Git mutation remains denied, active Agentic SDLC
  keeps `sdlc-commit` ownership, Worktree preparations share the repository
  lock, Task workers receive assignment/session-bound commit authorization,
  and dirty Task Implementer sources receive an explicit `$commit` handoff
  instead of auto-commit or project-scoped staging. Hook-modified direct-child
  commits now stay review-required until an exact private review transition
  acknowledges the inspected commit and tree; failed hooks that create no
  commit become stale for a fresh explicit retry. Fresh invocations reconcile
  exact post-commit crash windows without duplicating history; merge commits
  and malformed Worktree ownership or coordination records fail closed. A
  successful prepare remains bound after its one-shot authorization is
  consumed, so PostToolUse does not incorrectly reopen lifecycle evidence.
  Exact-child crash recovery now revalidates live Task Implementer worker
  ownership before completing a delegated claim. Caller-provided Git
  environment that can reshape repository discovery, worktree/common-dir,
  object storage, command configuration, or the real index now fails before
  authorization or mutation; contradictory explicit origin markers are inert.
- Made `troubleshoot` evidence-intensive and completion-gated. It now discovers
  the deployed stack and compares it with official vendor architecture, verifies
  every component and dependency, correlates bounded logs across component,
  application, orchestrator, systemd, OS, kernel, network, storage, GPU, and
  hardware layers, performs real code-path debugging, and uses one canonical
  report with seven enforced `PASS`/`FAIL`/`UNKNOWN` criteria. Added Slurm,
  Soperator, Kubernetes, Nebius, Linux, network, storage, GPU, and code-debugging
  playbooks plus healthy-status/log-revealed-cause evaluation scenarios.
  Structured `PASS` rows now require affirmative verified evidence and an exact
  no-gap sentinel, and a valid report is finalized before any later terminal
  Stop peer returns so report obligations cannot leak into subsequent turns.
  Reports now bind conclusions to explicit included/excluded boundaries,
  exercised paths, and incident-window endpoints; component proof names DNS and
  restart history; and the Stop validator requires each canonical log layer
  exactly once, in order, with a canonical coverage status. Evidence-bearing
  table cells and `PASS:` detail are validated independently so neighboring
  prose cannot make a placeholder satisfy the gate.
- Added the internal explicit-only `prompt-session-intake` owner and its
  non-blocking `UserPromptSubmit` capture hook for sessions explicitly bound to
  Task Implementer or Agentic SDLC. Every direct prompt continues to the
  current agent; safe input records only metadata-only event-v2 session/turn
  provenance and never the submitted prompt body, while
  secrets, stale writer provenance, ambiguity, conflicts, unsafe state, and
  internal capture failures skip persistence without stopping delivery. The
  staged/accepted/consumed sidecar records merge/no-op/sensitive and routes only
  an agent-selected durable project-intent projection through prompt-only CAS
  adapters. Mixed execution/conversation wrappers are excluded, project-contract
  command examples remain eligible, and every merge marker binds both operation
  ID and accepted projection digest. Exact retries and byte-identical
  projections do not append twice; distinct concurrent same-base updates keep
  one winner without auto-rebase. Event-v1 journals remain inert. Capture never
  starts or resumes a workflow. Task primary and manifest-proven lane paths
  share one logical objective; unrelated projects remain capture-ineligible.
  Captured or manual prompt edits remain inert until explicit `run`.
  Prompt-session cleanup always passes
  the shared Stop arbiter and best-effort releases writer provenance without
  adding a second registered Stop hook. Its one-shot reason digest still
  excludes an exact peer-generated Stop continuation without relying on an
  undocumented prompt-origin field.
- Added the implicit `maintain-project-specs` skill as the single canonical
  requirements/design owner for ordinary projects, Task Implementer, and
  Agentic SDLC. It provides stable-ID managed regions, shared v3 receipts,
  committed project opt-out, exact-scope lifecycle state, journaled paired
  migration and recovery for both former formats, pre-implementation rule
  rendering, deferred project `AGENTS.md` sealing, multi-edit-safe
  reconciliation, and focused owner/hook tests. Its new hook bundle adds
  SessionStart, UserPromptSubmit, PreToolUse, and PostToolUse guardrails plus a
  shared single-Stop arbiter; installer registration migrates only the two
  superseded Stop owners while preserving unrelated entries.
- Hardened the new project-contract path so only an exact committed policy can
  disable it, private lifecycle transitions use owner-only compare-and-swap state,
  rendered rules and final project instructions are reverified by their
  authoritative owner, and direct or misleading coordinator bypasses fail
  closed. Paired migration atomically publishes a complete recovery transaction
  outside Git and validates exact evidence without consuming backups during
  rollback. Stop arbitration stays within its outer timeout and combines every
  initial continuation unless a terminal result takes precedence. Task
  Implementer no longer fabricates an owner receipt for historical commit
  inspection. Valid non-Git directories now remain outside the managed project
  lifecycle instead of being globally denied, while a broken Git marker still
  fails closed. Planned and implementation lifecycle states now require a
  verified project-instructions render receipt, interrupted transaction cleanup
  retires atomically and is recoverable on the next locked invocation, and
  environment-sensitive Git/ripgrep reads require explicit helper-disabling
  arguments before the hook classifies them as non-material.
- Repaired project-contract bootstrap and command classification. Installed
  hooks now trust non-symlinked coordinators from canonical
  `~/.agents/skills`, ordinary reads and read-only shell composition no longer
  depend on a fixed system-owned executable allowlist, and quoted writer names
  or shell-control text remain search data. Proven fixed effects wholly outside
  the selected project now pass through epoch-neutral, while mixed, dynamic,
  ambiguous, and authoritative control-plane effects stay fail-closed. Exact
  canonical-spec intent-to-add, current-session task/runtime/decision inputs,
  and numeric mode-`0600` normalization break bounded bootstrap cycles without
  allowing general staging, permission, hard-link alias, or private-state
  mutation. Multi-destination directory creation, explicit target-directory
  options, hard-link sources, and tree moves retain every relevant effect
  target. Owner
  validation can atomically write its mode-`0600` receipt only at the exact
  hook-bound session path without shell redirection, and opaque turn tokens let
  agents run verified lifecycle transitions without access to raw hook turn
  identifiers. Explicit selected-project writers, cross-session paths, and
  unsafe helper options remain phase-gated.
- Added the implicit `ai-stack` skill for effective, efficient AI technology
  selection with explicit evidence classification across model providers,
  PyTorch training, inference, agent frameworks and durability, MCP/A2A
  interoperability, retrieval, evaluation, safety, and operations. The dated
  baseline uses explicit switch conditions, exact protocol and compatibility
  records, official-source verification, and logical-only implementation
  handoffs; adjacent `app-stack`, `design`, and `research` skills now route
  AI-specific decisions to it.
- Added Prompt Durability v2 to Task Implementer and Agentic SDLC. Managed
  prompts now require only generated metadata plus a meaningful Ask; optional
  and custom sections feed a coordinator-owned requirements compiler with
  selective stable clarifications. Generated `00-START-HERE.md` hubs and
  default New Prompt tasks replace manual cloning. Exact raw and normalized
  intent digests distinguish formatting from work, completed edits create
  linked fresh-objective runs, and explicit cross-prompt run requests persist
  in exact-byte and intent-drift-checked FIFO queues that activate after
  authoritative resource release and recover interrupted dequeue. Fenced-code
  indentation remains semantic, lineage and immutable snapshots are
  integrity-checked, and ready refinement state must match the exact compiled
  requirements contract before planning. Prompt-v1 execution is a hard cut
  with read-only history and no migration. The disposable Task Implementer
  live-test prompt renderer now accepts only the same v2 managed frontmatter.
  Fence-aware parsing keeps Markdown examples inside Ask, revision and
  refinement writes recover at an explicit manifest/binding commit point, and
  execution coordinator v7 binds every plan to the exact accepted prompt
  revision and intent before resources can be prepared.
- Added the explicit-only `scaffold-project` composition skill for approved
  greenfield and additive brownfield repositories. It models logical
  capabilities, materialization units, runtime units, external services, and
  one content owner per path; gathers exact private specialist candidates;
  finalizes a closed canonical JSON plan with content-addressed payloads and
  target preconditions; and applies an approved digest through a guarded
  POSIX executor with all-path preflight, no-follow writes, private backups,
  crash-safe journaling, created-directory identity binding, internal-bundle
  symlink rejection, graph-to-operation ownership enforcement, unresolved
  placeholder rejection, positive owner artifact contracts, runtime-authority
  Unicode normalization, additive suffix-only brownfield merges, drift
  detection, target-root compare-and-swap checks, retained-descriptor
  greenfield roots with platform-native atomic no-replace publication,
  per-operation architecture-source revalidation, bundle-wide apply locking,
  exact in-memory use of the approved manifest, finalized action/payload
  relation checks, apply-time semantic-merge revalidation, case-folded VCS
  control-path rejection, architecture-source/output separation, unique
  semantic-merge marker identities, structured filesystem failures, and
  idempotent resume. It never
  deletes files, runs native generators, installs dependencies, initializes
  Git, provisions, deploys, publishes, or enters Agentic SDLC.
- Added the implicit `frontend-project` specialist for standalone or
  coordinated-candidate React, TypeScript, and Vite scaffolding, with
  deterministic repo-owned templates, strict TypeScript, Vitest/Testing
  Library coverage, context-safe display-name rendering, explicit
  compatible-version inputs, and no dependency installation or lockfile
  fabrication.
- Added the implicit `container` specialist for OCI image and runtime
  engineering from repository source through a validated local artifact and
  structured deployment handoff. It covers Dockerfile/Containerfile,
  BuildKit/buildx, Compose including approved single-host production profiles,
  non-root and read-only hardening, signals, health, storage, networking,
  multi-platform and GPU evidence, troubleshooting, and SBOM/provenance/
  vulnerability/signing requirements. Docker-first audit and smoke helpers are
  offline or isolated by default, redact values, bound output and resources,
  verify task ownership before cleanup, and require explicit opt-in for builds,
  runtime tests, network access, and supply-chain tooling. The retained typed
  Python and React/Vite renderer still rejects instruction injection, requires
  deterministic dependency and setuptools-scm version identity, and keeps its
  local Compose validator fail-closed. Registry publication and signing actions
  remain with `publish-image`, workflow YAML with `github-workflows`, and
  Kubernetes resources with `helmchart`.
- Added the explicit-only shared `project-agent-instructions` skill. After
  owner-validated requirements and design, it uses deterministic
  inspect/apply/verify state to decide whether the exact selected project needs
  a concise `AGENTS.md`. It preserves human-owned and override files, records
  private evidence, uses guarded filesystem transitions, and fails closed on
  ownership, scope, recovery, safety, or concurrent-change ambiguity.
- Added the explicit-only `nosleep4mac` skill with one no-argument,
  idempotent workflow that atomically converges a public-safe per-user macOS
  LaunchAgent running `/usr/bin/caffeinate -s`. It preserves display and
  battery sleep, verifies the exact AC-only assertion, repairs stopped or
  safely drifted managed state with rollback, and leaves healthy repeated runs
  byte-for-byte and PID-stable.

### Changed

- Refined `design` and `ai-stack` around the least-agentic sufficient
  architecture: direct model calls for one-request tasks, deterministic
  workflows for known sequences, and agents only for model-selected actions or
  observation-driven next steps. Unknown-count loops remain deterministic when
  code owns continuation, while graphs and durable workflows may wrap any
  control-flow level. OpenAI direct text or reasoning generation supported by
  Responses routes to an official OpenAI SDK plus Responses API; other direct
  workloads use their official task-specific APIs. OpenAI-native agents route
  to the OpenAI Agents SDK, Anthropic/Gemini or provider-neutral Python agents
  to Pydantic AI, and coding-focused engineering specialists to the Codex SDK
  with app-server reserved for deep embedded clients.
- Replaced the Task Implementer README's text-only lifecycle map with a
  polished visual workflow that distinguishes long-lived and temporary Git
  roles, isolated worker commits, verified wave promotion, repeated dependency
  waves, run finalization, and explicit public source integration.
- Project compatibility intent is now semantic and project-scoped. A clear
  statement that existing users depend on safe future code or interface changes
  produces durable rules for supported APIs/imports, CLI behavior,
  configuration, persisted formats, and upgrade paths without requiring `GA`
  or `backward compatibility`; breaking those surfaces requires explicit
  approval, migration or deprecation planning, and regression coverage while
  internals retain one canonical path. Personal global defaults are never
  copied or allowed to suppress explicit project intent, active sufficient
  project instructions are reused, and an incomplete same-directory override
  fails closed. Nested selected projects now resolve instruction discovery from
  the nearest effective marker ancestor within the enclosing Git worktree
  while retaining their own receipt, evidence, and `AGENTS.md` target scope.
- Upgraded Task Implementer and Agentic SDLC editable prompts to prompt-v3.
  Every full prompt ID now has an exact collision-safe short reference stored
  in metadata and prefixed to its filename, and public `run` accepts the ref,
  full ID, exact filename, or managed path. Initialization performs a one-time
  journaled and retryable v2-to-v3 metadata/name migration, repairs mutable
  queue/run pointers, and leaves immutable historical revision bytes unchanged;
  there is no v2 write path. Bound direct material turns can create a lossless
  multiline objective or append a timestamped refinement with stale-base
  rejection, and their operation marker makes interrupted merge retries
  exactly-once.
- Narrowed the project-contract hook to the authority it actually owns:
  selected-project lifecycle sequencing and canonical
  `${CODEX_HOME}/project-specs` state. Fixed writes to config, hooks, task
  state, installed skills, credentials, and other external user files now pass
  through epoch-neutral to Codex, OS, destructive-action, and domain-policy
  owners. `config-codex` now distinguishes a proven repo-owned false denial
  from an external or unrepairable policy, repairs canonical source and installs
  it through the provenance path, then retries the identical authorized edit
  without alternate writers, redirection, installed-only edits, or guard
  disabling.
- Refactored the session-private `$troubleshoot` report obligation around one
  concise outcome, cause/fix, verification, and exact next-action report. The
  new `DIAGNOSED-FIXED` classification distinguishes a proven, repaired, and
  source-validated causal owner from complete end-to-end `VERIFIED_FIXED`
  activation. Ordinary incomplete, malformed, partial, `FAIL`, or `UNKNOWN`
  reports are advisory and cannot request another turn, deny tools, or emit a
  generated fallback; sensitive output, invalid trusted state, missing
  authority, peer Stop policy, and exact remediation-budget exhaustion remain
  fail-closed. Remediation authorization remains on its independent schema.
- Corrected `troubleshoot` fresh-state pending guidance to refer to the prior
  terminal marker instead of assuming it was exhausted. A resolved-marker to
  independent-blocker regression now verifies the exact patch-only handoff and
  authorization promotion.
- Fixed project-contract `PreToolUse` classification for read-only
  `find -exec stat` compositions and exact `git branch --show-current`
  inspection while keeping arbitrary `find -exec` helpers and other branch
  actions material. Canonical project-instructions guidance now includes the
  explicit Codex home and absolute current-session private paths required by
  the hook, and malformed coordinator feedback names those inspect bindings.
- Bound lifecycle-owned project-instructions inspect, render, plan, apply,
  verify, and seal evidence to the canonical current-session private bundle.
  Alternate same-project bundles and malformed coordinator-shaped
  commands now fail closed. PostToolUse also treats admitted canonical spec
  reconciliation as epoch-neutral and invalidates a concurrently completed
  plan, armed seal, or seal when an earlier successful material write reports
  late, preventing stale lifecycle completion.
- Clarified that lifecycle reconciliation applies and verifies an explicit
  project-instructions decision rather than promising `AGENTS.md` creation.
  Missing plus `not-needed` now has direct render/apply/verify coverage and is
  reported as a successful no-file outcome; the installed-hook refresh removes
  the stale per-edit sentence that caused the ambiguity.
- Changed project-contract reconciliation to keep successful PostToolUse
  accounting silent while preserving per-write epochs, concurrent recorder
  convergence, failure discrimination, and stale-plan sealing protection. Stop
  now requests one accumulated semantic review; requirements change only for
  accepted intent, design changes only for implemented boundaries or evidence,
  and implementation-only or reverted deltas can revalidate and seal with
  byte-identical specs. The PostToolUse registration no longer carries a static
  status message.
- Changed `project-agent-instructions` to a single v3 managed-tail contract.
  Needed rules can attach to a tracked human `AGENTS.md` without changing any
  existing byte; later human-prefix edits remain outside automation ownership,
  while marker or managed-body edits fail closed. Refresh preserves the exact
  prefix, retirement restores it or deletes a generated-only file, v1/v2
  markers require manual resolution, and Task Implementer plus Agentic SDLC
  consume the shared v3 schemas and `attached` outcome.
- Documented the current source-hook catalog in the root README: five
  installer-discovered bundles, 13 source entries converging to 11 distinct
  registrations, seven manifest-referenced entrypoints, event-specific
  behavior, concurrent peer boundaries, matcher aliases, single-Stop
  arbitration, and the source-versus-installed trust boundary. The surrounding
  ownership and `--install-all-hooks` guidance distinguishes peer policies and
  dynamic bundle discovery.
- Made pending `troubleshoot` remediation-budget repairs actionable across
  UserPromptSubmit, PreToolUse, and Stop. Missing or invalid markers and invalid
  same-blocker versus independent-blocker transitions now retain their precise
  bounded validation reason instead of falling back to a generic pending
  message. Fresh-tranche guidance requires the complete canonical v4 marker,
  including `blocker_summary`; invalid active-resize markers get atomic restore
  guidance, while deleted resize markers fail closed without suggesting a
  budget reset. Authorization binding, patch-only repair, terminal locks, and
  the one-continuation Stop bound remain unchanged.
- Refactored Task Implementer around persistent Worktree-owned per-project
  lanes. `workspace init` is idempotent by canonical repository, named source
  ref, and scope; it permits dirty source state while basing the lane only on
  committed `HEAD`. Runs now acquire monotonic generations with repository-wide
  claims, may execute back-to-back, and leave immutable pending receipts.
  Generation numbering remains monotonic across explicit lane removal and
  reincarnation. Reuse/refresh now shares the lane transition lock with run
  acquisition and removal, repository claims publish only after a complete
  peer scan, and removal atomically guards lane-ref deletion with the exact
  durable source ref/head proof.
  Added public `integrate [project-folder]` for validated two-parent source
  promotion plus same-lane rearm, and `workspace remove [project-folder]` for
  proof-gated explicit cleanup that preserves prompt/run history. General
  Worktree schema-v4 and public lifecycle behavior remain compatible, while
  public Worktree integration/removal reject Task lanes. The Task Implementer
  README now provides a generic post-run operator workflow covering source
  integration, primary-checkout manual acceptance, defect follow-up,
  independent publication, and optional lane retirement. It also defines the
  primary checkout, persistent lane, temporary wave and worker worktrees,
  source integration candidate, and private prompt state; distinguishes
  internal wave integration from public source integration; and explains that
  lane cleanliness is a managed precondition, not a request for a manual
  `$commit` or destructive cleanup. The Task Implementer contract smoke test
  now preserves those core README distinctions.
- Changed `task-implementer workspace init` to ask VS Code to reuse its last
  active window for the generated `CODE` + `PROMPTS` workspace instead of
  always opening a new window. Focused tests now lock the workspace and prompt
  editor arguments, and the runtime contract documents the extension-host
  restart boundary.
- Clarified in the Task Implementer README that `workspace init` creates no
  project documentation, while `run` creates or updates Task
  Implementer-managed `docs/requirements.md` and `docs/design.md` regions and
  rejects Agentic SDLC-owned specification schemas.
- Hardened Task Implementer prestart recovery with a coordinator-owned,
  compare-and-swap lease rearm at the exact clean assigned base. A fresh lease
  preserves the immutable assignment and existing wave/generation resources,
  stale workers cannot start with the old lease, interrupted responses recover
  by re-observing the current lease, and active, conflicting, or mutated
  prestart state fails closed.
- Hardened `project-agent-instructions` to schema v2 with owner-issued full-spec
  and traceability receipts, layered effective Codex config fingerprints,
  selected-project root markers, tracked evidence locators, structured
  deterministic rules, a 2 KiB preferred and 4 KiB hard body budget, public
  portable output independent of personal global instructions, separate exact
  ownership receipts, explicit exact-digest adoption and retirement, and
  recovery artifacts that block every transition. Legacy v1 output now fails
  closed without a compatibility shim. Owner validators are replayed rather
  than trusted by name, current file-based Codex profiles are fingerprinted,
  rendered paths and common credential forms fail closed, and retirement
  preserves concurrent replacements. Target writes are descriptor-anchored,
  and refresh/retirement backups remain durable until private transition state
  is persisted. Creation now rechecks recovery artifacts at the final anchored
  mutation boundary, dot-segment root markers are rejected, and spec, renderer,
  or evidence projection drift refreshes stale marker provenance even when the
  rendered body is unchanged. Empty root-marker lists follow Codex's
  no-parent-traversal semantics; ignored targets, untracked human instruction
  sources, control-character rule injection, incomplete requirement coverage,
  and Agentic marker/body mapping drift now fail closed. Task Implementer and
  Agentic SDLC issue receipts only for tracked specs, and Task dispatch proves
  specs plus active/ancestor project instructions belong to the exact clean
  integration contract commit. Project-file changes require a fresh Codex
  session. Added a concise skill README covering the coordinator-only flow,
  decision and ownership model, safety boundaries, source layout, and focused
  validation without promoting private helper actions into a public interface.
- Standardized side-effect-free `$skill-name --help` and `$skill-name -h`
  behavior across all repo-owned skills. Help now reports concise purpose and
  invocation policy, shows exact usage for every public action, and describes
  every public action, positional argument, and flag in one concise line while
  keeping private helper actions and flags private. Internal and
  coordinator-only skills report that they have no standalone public workflow
  action; `align-skill`, its templates, and the shared validator enforce this
  contract for created, existing, and future skills.
- Added primary-anchored `worktree integrate` preflight and exact-head guards.
  Fresh explicit integration may now delegate safe whole-repository commits for
  an ordinary dirty child and then its dirty source before candidate creation;
  durable source-scoped preparation claims serialize competing lifecycle
  owners, reviewed-tree proof detects hook-added content, and nested ownership,
  orphan candidates, relocated-source publication, lease-acquisition races,
  interrupted handoffs, active attempts, unsafe dirt, and head races fail closed.
  Successful preparatory commits are retained for retry. Documented the serial
  child-integration topology and source-only publication boundary.
- Aligned every Task Implementer and Agentic SDLC `worktree` composition
  boundary with the explicit-only outer lifecycle. Coordinators now return the
  exact integration command and stop for a fresh user invocation; the Agentic
  Stop hook refuses to auto-continue `outer-integration-pending`; publication
  skills keep managed children local; and runtime allowlists plus a canonical
  static test reject public `add`, `integrate`, or `remove` calls from private
  coordinator interop.
- Kept Task Implementer and Agentic SDLC as separate peer development domains
  over the shared Worktree lifecycle. Ordinary task leases and Agentic
  preparation now reject Task Implementer persistent lanes before state or
  resource mutation. Agentic execution hard-cuts to coordinator v7, task v4,
  and assignment v3; adds coordinator arm/watch, worker start/direct heartbeat,
  bounded prestart/read-only/stall/runtime enforcement, and coordinator-owned
  task commits; rejects indexed gitlink and tracked-symlink claims before
  resources; gives prestart scope violations fail-closed precedence; supports
  exact confirmed-stopped prestart requeue; journals task-finish intent for both
  commit/result crash windows; terminates each sequential worker process group
  on every post-spawn failure; rejects incomplete live Task lane branch
  identity; and makes verifier capability names resolve to declared regression
  tests.
- Closed two Task Implementer cross-lane claim gaps. Replanning now extends the
  active generation's Worktree-owned claims before replacement state is
  persisted while retaining earlier claims conservatively. External database,
  Kubernetes, Terraform, migration execution, and publication domains now add
  class-wide sentinel claims so distinct keys cannot bypass singleton behavior
  in separate lanes.
- Made successful `$worktree add` and exact `--reuse` hand off directly into
  the returned project directory for subsequent development commands, with a
  read-only child identity check. The skill now distinguishes command workdir
  routing from a persistent parent-shell, Codex-workspace, or editor switch;
  editor retargeting remains explicit and lifecycle actions remain anchored to
  the primary checkout. Exact reuse also fails closed when the selected project
  scope is no longer a directory.
- Made description-less `$worktree` creation derive its public-safe task slug
  from the resolved project directory basename instead of the generic `work`
  label. The helper now reports that resolved slug with created, reused, and
  inspected lifecycle output; explicit task slugs and exact reuse/duplicate
  guards remain authoritative. Existing generic-`work` lifecycles remain
  accessible only through an explicit `--task-slug work` exact reuse.
- Hardened the local-source `worktree` composition contract. Rejected `add`
  preflights no longer create or redirect private state; canonical non-symlink
  state paths are shared by manifests, reservations, leases, and locks.
  Publication guards now classify primary and linked checkouts, integration
  candidates, and Task/Agentic workers from durable ownership instead of branch
  config alone. Ownership manifests use hard-cut schema v4 to retain the exact
  lease owner, token, and `active|released` participation state. Outer leases
  use hard-cut schema v4 with an ordered promotion history, expected-head
  compare-and-set, resource-resurrection checks, terminal release receipts, and
  durable removal-intent snapshots across receipt/manifest deletion crashes.
  Task Implementer and Agentic SDLC now reconcile local interop against live
  clean Git plus durable lease truth, and Agentic promotion persists lease proof
  before local interop and coordinator `promoted` state. Deterministic gates now
  require removal-crash recovery, successive promotion history, both publication
  consumers' managed-worktree guards, canonical project-scope rejection, Git
  operation preflight, and the exact managed-child integration handoff. Agentic
  multi-feature runs now reuse the run-scoped lease at each live feature base
  and reconcile a later promotion from the exact lease-history predecessor.
  Atomic state replacement and ordered receipt, manifest, reservation, and
  removal-intent deletion now fsync their containing directories. Worker
  session claims are fully written and fsynced before atomic no-replace
  publication, eliminating empty-claim races and crash-stranded final claims.
- Reworked `worktree` into a hard-cut local-source lifecycle. `add` now requires
  the complete primary checkout to be clean on a named non-default branch and
  creates a no-upstream child from the exact local `HEAD`; `--project` is only
  a starting-directory label. `integrate` uses a durable private recovery
  worktree, exact two-parent merge proof, non-mutating combined validation, and
  ff-only source promotion. Child push/PR actions are rejected and `remove`
  now depends only on exact local unused-or-integrated proof. Task Implementer
  and Agentic SDLC use managed-local promotion, release into this outer
  integration handoff, and record exact source-integration proof before
  completion. Older worktree, interop, and coordinator schemas are unsupported.
- Added a hook-free global live-product-validation invariant and detailed
  `troubleshoot` proof contract. Each trial freezes its declared workflow, and
  later changes start a new lineage rather than laundering prior intervention.
  Codex may operate that workflow and perform authorized stabilization or
  recovery without weakening production or high-impact approval boundaries,
  but out-of-band mutations that perform, bypass, or pre-satisfy product-owned
  steps now mark the affected evidence intervened; nominally read-only actions
  are classified by their criterion-relevant effects. Product-fixed claims
  require owner-correct implemented repair, replay from a declared or
  independently proven known-good checkpoint before the earliest product
  divergence or contamination, quiescent prior writers, observed product-owned
  transitions, and independent postconditions; checkpoint replay does not
  overclaim whole-workflow success. Config Codex validates exactly one
  canonical compact managed global policy, recognizes active ATX and Setext
  policy headings while ignoring fenced examples, rejects duplicate or
  override-like conflicting sections, and adds no hook.
- Unified Git-worktree ownership across `worktree`, Task Implementer, and
  Agentic SDLC. Workflows now resolve the actual symbolic `origin` default,
  automatically create deterministic `feature/*` promotion branches when
  starting there, reuse recorded non-default/managed identities, and bind PR
  authorization to the exact promoted head plus recorded default branch and
  SHA, failing closed on later default drift. Public worktrees support explicit
  exact-name reuse without altering dirty changes. Shared promotion is ff-only
  under a common-Git-directory lock. The `--project` documentation now makes
  clear that it selects an existing repository-relative operating scope, not a
  worktree destination or branch-naming input. Worker worktrees are removed
  after combined evidence, integration worktrees after promotion, and all
  internal refs are deleted with expected-old SHAs. Upgraded Task Implementer
  coordinator state to v5 and wave state to v4, Agentic SDLC coordinator state
  to v5, and made corresponding hard schema cuts to worktree ownership, lease,
  and reservation state.
- Made the `troubleshoot` remediation budget session-configurable with optional
  `$troubleshoot --attempt-limit=N --time-limit-minutes=N` flags, 5/120
  defaults, and hard 10/180 maxima. Bare invocations preserve the profile,
  partial overrides preserve the other field, explicit 5/120 resets it, and
  active reductions at or below consumed work fail without exhausting or
  changing the tranche. A dedicated `UserPromptSubmit` hook writes a bounded,
  atomic, workspace/session/turn-bound private authorization; v4 markers bind
  exact values to it, pending active resizes preserve the ledger and counters,
  and a terminal lock prevents exhausted state from being reopened without a
  new user instruction. Marker deletion now remains fail-closed under that lock
  while still admitting an exact marker restore. Previous v3 state is
  repair-only and is not reinterpreted. Kept attempt limits, retry-admission
  rules, blocker-tranche semantics, and exhaustion reporting out of the global
  `AGENTS.md` template so the workflow skill and optional enforcement hook
  remain the canonical owners at their respective boundaries. Config Codex now
  rejects stale managed blocks that still contain either policy version.
  Task-specific earlier stops remain workflow-owned, and free-text override
  summaries record same-blocker continuation but cannot authorize numeric
  limits. The workflow still requires new evidence and a new falsifiable
  hypothesis before every retry and requires newly written state to use the
  canonical v4 schema.
  It preserves historical exhausted v1 report delivery, makes previous v2 state
  fail closed for exact marker repair instead of adding a dual-limits
  compatibility path, and keeps fallback reports bounded while covering every
  counted attempt. The skill now requires the hook-provided terminal report to
  be returned verbatim so exact marker-derived fields are not paraphrased away.
- Made partial planned remediation-attempt markers recover atomically. The
  canonical ledger now explicitly contains only post-verification records;
  planned and in-progress work remains in prose with an empty ledger. The hook
  reports every missing canonical field together and gives one remove-or-
  complete repair action instead of prompting field-by-field repair cycles.
- Split complete application scaffolding into three explicit ownership layers:
  `app-stack` now emits a closed logical-only technology handoff;
  `scaffold-project` schema v2 owns repository topology, per-path routing,
  digest-bound normalized candidate inputs, validation provenance, approval,
  and guarded apply; and `frontend-project` deterministically materializes exact
  React/TypeScript/Vite candidates inside its assigned root. The frontend
  contract now includes an entrypoint and optional route shell, names-only
  `.env.example`, typed public `VITE_*` validation, and opt-in component-local
  Oxlint and Prettier profiles. App-stack-approved frontend package-manager,
  version, profile, runtime, and capability decisions are rechecked against
  candidate inputs, and unsupported required web UI profiles fail closed.
  App-stack handoff schema v2 now carries a closed component class and
  canonical technology name; scaffold finalization rejects omitted required
  components, kind drift, application technology drift, mixed assigned-unit or
  runtime contracts, unsupported non-frontend capability selections, unknown
  frontend capability IDs, and external-service substitutions. Runtime units
  now bind explicitly to their logical capability. Frontend requests accept
  only npm, pnpm, Yarn, or Bun and reject separator-normalized public
  API-key/access-key variable names.
  Generated TypeScript includes Vite client types, while Markdown display names
  are HTML-safe before Markdown escaping. Schema-v1 scaffold bundles are
  rejected without a compatibility path.
  External scaffold and frontend JSON documents now reject duplicate object
  keys and non-standard numeric literals before structural or digest
  validation.
- Added coordinated-candidate scope to Python, Terraform, Helm, GitHub
  Actions, `.gitignore`, and shell specialists so `scaffold-project` can
  collect exact private candidates without overlapping target writes or
  repository-root ownership. `app-stack` and `design` now emit one-way bounded
  scaffold handoffs after architecture approval without calling back from the
  scaffold workflow or starting Agentic SDLC.
- Integrated `project-agent-instructions` into Task Implementer after managed
  specifications and before contract commit or worker dispatch, and into
  Agentic SDLC after design and before auto-steering or planning. Both
  workflows now checkpoint the conditional decision, invalidate it when
  specifications or inherited policy drift, preserve `workspace init` as
  private-state-only, and allow only provenance-owned generated `AGENTS.md`
  changes in their scoped contract commit. Added workflow, handoff, verifier,
  steering, alignment, documentation, and source-installed parity coverage.
- Added durable nested-project conflict and precedence rules to the global
  Codex instruction template and live global instructions: read every
  applicable ancestor/project instruction file, keep higher-level safeguards,
  stop on irreconcilable conflicts, and explicitly reload newly generated
  project instructions in the current session.
- Redesigned the Agentic SDLC workflow image as a taller, readable task-to-skill
  map with substantially larger typography, an explicit `sdlc-start`
  coordinator, all 19 golden-path task owners, clearer conditional failure
  routing, and readable stop-budget and private-evidence text. Arrow paths now
  meet the base of each arrowhead and every arrowhead tip touches its
  destination boundary. The design document now embeds the canonical SVG
  directly, while the generated PNG remains synchronized as a fallback.
- Changed `code-review` so a direct standalone `$code-review` invocation
  completes the initial review, fixes only safe in-scope findings, validates
  each fix with focused red-before/green-after repository-native proof, reviews
  its touched diff, and returns the complete prioritized fixed and gated
  ledger. Already-green or unrelated checks cannot authorize remediation. The
  skill itself no longer resolves, loads, or invokes `align`; a caller or outer
  orchestrator remains responsible for any separate repository policy
  requiring alignment. Implicit, nested, and explicit no-write requests such
  as review-only, audit-only, or report-only remain non-mutating, including
  exact restoration of their initial worktree state. All modes use no-write or
  no-cache validation settings where available and clean exact task-created
  artifacts. P0-P3 priority remains independent of remediation safety. `align`
  keeps its child `code-review` lane report-only and retains ownership of
  cross-surface remediation, while its final gate also removes exact
  task-created validation residue.
- Added an evidence-guided Agentic SDLC repair loop with immutable
  `failure-event-v1` and `diagnosis-v1` records, stable blocker identity,
  deterministic classification, atomic `repair-control-v1`, an append-only
  repair journal, and bounded blocker/feature dispatch budgets. Proven
  mechanical causes now bypass diagnosis; ambiguous or persistent failures use
  `troubleshoot` as a conditional, no-committed-fix diagnostic branch that
  returns through classification. Inconclusive evidence stops instead of
  becoming speculative redesign, while positive system-contract proof and
  durable approval gate broader design changes. Post-wave implementation
  repairs use immutable corrective plan vN+1, preserve full task definitions
  and digests, append diagnosis/oracle-bound waves, require digest-protected
  oracle proof in `worker-result-v4`, and rerun the original
  evaluator oracle plus invalidated gates at the new integration commit. A
  process-locked revalidation cursor prevents successful repairs from reaching
  UAT or publication until immutable commit-bound evidence clears every
  invalidated gate. Pre-commit evidence follows the live integration HEAD;
  final commit evidence is accepted only after promotion, a clean matching
  project HEAD on the recorded base branch, and verified integration worktree
  and branch cleanup.
  Added deterministic classifier, evaluator, corrective-plan, execution,
  worker, Stop-hook, troubleshooting, and workflow-verifier coverage and
  regenerated the workflow diagram around the bounded repair and design loops.
- Replaced the dense text-only Agentic SDLC workflow diagram with a colorful,
  accessible architecture image while retaining the precise searchable phase
  order in the design document.
- Bound every canonical remediation attempt to its marker's exact blocker key.
  The optional `troubleshoot` guard now rejects missing, mixed, or carried
  attempt identities as invalid marker state, so three repairs against one issue
  cannot exhaust a causally independent issue. Added adversarial PreToolUse and
  Stop regressions plus aligned skill, hook, eval, metadata, and catalog
  contracts; semantic blocker classification remains parent-owned.
- Aligned the Agentic SDLC PR handoff around one immutable promoted SHA.
  Active-run `create-pr` is now publication-only after passing UAT and lease
  release, while `review-pr` is findings-and-readiness-only; any branch-changing
  fix returns through failure classification and the coordinator so affected
  validation, documentation, alignment, commit, UAT, and publication gates
  rerun. `sdlc-merge-pr` now accepts only an explicit, exact-command merge while
  the remote head still equals the promoted and reviewed SHA. Updated the
  design, state schema, failure taxonomy, skill metadata, catalog, and verifier
  contracts to match the coordinator-v4 workflow, lifecycle `--resume`,
  Computer Use GUI routing, session claims, and bounded observability
  evaluation. The verifier now treats `nebius-grafana-query` as an installed
  runtime dependency and includes its source digest in parity and verification
  identity checks.
- Integrated `nebius-grafana-query` as a narrowly gated structured evidence
  provider for `troubleshoot` and `sdlc-evaluate`. The consumers now decide
  relevance, authority, deployed scope, interpretation, and grading; the
  provider performs one lazy readiness/discovery check per workflow run, skips
  the path without repair or retry when unavailable, applies six-query fast
  and four-query deep limits within one cumulative ten-query budget, round-trips
  connectivity plus total and per-stage remaining budgets, rejects invalid
  requests before tool use, redacts untrusted telemetry, and returns a fixed
  facts envelope with data gaps and query cost without claiming root cause or
  acceptance. Evaluation deep queries are limited to named attribution,
  coverage, or dependency interpretations of predefined gates, and canonical
  rejection reasons map deterministically to the SDLC failure taxonomy.
  Troubleshooting retains causal proof and maps decisive missing telemetry to
  `BLOCKED_MISSING_EVIDENCE`; SDLC evaluation limits live observability to
  predefined operational gates, keeps production workloads prohibited, and
  records pass, fail, or inconclusive criteria. Embedded troubleshooting now
  requires a decision-changing question plus non-Grafana provenance for one
  matching signal before readiness, passes that admission as a provider-checked
  structured signal fit, starts with one cheapest matching query per provider
  invocation, treats query allowances as ceilings rather than targets, and
  forbids speculative datasource discovery or telemetry-family fan-out. SDLC
  evaluation now applies the same zero-call signal-fit gate plus a
  provider-checked criterion fit containing the exact measurement,
  candidate/control attribution, coverage, and pass/fail/inconclusive rules;
  it also admits only one grade-changing query per provider invocation instead
  of batching generic telemetry families.
- Hardened `nebius-grafana-query` with one pinned absolute query window,
  explicit `any` versus broad-scope semantics, evidence-gated federation,
  cardinality preflight, exact decomposition of recomposable backend failures,
  and ranked signal-aware reports. Reports now sort problem-first on the
  requested field, display a bounded top 20 with coverage disclosure, use
  per-group minimum/average/maximum only where valid, derive counter
  increase/rate instead of averaging raw counters, and separate access
  evidence from query and tool health.
- Made the config-owned default Nebius CLI profile's configured project the
  automatic runtime authority when no explicit task selector exists. The auth
  hook resolves it with sanitized `nebius profile current` plus
  `nebius config get parent-id`, while explicit task selectors keep precedence
  and raw-token helper children remain explicit-selector-only. Added
  `nebius/references/project-selection.md` so other Nebius implementations can
  reuse the same authority, validation, and fail-closed lookup contract without
  parsing the CLI configuration YAML directly.
- Refactored `install-grafana-mcp-for-nebius` to pin and revalidate one human
  Nebius CLI profile, isolate private token state by server/profile, sanitize
  agent and competing Grafana credentials, rotate token-file auth with bounded
  failure handling and stale-lock recovery, require current token-file support,
  pre-mint and reuse a fresh startup token, renew stale startup state before MCP
  launch with foreground browser authentication when necessary, enforce a
  bounded 300-second Codex startup timeout, compare exact structured Codex MCP
  values while digest-binding and atomically changing only that timeout, use
  separate 90-second background and 210-second foreground refresh-lock budgets,
  validate the complete trusted wrapper path, and enforce read-only MCP
  arguments. Expanded
  `nebius-grafana-query` with
  fail-closed one-or-many tenant/project/resource scoping, attributed
  multi-datasource fan-out and aggregation provenance, bounded
  metrics/logs/traces, proxied Tempo tools, and strict GET-only
  datasource-proxy fallbacks.
- Hardened `troubleshoot` retry admission so each blocker tranche has a
  non-overridable three-attempt maximum, every retry requires newly acquired
  evidence and a genuinely new evidence-derived hypothesis, repeated or
  evidence-free retries fail closed, and all repeated-failure stops return a
  structured investigation report before further work.
- Routed system-contract-changing `troubleshoot` remediations through `design`
  after causal proof and before implementation, while keeping even difficult
  repairs inside one existing private boundary in `troubleshoot`, preserving
  its verification and reporting ownership, and sending active Agentic SDLC
  failures through classification and coordinator routing first.
- Made `config-codex`'s create-only `config.toml` template an explicit
  public-safe recovery baseline derived through a documented allowlist rather
  than a live-config copy. Recovery now preserves portable Sol/xhigh/Fast,
  agent, task-state, and public MCP settings while excluding personal project
  lists, absolute paths, secret-bearing values, private/plugin-managed
  integrations, desktop/generated state, notification commands, and
  undocumented keys; executable MCP dependencies are version-pinned, missing
  configs are created through an atomic no-clobber `0600` renderer, and
  existing laptop configs remain patch-only.
- Narrowed `install-grafana-mcp-for-nebius` to explicit installation,
  configuration, authentication repair, external-Grafana setup, and
  datasource-list readiness validation. Routine datasource, dashboard,
  PromQL, LogQL, and trace-tool questions now hand off to the read-only
  `nebius-grafana-query` skill without a legacy query path in the installer.
- Aligned the public Codex config template, local preflight, global-context
  setup guidance, fixture validation, and installed skill copies on
  `agents.max_concurrent_threads_per_session = 16`, removing the legacy
  `agents.max_threads` alias and undocumented `agents.max_depth` setting from
  the reusable baseline.
- Required standalone custom-agent files to self-identify with aligned,
  non-empty `name`, `description`, and `developer_instructions` metadata while
  remaining read-only, with redacted regular non-symlink target validation plus
  source, mirror, fixture, and preflight coverage.
- Updated the public Codex config baseline to use `gpt-5.6-sol`, `xhigh`
  reasoning for normal and Plan modes, and the Fast service tier by default.
- Refactored the read-only `code-info` skill to accept explicit project-folder
  paths and report concise project descriptions, documented features, CLI
  command hierarchies through three levels, separated code/test/docs/config LOC,
  project packages, direct and statically selected/resolved dependencies, and
  approximate pinned Redis/SQLite size comparisons. Added temporary-fixture
  unit coverage and kept project code, manifests, package managers, builds,
  and tests unexecuted during inspection.

### Fixed (earlier entries)

- Fixed Task Implementer's run-owned project-contract lifecycle adapter after a
  sealed promotion-review correction advances the retained integration checkout.
  The adapter now binds the initial prepared wave to its original base and an
  active correction to its exact recorded contract commit, admitting an adopted
  contract head only when the owner journal remains authoritative. It retains
  the existing branch, registered linked-worktree, common-Git-directory, clean
  or exact staged-spec-pair, selected-project, helper, command-digest, and
  private-path checks. A real-Git
  regression now crosses sealed contract adoption and the hidden lifecycle
  authorization instead of relying only on the mocked dispatch gate, and
  rejects unrelated descendants, forward or stale journal identity,
  original-base substitution, and an independent repository substituted at
  the retained integration path.
- Fixed retained Task Implementer runs that were blocked when a sealed project-
  contract reconciliation left only canonical requirements/design and a
  provenance-only `AGENTS.md` dirty in the persistent lane. A hidden current-v7
  owner transition now binds the sealed lifecycle, instruction-state, impact,
  and file digests; journals one coordinator-owned integration commit; and
  admits the overlay only while lane bytes, integration ancestry, and the lease
  remain exact. Promotion journals temporary lane cleanup and restores the
  overlay after interruption or failure. Promotion-review correction graphs now
  advance one dependency-ready frontier at a time, and a resource-free future
  task may change only its dependencies when its correction target is indexed.
  Arbitrary, staged, untracked, partial, symlinked, or unsealed dirt remains
  blocked.
- Fixed Task Implementer correction replanning after completed waves. The
  coordinator now hashes the final combined indexed wave plan instead of only
  the replacement tail. A private current-v7 owner recovery accepts only the
  proven historical tail-digest signature, exact caller-bound old and combined
  digests, a done retained prefix, consistent deterministic replacement state,
  and no live replacement worker; it journals a plan-basis-first repair and
  never changes task, Git, Worktree, lease, promotion, or public workflow
  state. Focused real-Git coverage reproduces the resume blocker, rejects live
  worker repair, exercises interruption replay, refreshes and binds a validated
  spec-receipt-only `retain_plan` impact, rejects stale refinement and material
  impact, and reaches normal resume planning after recovery. Repeating an
  unchanged retained-prefix replan is now byte-idempotent and never blocks its
  active replacement wave.
- Fixed a long-running Task Implementer/project-contract deadlock at the Stop
  boundary. The initial project lifecycle Stop from `implementation-open` now
  atomically enters `reconciliation-required`, preserves the implementation
  write epoch, clears stale planning bindings, and admits canonical spec
  reconciliation before generating its continuation. It no longer assumes a
  Stop-generated synthetic turn will emit `UserPromptSubmit`; concurrent
  material-write accounting retries safely and recursive Stop remains
  fail-closed.
- Fixed Task Implementer cross-wave contract refresh while the outer project
  lifecycle remains implementation-open. The attested run-owned bridge now
  admits only exact read-only inspect/render at that phase as well as during
  reconciliation; terminal apply/verify and all existing run, checkout, path,
  command-digest, helper, and clean-worktree constraints remain unchanged.
- Fixed Task Implementer prompt-session acceptance from a verified managed
  worktree lane. The prompt validator now admits either the canonical primary
  project or the manifest-proven lane project for the same scope while retaining
  strict prompt-root, source-root, and unrelated-project rejection checks.
- Fixed prompt-session exactly-once and lifecycle arbitration gaps: new
  objectives now publish their final marker-bearing bytes in one exclusive
  create, reserved operation-marker input is rejected, prompt results are bound
  to the exact event project workspace (including Task primary-to-lane scope
  identity), interrupted prompt-v3 pointer migration
  accepts already-repaired queue state, and the shared Stop arbiter stays within
  one 25-second monotonic budget. Prompt-v2 migration now changes frontmatter
  only, preserving body lines that resemble metadata.
- Guarded active-run `gh pr create` and GitHub PR-creation MCP calls with
  branch-, phase-, exact-HEAD-, UAT-, and expiry-bound authorization while
  allowing read-only PR inspection without publication authorization. Guarded
  pushes now allow only `origin` plus one exact current-branch refspec, and CLI
  or MCP PR creation rejects another head. Added regression coverage for exact
  authorized pushes, stale heads, missing expiry/UAT, extra refs, CLI PR
  creation, MCP read-versus-create classification, and explicit
  `--match-head-commit` merge authorization. The guarded merge command now
  requires one explicit PR number or URL and one canonical single-action shape;
  implicit PR selection, admin bypass, branch deletion, repository override,
  unsupported flags, shell operators, appended actions, PR-scope mismatch, and
  non-passing readiness evidence fail closed. Active Agentic SDLC GitHub merge
  MCP writes now fail closed in favor of the race-safe CLI path. Sensitive
  publication and merge actions must also be direct: wrappers, absolute
  executable paths, prepended commands, nested shells, operators, and
  redirections are denied, while quoted documentation searches remain allowed.
  CLI PR creation now requires an explicit matching `--head`.
- Made repeated `install-grafana-mcp-for-nebius` checks and applies explicitly
  no-change on canonical state, including avoiding redundant directory-mode
  writes, preserving private state and Codex registration bytes and metadata,
  and refusing a mismatched pinned registration without an explicit migration
  flag.
  Documented and tested the Nebius-auth hook boundary so denied parent commands
  are correctly treated as command-shape enforcement rather than setup failure.
- Fixed `grafana-nebius` startup closing before `initialize` when its private
  startup token was stale. The registered wrapper now runs the pinned,
  identity-checked `nebius iam get-access-token --profile` flow before
  launching `mcp-grafana`; only foreground startup may open browser
  authentication, background refresh remains noninteractive, and the canonical
  MCP registration provides the bounded startup time needed for that flow.
- Fixed the Nebius auth selector-discovery loop where even
  `nebius profile current` was denied for lacking the project selector it was
  intended to help discover. Default-profile lookup now clears ambient auth,
  validates bounded single-value output, and fails closed when the profile or
  configured `parent-id` is absent or malformed.
- Fixed `install-grafana-mcp-for-nebius` token-file capability detection on
  macOS binaries where `strings` embeds
  `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` inside a larger record, preventing a
  false unsupported-build result for compatible official releases. The runtime
  wrapper now also preserves the client's stdin stream when it backgrounds the
  stdio server instead of allowing non-interactive Bash to replace it with
  `/dev/null`. Scheduled token rotation now closes stdio so the next process
  loads the fresh credential, returns an explicit restart-required failure,
  and does not assume same-chat recovery, working around the upstream
  static-client behavior through `mcp-grafana` `v0.17.2`; unexpected
  refresh-worker exits also stop MCP with failure. Parent-side supervision now
  keeps the child unreaped, uses a process-start-bound STOP/KILL handshake
  before confirming restart-required state, and preserves natural exit status
  at the refresh boundary. Canonical registration post-write verification now
  includes the exact wrapper command, replacement rejects concurrent changes
  before removal, requires a typed simple stdio shape, and never replays prior
  command, argument, or environment values through process arguments. A failed
  canonical add now reports the recoverable missing-entry state without legacy
  rollback. Post-write drift is left intact for inspection instead of being
  deleted without ownership proof. Stopped-child supervision now KILLs the
  unreaped direct child on every post-STOP inspection failure, timeout, and
  parent-interrupt cleanup path so no suspended process can deadlock `wait`.
- Hardened the `global-context-management` local template validator by parsing
  Python templates without dynamic code compilation and by rejecting a
  non-canonical SessionStart command before running its smoke test without a
  shell.
- Fixed `troubleshoot` Stop-hook handling for historical exhausted markers and
  incomplete reports. Attempt labels now derive from ledger order, only
  exhausted v1 data markers receive a report-only compatibility path, and
  canonical v2 markers require new evidence in every state. Report validation
  now requires substantive per-attempt remediation, verification, result, and
  evidence fields bound to the guard's bounded redacted marker summaries and
  rejects sensitive assistant-authored reports. The single correction prompt
  contains a bounded redacted minimum report; if ignored, Stop terminates with
  that fallback in a UI/event-stream warning instead of recursing indefinitely.
  Fallback attempt fields escape report delimiters so the generated report
  validates itself, and a user-lowered attempt limit now also bounds the ledger
  length.
  Fallback redaction covers secrets, private or credential-bearing URLs,
  private IPv4/IPv6 addresses, internal hostnames and localhost, cloud
  access-key shapes, and Unix or Windows personal paths while retaining public
  vendor links and artifact identities as admissible evidence.
- Made remediation-marker result failures actionable by naming the two
  canonical values and distinguishing counted same-blocker failures from
  unverified progress and causally independent blocker transitions, preventing
  repeated repair attempts that only guess unsupported enum spellings.
- Reset remediation budgets per causally independent blocker instead of
  carrying earlier attempts, active time, tranche, exhaustion state, or stop
  trigger forward. The optional guard now rejects contradictory lifecycle
  fields, reports a bounded public-safe marker-validation reason without
  reflecting marker content or filesystem exception details, and treats exact
  marker repair as non-counting coordination work rather than forcing a false
  exhaustion report.
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

### Added (earlier entries)

- Added the implicitly invokable `optimize-pytest` skill for safe pytest
  performance measurement, cumulative fixture and collection-cost review,
  focused optimization, like-for-like validation, and evidence-gated
  parallelism, affected-test selection, CI sharding, and performance
  governance.
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

### Changed (earlier entries)

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
  prompt-bound `run <prompt-ref-or-file>`. Private managed prompts,
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
  `run <prompt-ref-or-file>` now builds deterministic dependency
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
  `run <prompt-ref-or-file>`. Initialization creates a starter only
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
