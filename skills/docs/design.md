<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v1 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001,REQ-005 status=ready priority=P0 version=1 -->
### FEAT-001: Source-owned skill and installation boundary

#### Requirements Covered

- REQ-001: Keep reusable skill source portable and install user skills into the canonical Codex discovery root.
- REQ-005: Treat source, installation, and fresh-runtime activation as separate proof surfaces.

#### Context Evidence

`README.md` catalogs source skills and invocation policy. `install-skills.sh`
discovers directories containing `SKILL.md`, records source ownership, and
defaults user installations to `${HOME}/.agents/skills`. Skill-local tests and
the installer idempotency suite verify those boundaries.

#### Design Details

The repository remains the semantic source of public skills. The installer
copies a reviewed skill into one selected user root, preserves unmanaged and
differently owned entries, and records source identity for safe convergence.
Hook payloads remain a separate opt-in installation into
`${CODEX_HOME}/hooks`; hook registration and trust are not implied by skill
installation.

#### Selected Option

Use `${HOME}/.agents/skills` as the single default user-skill installation and
discovery root, matching current Codex behavior and avoiding parallel installed
copies.

#### Alternatives Considered

Duplicating user skills under `${CODEX_HOME}/skills` would satisfy a stale hook
assumption but introduce two authorities and ongoing drift. Repository-only
execution would bypass installed-runtime review and is rejected.

#### Implementation Boundaries

Owned surfaces are `install-skills.sh`, the root catalog and changelog,
skill-local metadata, and focused installer tests. Runtime hooks and private
state remain outside Git.

#### Test-First Success Criteria

- TDD-001: A disposable default installation lands under
  `${HOME}/.agents/skills`, reruns idempotently, and preserves foreign entries.
- TDD-002: Hook installation reports changed files separately and requires
  restart/trust review without moving the canonical skill root.

#### Validation Plan

Run shell syntax checks, installer contract tests, and source-to-install parity
checks before any runtime claim.

#### Test Plan

Exercise default and explicit destinations, source ownership, idempotent
updates, stale-source cleanup, and unrelated destination preservation.

#### Evaluation Plan

Verify source, installed files, hook registration, and fresh runtime as four
explicit evidence gates.

#### Rollout And Rollback

Use the reviewed installer with recoverable hook backups. If installation
validation fails, retain source changes and restore only the exact backed-up
runtime hook before restarting.

#### Done Definition

The canonical root, installer behavior, documentation, tests, and reported
activation boundary agree without a duplicate installed skill tree.

<!-- /FEATURE: FEAT-001 -->

<!-- FEATURE: FEAT-002 reqs=REQ-002,REQ-003 status=ready priority=P0 version=4 -->
### FEAT-002: Receipt-bound project lifecycle and coordinator trust

#### Requirements Covered

- REQ-002: Enforce canonical planning, implementation, reconciliation, and sealing through one owner lifecycle.
- REQ-003: Admit only canonical reviewed coordinator commands.

#### Context Evidence

`maintain-project-specs/scripts/project_specs.py` owns inspection, migration,
validation, planning, opening, waivers, and sealing.
`maintain-project-specs/assets/hooks/project_specs_lifecycle.py` binds selected
scope, phases, coordinator commands, material writes, and Stop arbitration.
`project-agent-instructions` owns conditional project `AGENTS.md` rendering and
the terminal apply/verify decision.

#### Design Details

The hook records bounded private lifecycle state and classifies tools before
execution. Ordinary writes are admitted only by phase and selected-project
scope. Coordinator commands form a narrow exception: the hook verifies the
interpreter, resolved script path, owner action, project root, and required
evidence before allowing the command. Project-instructions planning, apply,
verification, and sealing are additionally bound to the exact canonical
current-session private bundle; another same-project bundle cannot substitute
different decision evidence after planning. A coordinator-shaped
command with invalid or noncanonical binding is denied instead of falling
through as a non-material Python invocation. An installed hook resolves those
scripts from the canonical `${HOME}/.agents/skills` root. Source tests inject
or derive an explicit source-bundle root; a copied installed hook never infers
repository trust from its runtime location.

`PostToolUse` remains registered and records every successful material
selected-project write by advancing the write epoch and moving or retaining the
`reconciliation-required` phase. Routine successful recording returns no hook
context and carries no static status message. Recording failures and the one
terminal project-instructions apply transition remain visible. Concurrent
recorders retry the compare-and-swap transition from freshly loaded active
implementation state, so each successful tool advances the epoch. A successful
material tool admitted before a concurrent plan or seal transition but recorded
afterward advances the epoch and returns the lifecycle to
`reconciliation-required`, invalidating the later evidence rather than allowing
a stale seal. The Stop delegate is the sole routine reconciliation prompt and
directs one review of the accumulated selected-project status, staged and
unstaged diff, untracked implementation files, current task context, and
accepted user intent.

Every lifecycle prompt names the terminal artifact as a
`project-instructions decision`, not as an `AGENTS.md` mutation. A missing
target plus an evidence-backed `not-needed` disposition produces an empty
render, verified private final state, and no repository file. Messages and
completion reports state that result explicitly. `needed` remains the only
missing-target disposition that creates `AGENTS.md`; the hook never chooses
between those semantic outcomes.

The semantic owner, not the hook, classifies that final evidence. Accepted
observable behavior, constraints, non-goals, or acceptance criteria update
requirements. Component boundaries, interfaces, data or control flow, failure
handling, security, operations, rollout, or meaningful implementation evidence
update design. Refactors, formatting, test-only edits, reverted changes, and
bug fixes that restore an existing documented contract may leave both specs
byte-identical. Ambiguous impact remains unsealed. In every outcome, validation
and planning advance `planned_write_epoch` to the current `write_epoch` before
the terminal project-instructions apply, verify, and seal sequence.

#### Selected Option

Resolve trusted installed coordinators from the canonical user-skill root,
retain a distinct source-test binding, keep PostToolUse as a silent evidence
recorder, consolidate semantic reconciliation at Stop, and describe the final
project-instructions outcome without implying unconditional file creation.
Bind terminal decision evidence to the canonical current-session private
bundle, and let late successful write accounting invalidate later planning or
sealing evidence. Keep the existing fail-closed command contract and phase
machine.

#### Alternatives Considered

Keeping `${CODEX_HOME}/skills` as a second default contradicts the installer
and Codex discovery contract. Trusting any script inside the current repository
would permit unreviewed worktree changes to obtain coordinator authority.
Removing PostToolUse would lose post-success failure discrimination and stale
epoch protection. Per-edit semantic inference or a persistent change-content
journal would make hooks document authors, duplicate Git evidence, and expand
private-state migration and privacy risk.

#### Implementation Boundaries

Owned changes are the lifecycle hook's coordinator-root resolution and output
contract, PostToolUse registration metadata, focused tests and semantic evals,
lifecycle reference documentation, project-instructions outcome wording and
missing-target regression coverage, skill README and runtime instructions,
root installation guidance, and changelog. Canonical owner schemas, lifecycle
phases, receipts, decision semantics, and public coordinator commands remain
unchanged. The public coordinator retains bounded receipt persistence and an
opaque turn-token input while preserving raw turn IDs for direct owner tests.

#### Test-First Success Criteria

- TDD-001: An installed hook under a disposable `${CODEX_HOME}/hooks` admits
  both canonical coordinators under a disposable `${HOME}/.agents/skills`.
- TDD-002: The same hook rejects checkout, basename-spoofed, missing, and
  evidence-mismatched coordinator commands.
- TDD-003: A source-bundle hook test can exercise reviewed source coordinators
  without weakening installed-hook trust.
- TDD-004: Repeated and concurrent successful project writes each advance the
  write epoch and return no routine additional context; failed, external, and
  non-material effects remain epoch-neutral.
- TDD-005: PostToolUse stays registered without a static status message, and
  Stop emits one consolidated semantic reconciliation request.
- TDD-006: Implementation-only and reverted deltas can preserve both spec
  digests, re-plan at the current epoch, and seal successfully.
- TDD-007: Semantic evaluations distinguish requirements-only, design-only,
  combined, neither, and ambiguous impact without hook-owned inference.
- TDD-008: Missing target plus `not-needed` renders and verifies a private
  successful outcome without creating `AGENTS.md`, while lifecycle prompts
  describe this as a decision rather than a promised file mutation.
- TDD-009: Planning, apply, verify, and seal accept only the canonical
  current-session project-instructions bundle; alternate same-project bundles
  and malformed coordinator-shaped commands fail closed.
- TDD-010: A material tool admitted before planning but recorded afterward
  advances the epoch, returns the lifecycle to `reconciliation-required`, and
  prevents stale apply or seal completion.

#### Validation Plan

Run focused hook and owner coordinator tests, semantic reconciliation evals,
syntax and lint checks, changed-scope review, security review, installer
refresh tests, and alignment before installation.

#### Test Plan

Cover every coordinator action and lifecycle phase, both coordinator owners,
canonical and noncanonical roots, path normalization, symlinks, missing files,
interpreter mismatch, selected-project mismatch, repeated PostToolUse output,
late successful material-write recording, unchanged-spec sealing,
missing-target `not-needed`, and semantic reconciliation categories.

#### Evaluation Plan

Reproduce the original `planning-required` rejection using the faulty root,
then confirm the repaired installed path reaches owner `inspect` without
admitting an arbitrary material command. In a separately authorized fresh
runtime, make multiple safe edits and verify no routine reconciliation context
or status text appears before exactly one terminal request. Confirm that the
request calls for an explicit project-instructions outcome and says
`not-needed` leaves a missing `AGENTS.md` absent. Treat any generic
host-rendered hook-completion row as a runtime observation, not source proof.

#### Rollout And Rollback

Install the reviewed hook with a recoverable backup, restart Codex, review and
trust the changed hook, then use a fresh selected-project session. Restore the
exact backup if the fresh probe fails before accepting further project writes.

#### Done Definition

The lifecycle reaches a receipt-bound terminal state using canonical installed
coordinators, repeated writes stay silently accounted, semantic reconciliation
occurs once at Stop, unchanged specs can seal after current-epoch validation,
and noncanonical commands still fail closed.

<!-- /FEATURE: FEAT-002 -->

<!-- FEATURE: FEAT-003 reqs=REQ-004 status=ready priority=P0 version=2 -->
### FEAT-003: Project-owned lifecycle protection boundary

#### Requirements Covered

- REQ-004: Keep the project lifecycle guard authoritative only for selected-project and lifecycle-owned private state.

#### Context Evidence

The project lifecycle hook currently classifies fixed external writes but also
lists config, hooks, task state, and installed coordinator files as protected
control-plane roots. That list extends a selected-project contract into a
laptop-wide write policy and caused the authorized Playwright config patch to
be denied.

#### Design Details

Retain authoritative protection only for the lifecycle's own canonical
`${CODEX_HOME}/project-specs` root. Continue to validate containment, regular
file identity, symlinks, hard links, mixed targets, dynamic targets, and exact
selected-project scope. Fixed effects wholly outside those boundaries pass
through this hook without changing lifecycle state or write epoch. Config,
hooks, task state, installed skills, credentials, and ordinary user files then
remain governed by their real owners: Codex permissions, the operating system,
destructive-action rules, and any matching domain or enterprise hooks.

#### Selected Option

Remove unrelated Codex-home roots from this hook's control plane and keep the
project-specs root as the only private runtime boundary it protects.

#### Alternatives Considered

Keeping an expanding list of unrelated protected roots duplicates other owners
and turns project lifecycle into a global policy. Disabling the hook entirely
would remove selected-project sequencing and receipt protection. Adding a new
alternate writer would evade the defect rather than repair its owner.

#### Implementation Boundaries

Owned changes are lifecycle hook protected-root classification and focused
disposable-home tests. Config, task-state, hook installation, and security
semantics remain with their existing owners.

#### Test-First Success Criteria

- TDD-001: Fixed writes to config, hooks, task state, installed skills, and
  unrelated user files pass through in every phase without changing lifecycle
  state or write epoch.
- TDD-002: Selected-project writes, canonical project-specs state, mixed
  targets, symlink escapes, hard-link aliases, and dynamic targets retain their
  existing fail-closed behavior.

#### Validation Plan

Run hook unit tests with disposable Codex homes and compare lifecycle state
before and after external and protected-state cases.

#### Test Plan

Cover each formerly protected external root, absolute and relative targets,
multi-file patches, symlinks, hard links, every active phase, and failed tool
responses.

#### Evaluation Plan

Retry the denied Playwright config patch unchanged and independently confirm
that lifecycle receipts and state remain protected.

#### Rollout And Rollback

Install from reviewed source and verify in a fresh session. Restore the prior
hook if project or lifecycle-private containment regresses.

#### Done Definition

External writes are no longer denied by the project hook, while selected-project
sequencing and authoritative lifecycle state remain protected.

<!-- /FEATURE: FEAT-003 -->

<!-- FEATURE: FEAT-004 reqs=REQ-005 status=ready priority=P1 version=1 -->
### FEAT-004: Layered repair verification and activation

#### Requirements Covered

- REQ-005: Separate source correctness, installed parity, registration/trust, and fresh-runtime behavior.

#### Context Evidence

`install-skills.sh` provides idempotent skill and opt-in hook synchronization,
registration merging, provenance, backups, and restart guidance. The lifecycle
and global-context references distinguish source tests from runtime activation.

#### Design Details

Verification proceeds from focused source regression tests to broader skill
and installer checks, then installed byte parity and hook registration review.
Only a restarted, trusted, fresh selected-project session can prove activation.
Temporary bootstrap links or copies remain recovery artifacts and are removed
after that proof, never treated as the durable installation model.

#### Selected Option

Use layered, independently reported verification gates with a fresh-session
behavior probe as the terminal runtime criterion.

#### Alternatives Considered

One passing source test cannot prove installation. Byte parity cannot prove a
running session reloaded the hook. A healthy state produced through temporary
bootstrap intervention cannot prove the repaired clean path.

#### Implementation Boundaries

Source tests and documentation live in this repository. Installed files,
registration state, trust decisions, and temporary recovery artifacts remain
local runtime state and are never committed.

#### Test-First Success Criteria

- TDD-001: Focused source tests fail for the stale trust root and pass for the
  canonical root.
- TDD-002: Installed hook bytes match reviewed source after synchronization.
- TDD-003: A fresh runtime executes the canonical coordinator and creates or
  reconciles the selected project's spec pair through the owner lifecycle.

#### Validation Plan

Run changed-scope review, lint and syntax, security, regression, installer
parity, and fresh-session lifecycle checks in that order.

#### Test Plan

Use disposable source tests before local installation; after restart, run one
clean project lifecycle from inspection through sealing and separately verify
postconditions.

#### Evaluation Plan

Report the highest completed gate and any residual restart, trust, or
fresh-session requirement without upgrading the claim.

#### Rollout And Rollback

Keep installer-generated hook backups until fresh-session proof succeeds.
Rollback restores only the exact prior runtime hook and registration state;
source history remains available for correction.

#### Done Definition

Source, installed parity, registration/trust, and fresh runtime are all proven
or any missing gate is explicitly reported as the remaining blocker.

<!-- /FEATURE: FEAT-004 -->

<!-- FEATURE: FEAT-005 reqs=REQ-006 status=ready priority=P0 version=2 -->
### FEAT-005: Effect-proportional command and private-input classification

#### Requirements Covered

- REQ-006: Preserve flexible investigation and bounded private workflow inputs while material project effects remain lifecycle-gated.

#### Context Evidence

The former lifecycle hook inspected raw shell text before quoting, rejected
material effects outside the selected project, and admitted content writes but
not exact permission tightening for its required caller-owned runtime and
decision inputs. It also required new canonical specs to be Git-tracked while
blocking a repository-root-relative intent-to-add command. The focused hook
suite now carries regressions for each of those reproduced deadlocks and false
blocks.

#### Design Details

Replace the universal read-command allowlist with effect-oriented detection.
Explicit file writers, redirects, known mutating Git operations, mutating
tool methods, and declared write tools remain material. Common shell
composition is decomposed or conservatively scanned for those effects rather
than rejected merely for using a pipe or separator. Commands without a known
material effect pass through the project lifecycle and remain subject to their
normal execution sandbox, permissions, and product-specific hooks.

Add one narrow lifecycle-private write class before selected-project
containment: direct `runtime-config.json` or `decision.json` members of the
current lifecycle's caller-owned project-instructions directory. Bind it to
trusted payload session identity, canonical workspace hashing, path
containment, regular-file or absent-target checks, and single-purpose tool
targets. These writes never change lifecycle phase or write epoch.
Coordinator-owned receipt, manifest, render, ownership, final, and lifecycle
files stay protected. Task state and other external user files need no special
exception because they are outside this hook's ownership boundary.

Add two explicit bootstrap transitions before generic material-write
containment. Canonical specs may be marked intent-to-add only by Git with an
explicit `-C` binding to the selected project, the intent-only option, an
option delimiter, and one or both exact `docs/requirements.md` and
`docs/design.md` targets. Caller-authored runtime and decision inputs may have
their permissions tightened only by a single uncomposed `chmod` using numeric
mode 600 or 0600 against one exact current-session target. Neither transition
changes file contents, satisfies a lifecycle phase transition, or advances the
project write epoch. General staging, other Git mutations, other modes,
multiple targets, symlinks, sibling sessions, and coordinator-owned state stay
denied.

Parse shell quoting before recognizing control operators or writer tokens.
Quoted search patterns containing pipe characters, redirection characters,
backticks, command-substitution text, or mutator names remain data; the same
tokens outside quotes remain material. Malformed shell syntax continues to
fail closed.

Classify lifecycle relevance separately from generic materiality. Resolve a
fixed effective working directory and explicit targets without requiring them
to be inside the selected project. A material operation whose proven targets
and working directory are wholly outside that project passes through this hook
and is epoch-neutral. If any target is inside the selected project, apply the
normal phase and protected-path gates. Reject mixed internal/external target
sets while planning or when the combined effect cannot be safely separated;
retain conservative handling for unresolved variables, dynamic cwd changes,
targetless writers from the selected project, and malformed commands.
Command-specific extraction covers multi-destination directory installation,
destination-bearing copy/install/link options, hard-link source effects, and
tree moves so no project or protected-control-plane target can hide behind a
final external operand.

The lifecycle's own authoritative `${CODEX_HOME}/project-specs` state remains
protected independent of location. Exact current-session caller inputs keep
their bounded authoring and numeric mode-tightening transitions, but receipts,
lifecycle state, render/ownership/final state, other lifecycle sessions, and
symlink escapes never become ordinary external pass-through. Existing caller
inputs must also be owned, regular, single-link files so an epoch-neutral
permission transition cannot change an aliased project or authoritative inode.
Other Codex-home and user-file roots are not this hook's control plane. This
hook does not grant operating-system, sandbox, destructive-action, credential,
IAM, data, network, or domain-policy authorization; it merely declines to apply
a selected-project contract to proven external effects.

Validation receipt persistence remains owner-controlled: `validate --output`
requires the hook-bound session and accepts only that session's canonical
`spec-receipt.json`, which it writes atomically with mode 0600. The hook exposes
only a digest of the current turn identity; `plan`, `open`, `waive`, and `seal`
accept that opaque token together with the bound session, and the hook rechecks
both before dispatch. The `start-prompt` transition retains the exact raw
current-turn identity because it is invoked by the hook rather than by the
model.

#### Selected Option

Make the hook a project-mutation lifecycle guard, not a general executable
trust policy. Preserve exact target gates for known writes and exact private
workflow inputs while allowing ordinary investigation by default.

#### Alternatives Considered

Expanding the fixed command allowlist would continue to break on common tools,
Homebrew ownership, pipelines, and future read-only integrations. Disabling
PreToolUse would remove useful sequencing protection. Treating unrelated
`${CODEX_HOME}` roots as project lifecycle state would preserve the ownership
defect; allowing the lifecycle's own project-specs state would expose
authoritative evidence to hand editing.

#### Implementation Boundaries

Owned changes are lifecycle hook classification helpers and tests, the
project-instructions private-input naming contract, lifecycle documentation,
root guidance, and changelog. This feature does not broaden operating-system
permissions, sandbox scope, external-system authorization, or destructive
action approval.

#### Test-First Success Criteria

- TDD-001: Previously blocked ordinary reads and read-only compositions are
  admitted without changing lifecycle state.
- TDD-002: Explicit project writers remain phase- and scope-gated, including
  redirects, patch tools, mutating Git actions, and mixed commands.
- TDD-003: Only the exact current-session runtime declaration and decision
  inputs are caller-writable within project-specs; sibling, cross-session,
  symlinked, and coordinator-owned lifecycle state stays denied, while task
  state remains external pass-through.
- TDD-004: Coordinator inspect, render, plan, open, apply, verify, and seal
  remain receipt-bound and unchanged in authority.
- TDD-005: Exact canonical intent-to-add and current-session mode tightening
  are admitted and epoch-neutral, while broader Git and chmod variants remain
  denied.
- TDD-006: Quoted mutator and shell-control patterns remain read-only, while
  equivalent unquoted controls stay material.
- TDD-007: Fixed commands wholly outside the selected project are admitted and
  epoch-neutral, while selected-project, mixed, lifecycle-state, dynamic, and
  ambiguous mutations retain their existing gates.

#### Validation Plan

Run focused lifecycle-hook tests, project-instruction contract tests, shell
syntax and lint checks, changed-scope security review, installer tests, and a
fresh-runtime lifecycle probe.

#### Test Plan

Use disposable Git roots and Codex homes to cover command composition,
executable ownership, known writers, Git subcommands, MCP/tool method
classification, private path identity, multi-target patches, symlinks, phase
transitions, and unchanged write epochs.

#### Evaluation Plan

Repeat the blocked commands from this incident and complete a clean lifecycle
bootstrap. Independently verify that an implementation edit before `open` and
an authoritative private-state edit are still rejected.

#### Rollout And Rollback

Install the repaired source hook through `install-skills.sh`, restart Codex,
review hook trust, and test in a fresh session. Preserve the installer backup
until the fresh probe succeeds; rollback restores only that exact hook and
registration state.

#### Done Definition

The quote-aware shared analyzer, exact intent-to-add and private mode
transitions, external effect pass-through, protected control plane, lifecycle
Pre/Post symmetry, and focused hook regressions are implemented in source.
Agents can investigate and prepare required private inputs without lifecycle
deadlocks, while project mutations and authoritative lifecycle transitions
remain fail-closed and receipt-bound. Installation and fresh-runtime activation
remain separate rollout gates under REQ-005.

<!-- /FEATURE: FEAT-005 -->

<!-- FEATURE: FEAT-006 reqs=REQ-007 status=ready priority=P0 version=1 -->
### FEAT-006: Owner-correct recovery from config write denials

#### Requirements Covered

- REQ-007: Distinguish repo-owned guard defects from external policy and repair the causal owner without evasion.

#### Context Evidence

`config-codex/SKILL.md`, its README, and `references/local-setup.md` currently
turn every blocking guard into a mandatory manual edit. The same workflow
already owns reviewed hook-source installation, provenance checks, backups,
restart guidance, and source-installed-runtime separation.

#### Design Details

Split denial handling into two branches. For a proven repo-owned guard whose
documented boundary contradicts the denial, and when the request authorizes
source plus local installation changes, reproduce the false block, repair the
canonical repository source and docs, validate, sync through the documented
installer, report restart/trust status, and retry the identical target edit.
This sequence does not change writer or target and therefore repairs policy
rather than evading it.

For OS, sandbox, enterprise, external, unknown-provenance, conflicting, or
out-of-authority controls, stop and report the exact blocker plus smallest
manual action. Prohibit redirection, alternate tools, direct installed-hook
edits, disabling or unregistering the guard, cwd tricks, or pre-repair target
mutation in both branches.

#### Selected Option

Use provenance- and authority-gated source-first repair, with manual fallback
only when the policy owner cannot be repaired in scope.

#### Alternatives Considered

Universal manual fallback is safe but leaves repo-owned defects unfixed.
Alternate writers and hook disabling bypass policy. Editing only installed
payloads loses provenance and will drift on the next installation.

#### Implementation Boundaries

Owned surfaces are the config-codex skill contract, setup documentation,
cross-surface tests, root changelog, and the canonical source/install workflow.

#### Test-First Success Criteria

- TDD-001: Static contract tests require no-evasion wording, owner/provenance
  gates, source-first repair, separate install/restart, identical retry, and
  external-policy manual fallback across all config-codex surfaces.
- TDD-002: The project lifecycle false-block regression proves the unchanged
  config write passes after source repair while project-specs state stays denied.

#### Validation Plan

Run config-codex contract tests, lifecycle-hook tests, installer checks, source
to installed parity, and a fresh-runtime review boundary.

#### Test Plan

Cover repo-owned authorized, external, unknown-provenance, source-drift,
out-of-scope, and conflicting-policy denials.

#### Evaluation Plan

Apply the original Playwright config patch through the repaired installed hook
and report fresh-session activation separately.

#### Rollout And Rollback

Install with the existing recoverable backup path. Restore the exact previous
hook if regression checks fail before a fresh runtime is trusted.

#### Done Definition

Config-codex repairs a proven in-scope owner defect and retries the original
operation without teaching or using a bypass.

<!-- /FEATURE: FEAT-006 -->

<!-- FEATURE: FEAT-007 reqs=REQ-008 status=ready priority=P0 version=3 -->
### FEAT-007: Turn-scoped troubleshoot report obligation

#### Requirements Covered

- REQ-008: Require a structured report for every explicit troubleshoot terminal path.

#### Context Evidence

`remediation_attempt_guard.py` currently validates terminal report fields only
when a remediation marker is exhausted. Missing or non-exhausted marker state
returns success immediately, which allowed the prior `DIAGNOSED_NOT_FIXED`
turn to finish with an abbreviated summary. The Stop arbiter already provides
the single composition point for report enforcement.

#### Design Details

At `UserPromptSubmit`, every explicit `$troubleshoot` invocation writes a
session-private, turn-bound `troubleshoot-report-obligation.json` sidecar even
when no remediation budget is requested. The separate file keeps the existing
remediation authorization schema unchanged. Preserve the obligation until Stop
validates a report or a later resumed turn in the same session reports the
interrupted state. Stop requires one supported classification plus a single
canonical report envelope for outcome, failure contract, architecture,
component verification, timeline, logs, hypotheses, code debugging, cause,
remediation, post-fix validation, completion gate, and remaining uncertainty.
The completion gate contains exactly seven criteria with `PASS`, `FAIL`, or
`UNKNOWN`, supporting evidence, and gaps or next actions. `VERIFIED_FIXED` is
admitted only when all seven are `PASS`. A `PASS` row uses a structured
canonical evidence reference and exact no-gap sentinel. The parser
cross-validates that reference against the official-architecture verdict,
relevant component-matrix cells, log coverage statuses, or all code-debugging
and post-fix fields. Structured states and narrative evidence are checked
separately: a valid status token does not override explicit missing,
unavailable, unexamined, or unverified detail. Exhaustion uses the same envelope
and additionally retains its exact marker-derived attempts, blocker, and next
action.

An incomplete response receives a bounded corrective continuation with the
missing structure. The sidecar records the correction count so the arbiter
cannot loop indefinitely. A valid report becomes ready but is marked delivered
only after the shared arbiter proves that no project-contract or SDLC peer still
requires continuation; otherwise the obligation remains active for the later
terminal Stop. If a later peer returns a terminal result, that result keeps
precedence after the arbiter finalizes the already validated report, preventing
stale obligation carryover. If the host terminates before Stop, no local hook
can emit an assistant response; keeping the obligation lets the next available
turn in the same session report that interruption before completing. A
replacement session cannot be mechanically bound to the old private
obligation. Hook diagnostics remain redacted and cannot substitute a
user-visible assistant report.

#### Selected Option

Add a separate session-private report-obligation sidecar and extend the existing
Stop delegate, rather than changing remediation authorization, registering
another independent Stop hook, or relying only on advisory skill prose.

#### Alternatives Considered

Skill prose alone already failed. Requiring a remediation marker for every
diagnostic would conflate reporting with retry accounting. A second Stop hook
would complicate arbitration and could race with lifecycle delegates.

#### Implementation Boundaries

Owned surfaces are troubleshoot skill/report contracts, the remediation guard,
shared Stop arbiter tests, hook documentation, process evals, and installer
registration validation. Hosted-process termination remains outside local hook
control.

#### Test-First Success Criteria

- TDD-001: Explicit invocation with no marker rejects an incomplete response
  and accepts each supported substantive outcome.
- TDD-002: Non-exhausted, tool-error, blocked, unresolved, and exhausted paths
  compose with one bounded correction sequence and the shared Stop arbiter.
- TDD-003: An undelivered obligation survives interruption into a resumed turn
  and clears only after an accepted report.
- TDD-004: Contradictory `PASS` evidence is rejected, and a valid general or
  exhausted report is finalized before a later terminal peer result returns.

#### Validation Plan

Run focused hook, arbiter, skill-contract, process-eval, installer registration,
syntax, lint, and adversarial redaction checks.

#### Test Plan

Use disposable task-state roots and synthetic turn IDs for every terminal
classification, missing fields, exhaustion details, correction bounds,
interrupted resume, and other Stop delegates.

#### Evaluation Plan

Recreate the previous no-marker Playwright turn and confirm Stop requests the
complete report before allowing completion.

#### Rollout And Rollback

Install the reviewed payload and restart Codex. If Stop loops or blocks valid
reports, restore the installer backup and retain source evidence for repair.

#### Done Definition

Every reachable explicit troubleshoot terminal path ends with the documented
assistant report, and unreachable process-death cases are carried into the
next resumed turn without overstating hook guarantees.

<!-- /FEATURE: FEAT-007 -->

<!-- FEATURE: FEAT-008 reqs=REQ-009 status=ready priority=P0 version=1 -->
### FEAT-008: Objective-owned hybrid prompt-session intake

#### Requirements Covered

- REQ-009: Capture, refine, merge, and route direct prompts for an explicitly bound Task Implementer or Agentic SDLC session.

#### Context Evidence

Task Implementer and `sdlc-start` already own private editable prompt files,
immutable execution revisions, explicit `run` routing, and workflow-specific
state, but neither prompt schema binds a Codex session or exposes a stable short
reference. Codex `UserPromptSubmit` supplies session and turn identity plus the
direct prompt, while its command hook can add context or block but cannot
perform semantic rewriting. The repository already composes terminal workflow
checks through one shared Stop arbiter.

#### Design Details

Add a separately owned internal `prompt-session-intake` skill. Its
`UserPromptSubmit` command hook remains capture-only: after an exact explicit
Task Implementer or SDLC binding, it rejects secrets before persistence,
excludes Stop continuations and non-root agent turns, acquires only a bounded
session-state lock, and atomically stages a turn with causal session/turn
identity and an unguessable acceptance token. It returns compact additional
context directing the coordinator to classify the already-delivered prompt.
It never semantically edits a prompt or invokes a workflow.

The coordinator classifies a staged turn as intent, steering, constraint,
clarification answer, acceptance change, conversation, status, or control.
Only the first five material classes produce a concise lossless refinement.
Acceptance compares the objective prompt's recorded base digest and the shared
cross-workflow registry under one writer session, then delegates the merge to
the owning workflow adapter with a deterministic non-secret operation ID. The
adapter embeds that operation once and returns the already-applied prompt on an
exact interrupted retry. A successful workflow start or resume consumes the
accepted turn only when the resulting prompt frontmatter and operation marker
match the claimed workflow result. Peer hook blocking leaves the staged turn
unaccepted and therefore permanently ineligible for replay. Diagnostics expose
only identifiers and digests, never raw input.

Keep one lossless private session journal for provenance and one canonical
objective prompt for execution. Session identity binds provenance and routing;
the full prompt ID remains canonical objective identity. Fresh sessions attach
only when exactly one compatible active objective exists; no candidate creates
one on the first material turn and multiple candidates pause for explicit
selection. A cross-workflow registry binds canonical project, workflow,
prompt, writer session, prompt digest, and last consumed turn.

Upgrade both prompt workspaces to schema v3. Derive a stable five-character
reference from the full prompt ID, extend it deterministically on collision,
place it at the beginning of the editable filename, and store it in prompt
metadata. The v3 resolver accepts a unique reference, full prompt ID, or exact
filename but always returns and persists the full ID. Initialization performs
one journaled, idempotent v2-to-v3 migration that renames editable prompts and
rewrites owned indexes/bindings while preserving immutable revisions and
manual edits. Each file transition is atomic, and an interrupted process
finishes from the validated source or already-installed target before mutable
pointers advance. After migration, only v3 reads and writes exist.

Task Implementer and SDLC remain semantic and execution owners through thin
adapters. Direct accepted turns automatically call the same canonical
start/resume path used by explicit `run`; manually editing a prompt file has no
trigger and still requires explicit `run`. The existing global-context hook
stays lightweight. Prompt intake contributes a delegate to the shared Stop
arbiter rather than registering a competing Stop hook.

#### Selected Option

Use capture-only hooks plus coordinator-owned two-phase acceptance and thin
workflow adapters, with objective-owned canonical prompts and session-owned
provenance. Use a hard v3 prompt schema transition and keep installation and
fresh-runtime activation outside source implementation.

#### Alternatives Considered

Letting the hook rewrite or run prompts would exceed the hook contract and put
semantic judgment in a concurrent command process. One prompt file per session
would fragment a long-lived objective across sessions. Treating the short
reference as authority would make collisions unsafe. Auto-running file watches
would make manual edits surprising. Dual v2/v3 writers would create two
canonical paths and prolong migration ambiguity.

#### Implementation Boundaries

Owned surfaces are the new prompt-session-intake skill and hook payload, shared
Stop-arbiter composition, Task Implementer and SDLC prompt-workspace adapters
and schema migrations, focused tests, installer/catalog metadata, workflow and
root documentation, and changelog. Runtime installation, registration, trust,
restart, and fresh-session validation require separate explicit authorization.

#### Test-First Success Criteria

- TDD-001: An unbound or excluded turn is inert; an exact bound root-agent turn
  stages once with secure modes, atomic state, causal identity, and no raw
  diagnostic output.
- TDD-002: Secret-bearing input is rejected before any journal or event write,
  and concurrent/duplicate/stale acceptance cannot produce a second merge or
  workflow run.
- TDD-003: Material classes accept only against the current base digest and one
  writer binding; conversation, status, and control classes consume without
  prompt mutation or execution.
- TDD-004: Task Implementer and SDLC adapters merge refined intent into their
  canonical prompt and enter the same start/resume path as explicit `run`,
  while manual file edits remain inert until explicit invocation.
- TDD-005: One-time v2-to-v3 migration preserves objective and run evidence,
  prefixes filenames with collision-safe references, rejects ambiguous refs,
  and leaves no v2 write path.
- TDD-006: Prompt intake composes through the single Stop arbiter and does not
  make the generic UserPromptSubmit hook route either workflow.

#### Validation Plan

Run focused prompt-intake and both prompt-workspace suites, hook composition
and installer contract tests, syntax and lint checks, adversarial security
review, changed-scope code review, skill-folder alignment, and project-wide
alignment. Report source proof separately from every runtime gate.

#### Test Plan

Use disposable homes, repositories, sessions, prompt workspaces, and synthetic
hook payloads for all binding, migration, reference, state transition,
concurrency, secret, CAS, manual-edit, workflow-isolation, Stop, and recovery
paths. Retain immutable revision comparisons across migration and rerun cases.

#### Evaluation Plan

In a separately authorized fresh runtime, bind one session per workflow,
exercise creation, steering, clarification, completion follow-up, manual edit,
ambiguous attachment, and ref-based rerun, then independently verify each
canonical prompt, immutable revision, registry transition, and run count.

#### Rollout And Rollback

Land and validate source first. Later install through the repository installer
with recoverable hook backups, restart and review trust, then probe a fresh
session. Before any accepted v3 write, migration retains recoverable source
prompt bytes; rollback restores the exact prior runtime hook and uses the
preserved private state for explicit recovery rather than reviving v2 writers.

#### Done Definition

Both workflows share one safe session-intake boundary, retain independent
semantic/execution ownership, use canonical v3 prompt identity with stable
user-facing refs, preserve manual control and immutable evidence, and pass the
source validation matrix without claiming runtime activation.

<!-- /FEATURE: FEAT-008 -->

<!-- FEATURE: FEAT-009 reqs=REQ-010 status=ready priority=P0 version=2 -->
### FEAT-009: Evidence-intensive troubleshooting and technology playbooks

#### Requirements Covered

- REQ-010: Discover and verify the deployed stack, layered evidence, relevant code paths, and technology-specific failure domains before completion.

#### Context Evidence

The baseline troubleshoot workflow owned hypotheses, bounded experiments,
earliest-divergence localization, causal proof, remediation budgets, and
live-product evidence gates. FEAT-009 adds the mandatory stack-discovery phase,
component verification matrix, normalized incident timeline, layered log
ledger, real code-debugging gate, controlled debug escalation, and detailed
Slurm, Soperator, and Nebius playbooks.

#### Design Details

Add `DISCOVERY` between intake and baseline. Discovery records deployed
technology versions, topology, configuration sources, dependencies, ports,
protocols, identities, and control/data flows, then compares them with
version-matched official documentation. Keep emergency evidence preservation
in intake so discovery cannot erase volatile incident state. Extend the
investigation protocol with a component matrix, normalized timeline, layered
log ledger, and experiment contract. Interpret mandatory logs as coverage
accounting: retrieve each relevant source through bounded, filtered queries or
record why it is unavailable, unsafe, or not applicable.

Bind every conclusion to an explicit included and excluded system boundary,
exercised control and data paths, and incident-window start and end with a
clock basis. The generic component matrix names DNS or service-name resolution
and restart history explicitly. The canonical log ledger contains exactly one
row for each component, application/job, container/orchestrator,
service-manager, OS/kernel, network/firewall, storage, and GPU or hardware
layer. The optional report hook rejects missing, duplicate, unknown, or
out-of-order layer names and cross-validates `Logs` PASS against every row;
fallback reports use the same canonical layer set.
It validates every evidence-bearing component and log cell independently and
requires substantive detail after `PASS:`, so a neighboring cell or status
token cannot make a placeholder satisfy the report gate.

Keep the main skill as the generic state machine and put product detail in
`slurm.md`, `soperator.md`, `kubernetes.md`, `nebius.md`, `linux.md`,
`network.md`, `storage.md`, `gpu.md`, and `code-debugging.md`. Product overlays
reuse generic lower-layer playbooks instead of duplicating them. Soperator uses
upstream terminology: ActiveChecks are separately scheduled Kubernetes or
Slurm diagnostic jobs and commonly consume complete or exclusive GPU capacity;
passive or workload-coupled checks execute through Slurm prolog, epilog, or
`HealthCheckProgram` around customer jobs. Nebius guidance freezes tenant,
project, region, resource, and service identity, then routes Compute, MK8s,
Soperator, VPC, storage, quotas/capacity, IAM, AI services, observability, audit,
and maintenance evidence to the exact current product documentation and
existing Nebius skill authorities.

When code is plausibly causal, require reproduction, entry/control/data/state
tracing, stack or core evidence, effective inputs, recent-change comparison,
focused tests/static analysis, and bounded instrumentation. Passing tests alone
are supporting evidence, never universal correctness proof. Debug escalation
records scope, expected decision, duration, resource and secret risk, original
settings, rollback, and post-restoration verification.

#### Selected Option

Use progressive disclosure through focused technology references and enforce
completion through the skill contract plus the optional Stop hook. Keep the
five overall outcomes and add criterion-level verdicts rather than replacing
causal outcome classification.

#### Alternatives Considered

Prose-only guidance cannot prevent premature completion. A universal cloud or
cluster command runner would overreach authorization and encode stale vendor
behavior. Requiring unconditional broad log retrieval would conflict with
observability admission and secret/minimization rules. A compatibility parser
for the superseded report shapes would retain two canonical paths.

#### Implementation Boundaries

Owned surfaces are the troubleshoot state machine, investigation and
verification references, technology playbooks, report hook and fallback,
process rubric/cases/prompts, contract and hook tests, skill metadata, README,
root catalog, project specs, and changelog. Existing authentication, marker,
sidecar, observability-admission, design-handoff, and live-product authority
contracts remain unchanged. Installation and fresh-runtime activation stay
separate.

#### Test-First Success Criteria

- TDD-001: Contract tests require discovery, version-matched sources,
  component/log/timeline ledgers, hypothesis-bound commands, code debugging,
  debug rollback, and every technology reference.
- TDD-002: Report tests reject missing or duplicate criteria or canonical log
  layers, invalid verdicts, placeholder component identities or evidence cells,
  empty boundary or incident-window fields, and any `VERIFIED_FIXED` report
  with a non-`PASS` row.
- TDD-003: Soperator cases distinguish resource-consuming ActiveChecks from
  passive or workload-coupled job hooks and preserve node-local log gaps.
- TDD-004: Nebius cases distinguish identity, quota, capacity, IAM, operation,
  managed-control-plane, workload, and lower-layer evidence without mutating
  cloud state during default diagnosis.

#### Validation Plan

Run troubleshoot contract and hook suites, skill structure validation, Ruff,
Markdown lint, changed-scope code and security review, adversarial process
evaluations, `align-skill`, and project-wide alignment. Treat fresh-session
graded scenarios as the behavioral gate beyond static contract tests.

#### Test Plan

Cover healthy-status hidden failures, clock skew and rotation, missing logs,
MUNGE and controller-worker failures, Slurm accounting divergence, stale GPU
errors, resource exhaustion, ActiveCheck contention, passive-check job impact,
Nebius project/quota/IAM/VPC/MK8s/storage failures, misleading stack traces,
and debug settings left enabled.

#### Evaluation Plan

In disposable or authorized read-only targets, require a complete architecture
verdict, component matrix, incident timeline, log ledger, hypothesis history,
root-cause confidence, remediation evidence, post-fix validation, unknowns, and
residual risks. A healthy final state without causal replay remains mitigation,
not a verified fix.

#### Rollout And Rollback

Land and validate source first. Installation, hook registration/trust, restart,
and fresh-session behavior require a separate authorized rollout with parity
checks and recoverable hook backups. Roll back only the exact installed payload
if runtime validation fails; preserve reviewed source evidence for repair.

#### Done Definition

Every explicit troubleshooting run accounts for design, infrastructure,
connectivity, configuration, runtime health, logs, and relevant code paths with
evidence-backed verdicts. Every conclusion is restricted to its declared
boundary, exercised paths, and observation window; every canonical log layer is
examined or explicitly unavailable, unsafe, or not applicable, while
technology-specific diagnosis remains bounded, version-aware, and
authorization-safe.

<!-- /FEATURE: FEAT-009 -->

<!-- FEATURE: FEAT-010 reqs=REQ-011 status=ready priority=P0 version=4 -->
### FEAT-010: Claim-bound whole-repository commit transaction

#### Requirements Covered

- REQ-011: Commit complete multi-project repository changes through one safe transaction.

#### Context Evidence

The `commit` contract already requires repository-root `git add -A`, staged
review, `git write-tree`, normal hooks, and final tree verification. The
selected-project lifecycle hook classifies every mutating Git command as
material but has no Git target model, so general staging becomes ambiguous and
is denied before the documented commit workflow can run. Worktree already uses
durable preparation claims and direct-child/tree verification; Task Implementer
and Agentic SDLC retain separate lane and feature-promotion owners. The initial
prompt matcher accepts only a first `$commit` token, so equally explicit user
directives such as `run $commit` fail to mint the transaction authorization.

#### Design Details

Add a commit-owned prompt hook that recognizes a bounded leading invocation
grammar: optional `please`, then either `$commit` directly or one directive
verb from `run`, `apply`, `execute`, `invoke`, or `use` immediately followed by
`$commit`. Parse the invocation once so default-branch authorization uses
the body after the actual `$commit` token. Reject help, questions, quotations,
later prose mentions, excluded origins, contradictory explicit origin markers,
and explicitly non-root turns. The current runtime makes `agent_type` optional
for primary UserPromptSubmit events, so an absent value is not treated as
delegation. Record the bounded current-turn authorization outside the
repository. Direct helper execution
requires that authorization. Worktree and Task Implementer delegated modes
instead require their existing exact owner claims. The active Agentic SDLC
policy classifies the helper as commit-sensitive and denies ordinary mode while
preserving its private `sdlc-commit` path.

Add an installed commit transaction helper with `prepare`, `execute`, and
private `review` transitions. `prepare` copies the current index into a private
temporary index, applies whole-repository `git add -A`, writes the candidate
tree, and persists only repository/worktree/ref identities, head and tree IDs,
bounded digests, the lifecycle binding, authorization source, status, and a
token hash. `execute` acquires the shared common-directory/ref lock, recomputes
and compares the candidate, stages the real index, runs cached checks and the
normal-hook commit, then proves unchanged branch, one direct child, matching
commit tree, and the expected final checkout state. If a successful hook
changes the committed tree, `review` can complete only the retained current,
clean, exact direct child after the agent supplies its independently reviewed
commit and tree IDs. A failed hook that creates no commit and pre-commit drift
become stale for a fresh explicit invocation.

Before any Git discovery or transaction command, reject caller environment
variables that can reshape repository discovery, common/worktree directories,
the object store, command configuration, or index selection. Every ordinary
Git subprocess receives the validated environment unchanged; only the private
preview path adds the transaction-owned `GIT_INDEX_FILE` override. Never use a
caller-selected alternate index as the canonical real index.

Use durable `AUTHORIZED`, `PREPARED`, `STAGED`, `COMMITTED`, `STALE`, and
`REVIEW_REQUIRED` states. A fresh explicit authorization may rebind an
interrupted claim only when immutable old and current Git evidence proves the
same staged state or already-created direct child. Revalidate delegated worker
ownership before adopting that direct child so a completed or revoked task
cannot acquire committed claim state through crash recovery. Never clear
ownership by PID or timeout and never reset, amend, unstage, or remove
repository changes.

Keep the current project lifecycle single-scope. A fresh zero-write commit-only
turn may record the existing bounded non-project waiver; a nonterminal selected
scope with project writes still blocks. Sibling project lifecycle state is not
discovered or aggregated because committing Git metadata must not turn the
selected-project hook into a repository-wide semantic owner.

The lifecycle hook admits only one uncomposed canonical installed helper whose
interpreter, path, digest, current session, selected project, repository,
worktree, authorization, claim, and action flags match. Protect commit private
state as control-plane data. Verified Git-only completion is epoch-neutral;
helper-reported hook or concurrent file mutation invalidates current lifecycle
evidence and requires review.

#### Selected Option

Use one hidden claim-bound transaction behind a bounded explicit `$commit`
invocation, including common leading imperative forms, with workflow-specific
delegated adapters and a shared Git-ref conflict registry. Keep user
interaction to one command while retaining exact review, recovery, and
cross-workflow ownership.

#### Alternatives Considered

Allowing raw repo-root Git is simpler but cannot bind explicit intent, reviewed
tree, hook output, one-shot consumption, or recovery. Requiring every affected
project to emit a lifecycle attestation adds user ceremony and incorrectly
turns a selected-project guard into a monorepo semantic coordinator. Letting
Task Implementer auto-commit dirty source state broadens its explicit
integration authority. Allowing ordinary commit inside active Agentic SDLC
invalidates coordinator evidence and exact-head guarantees.

#### Implementation Boundaries

Owned surfaces are the `commit` helper, prompt hook, contract and real-Git
tests; lifecycle helper admission and protected state; shared Worktree ref
coordination and delegated adapter; Task Implementer dirty-source handoff;
Agentic SDLC helper classification; corresponding README, lifecycle reference,
installer registration, canonical specs, and changelog. Lane integration,
feature sealing/promotion, publishing, and runtime installation remain with
their existing owners.

#### Test-First Success Criteria

- TDD-001: A direct `$commit` or a bounded leading directive such as
  `run $commit`, `apply $commit`, or `execute $commit` prepares and creates one
  exact commit from root files plus changes in multiple sibling project folders
  without sibling lifecycle state.
- TDD-002: Raw Git, conversational mentions, questions, quotations, help,
  later prose references, malformed helpers, wrong interpreter or helper
  digest, stale sessions, alternate worktrees/refs, and token replay fail
  before repository mutation. Contradictory explicit origin markers and
  repository-shaping Git environment fail before authorization or index
  mutation.
- TDD-003: Head, index, status, candidate-tree, and claim drift fail closed;
  crashes before or after commit reconcile from a fresh explicit invocation
  without duplicate history, while merge heads fail direct-child proof.
- TDD-004: A commit-hook tree change retains the exact direct child in
  `REVIEW_REQUIRED` until its actual commit and tree are reviewed; new dirt or
  a failed hook without a commit becomes stale without altering retained Git
  state.
- TDD-005: Task Implementer workers and Worktree integrations use exact
  delegated claims, dirty Task source reports the explicit `$commit` handoff,
  stale worker ownership blocks exact-child crash recovery, and competing
  same-ref owners serialize.
- TDD-006: Active Agentic SDLC denies ordinary helper execution while its
  existing authorized sealing and released outer Worktree handoff continue.

#### Validation Plan

Run commit contract and disposable-repository transaction tests first, then
lifecycle hook, Worktree, Task Implementer interoperability, SDLC policy and
managed-outer integration, installer registration, security review, lint,
changed-scope review, and project-wide alignment.

#### Test Plan

Cover pre-staged, unstaged, untracked, renamed, deleted, linked-worktree,
submodule, default-branch, conflict, secret-like, generated-file, shell quoting,
symlink/hard-link, concurrent owner, failed hook, crash-window, stale claim,
fresh-session recovery, delegated workflow cases, accepted leading directive
forms, rejected conversational or quoted mentions, contradictory explicit
origins, and alternate-index environment injection.

#### Evaluation Plan

Validate source in disposable homes and repositories. In a separately
authorized rollout, install source and hook bundles together, confirm source to
installed digest parity and trust, restart, then run a fresh multi-project
commit probe. Source tests or byte parity alone do not prove activation.

#### Rollout And Rollback

Land source and tests first. Runtime installation, hook registration, trust,
restart, and fresh-session verification require separate authorization and
recoverable backups. Roll back only the exact installed bundle; unsupported or
incomplete claims remain inert private metadata and never trigger Git cleanup.

#### Done Definition

An explicit `$commit`, including a bounded common leading directive, safely
commits the complete reviewed repository tree across project folders with one
user action, while casual mentions and raw Git remain denied, workflow owners
remain isolated, recovery is idempotent, and installation plus fresh-session
behavior are reported as separate proof gates.

<!-- /FEATURE: FEAT-010 -->
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
