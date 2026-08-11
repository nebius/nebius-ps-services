<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v1 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Publish portable, discoverable engineering skills

#### User Story

Codex users and repository maintainers need each workflow to remain a public,
reusable skill with deterministic discovery, bounded instructions, and
reviewable supporting assets.

#### Acceptance Criteria

- AC-001: Every installable skill is rooted at a directory containing
  `SKILL.md`, with optional scripts, references, assets, evaluations, and agent
  metadata kept within that skill boundary.
- AC-002: The repository installer places user skills in the canonical
  `${HOME}/.agents/skills` discovery root unless the caller explicitly selects
  another destination.
- AC-003: Skill instructions, examples, and generated public artifacts contain
  no secrets, private endpoints, customer data, or environment-specific
  credentials.

#### Negative Criteria

- NC-001: Runtime hooks must not require a duplicate user-skill installation
  under `${CODEX_HOME}/skills`.
- NC-002: Installation must not overwrite unmanaged or differently owned skill
  directories.

#### Validation Method

Validate the source catalog, installer defaults, skill metadata, and installed
layout against repository checks and current official Codex skill-location
guidance.

#### Test Method

Run the installer and skill contract tests with disposable destination roots,
including idempotency and ownership-conflict cases.

#### Evaluation Method

Review source-to-install parity and confirm a fresh Codex runtime discovers the
installed skill from the canonical user root.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=active priority=P0 type=constraint -->
### REQ-002: Enforce one fail-closed project contract lifecycle

#### User Story

Project contributors need canonical requirements and design to be established,
validated, and reconciled before implementation can be represented as complete.

#### Acceptance Criteria

- AC-001: Managed projects use `docs/requirements.md` and `docs/design.md` as
  the canonical requirement and design pair, with stable record identifiers and
  one owner-issued receipt.
- AC-002: Lifecycle state progresses through planning, implementation,
  reconciliation, project-instruction verification, and sealing without hooks
  semantically authoring specifications.
- AC-003: Exact project scope, receipt digests, write epochs, conditional
  project rules, and reload requirements fail closed on stale or ambiguous
  evidence.
- AC-004: Successful material project writes are recorded without routine
  per-write reconciliation context, and one terminal Stop request directs the
  agent to review the accumulated selected-project implementation delta.
- AC-005: Final reconciliation updates requirements only for accepted intent
  changes and design only for implemented boundary or evidence changes;
  implementation-only or reverted changes may preserve both documents
  byte-for-byte after explicit validation at the latest write epoch.
- AC-006: Lifecycle prompts and completion reports distinguish the required
  project-instructions decision from repository file creation: `not-needed` is
  a verified successful outcome and leaves a missing project `AGENTS.md`
  absent.
- AC-007: A clear statement that existing users depend on a project and future
  code or interface changes must not break them is explicit compatibility
  intent even when it does not use `GA`, `backward compatibility`, or another
  prescribed keyword. The semantic owner captures that intent in the canonical
  specs before implementation and makes it available to the current turn.
- AC-008: Explicit project compatibility intent protects supported observable
  behavior and public interfaces, including APIs and public import paths, CLI
  commands, flags, output and exit behavior, configuration schemas and
  defaults, persisted formats, and upgrade paths. Breaking one of those
  surfaces requires explicit approval, a deprecation or migration plan, and
  regression coverage; private internals retain one canonical path.
- AC-009: Conditional project-instruction discovery keeps the exact selected
  project as the rule target while resolving the nearest matching configured
  project-root marker from that directory through the enclosing Git root as
  the instruction-chain root. An empty marker list retains selected-directory
  discovery semantics.

#### Negative Criteria

- NC-001: A missing coordinator, stale receipt, or incomplete spec pair must not
  be bypassed through hand-written lifecycle state or an independently
  authoritative receipt.
- NC-002: Project `AGENTS.md` must not be mutated before final reconciliation
  and the terminal seal transition.
- NC-003: Suppressing routine hook context must not remove post-success write
  accounting, hide recording failures, or permit sealing against a stale
  implementation epoch.
- NC-004: A hook must not imply that reconciliation always creates or changes
  project `AGENTS.md`.
- NC-005: Personal global compatibility defaults must not be copied into
  portable project instructions or suppress an explicit project compatibility
  rule; an active same-directory project instruction may satisfy the rule only
  when it already expresses the required contract.
- NC-006: Hooks must not infer compatibility semantics, and nested selected
  projects must not be forced to contain a duplicate local root marker when an
  effective marker exists at an ancestor within the enclosing Git worktree.

#### Validation Method

Run owner validation, lifecycle transition tests, hook contract tests, and
project-instruction discovery/render/apply/verify tests.

#### Test Method

Exercise every lifecycle phase, rejected transition, stale-evidence path,
missing-spec path, bounded waiver, repeated successful writes, terminal
reconciliation, unchanged-spec seal, and missing-target `not-needed` outcome
using disposable repositories and private state roots. Cover paraphrased
user-dependence intent, global-policy isolation, missing and human-owned
targets, same-directory overrides, nearest-ancestor root discovery, and empty
root-marker configuration.

#### Evaluation Method

Confirm repeated implementation writes remain silent while advancing the write
epoch, one terminal reconciliation classifies the final delta, an explicit
project-instructions outcome is reported without promising file creation, and
the selected project reaches `sealed` with a current receipt or an explicit
bounded waiver. Confirm that explicit no-break intent is injected for the
current implementation and persists at terminal seal when project rules are
needed, without requiring magic words or weakening higher-level safeguards.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=active priority=P0 type=constraint -->
### REQ-003: Trust only canonical installed lifecycle coordinators

#### User Story

The project-contract hook needs to execute reviewed coordinator scripts from
the same user-skill root that Codex and the repository installer use.

#### Acceptance Criteria

- AC-001: An installed lifecycle hook recognizes the reviewed
  `maintain-project-specs` and `project-agent-instructions` coordinators under
  `${HOME}/.agents/skills`.
- AC-002: Source-bundle tests may bind an explicit checkout-relative skill root
  without making arbitrary repository scripts trusted by an installed hook.
- AC-003: Coordinator recognition binds the exact script, action, selected
  project, interpreter, and required evidence arguments before execution.

#### Negative Criteria

- NC-001: An arbitrary script with a matching basename or subcommand must not
  be treated as a lifecycle coordinator.
- NC-002: The installed hook must not derive a checkout trust root from its
  copied `${CODEX_HOME}/hooks` location.

#### Validation Method

Compare installed-hook and source-hook behavior using isolated homes and exact
coordinator paths.

#### Test Method

Add regression tests covering installed `${HOME}/.agents/skills`
coordinators, source-bundle coordinators, missing coordinators, symlink and
path-spoof rejection, and both coordinator owners.

#### Evaluation Method

Reproduce the former `planning-required` deadlock against the faulty hook and
show that the repaired hook admits only the canonical coordinator command.

<!-- /REQUIREMENT: REQ-003 -->

<!-- REQUIREMENT: REQ-004 status=active priority=P0 type=constraint -->
### REQ-004: Restrict project lifecycle authority to project-owned state

#### User Story

Codex users need a selected-project lifecycle guard to protect only the project
contract it owns, without turning that hook into a laptop-wide write policy.

#### Acceptance Criteria

- AC-001: The project-contract guard protects selected-project mutations and
  the authoritative `${CODEX_HOME}/project-specs` state owned by its receipt,
  decision, render, ownership, and lifecycle coordinators.
- AC-002: Fixed, provable writes outside the selected project and outside that
  lifecycle-owned private root pass through this hook, including Codex config,
  hook, task-state, installed-skill, and other user-file targets.
- AC-003: External pass-through does not grant authority, bypass another hook,
  or change lifecycle phase, write epoch, or selected-project mutation scope;
  the operating system, Codex permissions, destructive-action safeguards, and
  owning domain policies still decide whether the operation proceeds.

#### Negative Criteria

- NC-001: External pass-through must not permit lifecycle receipt or state
  forgery, mixed selected-project and external mutation, dynamic target
  evasion, symlink escape, or hard-link aliasing into protected state.
- NC-002: The project lifecycle hook must not deny an otherwise authorized
  external write merely because the target is under `${CODEX_HOME}` or is
  configuration, hook, task-state, installed-skill, or coordinator material.

#### Validation Method

Validate selected-project containment, lifecycle-private containment, resolved
external targets, and unchanged lifecycle state before and after pass-through.

#### Test Method

Cover config, hooks, task state, installed skills, unrelated user files,
selected-project targets, lifecycle-owned private state, mixed targets, dynamic
targets, symlink escapes, hard links, and every non-terminal lifecycle phase.

#### Evaluation Method

Confirm authorized external writes are no longer denied by this hook while
selected-project sequencing and authoritative lifecycle state remain protected.

<!-- /REQUIREMENT: REQ-004 -->

<!-- REQUIREMENT: REQ-005 status=active priority=P1 type=constraint -->
### REQ-005: Separate source, installation, and runtime activation proof

#### User Story

Maintainers need accurate evidence about whether a lifecycle repair exists in
source, is installed locally, and is active in a fresh Codex runtime.

#### Acceptance Criteria

- AC-001: Source tests, installed byte parity, hook registration and trust, and
  fresh-session behavior are reported as separate verification gates.
- AC-002: Hook installation preserves recoverable backups and reports when a
  restart and `/hooks` review are required.
- AC-003: Temporary recovery artifacts are removed only after the repaired
  runtime has independently demonstrated the canonical coordinator path.

#### Negative Criteria

- NC-001: Passing source tests or byte equality alone must not be reported as
  fresh-runtime activation.
- NC-002: A healthy final filesystem state after manual intervention must not be
  treated as proof that the original product-owned transition succeeded.

#### Validation Method

Record each source, install, registration, restart, trust, and fresh-session
gate independently.

#### Test Method

Run focused source tests, installer idempotency and parity checks, hook syntax
checks, and a clean post-restart lifecycle probe in the selected project.

#### Evaluation Method

Classify completion only from the strongest completed gate and report any
remaining restart or fresh-session requirement explicitly.

<!-- /REQUIREMENT: REQ-005 -->

<!-- REQUIREMENT: REQ-006 status=active priority=P0 type=constraint -->
### REQ-006: Keep lifecycle enforcement proportional to material effects

#### User Story

Codex agents need to inspect, diagnose, test, and maintain workflow-private
inputs without a project lifecycle hook turning into a general command
allowlist or filesystem sandbox.

#### Acceptance Criteria

- AC-001: Ordinary read-only shell commands, pipelines, repository inspection,
  and read-only connector or computer-use tools remain available in every
  lifecycle phase unless their declared operation has a material side effect.
- AC-002: The guard classifies and constrains known project mutations by their
  actual targets while leaving non-project security, permissions, and sandbox
  enforcement to their owning layers.
- AC-003: The exact current-session runtime declaration and semantic
  project-instruction decision may be authored in the caller-owned private
  workflow directory, while receipts, lifecycle state, render state, ownership
  state, and final state remain coordinator-owned.
- AC-004: Read-only activity and admitted private authoring do not advance the
  project write epoch or satisfy any lifecycle transition.
- AC-005: The lifecycle owner can atomically persist a mode-0600 validation
  receipt at the exact current-session private path without shell redirection,
  and current-turn lifecycle transitions can use a hook-issued opaque turn
  token.
- AC-006: First-time canonical specs can be marked intent-to-add through one
  exact selected-project Git transition, and caller-authored runtime or
  decision inputs can be normalized to mode 0600 through one exact
  current-session permission transition.
- AC-007: A command with fixed, provable effects wholly outside the selected
  project passes through the project-contract hook and does not advance its
  lifecycle write epoch, while remaining subject to the operating system,
  Codex permissions, destructive-action safeguards, and other domain hooks.

#### Negative Criteria

- NC-001: The lifecycle hook must not deny an otherwise authorized command
  solely because its executable is user-owned, absent from a fixed allowlist,
  or combined with other read-only commands.
- NC-002: Flexibility must not permit direct edits to canonical receipts,
  lifecycle state, generated project instructions, or project implementation
  before the required phase.
- NC-003: A stale session, incorrect turn token, symlinked coordinator, or
  receipt target outside the canonical private root must remain denied.
- NC-004: Bootstrap admission must not permit content staging, arbitrary Git
  index mutation, broader chmod modes or targets, cross-session permission
  changes, or edits to coordinator-owned private state.
- NC-005: Out-of-project pass-through must not admit direct lifecycle-state
  forgery, mixed selected-project and external mutation, dynamic or ambiguous
  targets, or any command that another security or domain policy denies.

#### Validation Method

Exercise the hook against representative read-only, known-mutating, private
workflow, cross-session, and ambiguous-target operations in every lifecycle
phase.

#### Test Method

Cover `cat`, `find`, `grep`, `nl`, read-only pipelines, quoted metacharacter
patterns, ordinary Git reads, exact canonical intent-to-add, read-only MCP and
computer-use calls, explicit redirects, mutating Git and shell commands, exact
private decision inputs and mode normalization, mixed targets, and symlink
escapes. Exercise fixed external targets and working directories separately
from mixed or ambiguous project-affecting commands.

#### Evaluation Method

Re-run the original blocked diagnosis and lifecycle bootstrap without a
symlink-only installation model, then confirm that real implementation writes
still require a validated plan and open transition.

<!-- /REQUIREMENT: REQ-006 -->

<!-- REQUIREMENT: REQ-007 status=active priority=P0 type=constraint -->
### REQ-007: Repair repo-owned Codex guard defects without evasion

#### User Story

Codex users need `config-codex` to distinguish an external policy denial from
a defective repo-owned guard so authorized configuration work can be repaired
at its causal owner and retried.

#### Acceptance Criteria

- AC-001: A blocked config operation identifies the denying hook, its active
  registration, canonical source owner, installed provenance, and documented
  ownership boundary before proposing repair.
- AC-002: When the current request authorizes the repo-owned repair, the
  workflow reproduces the false denial in a focused test, changes canonical
  source first, validates it, synchronizes through the documented installer,
  reports restart/trust boundaries, and retries the identical authorized edit.
- AC-003: When the denial is owned by the operating system, sandbox,
  enterprise policy, an external or unknown-provenance guard, or a source
  outside current authority, the workflow reports the exact blocker and the
  smallest out-of-band action instead of attempting evasion.

#### Negative Criteria

- NC-001: Repair must not change writers, use shell redirection, edit only an
  installed hook, disable or unregister the guard, change working directory to
  escape classification, or mutate the original target before owner repair.
- NC-002: A source repair must not weaken lifecycle-private state protection,
  selected-project phase gates, destructive-action safeguards, or an external
  security policy.

#### Validation Method

Trace source, installation, registration, and active-runtime boundaries and
verify the repaired rule is owner-correct rather than an alternate write path.

#### Test Method

Add cross-surface skill contract tests plus an end-to-end false-block case that
fails before source repair and admits the same operation after reviewed install.

#### Evaluation Method

Re-run the originally denied config patch unchanged and independently confirm
that protected project lifecycle state remains denied.

<!-- /REQUIREMENT: REQ-007 -->

<!-- REQUIREMENT: REQ-008 status=active priority=P0 type=constraint -->
### REQ-008: End every troubleshoot invocation with a structured report

#### User Story

Users invoking `$troubleshoot` need a durable terminal report whether the work
succeeds, is blocked, encounters a tool or coordination error, exhausts its
budget, or remains unresolved.

#### Acceptance Criteria

- AC-001: Every explicit `$troubleshoot` turn establishes a turn-scoped report
  obligation even when no remediation-budget marker is created.
- AC-002: Stop accepts only a substantive report containing outcome, failure
  contract, architecture verdict, component matrix, incident timeline, logs,
  hypotheses, code debugging, cause, remediation, post-fix validation,
  completion gate, and remaining uncertainty, with one supported terminal
  classification.
- AC-003: Incomplete final text receives a bounded corrective continuation;
  exhaustion retains marker-derived attempt and blocker evidence inside the
  same canonical report envelope, and an undelivered obligation survives into
  the next available agent turn.
- AC-004: The workflow documents that a host or process termination before any
  Stop event cannot be converted into an assistant response by a local hook;
  the next resumed turn must report the interrupted state before completion.
- AC-005: The completion gate records `PASS`, `FAIL`, or `UNKNOWN`, with
  evidence and gaps, for design, infrastructure, connectivity, configuration,
  runtime health, logs, and relevant code paths; `VERIFIED_FIXED` requires all
  seven criteria to be `PASS`. A `PASS` row requires its canonical evidence
  reference and exact no-gap sentinel, cross-validated against structured
  architecture, component, log-coverage, code-debugging, and post-fix proof
  state. Referenced detail that explicitly reports missing, unavailable,
  unexamined, unverified, or otherwise insufficient evidence invalidates the
  pass even when a structured status token is present.
- AC-006: The shared Stop arbiter keeps a peer continuation authoritative, but
  finalizes an already validated report before returning a later terminal peer
  result so the delivered report obligation cannot leak into another turn.

#### Negative Criteria

- NC-001: Success, ordinary blocking, tool failure, non-exhausted early stop,
  or absence of a remediation marker must not silently bypass report checking.
- NC-002: Report enforcement must not loop indefinitely, fabricate evidence,
  expose secrets, or replace a required report with only a hook system message.
- NC-003: A result of "no issue found" must not hide unavailable components,
  missing log intervals, unexamined code paths, or another coverage gap.

#### Validation Method

Validate explicit-invocation tracking, the canonical report schema, completion
matrix parsing, bounded corrections, exhaustion composition, interruption
carry-forward, and Stop-arbiter behavior.

#### Test Method

Cover success, blocked, tool error, ordinary early stop, unresolved work,
budget exhaustion, missing or duplicate criteria, invalid verdicts, incomplete
or contradictory success claims, bounded correction, resumed interruption, and
composition with continuing and terminal project-contract and SDLC Stop
delegates.

#### Evaluation Method

Invoke `$troubleshoot` with and without a remediation marker and verify every
reachable terminal path produces the documented user-visible report.

<!-- /REQUIREMENT: REQ-008 -->

<!-- REQUIREMENT: REQ-009 status=active priority=P0 type=feature -->
### REQ-009: Capture and route bound-session prompt intent safely

#### User Story

Task Implementer and Agentic SDLC users need direct prompts in an explicitly
bound Codex session to refine the active objective and continue its workflow
without repeatedly locating or hand-editing a prompt file.

#### Acceptance Criteria

- AC-001: An explicit workspace initialization or workflow invocation binds
  one Codex session to exactly one canonical project and workflow before direct
  prompt capture can affect an objective.
- AC-002: Every safe direct turn receives durable session and turn provenance,
  while only intent, steering, constraint, clarification-answer, or acceptance
  changes update the objective-owned canonical prompt.
- AC-003: Semantic and grammatical refinement is coordinator-owned, concise,
  and lossless with respect to user facts, constraints, data, and acceptance
  intent; hooks only stage bounded input and cannot author or execute workflow
  semantics.
- AC-004: Accepted prompt updates use staged, accepted, and consumed states,
  reject stale base digests or manual-edit drift, and automatically start or
  resume only the session's bound workflow.
- AC-005: Prompt workspaces use one canonical v3 schema with a collision-safe
  five-character user-facing prompt reference at the filename prefix and the
  existing full internal prompt ID as authority.
- AC-006: A one-time v2-to-v3 migration preserves editable prompts, immutable
  run revisions, bindings, queues, active objectives, and manual edit support;
  users can resolve `run` targets by unique reference, full ID, or filename.
- AC-007: Manual prompt-file edits never auto-run and remain effective only
  through an explicit Task Implementer or SDLC `run` invocation.
- AC-008: Source implementation, hook installation, hook registration and
  trust, restart, and fresh-session behavior remain separately reported proof
  gates.

#### Negative Criteria

- NC-001: Unbound, ambiguous, peer-blocked, Stop-generated continuation,
  subagent, duplicate, stale-session, or replayed turns must not update or run
  an objective.
- NC-002: Secrets or recognized credential material must not be persisted in a
  raw journal, staged event, canonical prompt, diagnostic, or test artifact.
- NC-003: A short prompt reference must never become canonical identity, and a
  collision must fail closed rather than select an arbitrary prompt.
- NC-004: The generic context hook must not become a workflow router, and a
  second independent Stop hook must not race the shared lifecycle arbiter.
- NC-005: Prompt schema v3 must not retain parallel v2 write behavior,
  compatibility aliases, or a second canonical prompt path.

#### Validation Method

Validate state ownership, file modes, atomic writes, no-follow path handling,
secret rejection, idempotency, prompt migration, ref resolution, workflow
adapters, shared Stop arbitration, and source/install/runtime separation.

#### Test Method

Use disposable Codex homes and repositories to cover binding, classification,
stage/accept/consume, concurrent and duplicate turns, manual drift, secrets,
ambiguous attachment, workflow isolation, migration, ref collisions, immutable
revisions, explicit manual reruns, and Stop/subagent exclusions.

#### Evaluation Method

After separately authorized installation and restart, bind fresh Task
Implementer and SDLC sessions, submit direct steering prompts, and confirm each
objective is refined and resumed once while unrelated, unsafe, and manually
edited prompts remain inert.

<!-- /REQUIREMENT: REQ-009 -->

<!-- REQUIREMENT: REQ-010 status=active priority=P0 type=feature -->
### REQ-010: Require evidence-intensive cross-layer troubleshooting

#### User Story

Operators and developers need `$troubleshoot` to investigate the full deployed
stack, correlated logs, infrastructure, and relevant code paths instead of
stopping after a few status commands or passive terminal waiting.

#### Acceptance Criteria

- AC-001: Troubleshooting discovers technologies, deployed versions,
  deployment model, configuration authorities, components, dependencies,
  ports, protocols, authentication, and control/data flows before hypothesis
  testing, then compares observations with version-matched official vendor
  architecture and configuration documentation.
- AC-002: Every in-scope component receives an evidence-backed verification
  record for existence, version, effective configuration, process or workload
  health, dependency reachability, DNS or service-name resolution,
  authentication, resource pressure, clock state, restart history, recent
  changes, and current verdict.
- AC-003: The explicit included and excluded system boundary, exercised paths,
  incident-window start and end, clock offsets, correlation identities, and
  bounded log coverage are recorded. The log ledger contains exactly one row
  for each component, application/job, container/orchestrator,
  service-manager, OS/kernel, network/firewall, storage, and GPU or hardware
  layer; unavailable, unsafe, and not-applicable sources remain explicit.
  Evidence-bearing component and log cells are substantive independently; a
  neighboring cell or status token cannot make a placeholder count as proof.
- AC-004: Each experiment states a hypothesis, expected evidence, timeout or
  output bound, and next branch. Code hypotheses require reproduction,
  execution/data-path tracing, failure artifacts, configuration/environment
  review, focused analysis, and bounded instrumentation when needed.
- AC-005: Temporary debug escalation is narrow, authorized, time-bounded,
  impact-assessed, redacted, and restored with proof before completion.
- AC-006: Technology references cover Slurm, Soperator, Kubernetes, Nebius,
  Linux, networking, storage, GPUs, and code debugging while the main skill
  retains one generic causal control flow.
- AC-007: Soperator guidance distinguishes separately scheduled ActiveChecks,
  which may require complete or exclusive GPU resources, from passive or
  workload-coupled prolog, epilog, and `HealthCheckProgram` checks around
  customer jobs.
- AC-008: Every health conclusion is limited to the declared components,
  dependencies, exercised control and data paths, and observation window.

#### Negative Criteria

- NC-001: A healthy CLI status, passing test suite, green Kubernetes object,
  or recent terminal output alone must not prove system or code health.
- NC-002: Troubleshooting must not use indefinite `tail -f`, arbitrary sleeps,
  unfiltered log dumps, speculative observability calls, or repeated unchanged
  commands without a new hypothesis and evidence target.
- NC-003: Missing evidence must remain `UNKNOWN`; the workflow must not claim
  an entire codebase is bug-free or silently mark an unavailable layer healthy.
- NC-004: ActiveChecks, cloud changes, credential/IAM changes, production debug
  escalation, and other resource-consuming or availability-affecting actions
  require the existing exact authorization and live-product evidence gates.

#### Validation Method

Validate state-machine routing, component/log/timeline ledgers, playbook source
quality, report completion enforcement, mutation classification, and debug
restoration against the skill contract and optional Stop hook.

#### Test Method

Use adversarial fixtures where status appears healthy but logs reveal
configuration drift, clock or MUNGE failure, controller-worker connectivity,
accounting errors, stale logs, GPU Xid events, resource exhaustion, Soperator
check failures, Nebius control-plane constraints, or application defects.
Reject missing or duplicate canonical log-layer rows, placeholder component
identities or component/log evidence, and missing assertion-boundary or
incident-window fields.

#### Evaluation Method

Run fresh-session graded scenarios and require evidence-backed coverage for
every completion criterion; static text tests alone do not prove runtime
investigation quality.

<!-- /REQUIREMENT: REQ-010 -->

<!-- REQUIREMENT: REQ-011 status=active priority=P0 type=feature -->
### REQ-011: Commit complete multi-project repository changes through one safe transaction

#### User Story

Codex users need one explicit `$commit` invocation to commit the complete local
repository diff even when related changes span multiple project folders,
without weakening project lifecycle, Task Implementer, Worktree, or Agentic
SDLC ownership.

#### Acceptance Criteria

- AC-001: Direct commit mutation requires an explicit current-turn invocation,
  expressed either as a leading `$commit` token or as a bounded leading
  directive such as `run`, `apply`, `execute`, `invoke`, or `use` immediately
  followed by `$commit`; optional leading politeness does not weaken that
  binding. The authorization preserves the public whole-repository, local-only
  workflow with repository-root `git add -A`, normal hooks, and no push.
- AC-002: The reviewed candidate tree is computed from the current real index
  plus the complete tracked and untracked worktree delta before the real index
  is changed, then bound to the exact common Git directory, worktree, ref,
  branch, base `HEAD`, index, status, selected lifecycle, and one-shot claim.
  Repository-shaping Git environment variables are rejected before discovery
  or mutation so callers cannot redirect the canonical repository, worktree,
  object store, configuration, or real index.
- AC-003: Execution revalidates the claim under the repository/ref mutation
  lock, requires the staged tree and committed tree to equal the reviewed tree,
  and proves one single-parent direct-child commit with the expected final
  checkout state.
- AC-004: Changes under repository root files and any number of sibling project
  folders may commit together without requiring lifecycle attestations from
  every affected sibling; the current selected-project lifecycle remains the
  only project-contract gate.
- AC-005: Task Implementer worker commits and Worktree integration commits use
  their exact delegated claims, and interrupted exact-child adoption revalidates
  that delegated ownership before completing the claim. Dirty Task Implementer
  source checkouts direct the user to a separate explicit `$commit`, and active
  Agentic SDLC continues to require `sdlc-commit`.
- AC-006: Interrupted execution can adopt only an exact staged state or exact
  direct-child commit, including from a fresh explicit invocation after the
  commit completed but claim persistence did not; a hook-modified committed
  tree remains review-required until that clean exact direct child is
  explicitly reviewed, while merge commits, new dirt, failed hooks without a
  commit, ref movement, or identity drift become stale without reset, amend,
  unstage, or duplicate commit.

#### Negative Criteria

- NC-001: Raw `git add`, path-scoped staging, `git add .`, composed or wrapped
  helper commands, alternate helpers, untrusted helper bytes, stale sessions,
  claim replay, malformed Worktree ownership/coordination records, and
  mismatched worktree/ref identities remain denied. Caller-provided Git
  environment that can reshape repository discovery or state remains denied;
  the helper's private preview index is the only permitted index override.
- NC-002: `$commit` must not discover, integrate, promote, clean, or advance
  Task Implementer lanes, Worktree children, or Agentic SDLC execution state.
- NC-003: The transaction must not persist prompt bodies, diff contents,
  secrets, private endpoints, commit messages, or repository file contents in
  private authorization and claim state.
- NC-004: Casual mentions, questions, quoted examples, help requests, later
  prose references, subagent turns, and Stop or system continuations must not
  mint commit authorization merely because they contain `$commit` or an
  invocation verb.

#### Validation Method

Use disposable multi-project Git repositories and private Codex homes to prove
prompt binding, helper trust, whole-tree review, claim consumption, shared-ref
serialization, crash recovery, workflow isolation, and unchanged lifecycle
write epochs for verified Git-only effects.

#### Test Method

Cover root and sibling project changes, pre-staged and untracked files,
renames/deletions, linked worktrees, default/protected branches, conflicts,
secret-like content, malformed commands and managed state, merge commits, drift
at each transition, commit-hook mutation, duplicate execution, Task Implementer
and Worktree delegation, active/released Agentic SDLC state, accepted leading
directive forms, and rejected conversational or quoted mentions.
Also cover contradictory explicit origin markers and repository-shaping Git
environment without changing either the real index or an alternate index.

#### Evaluation Method

After separately authorized source installation and restart, run one fresh
explicit `$commit` in a disposable multi-project repository and independently
verify exact committed tree, direct ancestry, clean status, hook admission, and
absence of a second commit on recovery.

<!-- /REQUIREMENT: REQ-011 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
