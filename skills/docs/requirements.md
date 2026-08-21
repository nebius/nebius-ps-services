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
- AC-010: The first Stop continuation from `implementation-open` atomically
  enters `reconciliation-required` before requesting reconciliation, preserves
  the implementation write epoch, and invalidates stale planning evidence. The
  transition must not depend on a Stop-generated continuation emitting a new
  `UserPromptSubmit` event.
- AC-011: Project-instruction rendering rejects a disposition that cannot be
  applied to the inspected target before publishing render evidence. A revised
  current-session decision may replace prior rendered rules only while holding
  the private-bundle render lock and when the canonical predecessor state
  exactly owns the current private rules bytes. The final replacement boundary
  revalidates the identity and exact bytes of both predecessor state and rules;
  replacement is atomic and matching state is published next. Only a proven
  private I/O failure while publishing that matching state returns
  `RENDER_STATE_PUBLICATION_INCOMPLETE` and is safely recoverable by rerunning
  the exact decision. If replacement completed before its directory sync
  failed, that equal-bytes retry must re-sync the private parent directory;
  generic `UNSAFE_TARGET` remains terminal.

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
- NC-007: A continuation must not ask the agent to reconcile canonical specs
  while retaining a lifecycle phase that denies those exact spec mutations.
- NC-008: A missing, malformed, ownership-mismatched, unsafe, or concurrently
  changing render-state predecessor must not authorize replacement of existing
  private rules, and a managed target must not treat `existing-sufficient` as a
  renderable outcome. A predecessor-state change after validation but before
  final rules replacement must block without changing the rules.

#### Validation Method

Run owner validation, lifecycle transition tests, hook contract tests, and
project-instruction discovery/render/revision/apply/verify tests.

#### Test Method

Exercise every lifecycle phase, rejected transition, stale-evidence path,
missing-spec path, bounded waiver, repeated successful writes, terminal
reconciliation, unchanged-spec seal, and missing-target `not-needed` outcome
using disposable repositories and private state roots. Cover paraphrased
user-dependence intent, global-policy isolation, missing and human-owned
targets, same-directory overrides, nearest-ancestor root discovery, and empty
root-marker configuration. Exercise an `implementation-open` Stop followed by
canonical spec reconciliation without an intervening `UserPromptSubmit`. Revise
an owned empty render to needed rules in the same current-session bundle, and
prove that unowned or tampered predecessor bytes remain unchanged and blocked.
Mutate the predecessor state between validation and final replacement and
require the rules to remain byte-identical. Inject a matching-state publication
I/O failure through the CLI, require the distinct retryable result, then prove
that the exact rerun completes and re-syncs the parent directory after an
interrupted post-replacement sync while generic unsafe targets remain terminal.

#### Evaluation Method

Confirm repeated implementation writes remain silent while advancing the write
epoch, one terminal reconciliation classifies the final delta, an explicit
project-instructions outcome is reported without promising file creation, and
the selected project reaches `sealed` with a current receipt or an explicit
bounded waiver. Confirm that explicit no-break intent is injected for the
current implementation and persists at terminal seal when project rules are
needed, without requiring magic words or weakening higher-level safeguards.
Confirm a Stop-generated continuation can immediately reconcile canonical
specs and that the transition neither advances the write epoch nor preserves
stale receipt, spec, rules, or planned-epoch bindings. Confirm an inapplicable
decision fails before render publication and that an exactly owned decision
revision reaches planning without deleting or bypassing private evidence.
Confirm the final replacement rejects either predecessor changing after
validation, and only `RENDER_STATE_PUBLICATION_INCOMPLETE` authorizes an exact
render retry that completes state durability while `UNSAFE_TARGET` still stops.

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
### REQ-008: End every troubleshoot invocation with a concise outcome report

#### User Story

Users invoking `$troubleshoot` need a clear answer about what was fixed, what
was verified, and who must do what next without a report-format problem
interrupting safe diagnostic or repair work.

#### Acceptance Criteria

- AC-001: Every explicit `$troubleshoot` turn establishes a turn-scoped report
  obligation even when no remediation-budget marker is created, but the
  obligation is advisory during an ordinary, non-exhausted Stop.
- AC-002: The normal user-visible report has exactly four required sections:
  `Outcome`, `Root Cause And Fix`, `Verification`, and `Next Action`. It states
  one supported classification, confidence, fixed scope, current state, root
  cause, changes made, verified and unverified scope, and an exact next-action
  owner, action, and done condition. A bounded `Evidence Appendix` is optional.
  Public HTTPS links may be used after private-host validation. Prefer local
  evidence labels as plain inline-code, repository-relative slash-separated
  paths with an optional positive line suffix. A local inline or Markdown
  target may instead be repository-relative, platform-native absolute,
  home-relative, or a constrained local `file:` URI when its decoded,
  canonical destination remains inside the Git repository root derived from
  the event working directory. Existing candidates resolve symlinks; a
  nonexistent candidate uses lexical containment beneath the deepest existing
  ancestor. When no Git root can be proven, absolute, home-relative, and local
  `file:` forms are unsafe because containment is indeterminate. Reject parent
  or symlink escape, ambiguous encoding or Markdown link syntax, renderer-active
  schemes, remote file authorities, credentials, query or fragment components,
  UNC paths, and non-native separators or path syntax. Repository containment
  validates only the reference address and never exempts the decoded target,
  link label, surrounding prose, file content, secrets, or private endpoints
  from independent safety checks.
- AC-003: `DIAGNOSED-FIXED` means the causal owner and earliest divergence are
  proven, an owner-correct repair is applied, and the original reproducer plus
  focused regression and source or boundary checks pass for the named fixed
  scope. Installation, deployment, restart, or live replay may remain
  explicitly unverified with a concise next action. `VERIFIED_FIXED` remains
  reserved for complete end-to-end proof, while `DIAGNOSED_NOT_FIXED` means the
  cause is diagnosed but no owner-correct repair was applied.
- AC-004: Ordinary malformed, incomplete, `FAIL`, or `UNKNOWN` report content
  records an advisory-incomplete disposition and allows Stop to continue. It
  must not request another turn, deny later tools, or substitute a generated
  fallback report. The workflow continues safe evidence collection and repair
  while a decision-changing bounded experiment remains.
- AC-005: Exact remediation-budget exhaustion remains a hard Stop contract and
  cannot use `DIAGNOSED-FIXED`. A contained local-reference format defect is
  advisory, while an ordinary report containing sensitive content, an
  outside-repository target, traversal or symlink escape, or an unsafe local
  URI closes its obligation and terminates with one bounded deterministic
  warning; it must not request a model-authored replacement. Stop is terminal
  detection and does not claim to retract output already rendered by the host.
  Generated exhausted-report fields must replace over-limit reference markup
  atomically rather than truncate through a Markdown or inline-code token.
  Invalid trusted coordination state, missing mutation authority, and
  independent peer Stop policy remain fail-closed.
- AC-006: The workflow documents that host or process termination before any
  Stop event cannot be converted into an assistant response by a local hook.
  A later resumed turn reports the interrupted state without claiming proof
  that the hook could not observe.
- AC-007: The shared Stop arbiter keeps a peer continuation or terminal result
  authoritative. It evaluates every delegate for lifecycle transitions and
  cleanup, retains the first terminal result in delegate order, and finalizes a
  valid concise report transactionally. If that finalization fails after a peer
  terminal result, the first terminal result remains authoritative and gains
  only a generic trusted-state warning. An ordinary advisory-incomplete report
  never becomes a competing Stop decision.
- AC-008: The report-obligation sidecar uses one current schema and canonical
  status machine. An older active schema is not migrated in place; it fails
  closed with an actionable fresh-session instruction, and activation must not
  proceed while an older active obligation remains. Activation preflight emits
  schema/status counts only, preserves recognized terminal history under the
  exact v1 and v2 schemas, and fails closed on active legacy, unknown-schema,
  unsupported-status, unreadable, or malformed sidecars.

#### Negative Criteria

- NC-001: An ordinary report-format defect, unknown criterion, unavailable log
  source, or non-passing check must not stop the agent, deny tools, or consume a
  remediation attempt by itself.
- NC-002: Reporting must not loop, fabricate evidence, expose secrets, emit a
  large generated substitute report, create a second automatic report, or
  obscure a successful source repair behind `DIAGNOSED_NOT_FIXED` merely
  because activation remains pending.
- NC-003: A concise report must not hide material unavailable components,
  unverified activation or live state, missing log intervals, or another
  decision-relevant coverage gap; summarize them under `Not verified` and give
  the exact next action.
- NC-004: The hook validator must not be described as sanitization or complete
  secret detection, and a terminal warning must not echo rejected content.

#### Validation Method

Validate invocation tracking, concise field-specific parsing, advisory ordinary
Stop behavior, `DIAGNOSED-FIXED` semantics, safe reference validation,
sensitive-output terminal detection, strict exhaustion composition,
interruption handling, schema migration boundaries, bounded prompt context,
and all-delegate Stop-arbiter behavior.

#### Test Method

Cover source-fixed/runtime-pending, verified, diagnosed-unrepaired, blocked,
tool-error, ordinary early-stop, malformed and partial reports, sensitive
content, local reference forms and full-Git-root containment, old-schema
resume, atomic state
failure including peer-terminal finalization, repeated Stop, budget exhaustion,
inclusive 5/120 limits, nested-working-directory and exhausted-fallback
containment, resumed
interruption, and composition with continuing and terminal project-contract
and SDLC delegates.

#### Evaluation Method

Invoke `$troubleshoot` with and without a remediation marker and confirm that
ordinary report quality is advisory, hard boundaries remain fail-closed, and a
successful source repair produces one concise `DIAGNOSED-FIXED` report with an
exact activation next action. In a disposable fresh session, force only fake
sensitive content and confirm the original may remain visible but no automatic
continuation or second assistant report is created.

<!-- /REQUIREMENT: REQ-008 -->

<!-- REQUIREMENT: REQ-009 status=active priority=P0 type=feature -->
### REQ-009: Capture and route bound-session prompt intent safely

#### User Story

Task Implementer and Agentic SDLC users need every direct prompt to reach the
current agent normally. When the session is bound to an active managed
objective, eligible material intent should also be captured in that
objective's canonical prompt without turning capture into an execution gate or
requiring the user to locate and edit the prompt file.

#### Acceptance Criteria

- AC-001: An explicit workspace initialization or workflow invocation registers
  one active canonical objective for the selected workflow and project.
  Session binding and prompt capture are subordinate metadata and must never be
  prerequisites for delivering or executing a direct prompt in the current
  agent turn.
- AC-002: `UserPromptSubmit` prompt intake is non-blocking for direct root-agent
  prompts. Recognized secrets, stale writer provenance, binding conflicts,
  ambiguous objectives, unsafe private state, capture failures, and unexpected
  intake errors skip or report capture without returning a stop decision.
- AC-003: Every eligible safe direct turn in a bound workflow receives durable
  session and turn provenance when metadata-only staging succeeds. Staging
  persists bounded identity, digest, workflow, project, token, and timing
  fields but never the submitted prompt body or a raw prompt journal.
- AC-004: The current agent classifies the already-delivered prompt as merge,
  no-op, or sensitive. Only objective intent, steering, constraints, material
  clarification answers, and acceptance changes may produce a project-intent
  projection. Skill, workflow, shell, tool, delivery, agent-control, status,
  conversation, unrelated, and duplicate-only turns do not mutate the prompt.
- AC-005: A mixed turn persists and merges only its durable project-intent
  projection. That projection is concise and lossless with respect to every
  selected fact, value, negation, decision, uncertainty, example, reference,
  constraint, and acceptance outcome while excluding ephemeral execution or
  conversation clauses. Commands remain eligible when they define a project
  interface, behavior, example, or verification contract rather than request
  immediate execution.
- AC-006: Accepted prompt updates use version-separated staged, accepted, and
  consumed event-v2 states, canonical prompt identity, compare-and-set writes,
  and an exact-once operation marker bound to both the operation identity and
  accepted projection digest. A capture conflict may leave bounded private
  diagnostic state but cannot stop the direct request, auto-rebase over prompt
  drift, or create a second workflow execution path.
- AC-007: A staged event may be newly classified only by its matching current
  Codex session and current-turn claim. An already-accepted immutable
  projection may complete its exact merge and consume transition, but an older
  staged event must never be classified from later conversation context.
- AC-008: Task Implementer resolves its primary-checkout project and
  manifest-proven managed-lane project as one logical active objective for
  attachment, capture, registry updates, and prompt-result validation.
  Unrelated projects remain ineligible for capture.
- AC-009: Prompt workspaces use one canonical v3 schema with a collision-safe
  five-character user-facing prompt reference at the filename prefix and the
  existing full internal prompt ID as authority.
- AC-010: A one-time prompt-workspace v2-to-v3 migration preserves editable prompts, immutable
  run revisions, bindings, queues, active objectives, and manual edit support;
  users can resolve `run` targets by unique reference, full ID, or filename.
- AC-011: Prompt-session event-v1 records and their raw journals remain inert
  in their original namespace. Event-v2 does not read, migrate, rewrite, or
  delete them, and it has no legacy compatibility path.
- AC-012: Manual prompt-file edits never auto-run and remain effective only
  through an explicit Task Implementer or SDLC `run` invocation.
- AC-013: Source implementation, hook installation, hook registration and
  trust, restart, and fresh-session behavior remain separately reported proof
  gates.

#### Negative Criteria

- NC-001: Unbound, ambiguous, Stop-generated continuation, subagent, no-op,
  sensitive, stale-session, failed-capture, or replayed turns must not update an
  objective, but prompt-intake policy must not prevent the direct user turn
  from running.
- NC-002: Secrets or recognized credential material must not be persisted in a
  prompt body, project-intent projection, canonical prompt, diagnostic, or
  test artifact. A recognized pre-stage match creates no event; a later
  sensitive disposition retains no submitted digest, operation token, or
  projection.
- NC-003: A short prompt reference must never become canonical identity, and a
  collision must fail closed rather than select an arbitrary prompt.
- NC-004: The generic context hook must not become a workflow router, and a
  second independent Stop hook must not race the shared lifecycle arbiter.
  Prompt-session Stop evaluation must not block completion or request a
  continuation solely because capture is staged, accepted, incomplete, or
  internally invalid.
- NC-005: Prompt schema v3 must not retain parallel v2 write behavior,
  compatibility aliases, or a second canonical prompt path.

#### Validation Method

Validate unconditional hook pass-through, metadata-only event-v2 ownership,
current-session/current-turn authorization, file modes, atomic writes,
no-follow path handling, selective projection, secret non-persistence,
projection-bound operation identity, idempotency, prompt migration, ref
resolution, logical primary/lane identity, workflow adapters, non-blocking
shared Stop arbitration, and source/install/runtime separation.

#### Test Method

Use disposable Codex homes and repositories to cover wrapper-level pass-through
for every structured and unexpected intake failure, binding, dispositions,
the project-intent allowlist and no-op list, mixed prompts, commands as data,
contextual clarification answers, metadata-only stage/accept/consume/discard,
concurrent and duplicate turns, projection substitution, manual drift,
secrets, stale events, event-v1 isolation, primary/lane attachment, workflow
isolation, prompt migration, ref collisions, immutable revisions, explicit
manual reruns, and Stop/subagent exclusions.

#### Evaluation Method

After separately authorized installation and restart, bind fresh Task
Implementer and SDLC sessions, submit direct material and nonmaterial prompts,
and confirm every prompt reaches the current agent. Verify safe project intent
is projected and captured once without starting either workflow, excluded
clauses and prompt bodies are never persisted, and secret, unrelated, unsafe,
and manually edited input is not captured or auto-executed.

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
- AC-009: Detailed architecture, component, incident, log, hypothesis, code,
  and completion ledgers are retained as internal decision evidence. The
  ordinary user-visible report summarizes only decision-relevant proof and
  gaps; it does not reproduce those ledgers unless an optional evidence
  appendix is requested or needed for a hard boundary.

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
quality, concise report projection, mutation classification, and debug
restoration against the skill contract and optional Stop hook.

#### Test Method

Use adversarial fixtures where status appears healthy but internal evidence
reveals configuration drift, clock or MUNGE failure, controller-worker
connectivity, accounting errors, stale logs, GPU Xid events, resource
exhaustion, Soperator check failures, Nebius control-plane constraints, or
application defects. Reject unsupported internal conclusions while confirming
that an incomplete ordinary final report remains advisory and non-blocking.

#### Evaluation Method

Run fresh-session graded scenarios and require evidence-backed internal
coverage for every applicable completion criterion plus an accurate concise
projection; static text tests alone do not prove runtime investigation quality.

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
  Agentic SDLC continues to require `sdlc-commit`. Task start and base-state
  recovery return one transient canonical commit context with the exact
  PATH-canonical Python-family executable, helper, outer lifecycle cwd, worker
  root, raw session ID, authorization, claim, and prepare argv; persisted
  session fingerprints are never accepted as raw `--session-id` values. Task
  start and recovery also return a transient result context with the exact
  private result path and explicit external publication working directory;
  workers never publish results from the selected project cwd. The context also
  exposes an exact draft path and publisher argv; the helper, not the worker,
  computes the final canonical digest, canonicalizes changed paths as a sorted
  unique set, and atomically publishes the result. Coordinator acceptance does
  not treat list order as scope authority and may revalidate only an exact
  digest-and-commit-bound prior ordering rejection.
- AC-005a: Task arm and rearm expose their opaque worker deadline only as the
  canonical `start_lease` output consumed unchanged by `task-start`, together
  with an exact assignment-derived start argv and scope cwd. A confirmed
  running-task recovery exposes the equivalent exact recovery argv and scope
  cwd. Internal task-plane `dispatched_at` state is not presented as a worker
  command input, and coordinators do not manually reconstruct private paths.
- AC-006: Interrupted execution can adopt only an exact staged state or exact
  direct-child commit, including from a fresh explicit invocation after the
  commit completed but claim persistence did not; a hook-modified committed
  tree remains review-required until that clean exact direct child is
  explicitly reviewed, while merge commits, new dirt, failed hooks without a
  commit, ref movement, or identity drift become stale without reset, amend,
  unstage, or duplicate commit.

#### Negative Criteria

- NC-001: Raw `git add`, path-scoped staging, `git add .`, composed or wrapped
  helper commands, alternate helpers, untrusted helper bytes, non-PATH or
  non-Python-family interpreters, stale sessions,
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
write epochs for verified Git-only effects. Run the lifecycle hook and worker
adapter under different canonical Python versions, prove the exact returned raw
session succeeds, and prove substituting its fingerprint fails before staging.

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

<!-- REQUIREMENT: REQ-012 status=active priority=P0 type=feature -->
### REQ-012: Checkpoint a dirty persistent Task Implementer lane before a run

#### User Story

Task Implementer users need one explicit `run` invocation to preserve a
coherent dirty persistent lane as the next generation baseline, including
related changes that span repository root and sibling project folders, without
manually committing, stashing, resetting, or cleaning the managed checkout.

#### Acceptance Criteria

- AC-001: A resource-free run against an idle or safely reconcilable persistent
  lane computes and reviews the complete lane candidate before acquiring its
  first generation. A clean candidate keeps the current `HEAD` and creates no
  commit.
- AC-002: A dirty candidate uses repository-root `git add -A`, normal commit
  hooks, and one fixed Task Implementer checkpoint message to create one
  single-parent direct-child commit containing staged, unstaged, deleted,
  renamed, and untracked non-ignored changes across the whole lane.
- AC-003: Every changed lane path, including both sides of a rename, becomes an
  exact initial generation claim in addition to the planned coordinator, task,
  and conflict-domain claims. Any live overlapping lane claim blocks before
  real-index or history mutation.
- AC-004: Worktree owns one recoverable checkpoint-and-generation-open
  transaction that binds the lane, run, workspace, branch, pre-checkpoint
  `HEAD`, status digest, candidate tree, complete changed paths, and claims.
  Task Implementer persists the preparation token, candidate tree, path digest,
  and claims digest, pauses dirty candidates for review, and the open consumes
  only that exact evidence. The resulting checkpoint `HEAD` becomes the lane,
  lease, interop, coordinator, and first-wave baseline before task resources
  are created.
- AC-005: The primary/source checkout remains byte-for-byte and Git-state
  untouched. `workspace init`, `workspace reuse`, `integrate`, and
  `workspace remove` never create a checkpoint, and dirt discovered after
  generation acquisition remains a conflict rather than another automatic
  commit.
- AC-006: Interrupted execution adopts only the exact staged candidate or the
  exact clean direct-child checkpoint. Hook-modified commits require review
  and rotate the preparation token so a blind retry cannot adopt them.
  Conflicts, unsafe content, unexpected paths, gitlinks, tracked symlinks,
  wrong branch or head, Git operations, or non-quiescent writers block without
  reset, stash, clean, amend, unstage, or duplicate history.

#### Negative Criteria

- NC-001: Checkpoint behavior must not become a sixth public action, a manual
  managed-lane repair instruction, or a compatibility alias for the former
  clean-lane acquisition path.
- NC-002: Private checkpoint state must not persist prompt bodies, diff
  contents, repository file contents, secrets, private endpoints, or commit
  message input.
- NC-003: A checkpoint must not weaken selected-project lifecycle gates,
  bypass applicable repository instructions, silently absorb an unresolved
  cross-project ownership conflict, or create task resources before its exact
  generation baseline is durable.

#### Validation Method

Use disposable multi-project repositories, linked persistent lanes, private
Codex homes, normal and adversarial commit hooks, injected crash boundaries,
and concurrent generation attempts to verify exact tree, ancestry, claims,
recovery, source isolation, and public-interface invariants.

#### Test Method

Cover clean no-op; root, selected-project, and sibling-project dirt; staged and
unstaged files; additions, deletions, renames, untracked and ignored files;
conflicting claims; operations in progress; gitlinks and symlinks; hook
failure, mutation, and timeout; crashes before and after commit; concurrent
runs; active-generation dirt; final wave integration; and no implicit
checkpoint from the other four public actions.

#### Evaluation Method

After source validation and separately authorized installation, restart into a
fresh session and run one dirty-lane prompt in a disposable repository. Verify
one checkpoint commit, clean managed lane, unchanged primary checkout, exact
post-checkpoint generation baseline, and no second commit on retry.

<!-- /REQUIREMENT: REQ-012 -->

<!-- REQUIREMENT: REQ-013 status=active priority=P1 type=feature -->
### REQ-013: Reopen an existing Task Implementer workspace without lane mutation

#### User Story

Task Implementer users need one explicit command from the primary project or
its owning managed lane to reopen the same generated workspace after an editor
window closes, without knowing private workspace or worktree paths.

#### Acceptance Criteria

- AC-001: `$task-implementer workspace reuse [project-folder]` defaults to the
  current directory and resolves exactly the current repository, source ref,
  and repo-relative project scope. Primary-project and owning-lane invocations
  resolve the same workspace; every other caller Git root is rejected even if
  branch metadata aliases the recorded source ref.
- AC-002: Reuse requires an existing safe workspace-v2 manifest, generated VS
  Code workspace, and authoritative live Worktree lane identity including lane
  ID, incarnation, branch, worktree, scope, an existing source ref, common Git
  directory, and primary checkout.
- AC-003: Reuse opens dirty, idle, active, pending, integrating, conflicted, or
  source-promoted live lanes without refreshing Git, changing lane or prompt
  state, checkpointing, migrating, queuing, integrating, removing, or claiming
  execution readiness.
- AC-004: Missing workspace state returns `WORKSPACE_NOT_FOUND` with
  `workspace init` guidance and creates nothing. Unsafe, creating,
  creation-recovery, removing, removed, or identity-mismatched state fails
  closed before editor launch.
- AC-005: Editor unavailability, timeout, or nonzero exit is a warning after
  successful validation and does not invalidate reuse. `run <prompt-ref>`
  remains valid directly from the primary source project.

#### Negative Criteria

- NC-001: Reuse must not scan other branches, guess among workspaces, accept a
  user-supplied editor target, or become `workspace open`, `workspace --reuse`,
  or another compatibility alias.
- NC-002: Reuse must not invoke lane ensure, idle refresh, creation recovery,
  prompt migration, intake, generation, integration, removal, or public
  Worktree lifecycle actions.

#### Validation Method

Use disposable repositories, persistent lanes, and isolated private homes to
compare source-project and lane-project resolution and to snapshot Git,
workspace, prompt, run, and lane state before and after repeated reuse.

#### Test Method

Cover clean and dirty idle lanes, active and retained live lifecycle state,
missing workspace state, unsafe paths and permissions, removed or mismatched
lane identity, repeated invocation, source and lane cwd aliases, and editor
unavailable, timeout, and nonzero exit behavior.

#### Evaluation Method

After separately authorized installation, invoke reuse from a disposable
source project and its managed lane, confirm the same generated workspace opens,
and independently verify unchanged Git and private workflow state.

<!-- /REQUIREMENT: REQ-013 -->

<!-- REQUIREMENT: REQ-014 status=active priority=P0 type=feature -->
### REQ-014: Bridge Task Implementer project contracts through the owner lifecycle

#### User Story

Task Implementer users need one explicit `run` to move from a reviewed lane
checkpoint into worker dispatch without manually reconciling incompatible
private project-instruction bundles or bypassing the selected-project
lifecycle.

#### Acceptance Criteria

- AC-001: The project lifecycle admits a canonical project-instructions
  inspect or render command bound to a Task Implementer run-owned bundle only
  after a trusted, read-only Task Implementer adapter proves the exact
  workspace, run, active wave, integration checkout, selected project, source
  lane, canonical installed or sibling-source helpers, action, and every
  action-specific private path. First preparation is bound to the original
  wave base; a retained correction is bound to its exact recorded contract
  commit, and an adopted sealed-contract head additionally requires the
  authoritative owner journal. The integration checkout remains the exact
  registered linked worktree sharing the source lane's common Git directory;
  an independent repository cannot substitute at replan, prepare,
  authorization, or dispatch. The integration checkout may be clean or have
  only the complete selected-project requirements and design staged; unstaged,
  untracked, deleted, symlinked, sibling, or other staged paths remain invalid.
- AC-002: The run-owned receipt, runtime declaration, manifest, decision,
  rendered rules, ownership, and state remain under the retained run
  orchestration root. The current lifecycle session remains authoritative for
  the outer selected lane and does not copy, rewrite, or impersonate that
  evidence.
- AC-003: A successful first `wave-plan` that opens the lane generation makes
  the selected lane lifecycle reconciliation-required before Stop. Repeated
  resume observations do not reopen a sealed lifecycle or require a second
  checkpoint.
- AC-004: The prepared-wave bridge admits only non-repository-mutating inspect
  and render during reconciliation or while implementation remains open at an
  exact prepared-wave boundary. Apply and verify remain on the ordinary
  terminal project lifecycle path, preserving the project-instructions
  helper's validation, repository-mutation, and terminal-state contracts. Task
  Implementer dispatch continues to replay the exact run-owned render against
  the clean contract commit.
- AC-005: The public Task Implementer interface remains exactly five actions,
  and users are never asked to supply lifecycle session paths, run IDs, wave
  IDs, integration paths, or an adapter attestation.
- AC-006: When the selected-project hook is bound to the persistent lane, it
  may admit an exact Task Implementer worker `prepare`, `execute`, or `review`
  commit transaction only after the read-only adapter proves the active
  capacity batch, assignment digest, running task plane, worker session,
  worker worktree, canonical commit evidence, original command digest, and
  outer selected project. The adapter returns the exact command-derived worker
  session only after matching its digest to that plane and evidence; commit
  validation then uses the attested worker root and worker session even when
  the hook payload retains the outer lifecycle session. Direct commits remain
  payload-session-bound, and selected-project lifecycle ownership is unchanged.

#### Negative Criteria

- NC-001: An arbitrary alternate bundle, inactive or stale run, mismatched
  selected project, different integration worktree, unsafe private path,
  symlink, unrelated integration delta, malformed action, untrusted helper, or
  basename lookalike must remain denied.
- NC-002: The bridge must not make every external project-instructions command
  lifecycle-neutral, weaken current-session evidence for ordinary work, or
  let Task Implementer author lifecycle state directly.
- NC-003: The lifecycle impact path must not infer a successful repository
  mutation from a failed Task Implementer command or from read-only workspace,
  intake, verification, reuse, integration inspection, or help actions.
- NC-004: Cross-worktree commit admission must not authorize raw Git, a
  coordinator-root transaction, an undispatched task, a sibling worktree, a
  stale assignment or session, altered evidence, or a composed command.

#### Validation Method

Use disposable linked-worktree repositories and isolated Codex homes to submit
the real Task Implementer run-owned project-instructions command through the
lifecycle hook and to observe the post-`wave-plan` lifecycle transition.

#### Test Method

Cover exact inspect and render admission; apply and verify bridge rejection;
ordinary terminal apply and verify behavior; current-session coordinator
behavior; alternate run, session, project, worktree, path, mode, symlink,
helper, action, and digest rejection; failed and repeated `wave-plan`;
dispatch replay; exact coordinator-bound worker commit prepare, execute, and
review; wrong-root and future-batch rejection; and unchanged public command
metadata.

#### Evaluation Method

After source validation and separately authorized installation, restart Codex
and resume a disposable retained run from the first-wave pre-dispatch boundary.
Verify canonical run-owned rendering, worker dispatch, one outer lifecycle
reconciliation, terminal sealing, retained recovery safety, and no user-visible
adapter ceremony.

<!-- /REQUIREMENT: REQ-014 -->

<!-- REQUIREMENT: REQ-015 status=active priority=P0 type=feature -->
### REQ-015: Recover missing active-wave worktree paths without discarding evidence

#### User Story

Task Implementer coordinators need an owner-supported way to continue a
stopped run when an active wave's worker or integration directory disappears
but the Worktree lease, branch, and Git administrative registration remain,
including an integrated checkout that disappears before promotion validation.

#### Acceptance Criteria

- AC-001: Recovery requires explicit confirmation that prior workers stopped,
  runs only from the exact owning project scope, and revalidates the active
  workspace, run, wave, running capacity batch or promotion-pending phase, and
  active Worktree generation lease before Git mutation. Running worker
  recovery also revalidates its assignment and task plane.
- AC-002: Each recovered path must have one exact `present` lease resource and
  one locked Git registration with the expected path, branch, administrative
  `HEAD`, branch tip, clean index, and no index lock. A running wave's
  integration tip must equal the wave contract commit; a promotion-pending
  integration tip must equal the recorded integrated head. A running worker
  tip may equal its base or one direct child of that base.
- AC-003: A proven missing path is rehydrated through Git's locked-worktree
  recovery mechanism, journaled, independently revalidated as a clean linked
  checkout, and kept `present` in the lease. The result explicitly reports
  that filesystem-only state was not recoverable.
- AC-004: Resource recovery changes no task plane or wave state and infers no
  commit, result, integration, or promotion. A fresh replacement worker still
  invokes normal `task-recover` from the restored assigned scope to transfer
  session ownership; promotion-pending recovery restores only the integration
  checkout needed for validation and promotion.
- AC-005: Worktree anchor discovery ignores lexically unrelated missing
  registrations before strict resolution while continuing to strictly resolve
  and validate the one current managed checkout candidate.

#### Negative Criteria

- NC-001: Recovery must not mark retained resources absent, prune Git
  administration, delete or recreate branches, reset, stash, clean, copy a
  worker patch, reconstruct a commit, release a lease, or promote a wave.
- NC-002: Missing or ambiguous registrations, unlocked records, broken
  symlinks, path collisions, staged index state, index locks, lease mismatch,
  branch or head drift, more than one worker commit, stale run state, or an
  unconfirmed worker stop remain retained and fail closed.
- NC-003: The internal recovery path must not become a sixth public Task
  Implementer action or authorize cloud, network, infrastructure, or service
  mutation.

#### Validation Method

Use disposable real linked worktrees and isolated Worktree lease state. Remove
active directories without removing their locked Git registrations, invoke
the exact owner recovery, and compare Git registrations, branches, lease rows,
task planes, wave state, and repository heads before and after.

#### Test Method

Cover missing integration and worker paths, worker base and one-direct-child
tips, explicit stop confirmation, idempotent present paths, staged index and
index-lock retention, broken symlinks, wrong lease tuples, branch/head drift,
future-batch assignments, normal post-rehydration `task-recover`, and unchanged
promotion state.

#### Evaluation Method

After separately authorized installation and restart, inspect a disposable
retained run with the same failure shape, rehydrate only the exact active-wave
resources, start a fresh replacement session through `task-recover`, and
independently verify that no product commit or promotion was inferred.

<!-- /REQUIREMENT: REQ-015 -->

<!-- REQUIREMENT: REQ-016 status=active priority=P0 type=feature -->
### REQ-016: Prove prompt-revision impact before workflow progression

#### User Story

Task Implementer and Agentic SDLC users need a semantic edit to an editable
objective prompt to be reflected in the canonical project contract, or proven
already satisfied, before an existing execution plan can continue. Users also
need concise status that explains whether the latest accepted revision changed
the contract without exposing prompt-derived content or private digests.

#### Acceptance Criteria

- AC-001: Both workflows continue to detect editable prompt changes from
  owner-computed exact-byte and normalized-intent digests and immutable prompt
  revisions. The editable prompt is never rewritten to embed a self-hash or
  caller-maintained digest.
- AC-002: For every accepted prompt revision, a shared owner validator requires
  exactly one disposition for every extracted statement occurrence. A
  disposition either maps the statement to current requirement and design
  record IDs, identifies an execution-only effect, or uses a bounded
  non-contract reason. Missing, duplicate, nonexistent, or superseded record
  mappings fail closed.
- AC-003: An immutable private impact attempt binds the workflow, prompt ID and
  revision, prompt intent identity, refinement identity, current canonical
  spec receipt, exact requirements and design identities, the complete
  statement-set identity, prior accepted impact head, and the derived effect
  and plan action. An atomic ledger selects one accepted attempt; identical
  retries adopt it and changed inputs create a superseding attempt rather than
  replacing history. Publication serializes per run, compare-checks the
  observed ledger head, and preserves then skips a conflicting orphan attempt
  after interruption instead of wedging or overwriting history.
- AC-004: `no_effect` is valid only when every statement is mapped to an active
  existing contract record or a bounded non-contract reason. Requirements or
  design effects require reconciliation and safe-boundary replanning before
  new dispatch or promotion. Material ambiguity remains pending clarification
  and produces no unlocking impact receipt.
- AC-005: Workflow state distinguishes the latest settled prompt revision from
  the execution plan's basis revision. A later proven no-effect revision may
  retain the older plan; an impactful revision freezes new dispatch and
  promotion while preserving already-running resources until a safe replan.
- AC-006: One plan-basis receipt binds the accepted impact and canonical
  spec-transition identities to the Task coordinator plan or Agentic feature
  plan. Every resource creation, dispatch, integration, promotion, and terminal
  progression boundary verifies that receipt against the current prompt,
  accepted impact head, plan identity, and canonical spec bytes so a stale
  downstream artifact cannot advance another generation.
- AC-007: Existing non-terminal state without an impact receipt is reconciled
  forward: new progression is frozen until the current basis is reconstructed
  and validated or safely replanned with a distinct plan identity. An existing
  coordinator cannot adopt the latest material revision as its historical
  basis merely because its old plan bytes still match. Terminal history remains
  readable as historical state without fabricating retroactive evidence.
- AC-008: Task Implementer and Agentic SDLC status report the public prompt
  reference and revision, editable-versus-accepted snapshot state, semantic
  edit detection, impact classification, mapped public record IDs or bounded
  reason category, and retain/replan/clarification action. Status never exposes
  raw prompt text, extracted statements, free-form rationale, private impact or
  run paths, or prompt-derived digest values. Existing editable prompt-path
  output remains unchanged.
- AC-009: The shared impact validator remains an internal
  `maintain-project-specs` owner boundary. Task Implementer and Agentic SDLC
  remain adapters with separate workflow roots and retain their explicit
  public `run` boundary.

#### Negative Criteria

- NC-001: Matching spec bytes, an agent-authored `no_effect` label, or a
  requirements-only digest must not independently prove that a changed prompt
  revision is satisfied.
- NC-002: Impact evidence must not become a second semantic requirements/design
  owner, mutate canonical specs, persist raw prompt content, or expose
  content-derived digests in public output.
- NC-003: Migration must not invent historical semantic coverage, retain a
  permanent legacy execution path, discard active resources, or allow stale
  coordinator evidence to bypass forward reconciliation.

#### Validation Method

Reparse the exact canonical specs, validate their current record inventory and
receipt, enumerate every refinement statement occurrence, derive the aggregate
effect and plan action from its dispositions, and verify the accepted impact
plus plan-basis chain at every progression boundary.

#### Test Method

Use disposable prompt workspaces and canonical spec pairs to cover omitted and
duplicate statements, inactive or nonexistent IDs, changed aggregate claims,
spec drift, concurrent writers, interrupted receipt publication, multiple
revisions, no-effect retention, impactful safe replanning, stale plan bases,
forward migration, terminal history, and sanitized status for both workflows.

#### Evaluation Method

Edit an accepted prompt after requirements and design exist, explicitly rerun
each workflow, and confirm the new revision is either traced to current record
IDs or forces reconciliation and replanning before new work. Confirm a real
semantic edit can no longer disappear behind an unsubstantiated `no_effect`
message and that no prompt hash header is created.

<!-- /REQUIREMENT: REQ-016 -->

<!-- REQUIREMENT: REQ-017 status=active priority=P0 type=feature -->
### REQ-017: Correct a reviewed wave without promoting known-bad integration

#### User Story

Task Implementer coordinators need blockers found by combined integration
review to be fixed by isolated workers while the reviewed wave remains
unpromoted and all accepted worker evidence remains intact.

#### Acceptance Criteria

- AC-001: An exact clean `promotion_pending` wave may append one correction
  round only when every indexed task is merged, every batch is done, the
  integration tip equals the sealed integrated head, and the persistent lane
  remains clean at the wave's original base.
- AC-002: Correction tasks are new immutable task IDs, cannot rewrite indexed
  task plans or depend on future waves, and extend the active generation's
  repository claims before coordinator or wave state changes.
- AC-003: Correction workers receive ordinary assignments and one-direct-child
  commit authorization from the sealed integrated head. Previously merged
  assignments remain valid against their own immutable task-plane bases and
  plan identities; assigned or running work must match the current correction
  plan and base.
- AC-004: Correction commits merge into the retained integration branch in
  stable task order. The new integration head contains every original and
  correction task commit, and promotion requires fresh combined validation and
  review evidence bound to that exact head.
- AC-005: Promotion remains one fast-forward from the original wave base. Task
  and integration cleanup retains the existing proof gates and marks original
  and correction tasks done only after successful promotion.

#### Negative Criteria

- NC-001: A failed review must not be represented as passing evidence, promote
  a known-bad integration, reset or rebuild worker history, or authorize the
  coordinator to write product fixes.
- NC-002: Corrections must not depend on unpromoted future work, overwrite an
  existing assignment or result, change the persistent lane before promotion,
  or bypass normal worker commit and result validation.

#### Validation Method

Use disposable persistent-lane, integration, and worker worktrees to retain a
reviewed integration, append correction tasks, dispatch from the exact sealed
head, merge the resulting direct-child commits, and compare lane and wave state
before and after the final promotion.

#### Test Method

Cover exact retained state, stale or dirty integration and lane rejection,
indexed-task mutation, future dependency rejection, plan-digest advancement,
historical assignment validation, correction dispatch, ordered re-integration,
fresh evidence, one fast-forward promotion, cleanup, and interrupted replay.

#### Evaluation Method

Reproduce a blocking combined-review finding in a disposable retained run,
append a worker-owned correction, and independently verify that the lane does
not move until the corrected integration passes review and promotes once.

<!-- /REQUIREMENT: REQ-017 -->

<!-- REQUIREMENT: REQ-018 status=active priority=P0 type=feature -->
### REQ-018: Resume interrupted Task Implementer runs from authoritative state

#### User Story

Task Implementer users need to rerun the same accepted prompt after any
interruption without duplicating completed work, losing retained evidence, or
requiring knowledge of private coordinator commands.

#### Acceptance Criteria

- AC-001: Repeating public `run` for the same prompt revision derives one
  bounded outcome from authoritative coordinator-v7, wave, task, immutable,
  Git, Worktree, lifecycle, and lease evidence: execute one exact transition,
  wait for a proven active owner, require stop confirmation, block on
  ambiguity, or report the unchanged run complete.
- AC-002: Coordinator-owned transitions use a durable monotonic intent record
  whose pre-state digest, expected effects, phase, and terminal digest prevent
  duplicate external effects and make interruption before or after state
  publication independently recoverable.
- AC-003: A current coordinator-v7 run without the new intent record is
  adopted only at a fully validated stable boundary. Partial, malformed,
  journal-inconsistent, dirty, or ownership-ambiguous state remains retained
  and fails closed with one minimum recovery action.
- AC-004: Resume planning is read-only. Execution reacquires every owning lock,
  recomputes the observed-state digest, and holds the resume-execution and
  reentrant project-scope locks through the selected transition and projection
  commit. It replans instead of mutating when another writer advanced the run.
- AC-005: Machine-readable coordinator, wave, task, immutable, interop, lease,
  and Git state owns effective execution status after coordinator creation.
  Human handoff status is a compare-and-swap projection that is reconciled
  after every transition without overwriting unconsumed correction input or
  user-authored history.
- AC-006: Final completion is published only after promotion, cleanup, external
  lease release, local interop reconciliation, handoff projection, and queued
  activation each have exact-once evidence.
- AC-007: Lifecycle continuation uses the project-contract owner's explicit
  state transition and never assumes a synthetic Stop continuation emits
  `UserPromptSubmit`. A required fresh runtime returns one precise reload
  boundary while preserving the run.
- AC-008: Promotion-review corrections become immutable, digest-addressed
  `pending-plan-v1` input bound to the active resume epoch and token before
  coordinator, wave, or task-plane publication. A retry consumes the same
  bytes regardless of later live Markdown edits, and another epoch cannot
  inherit an abandoned staged plan.
- AC-009: Every behavior-affecting coordinator argument is stored canonically
  and digest-bound. An interrupted intent replays only those arguments;
  effect-observed and state/projection-committed phases finish reconciliation
  without rerunning the mutation. Finalization binds fresh alignment evidence,
  and confirmed recovery binds explicit stop authority before changing state.
- AC-010: The lightweight deterministic verifier gives the real linked-
  worktree regression suite a bounded budget that exceeds its validated
  runtime; normal long-running crash coverage must not be reported as a test
  failure solely because an obsolete verifier timeout expired.
- AC-011: A new run bootstraps resume control under the execution and scope
  locks before its first controlled mutation. Every successful controlled
  response carries the next authoritative outcome, canonical arguments, and
  token when executable, so the coordinator never needs to infer or silently
  bypass the resume boundary between transitions.
- AC-012: Replanning computes the coordinator plan identity from the complete
  final ordered wave index, including every retained completed wave and the
  replacement correction tail. A retained current-v7 run written with only a
  deterministic replacement-tail digest is recoverable only through one
  owner-invoked, exact-digest, journaled repair that revalidates the completed
  prefix, replacement wave IDs, wave/task consistency, prompt-impact basis,
  and absence of assigned or running replacement workers before updating the
  plan basis and coordinator in recoverable order. Repeating an unchanged
  retained-prefix replan is byte-idempotent and never blocks its active
  replacement wave; successful recovery proceeds through ordinary resume
  adoption and its controlled next transition. If canonical spec receipt bytes
  advanced before recovery, the owner first refreshes the ready refinement's
  compiled managed-requirements digest; recovery republishes the current
  validated impact, accepts only `retain_plan`, and binds that exact impact into
  the repaired plan basis before the coordinator write.
- AC-013: When a sealed selected-lane lifecycle reconciliation leaves only the
  canonical requirements/design pair and provenance-owned `AGENTS.md` dirty
  during an active promotion review, one owner-invoked adoption binds the exact
  lifecycle and file digests, journals a single coordinator-owned integration
  commit, and treats that overlay as admissible only while the lane bytes and
  integration blobs remain identical. Promotion journals the temporary lane
  cleanup and either completes the fast-forward or restores the exact adopted
  bytes after interruption. No product path, arbitrary dirt, staged state, or
  unsealed lifecycle is admitted.
- AC-014: A promotion-review correction graph may span multiple dependency
  frontiers. Replanning appends only the currently dependency-ready frontier
  to the retained integration, dispatches it from the latest sealed integration
  head, and repeats after that frontier is merged. A future resource-free task
  may update only its dependencies to the newly indexed correction chain; its
  remaining immutable plan and claims stay unchanged.
- AC-015: Handoff projection maps every machine task state to the canonical
  human task-status vocabulary before publication. Unpromoted `committed` and
  `merged` planes remain `in_progress`; only a promoted or cleaned wave becomes
  `done`. A current-v7 handoff written with the unsupported historical
  `committed` projection is recoverable only through one owner-invoked,
  caller-digest-bound, journaled rewrite that validates coordinator, wave, and
  task-plane identity before normal repeated resume parses the handoff again.
- AC-016: Worker arm, start, recovery, commit, and result-publication responses
  return transient structured contexts containing the exact canonical cwd,
  argv, raw session binding where required, immutable assignment identity, and
  private result target. Coordinators and workers consume those values without
  reconstructing visually similar private paths or substituting persisted
  fingerprints for raw command inputs.
- AC-017: The canonical worker-result publisher validates assignment identity,
  exact changed paths, commit and tree evidence, stable schema, and destination
  before computing the final digest and publishing the immutable mode-0600
  result atomically. A malformed, partial, foreign, or digest-mismatched draft
  leaves no final result file.
- AC-018: An explicit public `run` may repair only a stale generated VS Code
  Python launcher caused by removal of its formerly resolved executable. The
  repair reuses canonical workspace initialization, preserves lane identity,
  prompts, runs, and orchestration state, then repeats full workspace
  verification before intake. Every other workspace mismatch remains
  fail-closed, and non-mutating `workspace reuse` never performs this repair.
- AC-019: A current-v7 assignment must match the current canonical worker
  guardrails until its first successful `task-start`. After start binds the
  exact immutable assignment digest and worker session into the task plane,
  later source-only guardrail wording growth must not strand that running,
  committed, or merged assignment. Resume still revalidates every other
  assignment, task, handoff, Git, Worktree, and lease identity and returns the
  current transient recovery and result-publication contexts without rewriting
  the accepted assignment.

#### Negative Criteria

- NC-001: Repeated `run` must not create another run, prompt revision,
  assignment, worker, branch, worktree, commit, merge, promotion, generation,
  or queue activation for an already accepted identity.
- NC-002: Stale heartbeats, process identifiers, elapsed time, Markdown status,
  or missing local paths alone must not prove worker termination or authorize
  cleanup, recovery, promotion, or finalization.
- NC-003: The recovery contract must not add a sixth public Task Implementer
  action, a public resume flag, support for coordinator-v1 through v6, a
  compatibility execution path, or coordinator-authored product changes.
- NC-004: An interrupted transition must not be abandoned in order to begin a
  second transition, and ambiguous Git or Worktree evidence must not be reset,
  pruned, force-cleaned, or silently reconstructed.
- NC-005: Plan-digest recovery must not infer an expected digest, rewrite a
  task or worker plane, change Git, Worktree, lease, promotion, or public
  workflow state, run after resume-control adoption, accept an ambiguous
  replacement suffix, follow a symlink or non-file recovery artifact, appear
  in ordinary helper help, repair a stale refinement, accept material
  `replan_required` impact, or become a legacy coordinator compatibility layer.
- NC-006: Contract-delta adoption must not generalize lane cleanliness, move or
  stage the persistent-lane index, accept README, changelog, product, untracked,
  deleted, renamed, symlinked, or partially reviewed paths, copy private
  lifecycle bytes into Git, or lose the lane overlay before its exact bytes are
  durable in the retained integration.
- NC-007: Projection recovery must not accept arbitrary Markdown edits, change
  machine state, infer completion, rewrite pending correction content or
  narrative, expose a public action, or retain `committed` as a second accepted
  handoff status.
- NC-008: Transient raw session IDs, start leases, or canonical argv must not be
  persisted in assignment, coordinator, authorization, prompt, handoff, or
  durable run state beyond the already required hashed ownership evidence.
- NC-009: Generated-launcher repair must not accept a user-edited task, change
  a lane incarnation, refresh source content, mutate Git, advance a run, scan
  other workspaces, or broaden recovery from the exact missing-executable
  failure.

#### Validation Method

Use isolated repositories, Codex homes, Worktree state, and subprocess fault
injection to compare authoritative identities before and after repeated public
run evaluation at every persistent and external-effect boundary.

#### Test Method

Cover interruption during intake, revision settlement, checkpoint, generation
open, plan publication, preparation, dispatch, worker execution and result
publication, integration, promotion, cleanup, final release, handoff
projection, queue activation, lifecycle continuation, and concurrent resume.
Include journal-less coordinator-v7 adoption, stale projections, file and
directory sync failures, partial journals, storage exhaustion, retained
completed prefixes with deterministic replacement-tail digests, interrupted
plan-basis-first and coordinator-first repair completion, broken-symlink and
  live-worker rejection, hidden owner-command routing, repeated unchanged
  replanning, spec-receipt-only retain-impact refresh, stale-refinement and
  material-impact rejection, sealed contract-delta adoption, interrupted
  promotion cleanup restoration, multi-frontier correction chaining, future
  dependency-only rebinding, canonical unpromoted task projection, exact
  unsupported-status recovery, repeated ordinary resume, and unchanged
  terminal replay. Change canonical worker guardrail wording after a task has
  started and prove that its exact accepted assignment remains resumable while
  an unstarted assignment with the same source drift stays fail-closed.

#### Evaluation Method

Interrupt a disposable run immediately before and after each durable write or
external effect, restart in a fresh process with the same prompt reference,
and confirm it either resumes from the first unproved transition or fails
closed without changing evidence. Confirm an unchanged completed objective
returns `ALREADY_COMPLETE` and a changed prompt creates a linked new revision.

<!-- /REQUIREMENT: REQ-018 -->

<!-- REQUIREMENT: REQ-019 status=active priority=P0 type=feature -->
### REQ-019: Select a least-agentic, provider-portable AI architecture

#### User Story

AI application designers need one architecture that separates direct model
calls, deterministic workflows, bounded agents, and specialist harnesses while
keeping provider choice, state, tools, policy, and operations replaceable.

#### Acceptance Criteria

- AC-001: `design` classifies every AI-enabled capability as a direct model
  call, a deterministic workflow containing model calls, or an agent before it
  finalizes component boundaries and control flow.
- AC-002: A direct call is preferred when one model request can solve the task;
  a deterministic workflow is preferred when the application knows the
  operation sequence; an agent is selected only when the model must choose an
  action, continuation, or next step from prior observations, including an
  unknown step count whose continuation is model-controlled, or the workload
  requires a model-driven observe-act-observe loop. An unknown iteration count
  remains deterministic when application code owns the continuation rule.
- AC-003: Delegation, persistent sessions, and specialist coordination are
  supporting agent-runtime triggers rather than sufficient reasons to make the
  whole application agentic. Latency and cost predictability bias the decision
  toward direct calls and deterministic workflows.
- AC-004: One application may use all three levels concurrently, with ordinary
  application logic, databases, APIs, RBAC, approvals, and other deterministic
  controls remaining outside model discretion.
- AC-005: Pydantic AI is the default portable Python model and agent runtime.
  Direct-call behavior uses its low-level direct model API when the caller
  needs `ModelResponse` control, or a no-tools single-request `Agent` when
  typed output parsing, validation retries, typed dependencies, or similar
  request services are required. That primitive choice does not make the
  behavior agentic; its `Agent` loop is preferred when the model owns actions
  or continuation. Intentionally provider-specific direct calls may use the
  provider's official task API. Provider-specific model features remain
  explicit, isolated settings rather than lowest-common-denominator behavior.
- AC-006: The application owns a stable logical runtime facade, canonical
  session and run state, tool and policy contracts, authorization, budgets,
  routing governance, telemetry, evaluation, and release identity. These may
  begin in-process; separate services, gateways, registries, queues, or stores
  require an operational or scale trigger. Resume and cancel requests carry
  verified principal and tenant context, expected run-state version, and
  idempotency identity; resume also carries the exact approval or external-
  event receipt when applicable. The facade reauthorizes and applies these
  mutations through an optimistic-concurrency transition.
- AC-007: Model selection uses a versioned capability catalog that separates
  functional support, operational health, and governance eligibility. Routing
  applies hard data, tenant, region, capability, context, health, and quota
  filters before scoring quality, latency, cost, health, or affinity.
- AC-008: A provider-native agent SDK is a specialized runtime escape hatch,
  not a model adapter. Its use requires a documented workload dependency on
  unique harness semantics, stays behind the application runtime facade, and
  retains platform policy, state, tool, budget, audit, and evaluation controls.
- AC-009: Tool adapters remain thin AI-facing bindings over typed domain APIs
  or a policy-enforcing tool gateway. Authorization, approval, idempotency,
  audit, output bounds, and side-effect semantics are deterministic. MCP is an
  interoperability boundary and never a trust boundary.
- AC-010: Routing and fallback remain separate. One layer owns each retry
  class, cross-provider fallback is allowed only for classified replay-safe
  failures, and irreversible effects are never replayed without domain-owned
  idempotency and durable progress evidence.
- AC-011: Specialist agents have bounded input, output, tool, security, step,
  cost, and evaluation contracts. Pydantic AI capabilities or Harness are
  conditional options for portable specialists; a coding-focused specialist
  may use the Codex SDK, and provider-native harnesses require the AC-008
  escape decision.
- AC-012: Control-flow autonomy is classified independently from orchestration
  and durability. An explicit graph or durable workflow may wrap direct calls,
  deterministic workflows, agents, or a mixture; graph, checkpoint, timer,
  approval, or compensation requirements do not by themselves require an
  agent. When durability is required, Temporal, DBOS, Prefect, Restate, or an
  existing qualified owner is selected from workload evidence rather than
  mandated by the presence of an agent.
- AC-013: The decision rule, runtime and provider boundaries, specialist
  choices, source links, skill metadata, trigger evaluations, READMEs, root
  catalog, project design, and changelog remain aligned.

#### Negative Criteria

- NC-001: A design must not route every AI-enabled behavior through one agent
  or let a model decide deterministic business logic, authorization, approval,
  data ownership, or known workflow transitions.
- NC-002: Tool use, multiple model calls, or a long task must not by itself
  classify a workflow as agentic when application code can determine the next
  operation or whether a deterministic loop should continue.
- NC-003: OpenAI-compatible wire shape must not be treated as proof of OpenAI
  Responses API, tool, session, or agent-runtime semantic compatibility.
- NC-004: Pydantic AI must not be wrapped around a provider-native agent
  runtime, and the platform must not duplicate Pydantic AI model, provider,
  profile, message, or tool semantics without a proven cross-runtime need.
- NC-005: Provider sessions, caches, or compaction must not be the only durable
  representation of conversation, workflow, approval, or side-effect state.
- NC-006: A decorator, prompt, provider session, authenticated MCP connection,
  or model-generated plan must not become the authorization or transaction
  boundary.
- NC-007: PostgreSQL, Redis, a vector database, a gateway, Kubernetes, a
  durable workflow engine, or multi-agent topology must not become mandatory
  merely because the application uses Pydantic AI or agents.
- NC-008: Codex app-server must not be required for ordinary CI or background
  automation when the Codex SDK supplies the needed coding-thread interface.
- NC-009: LangGraph, Temporal, or another orchestration runtime must not be
  placed downstream of an agent as though agentic autonomy were a prerequisite
  for graph or durability semantics.

#### Validation Method

Run skill-structure, Markdown, traceability, changed-scope review, security
review, and static trigger-evaluation checks over `ai-stack`, the root catalog,
project specs, and changelog. Recheck version-sensitive Pydantic AI, OpenAI,
Anthropic, Google, and interoperability claims against current official
documentation.

#### Test Method

Use should-trigger and quality scenarios for low-level and typed direct calls,
known multi-step workflows, dynamic tool choice, provider-specific features,
provider-native escape decisions, policy-owned routing, replay-safe fallback,
authenticated resume and cancel races, durable approvals, Pydantic AI
specialists, and a Codex engineering specialist. Confirm each selects the
lowest sufficient level and preserves the application-owned state, policy, and
tool boundaries.

#### Evaluation Method

Review a representative multi-provider application containing deterministic
logic, general AI work, side-effecting tools, and specialist agents. Confirm
Pydantic AI is the portable default without becoming a mandatory distributed
platform, native runtimes are justified exceptions, and every selected runtime
or component has a requirement, evidence state, owner, acceptance gate, and
revisit trigger.

<!-- /REQUIREMENT: REQ-019 -->

<!-- REQUIREMENT: REQ-020 status=active priority=P0 type=feature -->
### REQ-020: Report concise Task Implementer lane progress from machine evidence

#### User Story

Task Implementer users need one short, human-readable view of the persistent
lane, current or latest run, temporary workers, promoted work, remaining work,
and next public action without exposing changed files or disturbing execution.

#### Acceptance Criteria

- AC-001: Successful `run` completion seals a versioned `run-summary-v1` with
  task/correction/wave and temporary-resource totals, combined validation and
  review, lane promotion/release, source observation, queued-prompt status, and
  a structured next action. Its immutable finalization contract is unchanged.
- AC-002: Internal `lane-report` returns hard-cut
  `task-implementer/lane-report-v2` with current or latest run progress,
  persistent-lane generation totals, a bounded current step, remaining steps,
  and one structured next public action.
- AC-003: Current-run task totals come from coordinator-v7 wave indexes. A task
  is promoted only when its owning wave records a valid `promoted_head`;
  committed or merged work remains in progress until that boundary.
- AC-004: Temporary-worker totals describe logical worker assignments from
  task-plane-v5. The report distinguishes planned, created, queued, active,
  finished, failed, and superseded assignments without inferring process
  liveness or enumerating worktree paths.
- AC-005: Wave totals, active-wave identity, and promotion progress come from
  validated wave-v4 and coordinator-v7 state. Released, integrated, pending,
  active, and finalization-pending generation totals remain a separate lane
  backlog rather than an aggregate of incomplete historical run evidence.
- AC-006: The source head is recorded before workers start and completion says
  only `unchanged`, `moved`, or `unknown_legacy`. Movement warns that public
  integration rebuilds and revalidates its candidate.
- AC-007: Summary bytes are prepared and digest-bound before generation
  release, sealed unchanged after release, projected to the handoff before
  queue activation, and returned exactly by `ALREADY_COMPLETE`. Interrupted
  finalization is idempotent and blocks another generation until complete.
- AC-008: Clean source and lane state reports the exact primary-project
  `$task-implementer integrate "<primary-project-path>"` command. A dirty
  primary first directs the user to invoke `$commit` there.
- AC-009: The generated workspace labels its unchanged lane path
  `CODE — MANAGED PERSISTENT LANE` and supplies one manual
  `Task Implementer: Show Lane Status` task. It renders concise human text by
  default, retains explicit JSON for machine callers, and uses a cleared
  dedicated terminal without echoing the invocation or stealing focus.
- AC-010: Generated workspace preflight classifies current, the exact previous
  four-task generated shape, or tampered. Init/run migrate only that exact
  previous shape; reuse is non-mutating and requests upgrade; tampering fails
  before mutation.
- AC-011: Reporting takes two complete bounded observations and emits only a
  matching validated snapshot. One retry is allowed; continued movement
  returns `WORKSPACE_BUSY` without partial counts, a lock-file write, or
  blocking the Task Implementer writer.
- AC-012: Before coordinator-v7 exists, the report says planning is in progress
  and totals are unavailable. When no run is active it uses only the latest
  sealed pending summary, and legacy-only history remains explicitly
  unavailable rather than reconstructed.
- AC-013: Human and JSON lane reports contain no changed-file information,
  file paths, comparison statistics, commits, branch names, prompt content,
  private identities, or embedded run summaries, and their size is bounded.
- AC-014: The public Task Implementer interface remains exactly five actions;
  hooks and the Worktree mutation API are unchanged, and `lane-report` receives
  no coordinator privilege.

#### Negative Criteria

- NC-001: Lane reports must not expose or compute raw diffs, changed-file
  totals, changed paths, prompt bodies, private run/lane IDs, private state
  paths, recovery artifacts, or reconstructed historical summaries.
- NC-002: Reporting must not ensure, refresh, checkpoint, integrate, chmod,
  create or acquire a writable lock, inspect lease resources through a writer,
  create an overlay, dirty a checkout, or otherwise mutate lane, source, Git,
  prompt, run, Worktree, lease, or hook state.
- NC-003: A forged helper/Python path, extra folder/task, changed argument, or
  released-but-unsealed summary must not be accepted as current or permit a new
  generation.
- NC-004: Worker counts must not claim operating-system process liveness or
  physical worktree presence, and committed or merged tasks must not be shown
  as promoted.

#### Validation Method

Run reporting, workspace, wave, resume, interoperability, Worktree, lifecycle
hook, contract, lint, skill-alignment, and changed-surface alignment checks in
disposable repositories and isolated Codex homes.

#### Test Method

Cover planning without a coordinator, every task and wave state, worker arm
and start distinctions, blocked and superseded tasks, promotion and cleanup,
every finalization phase, legacy-only pending history, source integration,
lane removal, exact prior-shape migration, stale Python, forged helpers,
arbitrary tampering, moving snapshots, output bounds, and zero-mutation
fingerprints across private state, Git, Worktree, leases, and lock metadata.

#### Evaluation Method

Complete and replay a disposable multi-wave run. Confirm the human report
shows promoted and remaining progress without file information, the JSON view
matches the same counts, committed work remains unpromoted, an unstable read
fails without interference, and public integration or removal changes only the
expected generation and next-action projection.

<!-- /REQUIREMENT: REQ-020 -->

<!-- REQUIREMENT: REQ-021 status=active priority=P0 type=feature -->
### REQ-021: Align skills with lean authoring and target-specific evaluations

#### User Story

Skill authors need `align-skill` to make each selected skill concise,
well-scoped, safely executable, and independently evaluable without imposing a
costly migration or runtime benchmark on the entire skill catalog.

#### Acceptance Criteria

- AC-001: Every authorized writable skill that completes alignment has one
  canonical `evals/trigger-prompts.csv` containing at least three realistic
  should-trigger prompts and three meaningful near-miss should-not-trigger
  prompts with unique non-empty IDs and prompts.
- AC-002: The structural validator accepts an independent `--require-evals`
  flag. Default validation permits legacy skills without evaluations while
  validating any canonical trigger CSV that exists; strict validation requires
  the canonical CSV for every selected skill.
- AC-003: Strict validation rejects missing or dual trigger authorities,
  malformed headers or boolean labels, blank fields, duplicates, and
  insufficient positive or negative cases without adding a legacy
  compatibility path.
- AC-004: Alignment reviews descriptions for a concise job and outcome,
  triggering intent, routing-relevant inputs or modes, and adjacent negative
  boundaries without requiring literal checklist words in always-loaded
  metadata.
- AC-005: Alignment keeps `SKILL.md` directive-first and focused on trigger,
  scope, workflow, guardrails, validation, and output. It moves deep domain or
  workflow guidance into focused one-level references, preserves rationale that
  affects decisions or safety, and treats more than 500 lines as a review
  warning rather than an automatic failure.
- AC-006: Each workflow block uses the least prescriptive safe form: goals and
  constraints when variation is safe, bounded defaults when a preferred route
  allows judgment, and tested scripts or exact sequences for fragile,
  repetitive, or deterministic behavior.
- AC-007: Material workflow or output changes receive realistic output-quality
  cases with expected results and concrete assertions, or deterministic tests
  when the intended outcome is fully machine-verifiable. Description-only or
  invocation-only changes require trigger coverage but not an artificial
  output benchmark.
- AC-008: Alignment reports static definition validation, fresh-runtime trigger
  observations, and output-quality comparisons as separate evidence lanes with
  explicit pass, fail, not-run, or unavailable states. Static prompt files are
  never represented as executed runtime evidence.
- AC-009: Report-only, remote, or unauthorized targets remain unchanged and are
  reported as partial or missing evaluation coverage rather than fully aligned.
- AC-010: Changes to skill guidance are checked against current official
  product documentation, focused validator tests, review, security, lint, and
  changed-surface alignment without installing the complete dirty source tree.
- AC-011: When `SKILL.md` exceeds 500 lines or is structurally overloaded,
  alignment classifies each instruction block by runtime ownership, preserves
  decision-changing and safety-critical content before moving or removing it,
  and either refactors by progressive disclosure or records a specific
  justified exception.
- AC-012: Long-skill review always records before-and-after logical line counts
  with the validator-compatible `splitlines()` method and records comparable
  token cost only when a compatible tokenizer or runner exposes it. Missing
  token evidence is reported as unavailable and never replaced by a mandatory
  dependency or approximate failure threshold.
- AC-013: Length alone never causes a skill split. Independently triggered jobs
  with distinct outcomes are routed toward separate skills, and initial new
  skill scaffolding remains owned by `skill-creator`.
- AC-014: Working-byte baselines use owner-only task storage and are removed by
  exact scoped cleanup after comparison, or retained only with a documented
  reason, permissions, cleanup owner, and deadline.
- AC-015: The canonical trigger CSV is a target-owned regular file. Validation
  rejects symlinks in its skill-relative path and any resolved path outside the
  skill before reading content, and malformed-case diagnostics identify rows
  without echoing IDs or prompts into logs.

#### Negative Criteria

- NC-001: Basic catalog validation must not require immediate evaluation
  migration for every existing skill.
- NC-002: Alignment must not add empty evaluation boilerplate, brittle semantic
  regex checks, a hard 500-line limit, or duplicate Markdown and CSV trigger
  authorities.
- NC-003: A missing live runner or baseline must remain visibly unverified and
  must not be converted into a runtime or quality pass.
- NC-004: Removing passive prose must not discard decision-changing rationale,
  safety constraints, domain knowledge, or validation requirements.
- NC-005: Alignment must not mechanically summarize every section, move
  always-needed guardrails behind conditional reads, split a skill only to
  satisfy a size target, or treat unavailable token evidence as a failure.
- NC-006: Alignment must not use a line-count method that disagrees with the
  validator, leave temporary working-byte snapshots behind silently, or send
  private target content to an external tokenizer or runner without
  authorization.
- NC-007: Validation must not follow an eval path outside the selected skill or
  disclose raw trigger IDs or prompts in failure output.

#### Validation Method

Run the validator self-test, strict validation of `align-skill`, default
validation of the source catalog, portable upstream validation, Python and
Markdown checks, focused code and security review, project-spec validation,
and final changed-surface alignment.

#### Test Method

Cover strict and default modes; valid and missing evaluations; malformed CSV
headers, labels, blanks, duplicates, and case counts; Markdown-only and dual
trigger authorities; external file and directory symlinks; redacted duplicate
diagnostics; 500/501-line warning behavior; stateful-profile composition;
semantic 700-line refactoring and justified-exception cases; and the unchanged
real-source catalog regression.

#### Evaluation Method

Review `align-skill` against positive prompts for existing-skill alignment and
near-miss prompts that belong to new scaffolding, whole-project alignment, or
ordinary implementation. Compare material output changes against the captured
pre-edit skill snapshot when a clean evaluation runner is available, and
report unavailable runtime or compatible token evidence separately from static
readiness. Include a mixed 700-line target and a safety-critical target whose
justified core remains over the soft budget.

<!-- /REQUIREMENT: REQ-021 -->

<!-- REQUIREMENT: REQ-022 status=active priority=P0 type=feature -->
### REQ-022: Admit bounded commit-push Git effects without weakening lifecycle safety

#### User Story

Skill users need an explicitly authorized `commit-push` workflow to inspect its
fixed remote and publish the current branch without the selected-project
lifecycle misclassifying those known Git effects as ambiguous project writes.

#### Acceptance Criteria

- AC-001: Exact read-only Git query shapes used by `commit-push`, including
  version, remote URL, symbolic-ref, remote-ref listing, show-ref, and rev-list
  queries, are non-material only when their options cannot write configuration,
  refs, worktree files, or hook state.
- AC-002: One exact fetch from `origin` for the literal current branch may
  update only its matching remote-tracking ref and object database while
  suppressing `FETCH_HEAD`, tags, automatic maintenance, and commit-graph
  writes. The selected-project lifecycle treats that bounded Git control-plane
  effect as external and epoch-neutral only when no active
  reference-transaction hook can introduce hidden project effects.
- AC-003: One exact non-force push from `HEAD` to the same literal current
  branch on `origin`, optionally setting that branch's upstream, is external to
  the selected-project contract only when no active pre-push or
  reference-transaction hook can introduce hidden project effects.
- AC-004: A dirty `commit-push` workflow obtains its local commit through the
  existing claim-bound whole-repository commit transaction. Its explicit
  invocation can authorize that transaction without admitting raw `git add` or
  `git commit`.
- AC-005: Force, delete, mirror, all, tags, wildcard, arbitrary-remote,
  arbitrary-refspec, URL-targeted, dynamic, composed, hook-bearing, or otherwise
  ambiguous fetch and push commands remain denied by the project lifecycle.
- AC-006: Source tests prove the recognized and rejected command matrix without
  contacting a remote or mutating the developer checkout. Installed parity,
  hook trust, restart, fresh-session behavior, and a real push remain separate
  rollout gates.
- AC-007: The relevant fetch or push URL for `origin` resolves to exactly one
  HTTPS, SSH, or scp-style network target with a strictly validated authority.
  Local paths, file or external-helper transports, option-like authorities,
  embedded whitespace, and multiple URLs remain ambiguous.

#### Negative Criteria

- NC-001: Do not make generic `git fetch`, `git push`, `git config`, `git
  remote`, or `git symbolic-ref` read-only or lifecycle-exempt.
- NC-002: Do not infer safety only from an external working directory, because
  Git effects are rooted in repository metadata and remote refs rather than the
  process cwd.
- NC-003: Do not bypass repository-owned publication, Worktree, Task
  Implementer, Agentic SDLC, credential, or remote-policy controls.
- NC-004: Do not run a live push as part of source-level regression testing.

#### Validation Method

Run focused lifecycle-hook and commit authorization tests, disposable-repository
Git-effect tests, Python and Markdown checks, project-spec validation, security
and code review, changed-surface alignment, and repository hygiene checks.

#### Test Method

Cover every admitted read query, constrained current-branch fetch, push with
and without upstream setup, missing or active pre-push and
reference-transaction hooks, detached head, branch mismatch, alternate remotes,
unsafe or multiple URLs, unsafe flags, multiple or wildcard refspecs, command
composition, and raw Git mutations.

#### Evaluation Method

Reproduce the original command classifications against the source hook in a
disposable repository. After separately authorized installation and restart,
repeat the same preflight in a fresh session before any separately authorized
real publication.

<!-- /REQUIREMENT: REQ-022 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
